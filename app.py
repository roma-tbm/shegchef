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

from kitchen import calendar_rules
from kitchen.core import build_shift_sheet
from kitchen.core.deviations import DEVIATION_LABELS
from kitchen.core.planner import STATUSES
from kitchen.duties import duties_for
from kitchen.excel import build_workbook
from kitchen.menu_week import WEEK_PLAN, WEEKDAYS
from kitchen.models import MenuLine, PlanLine, Recipe
from kitchen.seed import demo_data
from kitchen.web import (
    deviation_store,
    menu_store,
    report_store,
    settings_store,
    status_store,
)
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
from kitchen.web.report_store import SaturdayReport, SemifinishedMarking, SemifinishedOutput
from kitchen.web.scaling import ScaleError, scaled_preview, validate_factor
from kitchen.web.settings_store import (
    duty_settings_from,
    load_day_inputs,
    save_day_inputs,
    save_settings,
    staff_settings_from,
)
from kitchen.web.shift_plan import (
    build_scenario,
    gaps_text,
    general_plan_form,
    next_day_menu,
    prep_target_day,
    route_form,
    routes_by_form,
    saturday_report_form,
)

COL_DISH = "Блюдо"
COL_MEAL = "Приём"
COL_PORT = "Порций"
COL_SERVE = "Выдача"
COL_REMOVE = "Убрать"

#: Отклонение в форме выбирается вместе с пустым вариантом «не было».
NO_DEVIATION = (deviation_store.NO_DEVIATION_KIND, "— не было —")

#: Подписи полей численности — те же значения, что принимает расчёт.
_COUNT_LABELS = {
    "chefs": "Шефы",
    "cooks": "Повара",
    "helper": "Разнорабочие",
    "cleaners": "Уборщики",
}
_WEEKEND_LABELS = {"saturday": "Суббота", "sunday": "Воскресенье"}

#: Виджеты форм, значения которых относятся к выбранной рабочей дате.
#: Streamlit хранит значение виджета по ключу и игнорирует новый `value=` при
#: следующей отрисовке, поэтому при смене дня их нужно сбросить вручную — иначе
#: форма показывала бы значения прежнего дня и сохранила бы их за новый.
_DAY_WIDGET_PREFIXES = (
    "duration_",
    "window_",
    "count_",
    "staff_",
    "weekend_",
    "consumption_",
    "output_",
    "marking_",
    "made_",
    "expires_",
    "copy_text_",
)
_DAY_WIDGET_KEYS = (
    "tasting_executor",
    "disabled_duties",
    "settings_shared",
    "new_staff_name",
    "new_staff_row",
    "notes_editor",
)

TABS = (
    "Меню",
    "Расчёт смены",
    "Продукты и дефицит",
    "Задачи по ролям",
    "Маршруты",
    "Экспорт",
    "Настройки смены",
    "Отклонения",
    "Отчёт за субботу",
)


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


def compute_sheet(
    menu: list[MenuLine], plan: object | None = None
) -> tuple[object | None, str | None]:
    """Расчёт смены существующим ядром. Возвращает (смена, ошибка).

    Если передан авторитетный `plan`, книга берёт расписание и причины
    неготовности из него, а не пересчитывает своё — иначе старые вкладки
    показывали бы другую смену, чем маршруты и общий план (AC12).
    """
    if not menu:
        return None, "Меню пустое: добавьте блюда во вкладке «Меню»."
    try:
        return build_shift_sheet(data_for(menu), plan=plan), None
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


def status_path() -> Path:
    """Файл статусов рядом с меню пользователя."""
    return status_store.default_status_path_for(st.session_state.store)


def settings_path() -> Path:
    """Файл настроек смены рядом с меню пользователя."""
    return settings_store.default_settings_path(st.session_state.store)


def notes_path() -> Path:
    """Файл отклонений и комментариев рядом с меню пользователя."""
    return deviation_store.default_notes_path(st.session_state.store)


def report_path() -> Path:
    """Файл отчётов рядом с меню пользователя."""
    return report_store.default_report_path(st.session_state.store)


def saved_report() -> SaturdayReport | None:
    """Сохранённый субботний отчёт пользователя, если он уже заполнен (AC09)."""
    return report_store.load_report(st.session_state.day, report_path())


def day_inputs() -> dict:
    """Настройки текущего дня: свои значения плюс общие как основа."""
    return load_day_inputs(st.session_state.day, settings_path())


def has_saved_settings() -> bool:
    """Сохранял ли человек настройки хоть раз.

    Пока настройки не сохранены, действуют значения по умолчанию: приложение
    обязано показать план сразу, а не пустые маршруты. После первого
    сохранения работают только введённые значения, включая нули.
    """
    return settings_path().exists()


def stored_menu(day: date) -> list[MenuLine]:
    """Меню даты из пользовательского хранилища, без подстановки заготовки.

    Подготовка следующего дня опирается на то, что человек действительно
    сохранил для этого дня (AC10, AC11). Заготовка недельного плана здесь не
    подставляется: воскресный расчёт должен видеть пустое меню, если
    понедельник не заполнен, а не чужие блюда.
    """
    return list(load_days(st.session_state.store).get(day.isoformat(), ()))


def next_day_menu_for(day: date) -> tuple[MenuLine, ...]:
    """Меню дня, к которому готовит смена `day`."""
    return tuple(stored_menu(prep_target_day(day)))


def compute_plan(menu: list[MenuLine]):
    """Сценарий смены целиком: настройки, статусы, комментарии и отклонения.

    Всё, что человек ввёл сам, доходит до расчёта отсюда, поэтому план
    показывает ровно ту смену, которую настроили, а не расчёт по умолчанию.
    """
    notes = deviation_store.load_day_notes(st.session_state.day, notes_path())
    deviations = deviation_store.load_deviations(st.session_state.day, notes_path())
    settings: dict = day_inputs() if has_saved_settings() else {}
    scenario = build_scenario(
        data_for(menu),
        st.session_state.day,
        menu=tuple(menu),
        next_menu=next_day_menu_for(st.session_state.day),
        duty_settings=duty_settings_from(settings) if settings else None,
        staff_settings=staff_settings_from(settings) if settings else None,
        overrides=status_store.overrides_for_day(st.session_state.day, status_path()),
        notes=tuple(sorted(notes.items())),
        deviations=tuple(
            (item_id, record.kind) for item_id, record in sorted(deviations.items())
        ),
    )
    # Сценарий кладём в сессию: все вкладки обязаны показывать одну и ту же
    # смену, иначе маршруты, отчёт и экспорт могут опираться на разные расчёты.
    st.session_state.scenario = scenario
    return scenario


def save_status(item_id: str, status: str) -> None:
    """Запоминает отметку человека по конкретной работе."""
    status_store.save_statuses(
        {st.session_state.day.isoformat(): ((item_id, status),)},
        status_path(),
    )
    invalidate_plan()


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
    st.session_state.plan = None
    st.session_state.scenario = None
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


def invalidate_plan() -> None:
    """Сброс кэша расчёта: план зависит и от меню, и от введённых статусов."""
    invalidate_sheet()
    st.session_state.plan = None
    st.session_state.scenario = None


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
    _reset_day_widgets()
    st.session_state.menu, st.session_state.menu_source = menu_for_day(
        new_day, load_days(st.session_state.store)
    )
    st.session_state.preview = None
    st.session_state.menu_seed += 1
    invalidate_plan()


def _reset_day_widgets() -> None:
    """Забывает значения виджетов, привязанных к выбранному дню.

    Без сброса форма настроек показывала бы данные прежнего дня: Streamlit
    игнорирует новый `value=`, если ключ виджета уже есть в session_state.
    """
    for key in [
        key
        for key in st.session_state
        if key in _DAY_WIDGET_KEYS or key.startswith(_DAY_WIDGET_PREFIXES)
    ]:
        del st.session_state[key]


def on_weekday_changed() -> None:
    st.session_state.weekday = st.session_state._weekday
    st.session_state.day = st.session_state._day
    st.session_state.menu = list(
        plan_to_menu(WEEK_PLAN, st.session_state.weekday, st.session_state.day)
    )
    st.session_state.menu_source = f"план «{st.session_state.weekday}»"
    st.session_state.preview = None
    st.session_state.menu_seed += 1
    invalidate_plan()


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
    invalidate_plan()
    persist_store()


def on_apply_scale() -> None:
    st.session_state.menu = sort_menu(
        [scaled for _, scaled in st.session_state.preview]
    )
    st.session_state.preview = None
    st.session_state.menu_seed += 1
    invalidate_plan()
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
        invalidate_plan()
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
        scenario = compute_plan(st.session_state.menu)
        sheet, error = compute_sheet(st.session_state.menu, scenario.plan)
        if error:
            st.error(error)
        else:
            st.session_state.sheet = sheet
    sheet = st.session_state.sheet
    if sheet is None:
        scenario = compute_plan(st.session_state.menu)
        sheet, error = compute_sheet(st.session_state.menu, scenario.plan)
        if error:
            st.info(error)
            return
        st.session_state.sheet = sheet
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
    scenario = compute_plan(st.session_state.menu)
    sheet, error = compute_sheet(st.session_state.menu, scenario.plan)
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


def render_routes_tab() -> None:
    st.subheader("Маршруты по сотрудникам")
    scenario = compute_plan(st.session_state.menu)
    plan = scenario.plan

    if not plan.staff:
        st.warning(
            "Состав бригады на этот день не задан: маршруты построить нельзя. "
            "Задайте состав в настройках смены или выберите будний день."
        )
    counts = plan.status_counts()
    col1, col2, col3 = st.columns(3)
    col1.metric("Готовность смены", "готова" if plan.ready else "не готова")
    col2.metric("Не заполнено", len(plan.gaps))
    col3.metric("Работ в маршрутах", sum(len(r.items) for r in plan.routes))
    if counts:
        st.caption(" · ".join(f"{name}: {value}" for name, value in counts.items() if value))

    if plan.gaps:
        st.warning("Смена не готова: перечисленное ниже не заполнено.")
        for line in gaps_text(plan.gaps).splitlines():
            st.write(line)

    st.subheader("Отметки по работам")
    st.caption(
        "Статус сохраняется локально и относится только к этой работе. "
        "После пересчёта отметки остаются на своих местах."
    )
    rows = []
    for item in plan.items:
        rows.append(
            {
                "id": item.item_id,
                "Время": item.span,
                "Операция": item.operation,
                "Статус": item.status,
                "Отметка человека": item.claimed_status,
                "Комментарий": item.comment,
                "Отклонение": DEVIATION_LABELS.get(item.deviation, ""),
            }
        )
    if rows:
        edited = st.data_editor(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            disabled=[
                "id",
                "Время",
                "Операция",
                "Отметка человека",
                "Комментарий",
                "Отклонение",
            ],
            column_config={
                "id": st.column_config.TextColumn("id", width="small"),
                "Статус": st.column_config.SelectboxColumn(
                    "Статус", options=list(STATUSES), required=True
                ),
            },
        )
        _apply_status_changes(edited, plan)
        _explain_claimed(plan)

    if plan.legacy:
        st.subheader("Исторические записи прежней выдачи")
        st.caption(
            "До разделения выдачи на роли работа имела один общий ID. Прежние "
            "статус, комментарий и отклонение сохранены, но исполнитель из "
            "хранилища не установлен: записи не переносятся на новую бригаду и "
            "не меняют готовность плана."
        )
        for record in plan.legacy:
            st.write(record.message)

    for route in plan.routes:
        employee = route.employee
        title = f"{employee.name} · {employee.role} · {len(route.items)} работ"
        with st.expander(title):
            st.code(route_form(scenario, employee).to_text(), language=None)
            st.download_button(
                f"Скачать маршрут: {employee.name}",
                data=route_form(scenario, employee).to_text().encode("utf-8"),
                file_name=f"маршрут_{_safe_name(employee.name)}.txt",
            )


def _explain_claimed(plan) -> None:
    """Показывает разницу между фактом и допустимостью (AC08, AC15).

    Отметка человека не снимает нарушение ограничения, поэтому обе строки
    показываются рядом: «выполнено по факту» и «нарушает правило».
    """
    claimed = [i for i in plan.items if i.claimed_status]
    if not claimed:
        return
    st.info(
        "Отметка «выполнено» не снимает нарушение ограничения: работа остаётся "
        "заблокированной, а ваш факт сохранён отдельно."
    )
    for item in claimed:
        st.write(
            f"{item.span} · {item.operation} — факт: {item.claimed_status}; "
            f"причина блокировки: {item.reason or 'нарушение ограничения'}"
        )


def _apply_status_changes(edited: pd.DataFrame, plan) -> None:
    """Сохраняет только реально изменившиеся статусы."""
    before = {item.item_id: item.status for item in plan.items}
    changed = [
        (str(row["id"]), str(row["Статус"]).upper())
        for _, row in edited.iterrows()
        if str(row["id"]) in before and str(row["Статус"]).upper() != before[str(row["id"])]
    ]
    if not changed:
        return
    for item_id, status in changed:
        save_status(item_id, status)
    st.rerun()


def _safe_name(name: str) -> str:
    return "-".join(
        "".join(ch if ch.isalnum() or ch in " -" else "_" for ch in name).split()
    ) or "сотрудник"


def render_settings_tab() -> None:
    """Ввод того, чего нет в источнике: длительности, окна, состав, бракераж.

    Форма показывает только обязанности выбранного дня: субботние окна не нужны
    в понедельник и наоборот, а лишние поля в форме только мешают.
    """
    st.subheader("Настройки смены")
    st.caption(
        "Здесь вводятся значения, которых нет в плане: длительности и окна "
        "работ, состав бригады и исполнитель бракеража. Пустое поле остаётся "
        "незаполненным и попадает в план как пропуск — ничего не подставляется "
        "за вас. Настройки хранятся локально и не попадают в Excel."
    )

    inputs = day_inputs()
    day = st.session_state.day
    mode = _mode_of(day)
    applicable = duties_for(day.weekday(), mode)

    with st.form("settings_form"):
        st.markdown("**Обязанности дня**")
        durations: dict[str, str] = {}
        windows: dict[str, str] = {}
        for duty in applicable:
            label = f"{duty.source} · {duty.operation}"
            known = duty.duration_min
            durations[duty.key] = st.text_input(
                f"Длительность, мин — {label}",
                value=inputs["durations"].get(duty.key, ""),
                placeholder="не задана в источнике" if known is None else str(known),
                key=f"duration_{duty.key}",
            )
            windows[duty.key] = st.text_input(
                f"Окно работы — {label}",
                value=inputs["windows"].get(duty.key, ""),
                placeholder=_window_placeholder(duty),
                key=f"window_{duty.key}",
            )

        st.markdown("**Персонал**")
        staff_text = inputs["staff"]
        counts = {
            key: st.text_input(
                f"Количество — {_COUNT_LABELS[key]}",
                value=staff_text.get(key, ""),
                key=f"count_{key}",
            )
            for key in settings_store.COUNT_KEYS
        }
        staff_rows = {
            key: st.text_input(
                f"Сотрудник — {key} (роль|смена|перерывы)",
                value=staff_text.get(key, ""),
                placeholder="Шеф-повар 1|Шеф-повар|08:00–17:00|09:00–09:30",
                key=f"staff_{key}",
            )
            for key in sorted(
                k for k in staff_text if k not in settings_store.COUNT_KEYS
            )
        }
        weekend = {
            key: st.text_area(
                f"Состав — {_WEEKEND_LABELS[key]} (имя|роль|смена, по строке)",
                value=staff_text.get(key, ""),
                placeholder="Разнорабочий (суб)|Разнорабочий|08:00–14:00",
                key=f"weekend_{key}",
            )
            for key in settings_store.WEEKEND_KEYS
        }
        new_staff_name = st.text_input(
            "Добавить сотрудника: имя",
            value="",
            key="new_staff_name",
        )
        new_staff_row = st.text_input(
            "Добавить сотрудника: роль|смена|перерывы",
            value="",
            key="new_staff_row",
        )
        tasting = st.text_input(
            "Исполнитель бракеража",
            value=inputs["tasting"],
            placeholder="без настройки точка остаётся неназначенной",
            key="tasting_executor",
        )
        disabled = st.multiselect(
            "Не выполнять на этот день",
            [d.key for d in applicable],
            default=[k for k in inputs.get("disabled", []) if k in {d.key for d in applicable}],
            key="disabled_duties",
        )
        apply_to_all = st.checkbox(
            "Сохранить как общие настройки для всех дней",
            value=False,
            key="settings_shared",
        )
        submitted = st.form_submit_button("Сохранить настройки")

    if submitted:
        payload = {
            "durations": {k: v for k, v in durations.items() if v.strip()},
            "windows": {k: v for k, v in windows.items() if v.strip()},
            "staff": {
                **{k: v for k, v in counts.items() if v.strip()},
                **{k: v for k, v in staff_rows.items() if v.strip()},
                **{k: v for k, v in weekend.items() if v.strip()},
            },
            "tasting": tasting.strip(),
            "disabled": list(disabled),
        }
        if new_staff_name.strip() and new_staff_row.strip():
            payload["staff"][new_staff_name.strip()] = new_staff_row.strip()
        try:
            _validate_settings(payload)
        except ValueError as exc:
            st.error(f"Настройки не сохранены: {exc}")
            return
        save_day_inputs(day, payload, settings_path())
        if apply_to_all:
            save_settings(payload, settings_path())
        invalidate_plan()
        st.rerun()

    if inputs["tasting"]:
        st.caption(f"Бракераж на этот день: {inputs['tasting']}")
    elif not has_saved_settings():
        st.info(
            "Настройки ещё не сохранялись: план построен по значениям по "
            "умолчанию. Состав бригады в них — заглушка, а не подтверждённые "
            "данные; задайте свой состав."
        )


def _validate_settings(payload: dict) -> None:
    """Проверяет ввод до сохранения: понятная ошибка лучше пустого расчёта."""
    duty_settings_from(payload)
    staff_settings_from(payload)


def _mode_of(day: date) -> str:
    """Режим дня по тем же календарным правилам, что и расчёт."""
    return calendar_rules.mode_for(day)


def _window_placeholder(duty) -> str:
    if duty.window is not None:
        return f"из плана: {duty.window[0]:%H:%M}–{duty.window[1]:%H:%M}"
    return "не задано в источнике"


def render_notes_tab() -> None:
    """Комментарии и отклонения по конкретным работам (п. 42, AC12)."""
    st.subheader("Отклонения и комментарии")
    scenario = compute_plan(st.session_state.menu)
    plan = scenario.plan

    st.caption(
        "Отклонение — причина с типом: задержка, отсутствие или замена продукта, "
        "изменение порций, проблема оборудования или другая причина. "
        "Комментарий и причина привязаны к конкретной работе и переживают "
        "пересчёт; статус выполнения хранится отдельно."
    )

    rows = []
    stored = deviation_store.load_deviations(st.session_state.day, notes_path())
    for item in plan.items:
        record = stored.get(item.item_id)
        rows.append(
            {
                "id": item.item_id,
                "Время": item.span,
                "Операция": item.operation,
                "Комментарий": item.comment,
                "Отклонение": item.deviation or deviation_store.NO_DEVIATION_KIND,
                "Минуты": str(record.minutes) if record else "",
            }
        )
    for legacy in plan.legacy:
        # PA-03: старую запись показываем строкой формы, чтобы сохранение
        # нового дня не стёрло её молча. Строка не привязана к человеку:
        # исполнитель прежней единой выдачи неизвестен.
        saved = stored.get(legacy.item_id)
        rows.append(
            {
                "id": legacy.item_id,
                "Время": "—",
                "Операция": f"{legacy.operation} (историческая запись)",
                "Комментарий": legacy.comment or (saved.comment if saved else ""),
                "Отклонение": legacy.deviation
                or deviation_store.NO_DEVIATION_KIND,
                "Минуты": str(saved.minutes) if saved else "",
            }
        )
    if not rows:
        st.warning("Работ на этот день нет: записывать нечего.")
        return

    edited = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        disabled=["id", "Время", "Операция"],
        column_config={
            "id": st.column_config.TextColumn("id", width="small"),
            "Отклонение": st.column_config.SelectboxColumn(
                "Отклонение",
                options=[NO_DEVIATION, *deviation_store.deviation_kinds()],
            ),
        },
        key="notes_editor",
    )
    if st.button("Сохранить комментарии и отклонения", width="stretch"):
        _save_notes(edited)

    saved = deviation_store.saved_rows(st.session_state.day, notes_path())
    if saved:
        st.markdown("**Сохранённые записи**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "id": row["id"],
                        "Комментарий": row["comment"],
                        "Отклонение": row["label"],
                        "Минуты": row["minutes"],
                    }
                    for row in saved
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Сохранённых комментариев и отклонений за этот день нет.")


def _save_notes(edited: pd.DataFrame) -> None:
    """Сохраняет комментарии и отклонения, отвергая незаполненные причины."""
    rows = [
        {
            "id": row["id"],
            "comment": row.get("Комментарий", ""),
            "kind": row.get("Отклонение", ""),
            "minutes": row.get("Минуты", ""),
        }
        for _, row in edited.iterrows()
    ]
    saved, problems = deviation_store.save_from_rows(
        st.session_state.day, rows, notes_path()
    )
    if problems:
        st.error(
            "Не сохранено. Для отклонения укажите пояснение (и минуты, если это "
            "задержка):\n\n" + "\n\n".join(problems)
        )
        return
    invalidate_plan()
    st.rerun()


def render_report_tab() -> None:
    """Субботний отчёт: расход, выход полуфабрикатов и маркировка (AC09)."""
    st.subheader("Отчёт за субботу")
    scenario = compute_plan(st.session_state.menu)
    day = st.session_state.day
    if day.weekday() != 5:
        st.info("Отчёт заполняется за субботу. Выберите рабочую дату — субботу.")
        return

    saved = report_store.load_report(day, report_path()) or SaturdayReport(day=day)
    product_units = {
        need.product: need.unit
        for group in scenario.needs
        for need in group.items
    }
    products = sorted(product_units)
    if not products:
        st.warning("Нет продуктов в плане: заполнять нечего.")
        return

    st.caption(
        "Расход сырья и выход полуфабриката — разные величины в своих единицах: "
        "норма выхода в источнике не подтверждена, поэтому выход остаётся пустым, "
        "пока вы его не введёте. Отчёт ничего не списывает со склада."
    )

    with st.form("saturday_report"):
        consumption: dict[str, float] = {}
        qty: dict[str, float] = {}
        units: dict[str, str] = {}
        names: dict[str, str] = {}
        made: dict[str, date] = {}
        expires: dict[str, date] = {}
        for product in products:
            st.markdown(f"**{product}**")
            col1, col2, col3 = st.columns(3)
            consumption[product] = _qty_input(
                col1, f"Расход факт — {product}", saved.consumption.get(product),
                f"consumption_{product}",
            )
            unit = saved.outputs.get(product)
            qty[product] = _qty_input(
                col2, f"Выход — {product}", unit.qty if unit else None, f"output_{product}"
            )
            units[product] = col2.text_input(
                f"Ед. выхода — {product}",
                value=unit.unit if unit else "",
                key=f"output_unit_{product}",
            )
            marking = saved.markings.get(product)
            names[product] = col3.text_input(
                f"Название полуфабриката — {product}",
                value=marking.name if marking else "",
                key=f"marking_{product}",
            )
            col4, col5 = st.columns(2)
            made[product] = col4.date_input(
                f"Дата производства — {product}",
                value=marking.made if marking and marking.made else day,
                key=f"made_{product}",
            )
            expires[product] = col5.date_input(
                f"Срок годности — {product}",
                value=marking.expires if marking and marking.expires else day,
                key=f"expires_{product}",
            )
        submitted = st.form_submit_button("Сохранить отчёт")

    if submitted:
        # Единица расхода сохраняется рядом с количеством: она относится к
        # факту, а не к текущему меню, и переживает его изменение или очистку.
        consumption_units = {
            k: (product_units.get(k) or saved.consumption_units.get(k, ""))
            for k in products
            if consumption.get(k)
            and (product_units.get(k) or saved.consumption_units.get(k, ""))
        }
        report = SaturdayReport(
            day=day,
            consumption={k: v for k, v in consumption.items() if v},
            consumption_units=consumption_units,
            outputs={
                k: SemifinishedOutput(qty=qty[k], unit=units[k].strip())
                for k in products
                if qty.get(k)
            },
            markings={
                k: SemifinishedMarking(
                    name=names[k].strip(), made=made[k], expires=expires[k]
                )
                for k in products
                if qty.get(k)
            },
        )
        missing = report.incomplete_markings
        if missing:
            st.error(
                "Отчёт не сохранён: у полуфабрикатов не заполнено название, "
                "дата производства или срок годности — " + ", ".join(missing)
            )
            return
        report_store.save_report(day, report, report_path())
        st.success("Отчёт сохранён локально.")
        st.code(
            saturday_report_form(
                scenario,
                actuals=report.consumption,
                outputs=report.outputs,
                markings=report.markings,
            ).to_text(),
            language=None,
        )


def _qty_input(column, label: str, value: float | None, key: str) -> float:
    """Числовое поле количества: пустое поле — это «не заполнено», а не ноль."""
    try:
        current = float(value) if value else None
    except (TypeError, ValueError):
        current = None
    return column.number_input(
        label, min_value=0.0, value=current, step=0.5, key=key
    ) or 0.0


def _saturday_report_export(scenario):
    """Форма сохранённого субботнего отчёта или None в другой день.

    Отчёт не зависит от текущего меню: это отдельно сохранённый факт, поэтому
    его форма собирается независимо от книги и печатается даже после очистки
    меню (AC09, AC14).
    """
    if st.session_state.day.weekday() != 5:
        return None
    report = saved_report()
    facts = (
        {}
        if report is None
        else {
            "actuals": dict(report.consumption),
            "actual_units": dict(report.consumption_units),
            "outputs": dict(report.outputs),
            "markings": dict(report.markings),
        }
    )
    return saturday_report_form(scenario, **facts)


def render_export_tab() -> None:
    st.subheader("Экспорт")
    scenario = compute_plan(st.session_state.menu)
    sheet, error = compute_sheet(st.session_state.menu, scenario.plan)
    if error:
        st.info(error)
        report_form = _saturday_report_export(scenario)
        if report_form is not None:
            st.download_button(
                f"Скачать: {report_form.title}",
                data=report_form.to_text().encode("utf-8"),
                file_name=f"{_safe_name(report_form.title)}.txt",
            )
        return

    if st.button("Собрать книгу Excel", width="stretch"):
        try:
            data = data_for(st.session_state.menu)
            folder = Path(tempfile.gettempdir()) / "shegchef_exports"
            folder.mkdir(parents=True, exist_ok=True)
            name = f"Кухня_конструктор_{st.session_state.day:%d_%m_%Y}.xlsx"
            path = build_workbook(
                data, build_shift_sheet(data, plan=scenario.plan), folder / name
            )
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

    st.divider()
    st.subheader("Печатные формы")
    st.caption("Общий план, маршрут каждого сотрудника и субботний отчёт — "
               "обычный текст с фиксированными колонками.")
    forms = [general_plan_form(scenario), *routes_by_form(scenario)]
    report_form = _saturday_report_export(scenario)
    if report_form is not None:
        forms.append(report_form)
    for form in forms:
        st.download_button(
            f"Скачать: {form.title}",
            data=form.to_text().encode("utf-8"),
            file_name=f"{_safe_name(form.title)}.txt",
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
            invalidate_plan()
            persist_store()
            st.rerun()

    (
        tab_menu,
        tab_calc,
        tab_products,
        tab_tasks,
        tab_routes,
        tab_export,
        tab_settings,
        tab_notes,
        tab_report,
    ) = st.tabs(TABS)
    with tab_menu:
        render_menu_tab()
    with tab_calc:
        render_calc_tab()
    with tab_products:
        render_products_tab()
    with tab_tasks:
        render_tasks_tab()
    with tab_routes:
        render_routes_tab()
    with tab_export:
        render_export_tab()
    with tab_settings:
        render_settings_tab()
    with tab_notes:
        render_notes_tab()
    with tab_report:
        render_report_tab()


if __name__ == "__main__":
    main()