"""Персонал кухни: сотрудники, роли, рабочие графики и перерывы.

Единая конфигурация рабочего времени вместо `if role == ...` в десятках мест
(п. 48 stage3.md). Графики берутся из п. 4 и остаются настраиваемыми:
количество уборщиков задаётся параметром, а не выведено из кода.

Ключевые решения, зафиксированные в stage3.md:

  п. 4  базовые часы: повара 07:00–20:00, разнорабочий 08:00–20:00,
        шеф и уборщики 08:00–17:00. Число поваров, разнорабочих и уборщиков
        настраивается.
  п. 24 после 17:00 шеф и уборщики уже закончили, два повара и разнорабочий
        продолжают ту же смену до 20:00. Это продолжение смены, а не вторая.
  п. 8  персонал кухни обедает 08:30–09:00.
  п. 21 персонал кухни обедает 13:30–14:00.

Перерывы — настройка сотрудника, а не задача смены: они не попадают в
маршрутные листы и не могут быть заняты работой (см. kitchen/core/planner.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time

from kitchen.models import ROLES

ROLE_CHEF = "Шеф-повар"
ROLE_COOK = "Повар"
ROLE_HELPER = "Разнорабочий"
ROLE_CLEANER = "Клининг"

#: Базовые рабочие часы ролей — п. 4 stage3.md, единственный источник в коде.
ROLE_SHIFT: dict[str, tuple[time, time]] = {
    ROLE_COOK: (time(7, 0), time(20, 0)),
    ROLE_HELPER: (time(8, 0), time(20, 0)),
    ROLE_CHEF: (time(8, 0), time(17, 0)),
    ROLE_CLEANER: (time(8, 0), time(17, 0)),
}

#: Перерывы персонала кухни — п. 8 и п. 21 stage3.md.
DEFAULT_BREAKS: tuple[tuple[time, time], ...] = (
    (time(8, 30), time(9, 0)),
    (time(13, 30), time(14, 0)),
)

#: Граница, после которой идёт вечерняя часть смены (п. 24, 25, 26).
EVENING_MARK: time = time(17, 0)


def to_minutes(value: time) -> int:
    """Время суток в минутах от полуночи."""
    return value.hour * 60 + value.minute


def minutes_between(start: time, end: time) -> int:
    """Минут от start до end; равные времена дают 0.

    Это ДЛИТЕтельность промежутка, а не смещение от начала смены: 17:00–17:00
    — нулевой промежуток, а не сутки. Смена через полночь считается корректно.
    """
    delta = to_minutes(end) - to_minutes(start)
    return delta if delta >= 0 else delta + 24 * 60


def shift_offset(shift_start: time, value: time) -> int:
    """Смещение времени суток от начала смены, всегда неотрицательное.

    Отдельная величина по смыслу: «работа начинается через 0 минут от начала
    смены» и «смена длится 0 минут» — разные факты, смешивать их нельзя.
    """
    delta = to_minutes(value) - to_minutes(shift_start)
    return delta if delta >= 0 else delta + 24 * 60


def shift_span(shift: tuple[time, time]) -> int:
    """Длительность смены в минутах."""
    return shift_offset(shift[0], shift[1]) if shift[1] != shift[0] else 0


def combine(day: date, value: time) -> datetime:
    """Сутки + время суток -> datetime."""
    return datetime.combine(day, value)


def subtract_breaks(
    shift: tuple[time, time],
    breaks: tuple[tuple[time, time], ...],
) -> tuple[tuple[time, time], ...]:
    """Рабочий интервал за вычетом перерывов, отсортированный по началу."""
    base_start, base_end = shift
    segments: list[tuple[time, time]] = [(base_start, base_end)]
    for br_start, br_end in sorted(breaks):
        if br_end <= base_start or br_start >= base_end:
            continue
        nxt: list[tuple[time, time]] = []
        for seg_start, seg_end in segments:
            if br_end <= seg_start or br_start >= seg_end:
                nxt.append((seg_start, seg_end))
                continue
            if seg_start < br_start:
                nxt.append((seg_start, br_start))
            if br_end < seg_end:
                nxt.append((br_end, seg_end))
        segments = nxt
    return tuple(sorted(segments))


def breaks_overlap(
    start: time,
    end: time,
    breaks: tuple[tuple[time, time], ...],
) -> tuple[tuple[time, time], ...]:
    """Перерывы, пересекающиеся с интервалом [start, end]."""
    return tuple(
        (b_start, b_end)
        for b_start, b_end in breaks
        if start < b_end and b_start < end
    )


def fits_shift(start: time, end: time, shift: tuple[time, time]) -> bool:
    """Входит ли весь интервал работы в смену.

    Конец интервала не считается занятой минутой: работа с 07:00 до 08:00 при
    смене 07:00–20:00 помещается, работа точно до 20:00 помещается, а работа
    до 20:05 — нет.
    """
    if start == end:
        return shift_offset(shift[0], start) <= shift_span(shift)
    return (
        shift_offset(shift[0], start) + minutes_between(start, end) <= shift_span(shift)
    )


@dataclass(frozen=True, slots=True)
class Employee:
    """Идентифицируемый сотрудник с личным графиком.

    Имя уникально в пределах кухни: маршрутный лист печатается на человека,
    а не на «роль вообще».
    """

    name: str
    role: str
    shift_start: time
    shift_end: time
    breaks: tuple[tuple[time, time], ...] = DEFAULT_BREAKS
    order: int = 0
    """Порядок в списке: он же порядок обхода и печати маршрутов."""

    @property
    def shift(self) -> tuple[time, time]:
        return (self.shift_start, self.shift_end)

    @property
    def shift_min(self) -> int:
        """Длительность смены в минутах."""
        return shift_span(self.shift)

    @property
    def shift_bounds(self) -> tuple[int, int]:
        """Смена как интервал в минутах от полуночи: (начало, конец)."""
        start = to_minutes(self.shift_start)
        return start, start + self.shift_min

    @property
    def works_evening(self) -> bool:
        """Продолжает ли сотрудник смену после 17:00 (п. 24).

        Проверяется пересечением смены с окном 17:00–24:00, а не сравнением
        часов: смена 08:00–17:00 заканчивается ровно в 17:00 и вечерней работы
        не содержит, а смена 20:00–07:00 содержит.
        """
        return self.shift_bounds[1] > to_minutes(EVENING_MARK)

    @property
    def available(self) -> tuple[tuple[time, time], ...]:
        """Рабочие интервалы за вычетом перерывов."""
        return subtract_breaks(self.shift, self.breaks)

    def fits(self, start: time, end: time) -> bool:
        return fits_shift(start, end, self.shift)

    def on_break(self, start: time, end: time) -> bool:
        return bool(breaks_overlap(start, end, self.breaks))

    def replace_shift(self, shift: tuple[time, time]) -> "Employee":
        return replace(self, shift_start=shift[0], shift_end=shift[1])


@dataclass(frozen=True, slots=True)
class StaffSettings:
    """Настройки состава бригады и календарных правил графиков.

    Ничего из этого не зашито в расчёт: планировщик читает только эти поля.
    Состав выходных в источнике не задан (п. 28, 31), поэтому `saturday` и
    `sunday` по умолчанию пусты: планировщик показывает «состав не задан» и
    блокирует только выходные назначения, а не подставляет своих людей.
    """

    cooks: int = 2
    helper: int = 1
    chefs: int = 1
    cleaners: int = 2
    saturday: tuple[Employee, ...] = ()
    """Субботняя смена: один разнорабочий и один повар (п. 28), поимённо."""
    sunday: tuple[Employee, ...] = ()
    """Воскресная смена: состав в источнике не назван (п. 31)."""
    tasting_executor: str = ""
    """Исполнитель бракеража (п. 15). Пусто — точка остаётся неназначенной."""
    role_shift: dict[str, tuple[time, time]] = field(
        default_factory=lambda: dict(ROLE_SHIFT)
    )
    breaks: tuple[tuple[time, time], ...] = DEFAULT_BREAKS
    staff: tuple[Employee, ...] = ()
    """Явно заданный персонал будних смен. Пусто — собирается из численности."""

    def shift_for(self, role: str) -> tuple[time, time]:
        return self.role_shift.get(role, ROLE_SHIFT.get(role, (time(8, 0), time(17, 0))))

    def with_counts(self, **counts: int) -> "StaffSettings":
        """Новая настройка с изменённой численностью бригады."""
        for name, value in counts.items():
            if value < 0:
                raise ValueError(f"численность «{name}» не может быть отрицательной")
        return replace(self, **counts)

    def with_tasting_executor(self, name: str) -> "StaffSettings":
        return replace(self, tasting_executor=name.strip())

    def with_staff(self, staff: tuple[Employee, ...]) -> "StaffSettings":
        return replace(self, staff=staff)

    def with_weekend(
        self,
        saturday: tuple[Employee, ...] | None = None,
        sunday: tuple[Employee, ...] | None = None,
    ) -> "StaffSettings":
        """Новая настройка с заданным составом выходных."""
        return replace(
            self,
            saturday=self.saturday if saturday is None else tuple(saturday),
            sunday=self.sunday if sunday is None else tuple(sunday),
        )


def weekend_staff(
    settings: StaffSettings,
    day: date,
) -> tuple[tuple[Employee, ...], str]:
    """Состав бригады на выходной день и причина, если он не задан.

    Пустой кортеж означает «состав не задан», а не «никого нет»: вызывающий
    обязан показать это пользователю, а не молча выдать пустой план.
    """
    weekday = day.weekday()
    if weekday == 5:
        return settings.saturday, "" if settings.saturday else (
            "Субботный состав не задан (п. 28)"
        )
    if weekday == 6:
        return settings.sunday, "" if settings.sunday else (
            "Воскресный состав не задан (п. 31)"
        )
    return default_staff(settings), ""


def default_staff(settings: StaffSettings | None = None) -> tuple[Employee, ...]:
    """Персонал по умолчанию: шеф, два повара, разнорабочий, N уборщиков.

    Имена вида «Повар 1», «Уборщик 2» — placeholders настраиваемого состава,
    а не данные конкретных людей: пользователь задаёт имена сам.
    """
    cfg = settings or StaffSettings()
    if cfg.staff:
        return tuple(sorted(cfg.staff, key=lambda e: (e.order, e.name)))
    out: list[Employee] = []
    order = 0
    for count, role in (
        (cfg.chefs, ROLE_CHEF),
        (cfg.cooks, ROLE_COOK),
        (cfg.helper, ROLE_HELPER),
        (cfg.cleaners, ROLE_CLEANER),
    ):
        start, end = cfg.shift_for(role)
        for index in range(1, count + 1):
            numbered = count > 1 or role == ROLE_CLEANER
            out.append(
                Employee(
                    name=f"{role} {index}" if numbered else role,
                    role=role,
                    shift_start=start,
                    shift_end=end,
                    breaks=cfg.breaks,
                    order=order,
                )
            )
            order += 1
    return tuple(out)


def employees_by_role(
    staff: tuple[Employee, ...],
    role: str,
) -> tuple[Employee, ...]:
    """Сотрудники роли в порядке обхода."""
    return tuple(e for e in staff if e.role == role)


def by_role(
    staff: tuple[Employee, ...],
) -> tuple[tuple[str, tuple[Employee, ...]], ...]:
    """Персонал, сгруппированный по ролям в порядке ROLES."""
    out: list[tuple[str, tuple[Employee, ...]]] = []
    for role in ROLES:
        people = employees_by_role(staff, role)
        if people:
            out.append((role, people))
    return tuple(out)


def staff_summary(staff: tuple[Employee, ...]) -> tuple[str, ...]:
    """Человекочитаемая сводка состава для печати и интерфейса."""
    out: list[str] = []
    for role, people in by_role(staff):
        names = ", ".join(p.name for p in people)
        windows = ", ".join(
            sorted({f"{p.shift_start:%H:%M}–{p.shift_end:%H:%M}" for p in people})
        )
        out.append(f"{role}: {names} · {windows}")
    return tuple(out)


__all__ = [
    "DEFAULT_BREAKS",
    "EVENING_MARK",
    "Employee",
    "ROLE_CHEF",
    "ROLE_CLEANER",
    "ROLE_COOK",
    "ROLE_HELPER",
    "ROLE_SHIFT",
    "StaffSettings",
    "breaks_overlap",
    "by_role",
    "combine",
    "default_staff",
    "employees_by_role",
    "fits_shift",
    "minutes_between",
    "shift_offset",
    "shift_span",
    "staff_summary",
    "subtract_breaks",
    "to_minutes",
    "weekend_staff",
]
