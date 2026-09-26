"""Слой представления: превращает посчитанную смену в книгу Excel."""

from kitchen.excel.builder import build_workbook
from kitchen.excel.menu_io import MENU_SHEET, read_menu

__all__ = ["MENU_SHEET", "build_workbook", "read_menu"]
