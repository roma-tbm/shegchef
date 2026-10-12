"""Календарь недели: режимы дня и переходы к понедельнику (п. 27, 28, 31, 46).

Отдельно проверяется исправление дефекта R2: `next_monday` обязан возвращать
понедельник, а не воскресенье, в том числе на границах месяца и года.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from kitchen.calendar_rules import (
    FINAL_WASH_END,
    FINAL_WASH_START,
    FRIDAY_CLEANING_END,
    FRIDAY_PRODUCTION_STOP,
    HOT_KITCHEN_FLOOR_MIN,
    MODE_BY_WEEKDAY,
    RUSSIAN_WEEKDAYS,
    following_monday,
    is_production_day,
    mode_for,
    next_monday,
    week_days,
    week_plan,
    weekday_name,
)
from kitchen.duties import (
    MODE_FRIDAY,
    MODE_NORMAL,
    MODE_SATURDAY,
    MODE_SUNDAY,
)

MONDAY = date(2026, 10, 5)


# ---------------------------------------------------------------------------
# R2: next_monday возвращает понедельник
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        # Регрессия R2: воскресенье возвращала саму себя.
        (date(2026, 10, 4), date(2026, 10, 5)),
        (date(2027, 1, 31), date(2027, 2, 1)),
        (date(2028, 12, 31), date(2029, 1, 1)),
        # Понедельник — уже понедельник.
        (MONDAY, MONDAY),
        # Остальные дни недели — понедельник следующей недели.
        (date(2026, 10, 6), date(2026, 10, 12)),
        (date(2026, 10, 7), date(2026, 10, 12)),
        (date(2026, 10, 8), date(2026, 10, 12)),
        (date(2026, 10, 9), date(2026, 10, 12)),
        (date(2026, 10, 10), date(2026, 10, 12)),
    ],
)
def test_next_monday(day: date, expected: date) -> None:
    assert next_monday(day) == expected
    assert next_monday(day).weekday() == 0


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (MONDAY, date(2026, 10, 12)),
        (date(2026, 10, 6), date(2026, 10, 12)),
        # Воскресенье: строго после — завтрашний понедельник.
        (date(2026, 10, 4), date(2026, 10, 5)),
        (date(2028, 12, 31), date(2029, 1, 1)),
    ],
)
def test_following_monday(day: date, expected: date) -> None:
    assert following_monday(day) == expected
    assert following_monday(day).weekday() == 0
    assert following_monday(day) > day


@pytest.mark.parametrize(
    "start",
    [
        date(2026, 10, 4),
        date(2026, 12, 31),
        date(2027, 2, 28),
        date(2028, 2, 29),
        date(2029, 12, 30),
        date(2026, 1, 1),
    ],
)
def test_next_monday_is_always_monday(start: date) -> None:
    """Инвариант на любой стартовой дате, включая переходы месяца и года."""
    for step in range(0, 40, 3):
        day = start + timedelta(days=step)
        assert next_monday(day).weekday() == 0
        assert next_monday(day) >= day
        assert (next_monday(day) - day).days <= 7
        assert following_monday(day) > day
        assert following_monday(day).weekday() == 0


def test_sunday_prepares_for_the_imminent_monday() -> None:
    """Воскресенье готовится к понедельнику «завтра», а не через неделю (п. 31)."""
    sunday = date(2026, 10, 11)
    assert sunday.weekday() == 6
    assert next_monday(sunday) == date(2026, 10, 12)
    assert (next_monday(sunday) - sunday).days == 1


# ---------------------------------------------------------------------------
# Режимы дня (п. 46)
# ---------------------------------------------------------------------------


def test_mode_for_each_weekday() -> None:
    expected = [
        MODE_NORMAL,    # пн
        MODE_NORMAL,    # вт
        MODE_NORMAL,    # ср
        MODE_NORMAL,    # чт
        MODE_FRIDAY,    # пт
        MODE_SATURDAY,  # сб
        MODE_SUNDAY,    # вс
    ]
    monday = date(2026, 10, 5)
    for offset, mode in enumerate(expected):
        assert mode_for(monday + timedelta(days=offset)) == mode
    assert len(MODE_BY_WEEKDAY) == len(RUSSIAN_WEEKDAYS) == 7


def test_weekday_names_russian() -> None:
    monday = date(2026, 10, 5)
    assert weekday_name(monday) == "Понедельник"
    assert weekday_name(monday + timedelta(days=6)) == "Воскресенье"


def test_week_days_covers_monday_to_sunday() -> None:
    days = week_days(date(2026, 10, 8))  # четверг
    assert len(days) == 7
    assert days[0] == date(2026, 10, 5)
    assert days[0].weekday() == 0
    assert days[-1].weekday() == 6
    assert days == tuple(days[0] + timedelta(days=i) for i in range(7))


def test_week_days_crosses_month_boundary() -> None:
    days = week_days(date(2026, 10, 1))  # четверг последней недели сентября
    assert days[0] == date(2026, 9, 28)
    assert days[-1] == date(2026, 10, 4)


def test_week_plan_marks_selected_day() -> None:
    rows = week_plan(date(2026, 10, 8))
    assert len(rows) == 7
    marked = [r for r in rows if "выбранный день" in r.note]
    assert len(marked) == 1
    assert marked[0].day == date(2026, 10, 8)
    assert all(r.label and r.production for r in rows)


def test_is_production_day_allows_everything_except_friday() -> None:
    assert is_production_day(MODE_NORMAL) is True
    assert is_production_day(MODE_SATURDAY) is True
    assert is_production_day(MODE_SUNDAY) is True
    assert is_production_day(MODE_FRIDAY) is False


# ---------------------------------------------------------------------------
# Границы жёстких ограничений из календаря (п. 17, 23, 27)
# ---------------------------------------------------------------------------


def test_hard_limit_boundaries_match_source() -> None:
    assert HOT_KITCHEN_FLOOR_MIN == 12 * 60
    assert (FINAL_WASH_START, FINAL_WASH_END) == (16 * 60 + 30, 16 * 60 + 40)
    assert FINAL_WASH_END - FINAL_WASH_START == 10
    assert FRIDAY_PRODUCTION_STOP == 14 * 60
    assert FRIDAY_CLEANING_END == 17 * 60
