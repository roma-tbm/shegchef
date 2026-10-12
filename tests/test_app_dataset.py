"""Веб использует тот же набор данных, что CLI: env-конфигурация (AC07, R1).

AppTest запускает настоящий app.py. Каталог данных задаётся переменной
окружения ``SHEGCHEF_DATA_DIR`` — доступной пользователю конфигурацией запуска,
без ручной подмены session_state или модульных атрибутов.

Здесь же — сквозная сверка фактического CLI и фактического веба (R5): оба
запускаются на одном временном наборе и дают одинаковую печатную форму.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from kitchen.models import MenuLine  # noqa: E402
from kitchen.core.deviations import REASON_DELAY  # noqa: E402
from kitchen.core.planner import STATUS_DONE  # noqa: E402
from kitchen.web import deviation_store, menu_store, settings_store, status_store  # noqa: E402
from kitchen.web.dataset import Dataset  # noqa: E402
from kitchen.web.shift_plan import general_plan_form  # noqa: E402

from conftest import app_on_day  # noqa: E402

import build as cli  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app.py"
MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)
ITEM = "П-stock-up"


def _line(name: str, day: date, portions: int = 50) -> MenuLine:
    return MenuLine(name, portions, time(12, 0), "Обед", day)


def _app(data_dir: Path) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


# ---------------------------------------------------------------------------
# R1: набор данных из конфигурации запуска
# ---------------------------------------------------------------------------


def test_web_reads_menu_from_the_alt_name_only(tmp_path, monkeypatch):
    """Меню только в «меню.json» подхватывается обычным запуском веба."""
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, Dataset.in_dir(tmp_path).menu_alt)

    tester = _app(tmp_path)
    tester.date_input(key="_day").set_value(MONDAY)
    tester.run()
    assert not tester.exception, [e.value for e in tester.exception]
    assert [line.recipe for line in tester.session_state["menu"]] == ["Солянка мясная"]


def test_web_merges_non_overlapping_dates(tmp_path, monkeypatch):
    """Непересекающиеся даты двух имён меню доступны вебу совместно."""
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, ds.menu)
    menu_store.save_days({TUESDAY.isoformat(): (_line("Рассольник", TUESDAY),)}, ds.menu_alt)

    tester = _app(tmp_path)
    tester.date_input(key="_day").set_value(MONDAY)
    tester.run()
    assert [line.recipe for line in tester.session_state["menu"]] == ["Солянка мясная"]

    tester.date_input(key="_day").set_value(TUESDAY)
    tester.run()
    assert [line.recipe for line in tester.session_state["menu"]] == ["Рассольник"]


def test_web_shows_menu_conflict_and_keeps_files(tmp_path, monkeypatch):
    """Конфликт одной даты виден в вебе до записи, исходные байты целы."""
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, ds.menu)
    menu_store.save_days({MONDAY.isoformat(): (_line("Рассольник", MONDAY),)}, ds.menu_alt)
    before = (ds.menu.read_text(encoding="utf-8"), ds.menu_alt.read_text(encoding="utf-8"))

    tester = _app(tmp_path)

    assert not tester.exception, [e.value for e in tester.exception]
    assert tester.error, "конфликт должен быть показан человеку"
    assert "конфликт" in " ".join(e.value for e in tester.error)
    assert (
        ds.menu.read_text(encoding="utf-8"),
        ds.menu_alt.read_text(encoding="utf-8"),
    ) == before


def test_web_resolves_conflict_with_explicit_file(tmp_path, monkeypatch):
    """SHEGHCHEF_MENU_FILE выбирает один источник и снимает конфликт."""
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, ds.menu)
    menu_store.save_days({MONDAY.isoformat(): (_line("Рассольник", MONDAY),)}, ds.menu_alt)
    monkeypatch.setenv("SHEGCHEF_MENU_FILE", str(ds.menu))

    tester = _app(tmp_path)
    tester.date_input(key="_day").set_value(MONDAY)
    tester.run()

    assert not tester.exception, [e.value for e in tester.exception]
    messages = " ".join(e.value for e in tester.error)
    assert "конфликт" not in messages and "поврежд" not in messages
    assert [line.recipe for line in tester.session_state["menu"]] == ["Солянка мясная"]


def test_web_save_keeps_other_dates(tmp_path, monkeypatch):
    """Сохранение меню одного дня не теряет записи других дней (R1)."""
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({TUESDAY.isoformat(): (_line("Рассольник", TUESDAY),)}, ds.menu)

    tester = _app(tmp_path)
    tester.date_input(key="_day").set_value(MONDAY)
    tester.run()
    # Добавляем блюдо: обработчик сохраняет меню дня в тот же каталог данных.
    tester.text_input(key="_add_search").set_value("суп")
    tester.run()
    tester.selectbox(key="_add_dish").select("Гороховый суп с копчёностями")
    tester.run()
    tester.button(key="_add_submit").click()
    tester.run()
    assert not tester.exception, [e.value for e in tester.exception]

    stored = menu_store.load_days(ds.menu)
    assert TUESDAY.isoformat() in stored
    assert MONDAY.isoformat() in stored


# ---------------------------------------------------------------------------
# R5: фактический CLI и фактический веб на одном наборе данных
# ---------------------------------------------------------------------------


def _seed_dataset(data_dir: Path) -> Dataset:
    ds = Dataset.in_dir(data_dir)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, ds.menu)
    settings_store.save_day_inputs(MONDAY, {"durations": {"stock-up": "20"}}, ds.settings)
    status_store.save_statuses({MONDAY.isoformat(): ((ITEM, STATUS_DONE),)}, ds.statuses)
    deviation_store.save_day_notes(MONDAY, {ITEM: "пояснение шефа"}, ds.notes)
    deviation_store.save_deviations(
        MONDAY,
        {
            ITEM: deviation_store.deviation_from_values(
                ITEM, REASON_DELAY, "опоздали", "15"
            )
        },
        ds.notes,
    )
    return ds


def test_actual_cli_and_actual_web_agree_end_to_end(tmp_path, monkeypatch):
    """CLI и веб на одном каталоге дают одинаковую печатную форму (R5)."""
    data_dir = tmp_path / "data"
    _seed_dataset(data_dir)
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(data_dir))

    out = tmp_path / "Кухня.xlsx"
    code = cli.main(
        ["--out", str(out), "--data-dir", str(data_dir), "--day", "05.10.2026", "--no-open"]
    )
    assert code == 0
    cli_forms = sorted(
        (out.parent / cli.PRINT_DIR_NAME).glob("Общий план*.txt")
    )
    assert cli_forms, "CLI должен выгрузить общий план"
    cli_text = cli_forms[0].read_text(encoding="utf-8")

    tester = app_on_day(APP, MONDAY)
    assert not tester.exception, [e.value for e in tester.exception]
    web_text = general_plan_form(tester.session_state["scenario"]).to_text()

    assert web_text == cli_text, "план CLI и веба должен совпадать"


# ---------------------------------------------------------------------------
# R7: сохранение веб-правок идёт в выбранный источник
# ---------------------------------------------------------------------------


def _scale_day(tester, factor: float) -> None:
    """Меняет порции через веб: предпросмотр масштаба и применение (сохраняет)."""
    tester.number_input(key="_scale_factor").set_value(factor)
    [b for b in tester.button if b.label == "Показать предпросмотр"][0].click()
    tester.run()
    assert not tester.exception, [e.value for e in tester.exception]
    [b for b in tester.button if b.label == "Применить масштаб"][0].click()
    tester.run()
    assert not tester.exception, [e.value for e in tester.exception]


def test_web_saves_to_explicit_menu_file_and_reopens(tmp_path, monkeypatch):
    """SHEGCHEF_MENU_FILE: правка сохраняется в выбранный файл, а не menu.json (R7.1)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    override = tmp_path / "custom_menu.json"
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, override)
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SHEGCHEF_MENU_FILE", str(override))

    tester = app_on_day(APP, MONDAY)
    _scale_day(tester, 2.0)
    assert [line.portions for line in menu_store.load_days(override)[MONDAY.isoformat()]] == [100]

    # Посторонний menu.json в каталоге данных не создаётся без явного выбора.
    assert not (data_dir / "menu.json").exists()
    assert not (data_dir / "меню.json").exists()

    # Смена даты, затем новая сессия: правка загружается обратно из выбранного файла.
    tester.date_input(key="_day").set_value(TUESDAY)
    tester.run()
    reopened = app_on_day(APP, MONDAY)
    assert [line.portions for line in reopened.session_state["menu"]] == [100]


def test_web_edits_menu_from_alt_name_without_conflict(tmp_path, monkeypatch):
    """Правка даты из меню.json не создаёт конфликт и не теряет другие даты (R7.2)."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, ds.menu_alt)
    menu_store.save_days({TUESDAY.isoformat(): (_line("Рассольник", TUESDAY),)}, ds.menu)
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))

    tester = app_on_day(APP, MONDAY)
    _scale_day(tester, 2.0)

    assert [line.portions for line in menu_store.load_days(ds.menu_alt)[MONDAY.isoformat()]] == [100]
    assert MONDAY.isoformat() not in menu_store.load_days(ds.menu), (
        "правка не должна появляться во втором имени меню"
    )
    assert [line.recipe for line in menu_store.load_days(ds.menu)[TUESDAY.isoformat()]] == [
        "Рассольник"
    ]

    # Переоткрытие приложения: конфликта нет, обе даты на месте.
    reopened = app_on_day(APP, MONDAY)
    assert [line.portions for line in reopened.session_state["menu"]] == [100]
    assert not [e for e in reopened.error if "конфликт" in e.value]
    reopened.date_input(key="_day").set_value(TUESDAY)
    reopened.run()
    assert [line.recipe for line in reopened.session_state["menu"]] == ["Рассольник"]


def test_web_refuses_to_save_while_menu_conflict(tmp_path, monkeypatch):
    """При конфликте без явного выбора запись запрещена, файлы целы (R7.3)."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, ds.menu)
    menu_store.save_days({MONDAY.isoformat(): (_line("Рассольник", MONDAY),)}, ds.menu_alt)
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))

    tester = _app(tmp_path)
    assert not tester.exception, [e.value for e in tester.exception]
    assert any("конфликт" in e.value for e in tester.error)
    assert ds.menu.read_text(encoding="utf-8") and ds.menu_alt.read_text(encoding="utf-8")


def test_web_conflict_saves_the_chosen_file_only(tmp_path, monkeypatch):
    """С явным выбором сохраняется выбранный источник, второй файл неизменен (R7.3)."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная", MONDAY),)}, ds.menu)
    menu_store.save_days({MONDAY.isoformat(): (_line("Рассольник", MONDAY),)}, ds.menu_alt)
    untouched = ds.menu.read_text(encoding="utf-8")
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SHEGCHEF_MENU_FILE", str(ds.menu_alt))

    tester = app_on_day(APP, MONDAY)
    _scale_day(tester, 2.0)

    assert [line.portions for line in menu_store.load_days(ds.menu_alt)[MONDAY.isoformat()]] == [100]
    assert ds.menu.read_text(encoding="utf-8") == untouched