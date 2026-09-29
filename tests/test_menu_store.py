"""Локальное JSON-хранение меню (kitchen/web/menu_store.py).

Excel остаётся экспортом, а правки меню веб-интерфейса живут в простом JSON.
"""

from __future__ import annotations

import json
from datetime import date, time

import pytest

from kitchen.menu_week import WEEK_PLAN
from kitchen.models import MenuLine
from kitchen.web.menu_store import load_days, plan_to_menu, save_days

DAY = date(2026, 9, 28)


def test_круг_save_load_сохраняет_меню(tmp_path):
    menu = (
        MenuLine("Каша овсяная с солёным арахисом", 50, time(8, 0), "Завтрак", DAY,
                 note="со старыми огурцами"),
        MenuLine("Солянка мясная", 65, time(12, 0), "Обед", DAY),
    )
    path = tmp_path / "menu.json"
    save_days({DAY.isoformat(): menu}, path)

    loaded = load_days(path)
    assert set(loaded) == {DAY.isoformat()}
    restored = loaded[DAY.isoformat()]
    assert len(restored) == 2
    assert restored[0].recipe == menu[0].recipe
    assert restored[0].portions == menu[0].portions
    assert restored[0].serve_at == menu[0].serve_at
    assert restored[0].meal == menu[0].meal
    assert restored[0].day == DAY
    assert restored[0].note == menu[0].note


def test_разные_даты_не_затирают_друг_друга(tmp_path):
    path = tmp_path / "menu.json"
    save_days({DAY.isoformat(): (MenuLine("Солянка мясная", 50, time(12, 0), "Обед", DAY),)}, path)
    other = date(2026, 9, 29)
    save_days({other.isoformat(): (MenuLine("Рассольник", 40, time(12, 0), "Обед", other),)}, path)

    loaded = load_days(path)
    assert set(loaded) == {DAY.isoformat(), other.isoformat()}


def test_пустое_меню_сохраняется_как_дата_с_пустым_кортежем(tmp_path):
    path = tmp_path / "menu.json"
    save_days({DAY.isoformat(): ()}, path)
    loaded = load_days(path)
    assert DAY.isoformat() in loaded
    assert loaded[DAY.isoformat()] == ()


def test_нулевые_порции_пропускают_строку_но_оставляют_дату(tmp_path):
    path = tmp_path / "menu.json"
    save_days(
        {DAY.isoformat(): (MenuLine("Блюдо", 0, time(12, 0), "Обед", DAY),)},
        path,
    )
    loaded = load_days(path)
    assert DAY.isoformat() in loaded
    assert loaded[DAY.isoformat()] == ()


def test_отсутствующая_дата_отличается_от_сознательно_пустой(tmp_path):
    path = tmp_path / "menu.json"
    other = date(2026, 9, 29)
    save_days({DAY.isoformat(): ()}, path)
    loaded = load_days(path)
    assert DAY.isoformat() in loaded
    assert other.isoformat() not in loaded


def test_битые_строки_в_одном_дне_не_мешают_остальным(tmp_path):
    path = tmp_path / "menu.json"
    good = "Каша овсяная с солёным арахисом"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "days": {
                    DAY.isoformat(): [
                        {"recipe": "", "portions": 50},
                        "мусор",
                        42,
                        {"recipe": good, "portions": 0},
                        {"recipe": good, "portions": 65, "serve_at": "12:00", "meal": "Обед"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = load_days(path)
    assert DAY.isoformat() in loaded
    assert len(loaded[DAY.isoformat()]) == 1
    assert loaded[DAY.isoformat()][0].recipe == good
    assert loaded[DAY.isoformat()][0].portions == 65


def test_пустое_меню_одной_даты_не_затирает_другую(tmp_path):
    path = tmp_path / "menu.json"
    other = date(2026, 9, 26)
    other_menu = (MenuLine("Солянка мясная", 50, time(12, 0), "Обед", other),)
    save_days({other.isoformat(): other_menu, DAY.isoformat(): ()}, path)
    loaded = load_days(path)
    assert set(loaded) == {other.isoformat(), DAY.isoformat()}
    assert loaded[other.isoformat()][0].recipe == "Солянка мясная"
    assert loaded[DAY.isoformat()] == ()


def test_отсутствующий_или_битый_файл_даёт_пустой_словарь(tmp_path):
    assert load_days(tmp_path / "нет.json") == {}
    broken = tmp_path / "битый.json"
    broken.write_text("{не json", encoding="utf-8")
    assert load_days(broken) == {}


def test_plan_to_menu_разворачивает_день_на_дату():
    menu = plan_to_menu(WEEK_PLAN, "пн", DAY)
    assert len(menu) == 6
    assert all(line.day == DAY for line in menu)
    assert all(line.meal in ("Завтрак", "Обед") for line in menu)
    assert any(line.recipe == "Каша рисовая" for line in menu)


def test_plan_to_menu_неизвестный_день_это_ошибка():
    with pytest.raises(ValueError):
        plan_to_menu(WEEK_PLAN, "воскресенье", DAY)