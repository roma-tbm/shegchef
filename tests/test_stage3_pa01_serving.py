"""PA-01: обязательное совместное участие в выдаче (stage3.md п. 6 и 16).

Пункты 6 и 16 требуют, чтобы в выдаче завтрака и обеда участвовали шеф-повар,
один повар и разнорабочий. Раньше работа выдачи создавалась один раз, а роли
перечислялись как альтернативы одного исполнителя, поэтому повар и
разнорабочий в выдаче отсутствовали.

Длительности здесь синтетические (как и в независимой сверке): сам stage3.md
их не задаёт. Проверяются состав участников, маршруты, ограничения смены и
перерывов, причины неполного состава, устойчивость идентификаторов и
изоляция отметок между участниками.
"""

from __future__ import annotations

from datetime import date, timedelta

from kitchen import seed
from kitchen.core.constraints import Interval
from kitchen.core.planner import STATUS_DONE
from kitchen.duties import DUTIES_BY_KEY, DutySettings, duties_for
from kitchen.staff import StaffSettings, to_minutes
from kitchen.web.shift_plan import build_scenario

MONDAY = date(2026, 10, 5)
SERVING_KEYS = ("breakfast-serving", "lunch-serving")
REQUIRED_ROLES = ("Шеф-повар", "Повар", "Разнорабочий")


def _filled_settings(weekday: int = 0, mode: str = "Обычный") -> DutySettings:
    """Синтетические длительности: 5 минут, финальная мойка — 10."""
    cfg = DutySettings()
    for duty in duties_for(weekday, mode):
        cfg = cfg.with_duration(
            duty.key, 10 if duty.key == "final-kitchen-clean" else 5
        )
    return cfg


def _staff(**overrides) -> StaffSettings:
    base = dict(chefs=3, cooks=4, helper=3, cleaners=4, tasting_executor="Шеф-повар 2")
    base.update(overrides)
    return StaffSettings(**base)


def _scenario(staff: StaffSettings | None = None, overrides=()):
    day = MONDAY
    data = seed.demo_data(day)
    return build_scenario(
        data,
        day,
        duty_settings=_filled_settings(),
        staff_settings=staff or _staff(),
        next_menu=seed.demo_data(day + timedelta(days=1)).menu,
        overrides=overrides,
    )


def _serving_items(plan, key: str):
    return tuple(i for i in plan.items if i.item_id.startswith(f"П-{key}"))


def test_serving_has_chef_cook_and_helper_participants() -> None:
    """В выдаче завтрака и обеда видны шеф, повар и разнорабочий."""
    plan = _scenario().plan
    for key in SERVING_KEYS:
        items = _serving_items(plan, key)
        assigned_roles = {i.role for i in items if i.assignee}
        assert set(REQUIRED_ROLES) <= assigned_roles, (key, items)


def test_serving_participants_are_in_their_own_routes() -> None:
    """Каждый участник выдачи попадает в свой персональный маршрут (AC12)."""
    plan = _scenario().plan
    for key in SERVING_KEYS:
        for item in _serving_items(plan, key):
            if not item.assignee:
                continue
            route = plan.route_of(item.assignee)
            assert route is not None
            assert any(x.item_id == item.item_id for x in route.items), (key, item)


def test_serving_respects_shift_and_breaks() -> None:
    """Участник выдачи не занят вне смены и не в свой перерыв (AC03)."""
    plan = _scenario().plan
    for key in SERVING_KEYS:
        for item in _serving_items(plan, key):
            if not item.assignee:
                continue
            employee = next(e for e in plan.staff if e.name == item.assignee)
            assert employee.fits(item.start.time(), item.end.time()), item
            span = Interval(to_minutes(item.start.time()), to_minutes(item.end.time()))
            for br_start, br_end in employee.breaks:
                assert not span.overlaps(
                    Interval(to_minutes(br_start), to_minutes(br_end))
                ), item


def test_missing_serving_role_is_named() -> None:
    """Если роль выдачи отсутствует, причина названа конкретно (AC15)."""
    plan = _scenario(_staff(cooks=0)).plan
    cook_items = [
        i for i in _serving_items(plan, "lunch-serving") if i.role == "Повар"
    ]
    assert cook_items, "работа повара на выдаче должна быть представлена"
    assert not any(i.assignee for i in cook_items)
    assert "Повар" in " ".join(plan.not_ready_reasons)


def test_serving_status_is_isolated_between_participants() -> None:
    """DONE одного участника не переезжает на другого (AC13)."""
    base = _scenario()
    items = _serving_items(base.plan, "breakfast-serving")
    cook_item = next((i for i in items if i.role == "Повар"), None)
    assert cook_item is not None and cook_item.assignee

    again = _scenario(overrides=((cook_item.item_id, STATUS_DONE),))
    restored = next(i for i in again.plan.items if i.item_id == cook_item.item_id)
    assert restored.status == STATUS_DONE

    others = [
        i
        for i in again.plan.items
        if i.item_id.startswith("П-breakfast-serving")
        and i.item_id != cook_item.item_id
    ]
    assert others, "участие остальных ролей не должно исчезнуть"
    assert all(i.status != STATUS_DONE for i in others)


def test_serving_has_no_duplicate_ids() -> None:
    """Повторный расчёт не создаёт дубликаты работ выдачи (AC13)."""
    plan = _scenario().plan
    ids = [i.item_id for i in plan.items]
    assert len(ids) == len(set(ids))


def test_serving_ids_do_not_depend_on_composition() -> None:
    """Смена состава не переименовывает существующие работы (AC13)."""
    base = {i.item_id for i in _serving_items(_scenario().plan, "breakfast-serving")}
    changed = {
        i.item_id
        for i in _serving_items(
            _scenario(_staff(chefs=5, cooks=6, helper=4)).plan, "breakfast-serving"
        )
    }
    assert base
    assert base == changed


def test_white_dishes_refill_is_explicit() -> None:
    """Пункт 16 отдельно требует пополнение чистой белой посуды (п. 16)."""
    operation = DUTIES_BY_KEY["dishes-during-lunch"].operation
    assert "чистой белой посуды" in operation
