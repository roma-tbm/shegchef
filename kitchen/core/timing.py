"""Шаг 4: обратный тайминг от времени выдачи.

Схема расчёта на один приём пищи (например, обед с выдачей в 12:00):

    цепочка блюда:  12:00 − Σ(длительности) ......... старт
    заготовки:     общий блок перед самым ранним стартом
    контроль:      от дедлайна назад (lead_min)
    закрытие:      после выдачи (lead_min со знаком «+»)

    12:00 ── выдача
     │  ▲ контроль шефа:  дедлайн − lead
     ▼
    10:15 старт горячих цехов
     │
    08:25 ─────── блок заготовок разнорабочего ─────── 10:15
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from kitchen.core.tasks import Task, TaskBoard
from kitchen.models import ROLES, MenuLine

SHIFT_START: time = time(6, 0)
"""Ограничение: раньше смены задачи не ставим."""


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """Задача с рассчитанным временем."""

    task: Task
    start: datetime
    end: datetime

    @property
    def role(self) -> str:
        return self.task.role

    @property
    def operation(self) -> str:
        return self.task.operation

    @property
    def meal(self) -> str:
        return self.task.meal

    @property
    def stage(self) -> str:
        return self.task.stage

    @property
    def duration_min(self) -> int:
        return self.task.duration_min

    @property
    def start_t(self) -> time:
        return self.start.time()

    @property
    def end_t(self) -> time:
        return self.end.time()

    @property
    def source(self) -> str:
        return self.task.source

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def shop(self) -> str:
        return self.task.shop

    def overlaps(self, other: "ScheduledTask") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class RoleLoad:
    """Нагрузка на роль: сколько человек нужно одновременно."""

    role: str
    simultaneous: int
    tasks: int
    total_min: int

    @property
    def people(self) -> int:
        return max(1, self.simultaneous)


@dataclass(frozen=True, slots=True)
class Timing:
    """Полный тайминг смены."""

    day: date
    scheduled: tuple[ScheduledTask, ...]
    deadline_by_meal: dict[str, datetime]
    prep_block: dict[str, tuple[datetime, datetime]]
    shift_start: datetime
    shift_end: datetime
    overruns: tuple[str, ...] = ()
    """Блюда, которые не помещаются до времени выдачи."""

    def by_role(self) -> tuple[tuple[str, tuple[ScheduledTask, ...]], ...]:
        out: dict[str, list[ScheduledTask]] = {}
        for st in self.scheduled:
            out.setdefault(st.task.role, []).append(st)
        return tuple(
            (role, tuple(sorted(items, key=lambda s: s.start)))
            for role, items in sorted(
                out.items(), key=lambda kv: ROLES.index(kv[0]) if kv[0] in ROLES else 99
            )
        )

    def by_meal(self) -> tuple[tuple[str, tuple[ScheduledTask, ...]], ...]:
        out: dict[str, list[ScheduledTask]] = {}
        for st in self.scheduled:
            out.setdefault(st.meal, []).append(st)
        order = list(self.deadline_by_meal)
        return tuple(
            (m, tuple(sorted(out[m], key=lambda s: s.start))) for m in order if m in out
        )

    def control_points(self) -> tuple[ScheduledTask, ...]:
        return tuple(s for s in self.scheduled if s.stage == "Контроль")

    def role_load(self) -> tuple[RoleLoad, ...]:
        out: list[RoleLoad] = []
        for role, items in self.by_role():
            edges: list[datetime] = []
            for s in items:
                edges.append(s.start)
                edges.append(s.end)
            marks = sorted(set(edges))
            simultaneous = 0
            for i in range(len(marks) - 1):
                mid = marks[i] + (marks[i + 1] - marks[i]) / 2
                simultaneous = max(
                    simultaneous,
                    sum(1 for s in items if s.start <= mid < s.end),
                )
            total = sum(s.duration_min for s in items)
            out.append(
                RoleLoad(
                    role=role,
                    simultaneous=simultaneous,
                    tasks=len(items),
                    total_min=total,
                )
            )
        return tuple(out)


def _chain_duration(chain: tuple[Task, ...]) -> int:
    return sum(t.duration_min for t in chain)


def calculate_backward_timing(
    day: date,
    menu: tuple[MenuLine, ...],
    board: TaskBoard,
) -> Timing:
    """Раскладывает задачи по часам от времени выдачи каждого блюда."""
    line_by_recipe = {line.recipe: line for line in menu}

    # 1. Дедлайны: самое раннее время выдачи в приёме пищи.
    deadline_by_meal: dict[str, datetime] = {}
    for line in menu:
        cur = deadline_by_meal.get(line.meal)
        cand = datetime.combine(day, line.serve_at)
        if cur is None or cand < cur:
            deadline_by_meal[line.meal] = cand

    # 2. Длины цепочек и обратный отсчёт до старта каждого блюда.
    chain_len: dict[str, int] = {r: _chain_duration(c) for r, c in board.chains.items()}
    shift_start = datetime.combine(day, SHIFT_START)
    last_end = shift_start

    dish_start: dict[str, datetime] = {}
    for recipe, length in chain_len.items():
        deadline = deadline_by_meal[line_by_recipe[recipe].meal]
        start = deadline - timedelta(minutes=length)
        if start < shift_start:
            start = shift_start
        dish_start[recipe] = start

    # 3. Заготовки: общий блок перед самым ранним стартом горячих цехов.
    prep_tasks = {
        meal: tuple(t for t in tasks if t.stage == "Заготовка")
        for meal, tasks in board.prep.items()
    }

    prep_block: dict[str, tuple[datetime, datetime]] = {}
    prep_window: dict[str, tuple[datetime, datetime]] = {}
    prep_schedule: list[tuple[Task, datetime, datetime]] = []
    for meal, tasks in prep_tasks.items():
        if not tasks:
            continue
        # Заготовка нужна тем блюдам, для которых она заведена.
        consumers: set[str] = set()
        for t in tasks:
            consumers.update(t.recipes)
        if consumers:
            end = min(dish_start[r] for r in consumers if r in dish_start)
        else:
            end = min(
                (dish_start[r] for r in chain_len if line_by_recipe[r].meal == meal),
                default=deadline_by_meal[meal],
            )
        total = sum(t.duration_min for t in tasks)
        start = end - timedelta(minutes=total)
        if start < shift_start:
            start = shift_start
        cursor = start
        for t in tasks:
            t_end = cursor + timedelta(minutes=t.duration_min)
            prep_schedule.append((t, cursor, t_end))
            prep_window[t.task_id] = (cursor, t_end)
            cursor = t_end
        prep_block[meal] = (start, cursor)

    # Блюдо сдвигается только по тем заготовкам, которые оно использует.
    # Каша не зависит от подготовки ветчины, поэтому не ждёт её.
    prep_ready: dict[str, datetime] = {}
    for meal, tasks in prep_tasks.items():
        for t in tasks:
            _, t_end = prep_window[t.task_id]
            for r in t.recipes:
                if t_end > prep_ready.get(r, shift_start):
                    prep_ready[r] = t_end

    # 4. Цепочки блюд.
    chain_schedule: list[tuple[Task, datetime, datetime]] = []
    overruns: list[str] = []
    for recipe, chain in board.chains.items():
        deadline = deadline_by_meal[line_by_recipe[recipe].meal]
        cursor = max(dish_start[recipe], prep_ready.get(recipe, dish_start[recipe]))
        if chain and cursor + timedelta(minutes=_chain_duration(chain)) > deadline:
            overruns.append(recipe)
        for t in chain:
            t_end = cursor + timedelta(minutes=t.duration_min)
            chain_schedule.append((t, cursor, t_end))
            cursor = t_end
        if cursor > last_end:
            last_end = cursor

    # 5. Якорные задачи: контроль, финальная разстановка, закрытие смены.
    anchor_schedule: list[tuple[Task, datetime, datetime]] = []
    for t in board.anchors:
        if t.anchor == "deadline":
            # Обратный отсчёт от выдачи: контроль должен закончиться
            # за lead_min минут до неё.
            anchor = deadline_by_meal[t.meal]
            end = anchor - timedelta(minutes=t.lead_min)
            start = end - timedelta(minutes=t.duration_min)
        elif t.anchor == "prep_end":
            # Сразу после блока заготовок.
            block = prep_block.get(t.meal)
            start = block[1] if block else deadline_by_meal[t.meal]
            start += timedelta(minutes=t.lead_min)
            end = start + timedelta(minutes=t.duration_min)
        else:  # last_deadline — закрытие смены после выдачи.
            # lead_min здесь — задержка СТАРТА после последней выдачи,
            # чтобы мытьё не начиналось раньше, чем накормили.
            anchor = max(deadline_by_meal.values())
            start = anchor + timedelta(minutes=t.lead_min)
            end = start + timedelta(minutes=t.duration_min)
        anchor_schedule.append((t, start, end))
        if end > last_end:
            last_end = end

    scheduled = [
        ScheduledTask(task=t, start=s, end=e)
        for t, s, e in (*prep_schedule, *chain_schedule, *anchor_schedule)
    ]
    scheduled.sort(key=lambda st: (st.start, ROLES.index(st.role) if st.role in ROLES else 99))

    # Граница смены — первый и последний реальные задачи, а не SHIFT_START:
    # 06:00 это только запрет ставить задачи раньше полуночи.
    if scheduled:
        shift_start = scheduled[0].start
    else:
        shift_start = max(deadline_by_meal.values()) if deadline_by_meal else shift_start

    return Timing(
        day=day,
        scheduled=tuple(scheduled),
        deadline_by_meal=deadline_by_meal,
        prep_block=prep_block,
        shift_start=shift_start,
        shift_end=last_end,
        overruns=tuple(overruns),
    )


__all__ = [
    "RoleLoad",
    "ScheduledTask",
    "SHIFT_START",
    "Timing",
    "calculate_backward_timing",
]
