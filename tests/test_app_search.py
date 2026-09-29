"""Поиск блюд в веб-интерфейсе: глобальный по всему каталогу ТТК.

AppTest запускает app.py как настоящий Streamlit-скрипт и проверяет весь путь
пользователя: ввод «суп», появление обеденных супов в списке независимо от
какого-либо предвыбранного приёма, авто-приём и стандартное время выдачи из
карточки, и фактическое добавление выбранного блюда в меню дня.

Тесты изолированы: личные меню пользователя не читаются, а сохранение идёт
в временную папку вместо ~/.shegchef/menu.json.
"""

from __future__ import annotations

import logging
from datetime import date, time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from kitchen.web.menu_store import load_days

SOUPS = (
    "Гороховый суп с копчёностями",
    "Домашний куриный суп с зеленью",
    "Марокканский суп с нутом и говядиной",
)


@pytest.fixture(autouse=True)
def _quiet_streamlit():
    logging.getLogger("streamlit").setLevel(logging.CRITICAL)
    yield


APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture()
def app(tmp_path: Path) -> AppTest:
    at = AppTest.from_file(str(APP)).run()
    # изоляция: не читаем и не пишем личные меню пользователя
    at.session_state["store"] = str(tmp_path / "menu.json")
    at.session_state["menu"] = []
    at.session_state["menu_source"] = "пустое меню"
    at.session_state["menu_seed"] += 1
    at.run()
    return at


def test_суп_находится_по_всему_каталогу_а_не_по_приёму(app: AppTest):
    app.text_input(key="_add_search").set_value("суп")
    app.run()

    options = {str(option) for option in app.selectbox(key="_add_dish").options}
    assert options == {f"{name} · Обед" for name in SOUPS}


def test_ручного_выбора_приёма_пищи_больше_нет(app: AppTest):
    app.text_input(key="_add_search").set_value("суп")
    app.run()
    selectboxes = [sb for sb in app.selectbox if sb.key == "_add_meal"]
    assert not selectboxes, "приём не выбирается вручную — он из карточки блюда"


def test_выбор_супа_задаёт_время_выдачи_из_карточки(app: AppTest):
    app.text_input(key="_add_search").set_value("суп")
    app.run()
    app.selectbox(key="_add_dish").select("Гороховый суп с копчёностями")
    app.run()

    assert app.time_input(key="_add_serve").value == time(12, 0)


def test_добавление_выбранного_супа_в_меню(app: AppTest):
    app.text_input(key="_add_search").set_value("суп")
    app.run()
    app.selectbox(key="_add_dish").select("Гороховый суп с копчёностями")
    app.run()
    app.button(key="_add_submit").click()
    app.run()

    assert not app.exception
    lines = app.session_state["menu"]
    assert len(lines) == 1
    line = lines[0]
    assert line.recipe == "Гороховый суп с копчёностями"
    assert line.meal == "Обед"
    assert line.portions == 50
    assert line.serve_at == time(12, 0)

    stored = load_days(Path(str(app.session_state["store"])))
    saved = stored.get(date.today().isoformat())
    assert saved and [l.recipe for l in saved] == ["Гороховый суп с копчёностями"]


def test_правку_времени_выдачи_не_затирает_авто_значение(app: AppTest):
    app.text_input(key="_add_search").set_value("суп")
    app.run()
    app.selectbox(key="_add_dish").select("Гороховый суп с копчёностями")
    app.run()
    app.time_input(key="_add_serve").set_value(time(11, 45))
    app.run()
    app.button(key="_add_submit").click()
    app.run()

    line = app.session_state["menu"][0]
    assert line.serve_at == time(11, 45)


def test_сообщение_ничего_не_найдено_по_всему_каталогу(app: AppTest):
    app.text_input(key="_add_search").set_value("борщ")
    app.run()

    texts = [info.value for info in app.info]
    assert any("борщ" in text and "каталоге ТТК" in text for text in texts)