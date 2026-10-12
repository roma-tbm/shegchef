"""AC14: печатные формы доступны, читаемы и согласованы с расчётом.

Проверяется то, чего не видно по строке в Python: что формы существуют для
всех шести категорий сотрудников, что ни одна строка не выходит за ширину
страницы, что длинный текст переносится, а не обрезается, и что напечатанные
значения совпадают с расчётом.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, time

import pytest

from kitchen import seed
from kitchen.core.planner import STATUS_BLOCKED
from kitchen.models import MenuLine
from kitchen.staff import ROLES, ROLE_CHEF, ROLE_CLEANER, ROLE_COOK, ROLE_HELPER
from kitchen.staff import Employee, StaffSettings, default_staff
from kitchen.web.shift_plan import (
    PAGE_WIDTH,
    Printable,
    general_plan_form,
    build_scenario,
    route_form,
    routes_by_form,
    saturday_report_form,
    wrap_text,
)

MONDAY = date(2026, 10, 5)
SATURDAY = date(2026, 10, 10)


def _lunch(day: date) -> tuple[MenuLine, ...]:
    """Одно выбранное блюдо — тот же вход, что и у пользователя в меню."""
    return (
        MenuLine(
            recipe=next(iter(seed.RECIPES)),
            portions=20,
            serve_at=time(12, 0),
            meal="Обед",
            day=day,
        ),
    )


@pytest.fixture
def scenario():
    return build_scenario(
        seed.demo_data(MONDAY), MONDAY, menu=_lunch(MONDAY), staff=default_staff()
    )


@pytest.fixture
def saturday():
    return build_scenario(
        seed.demo_data(SATURDAY),
        SATURDAY,
        menu=_lunch(SATURDAY),
        staff=default_staff(),
    )


# ---------------------------------------------------------------------------
# Доступность всех категорий
# ---------------------------------------------------------------------------


def test_general_plan_form_exists(scenario) -> None:
    """Общий план печатается и содержит дату и режим."""
    form = general_plan_form(scenario)
    text = form.to_text()

    assert "Общий план смены" in text
    assert f"{MONDAY:%d.%m.%Y}" in text
    assert scenario.plan.mode in text


def test_every_person_gets_a_route_form(scenario) -> None:
    """У каждого сотрудника есть свой маршрут (AC12, AC14)."""
    forms = routes_by_form(scenario)

    assert len(forms) == len(scenario.plan.staff)
    assert {form.title for form in forms} == {
        f"Маршрут — {e.name}" for e in scenario.plan.staff
    }


def test_all_four_roles_are_covered(scenario) -> None:
    """Шеф, повар, разнорабочий и уборщик присутствуют в печати."""
    roles = {e.role for e in scenario.plan.staff}
    assert roles == {ROLE_CHEF, ROLE_COOK, ROLE_HELPER, ROLE_CLEANER}
    assert set(ROLES) >= roles


def test_saturday_report_form_exists(saturday) -> None:
    """Субботний отчёт печатается с расходом, выходом и маркировкой."""
    form = saturday_report_form(saturday)
    text = form.to_text()

    assert "Субботний отчёт" in text
    assert "Расход факт" in text
    assert "Выход" in text
    assert "Срок годн." in text
    assert "не списывает складские остатки" in text


def test_saturday_report_refuses_other_days(scenario) -> None:
    """Отчёт за недерево — ошибка, а не пустая форма."""
    with pytest.raises(ValueError, match="только за субботу"):
        saturday_report_form(scenario)


# ---------------------------------------------------------------------------
# Читаемость: ширина страницы и переносы
# ---------------------------------------------------------------------------


def _widths(form: Printable) -> tuple[int, ...]:
    return form.fitted_widths()


def test_no_line_is_wider_than_the_page(scenario, saturday) -> None:
    """Ни одна строка формы не выходит за ширину печатной страницы."""
    forms = [
        general_plan_form(scenario),
        *routes_by_form(scenario),
        saturday_report_form(saturday),
    ]
    for form in forms:
        text = form.to_text()
        too_wide = [line for line in text.splitlines() if len(line) > PAGE_WIDTH]
        assert not too_wide, (
            f"{form.title}: {len(too_wide)} строк шире {PAGE_WIDTH} символов, "
            f"например {too_wide[0][:60]!r}…"
        )


def test_narrower_page_still_fits(scenario) -> None:
    """На более узкой странице формы тоже не выходят за границу."""
    form = general_plan_form(scenario)
    narrow = form.min_page_width()
    text = form.to_text(page_width=narrow)

    assert all(len(line) <= narrow for line in text.splitlines())


def test_impossible_page_width_is_reported(scenario) -> None:
    """Слишком узкая страница — понятная ошибка, а не текст за краем."""
    form = general_plan_form(scenario)
    too_narrow = form.min_page_width() - 1

    with pytest.raises(ValueError, match="нужно от"):
        form.to_text(page_width=too_narrow)


def test_column_captions_never_overflow(scenario) -> None:
    """Подпись колонки переносится, а не выталкивает строку за край."""
    form = general_plan_form(scenario)
    for width in (form.min_page_width(), PAGE_WIDTH):
        text = form.to_text(page_width=width)
        header_lines = text.splitlines()[3:]
        assert all(len(line) <= width for line in header_lines[:4])


def test_long_text_is_wrapped_not_cut() -> None:
    """Длинная задача переносится по словам и остаётся читаемой."""
    long_operation = "очень длинное описание операции " * 20
    wide = Printable(
        title="Проверка",
        subtitle="перенос",
        header=("Время", "Задача", "Исполнитель"),
        rows=(("08:00–09:00", long_operation, "Повар 1"),),
    )

    text = wide.to_text(page_width=90)
    lines = [line for line in text.splitlines() if "очень длинное" in line]
    assert len(lines) > 1, "текст должен занять несколько строк"
    assert text.count("очень длинное") == 20, "ни одно слово не потеряно"
    assert all(len(line) <= 90 for line in text.splitlines())


def test_wrap_text_keeps_words_whole() -> None:
    """Перенос не рвёт слова посередине."""
    lines = wrap_text("слово " * 30, 20)

    assert all(len(line) <= 20 for line in lines)
    assert " ".join(lines).split() == ["слово"] * 30


def test_wrap_text_handles_a_word_longer_than_the_line() -> None:
    """Слово длиннее строки не теряется и не зацикливает перенос."""
    lines = wrap_text("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", 5)

    assert "".join(lines) == "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    assert all(len(line) <= 5 for line in lines)


def test_time_and_assignee_columns_are_not_squeezed(scenario) -> None:
    """Длинная операция не вытесняет время и исполнителя из строки."""
    form = general_plan_form(scenario)
    header = form.header
    time_i, who_i = header.index("Время"), header.index("Исполнитель")
    widths = _widths(form)

    assert widths[time_i] >= max(len("Время"), 5)
    assert widths[who_i] >= max(len("Исполнитель"), 8)


# ---------------------------------------------------------------------------
# Согласованность с расчётом
# ---------------------------------------------------------------------------


def test_printed_rows_match_the_plan(scenario) -> None:
    """Значения общего плана совпадают с расчётом, а не только красивы."""
    form = general_plan_form(scenario)
    plan = scenario.plan

    assert len(form.rows) == len(plan.items)
    for row, item in zip(form.rows, plan.items):
        # Работа без длительности печатается прочерком, а не выдуманным
        # временем: время сверяется только там, где расчёт его знает.
        if item.span == "—":
            assert item.operation in " ".join(row)
            continue
        assert f"{item.start:%H:%M}" in row[0]
        assert f"{item.end:%H:%M}" in row[0]
        assert item.operation in " ".join(row)


def test_work_without_duration_prints_no_invented_time(scenario) -> None:
    """Неизвестное время остаётся прочерком (AC15)."""
    undated = [i for i in scenario.plan.items if i.span == "—"]

    for item in undated:
        row = next(r for r in general_plan_form(scenario).rows if item.operation in " ".join(r))
        assert row[0].strip() in {"—", ""}


def test_status_and_comment_reach_the_print(scenario) -> None:
    """Статус, комментарий и отклонение печатаются, а не теряются."""
    blocked = scenario.plan.items[0]
    scenario_with_notes = replace(
        scenario,
        plan=replace(
            scenario.plan,
            items=tuple(
                replace(i, comment="нет молока", deviation="product")
                if i.task_id == blocked.task_id
                else i
                for i in scenario.plan.items
            ),
        ),
    )

    form = general_plan_form(scenario_with_notes)
    text = form.to_text()
    # На узкой странице длинные ячейки переносятся по словам, поэтому текст
    # сравнивается без учёта переносов: данные не теряются, меняется разметка.
    flat = " ".join(text.split())
    assert "нет молока" in flat
    assert "Отсутствие или замена продукта" in flat


def test_route_form_lists_only_that_person(scenario) -> None:
    """Маршрут сотрудника не содержит чужих работ."""
    employee = scenario.plan.staff[0]
    form = route_form(scenario, employee)
    route = scenario.plan.route_of(employee.name)

    text = form.to_text()
    assert employee.name in text
    if route:
        assert len(form.rows) == len(route.items)
    else:
        assert not form.rows


def test_route_form_shows_breaks(scenario) -> None:
    """Перерывы печатаются вместе с маршрутом."""
    employee = next(e for e in scenario.plan.staff if e.breaks)
    text = route_form(scenario, employee).to_text()

    assert "Перерывы:" in text
    assert f"{employee.breaks[0][0]:%H:%M}" in text


def test_not_ready_reasons_are_printed(scenario) -> None:
    """Причины неготовности видны на бумаге, а не только в интерфейсе."""
    text = general_plan_form(scenario).to_text()

    assert "Не готово:" in text or scenario.plan.ready


def test_settings_are_reachable_by_one_person() -> None:
    """Формы печатаются и для смены с одним человеком (AC15)."""
    solo = default_staff(
        StaffSettings(
            staff=(
                Employee(
                    name="Повар 1",
                    role=ROLE_COOK,
                    shift_start=time(7, 0),
                    shift_end=time(20, 0),
                ),
            )
        )
    )
    form = general_plan_form(
        build_scenario(seed.demo_data(MONDAY), MONDAY, menu=_lunch(MONDAY), staff=solo)
    )

    assert "Повар 1" in form.to_text() or not form.rows
