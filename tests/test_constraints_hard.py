"""Жёсткие ограничения планировщика (п. 17, 23, 27, 35).

Проверяется исправление дефекта R4: мойка пола горячей кухни распознаётся по
явному признаку операции и зоны, а не по ограниченному набору формулировок,
и ограничение включено в общую проверку перед назначением.
"""

from __future__ import annotations

from datetime import time

import pytest

from kitchen.calendar_rules import (
    FINAL_WASH_END,
    FINAL_WASH_START,
    FRIDAY_PRODUCTION_STOP,
    HOT_KITCHEN_FLOOR_MIN,
)
from kitchen.core.constraints import (
    LIMIT_BREAK,
    LIMIT_FINAL_WASH,
    LIMIT_FRIDAY,
    LIMIT_HOT_KITCHEN_FLOOR,
    LIMIT_SHIFT,
    Interval,
    LIMIT_TEXT,
    Violation,
    check_all,
    check_break,
    check_final_wash,
    check_friday,
    check_hot_kitchen_floor,
    check_shift,
    duty_interval,
    duty_window,
    from_minutes,
    hhmm,
    is_floor_wash,
    to_minutes,
)
from kitchen.duties import (
    ACT_DISH_WASH,
    ACT_DUST,
    ACT_FLOOR_WASH,
    ACT_GENERAL_WASH,
    ACT_PRODUCTION,
    ACT_SURFACE,
    DUTIES_BY_KEY,
    FIXED_TIME,
    MODE_FRIDAY,
    MODE_NORMAL,
    TIME_WINDOW,
    ZONE_DISHES,
    ZONE_HALL,
    ZONE_HOT_KITCHEN,
    ZONE_PRODUCTION,
    DutyRule,
)
from kitchen.staff import ROLE_CHEF, ROLE_CLEANER, ROLE_COOK, Employee

CLEANER = Employee("Уборщик", ROLE_CLEANER, time(8, 0), time(17, 0))
CHEF = Employee("Шеф", ROLE_CHEF, time(8, 0), time(17, 0))
COOK = Employee("Повар", ROLE_COOK, time(7, 0), time(20, 0))


def duty(
    key: str = "d",
    operation: str = "Операция",
    *,
    stage: str = "Закрытие",
    duration: int | None = 10,
    zone: str = "",
    action: str = "",
    hard_constraint: str = "",
    fixed_at: time | None = None,
    window: tuple[time, time] | None = None,
) -> DutyRule:
    return DutyRule(
        key=key,
        source="п. 17",
        operation=operation,
        role=ROLE_CLEANER,
        kind=FIXED_TIME if fixed_at else TIME_WINDOW,
        stage=stage,
        duration_min=duration,
        fixed_at=fixed_at,
        window=window,
        hard_constraint=hard_constraint,
        zone=zone,
        action=action,
    )


def limits(violations: tuple[Violation, ...]) -> set[str]:
    return {v.limit for v in violations}


# ---------------------------------------------------------------------------
# R4: распознавание мойки пола горячей кухни
# ---------------------------------------------------------------------------


def test_review_repro_is_a_violation() -> None:
    """Регрессия R4: этот текст раньше проходил проверку без нарушения."""
    rule = DutyRule(
        "hot-floor",
        "test",
        "Мойка горячего кухонного пола",
        ROLE_CLEANER,
        TIME_WINDOW,
        "Закрытие",
        10,
    )
    assert is_floor_wash(rule) is True
    violations = check_hot_kitchen_floor(rule, Interval(719, 729))
    assert limits(violations) == {LIMIT_HOT_KITCHEN_FLOOR}
    assert "11:59–12:09" in violations[0].detail


@pytest.mark.parametrize(
    "operation",
    [
        "Мойка горячего кухонного пола",
        "Мойка пола горячей кухни",
        "Мытьё полов",
        "Мыть пол кухни",
        "Мойка пола",
        "Влажная уборка пола",
        "Уборка кухни",
        "Финальная уборка кухни",
        "Протереть пол кухни",
        "Мытьё пола производственной кухни",
    ],
)
def test_floor_wash_variants_are_recognised(operation: str) -> None:
    assert is_floor_wash(duty(operation=operation)) is True
    assert check_hot_kitchen_floor(duty(operation=operation), Interval(600, 660))


@pytest.mark.parametrize(
    "operation",
    [
        "Мойка белой посуды",
        "Мытьё металлической посуды",
        "Протирание столов",
        "Поверхностная уборка обеденного зала",
        "Уборка коридора",
        "Уборка производственных помещений",
        "Процеживание компотов",
        "Пополнение солонок и перечниц",
        "Финальная мойка горячей кухни",
    ],
)
def test_other_work_is_not_floor_wash(operation: str) -> None:
    assert is_floor_wash(duty(operation=operation)) is False


def test_explicit_marker_beats_wording() -> None:
    """Явный признак операции важнее текста: переформулировка не снимает запрет."""
    rule = duty(operation="Уборка кухни по графику", zone=ZONE_HOT_KITCHEN, action=ACT_FLOOR_WASH)
    assert is_floor_wash(rule) is True
    assert check_hot_kitchen_floor(rule, Interval(700, 720))


def test_explicit_marker_can_exclude_wording() -> None:
    """Явно помеченная поверхностная уборка зала не считается мойкой пола."""
    rule = duty(operation="Мытьё пола — только по графику", zone=ZONE_HALL, action=ACT_SURFACE)
    assert is_floor_wash(rule) is False
    assert check_hot_kitchen_floor(rule, Interval(600, 630)) == ()


@pytest.mark.parametrize(
    ("zone", "action"),
    [
        (ZONE_HALL, ACT_SURFACE),
        (ZONE_DISHES, ACT_DISH_WASH),
        (ZONE_PRODUCTION, ACT_DUST),
        (ZONE_HOT_KITCHEN, ACT_GENERAL_WASH),
        ("", ACT_PRODUCTION),
    ],
)
def test_only_floor_wash_is_blocked_before_noon(zone: str, action: str) -> None:
    rule = duty(operation="Любая работа", zone=zone, action=action)
    assert is_floor_wash(rule) is False


def test_catalog_rules_carry_explicit_zones_and_actions() -> None:
    """Каталог проставляет зону и характер операции там, где это сказано в п. 11–23."""
    # п. 11 и п. 23 прямо называют помещение.
    named = [
        DUTIES_BY_KEY[key]
        for key in ("clean-hall", "clean-corridor", "clean-production", "wipe-tables",
                    "final-kitchen-clean", "dishes-after-breakfast", "metal-dishes",
                    "white-dishes", "juice-dispenser", "stock-up")
    ]
    assert all(d.zone for d in named), "у уборки должна быть явная зона"
    assert all(d.action for d in named), "у уборки должен быть характер операции"

    # п. 27 перечисление помещений не приводит: зона не выдумывается, но это
    # обязано быть видно в примечании, а не молчаливо пустым (AC15).
    unspecified = [
        DUTIES_BY_KEY[key]
        for key in ("friday-general-cleaning", "clean-all-rooms", "clean-after-breakfast")
    ]
    for d in unspecified:
        assert d.zone == "", f"{d.key}: зона не названа в источнике"
        assert d.action, f"{d.key}: характер операции должен быть указан"
        assert d.note, f"{d.key}: отсутствие зоны должно быть объяснено"

    hot = [d for d in DUTIES_BY_KEY.values() if d.is_hot_floor_wash]
    assert hot, "в каталоге есть мойка пола горячей кухни"
    assert all(d.zone == ZONE_HOT_KITCHEN for d in hot)


# ---------------------------------------------------------------------------
# Ограничение №1: горячая кухня не моется до 12:00 (п. 17, 40)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "violated"),
    [
        (8 * 60, 9 * 60, True),
        (11 * 60, 11 * 60 + 30, True),
        (11 * 60 + 40, HOT_KITCHEN_FLOOR_MIN, True),
        (11 * 60 + 40, 11 * 60 + 50, True),  # пересекает 12:00
        (HOT_KITCHEN_FLOOR_MIN, 13 * 60, False),
        (16 * 60 + 30, FINAL_WASH_END, False),
    ],
)
def test_hot_floor_before_noon(start: int, end: int, violated: bool) -> None:
    rule = DUTIES_BY_KEY["final-kitchen-clean"]
    got = bool(check_hot_kitchen_floor(rule, Interval(start, end)))
    assert got is violated


def test_early_floor_wash_reaches_general_check() -> None:
    """Ограничение не должно обходиться общей проверкой перед назначением."""
    rule = duty(operation="Мойка пола кухни", zone=ZONE_HOT_KITCHEN, action=ACT_FLOOR_WASH)
    violations = check_all(rule, Interval(11 * 60, 12 * 60), mode=MODE_NORMAL, employee=CLEANER)
    assert LIMIT_HOT_KITCHEN_FLOOR in limits(violations)


# ---------------------------------------------------------------------------
# Ограничение №2: финальная мойка строго 16:30–16:40 (п. 23)
# ---------------------------------------------------------------------------


def test_final_wash_allowed_only_in_exact_window() -> None:
    rule = DUTIES_BY_KEY["final-kitchen-clean"]
    assert check_final_wash(rule, Interval(FINAL_WASH_START, FINAL_WASH_END)) == ()


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (8 * 60, 9 * 60),
        (12 * 60, 13 * 60),
        (FINAL_WASH_START - 10, FINAL_WASH_START),
        (FINAL_WASH_START, FINAL_WASH_END + 5),
        (FINAL_WASH_END, FINAL_WASH_END + 30),
    ],
)
def test_final_wash_outside_window_is_violation(start: int, end: int) -> None:
    """Попытка задать ранний шаблон финальной мойки запрещена (AC07)."""
    rule = DUTIES_BY_KEY["final-kitchen-clean"]
    assert limits(check_final_wash(rule, Interval(start, end))) == {LIMIT_FINAL_WASH}


def test_final_wash_constraint_is_declared_explicitly() -> None:
    rule = DUTIES_BY_KEY["final-kitchen-clean"]
    assert rule.hard_constraint == LIMIT_FINAL_WASH
    assert rule.zone == ZONE_HOT_KITCHEN
    assert duty(operation="Мойка кухни").hard_constraint == "", "прочие правила не помечены"


# ---------------------------------------------------------------------------
# Ограничение №3: пятница после 14:00 — производства нет (п. 27)
# ---------------------------------------------------------------------------


def production(key: str = "cook-window") -> DutyRule:
    return duty(key, "Приготовление супа", stage="Приготовление",
                action=ACT_PRODUCTION, window=(time(9, 0), time(20, 0)))


@pytest.mark.parametrize(
    ("start", "end", "violated"),
    [
        (9 * 60, 10 * 60, False),
        (13 * 60, FRIDAY_PRODUCTION_STOP, False),          # ровно до 14:00
        (13 * 60 + 50, 14 * 60 + 10, True),                 # пересекает границу
        (14 * 60, 15 * 60, True),
        (16 * 60, 18 * 60, True),
    ],
)
def test_friday_production_stop(start: int, end: int, violated: bool) -> None:
    got = bool(check_friday(production(), Interval(start, end), MODE_FRIDAY))
    assert got is violated


def test_friday_boundary_crossing_is_not_truncated_silently() -> None:
    violations = check_friday(production(), Interval(13 * 60 + 50, 14 * 60 + 10), MODE_FRIDAY)
    assert limits(violations) == {LIMIT_FRIDAY}
    assert "пересекает 14:00" in violations[0].detail


def test_friday_rule_does_not_apply_to_other_days() -> None:
    for mode in (MODE_NORMAL, "Суббота", "Воскресенье"):
        assert check_friday(production(), Interval(16 * 60, 18 * 60), mode) == ()


def test_friday_allows_general_cleaning_after_noon() -> None:
    """Генеральная уборка 14:00–17:00 в пятницу разрешена (п. 27)."""
    rule = DUTIES_BY_KEY["friday-general-cleaning"]
    assert check_friday(rule, Interval(14 * 60, 17 * 60), MODE_FRIDAY) == ()


def test_friday_production_in_catalog_is_marked() -> None:
    """Вечерняя подготовка не должна быть обычным шаблоном пятницы."""
    assert DUTIES_BY_KEY["evening-prep"].applies_to(4, MODE_FRIDAY) is False
    assert DUTIES_BY_KEY["evening-prep"].applies_to(4, MODE_NORMAL) is True


# ---------------------------------------------------------------------------
# Ограничения №4–№7: рабочее время и перерывы (п. 4, 8, 21)
# ---------------------------------------------------------------------------


def test_task_outside_shift_is_violation() -> None:
    rule = duty(operation="Мойка белой посуды", zone=ZONE_DISHES, action=ACT_DISH_WASH)
    assert limits(check_shift(CLEANER, rule, Interval(17 * 60, 17 * 60 + 30))) == {LIMIT_SHIFT}
    assert check_shift(CLEANER, rule, Interval(16 * 60, 17 * 60)) == ()
    assert check_shift(COOK, rule, Interval(7 * 60, 20 * 60)) == ()


def test_task_in_break_is_violation() -> None:
    rule = duty(operation="Уборка зала", zone=ZONE_HALL, action=ACT_SURFACE)
    assert limits(check_break(CLEANER, rule, Interval(8 * 60 + 30, 9 * 60))) == {LIMIT_BREAK}
    assert check_break(CLEANER, rule, Interval(13 * 60 + 30, 14 * 60)) != ()
    assert check_break(CLEANER, rule, Interval(9 * 60, 9 * 60 + 30)) == ()


def test_chef_break_is_protected_even_for_planning_window() -> None:
    """Окно 13:30–14:00 совпадает с перерывом — работа туда не назначается."""
    rule = duty(operation="Планирование следующего дня", stage="Контроль",
                window=(time(13, 30), time(14, 0)))
    assert limits(check_break(CHEF, rule, Interval(13 * 60 + 30, 14 * 60))) == {LIMIT_BREAK}


def test_all_violations_are_collected_at_once() -> None:
    """Показываются все причины блокировки, а не только первая."""
    # action не задан: и мойка пола, и производство определяются по описанию —
    # такой синтетический случай закрывает все пять ограничений разом.
    rule = duty(operation="Мойка пола кухни", stage="Заготовка",
                hard_constraint=LIMIT_FINAL_WASH)
    got = limits(
        check_all(rule, Interval(11 * 60, 18 * 60), mode=MODE_FRIDAY, employee=CHEF)
    )
    assert got == {LIMIT_HOT_KITCHEN_FLOOR, LIMIT_FINAL_WASH, LIMIT_FRIDAY, LIMIT_SHIFT, LIMIT_BREAK}


def test_floor_wash_is_not_treated_as_friday_production() -> None:
    """Мойка пола — уборка, а не производство, поэтому п. 27 её не блокирует."""
    rule = duty(operation="Мойка пола кухни", zone=ZONE_HOT_KITCHEN, action=ACT_FLOOR_WASH,
                stage="Заготовка")
    assert rule.is_production is False
    got = limits(check_all(rule, Interval(15 * 60, 16 * 60), mode=MODE_FRIDAY))
    assert got == set()


def test_check_all_without_employee_only_calendar_limits() -> None:
    rule = DUTIES_BY_KEY["final-kitchen-clean"]
    violations = check_all(rule, Interval(FINAL_WASH_START, FINAL_WASH_END), mode=MODE_FRIDAY)
    assert violations == ()


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def test_interval_helpers() -> None:
    span = Interval(600, 660)
    assert span.length == 60
    assert span.overlaps(Interval(630, 700)) is True
    assert span.overlaps(Interval(660, 700)) is False
    assert span.within(Interval(540, 700)) is True
    assert span.within(Interval(540, 630)) is False


def test_time_conversion_roundtrip() -> None:
    assert to_minutes(time(12, 0)) == 720
    assert from_minutes(720) == time(12, 0)
    assert hhmm(719) == "11:59"
    assert hhmm(720) == "12:00"


def test_duty_interval_and_window_from_catalog() -> None:
    fixed = DUTIES_BY_KEY["breakfast-serving"]
    assert duty_interval(fixed, 30) == Interval(8 * 60, 8 * 60 + 30)
    assert duty_window(fixed) == Interval(8 * 60, 8 * 60)

    windowed = DUTIES_BY_KEY["lunch-prep-window"]
    assert duty_interval(windowed, 150) == Interval(9 * 60, 9 * 60 + 150)
    assert duty_window(windowed) == Interval(9 * 60, 11 * 60 + 30)


def test_violation_text_is_readable() -> None:
    rule = DUTIES_BY_KEY["final-kitchen-clean"]
    text = str(check_final_wash(rule, Interval(8 * 60, 9 * 60))[0])
    assert "16:30" in text
    assert rule.operation in text
    assert "Клининг" in text


def test_limit_text_covers_every_limit() -> None:
    for key in (LIMIT_HOT_KITCHEN_FLOOR, LIMIT_FINAL_WASH, LIMIT_FRIDAY, LIMIT_SHIFT, LIMIT_BREAK):
        assert LIMIT_TEXT.get(key), f"для {key} нужен понятный текст"
