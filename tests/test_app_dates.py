"""Переключение рабочей даты в веб-интерфейсе (app.py).

AppTest запускает app.py как настоящий Streamlit-скрипт, меняет дату через
выпадающий календарь и проверяет, что меню новой даты приходит из хранилища
или плана, а не «переезжает» со старой. Сознательно очищенное меню
отличается от даты, которой в хранилище нет вовсе.

Даты для тестов считаются относительно сегодняшнего дня, чтобы смена даты
всегда отличалась от стартовой — иначе Streamlit не вызовет on_change.
Изоляция как в test_app_search: личные меню пользователя не читаются.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta, time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from kitchen.menu_week import WEEK_PLAN
from kitchen.models import MenuLine
from kitchen.web.menu_store import load_days, plan_to_menu, save_days

APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture(autouse=True)
def _quiet_streamlit():
    logging.getLogger("streamlit").setLevel(logging.CRITICAL)
    yield


@pytest.fixture()
def app(tmp_path) -> AppTest:
    at = AppTest.from_file(str(APP)).run()
    # изоляция: не читаем и не пишем личные меню пользователя
    at.session_state["store"] = str(tmp_path / "menu.json")
    at.session_state["menu"] = []
    at.session_state["menu_source"] = "пустое меню"
    at.session_state["menu_seed"] += 1
    at.run()
    return at


def _future_monday() -> date:
    today = date.today()
    return today + timedelta(days=(7 - today.weekday() % 7) % 7 or 7)


def _future_saturday() -> date:
    today = date.today()
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)
    if saturday <= today:
        saturday += timedelta(days=7)
    return saturday


def test_смена_даты_загружает_сохранённое_меню_нового_дня(app, tmp_path):
    a = date.today() - timedelta(days=2)
    b = date.today() - timedelta(days=1)
    first = MenuLine("Солянка мясная", 50, time(12, 0), "Обед", a)
    second = MenuLine("Рассольник", 40, time(12, 0), "Обед", b)
    store = Path(str(app.session_state["store"]))
    save_days({a.isoformat(): (first,), b.isoformat(): (second,)}, store)

    app.date_input(key="_day").set_value(a)
    app.run()
    assert not app.exception
    assert app.session_state["day"] == a
    assert [line.recipe for line in app.session_state["menu"]] == ["Солянка мясная"]

    app.date_input(key="_day").set_value(b)
    app.run()
    assert not app.exception
    assert app.session_state["day"] == b
    assert [line.recipe for line in app.session_state["menu"]] == ["Рассольник"]

    app.date_input(key="_day").set_value(a)
    app.run()
    assert [line.recipe for line in app.session_state["menu"]] == ["Солянка мясная"]

    stored = load_days(store)
    assert [line.recipe for line in stored[a.isoformat()]] == ["Солянка мясная"]
    assert [line.recipe for line in stored[b.isoformat()]] == ["Рассольник"]


def test_переход_на_будний_день_синхронизирует_селектор(app):
    monday = _future_monday()
    tuesday = monday + timedelta(days=1)

    app.date_input(key="_day").set_value(tuesday)
    app.run()
    assert not app.exception
    assert app.selectbox(key="_weekday").value == "Вторник"

    app.date_input(key="_day").set_value(monday)
    app.run()
    assert not app.exception
    assert app.selectbox(key="_weekday").value == "Понедельник"


def test_будний_день_без_записи_разворачивает_план_но_не_сохраняет_его(app):
    monday = _future_monday()
    expected = [
        line.recipe for line in plan_to_menu(WEEK_PLAN, "Понедельник", monday)
    ]
    assert expected, "план на понедельник не должен быть пустым"

    app.date_input(key="_day").set_value(monday)
    app.run()
    assert not app.exception
    assert app.session_state["day"] == monday
    assert [line.recipe for line in app.session_state["menu"]] == expected
    assert app.session_state["menu_source"] == "план «Понедельник»"

    stored = load_days(Path(str(app.session_state["store"])))
    assert monday.isoformat() not in stored, "заготовка плана не становится правкой"


def test_выходной_без_записи_даёт_пустое_меню(app):
    saturday = _future_saturday()
    app.date_input(key="_day").set_value(saturday)
    app.run()
    assert not app.exception
    assert app.session_state["menu"] == []
    assert "выходной" in app.session_state["menu_source"]
    assert app.selectbox(key="_weekday").value == "Понедельник"


def test_сохранённое_пустое_меню_не_считается_выходным(app, tmp_path):
    monday = _future_monday()
    save_days({monday.isoformat(): ()}, Path(str(app.session_state["store"])))

    app.date_input(key="_day").set_value(monday)
    app.run()
    assert not app.exception
    assert app.session_state["menu"] == []
    assert "сохранённое меню" in app.session_state["menu_source"]
    assert "выходной" not in app.session_state["menu_source"]


def test_смена_даты_сбрасывает_расчёт_предпросмотр_и_экспорт(app):
    a = date.today() - timedelta(days=2)
    b = date.today() - timedelta(days=1)
    store = Path(str(app.session_state["store"]))
    save_days(
        {
            a.isoformat(): (MenuLine("Солянка мясная", 50, time(12, 0), "Обед", a),),
            b.isoformat(): (MenuLine("Рассольник", 40, time(12, 0), "Обед", b),),
        },
        store,
    )

    app.date_input(key="_day").set_value(a)
    app.run()
    app.session_state["preview"] = object()
    app.session_state["sheet"] = object()
    app.session_state["excel_bytes"] = b"x"
    app.session_state["excel_name"] = "x.xlsx"

    app.date_input(key="_day").set_value(b)
    app.run()
    assert not app.exception
    assert app.session_state["preview"] is None
    assert app.session_state["sheet"] is None
    assert app.session_state["excel_bytes"] is None
    assert app.session_state["excel_name"] == ""