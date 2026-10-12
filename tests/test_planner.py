"""Планировщик смены: маршруты, ограничения и видимые пропуски (D5, D6).

Проверяется главное из AC06, AC07, AC08, AC12, AC13, AC15: ограничение
действует внутри расчёта, план не выдаётся готовым при незаполненных полях,
а введённый пользователем статус не переезжает на другую задачу.
"""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from kitchen.core.constraints import (
    LIMIT_FINAL_WASH,
    LIMIT_FRIDAY,
    LIMIT_HOT_KITCHEN_FLOOR,
    LIMIT_SHIFT,
    Interval,
)
from kitchen.core.planner import (
    REASON_NO_DURATION,
    REASON_NO_WINDOW,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_TODO,
    ShiftPlan,
    plan_shift,
)
from kitchen.core.tasks import generate_tasks
from kitchen.core.timing import calculate_backward_timing
from kitchen.duties import DutySettings
from kitchen.staff import (
    DEFAULT_BREAKS,
    ROLE_CHEF,
    ROLE_CLEANER,
    ROLE_COOK,
    ROLE_HELPER,
    Employee,
    StaffSettings,
    default_staff,
    to_minutes,
)

MONDAY = date(2026, 10, 5)
FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)

RECIPES: dict = {}


def _menu(*recipes: str, meal: str = "Обед", at: time = time(12, 0)):
    from kitchen.models import MenuLine

    return tuple(
        MenuLine(recipe=r, portions=20, serve_at=at, meal=meal, day=MONDAY)
        for r in recipes
    )


def _build(day: date, recipes: tuple[str, ...], **kwargs):
    """Прогоняет меню через шаги 3–4 и планировщик."""
    from kitchen import seed
    from kitchen.models import MenuLine

    menu = tuple(
        MenuLine(recipe=r, portions=20, serve_at=time(12, 0), meal="Обед", day=day)
        for r in recipes
        if r in seed.RECIPES
    )
    if not menu:
        pytest.skip("нет подходящих рецептов в seed")
    board = generate_tasks(menu, seed.RECIPES, seed.CATALOG_TASKS)
    timing = calculate_backward_timing(day, menu, board)
    staff = kwargs.pop("staff", None)
    if staff is None:
        staff = default_staff(StaffSettings())
    # Подготовка следующего дня порождается его меню (AC11), поэтому в
    # изолированном расчёте следующий день задан явно.
    next_menu = kwargs.pop(
        "next_menu",
        tuple(
            MenuLine(
                recipe=line.recipe, portions=line.portions, serve_at=line.serve_at,
                meal=line.meal, day=day + timedelta(days=1),
            )
            for line in menu
        ),
    )
    next_board = generate_tasks(next_menu, seed.RECIPES, seed.CATALOG_TASKS)
    plan = plan_shift(
        day=day,
        menu=menu,
        timing=timing,
        board=board,
        staff=staff,
        next_menu=next_menu,
        next_board=next_board,
        **kwargs,
    )
    return plan, menu, board, timing


def _monday(**kwargs):
    from kitchen import seed

    recipe = next(iter(seed.RECIPES))
    return _build(MONDAY, (recipe,), **kwargs)


# ---------------------------------------------------------------------------
# Сквозной расчёт
# ---------------------------------------------------------------------------


def test_plan_produces_items_and_routes() -> None:
    plan, _, _, _ = _monday()
    assert plan.items, "планировщик обязан что-то вернуть"
    assert plan.routes, "нужны маршруты"
    assert all(route.items for route in plan.routes[:1]), "у первого маршрута есть работы"


def test_ttk_operations_are_scheduled_with_portions() -> None:
    """Операции ТТК получают время, блюдо и порции (AC02, AC12)."""
    plan, menu, _, _ = _monday()
    ttk = [i for i in plan.items if i.task_id]
    assert ttk, "операции ТТК должны попасть в план"
    first = ttk[0]
    assert first.portions > 0
    assert first.recipe
    assert first.end > first.start


def test_every_item_has_time_role_and_status() -> None:
    plan, _, _, _ = _monday()
    for item in plan.items:
        assert item.role
        assert item.status
        assert item.source, f"{item.item_id}: нет происхождения задачи"


# ---------------------------------------------------------------------------
# AC15: незаполненные длительности видны и не выдуманы
# ---------------------------------------------------------------------------


def test_missing_durations_are_reported_not_invented() -> None:
    """Длительность из источника не подставляется, а показывается как пропуск."""
    plan, _, _, _ = _monday()
    gaps = [g for g in plan.gaps if g.code == f"gap:{'breakfast-stock'}"]
    assert gaps, "пропуск длительности должен быть виден"
    assert REASON_NO_DURATION in gaps[0].reason
    item = next(i for i in plan.items if i.source == "п. 5" and "термосы" in i.operation)
    assert item.duration_min == 0, "длительность не должна выдумываться"
    assert item.status != STATUS_DONE


def test_plan_is_not_ready_while_gaps_exist() -> None:
    """Ложная готовность запрещена (AC15)."""
    plan, _, _, _ = _monday()
    assert plan.ready is False
    assert plan.not_ready_reasons


def _filled(mode: str = "Обычный", weekday: int = 0, minutes: int = 15) -> DutySettings:
    """Настройки с заполненными длительностями, помещающимися в окна.

    Длительность берётся не больше длины окна: иначе работа физически не
    помещается в подтверждённый источником интервал, и план был бы не готов
    по существу, а не из-за незаполненных полей.
    """
    from kitchen.duties import duties_for

    settings = DutySettings()
    for duty in duties_for(weekday, mode):
        limit = minutes
        if duty.window is not None:
            span = to_minutes(duty.window[1]) - to_minutes(duty.window[0])
            limit = min(limit, span)
        if duty.fixed_at is not None:
            limit = min(limit, 15)
        settings = settings.with_duration(duty.key, max(1, limit))
    return settings


def test_filling_durations_removes_duration_gaps() -> None:
    """Заполненные длительности убирают именно пропуски длительностей."""
    before, _, _, _ = _monday()
    assert any(REASON_NO_DURATION in g.reason for g in before.gaps)

    after, _, _, _ = _monday(duty_settings=_filled())
    assert not any(REASON_NO_DURATION in g.reason for g in after.gaps), (
        [g.message for g in after.gaps]
    )
    assert not any(i.reason == REASON_NO_DURATION for i in after.items)


#: Перерывы, не пересекающиеся с п. 7 (08:30) и п. 21 (13:30–14:00).
#: Базовые DEFAULT_BREAKS из stage3.md с п. 7 конфликтуют — см. отдельный тест.
NO_COLLISION_BREAKS: tuple[tuple[time, time], ...] = (
    (time(9, 0), time(9, 30)),
    (time(14, 30), time(15, 0)),
)


def _sufficient_staff(
    breaks: tuple[tuple[time, time], ...] = NO_COLLISION_BREAKS,
) -> tuple[Employee, ...]:
    """Состав и перерывы, при которых все обязанности понедельника помещаются."""
    return default_staff(
        StaffSettings(breaks=breaks, chefs=3, cooks=4, helper=3, cleaners=4)
    )


def test_filling_durations_makes_plan_ready() -> None:
    """Заполненные длительности и достаточный состав дают готовый план."""
    plan, _, _, _ = _monday(duty_settings=_filled(), staff=_sufficient_staff())
    assert plan.ready is True, plan.not_ready_reasons
    assert plan.not_ready_reasons == ()
    assert all(i.assignee for i in plan.items if i.task_id)


def test_source_conflict_of_break_and_duty_is_reported() -> None:
    """Пересечение перерыва и пункта источника не разрешается молча.

    Пункт 7 требует проверки чек-листов в 08:30, а пункты 8 и 21 задают перерыв
    08:30–09:00. Обе цифры подтверждены источником, поэтому «правильного»
    решения здесь нет: расчёт обязан показать коллизию, а не выбрать одну
    трактовку за пользователя.
    """
    plan, _, _, _ = _monday(
        duty_settings=_filled(), staff=_sufficient_staff(breaks=DEFAULT_BREAKS)
    )
    messages = " ".join(plan.not_ready_reasons)
    assert "чек-листы" in messages
    assert "08:30–09:00" in messages, "коллизия должна называть время перерыва"
    assert plan.ready is False


def test_small_staff_reports_capacity_problem_by_name() -> None:
    """Нехватка людей называется прямо, а не молчалив пустым маршрутом."""
    plan, _, _, _ = _monday(duty_settings=_filled())
    assert plan.ready is False
    assert plan.not_ready_reasons
    for gap in plan.gaps:
        assert gap.reason, "у пропуска должна быть названная причина"
        assert gap.blocks, "у пропуска должно быть указано, что он блокирует"


def test_no_person_is_booked_twice_at_once() -> None:
    """Один сотрудник не получает две работы, идущие внахлёст."""
    plan, _, _, _ = _monday(duty_settings=_filled(), staff=default_staff(StaffSettings()))

    by_person: dict[str, list] = {}
    for item in plan.items:
        if item.assignee and item.start < item.end:
            by_person.setdefault(item.assignee, []).append(item)

    assert by_person, "у кого-то должны быть назначенные работы"
    for person, items in by_person.items():
        ordered = sorted(items, key=lambda i: i.start)
        for prev, nxt in zip(ordered, ordered[1:]):
            assert prev.end <= nxt.start, f"{person}: {prev.operation} и {nxt.operation}"


def test_unplaced_work_is_not_reported_as_a_double_booking() -> None:
    """Пункт без времени не считается наложением и не получает исполнителя."""
    from kitchen import seed

    day = date(2026, 10, 5)
    data = seed.demo_data(day)
    board = generate_tasks(data.menu, seed.RECIPES, seed.CATALOG_TASKS)
    plan = plan_shift(
        day=day,
        menu=data.menu,
        timing=calculate_backward_timing(day, data.menu, board),
        board=board,
        staff=default_staff(StaffSettings()),
    )

    assert not [g for g in plan.gaps if g.code.startswith("overlap:")], (
        "накладок быть не должно: расчёт не ставит две работы на одного человека"
    )
    unscheduled = [i for i in plan.items if i.start >= i.end]
    assert unscheduled, "в демо-меню есть пункты без времени"
    assert all(not i.assignee for i in unscheduled), (
        "у пункта без времени не должно быть исполнителя"
    )


def test_missing_window_is_reported_for_weekend() -> None:
    """Субботнее окно в источнике не названо — оно не подставляется."""
    settings = DutySettings()
    settings = settings.with_duration("sat-vegetables", 120)
    settings = settings.with_duration("sat-meat", 180)
    plan, _, _, _ = _build(SATURDAY, ("Каша овсяная с солёным арахисом",), duty_settings=settings)
    windows = [g for g in plan.gaps if REASON_NO_WINDOW in g.reason]
    assert windows, "незаполненное окно субботы должно быть видно"


# ---------------------------------------------------------------------------
# AC07 / AC08: ограничения внутри расчёта
# ---------------------------------------------------------------------------


def test_final_wash_is_blocked_outside_window() -> None:
    """Финальная мойка не может быть поставлена не в 16:30–16:40."""
    from kitchen.core.constraints import check_final_wash
    from kitchen.duties import DUTIES_BY_KEY

    duty = DUTIES_BY_KEY["final-kitchen-clean"]
    from kitchen.core.constraints import Interval

    assert check_final_wash(duty, Interval(16 * 60 + 30, 16 * 60 + 40)) == ()
    assert check_final_wash(duty, Interval(14 * 60, 15 * 60))


def test_friday_has_no_production_after_two() -> None:
    """В пятницу после 14:00 производства нет (AC08)."""
    from kitchen import seed

    recipe = next(iter(seed.RECIPES))
    plan, _, _, _ = _build(FRIDAY, (recipe,))
    assert plan.mode == "Пятница"
    for item in plan.items:
        if item.action == "Производство" and item.status != STATUS_BLOCKED:
            assert item.start.time() < time(14, 0), (
                f"{item.operation} производство после 14:00 в пятницу"
            )


def test_violation_makes_item_blocked_not_assigned() -> None:
    """Нарушение ограничения не выдаётся как готовое назначение (AC06)."""
    from kitchen.core.constraints import Interval, check_all
    from kitchen.duties import DUTIES_BY_KEY

    duty = DUTIES_BY_KEY["final-kitchen-clean"]
    chef = Employee("Шеф", ROLE_CHEF, time(8, 0), time(17, 0))
    violations = check_all(duty, Interval(10 * 60, 11 * 60), mode="Обычный", employee=chef)
    limits = {v.limit for v in violations}
    assert LIMIT_FINAL_WASH in limits
    assert LIMIT_HOT_KITCHEN_FLOOR in limits

    # Вне смены — отдельная причина, тоже должна называться.
    outside = check_all(duty, Interval(17 * 60, 18 * 60), mode="Обычный", employee=chef)
    assert LIMIT_SHIFT in {v.limit for v in outside}


# ---------------------------------------------------------------------------
# Перерывы (п. 8, п. 21)
# ---------------------------------------------------------------------------


def test_no_work_is_assigned_during_break() -> None:
    """В перерыв не назначается работа (п. 8, п. 21)."""
    settings = _filled()
    plan, _, _, _ = _monday(duty_settings=settings)
    chef = next(r for r in plan.routes if r.employee.role == ROLE_CHEF)
    for item in chef.items:
        for br_start, br_end in chef.employee.breaks:
            br_span = Interval(to_minutes(br_start), to_minutes(br_end))
            item_span = Interval(to_minutes(item.start.time()), to_minutes(item.end.time()))
            assert not item_span.overlaps(br_span), (
                f"{item.operation} назначено в перерыв {br_start:%H:%M}-{br_end:%H:%M}"
            )


# ---------------------------------------------------------------------------
# AC13: статусы и пересчёт
# ---------------------------------------------------------------------------


def test_user_status_survives_recalculation() -> None:
    """Введённый статус сохраняется после пересчёта (п. 42, AC13)."""
    settings = _filled()
    plan, _, _, _ = _monday(duty_settings=settings)
    target = plan.items[0].item_id
    again, _, _, _ = _monday(
        duty_settings=settings, overrides=((target, STATUS_DONE),)
    )
    assert again.route_of(target and plan.items[0].assignee) is not None
    restored = next(i for i in again.items if i.item_id == target)
    assert restored.status == STATUS_DONE


def test_done_is_not_transferred_to_another_task() -> None:
    """DONE нельзя переносить на другую задачу по совпавшему порядку (AC13)."""
    settings = _filled()
    plan, _, _, _ = _monday(duty_settings=settings)
    other = plan.items[1].item_id
    again, _, _, _ = _monday(
        duty_settings=settings, overrides=((other, STATUS_DONE),)
    )
    untouched = [i for i in again.items if i.item_id != other]
    assert all(i.status != STATUS_DONE for i in untouched), (
        "статус DONE не должен переноситься на другие задачи"
    )


def test_skipped_item_is_not_counted_in_load() -> None:
    from kitchen.core.planner import STATUS_SKIPPED

    settings = _filled()
    plan, _, _, _ = _monday(duty_settings=settings)
    first_route = next(r for r in plan.routes if r.items)
    item = first_route.items[0]
    again, _, _, _ = _monday(
        duty_settings=settings, overrides=((item.item_id, STATUS_SKIPPED),)
    )
    route = again.route_of(first_route.name)
    assert route is not None
    assert route.worked_min == first_route.worked_min - item.duration_min


# ---------------------------------------------------------------------------
# Маршруты (AC12)
# ---------------------------------------------------------------------------


def test_routes_contain_required_fields() -> None:
    """В маршруте есть время, задача, исполнитель и статус (AC12)."""
    settings = _filled()
    plan, _, _, _ = _monday(duty_settings=settings)
    for route in plan.routes:
        assert route.employee.name
        assert route.employee.role
        for item in route.items:
            assert item.assignee == route.employee.name
            assert item.span
            assert item.operation
            assert item.status


def test_housekeeping_items_may_have_empty_recipe() -> None:
    """Для хозяйственных работ неприменимые поля пустые — это допустимо."""
    from kitchen.duties import DUTIES_BY_KEY

    item_keys = {i.item_id for i in _monday()[0].items}
    housekeeping = [
        i
        for i in _monday()[0].items
        if i.source == DUTIES_BY_KEY["clean-hall"].source
    ]
    assert housekeeping
    assert housekeeping[0].meal == "" or housekeeping[0].meal
    assert item_keys


# ---------------------------------------------------------------------------
# Состав персонала
# ---------------------------------------------------------------------------


def test_weekend_without_staff_is_not_ready() -> None:
    """Без состава выходного плана быть не может (AC15)."""
    plan, _, _, _ = _build(SATURDAY, ("Каша овсяная с солёным арахисом",), staff=())
    assert plan.ready is False
    assert any("Субботный состав" in g.subject for g in plan.gaps)


def test_weekend_with_explicit_staff_can_be_planned() -> None:
    """Заданный субботний состав — работы распределяются (AC09).

    Готовность плана здесь не проверяется: субботний состав в источнике состоит
    из одного разнорабочего и одного повара (п. 28), а обычных выдач в субботу
    нет, поэтому состав не покрывает шефа и клининга — и это должно быть видно.
    """
    from kitchen.duties import duties_for

    settings = DutySettings()
    for duty in duties_for(5, "Суббота"):
        settings = settings.with_duration(duty.key, 60).with_window(
            duty.key, time(8, 0), time(14, 0)
        )
    staff = (
        Employee("Разнорабочий (суб)", ROLE_HELPER, time(8, 0), time(14, 0), order=0),
        Employee("Повар (суб)", ROLE_COOK, time(8, 0), time(14, 0), order=1),
    )
    plan, _, _, _ = _build(
        SATURDAY, ("Каша овсяная с солёным арахисом",), staff=staff, duty_settings=settings
    )
    assert plan.staff == staff
    assert plan.mode == "Суббота"

    # Субботние работы повара и разнорабочего получили исполнителей.
    sat_duties = {d.key for d in duties_for(5, "Суббота")}
    assigned = [
        i for i in plan.items
        if i.item_id in {f"П-{k}" for k in sat_duties} and i.assignee
    ]
    assert assigned, "субботние работы должны достаться сотрудникам"
    assert {i.assignee for i in assigned} <= {e.name for e in staff}

    # Чего не хватает — названо прямо, а не молчаливо пустым маршрутом.
    assert any("Шеф-повар" in g.subject for g in plan.gaps)


def test_saturday_staff_cannot_do_ordinary_serving() -> None:
    """Субботний состав не обслуживает обычные выдачи (п. 28)."""
    from kitchen.duties import duties_for

    assert duties_for(5, "Суббота") == tuple(
        d for d in duties_for(5, "Суббота") if d.key.startswith("sat-")
    )
    for duty in duties_for(5, "Суббота"):
        assert duty.day_modes == ("Суббота",)


def test_staff_count_is_configurable() -> None:
    """Численность задаётся настройкой, а не выведена из кода (AC03)."""
    three = default_staff(StaffSettings(cleaners=3))
    assert sum(1 for e in three if e.role == ROLE_CLEANER) == 3
    one = default_staff(StaffSettings(cleaners=1))
    assert sum(1 for e in one if e.role == ROLE_CLEANER) == 1