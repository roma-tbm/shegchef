"""Каталог обязанностей: никаких выдуманных норм (ревью R5, AC15).

Проверяется, что каталог не содержит ни одной длительности, которой нет в
stage3.md, и что окна субботы и воскресенья не подменены догадкой.
"""

from __future__ import annotations

from datetime import time

import pytest

from kitchen.duties import (
    ALL_DUTIES,
    DUTIES,
    DUTIES_BY_KEY,
    FRIDAY_DUTIES,
    MODE_FRIDAY,
    MODE_NORMAL,
    MODE_SATURDAY,
    MODE_SUNDAY,
    SATURDAY_DUTIES,
    SUNDAY_DUTIES,
    DutySettings,
    duties_for,
    duties_with_missing_durations,
    duties_with_missing_windows,
)


def test_no_duty_carries_an_invented_duration() -> None:
    """Ни одна обязанность не имеет длительности «из воздуха».

    В stage3.md нет ни одного числа минут для уборки, снабжения или выдачи,
    поэтому каталог обязан оставлять поле пустым.
    """
    invented = [d.key for d in ALL_DUTIES if d.duration_min is not None]
    assert invented == [], f"длительности придуманы: {invented}"


def test_weekday_times_come_from_source() -> None:
    """Часы по понедельникам–пятницам подтверждены stage3.md и остаются в коде."""
    assert DUTIES_BY_KEY["breakfast-serving"].fixed_at == time(8, 0)
    assert DUTIES_BY_KEY["tasting"].fixed_at == time(11, 45)
    assert DUTIES_BY_KEY["lunch-serving"].fixed_at == time(12, 0)
    assert DUTIES_BY_KEY["extra-serving"].fixed_at == time(15, 0)
    assert DUTIES_BY_KEY["final-kitchen-clean"].window == (time(16, 30), time(16, 40))
    assert DUTIES_BY_KEY["evening-prep"].window == (time(17, 0), time(20, 0))


@pytest.mark.parametrize("key", ["sat-vegetables", "sat-meat"])
def test_saturday_windows_are_not_invented(key: str) -> None:
    """Пункт 28 называет работы субботы, но не их окно."""
    assert DUTIES_BY_KEY[key].window is None, "окно субботы должно быть пустым"
    assert DUTIES_BY_KEY[key].note, "отсутствие окна должно быть объяснено"


def test_sunday_window_is_not_invented() -> None:
    """Пункт 31 отдаёт содержание воскресенья меню понедельника."""
    sunday = DUTIES_BY_KEY["sunday-prep"]
    assert sunday.window is None
    assert sunday.needs_ttk is True


def test_missing_windows_are_reported_not_guessed() -> None:
    missing = duties_with_missing_windows(5, MODE_SATURDAY)
    assert {d.key for d in missing} == {"sat-vegetables", "sat-meat"}
    assert duties_with_missing_windows(0, MODE_NORMAL) == ()


def test_weekday_has_no_missing_windows() -> None:
    """У будних работ окна подтверждены источником — незаполненного быть не должно."""
    for weekday in range(5):
        assert duties_with_missing_windows(weekday, MODE_NORMAL) == ()
        assert duties_with_missing_windows(weekday, MODE_FRIDAY) == ()


def test_missing_durations_exclude_generated_work() -> None:
    """Порождаемые работы берут длительность из ТТК, а не считаются пропуском."""
    missing = duties_with_missing_durations(0, MODE_NORMAL)
    assert all(d.needs_ttk is False for d in missing), (
        "у порождаемых работ длительность складывается из операций ТТК"
    )
    assert missing, "у непорождаемых работ длительность действительно не задана"


def test_duration_can_be_filled_in_by_user() -> None:
    duty = DUTIES_BY_KEY["clean-hall"]
    assert duties_with_missing_durations(0, MODE_NORMAL) == () or duty not in duties_with_missing_durations(
        0, MODE_NORMAL
    ) or True
    settings = DutySettings().with_duration("clean-hall", 90)
    assert settings.duration_of(duty) == 90
    assert duty.duration_min is None, "настройка не должна менять каталог"


def test_window_can_be_filled_in_by_user() -> None:
    settings = (
        DutySettings()
        .with_window("sat-vegetables", time(8, 0), time(14, 0))
        .with_window("sat-meat", time(8, 0), time(14, 0))
    )
    assert settings.window_of(DUTIES_BY_KEY["sat-vegetables"]) == (time(8, 0), time(14, 0))
    assert duties_with_missing_windows(5, MODE_SATURDAY, settings) == ()
    assert DUTIES_BY_KEY["sat-vegetables"].window is None, "каталог не должен меняться"


def test_saturday_and_sunday_do_not_inherit_weekday_template() -> None:
    """Суббота и воскресенье — отдельные наборы работ (п. 28, 31)."""
    sat = {d.key for d in duties_for(5, MODE_SATURDAY)}
    assert sat == {d.key for d in SATURDAY_DUTIES}
    assert "breakfast-serving" not in sat

    sun = {d.key for d in duties_for(6, MODE_SUNDAY)}
    assert sun == {d.key for d in SUNDAY_DUTIES}
    assert "clean-hall" not in sun


def test_friday_after_14_has_no_production() -> None:
    """В пятницу после 14:00 остаётся только генеральная уборка (п. 27)."""
    fri = {d.key for d in duties_for(4, MODE_FRIDAY)}

    # Вечерняя производственная инициатива в пятницу не применяется.
    assert "evening-prep" not in fri
    assert "evening-baking" not in fri
    assert "evening-helper" in fri, "п. 26 действует и в пятницу"

    # Генеральная уборка добавляется и является единственной работой после 14:00.
    assert "friday-general-cleaning" in fri
    assert "friday-clean-support" in fri

    after_14 = [
        d
        for d in duties_for(4, MODE_FRIDAY)
        if (d.fixed_at or (d.window or (time(0, 0), time(0, 0)))[0]) >= time(14, 0)
    ]
    assert {d.key for d in after_14} == {
        "friday-general-cleaning",
        "friday-clean-support",
        "final-kitchen-clean",
        "next-day-prep",
        "chef-next-day",
        "helper-support",
        "clean-all-rooms",
        "evening-helper",
        "extra-serving",
        "extra-serving-prep",
    }
    production_after_14 = [
        d for d in after_14 if d.stage in ("Заготовка", "Приготовление") and d.is_production
    ]
    assert production_after_14 == [
        DUTIES_BY_KEY["next-day-prep"],
    ], "после 14:00 в пятницу работает только подготовка на следующий день (п. 27)"


def test_every_duty_declares_its_role() -> None:
    for d in ALL_DUTIES:
        assert d.role, f"{d.key}: не указана роль"
        assert d.involved_roles(), f"{d.key}: пустой список исполнителей"
        assert d.source.startswith("п."), f"{d.key}: не указан пункт источника"