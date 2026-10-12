"""PA-02: текстовые печатные формы помещаются в A4 landscape 10 pt.

Раньше общий план и маршруты экспортировались строкой шириной 190 символов.
При заявленных A4 landscape, моноширинном шрифте 10 pt и полях 28 pt это
около 1144 pt при доступных 786 pt: правые колонки (исполнитель, комментарий)
обрезались. Проверка опирается на физическую геометрию страницы, а не на
«красоту» переносов.
"""

from __future__ import annotations

from datetime import date, time

from kitchen import seed
from kitchen.models import MenuLine
from kitchen.staff import StaffSettings, default_staff
from kitchen.web.shift_plan import (
    PAGE_WIDTH,
    build_scenario,
    general_plan_form,
    route_form,
    routes_by_form,
    saturday_report_form,
)

MONDAY = date(2026, 10, 5)
SATURDAY = date(2026, 10, 10)

#: A4 landscape и параметры печати, заявленные кодом и README.
A4_LANDSCAPE_WIDTH_PT = 841.89
MARGIN_PT = 28
FONT_PT = 10
#: DejaVu Sans Mono: ширина знака 0.602 em, при 10 pt — 6.02 pt.
CHAR_WIDTH_PT = 6.02


def _lunch(day: date) -> tuple[MenuLine, ...]:
    return (
        MenuLine(
            recipe=next(iter(seed.RECIPES)),
            portions=20,
            serve_at=time(12, 0),
            meal="Обед",
            day=day,
        ),
    )


def _scenario(day: date):
    return build_scenario(seed.demo_data(day), day, menu=_lunch(day), staff=default_staff())


def _all_forms():
    monday = _scenario(MONDAY)
    forms = [general_plan_form(monday), *routes_by_form(monday)]
    forms.append(saturday_report_form(_scenario(SATURDAY)))
    return forms


def test_page_width_fits_a4_landscape_at_10pt() -> None:
    """Ширина строки в pt не превышает доступную ширину листа."""
    available_pt = A4_LANDSCAPE_WIDTH_PT - 2 * MARGIN_PT
    assert PAGE_WIDTH * CHAR_WIDTH_PT <= available_pt, (
        f"{PAGE_WIDTH} символов * {CHAR_WIDTH_PT} pt = "
        f"{PAGE_WIDTH * CHAR_WIDTH_PT:.2f} pt > {available_pt:.2f} pt"
    )
    assert PAGE_WIDTH * CHAR_WIDTH_PT <= A4_LANDSCAPE_WIDTH_PT


def test_default_forms_fit_the_declared_page() -> None:
    """Каждая форма помещается на заявленную страницу по умолчанию."""
    for form in _all_forms():
        assert form.min_page_width() <= PAGE_WIDTH, form.title
        for line in form.to_text().splitlines():
            assert len(line) <= PAGE_WIDTH, (form.title, line)


def test_reflow_keeps_assignees_and_columns() -> None:
    """После переноса исполнители не теряются, колонки не пропадают."""
    scenario = _scenario(MONDAY)
    form = general_plan_form(scenario)
    text = form.to_text()

    assert len(form.header) == 9
    assert all(len(row) == len(form.header) for row in form.rows)
    for item in scenario.plan.items:
        if item.assignee:
            assert item.assignee in text, item
    widths = form.fitted_widths(PAGE_WIDTH)
    assert all(w > 0 for w in widths)
    assert sum(widths) + 2 * (len(widths) - 1) <= PAGE_WIDTH


def test_route_form_still_shows_every_person() -> None:
    """Маршрут каждого сотрудника собирается и помещается на страницу."""
    scenario = _scenario(MONDAY)
    for employee in scenario.plan.staff:
        form = route_form(scenario, employee)
        text = form.to_text()
        assert employee.name in text
        for line in text.splitlines():
            assert len(line) <= PAGE_WIDTH
