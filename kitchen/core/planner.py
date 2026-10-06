"""Шаг 5: планировщик смены — маршруты людей и видимые незаполненные поля.

Связывает уже существующие шаги расчёта и ничего не дублирует:

    menu_week/меню → kitchen.core.ingredients (потребности)
                   → kitchen.core.tasks       (операции из ТТК)
                   → kitchen.core.timing      (обратный тайминг от выдачи)
                   → kitchen.core.constraints (жёсткие ограничения)
                   → kitchen.duties           (обязанности людей)
                   → kitchen.staff            (графики, перерывы)

Два принципа, определяющие поведение модуля:

  1. Ограничения действуют внутри расчёта. Назначение, нарушающее смену,
     перерыв, п. 17, п. 23 или п. 27, не выдаётся как готовый план: работа
     получает статус BLOCKED и причину, а маршрут остаётся пустым.
  2. Ничего не выдумывается. Длительность, окно или исполнитель, не заданные
     источником, остаются пустыми, работа переходит в UNPLANNED с указанием
     причины — и блокирует только то, что от неё зависит (AC15).

Перерывы (п. 8, п. 21) не назначаются как задачи и не занимаются работой.
Планирование шефа 13:30–14:00 (п. 21) накладывается на перерыв, поэтому оно
показывается в общем плане без исполнителя и не распределяется на человека.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from kitchen.calendar_rules import mode_for
from kitchen.core.constraints import Interval, Violation, check_all, from_minutes
from kitchen.core.tasks import Task, TaskBoard
from kitchen.core.timing import ScheduledTask, Timing
from kitchen.duties import (
    DUTIES_BY_KEY,
    DutyRule,
    DutySettings,
    duties_for,
)
from kitchen.models import ROLES, MenuLine
from kitchen.staff import (
    ROLE_CHEF,
    ROLE_CLEANER,
    ROLE_COOK,
    ROLE_HELPER,
    Employee,
    StaffSettings,
    to_minutes,
    weekend_staff,
)

STATUS_TODO = "TODO"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_DONE = "DONE"
STATUS_BLOCKED = "BLOCKED"
STATUS_SKIPPED = "SKIPPED"

STATUSES: tuple[str, ...] = (
    STATUS_TODO,
    STATUS_IN_PROGRESS,
    STATUS_DONE,
    STATUS_BLOCKED,
    STATUS_SKIPPED,
)

#: Статусы, при которых работа считается закрытой и не участвует в пересчёте.
CLOSED_STATUSES: frozenset[str] = frozenset({STATUS_DONE, STATUS_SKIPPED})

#: Причины отсутствия исполнителя. Пустые строки в перечислении означают
#: «источник не задаёт» — это показывается пользователю, а не заменяется
#: догадкой.
REASON_NO_DURATION = "длительность в источнике не задана"
REASON_NO_WINDOW = "окно работы в источнике не задано"
REASON_NO_ASSIGNEE = "исполнитель в источнике не назначен"
REASON_TASTING_EXECUTOR = "не указан исполнитель бракеража"
REASON_STAFF_MISSING = "состав бригады не задан"
REASON_BLOCKED = "нарушено ограничение"
REASON_NO_EMPLOYEE = "нет сотрудника этой роли"
REASON_CONFLICT = "не нашлось свободного сотрудника"
REASON_TOO_MANY_SIMULTANEOUS = "не хватает сотрудников одновременно"
REASON_NO_TEMPLATE = "нет операций в ТТК"
REASON_NO_NEXT_MENU = "меню следующего дня не задано"

#: Старые ID работ выдачи до PA-01: одна работа представляла всю бригаду.
#: После разделения выдачи на роли их записи больше не совпадают с новыми
#: работами, и исполнитель из хранилища достоверно неизвестен.
LEGACY_BRIGADE_IDS: dict[str, str] = {
    "П-breakfast-serving": "breakfast-serving",
    "П-lunch-serving": "lunch-serving",
}
REASON_LEGACY_EXECUTOR = (
    "исполнитель прежней единой работы в сохранённых данных не установлен"
)


def _work_key(
    *,
    kind: str,
    role: str,
    stage: str,
    operation: str,
    source: str,
    meal: str,
    order: int,
) -> str:
    """Устойчивая идентичность работы вместо её позиции в списке (AC13).

    Позиционный номер меняется при замене блюда и при перестановке меню, а
    значит не может быть ключом сохранённой отметки «сделано». Ключ строится
    из содержания работы: вид, роль, этап, операция, блюдо и приём пищи.

    Политика пересчёта объявлена явно:

      * смена блюда, операции, роли, этапа или приёма пищи — другая работа,
        отметка не переносится;
      * изменение порций и времени выдачи — та же самая работа, отметка
        сохраняется: операция не изменилась, изменился только объём.
    """
    parts = (kind, role, stage, operation, source, meal, str(order))
    digest = hashlib.blake2s("\x1f".join(parts).encode("utf-8"), digest_size=5)
    return digest.hexdigest()


def _fmt_span(start: datetime, end: datetime) -> str:
    """Человекочитаемый интервал: 08:00–12:00 или диапазон суток."""
    if start.date() != end.date():
        return f"{start:%d.%m %H:%M}–{end:%d.%m %H:%M}"
    return f"{start:%H:%M}–{end:%H:%M}"


@dataclass(frozen=True, slots=True)
class Gap:
    """Незаполненное поле плана: что именно и почему (AC15)."""

    code: str
    subject: str
    reason: str
    blocks: str = ""
    """Что именно не считается из-за этого пропуска."""

    @property
    def message(self) -> str:
        return f"{self.subject}: {self.reason}"


@dataclass(frozen=True, slots=True)
class LegacyRecord:
    """Сохранённая запись старой работы выдачи, чью модель заменили (PA-03).

    До PA-01 `П-breakfast-serving`/`П-lunch-serving` обслуживали всю бригаду.
    После разделения на роли нельзя достоверно узнать, кто именно выполнил
    работу: в `statuses.json` и `notes.json` исполнитель не сохраняется.
    Поэтому запись остаётся видимой как отдельный исторический факт, но не
    применяется ни к одному новому участнику и не влияет на готовность плана.
    """

    item_id: str
    duty_key: str
    operation: str
    status: str = ""
    comment: str = ""
    deviation: str = ""
    reason: str = REASON_LEGACY_EXECUTOR

    @property
    def message(self) -> str:
        parts: list[str] = []
        if self.status:
            parts.append(f"статус: {self.status}")
        if self.comment:
            parts.append(f"комментарий: {self.comment}")
        if self.deviation:
            parts.append(f"отклонение: {self.deviation}")
        detail = "; ".join(parts) or "пустая запись"
        return (
            f"{self.item_id} · {self.operation} — историческая запись прежней "
            f"работы выдачи ({self.reason}); {detail}"
        )


@dataclass(frozen=True, slots=True)
class PlannedItem:
    """Одна строка маршрутного листа.

    Хозяйственные работы имеют пустые `meal`, `recipe`, `task_id`, `products`
    — это допустимо и указано в требованиях, а не ошибка данных.
    """

    item_id: str
    status: str
    start: datetime
    end: datetime
    operation: str
    role: str
    stage: str = ""
    """Этап работы: Заготовка/Приготовление/Контроль/Закрытие/Обслуживание.

    Нужен книге и печати, чтобы контрольные точки и закрывающие работы были
    распознаны в авторитетном плане, а не только в старом тайминге.
    """
    assignee: str = ""
    meal: str = ""
    recipe: str = ""
    task_id: str = ""
    products: tuple[str, ...] = ()
    portions: int = 0
    duration_min: int = 0
    source: str = ""
    """Пункт stage3.md или блюдо — происхождение задачи (AC04)."""

    note: str = ""
    reason: str = ""
    """Причина BLOCKED/UNPLANNED: пустая строка означает «причин нет»."""

    violations: tuple[Violation, ...] = ()
    zone: str = ""
    action: str = ""

    comment: str = ""
    """Комментарий пользователя к работе (п. 42)."""

    deviation: str = ""
    """Тип отклонения: задержка, замена продукта, порции, оборудование."""

    claimed_status: str = ""
    """Отметка пользователя о фактическом выполнении заблокированной работы.

    Хранится отдельно от `status`: пользователь может отметить как выполненную
    работу, нарушающую ограничение, но расчёт обязан продолжать считать план
    неготовым и показывать причину (AC08, AC15).
    """

    @property
    def is_assigned(self) -> bool:
        return bool(self.assignee)

    @property
    def blocked(self) -> bool:
        """Есть ли нарушение жёсткого ограничения у этой работы.

        Это свойство расчёта, а не пользовательский статус: отметка «сделано»
        не может снять нарушение ограничения (AC08, AC15).
        """
        return bool(self.violations)

    @property
    def is_anchor(self) -> bool:
        """Контрольная точка выдачи или бракеража, а не операция ТТК."""
        return not self.task_id

    @property
    def span(self) -> str:
        """Интервал работы. У неназначенного пункта времени нет — печатаем прочерк."""
        if self.start >= self.end:
            return "—"
        return _fmt_span(self.start, self.end)

    @property
    def detail(self) -> str:
        return ", ".join(self.products)


@dataclass(slots=True)
class Route:
    """Маршрут одного человека за день."""

    employee: Employee
    items: tuple[PlannedItem, ...] = ()

    @property
    def name(self) -> str:
        return self.employee.name

    @property
    def role(self) -> str:
        return self.employee.role

    @property
    def worked_min(self) -> int:
        return sum(i.duration_min for i in self.items if i.status != STATUS_SKIPPED)

    @property
    def load_pct(self) -> float:
        span = self.employee.shift_min
        return (self.worked_min / span * 100.0) if span else 0.0


@dataclass(slots=True)
class ShiftPlan:
    """Готовый расчёт смены — либо честная причина, почему он не готов."""

    day: date
    mode: str
    staff: tuple[Employee, ...]
    routes: tuple[Route, ...]
    items: tuple[PlannedItem, ...]
    gaps: tuple[Gap, ...] = ()
    timing: Timing | None = None
    board: TaskBoard | None = None
    overrides: tuple[tuple[str, str], ...] = ()
    """Что сохранил пользователь: (item_id, статус) (п. 42, AC13)."""

    next_menu: tuple[MenuLine, ...] = ()
    """Меню ближайшего следующего дня — источник подготовки (AC10, AC11)."""

    legacy: tuple[LegacyRecord, ...] = ()
    """Сохранённые записи до разделения выдачи (PA-03).

    Показываются человеку как история, но не применяются к новой бригаде:
    старый DONE не раздаётся всем участникам и не влияет на готовность.
    """

    @property
    def deviations(self) -> tuple[tuple[str, str], ...]:
        """Работы с зафиксированным отклонением: (item_id, тип)."""
        return tuple(
            (i.item_id, i.deviation) for i in self.items if i.deviation
        )

    @property
    def violations_count(self) -> int:
        """Сколько работ нарушают жёсткие ограничения (AC08, AC15)."""
        return sum(len(i.violations) for i in self.items)

    @property
    def ready(self) -> bool:
        """Готов ли план к печати.

        Ложь, если есть незаполненные поля или нарушения ограничений:
        показывать такой план как готовый — ложная готовность (AC15).
        Нарушение остаётся нарушением независимо от пользовательского
        статуса: DONE не превращает запрещённое назначение в допустимое.
        """
        return not self.gaps and self.violations_count == 0

    @property
    def not_ready_reasons(self) -> tuple[str, ...]:
        out = [g.message for g in self.gaps]
        out.extend(
            f"{i.span} {i.operation}: {v}"
            for i in self.items
            for v in i.violations
        )
        return tuple(dict.fromkeys(out))

    def items_of(self, role: str) -> tuple[PlannedItem, ...]:
        return tuple(i for i in self.items if i.role == role)

    def unassigned(self) -> tuple[PlannedItem, ...]:
        """Работы без исполнителя — их нельзя печатать как выполненные."""
        return tuple(i for i in self.items if not i.assignee)

    def status_counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for item in self.items:
            out[item.status] = out.get(item.status, 0) + 1
        return out

    def route_of(self, name: str) -> Route | None:
        for route in self.routes:
            if route.employee.name == name:
                return route
        return None


# ---------------------------------------------------------------------------
# Назначение
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Load:
    """Занятость сотрудника за день: интервалы заняты, перерывы запрещены."""

    employee: Employee
    busy: list[Interval] = field(default_factory=list)
    breaks: tuple[Interval, ...] = ()

    @classmethod
    def of(cls, employee: Employee) -> "_Load":
        br = tuple(
            Interval(to_minutes(s), to_minutes(e))
            for s, e in employee.breaks
        )
        return cls(employee=employee, breaks=br)

    def free(self, span: Interval) -> bool:
        if any(span.overlaps(b) for b in self.breaks):
            return False
        return not any(span.overlaps(b) for b in self.busy)

    def take(self, span: Interval) -> None:
        self.busy.append(span)
        self.busy.sort(key=lambda i: i.start)


class _Assigner:
    """Назначение работ сотрудникам с учётом роли, смены и перерывов.

    Работы идут в порядке времени: раньше начавшаяся получает сотрудника
    первым, поэтому при конфликте выигрывает та работа, которая и так ближе
    к дедлайну.
    """

    def __init__(self, staff: tuple[Employee, ...]) -> None:
        self.staff = staff
        self._loads = [_Load.of(e) for e in staff]

    def loads_for(self, roles: tuple[str, ...]) -> list[_Load]:
        """Занятость только сотрудников указанных ролей."""
        return [load for load in self._loads if load.employee.role in roles]

    def loads_named(self, names: tuple[str, ...]) -> list[_Load]:
        """Занятость конкретных людей поимённо.

        Отдельно от ролей, потому что исполнитель бракеража (п. 15) — это
        человек, а не должность: роль «Шеф-повар» не отвечает на вопрос
        «кто именно сегодня дегустирует».
        """
        return [load for load in self._loads if load.employee.name in names]

    def candidates(self, roles: tuple[str, ...]) -> list[_Load]:
        return [
            load
            for load in self._loads
            if load.employee.role in roles
            and load.employee.shift_min > 0
        ]

    def place(
        self, roles: tuple[str, ...], span: Interval, names: tuple[str, ...] = ()
    ) -> Employee | None:
        """Назначает работу: сначала тем, кого назвали поимённо, иначе по роли."""
        if names:
            for load in self.loads_named(names):
                if load.employee.shift_min > 0 and load.employee.fits(
                    from_minutes(span.start), from_minutes(span.end)
                ) and load.free(span):
                    load.take(span)
                    return load.employee
            return None
        for load in self.candidates(roles):
            if load.employee.fits(from_minutes(span.start), from_minutes(span.end)) and load.free(span):
                load.take(span)
                return load.employee
        return None

    def capacity(self, roles: tuple[str, ...]) -> int:
        return len(self.candidates(roles))

    def overlapping_count(self, span: Interval) -> int:
        return sum(
            1
            for load in self.candidates(roles)
            if any(span.overlaps(b) for b in load.busy)
        )

    def simultaneous(self, roles: tuple[str, ...], span: Interval) -> int:
        """Сколько сотрудников роли уже заняты в этом интервале."""
        return sum(
            1
            for load in self.candidates(roles)
            if any(span.overlaps(b) for b in load.busy)
        )


def _portions_for(task: Task, menu: tuple[MenuLine, ...]) -> int:
    """Порции задачи: сумма порций блюд, которые она обслуживает."""
    total = 0
    for line in menu:
        if line.recipe in task.recipes and line.meal == task.meal:
            total += line.portions
    return total


def _scheduled_item(
    index: int,
    st: ScheduledTask,
    *,
    role: str,
    meal: str = "",
    source: str = "",
    zone: str = "",
    action: str = "",
    assignee: str = "",
    status: str = STATUS_TODO,
    reason: str = "",
) -> PlannedItem:
    return PlannedItem(
        item_id=f"З-{index:03d}",
        status=status,
        start=st.start,
        end=st.end,
        operation=st.operation,
        role=role,
        stage=st.stage,
        assignee=assignee,
        meal=meal or st.meal,
        recipe=st.task.source,
        task_id=st.task_id,
        products=st.task.products,
        duration_min=st.duration_min,
        source=source or st.source,
        reason=reason,
        zone=zone,
        action=action,
    )


def _duty_window_span(
    duty: DutyRule, window: tuple[time, time] | None
) -> Interval:
    """Интервал окна работы в минутах от полуночи."""
    start, end = window if window else (time(0, 0), time(0, 0))
    return Interval(to_minutes(start), to_minutes(end))


def _earliest_fit(
    window: Interval, duration: int, loads: list[_Load], employee: Employee | None
) -> datetime | None:
    """Самое раннее начало внутри окна, при котором работа помещается.

    Окно из источника — это период, в котором работа разрешена, а не занятый
    интервал: конкретное время определяется длительностью и загрузкой.
    """
    if employee is None:
        return None
    blocked = [br for load in loads for br in load.busy]
    blocked += [
        Interval(to_minutes(bs), to_minutes(be)) for bs, be in employee.breaks
    ]
    start = window.start
    # Перерыв и занятый интервал не отменяют работу, а сдвигают её: ищем
    # первое место, где работа целиком помещается до конца окна.
    while start + duration <= window.end:
        clash = next((b for b in blocked if b.overlaps(Interval(start, start + duration))), None)
        if clash is None:
            break
        start = clash.end
    else:
        return None
    if start + duration > window.end:
        return None
    if not employee.fits(from_minutes(start), from_minutes(start + duration)):
        return None
    return start


def _place_duty(
    duty: DutyRule,
    *,
    day: date,
    mode: str,
    staff: tuple[Employee, ...],
    assigner: _Assigner,
    settings: DutySettings,
    roles: tuple[str, ...],
    names: tuple[str, ...] = (),
    role: str | None = None,
    item_id: str | None = None,
) -> PlannedItem | None:
    """Ставит обязанность в расписание; None — если ставить нечего.

    Отсутствие длительности или окна не превращается в выдуманное значение:
    работа возвращается как незапланированная с указанием причины.

    `names` — люди, которых пользователь назвал поимённо (исполнитель
    бракеража). Они приоритетнее ролей: настройка «кто дегустирует» должна
    работать буквально, а не подбирать «кого-то из шефов».

    `role` и `item_id` нужны для бригадной работы (п. 6, 16): одна обязанность
    даёт отдельную строку на каждую требуемую роль, и у каждой строки свой
    устойчивый идентификатор.
    """
    role = role or duty.role
    item_id = item_id or _duty_item_id(duty)
    duration = settings.duration_of(duty)
    window = settings.window_of(duty)

    if duration is None:
        return PlannedItem(
            item_id=item_id,
            status=STATUS_TODO,
            start=datetime.combine(day, duty.fixed_at or time(0, 0)),
            end=datetime.combine(day, duty.fixed_at or time(0, 0)),
            operation=duty.operation,
            role=role,
            stage=duty.stage,
            meal="",
            recipe="",
            duration_min=0,
            source=duty.source,
            note=duty.note,
            reason=REASON_NO_DURATION,
            zone=duty.zone,
            action=duty.action,
        )

    if duty.fixed_at is not None:
        start_min = to_minutes(duty.fixed_at)
        span = Interval(start_min, start_min + duration)
        start = datetime.combine(day, duty.fixed_at)
        employee = assigner.place(roles, span, names) if duty.assign else None
        end = start + timedelta(minutes=duration)
        item = _finish_duty(
            duty, day, mode, roles, start, end, span, employee, assigner,
            settings, role, item_id,
        )
        if employee is None and duty.assign:
            # Причина неудачи важнее пустой строки маршрута (AC06).
            candidates = [
                ld.employee
                for ld in (assigner.loads_named(names) if names else assigner.loads_for(roles))
            ]
            return replace(
                item,
                reason=_unplaced_reason(roles, span, candidates, day),
            )
        if employee is None:
            return replace(item, reason=REASON_NO_ASSIGNEE)
        return item

    if window is None:
        return PlannedItem(
            item_id=item_id,
            status=STATUS_TODO,
            start=datetime.combine(day, time(0, 0)),
            end=datetime.combine(day, time(0, 0)),
            operation=duty.operation,
            role=role,
            stage=duty.stage,
            meal="",
            recipe="",
            duration_min=duration,
            source=duty.source,
            note=duty.note,
            reason=REASON_NO_WINDOW,
            zone=duty.zone,
            action=duty.action,
        )

    win = _duty_window_span(duty, window)
    loads = assigner.loads_named(names) if names else assigner.loads_for(roles)
    best: Employee | None = None
    start_min: int | None = None
    for load in loads:
        cand = _earliest_fit(win, duration, [load], load.employee)
        if cand is not None and (start_min is None or cand < start_min):
            start_min, best = cand, load.employee
    if start_min is None:
        reason = _unplaced_reason(
            roles,
            win,
            [ld.employee for ld in loads],
            day,
        )
        return PlannedItem(
            item_id=item_id,
            status=STATUS_TODO,
            start=datetime.combine(day, window[0]),
            end=datetime.combine(day, window[1]),
            operation=duty.operation,
            role=role,
            stage=duty.stage,
            meal="",
            recipe="",
            duration_min=duration,
            source=duty.source,
            note=duty.note,
            reason=reason,
            zone=duty.zone,
            action=duty.action,
        )

    span = Interval(start_min, start_min + duration)
    load = next(ld for ld in loads if ld.employee is best)
    load.take(span)
    start = datetime.combine(day, from_minutes(start_min))
    end = datetime.combine(day, from_minutes(start_min + duration))
    return _finish_duty(
        duty, day, mode, roles, start, end, span, best, assigner, settings,
        role, item_id,
    )


def _unplaced_reason(
    roles: tuple[str, ...],
    span: Interval,
    candidates: list[Employee],
    day: date,
) -> str:
    """Почему работа не досталась ни одному сотруднику.

    Причина называется прямо: «занят» и «перерыв» требуют разных действий
    от шефа, а без причины пустая строка в маршруте выглядит как ошибка
    расчёта (AC06, AC15).
    """
    if not candidates:
        return REASON_NO_EMPLOYEE
    in_shift = [
        e for e in candidates
        if e.fits(from_minutes(span.start), from_minutes(span.end))
    ]
    if not in_shift:
        windows = ", ".join(
            f"{e.name} {e.shift_start:%H:%M}–{e.shift_end:%H:%M}" for e in candidates
        )
        return f"{REASON_CONFLICT}: работа не помещается в смену ({windows})"
    # Перерыв объясняет отказ, только если он единственная причина:
    # при иной длительности это место снова станет свободным.
    breaks = sorted(
        {
            (to_minutes(bs), to_minutes(be))
            for e in in_shift
            for bs, be in e.breaks
        }
    )
    overlapping = [
        (bs, be) for bs, be in breaks
        if Interval(bs, be).overlaps(span)
    ]
    if overlapping:
        windows = ", ".join(
            f"{from_minutes(bs):%H:%M}–{from_minutes(be):%H:%M}" for bs, be in overlapping
        )
        return f"{REASON_CONFLICT}: перерыв {windows} (п. 8, п. 21)"
    return f"{REASON_CONFLICT}: сотрудники роли заняты в это время"


def _duty_item_id(duty: DutyRule) -> str:
    """Устойчивый идентификатор обязанности из каталога.

    Один и тот же ключ даёт один и тот же ID независимо от того, нашлась ли
    длительность: ID — это идентичность работы, а не её состояние. Иначе
    введённая длительность молча переименовывала бы работу, и сохранённый
    комментарий или отметка выполнения переставали бы к ней относиться.
    """
    return f"П-{duty.key}"


def _brigade_item_id(duty: DutyRule, role: str) -> str:
    """Идентификатор строки бригадной работы для конкретной роли.

    Зависит только от ключа обязанности и роли, а не от того, кто именно её
    получил: смена состава не переименовывает работу, DONE и комментарии одного
    участника не переезжают к другому (AC13).
    """
    return f"П-{duty.key}:{role}"


def _finish_duty(
    duty: DutyRule,
    day: date,
    mode: str,
    roles: tuple[str, ...],
    start: datetime,
    end: datetime,
    span: Interval,
    employee: Employee | None,
    assigner: _Assigner,
    settings: DutySettings,
    role: str | None = None,
    item_id: str | None = None,
) -> PlannedItem:
    """Общая проверка обязанности после того, как время уже выбрано."""
    role = role or duty.role
    item_id = item_id or _duty_item_id(duty)
    duration = settings.duration_of(duty) or 0
    violations: tuple[Violation, ...] = ()
    if employee is not None:
        violations = check_all(
            duty, span, mode=mode, employee=employee, duration=duration
        )
    status = STATUS_BLOCKED if violations else STATUS_TODO
    reason = REASON_BLOCKED if violations else ""
    return PlannedItem(
        item_id=item_id,
        status=status,
        start=start,
        end=end,
        operation=duty.operation,
        role=role,
        stage=duty.stage,
        assignee=employee.name if employee else "",
        meal="",
        recipe="",
        duration_min=duration,
        source=duty.source,
        note=duty.note,
        reason=reason,
        violations=violations,
        zone=duty.zone,
        action=duty.action,
    )


# ---------------------------------------------------------------------------
# Основной расчёт
# ---------------------------------------------------------------------------


def plan_shift(
    *,
    day: date,
    menu: tuple[MenuLine, ...],
    timing: Timing,
    board: TaskBoard,
    staff: tuple[Employee, ...],
    duty_settings: DutySettings | None = None,
    tasting_executor: str = "",
    overrides: tuple[tuple[str, str], ...] = (),
    next_menu: tuple[MenuLine, ...] = (),
    next_board: TaskBoard | None = None,
    notes: tuple[tuple[str, str], ...] = (),
    deviations: tuple[tuple[str, str], ...] = (),
) -> ShiftPlan:
    """Собирает маршруты и видимые пропуски за один день.

    `timing` и `board` приходят из существующих шагов 3–4: планировщик не
    пересчитывает операции, он назначает их людям и проверяет ограничения.

    `notes` и `deviations` — то, что человек ввёл в интерфейсе: комментарий
    и тип отклонения по устойчивому `item_id`. Они привязаны к конкретной
    работе, поэтому переживают пересчёт и не достаются другой операции.
    """
    cfg = duty_settings or DutySettings()
    mode = mode_for(day)
    staff_note = ""
    if day.weekday() in (5, 6):
        staff, staff_note = weekend_staff(
            StaffSettings(saturday=staff, sunday=staff), day
        )
    assigner = _Assigner(staff)
    gaps: list[Gap] = []
    items: list[PlannedItem] = []

    _check_menu_coverage(gaps, menu, board)

    if staff_note:
        gaps.append(
            Gap(
                code=REASON_STAFF_MISSING,
                subject=staff_note,
                reason="состав бригады не задан в настройках",
                blocks="назначение сотрудников и печать маршрутов",
            )
        )

    _assign_production(items, timing, board, menu, assigner, mode, staff)
    if _needs_next_day_prep(day, cfg, next_menu, mode):
        _add_next_day_prep(items, gaps, day, mode, staff, assigner, cfg, next_menu, next_board)
    _add_duties(items, gaps, day, mode, staff, assigner, cfg, tasting_executor)
    _check_coverage(gaps, items, staff, mode, staff_note)
    if not staff_note:
        # Когда состав не задан, одна общая причина уже всё объясняет: десятки
        # одинаковых «нет исполнителя» только закроют её собой.
        _check_assignees(gaps, items)

    _apply_notes(items, notes, deviations)
    items.sort(key=lambda i: (i.start, ROLES.index(i.role) if i.role in ROLES else 99, i.item_id))
    applied = _apply_overrides(items, overrides)
    routes = _build_routes(staff, items)
    legacy = _legacy_records(overrides, notes, deviations)

    return ShiftPlan(
        day=day,
        mode=mode,
        staff=staff,
        routes=routes,
        items=tuple(items),
        gaps=tuple(gaps),
        timing=timing,
        board=board,
        overrides=applied,
        next_menu=tuple(next_menu),
        legacy=legacy,
    )


#: Обязанности, содержание которых порождается меню следующего дня.
NEXT_DAY_DUTY_KEYS: tuple[str, ...] = ("next-day-prep", "sunday-prep")


def _needs_next_day_prep(
    day: date,
    cfg: DutySettings,
    next_menu: tuple[MenuLine, ...],
    mode: str,
) -> bool:
    """Нужно ли выводить подготовку из меню следующего дня.

    Подготовка не создаётся, если сама обязанность отключена настройкой или
    не действует в режиме этого дня: тогда это явная причина, а не работа,
    которой в этом дне быть не может.

    Нет меню следующего дня — не повод молча пропустить обязанность. Если
    режим её допускает, отсутствие меню остаётся видимой причиной: иначе
    вечер пятницы выглядел бы закрытым, хотя завтра готовить нечего.
    """
    if day.weekday() == 6:
        keys = ("sunday-prep",)
    else:
        keys = ("next-day-prep",)
    active = {d.key for d in duties_for(day.weekday(), mode, cfg)}
    return any(k not in cfg.disabled and k in active for k in keys)


def _add_next_day_prep(
    items: list[PlannedItem],
    gaps: list[Gap],
    day: date,
    mode: str,
    staff: tuple[Employee, ...],
    assigner: _Assigner,
    cfg: DutySettings,
    next_menu: tuple[MenuLine, ...],
    next_board: TaskBoard | None,
) -> None:
    """Подготовка следующего дня порождается его меню, ТТК и порциями.

    Статического списка работ нет: операции берутся из задач ближайшего
    следующего дня, поэтому замена блюда в понедельник меняет и воскресный
    план (AC10, AC11).
    """
    key = "sunday-prep" if day.weekday() == 6 else "next-day-prep"
    duty = DUTIES_BY_KEY.get(key)
    if duty is None:
        return
    if not next_menu:
        gaps.append(
            Gap(
                code="gap:no-next-menu",
                subject=duty.operation,
                reason=REASON_NO_NEXT_MENU,
                blocks="подготовка полуфабрикатов и сырья к следующему дню",
            )
        )
        return
    board = next_board or TaskBoard((), {}, {}, ())
    if not board.tasks:
        gaps.append(
            Gap(
                code="gap:no-next-operations",
                subject=f"{duty.operation} ({_menu_names(next_menu)})",
                reason=REASON_NO_TEMPLATE,
                blocks="подготовка полуфабрикатов и сырья к следующему дню",
            )
        )
        return

    window = cfg.window_of(duty)
    start_t = window[0] if window else None
    if start_t is None:
        # Окна подготовки в источнике нет (воскресенье). Работы всё равно
        # показываются — с явной причиной, а не исчезают из плана.
        gaps.append(
            Gap(
                code="gap:prep-window",
                subject=duty.operation,
                reason=REASON_NO_WINDOW,
                blocks="назначение времени подготовки к следующему дню",
            )
        )

    cursor = datetime.combine(day, start_t or time(0, 0))
    for task in _prep_tasks(board):
        portions = sum(line.portions for line in next_menu if line.recipe == task.source)
        key = "З-" + _work_key(
            kind="prep",
            role=task.role,
            stage=task.stage,
            operation=task.operation,
            source=task.source,
            meal=task.meal,
            order=task.order,
        )
        if not task.duration_min:
            gaps.append(
                Gap(
                    code=f"gap:prep-duration:{task.task_id}",
                    subject=f"{task.source}: {task.operation}",
                    reason=REASON_NO_DURATION,
                    blocks="подготовка к следующему дню",
                )
            )
            items.append(_prep_item(task, duty, key, cursor, cursor, portions, ()))
            continue
        if start_t is None:
            items.append(
                _prep_item(
                    task, duty, key, cursor, cursor, portions, (), REASON_NO_WINDOW
                )
            )
            continue
        start = cursor
        end = start + timedelta(minutes=task.duration_min)
        cursor = end
        span = Interval(to_minutes(start.time()), to_minutes(end.time()))
        roles = (task.role,) if task.role in ROLES else ROLES
        employee = assigner.place(roles, span)
        violations = (
            check_all(
                duty, span, mode=mode, employee=employee, duration=task.duration_min
            )
            if employee is not None
            else ()
        )
        items.append(
            _prep_item(
                task,
                duty,
                key,
                start,
                end,
                portions,
                violations,
                REASON_BLOCKED if violations else "",
                employee,
            )
        )


def _prep_item(
    task: Task,
    duty: DutyRule,
    key: str,
    start: datetime,
    end: datetime,
    portions: int,
    violations: tuple[Violation, ...],
    reason: str,
    employee: Employee | None = None,
) -> PlannedItem:
    """Строка подготовки следующего дня, порождённая меню этого дня."""
    return PlannedItem(
        item_id=key,
        status=STATUS_BLOCKED if violations else STATUS_TODO,
        start=start,
        end=end,
        operation=f"{task.operation} (на следующий день)",
        role=task.role,
        stage=task.stage,
        assignee=employee.name if employee else "",
        meal=task.meal,
        recipe=task.source,
        task_id=task.task_id,
        products=task.products,
        portions=portions,
        duration_min=task.duration_min,
        source=task.source,
        note=task.note or duty.note,
        reason=reason,
        violations=violations,
        zone=duty.zone,
        action=duty.action,
    )


def _prep_tasks(board: TaskBoard) -> tuple[Task, ...]:
    """Подготовительные операции ближайшего следующего дня."""
    out: list[Task] = []
    for tasks in board.prep.values():
        out.extend(tasks)
    return tuple(sorted(out, key=lambda t: (t.order, t.task_id)))


def _menu_names(menu: tuple[MenuLine, ...]) -> str:
    return ", ".join(dict.fromkeys(line.recipe for line in menu))


def _check_menu_coverage(
    gaps: list[Gap],
    menu: tuple[MenuLine, ...],
    board: TaskBoard,
) -> None:
    """Блюдо без операций ТТК не даёт готового плана (AC15).

    Пустое меню — допустимое состояние, а вот выбранное блюдо без операций
    означает неполные данные: молчаливый пустой план выглядел бы как
    «всё запланировано», поэтому такая работа называется прямо.
    """
    planned = {task.source for task in board.tasks}
    planned |= {
        name for names in board.chains for name in names
    }
    for line in menu:
        if line.portions <= 0 or line.recipe in planned:
            continue
        gaps.append(
            Gap(
                code=f"gap:no-operations:{line.recipe}",
                subject=line.recipe,
                reason=REASON_NO_TEMPLATE,
                blocks="приготовление блюда и связанные с ним операции",
            )
        )


def _assign_production(
    items: list[PlannedItem],
    timing: Timing,
    board: TaskBoard,
    menu: tuple[MenuLine, ...],
    assigner: _Assigner,
    mode: str,
    staff: tuple[Employee, ...],
) -> None:
    """Назначает операции ТТК, порождённые на шагах 3–4."""
    if not board.tasks:
        return
    for st in timing.scheduled:
        task = st.task
        span = Interval(to_minutes(st.start_t), to_minutes(st.end_t))
        roles = (task.role,) if task.role in ROLES else ROLES
        employee = assigner.place(roles, span)
        violations: tuple[Violation, ...] = ()
        if employee is not None:
            violations = check_all(
                DutyRule(
                    key=task.task_id,
                    source="ТТК",
                    operation=task.operation,
                    role=task.role,
                    kind="",
                    stage=task.stage,
                    duration_min=task.duration_min,
                ),
                span,
                mode=mode,
                employee=employee,
                duration=task.duration_min,
            )
        items.append(
            PlannedItem(
                item_id="З-"
                + _work_key(
                    kind="task",
                    role=task.role,
                    stage=task.stage,
                    operation=task.operation,
                    source=task.source,
                    meal=task.meal,
                    order=task.order,
                ),
                status=STATUS_BLOCKED if violations else STATUS_TODO,
                start=st.start,
                end=st.end,
                operation=task.operation,
                role=task.role,
                stage=task.stage,
                assignee=employee.name if employee else "",
                meal=task.meal,
                recipe=task.source,
                task_id=task.task_id,
                products=task.products,
                portions=_portions_for(task, menu),
                duration_min=task.duration_min,
                source=task.source,
                note=task.note,
                reason=REASON_BLOCKED if violations else "",
                violations=violations,
            )
        )


def _add_duties(
    items: list[PlannedItem],
    gaps: list[Gap],
    day: date,
    mode: str,
    staff: tuple[Employee, ...],
    assigner: _Assigner,
    cfg: DutySettings,
    tasting_executor: str,
) -> None:
    """Добавляет обязанности каталога, которых нет в ТТК.

    Бригадная работа (п. 6, 16) даёт отдельную строку на каждую требуемую роль:
    участие шефа, повара и разнорабочего в выдаче видно в общем плане и в
    маршруте каждого. Одна роль — одна строка со своим устойчивым ID, поэтому
    отметки и комментарии не смешиваются между участниками.
    """
    for duty in duties_for(day.weekday(), mode, cfg):
        roles = duty.involved_roles()
        names: tuple[str, ...] = ()
        if duty.control_point == "Бракераж" and tasting_executor:
            # Исполнитель — человек, а не роль: ищем именно его (п. 15).
            names = (tasting_executor,)
        if duty.brigade:
            variants = [(r, _brigade_item_id(duty, r)) for r in roles]
        else:
            variants = [(duty.role, _duty_item_id(duty))]
        for variant_role, variant_id in variants:
            variant_roles = (variant_role,) if duty.brigade else roles
            placed = _place_duty(
                duty,
                day=day,
                mode=mode,
                staff=staff,
                assigner=assigner,
                settings=cfg,
                roles=variant_roles,
                names=names,
                role=variant_role,
                item_id=variant_id,
            )
            if placed is None:
                continue
            item = placed
            if not placed.assignee and placed.status != STATUS_BLOCKED:
                # Причина, названная самим размещением, важнее общего текста:
                # «перерыв» и «нет сотрудника» требуют разных действий.
                reason = placed.reason or REASON_NO_ASSIGNEE
                # Роль называется только при провале назначения (время известно):
                # у работы без длительности или окна причина и так конкретна.
                if duty.brigade and placed.start < placed.end and variant_role not in reason:
                    reason = f"роль «{variant_role}»: {reason}"
                    item = replace(placed, reason=reason)
                gaps.append(
                    Gap(
                        code=(
                            f"gap:{duty.key}:{variant_role}"
                            if duty.brigade
                            else f"gap:{duty.key}"
                        ),
                        subject=duty.operation,
                        reason=reason,
                        blocks="назначение исполнителя и печать маршрута",
                    )
                )
            items.append(item)


def _check_assignees(gaps: list[Gap], items: list[PlannedItem]) -> None:
    """Каждая обязательная работа должна иметь исполнителя (AC03, AC06).

    Наличие роли в бригаде не означает, что операция кому-то досталась: человек
    может быть занят всей сменой, а работа — всё равно обязательная. Без такой
    проверки план выглядит готовым, хотя маршрут её не напечатает никто.

    Не считается дефектом работа, у которой нет и времени: её отсутствие уже
    названо своей причиной (длительность или окно не заданы в источнике).
    """
    for item in items:
        if item.assignee or item.start >= item.end:
            continue
        gaps.append(
            Gap(
                code=f"gap:assignee:{item.item_id}",
                subject=item.operation or item.item_id,
                reason=item.reason or REASON_NO_ASSIGNEE,
                blocks="назначение исполнителя и печать маршрута",
            )
        )


def _check_coverage(
    gaps: list[Gap],
    items: list[PlannedItem],
    staff: tuple[Employee, ...],
    mode: str,
    staff_note: str,
) -> None:
    """Проверяет, что для каждой роли хватает людей и нет лишних операций."""
    if staff_note:
        return
    used_roles = {i.role for i in items}
    for role in ROLES:
        if role in used_roles and not any(e.role == role for e in staff):
            gaps.append(
                Gap(
                    code=f"role:{role}",
                    subject=f"Нет сотрудника роли «{role}»",
                    reason="состав бригады не содержит этой роли",
                    blocks="назначение работ этой роли",
                )
            )
    # Наложение работ одного сотрудника: у каждого человека своя полоса.
    by_person: dict[str, list[PlannedItem]] = {}
    for item in items:
        if item.assignee and item.status != STATUS_BLOCKED and item.start < item.end:
            by_person.setdefault(item.assignee, []).append(item)
    for person, person_items in sorted(by_person.items()):
        ordered = sorted(person_items, key=lambda i: i.start)
        for prev, nxt in zip(ordered, ordered[1:]):
            if prev.end > nxt.start:
                gaps.append(
                    Gap(
                        code=f"overlap:{person}",
                        subject=f"{person}: работы накладываются",
                        reason=(
                            f"{prev.span} {prev.operation} и "
                            f"{nxt.span} {nxt.operation}"
                        ),
                        blocks=f"маршрут сотрудника «{person}»",
                    )
                )
                break


def _apply_notes(
    items: list[PlannedItem],
    notes: tuple[tuple[str, str], ...],
    deviations: tuple[tuple[str, str], ...],
) -> None:
    """Привязывает комментарии и отклонения к их работам (п. 42, AC12).

    Записи для несуществующих или уже неактуальных работ игнорируются:
    после замены блюда комментарий не должен переползать на новую операцию.
    """
    comments = dict(notes)
    kinds = dict(deviations)
    for item in items:
        if item.item_id in comments:
            object.__setattr__(item, "comment", comments[item.item_id])
        if item.item_id in kinds:
            object.__setattr__(item, "deviation", kinds[item.item_id])


def _legacy_records(
    overrides: tuple[tuple[str, str], ...],
    notes: tuple[tuple[str, str], ...],
    deviations: tuple[tuple[str, str], ...],
) -> tuple[LegacyRecord, ...]:
    """Собирает сохранённые записи старых работ выдачи (PA-03).

    Записи старого общего ID не применяются к новым участникам: исполнитель
    неизвестен, а раздать один DONE всей бригаде — значит выдумать факты.
    Один старый ID даёт ровно одну историческую запись — повторный расчёт не
    плодит дубликаты.
    """
    statuses = dict(overrides)
    comments = dict(notes)
    kinds = dict(deviations)
    out: list[LegacyRecord] = []
    for legacy_id, duty_key in LEGACY_BRIGADE_IDS.items():
        status = statuses.get(legacy_id, "")
        comment = comments.get(legacy_id, "")
        deviation = kinds.get(legacy_id, "")
        if not (status or comment or deviation):
            continue
        duty = DUTIES_BY_KEY.get(duty_key)
        out.append(
            LegacyRecord(
                item_id=legacy_id,
                duty_key=duty_key,
                operation=duty.operation if duty else legacy_id,
                status=status,
                comment=comment,
                deviation=deviation,
            )
        )
    return tuple(out)


def _apply_overrides(
    items: list[PlannedItem], overrides: tuple[tuple[str, str], ...]
) -> tuple[tuple[str, str], ...]:
    """Восстанавливает статусы, введённые пользователем (п. 42, AC13).

    Статус применяется только к той же работе: идентичность устойчивая и не
    совпадает с позицией в списке, поэтому замена блюда не переносит отметку
    на новую операцию (AC13).

    Статус не снимает нарушение ограничения: работа, нарушающая правило,
    остаётся BLOCKED, а пользовательская отметка сохраняется отдельно как
    заявленный факт выполнения (AC08, AC15).
    """
    applied: list[tuple[str, str]] = []
    if not overrides:
        return ()
    wanted = dict(overrides)
    for item in items:
        status = wanted.get(item.item_id)
        if status not in STATUSES:
            continue
        if item.blocked:
            if status != STATUS_BLOCKED and item.claimed_status != status:
                object.__setattr__(item, "claimed_status", status)
                applied.append((item.item_id, status))
            continue
        if item.status != status:
            object.__setattr__(item, "status", status)
            applied.append((item.item_id, status))
    return tuple(applied)


def _build_routes(
    staff: tuple[Employee, ...], items: list[PlannedItem]
) -> tuple[Route, ...]:
    grouped: dict[str, list[PlannedItem]] = {}
    for item in items:
        if not item.assignee:
            continue
        grouped.setdefault(item.assignee, []).append(item)
    out: list[Route] = []
    for employee in staff:
        mine = sorted(grouped.get(employee.name, ()), key=lambda i: (i.start, i.item_id))
        out.append(Route(employee=employee, items=tuple(mine)))
    return tuple(out)


__all__ = [
    "CLOSED_STATUSES",
    "Gap",
    "LEGACY_BRIGADE_IDS",
    "LegacyRecord",
    "PlannedItem",
    "REASON_BLOCKED",
    "REASON_CONFLICT",
    "REASON_LEGACY_EXECUTOR",
    "REASON_NO_ASSIGNEE",
    "REASON_NO_DURATION",
    "REASON_NO_EMPLOYEE",
    "REASON_NO_TEMPLATE",
    "REASON_NO_WINDOW",
    "REASON_STAFF_MISSING",
    "REASON_TASTING_EXECUTOR",
    "REASON_TOO_MANY_SIMULTANEOUS",
    "Route",
    "STATUSES",
    "STATUS_BLOCKED",
    "STATUS_DONE",
    "STATUS_IN_PROGRESS",
    "STATUS_SKIPPED",
    "STATUS_TODO",
    "ShiftPlan",
    "plan_shift",
]