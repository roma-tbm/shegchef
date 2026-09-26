"""Слой представления: превращает посчитанную смену в книгу Excel."""

from kitchen.excel import folders, picker
from kitchen.excel.builder import (
    CONSTRUCTOR_COLS,
    FREE_SLOTS,
    MENU_DISH_COL,
    RECIPE_REF_SHEET,
    SEARCH_LABEL,
    build_workbook,
)
from kitchen.excel.menu_io import (
    MENU_SHEET,
    PLAN_SHEET,
    Block,
    iter_blocks,
    normalize_weekday,
    plan_for,
    read_menu,
    read_plan,
)

__all__ = [
    "CONSTRUCTOR_COLS",
    "FREE_SLOTS",
    "MENU_DISH_COL",
    "MENU_SHEET",
    "PLAN_SHEET",
    "RECIPE_REF_SHEET",
    "SEARCH_LABEL",
    "Block",
    "build_workbook",
    "folders",
    "iter_blocks",
    "normalize_weekday",
    "picker",
    "plan_for",
    "read_menu",
    "read_plan",
]
