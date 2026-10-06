"""Недельный календарь и режимы дня (п. 46 stage3.md).

  Понедельник — обычный рабочий день.
  Вторник    — обычный рабочий день.
  Среда      — обычный день + календарные задачи среды (вечерняя выпечка п. 25).
  Четверг    — обычный рабочий день.
  Пятница    — обычный день до 14:00, дальше GENERAL_CLEANING 14:00–17:00.
  Суббота    — производственный цикл полуфабрикатов (п. 28).
  Воскресенье— динамическая подготовка под меню понедельника (п. 31).

Режимы не зашиты в планировщик: календарь отдаёт режим, а правила ограничений
(п. 27, 35) читают его отсюда. Переходы недели, месяца и года считаются
календарём Python — специальной обработки для них нет и не требуется.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from kitchen.duties import (
    MODE_FRIDAY,
    MODE_NORMAL,
    MODE_SATURDAY,
    MODE_SUNDAY,
)

RUSSIAN_WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)

#: Режим дня по дню недели, 0 — понедельник.
MODE_BY_WEEKDAY: tuple[str, ...] = (
    MODE_NORMAL,    # Пн
    MODE_NORMAL,    # Вт
    MODE_NORMAL,    # Ср — календарные условия приходят через weekdays
    MODE_NORMAL,    # Чт
    MODE_FRIDAY,    # Пт
    MODE_SATURDAY,  # Сб
    MODE_SUNDAY,    # Вс
)

#: Пятница: производство прекращается в 14:00 (п. 27, ограничение №3).
FRIDAY_PRODUCTION_STOP: int = 14 * 60
FRIDAY_CLEANING_END: int = 17 * 60

#: Горячая кухня не моется до 12:00 (п. 17, ограничение №1).
HOT_KITCHEN_FLOOR_MIN: int = 12 * 60

#: Финальная мойка горячей кухни — 16:30–16:40 (п. 23, ограничение №2).
FINAL_WASH_START: int = 16 * 60 + 30
FINAL_WASH_END: int = 16 * 60 + 40


def weekday_name(day: date) -> str:
    return RUSSIAN_WEEKDAYS[day.weekday()]


def mode_for(day: date) -> str:
    """Режим дня по календарной дате."""
    return MODE_BY_WEEKDAY[day.weekday()]


def next_monday(day: date) -> date:
    """Ближайший понедельник на или после day.

    Воскресенье — подготовка к «ближайшему понедельнику», то есть к завтрашнему
    (п. 31, 47). Понедельник — уже понедельник, возвращается тот же день.
    Любой другой день — понедельник следующей недели.

    Сдвиг считается через `weekday()`: у понедельника weekday() == 0, поэтому
    `(0 - weekday) % 7` даёт 0 для понедельника и 1 для воскресенья.
    """
    ahead = (-day.weekday()) % 7
    return day + timedelta(days=ahead)


def following_monday(day: date) -> date:
    """Понедельник строго после day.

    Для понедельника это следующая неделя, для воскресенья — завтрашний день.
    """
    ahead = (7 - day.weekday()) % 7
    return day + timedelta(days=ahead or 7)


def week_days(day: date) -> tuple[date, ...]:
    """Пн–Вс недели, в которой лежит day."""
    monday = day - timedelta(days=day.weekday())
    return tuple(monday + timedelta(days=i) for i in range(7))


@dataclass(frozen=True, slots=True)
class WeekPlanRow:
    """Одна строка недельного календаря для печати и интерфейса."""

    day: date
    mode: str
    label: str
    production: str
    note: str = ""


def week_plan(day: date) -> tuple[WeekPlanRow, ...]:
    """Календарь недели: каждый день со своим режимом.

    Тексты режимов взяты из п. 46, дополнения — из п. 25, 27, 28, 31.
    """
    descriptions: dict[str, tuple[str, str, str]] = {
        MODE_NORMAL: (
            "Обычный рабочий день",
            "завтрак, обед, выдача 15:00, уборка",
            "пн/ср: вечерняя выпечка для завтраков вт/чт",
        ),
        MODE_FRIDAY: (
            "Обычный день до 14:00, затем GENERAL_CLEANING 14:00–17:00",
            "завтрак, обед; после 14:00 производства нет",
            "производство после 14:00 запрещено",
        ),
        MODE_SATURDAY: (
            "Производственный цикл полуфабрикатов",
            "1 разнорабочий — овощи, 1 повар — мясные полуфабрикаты",
            "обязательный отчёт по п. 29 и маркировка по п. 30",
        ),
        MODE_SUNDAY: (
            "Динамическая подготовка под меню понедельника",
            "меню понедельника → ТТК → порции → полуфабрикаты → подготовка",
            "статического списка работ нет",
        ),
    }
    out: list[WeekPlanRow] = []
    for item in week_days(day):
        mode = mode_for(item)
        label, production, note = descriptions[mode]
        if item == day:
            note = f"выбранный день. {note}".strip()
        out.append(
            WeekPlanRow(
                day=item,
                mode=mode,
                label=label,
                production=production,
                note=note,
            )
        )
    return tuple(out)


def is_production_day(mode: str) -> bool:
    """Допускает ли режим производство после 14:00."""
    return mode != MODE_FRIDAY


__all__ = [
    "FINAL_WASH_END",
    "FINAL_WASH_START",
    "FRIDAY_CLEANING_END",
    "FRIDAY_PRODUCTION_STOP",
    "HOT_KITCHEN_FLOOR_MIN",
    "MODE_BY_WEEKDAY",
    "RUSSIAN_WEEKDAYS",
    "WeekPlanRow",
    "following_monday",
    "is_production_day",
    "mode_for",
    "next_monday",
    "week_days",
    "week_plan",
    "weekday_name",
]