"""Шаг 5: итоговый лист смены.

Собирает всё вместе в плоский список блоков, пригодный для печати
или отправки в чат кухни.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING

from kitchen.core.ingredients import NeedGroup, ProductNeed, calculate_ingredients
from kitchen.core.tasks import TaskBoard, generate_tasks
from kitchen.core.timing import RoleLoad, ScheduledTask, Timing, calculate_backward_timing
from kitchen.models import ROLES, KitchenData, MenuLine, Product, Recipe, TaskTemplate

if TYPE_CHECKING:
    from kitchen.core.planner import PlannedItem, ShiftPlan

ROLE_ORDER = list(ROLES)


@dataclass(frozen=True, slots=True)
class MenuRow:
    meal: str
    category: str
    subcategory: str
    dish: str
    portions: int
    serve_at: time
    shop: str
    note: str


@dataclass(frozen=True, slots=True)
class TaskLine:
    start: datetime
    end: datetime
    role: str
    stage: str
    meal: str
    operation: str
    source: str
    products: str
    duration_min: int
    note: str
    task_id: str
    recipes: tuple[str, ...] = ()
    assignee: str = ""
    """Исполнитель из авторитетного плана (пусто — источник не назначил)."""
    status: str = ""
    """Статус работы: TODO, IN_PROGRESS, DONE, BLOCKED, SKIPPED (пусто без плана)."""
    claimed_status: str = ""
    """Отметка человека о фактическом выполнении заблокированной работы."""
    comment: str = ""
    """Комментарий человека к работе (п. 42)."""
    item_id: str = ""
    """Устойчивый идентификатор работы плана — тот же, что в маршрутах (AC13)."""


@dataclass(frozen=True, slots=True)
class ShopLine:
    product: str
    qty: str
    unit: str
    action: str
    storage: str
    purchase_unit: str


@dataclass(frozen=True, slots=True)
class ControlLine:
    """Контрольная точка дня с тем же состоянием, что и обычная работа.

    Расчётный статус и заявленный человеком факт хранятся раздельно: отметка
    «сделано» не превращает заблокированную точку в допустимую (AC12, AC15).
    """

    start: datetime
    end: datetime
    role: str
    operation: str
    source: str
    assignee: str = ""
    """Исполнитель контрольной точки из авторитетного плана."""
    status: str = ""
    claimed_status: str = ""
    comment: str = ""
    item_id: str = ""


@dataclass(frozen=True, slots=True)
class SheetBlock:
    """Тип блока печатного листа."""

    kind: str
    title: str
    lines: tuple[tuple[object, ...], ...]
    headers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ShiftSheet:
    kitchen: str
    chief: str
    day: date
    shift: str
    portions: int
    dishes: int
    shift_start: datetime
    shift_end: datetime
    menu: tuple[MenuRow, ...]
    needs: tuple[NeedGroup, ...]
    needs_flat: tuple[ProductNeed, ...]
    tasks: tuple[TaskLine, ...]
    tasks_by_role: tuple[tuple[str, tuple[TaskLine, ...]], ...]
    controls: tuple[ControlLine, ...]
    role_load: tuple[RoleLoad, ...]
    blocks: tuple[SheetBlock, ...]
    notes: tuple[str, ...]
    warnings: tuple[str, ...]


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _fmt_dt(value: datetime) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _span(start: datetime, end: datetime) -> str:
    if start.date() != end.date():
        return f"{_fmt_dt(start)} → {_fmt_dt(end)} (+1 сут)"
    return f"{_fmt_dt(start)}–{_fmt_dt(end)}"


def _menu_rows(data: KitchenData) -> tuple[MenuRow, ...]:
    rows = []
    for line in data.menu:
        r = data.recipes[line.recipe]
        rows.append(
            MenuRow(
                meal=line.meal,
                category=r.category,
                subcategory=r.subcategory,
                dish=r.name,
                portions=line.portions,
                serve_at=line.serve_at,
                shop=r.shop,
                note=line.note,
            )
        )
    order = {"Завтрак": 0, "Обед": 1, "Ужин": 2, "Перекус": 3}
    rows.sort(key=lambda r: (order.get(r.meal, 9), r.subcategory, r.dish))
    return tuple(rows)


def build_shift_sheet(
    data: KitchenData, plan: "ShiftPlan | None" = None
) -> ShiftSheet:
    """Полный вертикальный сценарий: меню → продукты → задачи → тайминг.

    `plan` — авторитетное расписание планировщика. Если он передан, времена,
    исполнители и причины неготовности берутся из него, а не пересчитываются
    заново: книга, общий план и маршруты обязаны показывать одну смену (AC12).
    Без `plan` поведение прежнее — прямой расчёт по данным книги.
    """
    menu: tuple[MenuLine, ...] = data.menu
    recipes: dict[str, Recipe] = data.recipes
    products: dict[str, Product] = data.products
    templates: tuple[TaskTemplate, ...] = data.tasks

    needs = calculate_ingredients(menu, recipes, products)
    board = generate_tasks(menu, recipes, templates)
    day = menu[0].day if menu else date.today()
    timing = calculate_backward_timing(day, menu, board)

    from kitchen.core.ingredients import group_by_category

    need_groups = group_by_category(needs)

    menu_rows = _menu_rows(data)

    if plan is not None:
        # Авторитетный план — единственный источник строк. В лист попадают
        # ВСЕ работы: производство, хозяйственные обязанности, подготовка
        # следующего дня и контрольные точки. Ни одна работа не теряется.
        task_lines = tuple(_task_line_from_plan(item) for item in plan.items)
        controls = _control_lines(task_lines)
        role_load = _role_load_of(task_lines)
        shift_start, shift_end = _plan_bounds(task_lines, timing)
        unavailable = plan.not_ready_reasons if not plan.ready else ()
    else:
        task_lines = tuple(_task_line(st) for st in timing.scheduled)
        controls = _control_lines(task_lines)
        role_load = timing.role_load()
        shift_start, shift_end = timing.shift_start, timing.shift_end
        unavailable = ()

    by_role = tuple(
        (
            role,
            tuple(
                sorted(
                    (t for t in task_lines if t.role == role),
                    key=lambda t: (t.start, t.end),
                )
            ),
        )
        for role in ROLE_ORDER
        if any(t.role == role for t in task_lines)
    )

    warnings = _warnings(
        task_lines, role_load, timing.overruns, needs, _without_tasks(menu, board)
    )
    if unavailable:
        warnings = warnings + tuple(
            f"Смена не готова: {reason}" for reason in unavailable
        )
    notes = (
        f"Смена {shift_start:%H:%M}–{shift_end:%H:%M} · "
        f"порций: {sum(r.portions for r in menu_rows)}",
    )
    notes = notes + tuple(a.text for a in data.assumptions)

    blocks = _blocks(
        data, day, menu_rows, need_groups, task_lines, by_role, controls, role_load
    )

    return ShiftSheet(
        kitchen=data.kitchen,
        chief=data.chief,
        day=day,
        shift=data.shift,
        portions=sum(r.portions for r in menu_rows),
        dishes=len({r.dish for r in menu_rows}),
        shift_start=shift_start,
        shift_end=shift_end,
        menu=menu_rows,
        needs=need_groups,
        needs_flat=needs,
        tasks=task_lines,
        tasks_by_role=by_role,
        controls=controls,
        role_load=role_load,
        blocks=blocks,
        notes=notes,
        warnings=warnings,
    )


def _task_line_from_plan(item: "PlannedItem") -> TaskLine:
    """Строка листа из работы авторитетного плана.

    Переносится всё, что видит человек в маршруте: время, операция, блюдо,
    продукты, исполнитель, статус и комментарий. Хозяйственные работы не имеют
    блюда/ТТК — это допустимо и печатается прочерком.
    """
    return TaskLine(
        start=item.start,
        end=item.end,
        role=item.role,
        stage=item.stage,
        meal=item.meal,
        operation=item.operation,
        source=item.recipe,
        products=", ".join(item.products),
        duration_min=item.duration_min,
        note=item.note,
        task_id=item.task_id,
        recipes=(item.recipe,) if item.recipe else (),
        assignee=item.assignee,
        status=item.status,
        claimed_status=item.claimed_status,
        comment=item.comment,
        item_id=item.item_id,
    )


def _task_line(st: ScheduledTask) -> TaskLine:
    return TaskLine(
        start=st.start,
        end=st.end,
        role=st.role,
        stage=st.stage,
        meal=st.meal,
        operation=st.operation,
        source=st.source,
        products=", ".join(st.task.products),
        duration_min=st.duration_min,
        note=st.task.note,
        task_id=st.task_id,
        recipes=st.task.recipes,
    )


def _control_lines(task_lines: tuple[TaskLine, ...]) -> tuple[ControlLine, ...]:
    """Контрольные точки — работы этапа «Контроль» из того же источника."""
    return tuple(
        ControlLine(
            start=t.start,
            end=t.end,
            role=t.role,
            operation=t.operation,
            source=t.source or t.operation,
            assignee=t.assignee,
            status=t.status,
            claimed_status=t.claimed_status,
            comment=t.comment,
            item_id=t.item_id,
        )
        for t in task_lines
        if t.stage == "Контроль" and t.start < t.end
    )


def _role_load_of(task_lines: tuple[TaskLine, ...]) -> tuple[RoleLoad, ...]:
    """Нагрузка по ролям, посчитанная по строкам авторитетного плана."""
    out: list[RoleLoad] = []
    for role in ROLE_ORDER:
        items = [t for t in task_lines if t.role == role and t.start < t.end]
        if not items:
            continue
        marks = sorted({m for t in items for m in (t.start, t.end)})
        simultaneous = 0
        for i in range(len(marks) - 1):
            mid = marks[i] + (marks[i + 1] - marks[i]) / 2
            simultaneous = max(
                simultaneous, sum(1 for t in items if t.start <= mid < t.end)
            )
        out.append(
            RoleLoad(
                role=role,
                simultaneous=simultaneous,
                tasks=len(items),
                total_min=sum(t.duration_min for t in items),
            )
        )
    return tuple(out)


def _plan_bounds(
    task_lines: tuple[TaskLine, ...], timing: Timing
) -> tuple[datetime, datetime]:
    """Границы смены по авторитетному плану, а не по старому таймингу.

    Берутся реальные начала и концы работ. Работа нулевой длительности
    (агрегатная подготовка) не двигает границы. Если работ нет вовсе,
    остаётся прежняя граница — чтобы пустая смена не выглядела нулевой.
    """
    spans = [(t.start, t.end) for t in task_lines if t.start < t.end]
    if not spans:
        return timing.shift_start, timing.shift_end
    return min(s for s, _ in spans), max(e for _, e in spans)


def _without_tasks(
    menu: tuple[MenuLine, ...], board: TaskBoard
) -> tuple[str, ...]:
    """Блюда в меню, для которых не нашлось ни одной операции.

    Проверка по самому меню, а не по флагу карточки: блюдо в каталоге и
    блюдо в сегодняшней смене — разные вещи, а сдвигать смену молча нельзя.
    """
    return tuple(
        line.recipe for line in menu if line.recipe not in board.chains
    )


def _warnings(
    task_lines: tuple[TaskLine, ...],
    role_load: tuple[RoleLoad, ...],
    overruns: tuple[str, ...],
    needs: tuple[ProductNeed, ...],
    without_tasks: tuple[str, ...] = (),
) -> tuple[str, ...]:
    out: list[str] = []

    if without_tasks:
        out.append(
            "Нет ни одной операции на: "
            + ", ".join(f"«{n}»" for n in without_tasks)
            + " — блюдо попадёт в продукты, но не в задачи. Добавьте строки "
            "в «Задачи»."
        )

    for recipe in overruns:
        out.append(
            f"«{recipe}» не помещается до времени выдачи — "
            f"увеличьте смену или сдвиньте выдачу"
        )

    for load in role_load:
        if load.simultaneous > 1:
            peak = _peak_window(task_lines, load.role)
            out.append(
                f"Роль «{load.role}»: пик {load.simultaneous} одновременных "
                f"операций ({peak}) — нужно минимум {load.people} чел."
            )

    short = [n for n in needs if n.shortage > 0 and n.qty_display > 0]
    if short:
        names = ", ".join(
            f"{n.product} — не хватает {_fmt_qty(n.shortage)} {n.unit}" for n in short
        )
        out.append(f"Не хватает по остаткам: {names}")

    return tuple(out)


def _peak_window(task_lines: tuple[TaskLine, ...], role: str) -> str:
    """Час, в который у роли пиковая нагрузка."""
    items = [t for t in task_lines if t.role == role and t.start < t.end]
    if not items:
        return "—"
    marks = sorted({m for t in items for m in (t.start, t.end)})
    best = (0, marks[0], marks[0])
    for i in range(len(marks) - 1):
        mid = marks[i] + (marks[i + 1] - marks[i]) / 2
        n = sum(1 for t in items if t.start <= mid < t.end)
        if n > best[0]:
            best = (n, marks[i], marks[i + 1])
    return f"{_fmt_dt(best[1])}–{_fmt_dt(best[2])}"


def _fmt_qty(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:.2f}".replace(".", ",").rstrip("0").rstrip(",")


def _status_mark(status: str) -> str:
    """Отметка расчётного статуса работы, а не всегда пустой квадрат.

    Заявленный человеком факт здесь не подставляется: BLOCKED обязан
    оставаться видимым независимо от отметки «сделано» (AC12, AC15).
    """
    return {
        "DONE": "☑",
        "IN_PROGRESS": "◐",
        "BLOCKED": "⛔",
        "SKIPPED": "—",
        "TODO": "☐",
    }.get(status or "", "☐")


def _claimed_note(status: str, claimed: str) -> str:
    """Заявленный факт выполнения отдельно от расчётного статуса."""
    if claimed and claimed != status:
        return f"факт: {claimed}"
    return ""


def _work_remark(comment: str, fact: str = "") -> str:
    """Комментарий человека и заявленный факт в одной ячейке примечания."""
    parts = [p for p in (comment, fact) if p]
    return " · ".join(parts) or "—"


def _blocks(
    data: KitchenData,
    day: date,
    menu_rows: tuple[MenuRow, ...],
    need_groups: tuple[NeedGroup, ...],
    task_lines: tuple[TaskLine, ...],
    by_role: tuple[tuple[str, tuple[TaskLine, ...]], ...],
    controls: tuple[ControlLine, ...],
    role_load: tuple[RoleLoad, ...],
) -> tuple[SheetBlock, ...]:
    blocks: list[SheetBlock] = []

    blocks.append(
        SheetBlock(
            kind="menu",
            title="Меню смены",
            headers=("Приём", "Подгруппа", "Блюдо", "Порций", "Выдача", "Цех"),
            lines=tuple(
                (r.meal, r.subcategory, r.dish, r.portions, _hhmm(r.serve_at), r.shop)
                for r in menu_rows
            ),
        )
    )

    for group in need_groups:
        lines = tuple(
            (
                n.product,
                _fmt_qty(n.qty_display),
                n.unit,
                n.action,
                n.breakdown or "—",
                f"{_fmt_qty(n.stock)} / {_fmt_qty(n.min_stock)}",
                n.status,
            )
            for n in group.items
        )
        title = f"Продукты · {group.category} — {group.owner}"
        if group.is_check_stock:
            title += " · только проверить остаток"
        blocks.append(
            SheetBlock(
                kind=f"need:{group.category}",
                title=title,
                headers=(
                    "Продукт",
                    "Нужно",
                    "Ед.",
                    "Действие",
                    "Из каких блюд",
                    "Остаток / мин.",
                    "Статус",
                ),
                lines=lines,
            )
        )

    for role, items in by_role:
        lines = tuple(
            (
                _span(t.start, t.end),
                t.stage,
                t.operation,
                t.source or "—",
                t.products or "—",
                t.assignee or "—",
                _status_mark(t.status),
                _work_remark(t.comment, _claimed_note(t.status, t.claimed_status)),
            )
            for t in items
        )
        blocks.append(
            SheetBlock(
                kind=f"role:{role}",
                title=f"Задачи · {role}",
                headers=(
                    "Время",
                    "Этап",
                    "Операция",
                    "Блюдо",
                    "Продукты",
                    "Исполнитель",
                    "Готово",
                    "Примечание",
                ),
                lines=lines,
            )
        )

    if controls:
        blocks.append(
            SheetBlock(
                kind="control",
                title="Точки контроля шеф-повара",
                headers=(
                    "Время",
                    "Операция",
                    "Блюдо",
                    "Исполнитель",
                    "Готово",
                    "Примечание",
                ),
                lines=tuple(
                    (
                        _span(c.start, c.end),
                        c.operation,
                        c.source,
                        c.assignee or "—",
                        _status_mark(c.status),
                        _work_remark(
                            c.comment, _claimed_note(c.status, c.claimed_status)
                        ),
                    )
                    for c in controls
                ),
            )
        )

    loads = role_load
    if loads:
        blocks.append(
            SheetBlock(
                kind="load",
                title="Нагрузка по ролям",
                headers=("Роль", "Задач", "Одновременно", "Нужно человек", "Минут"),
                lines=tuple(
                    (l.role, l.tasks, l.simultaneous, l.people, l.total_min)
                    for l in loads
                ),
            )
        )

    return tuple(blocks)


__all__ = [
    "ControlLine",
    "MenuRow",
    "SheetBlock",
    "ShiftSheet",
    "ShopLine",
    "TaskLine",
    "build_shift_sheet",
]
