"""Сквозной сценарий смены для интерфейса и печати.

Модуль не знает про Streamlit и Excel: он получает доменные объекты и
возвращает `ShiftPlan` плюс готовые строки печатных форм. Так его можно
проверять тестами и переиспользовать из CLI, веба и книги.

Сценарий повторяет существующие шаги ядра и ничего не дублирует:

    меню → потребности → операции ТТК → обратный тайминг → маршруты → печать
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping

from kitchen.core.ingredients import (
    NeedGroup,
    calculate_ingredients,
    group_by_category,
)
from kitchen.core.deviations import DEVIATION_LABELS
from kitchen.core.planner import (
    Gap,
    PlannedItem,
    Route,
    ShiftPlan,
    plan_shift,
)
from kitchen.core.tasks import TaskBoard, generate_tasks
from kitchen.core.timing import Timing, calculate_backward_timing
from kitchen.duties import DutySettings
from kitchen.web.report_store import SemifinishedMarking, SemifinishedOutput
from kitchen.models import KitchenData, MenuLine
from kitchen.staff import Employee, StaffSettings, default_staff, weekend_staff


@dataclass(frozen=True, slots=True)
class ShiftScenario:
    """Результат расчёта смены целиком."""

    day: date
    plan: ShiftPlan
    needs: tuple[NeedGroup, ...]
    board: TaskBoard
    timing: Timing
    menu: tuple[MenuLine, ...]
    next_menu: tuple[MenuLine, ...] = ()
    """Меню ближайшего следующего дня — источник подготовки (AC10, AC11)."""

    @property
    def needs_products(self) -> tuple[NeedGroup, ...]:
        return self.needs


def prep_target_day(day: date) -> date:
    """День, к которому готовит эта смена (п. 22, п. 31).

    Это всегда следующий календарный день: для воскресенья это понедельник,
    поэтому переход недели, месяца и года обрабатывается арифметикой дат.
    Поиск «какого-нибудь ближайшего заполненного дня» был молчаливой подменой:
    подготовка к понедельнику превращалась в подготовку ко вторнику.
    """
    return day + timedelta(days=1)


def next_day_menu(data: KitchenData, day: date) -> tuple[MenuLine, ...]:
    """Меню конкретного дня, к которому готовит эта смена (AC10, AC11).

    Берутся строки именно `prep_target_day(day)` с положительным числом
    порций. Поиск «какого-нибудь ближайшего дня» был молчаливой подменой:
    пустое меню понедельника выглядело бы как готовность, если во вторнике
    что-то есть. Отсутствие меню — это причина, которую видно (AC15).
    """
    target = prep_target_day(day)
    return tuple(
        line for line in data.menu if line.day == target and line.portions > 0
    )


def build_scenario(
    data: KitchenData,
    day: date,
    *,
    menu: tuple[MenuLine, ...] | None = None,
    staff: tuple[Employee, ...] | None = None,
    duty_settings: DutySettings | None = None,
    staff_settings: StaffSettings | None = None,
    overrides: tuple[tuple[str, str], ...] = (),
    next_menu: tuple[MenuLine, ...] | None = None,
    notes: tuple[tuple[str, str], ...] = (),
    deviations: tuple[tuple[str, str], ...] = (),
) -> ShiftScenario:
    """Считает смену целиком: от меню до маршрутов.

    `next_menu` — меню дня, к которому готовит эта смена. По умолчанию берётся
    ближайший следующий день из `data.menu`: так воскресный план и вечерняя
    подготовка будней следуют за меню, а не статическому списку (AC10, AC11).
    """
    cfg = staff_settings or StaffSettings()
    day_menu = menu if menu is not None else tuple(
        line for line in data.menu if line.day == day
    )
    board = generate_tasks(day_menu, data.recipes, data.tasks)
    timing = calculate_backward_timing(day, day_menu, board)
    needs = (
        group_by_category(
            calculate_ingredients(day_menu, data.recipes, data.products)
        )
        if day_menu
        else ()
    )
    tomorrow = next_menu if next_menu is not None else next_day_menu(data, day)
    prep_board = generate_tasks(tomorrow, data.recipes, data.tasks)

    roster = staff if staff is not None else _roster(cfg, day)
    shift_plan = plan_shift(
        day=day,
        menu=day_menu,
        timing=timing,
        board=board,
        staff=roster,
        duty_settings=duty_settings,
        tasting_executor=cfg.tasting_executor,
        overrides=overrides,
        next_menu=tomorrow,
        next_board=prep_board,
        notes=notes,
        deviations=deviations,
    )
    return ShiftScenario(
        day=day,
        plan=shift_plan,
        needs=tuple(needs),
        board=board,
        timing=timing,
        menu=day_menu,
        next_menu=tomorrow,
    )


def _roster(cfg: StaffSettings, day: date) -> tuple[Employee, ...]:
    """Состав бригады на день: выходные берутся из настроек, будни — по численности."""
    if day.weekday() in (5, 6):
        roster, _note = weekend_staff(cfg, day)
        return roster
    return default_staff(cfg)


# ---------------------------------------------------------------------------
# Печатные формы (AC14)
# ---------------------------------------------------------------------------

PRINTOUT_FORMS: tuple[str, ...] = (
    "Общий план",
    "Маршрут шеф-повара",
    "Маршрут повара",
    "Маршрут разнорабочего",
    "Маршрут уборщика",
    "Субботний отчёт",
)


#: Физические параметры печати общего плана и маршрутов (AC14).
#: A4 landscape, моноширинный шрифт 10 pt, поля 28 pt. Ширина знака взята у
#: DejaVu Sans Mono (0.602 em); при 10 pt это 6.02 pt. Ширина страницы в
#: символах выводится из этих чисел, а не задана «на глаз»: строка обязана
#: помещаться в доступную полосу без ручного уменьшения шрифта.
PRINT_PAGE = "A4"
PRINT_ORIENTATION = "landscape"
PRINT_FONT = "DejaVu Sans Mono (моноширинный)"
PRINT_FONT_PT = 10
PRINT_CHAR_WIDTH_PT = 6.02
PAGE_MARGIN_PT = 28
PAGE_WIDTH_PT = 841.89
"""Ширина A4 в альбомной ориентации, pt."""

#: Ширина печатной страницы по умолчанию в символах.
PAGE_WIDTH = int((PAGE_WIDTH_PT - 2 * PAGE_MARGIN_PT) // PRINT_CHAR_WIDTH_PT)

#: Подписи колонок, которые можно сжать: там текст, а не время и исполнитель.
#: Список задан подписями, а не номерами, чтобы сжатие работало и в формах
#: с другой составностью колонок, и не сдвинулось при добавлении колонки.
SHRINKABLE_HEADERS: frozenset[str] = frozenset(
    {"Задача", "Блюдо / объект", "Продукты / ТТК", "Комментарий", "Продукт"}
)

#: Подписи, которые сжимать нельзя: по ним читают время и исполнителя.
_FIXED_HEADERS: frozenset[str] = frozenset({"Время", "Статус", "Исполнитель"})

MIN_COLUMN = 8

#: Два пробела между колонками.
_GAP_WIDTH = 2


def wrap_text(text: str, width: int) -> list[str]:
    """Перенос по словам с сохранением слов целиком."""
    if width <= 0 or len(text) <= width:
        return [text]
    out: list[str] = []
    line = ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if len(candidate) <= width:
            line = candidate
            continue
        if line:
            out.append(line)
        while len(word) > width:
            out.append(word[:width])
            word = word[width:]
        line = word
    if line:
        out.append(line)
    return out or [""]


def _wrapped_row(
    row: tuple[str, ...], widths: tuple[int, ...], gap: str
) -> list[str]:
    """Раскладывает ячейки строки по строкам страницы."""
    cells = [wrap_text(cell, widths[i]) for i, cell in enumerate(row)]
    height = max(len(c) for c in cells)
    lines: list[str] = []
    for n in range(height):
        parts = []
        for i, cell in enumerate(cells):
            parts.append((cell[n] if n < len(cell) else "").ljust(widths[i]))
        lines.append(gap.join(parts).rstrip())
    return lines


@dataclass(frozen=True, slots=True)
class Printable:
    """Готовая форма: заголовок, строки и примечания."""

    title: str
    subtitle: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    notes: tuple[str, ...] = ()

    def shrinkable(self) -> tuple[int, ...]:
        """Номера колонок, которые ещё можно ужать."""
        return tuple(
            i
            for i, name in enumerate(self.header)
            if name in SHRINKABLE_HEADERS and name not in _FIXED_HEADERS
        )

    @property
    def widths(self) -> tuple[int, ...]:
        """Ширины колонок по содержимому — колонки не должны обрезаться."""
        return tuple(
            max(len(self.header[i]), *(len(r[i]) for r in self.rows)) if self.rows
            else len(self.header[i])
            for i in range(len(self.header))
        )

    def min_page_width(self) -> int:
        """Самая узкая страница, на которой форма ещё помещается целиком.

        Считается по тем же правилам, что и `fitted_widths`: сжимаются только
        текстовые колонки, время и исполнитель остаются целыми.
        """
        return self._widths_at(MIN_COLUMN, 0)

    def _widths_at(self, floor_width: int, page_width: int) -> int:
        """Ширина таблицы при заданном минимальном размере колонки."""
        widths = [
            floor_width if i in self.shrinkable() else w
            for i, w in enumerate(self.widths)
        ]
        return sum(widths) + _GAP_WIDTH * (len(widths) - 1)

    def to_text(self, page_width: int = PAGE_WIDTH) -> str:
        """Текст формы для печати без обрезанных колонок (AC14).

        Если суммарная ширина таблицы не помещается на страницу, длинные
        значения переносятся по словам внутри своей колонки, а строка
        продолжается ниже с отступом — читаемость важнее жёсткой сетки.
        Переносами обрабатываются и заголовки колонок: иначе подпись длиннее
        своей колонки вытолкнула бы строку за край страницы.

        Если даже минимальные колонки не помещаются, вызывающий код получает
        ошибку с требуемой шириной, а не текст, который молча не влезает.
        """
        needed = self.min_page_width()
        if page_width < needed:
            raise ValueError(
                f"форма не помещается на страницу шириной {page_width}: "
                f"нужно от {needed} символов"
            )
        widths = self.fitted_widths(page_width)
        gap = " " * _GAP_WIDTH
        lines = [self.title, self.subtitle, ""]
        lines.extend(_wrapped_row(self.header, widths, gap))
        lines.append(gap.join("-" * w for w in widths))
        for row in self.rows:
            lines.extend(_wrapped_row(row, widths, gap))
        for note in self.notes:
            lines.extend(wrap_text(note, page_width))
        return "\n".join(lines)

    def fitted_widths(self, page_width: int = PAGE_WIDTH) -> tuple[int, ...]:
        """Ширины колонок, уменьшенные до ширины страницы.

        Ни одна колонка не сжимается ниже `MIN_COLUMN`, а лишнее место
        остаётся у текстовых колонок — иначе длинная операция вытеснила бы
        время и исполнителя.
        """
        natural = self.widths
        if self._widths_at(MIN_COLUMN, page_width) > page_width:
            # Даже минимальные колонки не влезают: сузить нечего, и лучше
            # сказать об этом, чем отдать текст, уходящий за край страницы.
            return natural
        total = sum(natural) + _GAP_WIDTH * (len(natural) - 1)
        if total <= page_width:
            return natural
        over = total - page_width
        widths = list(natural)
        shrinkable = [
            i for i, w in enumerate(natural)
            if i in self.shrinkable() and w > MIN_COLUMN
        ]
        # Перебор распределяется по чуть-чуть от самой широкой колонки: так ни
        # одна колонка не падает до минимума, пока у соседней есть запас.
        # Текст не обрезается, а переносится — «Комментарий» остаётся читаемым
        # (AC14): в противном случае широкая «Задача» съедала бы его целиком.
        while over > 0:
            candidates = [i for i in shrinkable if widths[i] > MIN_COLUMN]
            if not candidates:
                break
            widest = max(candidates, key=lambda i: widths[i])
            widths[widest] -= 1
            over -= 1
        return tuple(widths)


def _item_row(item: PlannedItem) -> tuple[str, ...]:
    """Строка маршрута со всеми полями, которые печатаются (AC12, AC14)."""
    return (
        item.span,
        item.status,
        item.operation,
        item.meal or "—",
        item.recipe or "—",
        item.detail or "—",
        str(item.portions) if item.portions else "—",
        item.assignee or "—",
        _remarks(item),
    )


def _remarks(item: PlannedItem) -> str:
    """Причина пропуска, отклонение и комментарий человека в одной ячейке.

    Печать должна показывать то же, что в интерфейсе: если человек отметил
    задержку или замену продукта, это видно и на бумаге, иначе бумага
    противоречит экрану (AC12, AC14).
    """
    parts: list[str] = []
    if item.reason or item.note:
        parts.append((item.reason or item.note).strip())
    if item.deviation:
        parts.append(DEVIATION_LABELS.get(item.deviation, item.deviation))
    if item.comment:
        parts.append(item.comment.strip())
    if item.claimed_status and item.claimed_status != item.status:
        # Отметка «сделано» не подменяет расчётный статус: она печатается
        # рядом как заявленный факт, а работа остаётся BLOCKED (AC12, AC15).
        parts.append(f"факт: {item.claimed_status}")
    return " · ".join(parts) or "—"


_ROUTE_HEADER: tuple[str, ...] = (
    "Время",
    "Статус",
    "Задача",
    "Приём пищи",
    "Блюдо / объект",
    "Продукты / ТТК",
    "Порции",
    "Исполнитель",
    "Комментарий",
)


def general_plan_form(scenario: ShiftScenario) -> Printable:
    """Общий план смены: все работы независимо от исполнителя."""
    plan = scenario.plan
    notes = [f"Режим: {plan.mode}"]
    notes.extend(f"Не готово: {r}" for r in plan.not_ready_reasons)
    if plan.legacy:
        # PA-03: старые записи выдачи живут отдельным историческим блоком.
        # Так они не теряются, но и не раздаются новой бригаде и не влияют
        # на готовность плана.
        notes.append(
            "Исторические записи прежней работы выдачи (не переносятся на новую "
            "бригаду и не меняют готовность):"
        )
        notes.extend(f"• {record.message}" for record in plan.legacy)
    return Printable(
        title=f"Общий план смены — {scenario.day:%d.%m.%Y}",
        subtitle=f"Режим: {plan.mode} · сотрудников: {len(plan.staff)}",
        header=_ROUTE_HEADER,
        rows=tuple(_item_row(i) for i in plan.items),
        notes=tuple(notes),
    )


def route_form(scenario: ShiftScenario, employee: Employee) -> Printable:
    """Персональный маршрут одного сотрудника."""
    route = scenario.plan.route_of(employee.name)
    return Printable(
        title=f"Маршрут — {employee.name}",
        subtitle=(
            f"{scenario.day:%d.%m.%Y} · смена {employee.shift_start:%H:%M}–"
            f"{employee.shift_end:%H:%M} · роль {employee.role}"
        ),
        header=_ROUTE_HEADER,
        rows=tuple(_item_row(i) for i in (route.items if route else ())),
        notes=_route_notes(employee),
    )


def _route_notes(employee: Employee) -> tuple[str, ...]:
    notes = [
        "Перерывы: " + ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in employee.breaks)
    ]
    if employee.works_evening:
        notes.append("Вечерняя часть смены — продолжение той же смены (п. 24)")
    return tuple(notes)


def routes_by_form(scenario: ShiftScenario) -> tuple[Printable, ...]:
    """Формы маршрутов по одной на каждого сотрудника (AC14)."""
    return tuple(route_form(scenario, e) for e in scenario.plan.staff)


def saturday_report_form(
    scenario: ShiftScenario,
    *,
    actuals: Mapping[str, float] | None = None,
    actual_units: Mapping[str, str] | None = None,
    outputs: Mapping[str, "SemifinishedOutput"] | None = None,
    markings: Mapping[str, "SemifinishedMarking"] | None = None,
) -> Printable:
    """Субботний отчёт: продукты, факт, выход, маркировка (AC09).

    Расход сырья и выход полуфабриката — разные величины с разными единицами.
    Расход не копируется в выход: пока норма выхода не подтверждена источником,
    выход остаётся пустым, и это видно в примечаниях, а не выглядит как ноль
    или как «сделано по расходу».

    Отчёт ничего не списывает со склада: он только фиксирует факт (AC09).
    """
    if scenario.day.weekday() != 5:
        raise ValueError("субботний отчёт строится только за субботу")
    fact: Mapping[str, float] = actuals or {}
    units: Mapping[str, str] = actual_units or {}
    out: Mapping[str, SemifinishedOutput] = outputs or {}
    mark: Mapping[str, SemifinishedMarking] = markings or {}
    invalid = [key for key, m in mark.items() if not m.is_complete]
    if invalid:
        raise ValueError(
            "маркировка полуфабриката неполна: " + ", ".join(sorted(invalid))
        )

    header = (
        "Продукт",
        "Ед.",
        "План",
        "Расход факт",
        "Ед. расхода",
        "Выход",
        "Ед. выхода",
        "Название",
        "Дата произв.",
        "Срок годн.",
    )
    # Печатается не только текущее плановое меню: сохранённый факт обязан
    # пережить перезапуск и смену меню, поэтому строка заводится для любого
    # продукта из расхода, выхода или маркировки, даже если его больше нет в
    # плане. Иначе заполненный в субботу отчёт выглядел бы пустым на экспорте.
    needs_by_product = {
        need.product: need for group in scenario.needs for need in group.items
    }
    products: list[str] = []
    seen: set[str] = set()
    for source in (needs_by_product, units, out, fact, mark):
        for product in source:
            if product not in seen:
                seen.add(product)
                products.append(product)

    missing_units: list[str] = []
    rows: list[tuple[str, ...]] = []
    for product in products:
        need = needs_by_product.get(product)
        output = out.get(product)
        labeling = mark.get(product)
        saved_unit = units.get(product, "")
        need_unit = need.unit if need is not None else ""
        output_unit = output.unit if output is not None else ""
        # Единица расхода берётся из сохранённого факта или из плана. Единица
        # выхода полуфабриката для расхода не подставляется: это другая величина.
        consumption_unit = saved_unit or need_unit
        if product in fact and not consumption_unit:
            missing_units.append(product)
        unit = need_unit or saved_unit or output_unit or "—"
        rows.append(
            (
                product,
                unit,
                _qty(need.qty_display) if need is not None else "—",
                _qty(fact.get(product)),
                consumption_unit or "—",
                _qty(output.qty) if output is not None else "—",
                output.unit if output is not None else "—",
                labeling.name if labeling else "—",
                f"{labeling.made:%d.%m.%Y}" if labeling and labeling.made else "—",
                f"{labeling.expires:%d.%m.%Y}" if labeling and labeling.expires else "—",
            )
        )
    if not rows:
        # Пустой отчёт печатается, а не роняет расчёт ширин: строка должна
        # иметь столько же ячеек, сколько заголовок, иначе форма не собирается.
        rows.append(("—",) * len(header))

    notes = [
        f"Суббота {scenario.day:%d.%m.%Y}",
        "Отчёт не списывает складские остатки",
        "Расход сырья и выход полуфабриката — разные величины",
    ]
    if not fact:
        notes.append("Фактический расход не введён")
    if not out:
        notes.append(
            "Выход полуфабрикатов не введён: нормы выхода в источнике нет, "
            "по расходу сырья он не вычисляется"
        )
    if not mark:
        notes.append("Маркировка полуфабрикатов не заполнена")
    if missing_units:
        notes.append(
            "Единица расхода не указана: "
            + ", ".join(sorted(set(missing_units)))
            + " — количество без единицы неполно"
        )
    return Printable(
        title=f"Субботний отчёт — {scenario.day:%d.%m.%Y}",
        subtitle="Фактический расход, выход полуфабрикатов и маркировка",
        header=header,
        rows=tuple(rows),
        notes=tuple(notes),
    )


def _qty(value: float | int | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def gaps_text(gaps: Iterable[Gap]) -> str:
    """Человекочитаемый список незаполненного — для интерфейса и печати."""
    return "\n".join(f"• {g.message}" for g in gaps) or "Пропусков нет"


__all__ = [
    "PAGE_MARGIN_PT",
    "PAGE_WIDTH",
    "PAGE_WIDTH_PT",
    "PRINT_CHAR_WIDTH_PT",
    "PRINT_FONT",
    "PRINT_FONT_PT",
    "PRINT_ORIENTATION",
    "PRINT_PAGE",
    "PRINTOUT_FORMS",
    "Printable",
    "ShiftScenario",
    "build_scenario",
    "gaps_text",
    "general_plan_form",
    "route_form",
    "routes_by_form",
    "saturday_report_form",
]