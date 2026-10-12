"""Тесты сквозного сценария смены и печатных форм.

Модуль не знает про Streamlit и Excel, поэтому проверяется напрямую.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from kitchen import seed
from kitchen.duties import DutySettings, duties_for
from kitchen.models import KitchenData, MenuLine
from kitchen.staff import StaffSettings, default_staff, to_minutes
from kitchen.calendar_rules import mode_for
from kitchen.web.report_store import SemifinishedMarking, SemifinishedOutput
from kitchen.web.shift_plan import (
    PRINTOUT_FORMS,
    ShiftScenario,
    build_scenario,
    gaps_text,
    general_plan_form,
    route_form,
    routes_by_form,
    saturday_report_form,
)

MONDAY = date(2026, 10, 5)
SATURDAY = date(2026, 10, 10)

#: Длительности для теста берутся из черновика шефа, а не из источника.
DURATIONS = DutySettings(
    duration_overrides={
        "all": 10,
    }
)


def _lunch(day: date) -> tuple[MenuLine, ...]:
    recipe = next(iter(seed.RECIPES))
    return (
        MenuLine(
            recipe=recipe,
            portions=20,
            serve_at=time(12, 0),
            meal="Обед",
            day=day,
        ),
    )


def _scenario(day: date = MONDAY, **kwargs) -> ShiftScenario:
    return build_scenario(seed.demo_data(day), day, **kwargs)


def _filled(day: date = MONDAY) -> DutySettings:
    """Заполняет длительности у всех обязанностей дня — тестовый ввод."""
    settings = DutySettings()
    for duty in duties_for(day.weekday(), mode_for(day)):
        limit = 10
        if duty.window:
            limit = min(limit, to_minutes(duty.window[1]) - to_minutes(duty.window[0]))
        if duty.fixed_at:
            limit = min(limit, 10)
        settings = settings.with_duration(duty.key, max(1, limit))
    return settings


def test_scenario_runs_all_steps_of_the_pipeline() -> None:
    """Меню, продукты, операции ТТК, тайминг и маршруты считаются вместе."""
    scenario = _scenario(menu=_lunch(MONDAY))

    assert scenario.day == MONDAY
    assert scenario.needs, "потребность в продуктах должна считаться"
    assert scenario.board.tasks, "операции ТТК должны быть построены"
    assert scenario.timing.scheduled, "обратный тайминг должен быть рассчитан"
    assert scenario.plan.items, "маршруты должны быть построены"


def test_scenario_does_not_invent_other_days() -> None:
    """Меню других дней не попадает в смену."""
    data = seed.demo_data(MONDAY)
    saturday = build_scenario(data, SATURDAY)

    assert saturday.menu == tuple()
    assert saturday.plan.items, "у субботнего дня есть свои обязанности"
    assert all(not i.task_id for i in saturday.plan.items), (
        "операции ТТК без меню не должны появляться"
    )
    assert build_scenario(data, MONDAY).plan.items


def test_scenario_takes_weekend_roster_from_settings() -> None:
    """Выходной состав берётся из настроек, а не из буденной численности."""
    data = seed.demo_data(SATURDAY)
    empty = build_scenario(data, SATURDAY)
    assert empty.plan.staff == tuple()
    assert gaps_text(empty.plan.gaps)

    helper = default_staff(StaffSettings(chefs=0, cooks=0, helper=1, cleaners=0))[0]
    staffed = build_scenario(
        data,
        SATURDAY,
        staff_settings=StaffSettings(saturday=(helper,)),
    )
    assert [e.name for e in staffed.plan.staff] == [helper.name]


def test_general_plan_lists_every_item_with_its_reason() -> None:
    """В общем плане видно и время работы, и причину отсутствия времени."""
    scenario = _scenario(menu=_lunch(MONDAY))
    form = general_plan_form(scenario)

    assert form.title.startswith("Общий план смены")
    assert len(form.rows) == len(scenario.plan.items)
    assert all(len(row) == len(form.header) for row in form.rows)
    unscheduled = [r for r in form.rows if r[0] == "—"]
    assert unscheduled, "незаполненные пункты должны быть видны"
    assert all(row[-1] for row in unscheduled), "у пропуска обязана быть причина"


def test_route_form_contains_only_that_employee() -> None:
    """Маршрут сотрудника не содержит чужих работ."""
    scenario = _scenario(menu=_lunch(MONDAY), duty_settings=_filled())
    employee = scenario.plan.staff[0]
    form = route_form(scenario, employee)

    assert form.title == f"Маршрут — {employee.name}"
    assigned = [r for r in form.rows if r[7] != "—"]
    assert assigned, "у маршрута должны быть назначенные пункты"
    assert all(row[7] in (employee.name, "—") for row in form.rows)
    assert str(employee.shift_start)[:5] in form.subtitle
    assert any("Перерывы" in note for note in form.notes)


def test_routes_by_form_covers_every_employee() -> None:
    """Форм маршрутов столько же, сколько сотрудников в смене."""
    scenario = _scenario(menu=_lunch(MONDAY), duty_settings=_filled())
    forms = routes_by_form(scenario)

    assert len(forms) == len(scenario.plan.staff)
    titles = {f.title for f in forms}
    assert len(titles) == len(scenario.plan.staff), "имена сотрудников должны различаться"


def test_all_declared_printout_forms_are_produced() -> None:
    """Каждая заявленная печатная форма действительно строится."""
    scenario = _scenario(menu=_lunch(MONDAY), duty_settings=_filled())
    forms = [general_plan_form(scenario), *routes_by_form(scenario)]

    for form in forms:
        assert form.rows, f"пустая форма: {form.title}"
        assert form.to_text().startswith(form.title)

    assert PRINTOUT_FORMS[0] == "Общий план"
    assert PRINTOUT_FORMS[-1] == "Субботний отчёт"


def test_printable_columns_are_not_truncated() -> None:
    """Ширина колонки равна самому длинному значению."""
    form = general_plan_form(_scenario(menu=_lunch(MONDAY)))

    for i, width in enumerate(form.widths):
        assert width == max([len(form.header[i])] + [len(r[i]) for r in form.rows])


def test_saturday_report_keeps_missing_fact_empty() -> None:
    """Незаполненный факт остаётся пустым, а не подменяется планом."""
    scenario = _scenario(SATURDAY, menu=_lunch(SATURDAY))
    form = saturday_report_form(scenario)

    assert form.title.startswith("Субботний отчёт")
    assert any("Фактический расход не введён" in n for n in form.notes)
    assert any("Отчёт не списывает складские остатки" in n for n in form.notes)
    for row in form.rows:
        assert row[3] == "—", "расход без ввода должен быть пустым"
        assert row[5] == "—", "выход без ввода должен быть пустым"


def test_saturday_report_shows_consumption_output_and_marking_separately() -> None:
    """Расход, выход и маркировка — разные поля с разными единицами (AC09)."""
    scenario = _scenario(SATURDAY, menu=_lunch(SATURDAY))
    product = scenario.needs[0].items[0].product
    form = saturday_report_form(
        scenario,
        actuals={product: 3.0},
        outputs={product: SemifinishedOutput(qty=7.0, unit="л")},
        markings={
            product: SemifinishedMarking(
                name="Пюре картофельное",
                made=SATURDAY,
                expires=date(2026, 10, 13),
            )
        },
    )

    row = next(r for r in form.rows if r[0] == product)
    assert row[4], "единица расхода обязана печататься рядом с расходом"
    assert row[3] == "3", "фактический расход сырья"
    assert row[5] == "7", "выход полуфабриката введён отдельно"
    assert row[6] == "л", "единица выхода своя"
    assert row[7] == "Пюре картофельное"
    assert row[8] == "10.10.2026"
    assert row[9] == "13.10.2026"


def test_saturday_report_refuses_other_weekdays() -> None:
    """Отчёт за неделю не строится — это защита от неверной даты."""
    with pytest.raises(ValueError, match="субботу"):
        saturday_report_form(_scenario(MONDAY, menu=_lunch(MONDAY)))


def test_empty_menu_does_not_crash_the_scenario() -> None:
    """Пустое меню — это расчёт только обязанностей, а не ошибка."""
    scenario = build_scenario(seed.demo_data(MONDAY), MONDAY, menu=())
    assert scenario.needs == tuple()
    assert scenario.plan.gaps
    assert gaps_text(scenario.plan.gaps) != "Пропусков нет"