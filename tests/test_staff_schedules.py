"""Рабочие графики сотрудников (п. 4, 8, 21, 24, 48).

Проверяется исправление дефекта R3: длительность смены и смещение от её начала
— разные величины, равные границы дают нулевую длительность, а не сутки.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from kitchen.staff import (
    DEFAULT_BREAKS,
    EVENING_MARK,
    ROLE_CHEF,
    ROLE_CLEANER,
    ROLE_COOK,
    ROLE_HELPER,
    ROLE_SHIFT,
    Employee,
    StaffSettings,
    breaks_overlap,
    by_role,
    default_staff,
    employees_by_role,
    fits_shift,
    minutes_between,
    shift_offset,
    shift_span,
    staff_summary,
    subtract_breaks,
    weekend_staff,
)

COOK_SHIFT = (time(7, 0), time(20, 0))
HELPER_SHIFT = (time(8, 0), time(20, 0))
DAY_SHIFT = (time(8, 0), time(17, 0))


# ---------------------------------------------------------------------------
# R3: арифметика границ смены
# ---------------------------------------------------------------------------


def test_equal_times_give_zero_length() -> None:
    """Регрессия R3: равные времена трактовались как 1440 минут."""
    assert minutes_between(time(17, 0), time(17, 0)) == 0
    assert minutes_between(time(0, 0), time(0, 0)) == 0
    assert shift_span((time(8, 0), time(8, 0))) == 0


def test_minutes_between_regular_and_overnight() -> None:
    assert minutes_between(time(7, 0), time(20, 0)) == 13 * 60
    assert minutes_between(time(8, 0), time(17, 0)) == 9 * 60
    # Смена через полночь считается в положительную сторону.
    assert minutes_between(time(20, 0), time(7, 0)) == 11 * 60


def test_shift_offset_is_separate_from_span() -> None:
    """Смещение от начала смены и её длительность — независимые величины."""
    assert shift_offset(time(7, 0), time(7, 0)) == 0
    assert shift_span(COOK_SHIFT) == 13 * 60
    assert shift_offset(time(7, 0), time(20, 0)) == 13 * 60
    assert shift_offset(time(7, 0), time(7, 30)) == 30
    assert shift_offset(time(20, 0), time(7, 0)) == 11 * 60


def test_fits_shift_inside_shift() -> None:
    """Регрессия R3: 07:00–08:00 в смене 07:00–20:00 ошибочно не помещалось."""
    assert fits_shift(time(7, 0), time(8, 0), COOK_SHIFT) is True
    assert fits_shift(time(8, 0), time(8, 30), DAY_SHIFT) is True


def test_fits_shift_ending_exactly_at_shift_end() -> None:
    """Конец ровно в конце смены допустим, минута после — нет."""
    assert fits_shift(time(19, 30), time(20, 0), COOK_SHIFT) is True
    assert fits_shift(time(16, 30), time(17, 0), DAY_SHIFT) is True
    assert fits_shift(time(19, 55), time(20, 5), COOK_SHIFT) is False
    assert fits_shift(time(16, 59), time(17, 1), DAY_SHIFT) is False


def test_fits_shift_outside_shift() -> None:
    assert fits_shift(time(6, 0), time(7, 0), COOK_SHIFT) is False
    assert fits_shift(time(6, 30), time(7, 30), COOK_SHIFT) is False
    # Разнорабочий начинает в 08:00 — завтрак в 07:00 ему не подходит.
    assert fits_shift(time(7, 0), time(8, 0), HELPER_SHIFT) is False


def test_works_evening_by_shift() -> None:
    """Регрессия R3: смена 08:00–17:00 ошибочно считалась вечерней."""
    chef = Employee("Шеф", ROLE_CHEF, time(8, 0), time(17, 0))
    cleaner = Employee("Уборщик", ROLE_CLEANER, time(8, 0), time(17, 0))
    cook = Employee("Повар", ROLE_COOK, time(7, 0), time(20, 0))
    helper = Employee("Разнорабочий", ROLE_HELPER, time(8, 0), time(20, 0))
    night = Employee("Ночной", ROLE_COOK, time(20, 0), time(7, 0))

    assert chef.works_evening is False
    assert cleaner.works_evening is False
    assert cook.works_evening is True
    assert helper.works_evening is True
    # Ночная смена захватывает вечерние часы 20:00–24:00.
    assert night.works_evening is True


def test_shift_bounds_and_span() -> None:
    cook = Employee("Повар", ROLE_COOK, time(7, 0), time(20, 0))
    assert cook.shift == COOK_SHIFT
    assert cook.shift_min == 13 * 60
    assert cook.shift_bounds == (7 * 60, 20 * 60)
    assert EVENING_MARK == time(17, 0)


def test_employee_fits_and_on_break_use_its_own_settings() -> None:
    emp = Employee("Шеф", ROLE_CHEF, time(8, 0), time(17, 0))
    assert emp.fits(time(8, 0), time(16, 0)) is True
    assert emp.fits(time(17, 0), time(17, 30)) is False
    assert emp.on_break(time(8, 30), time(9, 0)) is True
    assert emp.on_break(time(9, 0), time(9, 30)) is False
    assert emp.on_break(time(13, 30), time(14, 0)) is True


def test_replace_shift_returns_new_employee() -> None:
    emp = Employee("Повар", ROLE_COOK, time(7, 0), time(20, 0))
    moved = emp.replace_shift((time(8, 0), time(16, 0)))
    assert moved.shift == (time(8, 0), time(16, 0))
    assert moved.name == emp.name
    assert emp.shift == (time(7, 0), time(20, 0)), "исходный сотрудник не меняется"


# ---------------------------------------------------------------------------
# Перерывы (п. 8, 21)
# ---------------------------------------------------------------------------


def test_default_breaks_match_source() -> None:
    assert DEFAULT_BREAKS == ((time(8, 30), time(9, 0)), (time(13, 30), time(14, 0)))


def test_subtract_breaks_splits_shift() -> None:
    segments = subtract_breaks(DAY_SHIFT, DEFAULT_BREAKS)
    assert segments == (
        (time(8, 0), time(8, 30)),
        (time(9, 0), time(13, 30)),
        (time(14, 0), time(17, 0)),
    )


def test_subtract_breaks_without_breaks_returns_whole_shift() -> None:
    assert subtract_breaks(COOK_SHIFT, ()) == (COOK_SHIFT,)


def test_breaks_overlap_touching_does_not_overlap() -> None:
    """Часы работы, примыкающие к перерыву, перерывом не считаются."""
    assert breaks_overlap(time(9, 0), time(9, 30), DEFAULT_BREAKS) == ()
    assert breaks_overlap(time(8, 0), time(8, 30), DEFAULT_BREAKS) == ()
    assert breaks_overlap(time(8, 15), time(8, 45), DEFAULT_BREAKS) == (
        (time(8, 30), time(9, 0)),
    )


# ---------------------------------------------------------------------------
# Состав бригады (п. 4, 48)
# ---------------------------------------------------------------------------


def test_role_shift_matches_source() -> None:
    assert ROLE_SHIFT[ROLE_COOK] == (time(7, 0), time(20, 0))
    assert ROLE_SHIFT[ROLE_HELPER] == (time(8, 0), time(20, 0))
    assert ROLE_SHIFT[ROLE_CHEF] == (time(8, 0), time(17, 0))
    assert ROLE_SHIFT[ROLE_CLEANER] == (time(8, 0), time(17, 0))


def test_default_staff_counts_from_settings() -> None:
    staff = default_staff()
    assert len(employees_by_role(staff, ROLE_CHEF)) == 1
    assert len(employees_by_role(staff, ROLE_COOK)) == 2
    assert len(employees_by_role(staff, ROLE_HELPER)) == 1
    assert len(employees_by_role(staff, ROLE_CLEANER)) == 2
    assert [e.shift for e in employees_by_role(staff, ROLE_COOK)] == [COOK_SHIFT] * 2


def test_number_of_cleaners_is_configurable() -> None:
    for count in (1, 2, 5):
        staff = default_staff(StaffSettings(cleaners=count))
        cleaners = employees_by_role(staff, ROLE_CLEANER)
        assert len(cleaners) == count
        assert len({e.name for e in cleaners}) == count
        assert all(e.shift == DAY_SHIFT for e in cleaners)


def test_staff_order_is_stable_and_unique() -> None:
    staff = default_staff(StaffSettings(cleaners=3))
    orders = [e.order for e in staff]
    assert orders == sorted(orders)
    assert len({e.name for e in staff}) == len(staff)


def test_explicit_staff_replaces_generated() -> None:
    custom = (Employee("Иван", ROLE_COOK, time(7, 0), time(20, 0), order=1),)
    staff = default_staff(StaffSettings().with_staff(custom))
    assert [e.name for e in staff] == ["Иван"]


def test_by_role_keeps_model_order() -> None:
    grouped = by_role(default_staff())
    assert [role for role, _ in grouped] == [
        ROLE_CLEANER,
        ROLE_HELPER,
        ROLE_COOK,
        ROLE_CHEF,
    ]


def test_staff_summary_mentions_names_and_windows() -> None:
    summary = staff_summary(default_staff())
    text = "\n".join(summary)
    assert ROLE_COOK in text
    assert "07:00–20:00" in text
    assert "08:00–17:00" in text


def test_with_counts_rejects_negative() -> None:
    with pytest.raises(ValueError):
        StaffSettings().with_counts(cleaners=-1)


def test_with_tasting_executor_is_trimmed() -> None:
    assert StaffSettings().with_tasting_executor("  Мария  ").tasting_executor == "Мария"
    assert StaffSettings().tasting_executor == ""


# ---------------------------------------------------------------------------
# R5: выходные не получают выдуманный состав
# ---------------------------------------------------------------------------


def test_weekend_composition_is_not_invented() -> None:
    """Регрессия R5: субботний и воскресный состав не задаются по умолчанию."""
    settings = StaffSettings()
    assert settings.saturday == ()
    assert settings.sunday == ()

    saturday, reason = weekend_staff(settings, date(2026, 10, 10))
    assert saturday == ()
    assert "Субботный состав не задан" in reason

    sunday, reason = weekend_staff(settings, date(2026, 10, 11))
    assert sunday == ()
    assert "Воскресный состав не задан" in reason


def test_weekday_uses_regular_composition() -> None:
    staff, reason = weekend_staff(StaffSettings(), date(2026, 10, 8))
    assert reason == ""
    assert len(staff) == 6


def test_weekend_composition_can_be_configured() -> None:
    sat_people = (Employee("Разнорабочий выходной", ROLE_HELPER, time(8, 0), time(14, 0)),)
    settings = StaffSettings().with_weekend(saturday=sat_people)
    staff, reason = weekend_staff(settings, date(2026, 10, 10))
    assert reason == ""
    assert [e.name for e in staff] == ["Разнорабочий выходной"]


def test_with_weekend_keeps_other_side() -> None:
    settings = StaffSettings().with_weekend(sunday=(Employee("Повар выходной", ROLE_COOK, time(8, 0), time(16, 0)),))
    assert settings.saturday == ()
    assert settings.sunday[0].name == "Повар выходной"


def test_role_shift_override_is_used() -> None:
    settings = StaffSettings(role_shift={**ROLE_SHIFT, ROLE_COOK: (time(9, 0), time(18, 0))})
    staff = default_staff(settings)
    cook = employees_by_role(staff, ROLE_COOK)[0]
    assert cook.shift == (time(9, 0), time(18, 0))
    assert settings.shift_for(ROLE_COOK) == (time(9, 0), time(18, 0))
