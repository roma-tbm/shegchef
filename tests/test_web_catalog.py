"""Глобальный поиск по каталогу ТТК и добавление блюда в меню.

Запрос «суп» обязан находить обеденные супы независимо от того, какой приём
пищи «выбран», — в новой схеме приём вообще не выбирается: он берётся из
карточки Recipe.meal, поэтому привязать блюдо к чужому приёму невозможно.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from kitchen.models import Recipe
from kitchen.seed import demo_data
from kitchen.web.catalog import (
    default_serve_at,
    make_menu_line,
    meal_of,
    search_recipes,
)

DAY = date(2026, 9, 28)

SOUPS = (
    "Гороховый суп с копчёностями",
    "Домашний куриный суп с зеленью",
    "Марокканский суп с нутом и говядиной",
)


@pytest.fixture()
def catalog() -> dict[str, Recipe]:
    return demo_data().recipes


def _recipe(name: str, meal: str, *, in_menu: bool = True) -> Recipe:
    return Recipe(
        name=name,
        meal=meal,
        category=meal,
        subcategory="Супы",
        shop="Горячий цех",
        prep_min=10,
        cook_min=40,
        in_menu=in_menu,
    )


def test_суп_находит_все_обеденные_супы(catalog):
    """Главный сценарий: «суп» не зависит от выбранного приёма пищи."""
    found = search_recipes(catalog, "суп")
    assert set(found) == set(SOUPS)
    assert all(meal_of(catalog, name) == "Обед" for name in found)


def test_пустой_запрос_показывает_весь_каталог(catalog):
    result = search_recipes(catalog)
    assert len(result) == len(catalog)


def test_каталог_упорядочен_завтрак_потом_обед(catalog):
    meals = [meal_of(catalog, name) for name in search_recipes(catalog)]
    rank = [0 if meal == "Завтрак" else 1 if meal == "Обед" else 2 for meal in meals]
    assert rank == sorted(rank), "завтраки должны идти раньше обедов"


def test_поиск_игнорирует_регистр(catalog):
    assert search_recipes(catalog, "СУП") == search_recipes(catalog, "суп")
    assert search_recipes(catalog, "Пюре") == search_recipes(catalog, "пюре")


def test_ничего_не_найдено_по_всему_каталогу(catalog):
    assert search_recipes(catalog, "борщ") == ()
    assert search_recipes(catalog, "  ") == search_recipes(catalog)


def test_only_in_menu_скрывает_блюда_вне_конструктора():
    recipes = dict(demo_data().recipes)
    recipes["Скрытое блюдо"] = _recipe("Скрытое блюдо", "Обед", in_menu=False)
    assert set(search_recipes(recipes, "")) == set(demo_data().recipes)
    assert "Скрытое блюдо" in search_recipes(recipes, "", only_in_menu=False)


def test_приём_пищи_берётся_из_карточки_блюда(catalog):
    """У добавления нет параметра приёма — его просто нечем подменить."""
    line = make_menu_line(
        catalog, "Гороховый суп с копчёностями", 50, time(12, 30), DAY,
    )
    assert line is not None
    assert line.recipe == "Гороховый суп с копчёностями"
    assert line.meal == "Обед"
    assert line.portions == 50
    assert line.serve_at == time(12, 30)
    assert line.day == DAY


def test_неизвестное_блюдо_или_нуль_порций_не_добавляются(catalog):
    assert make_menu_line(catalog, "Такого блюда нет", 50, time(12, 0), DAY) is None
    assert (
        make_menu_line(catalog, "Гороховый суп с копчёностями", 0, time(12, 0), DAY)
        is None
    )


def test_стандартное_время_выдачи(catalog):
    assert default_serve_at("Завтрак") == time(8, 0)
    assert default_serve_at("Обед") == time(12, 0)
    assert default_serve_at("Незнакомый приём") == time(12, 0)