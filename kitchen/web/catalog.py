"""Глобальный поиск по каталогу ТТК и чистое добавление блюда в меню.

Вкладка «Меню» ищет по всему каталогу, а не только внутри выбранного приёма
пищи: запрос «суп» находит обеденные супы сразу, даже если пользователь ещё
ничего не выбирал. Приём пищи блюда берётся из карточки Recipe.meal — привязать
блюдо к чужому приёму невозможно, «выдать блюдо чужому приёму» просто нечем.

Модуль чистый: без Streamlit и Excel, тестируется как обычный Python-модуль.
"""

from __future__ import annotations

from datetime import date, time
from typing import Mapping

from kitchen.excel.folders import SERVED_AT
from kitchen.models import MenuLine, Recipe

MEAL_ORDER = ("Завтрак", "Обед", "Ужин", "Перекус", "Банкет")
"""Приёмы пищи в кухонном порядке: завтрак → обед → …"""


def meal_order(meal: str) -> int:
    """Позиция приёма пищи в кухонном порядке; незнакомые — в самом конце."""
    try:
        return MEAL_ORDER.index(meal)
    except ValueError:
        return len(MEAL_ORDER)


def search_recipes(
    recipes: Mapping[str, Recipe],
    query: str = "",
    *,
    only_in_menu: bool = True,
) -> tuple[str, ...]:
    """Поиск по всему каталогу ТТК: подходит ли блюдо под часть названия.

    Пустой запрос возвращает весь каталог. Поиск идёт по названию без учёта
    регистра и не ограничен приёмом пищи. Результат упорядочен: сначала
    приём пищи (завтрак → обед → …), внутри — по алфавиту.
    """
    needle = query.strip().casefold()
    names = [
        name
        for name, recipe in recipes.items()
        if (not only_in_menu or recipe.in_menu is True)
        and (not needle or needle in name.casefold())
    ]
    return tuple(
        sorted(
            names,
            key=lambda name: (meal_order(recipes[name].meal), name.casefold()),
        )
    )


def meal_of(recipes: Mapping[str, Recipe], name: str) -> str:
    """Приём пищи блюда из каталога; незнакомого блюда — пустая строка."""
    recipe = recipes.get(name)
    return recipe.meal if recipe else ""


def default_serve_at(meal: str) -> time:
    """Стандартное время выдачи приёма пищи (для «Обеда» — 12:00)."""
    return SERVED_AT.get(meal, time(12, 0))


def make_menu_line(
    recipes: Mapping[str, Recipe],
    recipe: str,
    portions: int,
    serve_at: time,
    day: date,
) -> MenuLine | None:
    """Строка меню по выбранному блюду каталога.

    Приём пищи берётся из карточки Recipe.meal и не может быть подменён
    вручную. Блюда нет в каталоге или порций меньше 1 — возвращается None.
    """
    meal = meal_of(recipes, recipe)
    if not meal or portions < 1:
        return None
    return MenuLine(recipe, portions, serve_at, meal, day)


__all__ = [
    "MEAL_ORDER",
    "default_serve_at",
    "make_menu_line",
    "meal_of",
    "meal_order",
    "search_recipes",
]