"""Шаг 5: итоговый лист смены.

Собирает всё вместе в плоский список блоков, пригодный для печати
или отправки в чат кухни.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from kitchen.core.ingredients import NeedGroup, ProductNeed, calculate_ingredients
from kitchen.core.tasks import generate_tasks
from kitchen.core.timing import RoleLoad, Timing, calculate_backward_timing
from kitchen.models import ROLES, KitchenData, MenuLine, Product, Recipe, TaskTemplate

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
    start: datetime
    end: datetime
    role: str
    operation: str
    source: str


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


def build_shift_sheet(data: KitchenData) -> ShiftSheet:
    """Полный вертикальный сценарий: меню → продукты → задачи → тайминг."""
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
    task_lines = tuple(
        TaskLine(
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
        for st in timing.scheduled
    )
    by_role = tuple(
        (role, tuple(t for t in task_lines if t.role == role))
        for role in ROLE_ORDER
        if any(t.role == role for t in task_lines)
    )
    controls = tuple(
        ControlLine(
            start=st.start,
            end=st.end,
            role=st.role,
            operation=st.operation,
            source=st.source,
        )
        for st in timing.control_points()
    )

    warnings = _warnings(timing, needs)
    notes = (
        f"Смена {timing.shift_start:%H:%M}–{timing.shift_end:%H:%M} · "
        f"порций: {sum(r.portions for r in menu_rows)}",
    )
    notes = notes + tuple(a.text for a in data.assumptions)

    blocks = _blocks(data, day, menu_rows, need_groups, task_lines, by_role, controls, timing)

    return ShiftSheet(
        kitchen=data.kitchen,
        chief=data.chief,
        day=day,
        shift=data.shift,
        portions=sum(r.portions for r in menu_rows),
        dishes=len({r.dish for r in menu_rows}),
        shift_start=timing.shift_start,
        shift_end=timing.shift_end,
        menu=menu_rows,
        needs=need_groups,
        needs_flat=needs,
        tasks=task_lines,
        tasks_by_role=by_role,
        controls=controls,
        role_load=timing.role_load(),
        blocks=blocks,
        notes=notes,
        warnings=warnings,
    )


def _warnings(timing: Timing, needs: tuple[ProductNeed, ...]) -> tuple[str, ...]:
    out: list[str] = []

    for recipe in timing.overruns:
        out.append(
            f"«{recipe}» не помещается до времени выдачи — "
            f"увеличьте смену или сдвиньте выдачу"
        )

    for load in timing.role_load():
        if load.simultaneous > 1:
            peak = _peak_window(timing, load.role)
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


def _peak_window(timing: Timing, role: str) -> str:
    """Час, в который у роли пиковая нагрузка."""
    items = [st for st in timing.scheduled if st.role == role]
    if not items:
        return "—"
    marks = sorted({t for st in items for t in (st.start, st.end)})
    best = (0, marks[0], marks[0])
    for i in range(len(marks) - 1):
        mid = marks[i] + (marks[i + 1] - marks[i]) / 2
        n = sum(1 for st in items if st.start <= mid < st.end)
        if n > best[0]:
            best = (n, marks[i], marks[i + 1])
    return f"{_fmt_dt(best[1])}–{_fmt_dt(best[2])}"


def _fmt_qty(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:.2f}".replace(".", ",").rstrip("0").rstrip(",")


def _blocks(
    data: KitchenData,
    day: date,
    menu_rows: tuple[MenuRow, ...],
    need_groups: tuple[NeedGroup, ...],
    task_lines: tuple[TaskLine, ...],
    by_role: tuple[tuple[str, tuple[TaskLine, ...]], ...],
    controls: tuple[ControlLine, ...],
    timing: Timing,
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
                t.source,
                t.products or "—",
                "☐",
            )
            for t in items
        )
        blocks.append(
            SheetBlock(
                kind=f"role:{role}",
                title=f"Задачи · {role}",
                headers=("Время", "Этап", "Операция", "Блюдо", "Продукты", "Готово"),
                lines=lines,
            )
        )

    if controls:
        blocks.append(
            SheetBlock(
                kind="control",
                title="Точки контроля шеф-повара",
                headers=("Время", "Операция", "Блюдо", "Готово"),
                lines=tuple(
                    (_span(c.start, c.end), c.operation, c.source, "☐")
                    for c in controls
                ),
            )
        )

    loads = timing.role_load()
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
