"""Папки ТТК: как блюда разложены по приёмам пищи и подгруппам.

Одна и та же группировка нужна трём местам, и расходиться им нельзя:

* лист «ТТК» — папки с заголовками и сворачиванием;
* скрытый «Справочник блюд» — чтобы блюда одного приёма пищи стояли подряд
  и на них можно было повесить выпадающий список;
* «Конструктор меню» — окно позиций на каждый приём пищи.

Поэтому порядок папок живёт здесь, а остальные листы берут его отсюда.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import time

from kitchen.models import Recipe

MEAL_ORDER = ("Завтрак", "Обед", "Ужин", "Перекус", "Банкет")
"""Приёмы пищи в порядке появления в меню. Новые приёмы встанут в конец."""

SUB_ORDER = (
    "Супы",
    "Горячее",
    "Салаты и закуски",
    "Закуски",
    "Каши",
    "Молочное и яйца",
    "Выпечка и тосты",
    "Выпечка",
    "Напитки",
    "Десерты",
)
"""Подгруппы в кухонном порядке: от бульона до сладкого, не по алфавиту."""

SERVED_AT = {
    "Завтрак": time(8, 0),
    "Обед": time(12, 0),
    "Ужин": time(18, 0),
    "Перекус": time(11, 0),
}
"""Время выдачи по умолчанию — им заполняются свободные слоты окон.

Именно time, а не текст: иначе проверка ввода «ЧЧ:ММ» считает такую ячейку
некорректной, а сортировка по времени встаёт не по времени.
"""


@dataclass(frozen=True)
class DishFolder:
    """Папка ТТК верхнего уровня: один приём пищи и его подпапки."""

    meal: str
    subfolders: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def count(self) -> int:
        return sum(len(names) for _, names in self.subfolders)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for _, group in self.subfolders for name in group)


def _order(values: Iterable[str], preferred: tuple[str, ...]) -> tuple[str, ...]:
    """Сначала знакомые значения в заданном порядке, остальные — по алфавиту."""
    unique = set(values)
    known = [v for v in preferred if v in unique]
    rest = sorted(v for v in unique if v not in preferred)
    return tuple(known + rest)


def meal_folders(recipes: Mapping[str, Recipe]) -> tuple[str, ...]:
    """Приёмы пищи, которые реально есть в каталоге."""
    return _order((r.meal for r in recipes.values()), MEAL_ORDER)


def sub_folders(recipes: Mapping[str, Recipe], meal: str) -> tuple[str, ...]:
    """Подгруппы внутри приёма пищи."""
    return _order(
        (r.subcategory for r in recipes.values() if r.meal == meal), SUB_ORDER
    )


def grouped(recipes: Mapping[str, Recipe]) -> tuple[DishFolder, ...]:
    """Каталог, разложенный по папкам: приём пищи → подгруппа → блюда.

    Блюда внутри подпапки сортируются по алфавиту, чтобы перестановка строк
    в исходных данных не тасовала выпадающий список у пользователя.
    """
    folders: list[DishFolder] = []
    for meal in meal_folders(recipes):
        subs: list[tuple[str, tuple[str, ...]]] = []
        for sub in sub_folders(recipes, meal):
            names = tuple(
                sorted(
                    name
                    for name, r in recipes.items()
                    if r.meal == meal and r.subcategory == sub
                )
            )
            if names:
                subs.append((sub, names))
        if subs:
            folders.append(DishFolder(meal, tuple(subs)))
    return tuple(folders)


def ordered_names(recipes: Mapping[str, Recipe]) -> tuple[str, ...]:
    """Все блюда в порядке папок — так, как их видит шеф в ТТК."""
    return tuple(name for folder in grouped(recipes) for name in folder.names)


def meal_of(recipes: Mapping[str, Recipe], name: str) -> str:
    recipe = recipes.get(name)
    return recipe.meal if recipe else ""


def search_hits(recipes: Mapping[str, Recipe], query: str) -> tuple[DishFolder, ...]:
    """Папки, в которых название блюда содержит запрос.

    Поиск без регулярных выражений и без учёта регистра — так же, как
    работает COUNTIF в самой книге, чтобы список на экране и подбор при
    пересборке не расходились.
    """
    needle = query.strip().lower()
    if not needle:
        return grouped(recipes)
    out: list[DishFolder] = []
    for folder in grouped(recipes):
        subs = tuple(
            (sub, tuple(n for n in names if needle in n.lower()))
            for sub, names in folder.subfolders
        )
        subs = tuple((sub, names) for sub, names in subs if names)
        if subs:
            out.append(DishFolder(folder.meal, subs))
    return tuple(out)


__all__ = [
    "MEAL_ORDER",
    "SERVED_AT",
    "SUB_ORDER",
    "DishFolder",
    "grouped",
    "meal_folders",
    "meal_of",
    "ordered_names",
    "search_hits",
    "sub_folders",
]
