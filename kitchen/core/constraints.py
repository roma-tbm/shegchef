"""Жёсткие ограничения планировщика (п. 35 stage3.md).

Ограничения живут в расчёте, а не в интерфейсе: планировщик не может выдать
назначение, которое их нарушает. Каждое возвращает `Violation` с понятным
текстом — вместо того чтобы молча сдвинуть работу или спрятать конфликт.

  №1  горячая кухня не моется до 12:00 (п. 17);
  №2  финальная мойка горячей кухни — строго 16:30–16:40 (п. 23);
  №3  пятница после 14:00 — никакого производства (п. 27);
  №4  шеф-повар работает 08:00–17:00;
  №5  уборщики работают 08:00–17:00;
  №6  повара работают 07:00–20:00;
  №7  разнорабочий работает 08:00–20:00.

Плюс правила, следующие из рабочего времени сотрудника: задача целиком
внутри смены и не на перерыве. Перерыв — не задача смены (п. 8, 21).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from kitchen.calendar_rules import (
    FINAL_WASH_END,
    FINAL_WASH_START,
    FRIDAY_PRODUCTION_STOP,
    HOT_KITCHEN_FLOOR_MIN,
    MODE_FRIDAY,
)
from kitchen.duties import DutyRule, is_floor_wash_text
from kitchen.staff import Employee, breaks_overlap, fits_shift, minutes_between

LIMIT_HOT_KITCHEN_FLOOR = "hot_kitchen_floor"
LIMIT_FINAL_WASH = "final_wash_window"
LIMIT_FRIDAY = "friday_production_stop"
LIMIT_SHIFT = "shift_bounds"
LIMIT_BREAK = "break_overlap"
LIMIT_WINDOW = "duty_window"

LIMIT_TEXT: dict[str, str] = {
    LIMIT_HOT_KITCHEN_FLOOR: (
        "Горячую кухню нельзя мыть до 12:00 — до этого персонал переносит "
        "горячую посуду"
    ),
    LIMIT_FINAL_WASH: (
        "Финальная мойка горячей кухни выполняется 16:30–16:40"
    ),
    LIMIT_FRIDAY: "В пятницу после 14:00 производство прекращается",
    LIMIT_SHIFT: "Задача выходит за рабочее время сотрудника",
    LIMIT_BREAK: "Задача попадает в перерыв сотрудника",
    LIMIT_WINDOW: "Работа не помещается в своё окно",
}


@dataclass(frozen=True, slots=True)
class Violation:
    """Нарушение ограничения: что, где и почему нельзя."""

    limit: str
    duty_key: str
    operation: str
    role: str
    detail: str

    @property
    def rule(self) -> str:
        return LIMIT_TEXT.get(self.limit, self.limit)

    def __str__(self) -> str:
        who = f" [{self.role}]" if self.role else ""
        return f"{self.rule} — «{self.operation}»{who}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Interval:
    """Интервал работы в минутах от полуночи дня планирования."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end

    def within(self, other: "Interval") -> bool:
        return self.start >= other.start and self.end <= other.end


def to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def from_minutes(value: int) -> time:
    return time(value // 60, value % 60)


def hhmm(value: int) -> str:
    """Минуты от полуночи -> «ЧЧ:ММ» для текстов и печатных форм."""
    return f"{value // 60:02d}:{value % 60:02d}"


def duty_interval(duty: DutyRule, duration: int) -> Interval:
    """Интервал, который обязанность занимает при заданной длительности.

    Фиксированная точка начинается в своё время. Задача в окне занимает
    начало окна: остальное время — резерв, который планировщик может занять
    позже, но окно остаётся верхней границей.
    """
    if duty.fixed_at is not None:
        start = to_minutes(duty.fixed_at)
    elif duty.window is not None:
        start = to_minutes(duty.window[0])
    else:
        start = to_minutes(time(9, 0))
    return Interval(start, start + duration)


def duty_window(duty: DutyRule) -> Interval:
    """Всё окно обязанности, включая резерв."""
    if duty.window is not None:
        return Interval(to_minutes(duty.window[0]), to_minutes(duty.window[1]))
    start = to_minutes(duty.fixed_at or time(9, 0))
    return Interval(start, start)


def check_shift(employee: Employee, duty: DutyRule, span: Interval) -> tuple[Violation, ...]:
    """Задача целиком внутри смены сотрудника (ограничения №4–№7)."""
    shift = Interval(
        to_minutes(employee.shift_start),
        to_minutes(employee.shift_start) + employee.shift_min,
    )
    if span.within(shift):
        return ()
    return (
        Violation(
            limit=LIMIT_SHIFT,
            duty_key=duty.key,
            operation=duty.operation,
            role=duty.role,
            detail=(
                f"{hhmm(span.start)}–{hhmm(span.end)} не помещается "
                f"в смену {employee.name} "
                f"{employee.shift_start:%H:%M}–{employee.shift_end:%H:%M}"
            ),
        ),
    )


def check_break(
    employee: Employee,
    duty: DutyRule,
    span: Interval,
) -> tuple[Violation, ...]:
    """Задача не попадает в перерыв сотрудника (п. 8, 21).

    Перерывы — это время еды, а не производственная задача. Назначать в них
    работу нельзя даже там, где описание совпадает: приоритет у перерыва,
    трактовка записана в отчёте реализации.
    """
    hits = breaks_overlap(
        from_minutes(span.start),
        from_minutes(span.end),
        employee.breaks,
    )
    if not hits:
        return ()
    windows = ", ".join(f"{a:%H:%M}–{b:%H:%M}" for a, b in hits)
    return (
        Violation(
            limit=LIMIT_BREAK,
            duty_key=duty.key,
            operation=duty.operation,
            role=duty.role,
            detail=f"перерыв {employee.name}: {windows}",
        ),
    )


def check_hot_kitchen_floor(duty: DutyRule, span: Interval) -> tuple[Violation, ...]:
    """Ограничение №1: мойка пола горячей кухни не раньше 12:00 (п. 17).

    Проверяется и в начале интервала, и на пересечении: задача, начавшаяся
    в 11:40 и длящаяся до 12:30, тоже запрещена — горячий пол моют после
    того, как персонал закончил переносить посуду.
    """
    if not is_floor_wash(duty):
        return ()
    if span.start >= HOT_KITCHEN_FLOOR_MIN:
        return ()
    return (
        Violation(
            limit=LIMIT_HOT_KITCHEN_FLOOR,
            duty_key=duty.key,
            operation=duty.operation,
            role=duty.role,
            detail=(
                f"план {hhmm(span.start)}–{hhmm(span.end)} "
                f"начинается раньше 12:00"
            ),
        ),
    )


#: Признаки мойки пола горячей кухни в текстах обязанностей.
#: Используются только для правил вне каталога; сам каталог помечает работу
#: явно — зоной и характером операции (см. DutyRule.is_hot_floor_wash).
_FLOOR_MARKERS = ("мытьё пол", "мыть пол", "уборка кухни", "финальная уборка кухни",
                  "мытьё полов", "мойка пола")


def is_floor_wash(duty: DutyRule) -> bool:
    """Мытьё пола горячей кухни, а не уборка зала или посуды.

    Сначала читается явный признак операции и зоны, и только потом текст:
    переформулировка описания не должна снимать ограничение безопасности (п. 17).
    """
    if duty.action or duty.zone:
        return duty.is_hot_floor_wash
    if is_floor_wash_text(duty.operation):
        return True
    text = duty.operation.lower()
    return any(marker in text for marker in _FLOOR_MARKERS)


def check_final_wash(duty: DutyRule, span: Interval) -> tuple[Violation, ...]:
    """Ограничение №2: финальная мойка строго в окне 16:30–16:40 (п. 23).

    Проверяется и при попытке задать ранний шаблон: обязанность с признаком
    жёсткого ограничения нельзя поставить в другое время.
    """
    if duty.hard_constraint != LIMIT_FINAL_WASH:
        return ()
    if (span.start, span.end) == (FINAL_WASH_START, FINAL_WASH_END):
        return ()
    return (
        Violation(
            limit=LIMIT_FINAL_WASH,
            duty_key=duty.key,
            operation=duty.operation,
            role=duty.role,
            detail=(
                f"план {hhmm(span.start)}–{hhmm(span.end)} "
                f"вне окна 16:30–16:40"
            ),
        ),
    )


def check_window(duty: DutyRule, span: Interval) -> tuple[Violation, ...]:
    """Весь интервал работы должен помещаться в окно обязанности.

    Окно в источнике — верхняя граница, а не рекомендация: работа 14:00–18:00
    в окне 14:00–17:00 не может быть выполнена в срок, даже если повар свободен
    до вечера. Ограничение проверяется независимо от загрузки сотрудника, иначе
    расписание выглядело бы выполнимым там, где это не так (AC06).
    """
    if duty.window is None:
        return ()
    if duty.fixed_at is not None:
        # Фиксированная точка (11:45 бракераж, 12:00 выдача) стоит в своём
        # времени по п. 25–26 и длится столько, сколько заняла.
        return ()
    window = duty_window(duty)
    if span.within(window):
        return ()
    detail = (
        f"план {hhmm(span.start)}–{hhmm(span.end)} не помещается в окно "
        f"{hhmm(window.start)}–{hhmm(window.end)}"
    )
    return (
        Violation(
            limit=LIMIT_WINDOW,
            duty_key=duty.key,
            operation=duty.operation,
            role=duty.role,
            detail=detail,
        ),
    )


def check_friday(duty: DutyRule, span: Interval, mode: str) -> tuple[Violation, ...]:
    """Ограничение №3: в пятницу после 14:00 производства нет (п. 27).

    Проверяется и пересечение границы: работа 13:50–14:10 пересекает 14:00
    и запрещена целиком, а не обрезается по 14:00 молча.
    """
    if mode != MODE_FRIDAY:
        return ()
    if not duty.is_production:
        return ()
    if span.start >= FRIDAY_PRODUCTION_STOP:
        detail = f"план {hhmm(span.start)}–{hhmm(span.end)} целиком после 14:00"
    elif span.end > FRIDAY_PRODUCTION_STOP:
        detail = (
            f"план {hhmm(span.start)}–{hhmm(span.end)} "
            f"пересекает 14:00"
        )
    else:
        return ()
    return (
        Violation(
            limit=LIMIT_FRIDAY,
            duty_key=duty.key,
            operation=duty.operation,
            role=duty.role,
            detail=detail,
        ),
    )


def check_all(
    duty: DutyRule,
    span: Interval,
    *,
    mode: str,
    employee: Employee | None = None,
    duration: int | None = None,
) -> tuple[Violation, ...]:
    """Полная проверка обязанности перед назначением.

    Порядок не влияет на результат: нарушения собираются все, чтобы план
    показывал не одну, а все причины блокировки.
    """
    out: list[Violation] = []
    out.extend(check_hot_kitchen_floor(duty, span))
    out.extend(check_final_wash(duty, span))
    out.extend(check_friday(duty, span, mode))
    out.extend(check_window(duty, span))
    if employee is not None:
        out.extend(check_shift(employee, duty, span))
        out.extend(check_break(employee, duty, span))
    return tuple(out)


__all__ = [
    "Interval",
    "LIMIT_BREAK",
    "LIMIT_FINAL_WASH",
    "LIMIT_FRIDAY",
    "LIMIT_HOT_KITCHEN_FLOOR",
    "LIMIT_SHIFT",
    "LIMIT_TEXT",
    "LIMIT_WINDOW",
    "Violation",
    "check_all",
    "check_break",
    "check_final_wash",
    "check_friday",
    "check_hot_kitchen_floor",
    "check_shift",
    "check_window",
    "duty_interval",
    "duty_window",
    "from_minutes",
    "hhmm",
    "is_floor_wash",
    "to_minutes",
]