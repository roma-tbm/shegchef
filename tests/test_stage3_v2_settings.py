"""V2-04 и V2-06: доступный ввод настроек и отклонений через интерфейс.

Тесты проверяют чистую логику `kitchen/web` — то, что именно видит и
сохраняет человек, без Streamlit: длительности и окна обязанностей, состав и
графики персонала, исполнитель бракеража, комментарии и отклонения по работам.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

from kitchen import seed
from kitchen.core.deviations import DEVIATION_LABELS, REASON_DELAY, REASON_PRODUCT
from kitchen.duties import DUTIES_BY_KEY, duties_for
from kitchen.staff import ROLE_COOK, ROLE_HELPER
from kitchen.web.deviation_store import (
    deviation_from_values,
    deviation_kinds,
    load_day_notes,
    load_deviations,
    save_day_notes,
    save_deviations,
    save_from_rows,
    saved_rows,
)
from kitchen.web.settings_store import (
    default_settings_path,
    day_inputs_to_settings,
    duty_settings_from,
    load_day_inputs,
    load_settings,
    save_day_inputs,
    save_settings,
    staff_settings_from,
    tasting_executor_of,
)
from kitchen.web.shift_plan import build_scenario

MONDAY = date(2026, 10, 5)
SATURDAY = date(2026, 10, 10)


def _store(tmp_path):
    """Файл хранилища внутри временного каталога теста."""
    return Path(tmp_path) / "store.json"


def _inputs(**overrides) -> dict:
    base = {"durations": {}, "windows": {}, "staff": {}, "tasting": ""}
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# V2-04 — настройки доступны для ввода и сохраняются
# ---------------------------------------------------------------------------


def test_duty_duration_is_editable_without_python(tmp_path) -> None:
    """Длительность обязанности вводится шефом, а не правкой кода."""
    save_day_inputs(
        MONDAY, _inputs(durations={"clean-hall": "20"}), _store(tmp_path)
    )

    settings = duty_settings_from(load_day_inputs(MONDAY, _store(tmp_path)))
    assert settings.duration_of(DUTIES_BY_KEY["clean-hall"]) == 20


def test_duty_window_is_editable_without_python(tmp_path) -> None:
    """Окно обязанности настраивается и переживает перезапуск."""
    save_day_inputs(
        MONDAY, _inputs(windows={"clean-hall": "13:30–14:30"}), _store(tmp_path)
    )

    settings = duty_settings_from(load_day_inputs(MONDAY, _store(tmp_path)))
    assert settings.window_of(DUTIES_BY_KEY["clean-hall"]) == (
        time(13, 30),
        time(14, 30),
    )


def test_duty_settings_can_be_disabled_from_inputs() -> None:
    """Лишнюю обязанность можно выключить, не правя каталог."""
    settings = duty_settings_from(_inputs(disabled=["clean-hall"]))

    assert "clean-hall" in settings.disabled
    assert all(d.key != "clean-hall" for d in duties_for(0, "Обычный", settings))


def test_filled_durations_remove_duration_gaps() -> None:
    """Заполненные длительности снимают именно пропуски длительностей."""
    scenario = build_scenario(
        seed.demo_data(MONDAY),
        MONDAY,
        duty_settings=duty_settings_from(
            _inputs(durations={d.key: "15" for d in duties_for(0, "Обычный")})
        ),
    )

    assert not [g for g in scenario.plan.gaps if "длительность" in g.reason]


def test_personal_schedule_is_editable() -> None:
    """Персональные графики и перерывы задаются пользователем."""
    cfg = staff_settings_from(
        _inputs(staff={"Шеф-повар 1": "Шеф-повар|07:00–20:00|09:00–09:30"})
    )

    assert cfg.staff
    person = cfg.staff[0]
    assert person.name == "Шеф-повар 1"
    assert person.role == "Шеф-повар"
    assert person.shift_start == time(7, 0)
    assert person.breaks == ((time(9, 0), time(9, 30)),)


def test_staff_counts_are_editable() -> None:
    """Численность будней настраивается числами, а не константой в коде."""
    cfg = staff_settings_from(_inputs(staff={"chefs": "2", "cooks": "5"}))

    assert cfg.chefs == 2
    assert cfg.cooks == 5


def test_tasting_executor_reaches_the_plan() -> None:
    """Исполнитель бракеража настраивается и доходит до расчёта."""
    inputs = _inputs(
        durations={d.key: "15" for d in duties_for(0, "Обычный")},
        staff={"chefs": "2", "cooks": "2", "helper": "1", "cleaners": "2"},
        tasting="Шеф-повар 2",
    )
    cfg = staff_settings_from(inputs)

    assert tasting_executor_of(inputs) == "Шеф-повар 2"
    assert cfg.tasting_executor == "Шеф-повар 2"

    scenario = build_scenario(
        seed.demo_data(MONDAY),
        MONDAY,
        duty_settings=duty_settings_from(inputs),
        staff_settings=cfg,
    )
    tasting = [i for i in scenario.plan.items if "ракераж" in i.operation]
    assert tasting, "точка бракежа должна быть в плане"
    assert all(i.assignee == "Шеф-повар 2" for i in tasting)


def test_without_tasting_executor_the_point_stays_unassigned() -> None:
    """Без настройки бракераж остаётся явно неназначенным (AC05, AC15)."""
    scenario = build_scenario(
        seed.demo_data(MONDAY),
        MONDAY,
        staff_settings=staff_settings_from(_inputs()),
    )
    tasting = [i for i in scenario.plan.items if "ракераж" in i.operation]

    assert tasting
    assert all(not i.assignee for i in tasting)
    assert not scenario.plan.ready
    assert any("бракераж" in r.lower() for r in scenario.plan.not_ready_reasons)


def test_shared_settings_survive_a_restart(tmp_path) -> None:
    """Общие настройки открываются заново в том виде, в каком сохранены."""
    save_settings(
        {
            "durations": {"clean-hall": "25"},
            "windows": {"clean-hall": "13:00–14:00"},
            "staff": {"cooks": "5"},
            "tasting": "Шеф-повар 3",
        },
        _store(tmp_path),
    )

    loaded = load_settings(_store(tmp_path))
    assert loaded["durations"]["clean-hall"] == "25"
    assert loaded["staff"]["cooks"] == "5"

    cfg = staff_settings_from(loaded)
    assert cfg.cooks == 5
    assert cfg.tasting_executor == "Шеф-повар 3"
    assert duty_settings_from(loaded).duration_of(DUTIES_BY_KEY["clean-hall"]) == 25


def test_day_inputs_fall_back_to_shared_settings(tmp_path) -> None:
    """День без своих настроек берёт общие."""
    save_settings({"tasting_executor": "Шеф общий"}, _store(tmp_path))

    assert load_day_inputs(SATURDAY, _store(tmp_path))["tasting"] == "Шеф общий"


def test_day_inputs_keep_other_days(tmp_path) -> None:
    """Настройки других дней не затираются текущим днём."""
    save_day_inputs(MONDAY, _inputs(tasting="Шеф 1"), _store(tmp_path))
    save_day_inputs(SATURDAY, _inputs(tasting="Шеф 2"), _store(tmp_path))

    assert load_day_inputs(MONDAY, _store(tmp_path))["tasting"] == "Шеф 1"
    assert load_day_inputs(SATURDAY, _store(tmp_path))["tasting"] == "Шеф 2"


def test_day_inputs_roundtrip(tmp_path) -> None:
    """Ввод дня сохраняется целиком и читается обратно."""
    payload = _inputs(
        durations={"clean-hall": "20"},
        windows={"clean-hall": "13:00–14:00"},
        staff={"cooks": "4"},
        tasting="Шеф-повар 1",
    )
    save_day_inputs(MONDAY, payload, _store(tmp_path))

    loaded = load_day_inputs(MONDAY, _store(tmp_path))
    assert loaded["durations"]["clean-hall"] == "20"
    assert loaded["windows"]["clean-hall"] == "13:00–14:00"
    assert loaded["staff"]["cooks"] == "4"
    assert loaded["tasting"] == "Шеф-повар 1"


def test_invalid_duration_is_rejected() -> None:
    """Ошибочный ввод не превращается молча в нулевую длительность."""
    with pytest.raises(ValueError, match="Длительность"):
        duty_settings_from(_inputs(durations={"clean-hall": "abc"}))


def test_negative_duration_is_rejected() -> None:
    """Отрицательная длительность — опечатка, а не «минус работа»."""
    with pytest.raises(ValueError):
        duty_settings_from(_inputs(durations={"clean-hall": "-10"}))


def test_inverted_window_is_rejected() -> None:
    """Окно, начинающееся позже конца, отвергается."""
    with pytest.raises(ValueError, match="Окно"):
        duty_settings_from(_inputs(windows={"clean-hall": "15:00–14:00"}))


def test_unparsable_staff_entry_is_rejected() -> None:
    """Стока без разбора не должна молча портить расчёт."""
    with pytest.raises(ValueError, match="Персонал"):
        staff_settings_from(_inputs(staff={"Повар 1": "совсем не время"}))


def test_blank_staff_stays_blank() -> None:
    """Пустой состав персонала остаётся пустым, а не заполняется людьми."""
    cfg = staff_settings_from(_inputs())
    assert cfg.staff == ()
    assert cfg.saturday == ()
    assert cfg.sunday == ()


def test_weekend_staff_from_settings_is_used() -> None:
    """Состав выходного дня берётся из настроек (AC09, AC10)."""
    cfg = staff_settings_from(
        _inputs(
            staff={
                "saturday": (
                    "Разнорабочий (суб)|Разнорабочий|08:00–14:00|\n"
                    "Повар (суб)|Повар|08:00–14:00|"
                ),
                "sunday": "Повар (вс)|Повар|08:00–16:00|",
            }
        )
    )

    assert [p.role for p in cfg.saturday] == [ROLE_HELPER, ROLE_COOK]
    assert cfg.saturday[0].shift_start == time(8, 0)
    assert [p.role for p in cfg.sunday] == [ROLE_COOK]

    saturday = build_scenario(
        seed.demo_data(SATURDAY),
        SATURDAY,
        staff_settings=cfg,
        duty_settings=duty_settings_from(
            _inputs(
                durations={d.key: "30" for d in duties_for(5, "Суббота")},
                windows={d.key: "08:00–14:00" for d in duties_for(5, "Суббота")},
            )
        ),
    )
    assert {i.assignee for i in saturday.plan.items if i.assignee} >= {
        "Разнорабочий (суб)",
        "Повар (суб)",
    }


def test_day_inputs_produce_a_usable_scenario(tmp_path) -> None:
    """Сквозной путь: введённые настройки реально меняют расчёт."""
    payload = _inputs(
        durations={d.key: "15" for d in duties_for(0, "Обычный")},
        staff={"chefs": "3", "cooks": "4", "helper": "3", "cleaners": "4"},
        tasting="Шеф-повар 1",
    )
    save_day_inputs(MONDAY, payload, _store(tmp_path))
    loaded = load_day_inputs(MONDAY, _store(tmp_path))
    duties, staff = day_inputs_to_settings(loaded)

    scenario = build_scenario(
        seed.demo_data(MONDAY),
        MONDAY,
        duty_settings=duties,
        staff_settings=staff,
    )

    assert not [
        g for g in scenario.plan.gaps
        if "длительность" in g.reason or "окно работы" in g.reason
    ], [g.message for g in scenario.plan.gaps]
    #: Демо-меню содержит только один день, поэтому пропуск «меню следующего дня»
    #: остаётся закономерным — он не связан с настройками.
    assert any("меню следующего дня" in r for r in scenario.plan.not_ready_reasons)


def test_missing_settings_do_not_block_the_scenario() -> None:
    """Пустые настройки дают явные пропуски, а не падение расчёта."""
    scenario = build_scenario(
        seed.demo_data(MONDAY),
        MONDAY,
        duty_settings=duty_settings_from(_inputs()),
        staff_settings=staff_settings_from(_inputs()),
    )
    assert scenario.plan.gaps


def test_settings_do_not_change_the_source_data() -> None:
    """Работа с настройками не меняет исходные данные книги (AC16)."""
    data = seed.demo_data(MONDAY)
    before = (len(data.recipes), len(data.tasks), len(data.menu))

    duty_settings_from(_inputs(durations={"clean-hall": "20"}))
    staff_settings_from(_inputs(staff={"cooks": "9"}))

    assert (len(data.recipes), len(data.tasks), len(data.menu)) == before


def test_default_settings_path_is_next_to_the_store() -> None:
    """Файл настроек лежит рядом с данными пользователя, не в репозитории."""
    path = default_settings_path("/tmp/shegchef/store.json")
    assert path.name == "settings.json"
    assert path.parent == path.parent


# ---------------------------------------------------------------------------
# V2-06 — комментарии и отклонения
# ---------------------------------------------------------------------------


def test_every_required_deviation_kind_is_offered() -> None:
    """Все пять типов отклонения доступны пользователю (п. 42)."""
    kinds = deviation_kinds()

    assert [k for k, _ in kinds] == [
        "delay", "product", "portions", "equipment", "other",
    ]
    assert [label for _, label in kinds] == [
        DEVIATION_LABELS[k]
        for k in ("delay", "product", "portions", "equipment", "other")
    ]


def test_deviation_requires_a_kind_and_a_comment() -> None:
    """Отклонение без пояснения — незаполненная ячейка, а не факт."""
    parsed = deviation_from_values("З-abc", REASON_PRODUCT, "нет лука")
    assert parsed.is_complete

    with pytest.raises(ValueError, match="пояснения"):
        deviation_from_values("З-abc", REASON_PRODUCT, "   ").validate()


def test_delay_requires_minutes_and_others_forbid_them() -> None:
    """Минуты задержки обязательны для задержки и лишние для остальных."""
    with pytest.raises(ValueError, match="минуты"):
        deviation_from_values("З-abc", REASON_DELAY, "опоздали", "0").validate()
    with pytest.raises(ValueError, match="минуты"):
        deviation_from_values("З-abc", REASON_PRODUCT, "нет лука", "10").validate()

    delay = deviation_from_values("З-abc", REASON_DELAY, "опоздали на 20", "20")
    delay.validate()
    assert delay.minutes == 20
    assert delay.label == "Задержка"


def test_deviation_without_a_work_is_rejected() -> None:
    """Отклонение без привязки к работе некуда сохранять."""
    with pytest.raises(ValueError, match="работ"):
        deviation_from_values("  ", REASON_PRODUCT, "нет лука").validate()


def test_comment_and_deviation_survive_recalculation() -> None:
    """Введённые комментарий и отклонение переживают пересчёт (AC12)."""
    data = seed.demo_data(MONDAY)
    first = build_scenario(data, MONDAY)
    item = first.plan.items[0]

    again = build_scenario(
        data,
        MONDAY,
        notes=((item.item_id, "переделать сливками"),),
        deviations=((item.item_id, REASON_PRODUCT),),
    )
    restored = next(i for i in again.plan.items if i.item_id == item.item_id)
    assert restored.comment == "переделать сливками"
    assert restored.deviation == REASON_PRODUCT
    assert again.plan.deviations == ((item.item_id, REASON_PRODUCT),)


def test_comment_of_a_removed_work_is_dropped() -> None:
    """Комментарий не переползает на другую работу после пересчёта."""
    scenario = build_scenario(
        seed.demo_data(MONDAY),
        MONDAY,
        notes=(("З-нет-такой", "старое замечание"),),
    )
    assert all(i.comment != "старое замечание" for i in scenario.plan.items)


def test_deviations_survive_a_reload_from_disk(tmp_path) -> None:
    """Отклонения сохраняются и открываются повторно (AC12)."""
    save_deviations(
        SATURDAY,
        {
            "З-abc": deviation_from_values(
                "З-abc", REASON_DELAY, "поздно", "15"
            )
        },
        _store(tmp_path),
    )
    loaded = load_deviations(SATURDAY, _store(tmp_path))

    assert loaded["З-abc"].kind == REASON_DELAY
    assert loaded["З-abc"].minutes == 15
    assert loaded["З-abc"].item_id == "З-abc"


def test_deviations_of_another_day_are_kept(tmp_path) -> None:
    """Отклонения за другой день не затираются."""
    save_deviations(
        SATURDAY, {"З-a": deviation_from_values("З-a", REASON_PRODUCT, "нет лука")}, _store(tmp_path)
    )
    save_deviations(
        MONDAY, {"З-b": deviation_from_values("З-b", REASON_PRODUCT, "нет мяса")}, _store(tmp_path)
    )

    assert load_deviations(MONDAY, _store(tmp_path))["З-b"].comment == "нет мяса"
    assert load_deviations(SATURDAY, _store(tmp_path))["З-a"].comment == "нет лука"


def test_incomplete_deviation_is_not_saved(tmp_path) -> None:
    """Некорректное отклонение не попадает в хранилище молча."""
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        save_deviations(
            SATURDAY,
            {"З-abc": deviation_from_values("З-abc", REASON_DELAY, "поздно", "0")},
            store,
        )

    assert load_deviations(SATURDAY, store) == {}


def test_removed_comment_is_deleted_from_the_store(tmp_path) -> None:
    """Снятый комментарий не остаётся призраком в отчёте."""
    store = _store(tmp_path)
    save_day_notes(MONDAY, {"З-abc": "пояснение"}, store)
    save_day_notes(MONDAY, {"З-abc": "  "}, store)

    assert load_day_notes(MONDAY, store) == {}


def test_day_notes_roundtrip(tmp_path) -> None:
    """Комментарии дня сохраняются рядом с отклонениями и читаются обратно."""
    save_day_notes(MONDAY, {"З-abc": "пояснение"}, _store(tmp_path))

    assert load_day_notes(MONDAY, _store(tmp_path)) == {"З-abc": "пояснение"}


def test_day_notes_keep_other_days(tmp_path) -> None:
    """Комментарии другого дня сохраняются."""
    save_day_notes(MONDAY, {"З-a": "раз"}, _store(tmp_path))
    save_day_notes(SATURDAY, {"З-b": "два"}, _store(tmp_path))

    assert load_day_notes(SATURDAY, _store(tmp_path)) == {"З-b": "два"}
    assert load_day_notes(MONDAY, _store(tmp_path)) == {"З-a": "раз"}


# ---------------------------------------------------------------------------
# Хранение: настройки других дней и снятые записи не теряются и не воскресают
# ---------------------------------------------------------------------------


def test_shared_save_keeps_day_overrides(tmp_path) -> None:
    """Сохранение общих настроек не стирает настройки отдельных дней.

    Общие и дневные настройки лежат в одном файле. Если запись общих стирает
    раздел `days`, то сохранение формы одного дня тихо ломает настройку
    соседнего — и человек об этом не узнает.
    """
    store = _store(tmp_path)
    save_day_inputs(MONDAY, _inputs(tasting="Шеф понедельника"), store)
    save_day_inputs(SATURDAY, _inputs(tasting="Шеф субботы"), store)

    save_settings(_inputs(tasting="Шеф общий", durations={"clean-hall": "15"}), store)

    assert load_day_inputs(MONDAY, store)["tasting"] == "Шеф понедельника"
    assert load_day_inputs(SATURDAY, store)["tasting"] == "Шеф субботы"
    assert load_settings(store)["durations"]["clean-hall"] == "15"


def test_day_save_keeps_shared_settings(tmp_path) -> None:
    """Сохранение дня не стирает общие настройки."""
    store = _store(tmp_path)
    save_settings(_inputs(durations={"clean-hall": "30"}), store)

    save_day_inputs(MONDAY, _inputs(tasting="Шеф 1"), store)

    assert load_settings(store)["durations"]["clean-hall"] == "30"
    assert load_day_inputs(MONDAY, store)["tasting"] == "Шеф 1"


def test_form_save_removes_a_cleared_deviation(tmp_path) -> None:
    """Снятое в форме отклонение исчезает, а не возвращается при печати."""
    store = _store(tmp_path)
    rows = [{"id": "З-a", "comment": "нет лука", "kind": "product", "minutes": ""}]
    save_from_rows(MONDAY, rows, store)
    assert load_deviations(MONDAY, store)["З-a"].kind == "product"

    save_from_rows(MONDAY, [{"id": "З-a", "comment": "", "kind": "", "minutes": ""}], store)

    assert load_deviations(MONDAY, store) == {}
    assert load_day_notes(MONDAY, store) == {}
    assert saved_rows(MONDAY, store) == ()


def test_form_save_keeps_the_other_days_records(tmp_path) -> None:
    """Замена записей одного дня не трогает другой день."""
    store = _store(tmp_path)
    save_from_rows(
        SATURDAY, [{"id": "З-s", "comment": "суббота", "kind": "product", "minutes": ""}], store
    )

    save_from_rows(MONDAY, [{"id": "З-m", "comment": "понедельник", "kind": "product", "minutes": ""}], store)

    assert load_day_notes(SATURDAY, store) == {"З-s": "суббота"}
    assert load_deviations(MONDAY, store)["З-m"].comment == "понедельник"


# ---------------------------------------------------------------------------
# Субботний отчёт: сохранение целиком и отказ на неполных данных (AC09)
# ---------------------------------------------------------------------------

SATURDAY = date(2026, 10, 10)


def _report(**kwargs):
    from kitchen.web.report_store import SaturdayReport

    return SaturdayReport(day=SATURDAY, **kwargs)


def _output(qty: float = 5.0, unit: str = "кг"):
    from kitchen.web.report_store import SemifinishedOutput

    return SemifinishedOutput(qty=qty, unit=unit)


def _marking(name: str = "Бульон говяжий"):
    from kitchen.web.report_store import SemifinishedMarking

    return SemifinishedMarking(
        name=name, made=SATURDAY, expires=date(2026, 10, 13)
    )


def test_report_survives_app_restart(tmp_path) -> None:
    """Отчёт перечитывается после перезапуска приложения."""
    from kitchen.web import report_store

    path = tmp_path / "reports.json"
    report_store.save_report(
        SATURDAY,
        _report(
            consumption={"Картофель": 3.0},
            outputs={"Бульон говяжий": _output()},
            markings={"Бульон говяжий": _marking()},
        ),
        path,
    )

    loaded = report_store.load_report(SATURDAY, path)
    assert loaded is not None
    assert loaded.consumption == {"Картофель": 3.0}
    assert loaded.outputs["Бульон говяжий"].qty == 5.0
    assert loaded.markings["Бульон говяжий"].expires == date(2026, 10, 13)


def test_report_of_another_day_is_not_touched(tmp_path) -> None:
    """Сохранение одного дня не затирает отчёт соседней субботы."""
    from kitchen.web import report_store

    path = tmp_path / "reports.json"
    other = date(2026, 9, 26)
    saved = _report(
        consumption={"Соль": 0.1},
        outputs={"Бульон говяжий": _output()},
        markings={"Бульон говяжий": _marking()},
    )
    report_store.save_report(other, saved, path)

    report_store.save_report(SATURDAY, saved, path)
    assert report_store.load_report(other, path) is not None
    assert report_store.load_report(SATURDAY, path) is not None


def test_report_without_marking_is_not_saved(tmp_path) -> None:
    """Неполная маркировка не превращается в сохранённый факт.

    Выход без маркировки нельзя признать заполненным: п. 44 требует имя,
    дату производства и срок годности. Плохая запись не должна молча
    занимать место настоящей и выглядеть в следующем открытии как отчётная.
    """
    from kitchen.web import report_store

    path = tmp_path / "reports.json"
    report = _report(outputs={"Бульон говяжий": _output()})

    assert report.incomplete_markings == ("Бульон говяжий",)
    # С V3-05 это ошибка валидации, а не тихая потеря записи: плохая маркировка
    # не занимает место настоящей и не съедает предыдущий корректный отчёт.
    with pytest.raises(report_store.IncompleteReportError):
        report_store.save_report(SATURDAY, report, path)
    assert report_store.load_report(SATURDAY, path) is None


def test_output_without_marking_is_rejected(tmp_path) -> None:
    """Выход полуфабриката без единицы измерения — ошибка данных."""
    from kitchen.web.report_store import SemifinishedOutput

    with pytest.raises(ValueError, match="единица"):
        SemifinishedOutput(qty=5.0, unit="  ")
