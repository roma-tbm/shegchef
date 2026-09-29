"""Локальный веб-интерфейс планирования кухонной смены.

Запуск:

    streamlit run app.py

Интерфейс собирает меню дня из существующего недельного плана и каталога ТТК,
позволяет править порции, время выдачи и масштаб, затем рассчитывает смену
существующим ядром (kitchen.core.build_shift_sheet) и экспортирует результат
в Excel и текстовые задания по ролям.

Расчётная логика здесь не дублируется: масштабирование, текстовые сообщения
и JSON-хранение меню живут в kitchen/web как обычные Python-модули и
тестируются без запуска Streamlit. Этот файл только собирает интерфейс.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import date, time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from kitchen.core import build_shift_sheet
from kitchen.excel import build_workbook
from kitchen.menu_week import WEEK_PLAN, WEEKDAYS
from kitchen.models import MenuLine, PlanLine, Recipe
from kitchen.seed import demo_data
from kitchen.web import menu_store
from kitchen.web.catalog import (
    MEAL_ORDER,
    default_serve_at,
    make_menu_line,
    meal_of,
    search_recipes,
)
from kitchen.web.menu_store import (
    load_days,
    parse_serve_at,
    plan_to_menu,
    save_days,
)
from kitchen.web.messages import all_messages_text, messages_by_role
from kitchen.web.scaling import ScaleError, scaled_preview, validate_factor

COL_DISH = "Блюдо"
COL_MEAL = "Приём"
COL_PORT = "Порций"
COL_SERVE = "Выдача"
COL_REMOVE = "Убрать"

TABS = ("Меню", "Расчёт смены", "Продукты и дефицит", "Задачи по ролям", "Экспорт")


# ---------------------------------------------------------------------------
# Вспомогательные чистые функции
# ---------------------------------------------------------------------------


def sort_menu(menu: list[MenuLine]) -> list[MenuLine]:
    """Меню в удобном порядке: завтрак → обед, внутри — по выдаче."""
    def key(line: MenuLine) -> tuple[object, object, object]:
        meal_order = MEAL_ORDER.index(line.meal) if line.meal in MEAL_ORDER else 99
        return (meal_order, line.serve_at, line.recipe)

    return sorted(menu, key=key)


WEEKEND_DAYS = ("Суббота", "Воскресенье")


def weekday_of(day: date) -> str:
    """День недели по календарю: Пн–Пт из плана, сб и вс — выходные."""
    if day.weekday() < len(WEEKDAYS):
        return WEEKDAYS[day.weekday()]
    return WEEKEND_DAYS[day.weekday() - len(WEEKDAYS)]


def menu_for_day(
    day: date,
    stored: Mapping[str, tuple[MenuLine, ...]],
    plan: Iterable[PlanLine] = WEEK_PLAN,
) -> tuple[list[MenuLine], str]:
    """Меню даты и его источник: сохранённое, либо заготовка плана, либо пустое.

    Дата, которая есть в хранилище (включая сознательно очищенную — с пустым
    кортежем), берётся как есть. Даты без записи: будний день — разворот плана,
    выходной — пустое меню с пометкой.
    """
    key = day.isoformat()
    if key in stored:
        return list(stored[key]), f"сохранённое меню за {day:%d.%m.%Y}"
    wd = weekday_of(day)
    if wd in WEEKDAYS:
        return list(plan_to_menu(plan, wd, day)), f"план «{wd}»"
    return [], f"пустое меню: выходной ({wd})"


def recipes() -> dict[str, Recipe]:
    """Каталог ТТК как словарь «название → карточка»."""
    return demo_data().recipes


def data_for(menu: list[MenuLine]) -> object:
    """Полная кухня с сегодняшним меню — то, что считает ядро."""
    return replace(demo_data(st.session_state.day), menu=tuple(menu))


def compute_sheet(menu: list[MenuLine]) -> tuple[object | None, str | None]:
    """Расчёт смены существующим ядром. Возвращает (смена, ошибка)."""
    if not menu:
        return None, "Меню пустое: добавьте блюда во вкладке «Меню»."
    try:
        return build_shift_sheet(data_for(menu)), None
    except KeyError as exc:
        return None, (
            f"Неизвестное блюдо в меню: {exc}. Выберите блюдо из каталога ТТК "
            "или уберите его из меню."
        )
    except (ValueError, TypeError, IndexError) as exc:
        return None, f"Не удалось рассчитать смену: {exc}"


def persist_store() -> None:
    """Сохраняет текущее меню дня в локальный JSON."""
    save_days(
        {st.session_state.day.isoformat(): tuple(sort_menu(st.session_state.menu))},
        st.session_state.store,
    )


# ---------------------------------------------------------------------------
# Управление состоянием
# ---------------------------------------------------------------------------


def bootstrap() -> None:
    """Инициализация session_state при первом запуске."""
    if st.session_state.get("booted"):
        return
    today = date.today()
    st.session_state.day = today
    st.session_state.weekday = weekday_of(today)
    st.session_state.store = menu_store.default_store_path
    st.session_state.menu_seed = 0
    st.session_state.preview = None
    st.session_state.scale_factor = 1.0
    st.session_state.sheet = None
    st.session_state.excel_bytes = None
    st.session_state.excel_name = ""

    # Дата, сохранённая в JSON (даже пустая), берётся из него; без записи —
    # разворот плана для буднего дня и пустое меню для выходного.
    st.session_state.menu, st.session_state.menu_source = menu_for_day(
        today, load_days(st.session_state.store)
    )
    st.session_state.booted = True


def invalidate_sheet() -> None:
    st.session_state.sheet = None
    st.session_state.excel_bytes = None
    st.session_state.excel_name = ""


def on_day_changed() -> None:
    """Смена рабочей даты: загрузить меню нового дня из хранилища или плана.

    Текущее меню на новый день не переносится: правки уже сохранены
    обработчиками за старую дату, а новая дата всегда читается заново.

    Селектор дня недели (ключ `_weekday`) синхронизируется с календарём новой
    даты; для субботы/воскресенья, которых нет в плане, — безопасный fallback.
    """
    new_day = st.session_state._day
    if new_day is None or new_day == st.session_state.day:
        return
    st.session_state.day = new_day
    st.session_state.weekday = weekday_of(new_day)
    if st.session_state.weekday in WEEKDAYS:
        st.session_state._weekday = st.session_state.weekday
    else:
        st.session_state._weekday = WEEKDAYS[0]
    st.session_state.menu, st.session_state.menu_source = menu_for_day(
        new_day, load_days(st.session_state.store)
    )
    st.session_state.preview = None
    st.session_state.menu_seed += 1
    invalidate_sheet()


def on_weekday_changed() -> None:
    st.session_state.weekday = st.session_state._weekday
    st.session_state.day = st.session_state._day
    st.session_state.menu = list(
        plan_to_menu(WEEK_PLAN, st.session_state.weekday, st.session_state.day)
    )
    st.session_state.menu_source = f"план «{st.session_state.weekday}»"
    st.session_state.preview = None
    st.session_state.menu_seed += 1
    invalidate_sheet()


def on_add_dish() -> None:
    recipe = str(st.session_state.get("_add_dish") or "").strip()
    portions = int(st.session_state.get("_add_portions") or 50)
    serve = st.session_state.get("_add_serve") or time(12, 0)
    line = make_menu_line(recipes(), recipe, portions, serve, st.session_state.day)
    if line is None:
        return
    st.session_state.menu = sort_menu(st.session_state.menu + [line])
    st.session_state.preview = None
    st.session_state.menu_seed += 1
    invalidate_sheet()
    persist_store()


def on_apply_scale() -> None:
    st.session_state.menu = sort_menu(
        [scaled for _, scaled in st.session_state.preview]
    )
    st.session_state.preview = None
    st.session_state.menu_seed += 1
    invalidate_sheet()
    persist_store()


# ---------------------------------------------------------------------------
# Таблица меню (data editor)
# ---------------------------------------------------------------------------


def _parse_serve_cell(value: Any, meal: str) -> time:
    return parse_serve_at(value, default_serve_at(meal))


def menu_to_frame(menu: list[MenuLine]) -> pd.DataFrame:
    rows = []
    for line in sort_menu(menu):
        rows.append(
            {
                COL_DISH: line.recipe,
                COL_MEAL: line.meal,
                COL_PORT: line.portions,
                COL_SERVE: line.serve_at,
                COL_REMOVE: False,
            }
        )
    return pd.DataFrame(rows, columns=[COL_DISH, COL_MEAL, COL_PORT, COL_SERVE, COL_REMOVE])


def frame_to_menu(frame: pd.DataFrame, previous: list[MenuLine]) -> list[MenuLine]:
    """Правки таблицы обратно в строки меню. Некорректные ячейки не ломают ввод."""
    prev_map = {(line.recipe, line.meal): line for line in previous}
    day = st.session_state.day
    lines: list[MenuLine] = []
    for _, row in frame.iterrows():
        if row.get(COL_REMOVE):
            continue
        dish = str(row[COL_DISH]).strip()
        meal = str(row[COL_MEAL]).strip()
        if not dish or not meal:
            continue
        old = prev_map.get((dish, meal))
        try:
            portions = int(row[COL_PORT])
        except (TypeError, ValueError):
            portions = old.portions if old else 50
        if portions < 1:
            portions = old.portions if old and old.portions > 0 else 1
        serve = _parse_serve_cell(row[COL_SERVE], meal)
        note = old.note if old else ""
        lines.append(MenuLine(dish, portions, serve, meal, day, note))
    return sort_menu(lines)


def render_menu_editor() -> None:
    frame = menu_to_frame(st.session_state.menu)
    edited = st.data_editor(
        frame,
        key=f"menu_editor_{st.session_state.menu_seed}",
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        column_config={
            COL_DISH: st.column_config.TextColumn(COL_DISH, disabled=True, width="large"),
            COL_MEAL: st.column_config.TextColumn(COL_MEAL, disabled=True),
            COL_PORT: st.column_config.NumberColumn(
                COL_PORT, min_value=1, step=1, format="%d"
            ),
            COL_SERVE: st.column_config.TimeColumn(COL_SERVE, format="HH:mm", step=60),
            COL_REMOVE: st.column_config.CheckboxColumn(COL_REMOVE),
        },
        disabled=[COL_DISH, COL_MEAL],
    )
    new_menu = frame_to_menu(edited, st.session_state.menu)
    if tuple(new_menu) != tuple(sort_menu(st.session_state.menu)):
        st.session_state.menu = new_menu
        st.session_state.preview = None
        invalidate_sheet()
        persist_store()
    st.caption("Отметьте «Убрать», чтобы исключить блюдо; порции и время выдачи "
               "правятся прямо в таблице.")


# ---------------------------------------------------------------------------
# Вкладки
# ---------------------------------------------------------------------------


def render_menu_tab() -> None:
    st.subheader("Добавить блюдо из каталога ТТК")
    catalog = recipes()
    if not catalog:
        st.error("В каталоге ТТК нет блюд. Проверьте данные проекта.")
        return
    with st.container(border=True):
        col_a, col_b, col_c, col_d = st.columns([1.8, 1.2, 1.0, 1.2])
        query = col_a.text_input(
            "Поиск по названию (пусто — весь каталог)", key="_add_search"
        )
        choices = search_recipes(catalog, query)
        if not choices:
            col_b.selectbox("Блюдо", [""], disabled=True, key="_add_dish")
            dish = ""
        else:
            dish = col_b.selectbox(
                "Блюдо",
                choices,
                key="_add_dish",
                format_func=lambda name: f"{name} · {meal_of(catalog, name)}",
            )
        portions = col_c.number_input(
            "Порций", min_value=1, value=50, step=1, key="_add_portions"
        )
        meal = meal_of(catalog, dish)
        if st.session_state.get("_add_serve_dish") != dish:
            # Выбор блюда сменился — вернуть стандартное время выдачи его приёма.
            st.session_state._add_serve = default_serve_at(meal)
            st.session_state._add_serve_dish = dish
        col_d.time_input("Время выдачи", key="_add_serve")
        if meal:
            st.caption(f"Приём пищи: {meal} — берётся из карточки блюда.")
        if not choices and query.strip():
            st.info(
                f"По запросу «{query}» во всём каталоге ТТК ничего не найдено — "
                "проверьте название."
            )
        st.button("Добавить в меню", on_click=on_add_dish,
                  disabled=not dish, type="primary", key="_add_submit")

    st.divider()
    st.subheader(f"Меню дня · {st.session_state.menu_source}")
    render_menu_editor()

    st.divider()
    st.subheader("Масштабирование порций")
    col_f, col_g, _ = st.columns([1.2, 1.6, 3])
    factor = col_f.number_input(
        "Коэффициент", min_value=0.01, value=float(st.session_state.scale_factor),
        step=0.1, format="%.3f", key="_scale_factor",
    )
    if col_g.button("Показать предпросмотр", type="secondary"):
        try:
            st.session_state.scale_factor = validate_factor(factor)
            st.session_state.preview = list(
                scaled_preview(sort_menu(st.session_state.menu), factor)
            )
        except ScaleError as exc:
            st.error(str(exc))
            st.session_state.preview = None
    if st.session_state.preview:
        rows = []
        for original, scaled in st.session_state.preview:
            rows.append(
                {
                    COL_DISH: original.recipe,
                    COL_MEAL: original.meal,
                    "Было": original.portions,
                    "Стало": scaled.portions,
                    "Разница": scaled.portions - original.portions,
                }
            )
        st.write("Предпросмотр «было → стало»:")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        col_h, col_i, _ = st.columns([1, 1, 3])
        if col_h.button("Применить масштаб", type="primary"):
            on_apply_scale()
            st.rerun()
        if col_i.button("Отменить"):
            st.session_state.preview = None
            st.rerun()
        st.caption("После применения порции можно поправить вручную в таблице выше.")


def render_calc_tab() -> None:
    st.subheader("Расчёт смены")
    if st.session_state.day:
        st.caption(f"Рабочая дата: {st.session_state.day:%d.%m.%Y} · "
                   f"меню: {len(st.session_state.menu)} строк · "
                   f"порций: {sum(line.portions for line in st.session_state.menu)}")
    if st.button("Рассчитать смену", type="primary", width="stretch"):
        sheet, error = compute_sheet(st.session_state.menu)
        if error:
            st.error(error)
        else:
            st.session_state.sheet = sheet
    sheet = st.session_state.sheet
    if sheet is None:
        sheet, error = compute_sheet(st.session_state.menu)
        if error:
            st.info(error)
            return
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Порций", sheet.portions)
    col2.metric("Блюд", sheet.dishes)
    col3.metric("Задач", len(sheet.tasks))
    col4.metric("Смена", f"{sheet.shift_start:%H:%M}–{sheet.shift_end:%H:%M}")
    if sheet.warnings:
        st.subheader("Предупреждения")
        for warning in sheet.warnings:
            st.warning(warning)
    menu_rows = []
    for row in sheet.menu:
        menu_rows.append(
            {
                "Приём": row.meal,
                "Подгруппа": row.subcategory,
                COL_DISH: row.dish,
                COL_PORT: row.portions,
                COL_SERVE: f"{row.serve_at:%H:%M}",
            }
        )
    st.subheader("Меню смены")
    st.dataframe(pd.DataFrame(menu_rows), hide_index=True, width="stretch")


def render_products_tab() -> None:
    st.subheader("Продукты и дефицит")
    sheet, error = compute_sheet(st.session_state.menu)
    if error:
        st.info(error)
        return
    shortages = [
        need for need in sheet.needs_flat
        if need.shortage > 0 and need.qty_display > 0
    ]
    if shortages:
        rows = []
        for need in shortages:
            rows.append(
                {
                    "Продукт": need.product,
                    "Нужно": need.qty_display,
                    "Ед.": need.unit,
                    "Остаток": need.stock,
                    "Не хватает": need.shortage,
                }
            )
        st.error(f"Дефицит по {len(shortages)} позициям — докупите или скорректируйте меню.")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.success("Дефицита по остаткам нет.")
    for group in sheet.needs:
        rows = []
        for need in group.items:
            rows.append(
                {
                    "Продукт": need.product,
                    "Нужно": need.qty_display,
                    "Ед.": need.unit,
                    "Действие": need.action,
                    "Из блюд": need.breakdown or "—",
                    "Остаток / мин.": f"{need.stock} / {need.min_stock}",
                    "Статус": need.status,
                }
            )
        label = f"{group.category} · {group.owner}"
        if group.is_check_stock:
            label += " · только проверить остаток"
        with st.expander(f"{label} — {len(group.items)} поз."):
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def render_tasks_tab() -> None:
    st.subheader("Задачи по ролям")
    sheet, error = compute_sheet(st.session_state.menu)
    if error:
        st.info(error)
        return
    rows = []
    for task in sheet.tasks:
        subjects = ", ".join(task.recipes) if task.recipes else task.source
        rows.append(
            {
                "Время": f"{task.start:%H:%M}–{task.end:%H:%M}",
                "Этап": task.stage,
                "Роль": task.role,
                "Операция": task.operation,
                "Приём": task.meal,
                "Блюдо": subjects,
                "Продукты": task.products or "—",
                "Мин": task.duration_min,
            }
        )
    chronological = pd.DataFrame(rows)
    st.caption("Все задачи в хронологическом порядке")
    st.dataframe(chronological, hide_index=True, width="stretch")
    for role, tasks in sheet.tasks_by_role:
        role_rows = [r for r in rows if r["Роль"] == role]
        title = f"{role} — {len(role_rows)} задач"
        with st.expander(title, expanded=True):
            st.dataframe(
                pd.DataFrame(role_rows), hide_index=True, width="stretch"
            )


def render_export_tab() -> None:
    st.subheader("Экспорт")
    sheet, error = compute_sheet(st.session_state.menu)
    if error:
        st.info(error)
        return

    if st.button("Собрать книгу Excel", width="stretch"):
        try:
            data = data_for(st.session_state.menu)
            folder = Path(tempfile.gettempdir()) / "shegchef_exports"
            folder.mkdir(parents=True, exist_ok=True)
            name = f"Кухня_конструктор_{st.session_state.day:%d_%m_%Y}.xlsx"
            path = build_workbook(data, build_shift_sheet(data), folder / name)
            st.session_state.excel_bytes = path.read_bytes()
            st.session_state.excel_name = path.name
            st.success(f"Книга собрана: {path.name}")
        except PermissionError:
            st.error(
                "Не удалось записать книгу: файл занят другой программой "
                "(Excel/LibreOffice). Закройте его и повторите."
            )
        except OSError as exc:
            st.error(f"Ошибка создания Excel: {exc}")

    if st.session_state.excel_bytes:
        st.download_button(
            "Скачать книгу Excel",
            data=st.session_state.excel_bytes,
            file_name=st.session_state.excel_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )

    st.divider()
    st.subheader("Текстовые задания для сотрудников")
    st.caption("Файлы — обычный текст, удобный для отправки в Telegram. "
               "Отправка через Telegram Bot API не подключена.")
    short = st.session_state.day.strftime("%d.%m.%Y")
    st.download_button(
        "Скачать все задания одним файлом (.txt)",
        data=all_messages_text(sheet).encode("utf-8"),
        file_name=f"задания_{short}.txt",
        width="stretch",
    )
    for message in messages_by_role(sheet):
        with st.expander(f"Задание: {message.role}"):
            st.code(message.text, language=None)
            st.download_button(
                f"Скачать задание: {message.role}",
                data=message.text.encode("utf-8"),
                file_name=f"{message.filename}_{short}.txt",
            )
            st.text_area(
                "Или скопируйте текст",
                message.text,
                height=180,
                key=f"copy_text_{message.role}",
            )


# ---------------------------------------------------------------------------
# Сборка интерфейса
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Конструктор кухни",
        page_icon="🍳",
        layout="wide",
    )
    st.title("Конструктор кухни — планирование смены")
    st.caption("Локальный интерфейс: меню дня, масштабирование порций, "
               "расчёт и экспорт. Без Telegram-ботов и сетевых сервисов.")

    bootstrap()

    with st.sidebar:
        st.subheader("Смена")
        st.date_input(
            "Рабочая дата",
            value=st.session_state.day,
            key="_day",
            on_change=on_day_changed,
        )
        st.selectbox(
            "День недели из плана Пн–Пт",
            WEEKDAYS,
            index=(
                WEEKDAYS.index(st.session_state.weekday)
                if st.session_state.weekday in WEEKDAYS
                else 0
            ),
            key="_weekday",
            on_change=on_weekday_changed,
        )
        st.caption("Выбор дня недели загружает его меню из существующего плана. "
                   "Правки не трогают Excel — они хранятся в локальном JSON.")
        if st.button("Начать с пустого меню"):
            st.session_state.menu = []
            st.session_state.preview = None
            st.session_state.menu_source = "пустое меню"
            st.session_state.menu_seed += 1
            invalidate_sheet()
            persist_store()
            st.rerun()

    tab_menu, tab_calc, tab_products, tab_tasks, tab_export = st.tabs(TABS)
    with tab_menu:
        render_menu_tab()
    with tab_calc:
        render_calc_tab()
    with tab_products:
        render_products_tab()
    with tab_tasks:
        render_tasks_tab()
    with tab_export:
        render_export_tab()


if __name__ == "__main__":
    main()