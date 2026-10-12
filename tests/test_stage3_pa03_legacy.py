"""PA-03: совместимость ранее сохранённых отметок выдачи (AC12, AC16).

До PA-01 работы выдачи имели ID `П-breakfast-serving` и `П-lunch-serving`.
После разделения на роли появились ID с суффиксом роли. Старые данные в
хранилищах должны остаться доступными человеку как отдельный исторический факт:
их нельзя молча потерять, но и нельзя автоматически раздать DONE всей новой
бригаде — прежний исполнитель в сохранённых данных не зафиксирован.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path

import pytest

from kitchen import seed
from kitchen.core.deviations import Deviation
from kitchen.core.planner import STATUS_BLOCKED, STATUS_DONE
from kitchen.duties import DUTIES_BY_KEY, DutySettings, duties_for
from kitchen.staff import DEFAULT_BREAKS, StaffSettings
from kitchen.web import deviation_store, menu_store, status_store
from kitchen.web.dataset import Dataset
from kitchen.web.shift_plan import build_scenario, general_plan_form

MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)
BREAKFAST_LEGACY = "П-breakfast-serving"
LUNCH_LEGACY = "П-lunch-serving"
TABS = ("Меню", "Расчёт смены", "Продукты и дефицит", "Задачи по ролям", "Маршруты",
        "Экспорт", "Настройки смены", "Отклонения", "Отчёт за субботу")


def _filled_settings(weekday: int = 0, mode: str = "Обычный") -> DutySettings:
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


def _scenario(*, overrides=(), notes=(), deviations=(), breaks=DEFAULT_BREAKS):
    day = MONDAY
    return build_scenario(
        seed.demo_data(day),
        day,
        duty_settings=_filled_settings(),
        staff_settings=_staff(breaks=breaks),
        next_menu=seed.demo_data(day + timedelta(days=1)).menu,
        overrides=overrides,
        notes=notes,
        deviations=deviations,
    )


def _legacy_by_id(plan) -> dict:
    return {record.item_id: record for record in plan.legacy}


# ---------------------------------------------------------------------------
# PA-03: старые записи доступны и не раздаются бригаде
# ---------------------------------------------------------------------------


def test_legacy_status_comment_and_deviation_are_kept() -> None:
    """Старые статус, комментарий и отклонение видны как историческая запись."""
    scenario = _scenario(
        overrides=((BREAKFAST_LEGACY, STATUS_DONE),),
        notes=((BREAKFAST_LEGACY, "Сохранённый комментарий до PA-01"),),
        deviations=((BREAKFAST_LEGACY, "product"),),
    )

    record = _legacy_by_id(scenario.plan)[BREAKFAST_LEGACY]
    assert record.status == STATUS_DONE
    assert record.comment == "Сохранённый комментарий до PA-01"
    assert record.deviation == "product"
    assert record.operation == DUTIES_BY_KEY["breakfast-serving"].operation


def test_old_done_is_not_copied_to_the_new_brigade() -> None:
    """Старый DONE не превращает всех новых участников в выполнивших."""
    scenario = _scenario(
        overrides=((BREAKFAST_LEGACY, STATUS_DONE),),
        notes=((BREAKFAST_LEGACY, "комментарий старой работы"),),
        deviations=((BREAKFAST_LEGACY, "product"),),
    )
    serving = [
        i for i in scenario.plan.items if i.item_id.startswith("П-breakfast-serving:")
    ]

    assert len(serving) == 3
    assert all(i.status != STATUS_DONE for i in serving)
    assert all(i.claimed_status == "" for i in serving)
    assert all(i.comment == "" for i in serving)
    assert all(i.deviation == "" for i in serving)


def test_legacy_records_are_isolated_between_duties() -> None:
    """Комментарий завтрака не переносится на обед и наоборот."""
    scenario = _scenario(
        overrides=((BREAKFAST_LEGACY, STATUS_DONE),),
        notes=((BREAKFAST_LEGACY, "только завтрак"),),
    )

    assert set(_legacy_by_id(scenario.plan)) == {BREAKFAST_LEGACY}
    assert LUNCH_LEGACY not in _legacy_by_id(scenario.plan)


def test_legacy_history_has_no_duplicates_on_recalculation() -> None:
    """Повторный расчёт не плодит исторические записи."""
    kwargs = dict(
        overrides=((BREAKFAST_LEGACY, STATUS_DONE),),
        notes=((BREAKFAST_LEGACY, "комментарий"),),
        deviations=((BREAKFAST_LEGACY, "product"),),
    )
    first = _scenario(**kwargs)
    again = _scenario(**kwargs)

    assert [r.item_id for r in first.plan.legacy] == [BREAKFAST_LEGACY]
    assert [r.item_id for r in again.plan.legacy] == [BREAKFAST_LEGACY]


def test_legacy_record_does_not_change_readiness() -> None:
    """Историческая запись не подменяет расчётные статусы и готовность."""
    base = _scenario()
    with_legacy = _scenario(
        overrides=((BREAKFAST_LEGACY, STATUS_DONE),),
        notes=((BREAKFAST_LEGACY, "комментарий"),),
        deviations=((BREAKFAST_LEGACY, "product"),),
    )

    assert with_legacy.plan.gaps == base.plan.gaps
    assert with_legacy.plan.ready == base.plan.ready


def test_blocked_work_stays_blocked_with_old_done() -> None:
    """BLOCKED виден независимо от старого заявленного DONE."""
    scenario = _scenario(
        overrides=((BREAKFAST_LEGACY, STATUS_DONE),),
        notes=((BREAKFAST_LEGACY, "комментарий"),),
        deviations=((BREAKFAST_LEGACY, "product"),),
    )
    blocked = [i for i in scenario.plan.items if i.status == STATUS_BLOCKED]

    assert blocked, "с перерывами 08:30–09:00 блокировка должна быть видна"
    assert all(i.status == STATUS_BLOCKED for i in blocked)
    assert scenario.plan.ready is False


def test_general_plan_prints_the_historical_record() -> None:
    """Историческая запись доступна в общем печатном представлении."""
    scenario = _scenario(
        overrides=((BREAKFAST_LEGACY, STATUS_DONE),),
        notes=((BREAKFAST_LEGACY, "Сохранённый комментарий до PA-01"),),
        deviations=((BREAKFAST_LEGACY, "product"),),
    )

    flat = " ".join(general_plan_form(scenario).to_text().split())

    assert BREAKFAST_LEGACY in flat
    assert "историческ" in flat.lower()
    assert "Сохранённый комментарий до PA-01" in flat
    assert "DONE" in flat


def test_route_forms_do_not_invent_a_personal_assignment() -> None:
    """Историческая запись не приписывается людям без доказательства."""
    scenario = _scenario(
        overrides=((BREAKFAST_LEGACY, STATUS_DONE),),
        notes=((BREAKFAST_LEGACY, "комментарий"),),
    )
    from kitchen.web.shift_plan import route_form

    for employee in scenario.plan.staff:
        text = route_form(scenario, employee).to_text()
        assert "историческ" not in text.lower()


# ---------------------------------------------------------------------------
# PA-03: интерфейс и сохранение формы
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    return Dataset.in_dir(tmp_path).menu


def _menu(day: date = MONDAY):
    from kitchen.models import MenuLine

    return (
        MenuLine(
            recipe=next(iter(seed.RECIPES)),
            portions=20,
            serve_at=time(12, 0),
            meal="Обед",
            day=day,
        ),
    )


def _legacy_files(store: Path) -> None:
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)
    status_store.save_statuses(
        {MONDAY.isoformat(): ((BREAKFAST_LEGACY, STATUS_DONE),)},
        status_store.default_status_path_for(store),
    )
    notes_path = deviation_store.default_notes_path(store)
    deviation_store.save_day_notes(
        MONDAY, {BREAKFAST_LEGACY: "Сохранённый комментарий до PA-01"}, notes_path
    )
    deviation_store.save_deviations(
        MONDAY,
        {
            BREAKFAST_LEGACY: Deviation(
                item_id=BREAKFAST_LEGACY, kind="product", comment="Сохранённый комментарий до PA-01"
            )
        },
        notes_path,
    )


def test_legacy_records_do_not_leak_across_days(tmp_path) -> None:
    """Записи одного дня не появляются в другом дне."""
    notes_path = tmp_path / "notes.json"
    status_path = tmp_path / "statuses.json"
    deviation_store.save_day_notes(
        MONDAY, {BREAKFAST_LEGACY: "понедельник"}, notes_path
    )
    status_store.save_statuses(
        {MONDAY.isoformat(): ((BREAKFAST_LEGACY, STATUS_DONE),)}, status_path
    )

    assert deviation_store.load_day_notes(TUESDAY, notes_path) == {}
    assert status_store.overrides_for_day(TUESDAY, status_path) == ()


def test_saving_new_status_keeps_the_legacy_status(tmp_path) -> None:
    """Новая отметка участника не затирает старую запись выдачи."""
    path = tmp_path / "statuses.json"
    status_store.save_statuses(
        {MONDAY.isoformat(): ((BREAKFAST_LEGACY, STATUS_DONE),)}, path
    )
    status_store.save_statuses(
        {MONDAY.isoformat(): (("П-breakfast-serving:Повар", "IN_PROGRESS"),)}, path
    )

    saved = dict(status_store.overrides_for_day(MONDAY, path))
    assert saved[BREAKFAST_LEGACY] == STATUS_DONE
    assert saved["П-breakfast-serving:Повар"] == "IN_PROGRESS"


def test_app_shows_history_and_form_save_keeps_it(store: Path) -> None:
    """UI показывает историю, а сохранение формы не удаляет старые данные."""
    pytest.importorskip("streamlit")
    from conftest import app_on_day

    _legacy_files(store)
    app_path = Path(__file__).resolve().parent.parent / "app.py"
    tester = app_on_day(app_path, MONDAY)

    plan = tester.session_state["scenario"].plan
    assert [r.item_id for r in plan.legacy] == [BREAKFAST_LEGACY]

    routes = tester.tabs[TABS.index("Маршруты")]
    shown = " ".join(
        [m.value for m in routes.markdown]
        + [c.value for c in routes.caption]
        + [w.value for w in routes.warning]
        + [i.value for i in routes.info]
    )
    assert BREAKFAST_LEGACY in shown, shown

    notes = tester.tabs[TABS.index("Отклонения")]
    save_buttons = [b for b in notes.button if "Сохранить комментарии" in b.label]
    assert save_buttons, "кнопка сохранения комментариев должна быть"
    save_buttons[0].click().run()

    notes_path = deviation_store.default_notes_path(store)
    assert (
        deviation_store.load_day_notes(MONDAY, notes_path)[BREAKFAST_LEGACY]
        == "Сохранённый комментарий до PA-01"
    )
    assert (
        deviation_store.load_deviations(MONDAY, notes_path)[BREAKFAST_LEGACY].kind
        == "product"
    )
    assert status_store.overrides_for_day(
        MONDAY, status_store.default_status_path_for(store)
    ) == ((BREAKFAST_LEGACY, STATUS_DONE),)
    serving = [
        i
        for i in tester.session_state["scenario"].plan.items
        if i.item_id.startswith("П-breakfast-serving:")
    ]
    assert all(i.status != STATUS_DONE for i in serving)
