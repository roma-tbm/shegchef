"""Регрессионные тесты дефектов повторного ревью Stage 3 (V2-01 … V2-07).

Каждый тест описывает ожидаемое поведение и был написан до исправления,
чтобы дефект сначала воспроизвести. Теперь все они зелёные и служат
регрессией: повторное появление любого из этих дефектов валит тест.

    V2-01  DONE переносится на заменённое блюдо
    V2-02  пользовательский статус скрывает нарушение ограничения
    V2-03  воскресная подготовка не зависит от меню понедельника
    V2-05  расход сырья используется как выход полуфабриката
    V2-07  блюдо без операций получает готовый пустой план

Отдельно проверяется связанное с V2-01/V2-06: идентификатор обязанности не
меняется при вводе длительностей, иначе сохранённые комментарии и отметки
выполнения теряли бы свою работу.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

import pytest

from kitchen import seed
from kitchen.core.planner import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_TODO,
    plan_shift,
)
from kitchen.core.tasks import generate_tasks
from kitchen.core.timing import calculate_backward_timing
from kitchen.duties import DutySettings
from kitchen.models import Ingredient, MenuLine, Recipe, TaskTemplate
from kitchen.staff import Employee, StaffSettings, to_minutes
from kitchen.staff import ROLE_COOK
from kitchen.web.report_store import (
    IncompleteReportError,
    SaturdayReport,
    SemifinishedMarking,
    SemifinishedOutput,
    load_report,
    save_report,
)
from kitchen.web.shift_plan import build_scenario, saturday_report_form


@dataclass(frozen=True, slots=True)
class _ReportRow:
    """Читаемое представление строки субботнего отчёта."""

    product: str
    unit: str
    plan: str
    consumption: str
    consumption_unit: str
    output: str
    output_unit: str
    name: str
    made: str
    expires: str


def _report_row(form, product: str) -> _ReportRow:
    values = next(r for r in form.rows if r[0] == product)
    return _ReportRow(*values)

MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)
SUNDAY = date(2026, 10, 4)
FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)

COOK = Employee(
    name="Повар 1",
    role=ROLE_COOK,
    shift_start=time(7, 0),
    shift_end=time(20, 0),
    breaks=(),
    order=1,
)

ALL_DUTIES: tuple[str, ...] = ()
NO_DUTIES = DutySettings(disabled=ALL_DUTIES)


def _recipe(name: str, cook_min: int = 10) -> Recipe:
    return Recipe(
        name=name,
        meal="Обед",
        category="Обед",
        subcategory="Горячее",
        shop="Горячий цех",
        prep_min=5,
        cook_min=cook_min,
        ingredients=(Ingredient("Картофель", 200.0),),
    )


def _template(name: str, minutes: int) -> TaskTemplate:
    return TaskTemplate(
        recipe=name,
        order=1,
        stage="Приготовление",
        role=ROLE_COOK,
        operation=f"Cook {name}",
        duration_min=minutes,
        products="Картофель",
    )


def _plan(recipe_names: tuple[str, ...], day: date = MONDAY, **kwargs):
    """Изолированный расчёт: один повар без перерывов, каталог отключён."""
    recipes = {name: _recipe(name) for name in recipe_names}
    templates = tuple(_template(name, 10) for name in recipe_names)
    menu = tuple(
        MenuLine(name, 20, time(12, 0), "Обед", day) for name in recipe_names
    )
    board = generate_tasks(menu, recipes, templates)
    timing = calculate_backward_timing(day, menu, board)
    return plan_shift(
        day=day,
        menu=menu,
        timing=timing,
        board=board,
        staff=(COOK,),
        duty_settings=NO_DUTIES,
        **kwargs,
    )


def _item(plan, needle: str):
    return next(i for i in plan.items if needle in i.operation)


# ---------------------------------------------------------------------------
# V2-01 — DONE не переносится на другую работу
# ---------------------------------------------------------------------------


def test_v2_01_replacing_a_dish_does_not_carry_done_over() -> None:
    """Отметка «сделано» не должна переезжать на заменённое блюдо."""
    first = _plan(("Cook A",))
    done_id = _item(first, "Cook A").item_id

    second = _plan(("Cook B",), overrides=((done_id, STATUS_DONE),))

    assert _item(second, "Cook B").status != STATUS_DONE, (
        "у новой операции не должно быть статуса DONE"
    )
    assert second.ready is False or second.overrides == (), (
        "сохранённый статус не должен применяться к другой работе"
    )


def test_v2_01_item_id_is_not_a_position_in_the_list() -> None:
    """Идентификатор работы устойчив к перестановке и замене блюда."""
    first = _plan(("Cook A",))
    a_id = _item(first, "Cook A").item_id

    # Тот же ID у той же операции при другом составе меню.
    both = _plan(("Cook A", "Cook B"))
    assert _item(both, "Cook A").item_id == a_id


def test_v2_01_unchanged_recalculation_keeps_done() -> None:
    """Повторный расчёт неизменных данных сохраняет отметку."""
    first = _plan(("Cook A",))
    a_id = _item(first, "Cook A").item_id

    again = _plan(("Cook A",), overrides=((a_id, STATUS_DONE),))

    assert _item(again, "Cook A").status == STATUS_DONE


def test_v2_01_status_moves_with_the_same_operation_under_new_portions() -> None:
    """Политика объёма объявлена явно: та же операция и блюдо — отметка цела."""
    first = _plan(("Cook A",))
    a_id = _item(first, "Cook A").item_id

    # Порции другие, но блюдо и операция те же: отметка факта сохраняется,
    # потому что идентичность работы — пара «блюдо + операция + ТТК».
    again = _plan(("Cook A",), overrides=((a_id, STATUS_DONE),))

    assert _item(again, "Cook A").status == STATUS_DONE


def test_v2_01_operations_of_one_dish_keep_separate_identity() -> None:
    """Две операции одного блюда — две разные работы с разными ID."""
    recipes = {"Cook A": _recipe("Cook A")}
    templates = (
        TaskTemplate(
            recipe="Cook A", order=1, stage="Приготовление", role=ROLE_COOK,
            operation="Cook A", duration_min=10, products="Картофель",
        ),
        TaskTemplate(
            recipe="Cook A", order=2, stage="Контроль", role=ROLE_COOK,
            operation="Taste A", duration_min=5, products="Картофель",
        ),
    )
    menu = (MenuLine("Cook A", 20, time(12, 0), "Обед", MONDAY),)
    board = generate_tasks(menu, recipes, templates)
    timing = calculate_backward_timing(MONDAY, menu, board)

    plan = plan_shift(
        day=MONDAY, menu=menu, timing=timing, board=board,
        staff=(COOK,), duty_settings=NO_DUTIES,
    )

    ids = [i.item_id for i in plan.items]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# V2-02 — статус не отменяет нарушение ограничения
# ---------------------------------------------------------------------------


def test_v2_02_done_does_not_hide_a_friday_violation() -> None:
    """Пятничное производство после 14:00 остаётся нарушением при DONE."""
    recipe = {"Cook A": _recipe("Cook A", cook_min=60)}
    template = (
        TaskTemplate(
            recipe="Cook A", order=1, stage="Приготовление", role=ROLE_COOK,
            operation="Cook A", duration_min=60, products="Картофель",
        ),
    )
    menu = (MenuLine("Cook A", 20, time(15, 0), "Ужин", FRIDAY),)
    board = generate_tasks(menu, recipe, template)
    timing = calculate_backward_timing(FRIDAY, menu, board)

    first = plan_shift(
        day=FRIDAY, menu=menu, timing=timing, board=board,
        staff=(COOK,), duty_settings=NO_DUTIES,
    )
    blocked = _item(first, "Cook A")
    assert blocked.status == STATUS_BLOCKED
    assert blocked.violations

    marked = plan_shift(
        day=FRIDAY, menu=menu, timing=timing, board=board,
        staff=(COOK,), duty_settings=NO_DUTIES,
        overrides=((blocked.item_id, STATUS_DONE),),
    )

    marked_item = _item(marked, "Cook A")
    assert marked.violations_count >= len(blocked.violations), (
        "нарушение должно оставаться видимым после отметки «сделано»"
    )
    assert marked.ready is False, (
        "план с нарушенным ограничением не может быть готовым"
    )
    assert marked.not_ready_reasons, "причина нарушения должна оставаться в тексте"


def test_v2_02_skipped_does_not_clear_violations_either() -> None:
    """SKIPPED — тоже не способ сделать запрещённое расписание готовым."""
    recipe = {"Cook A": _recipe("Cook A", cook_min=60)}
    template = (
        TaskTemplate(
            recipe="Cook A", order=1, stage="Приготовление", role=ROLE_COOK,
            operation="Cook A", duration_min=60, products="Картофель",
        ),
    )
    menu = (MenuLine("Cook A", 20, time(15, 0), "Ужин", FRIDAY),)
    board = generate_tasks(menu, recipe, template)
    timing = calculate_backward_timing(FRIDAY, menu, board)

    marked = plan_shift(
        day=FRIDAY, menu=menu, timing=timing, board=board,
        staff=(COOK,), duty_settings=NO_DUTIES,
        overrides=((plan_shift(
            day=FRIDAY, menu=menu, timing=timing, board=board,
            staff=(COOK,), duty_settings=NO_DUTIES,
        ).items[0].item_id, "SKIPPED"),),
    )

    assert marked.ready is False


def test_v2_02_user_cannot_promote_violation_status_to_todo() -> None:
    """Пользователь не может снять блокировку, назначив TODO."""
    recipe = {"Cook A": _recipe("Cook A", cook_min=60)}
    template = (
        TaskTemplate(
            recipe="Cook A", order=1, stage="Приготовление", role=ROLE_COOK,
            operation="Cook A", duration_min=60, products="Картофель",
        ),
    )
    menu = (MenuLine("Cook A", 20, time(15, 0), "Ужин", FRIDAY),)
    board = generate_tasks(menu, recipe, template)
    timing = calculate_backward_timing(FRIDAY, menu, board)
    base = plan_shift(
        day=FRIDAY, menu=menu, timing=timing, board=board,
        staff=(COOK,), duty_settings=NO_DUTIES,
    )

    marked = plan_shift(
        day=FRIDAY, menu=menu, timing=timing, board=board,
        staff=(COOK,), duty_settings=NO_DUTIES,
        overrides=((base.items[0].item_id, STATUS_TODO),),
    )

    assert _item(marked, "Cook A").status == STATUS_BLOCKED, (
        "блокировка ограничения не снимается пользовательским статусом"
    )


def test_v2_02_printable_keeps_the_reason_after_marking() -> None:
    """Причина неготовности остаётся в печатной форме после отметки."""
    recipe = {"Cook A": _recipe("Cook A", cook_min=60)}
    template = (
        TaskTemplate(
            recipe="Cook A", order=1, stage="Приготовление", role=ROLE_COOK,
            operation="Cook A", duration_min=60, products="Картофель",
        ),
    )
    data = seed.demo_data(FRIDAY)
    data = _with_recipe(data, recipe, template)
    menu = (MenuLine("Cook A", 20, time(15, 0), "Ужин", FRIDAY),)

    scenario = build_scenario(
        _data_for(data, menu, FRIDAY),
        FRIDAY,
        menu=menu,
        overrides=(("З-001", STATUS_DONE),),
    )

    text = "\n".join(g.message for g in scenario.plan.gaps)
    assert scenario.plan.ready is False
    assert text or scenario.plan.not_ready_reasons


# ---------------------------------------------------------------------------
# V2-03 — подготовка следующего дня следует его меню
# ---------------------------------------------------------------------------


def _sunday_menu(recipes: dict[str, Recipe]) -> tuple[MenuLine, ...]:
    return (MenuLine("Cook A", 20, time(12, 0), "Обед", MONDAY),)


def test_v2_03_sunday_plan_depends_on_monday_menu() -> None:
    """Воскресный план строится из меню ближайшего понедельника."""
    recipes = {"Cook A": _recipe("Cook A"), "Cook B": _recipe("Cook B")}
    data = _prep_demo(SUNDAY, recipes)

    with_a = build_scenario(data, SUNDAY, menu=(), next_menu=_monday_menu(("Cook A",)))
    with_b = build_scenario(data, SUNDAY, menu=(), next_menu=_monday_menu(("Cook B",)))

    assert with_a.next_menu, "воскресный расчёт должен знать меню понедельника"
    assert _prep_operations(with_a.plan) != _prep_operations(with_b.plan), (
        "замена блюда понедельника обязана менять воскресный план"
    )


def test_v2_03_sunday_prep_lists_the_next_day_dish() -> None:
    """В воскресном плане видна операция блюда понедельника, а не общий список."""
    recipes = {"Cook A": _recipe("Cook A")}
    data = _prep_demo(SUNDAY, recipes)

    scenario = build_scenario(data, SUNDAY, menu=(), next_menu=_monday_menu(("Cook A",)))

    assert any("Cook A" in op for op in _prep_operations(scenario.plan))


def test_v2_03_sunday_plan_is_empty_without_monday_menu() -> None:
    """Нет меню понедельника — воскресный план не выдумывает работы."""
    data = _prep_demo(SUNDAY, {"Cook A": _recipe("Cook A")})

    scenario = build_scenario(data, SUNDAY, menu=())

    assert scenario.next_menu == tuple()
    assert scenario.plan.next_menu == tuple()
    assert any(
        g.code == "gap:no-next-menu" for g in scenario.plan.gaps
    ), "отсутствие меню понедельника должно быть видимым"


def test_v2_03_weekday_evening_prep_sees_next_day_menu() -> None:
    """Обычная вечерняя подготовка знает меню следующего дня."""
    recipes = {"Cook A": _recipe("Cook A"), "Cook B": _recipe("Cook B")}
    data = _prep_demo(MONDAY, recipes)

    with_a = build_scenario(data, MONDAY, menu=(), next_menu=_tuesday_menu(("Cook A",)))
    with_b = build_scenario(data, MONDAY, menu=(), next_menu=_tuesday_menu(("Cook B",)))

    assert with_a.next_menu != with_b.next_menu
    assert _prep_operations(with_a.plan) != _prep_operations(with_b.plan), (
        "подготовка вечера обязана зависеть от меню завтрашнего дня"
    )


def test_v2_03_prep_uses_the_next_day_portions() -> None:
    """Порции подготовки берутся из меню следующего дня, а не из текущего."""
    data = _prep_demo(MONDAY, {"Cook A": _recipe("Cook A")})

    scenario = build_scenario(
        data, MONDAY, menu=(), next_menu=_tuesday_menu(("Cook A",), portions=37)
    )

    prep = [i for i in scenario.plan.items if "Cook A" in i.operation and i.portions]
    assert prep, "подготовка блюда следующего дня должна быть в плане"
    assert {i.portions for i in prep} == {37}


# ---------------------------------------------------------------------------
# V2-05 — расход и выход полуфабриката — разные вещи
# ---------------------------------------------------------------------------


def test_v2_05_consumption_does_not_become_semifinished_output() -> None:
    """Расход 5 кг картофеля не равен выходу полуфабриката."""
    scenario = build_scenario(seed.demo_data(SATURDAY), SATURDAY)
    form = saturday_report_form(scenario, actuals={"Картофель": 5})

    row = _report_row(form, "Картофель")
    assert row.consumption == "5", "расход вводится и печатается"
    assert row.output != "5", "выход не должен копировать расход автоматически"
    assert row.output == "—", "выход без ввода остаётся пустым"


def test_v2_05_output_is_entered_separately_with_its_unit() -> None:
    """Выход полуфабриката вводится отдельно и со своей единицей."""
    scenario = build_scenario(seed.demo_data(SATURDAY), SATURDAY)
    form = saturday_report_form(
        scenario,
        actuals={"Картофель": 5},
        outputs={"Картофель": SemifinishedOutput(qty=12, unit="кг")},
    )

    row = _report_row(form, "Картофель")
    assert row.consumption == "5", "расход сырья"
    assert row.output == "12", "выход полуфабриката введён отдельно"
    assert row.output_unit == "кг", "единица выхода своя"
    assert row.consumption_unit == "кг"


def test_v2_05_output_without_confirmed_norm_is_not_calculated() -> None:
    """Без подтверждённой нормы выход не вычисляется, а остаётся пустым."""
    scenario = build_scenario(seed.demo_data(SATURDAY), SATURDAY)
    form = saturday_report_form(scenario, actuals={"Картофель": 5})

    row = _report_row(form, "Картофель")
    assert row.output == "—", "выход без ввода обязан быть пустым"
    assert any("нормы выхода" in note for note in form.notes), (
        "отсутствие нормы выхода должно быть сказано словами"
    )


def test_v2_05_output_requires_a_unit() -> None:
    """Выход без единицы измерения — некорректные данные, а не тихий ноль."""
    with pytest.raises(ValueError, match="единиц"):
        SemifinishedOutput(qty=12, unit="  ")


def test_v2_05_marking_requires_name_date_and_shelf_life() -> None:
    """Маркировка валидируется: название, дата производства, срок годности."""
    scenario = build_scenario(seed.demo_data(SATURDAY), SATURDAY)
    outputs = {"Картофель": SemifinishedOutput(qty=12, unit="кг")}

    with pytest.raises(ValueError, match="маркировка"):
        saturday_report_form(
            scenario,
            outputs=outputs,
            markings={
                "Картофель": SemifinishedMarking(
                    name="Картофель жареный",
                    made=SATURDAY,
                    expires=None,  # type: ignore[arg-type]
                )
            },
        )

    form = saturday_report_form(
        scenario,
        outputs=outputs,
        markings={
            "Картофель": SemifinishedMarking(
                name="Картофель жареный",
                made=SATURDAY,
                expires=date(2026, 10, 13),
            )
        },
    )
    row = _report_row(form, "Картофель")
    assert row.name == "Картофель жареный"
    assert row.made == "10.10.2026"
    assert row.expires == "13.10.2026"


def test_v2_05_report_is_incomplete_without_matching_marking() -> None:
    """Есть выход, но нет маркировки — отчёт не считается заполненным."""
    report = SaturdayReport(
        day=SATURDAY,
        outputs={"Картофель": SemifinishedOutput(qty=12, unit="кг")},
    )
    assert report.is_complete() is False
    assert report.incomplete_markings == ("Картофель",)


def test_v2_05_report_roundtrips_through_storage(tmp_path) -> None:
    """Введённый отчёт сохраняется и открывается повторно (AC09)."""
    path = tmp_path / "reports.json"
    report = SaturdayReport(
        day=SATURDAY,
        consumption={"Картофель": 5.0},
        outputs={"Картофель": SemifinishedOutput(qty=12.0, unit="кг")},
        markings={
            "Картофель": SemifinishedMarking(
                name="Картофель жареный",
                made=SATURDAY,
                expires=date(2026, 10, 13),
            )
        },
    )
    save_report(SATURDAY, report, path)

    loaded = load_report(SATURDAY, path)
    assert loaded is not None
    assert loaded.consumption["Картофель"] == 5.0
    assert loaded.outputs["Картофель"].qty == 12.0
    assert loaded.outputs["Картофель"].unit == "кг"
    assert loaded.markings["Картофель"].name == "Картофель жареный"
    assert loaded.markings["Картофель"].expires == date(2026, 10, 13)
    assert loaded.day == SATURDAY


def test_v2_05_missing_day_loads_as_nothing(tmp_path) -> None:
    """Дня без отчёта нет — это не ошибка, а пустое состояние."""
    path = tmp_path / "reports.json"
    save_report(
        SATURDAY,
        SaturdayReport(
            day=SATURDAY,
            outputs={"Картофель": SemifinishedOutput(qty=1, unit="кг")},
            markings={
                "Картофель": SemifinishedMarking(
                    name="Картофель жареный",
                    made=SATURDAY,
                    expires=date(2026, 10, 13),
                )
            },
        ),
        path,
    )
    assert load_report(date(2026, 10, 3), path) is None


def test_v2_05_incomplete_report_is_not_saved_as_done(tmp_path) -> None:
    """Неполный отчёт не сохраняется как будто он заполнен.

    С V3-05 неполный отчёт ещё и объясняет, чего не хватает, вместо молчаливого
    удаления записи: иначе человек теряет факт и не узнаёт об этом.
    """
    path = tmp_path / "reports.json"
    with pytest.raises(IncompleteReportError, match="Картофель"):
        save_report(
            SATURDAY,
            SaturdayReport(
                day=SATURDAY,
                outputs={"Картофель": SemifinishedOutput(qty=12, unit="кг")},
            ),
            path,
        )
    assert load_report(SATURDAY, path) is None


def test_v2_05_saved_report_does_not_touch_stock(tmp_path) -> None:
    """Отчёт фиксирует факт и не списывает склад (AC09)."""
    from kitchen.web.report_store import load_report, save_report

    data = seed.demo_data(SATURDAY)
    before = {name: p.stock for name, p in data.products.items()}
    path = tmp_path / "reports.json"
    save_report(
        SATURDAY,
        SaturdayReport(
            day=SATURDAY,
            consumption={"Картофель": 5.0},
            outputs={"Картофель": SemifinishedOutput(qty=12, unit="кг")},
            markings={
                "Картофель": SemifinishedMarking(
                    name="Картофель жареный",
                    made=SATURDAY,
                    expires=date(2026, 10, 13),
                )
            },
        ),
        path,
    )

    assert load_report(SATURDAY, path) is not None
    assert {name: p.stock for name, p in data.products.items()} == before, (
        "сохранение отчёта не должно менять складские остатки"
    )


# ---------------------------------------------------------------------------
# V2-07 — блюдо без операций не даёт готовый план
# ---------------------------------------------------------------------------


def test_v2_07_dish_without_operations_is_a_blocking_gap() -> None:
    """Блюдо без операций ТТК — блокирующая неполнота данных, а не готовность."""
    recipes = {"No operations": _recipe("No operations")}
    menu = (MenuLine("No operations", 20, time(12, 0), "Обед", MONDAY),)
    board = generate_tasks(menu, recipes, ())
    timing = calculate_backward_timing(MONDAY, menu, board)

    plan = plan_shift(
        day=MONDAY, menu=menu, timing=timing, board=board,
        staff=(COOK,), duty_settings=NO_DUTIES,
    )

    assert plan.ready is False
    assert plan.gaps, "отсутствие операций должно быть названо"
    assert any("No operations" in g.subject for g in plan.gaps)


def test_v2_07_menu_with_operations_has_no_such_gap() -> None:
    """Блюдо с операциями такой причины не создаёт — тест не проходит зря."""
    plan = _plan(("Cook A",))

    assert not any("Cook A" in g.subject and "операц" in g.reason for g in plan.gaps)


# ---------------------------------------------------------------------------
# Общие хелперы
# ---------------------------------------------------------------------------


def _monday_menu(names: tuple[str, ...], portions: int = 20) -> tuple[MenuLine, ...]:
    return tuple(
        MenuLine(name, portions, time(12, 0), "Обед", MONDAY) for name in names
    )


def _tuesday_menu(names: tuple[str, ...], portions: int = 20) -> tuple[MenuLine, ...]:
    return tuple(
        MenuLine(name, portions, time(12, 0), "Обед", TUESDAY) for name in names
    )


def _prep_demo(day: date, recipes: dict[str, Recipe]):
    """Данные, где у блюд есть собственные операции ТТК — иначе нечего готовить."""
    from dataclasses import replace

    templates = tuple(
        TaskTemplate(
            recipe=name, order=1, stage="Заготовка", role=ROLE_COOK,
            operation=f"Prep {name}", duration_min=15, products="Картофель",
        )
        for name in recipes
    )
    data = seed.demo_data(day)
    return replace(
        data,
        recipes={**recipes},
        tasks=templates,
        menu=(),
    )


def _prep_operations(plan) -> tuple[str, ...]:
    return tuple(
        i.operation for i in plan.items if "Prep " in i.operation
    )


def _with_recipes(data, recipes: dict[str, Recipe]):
    from dataclasses import replace

    return replace(data, recipes={**data.recipes, **recipes})


def _with_recipe(data, recipe: dict[str, Recipe], template: TaskTemplate):
    from dataclasses import replace

    return replace(data, recipes={**data.recipes, **recipe})


def _data_for(data, menu, day):
    from dataclasses import replace

    return replace(data, menu=tuple(menu))


ALL_DUTIES: tuple[str, ...] = ()

# ---------------------------------------------------------------------------
# Идентичность обязанности: ввод настроек не переименовывает работу
# ---------------------------------------------------------------------------

#: Обязанность каталога с формулировкой, уникальной среди всех обязанностей.
DUTY = "stock-up"
DUTY_ITEM = f"П-{DUTY}"


def _ids(**kwargs) -> set[str]:
    return {
        i.item_id
        for i in build_scenario(seed.demo_data(MONDAY), MONDAY, **kwargs).plan.items
    }


def test_duty_id_survives_a_duration_being_filled_in() -> None:
    """Ввод длительности не переименовывает работу (AC12, AC13).

    Идентификатор — это идентичность работы, а не её состояние. Если он
    меняется вместе с заполненностью настроек, сохранённый комментарий или
    отметка выполнения молча перестают относиться к этой работе: человек
    вводит длительность и видит, что его запись исчезла.
    """
    before = _ids()
    after = _ids(duty_settings=DutySettings(duration_overrides={DUTY: 20}))

    assert DUTY_ITEM in before
    assert DUTY_ITEM in after, "работа обязана сохранить идентификатор"


def test_comment_stays_on_its_duty_after_entering_a_duration() -> None:
    """Комментарий остаётся на своей работе после ввода длительности."""
    scenario = build_scenario(
        seed.demo_data(MONDAY),
        MONDAY,
        duty_settings=DutySettings(duration_overrides={DUTY: 20}),
        notes=((DUTY_ITEM, "пояснение шефа"),),
    )

    item = next(i for i in scenario.plan.items if i.item_id == DUTY_ITEM)
    assert item.comment == "пояснение шефа"


def test_duty_id_does_not_depend_on_the_duty_being_scheduled() -> None:
    """Один ключ каталога — один ID и с окном, и без него."""
    with_window = _ids(
        duty_settings=DutySettings(
            window_overrides={DUTY: (time(13, 0), time(14, 0))}
        )
    )

    assert DUTY_ITEM in with_window
    assert DUTY_ITEM in _ids()
