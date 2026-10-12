"""Проверки чистого масштабирования меню (kitchen/web/scaling.py).

Правило округления — математическое: дробная часть 0,5 и более вверх,
меньше 0,5 — вниз; результат не меньше 1 порции.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from kitchen.models import MenuLine
from kitchen.seed import demo_data
from kitchen.web.scaling import (
    ScaleError,
    scaled_portion,
    scaled_preview,
    scale_menu,
    validate_factor,
)

DAY = date(2026, 9, 28)


def _line(recipe: str = "Блюдо", portions: int = 50, note: str = "",
          meal: str = "Обед") -> MenuLine:
    return MenuLine(recipe, portions, time(12, 0), meal, DAY, note)


def test_масштабирование_возвращает_новые_объекты():
    menu = demo_data(DAY).menu
    result = scale_menu(menu, 2.0)

    assert tuple(result) != menu
    assert all(original is not scaled for original, scaled in zip(menu, result))
    assert [line.portions for line in menu] == [50] * 5, "исходные строки изменены"
    assert all(line.portions == 100 for line in result)


def test_округление_порций_половина_вверх():
    assert scaled_portion(3, 1.3) == 4     # 3,9
    assert scaled_portion(50, 1.3) == 65   # 65,0
    assert scaled_portion(2, 0.5) == 1     # 1,0
    assert scaled_portion(5, 0.4) == 2     # 2,0
    assert scaled_portion(7, 0.5) == 4     # 3,5 → 4


def test_результат_не_содержит_нулевых_порций():
    result = scale_menu([_line(portions=1)], 0.3)
    assert result[0].portions == 1
    result = scale_menu([_line(portions=0)], 1.0)
    assert result[0].portions == 1, "0 порций на входе не должно оставаться нулём"


@pytest.mark.parametrize("bad", [0, -1, -0.5, float("nan"), float("inf"), "много"])
def test_недопустимый_коэффициент(bad):
    with pytest.raises(ScaleError):
        validate_factor(bad)
    with pytest.raises(ScaleError):
        scale_menu([_line()], bad)


def test_коэффициент_единица_не_меняет_порции():
    line = _line(portions=37)
    result = scale_menu([line], 1.0)
    assert result[0].portions == 37


def test_кроме_порций_сохраняются_все_поля_menu_line():
    line = MenuLine(
        recipe="Каша овсяная с солёным арахисом",
        portions=50,
        serve_at=time(8, 0),
        meal="Завтрак",
        day=DAY,
        note="недельный вариант",
    )
    scaled = scale_menu([line], 2.0)[0]
    assert scaled.recipe == line.recipe
    assert scaled.meal == line.meal
    assert scaled.serve_at == line.serve_at
    assert scaled.day == line.day
    assert scaled.note == line.note
    assert scaled.portions == 100


def test_предпросмотр_показывает_пары_было_стало():
    menu = [_line("А", 50), _line("Б", 40)]
    pairs = scaled_preview(menu, 1.5)

    assert len(pairs) == 2
    for original, scaled in pairs:
        assert scaled.portions == int(original.portions * 1.5 + 0.5)
        assert scaled.recipe == original.recipe
        assert original.portions != scaled.portions