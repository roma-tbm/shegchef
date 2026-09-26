"""Чтение меню обратно из книги.

Пользователь правит лист «Конструктор меню» руками, поэтому чтение должно
быть терпимым к мелочам: заголовок не в первой строке, блюдо набрано с
пробелами, порции — текстом, а половина строк может быть пустой.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

from openpyxl import load_workbook

from kitchen.models import MenuLine

MENU_SHEET = "Конструктор меню"
HEADER_WINDOW = 30
"""Сколько первых строк просматриваем в поисках заголовка."""

MEAL_ORDER = ("Завтрак", "Обед", "Ужин", "Перекус", "Банкет")
DEFAULT_MEAL = "Обед"
DEFAULT_SERVE = time(12, 0)

# Синонимы заголовков: пользователь мог переименовать колонку.
DISH_ALIASES = ("Блюдо", "Наименование", "Блюдо/наименование")
PORTIONS_ALIASES = ("Порций", "Порции", "Порций, чел")
SERVE_ALIASES = ("Выдача", "Время выдачи", "К выдаче")
MEAL_ALIASES = ("Приём пищи", "Прием пищи", "Тип приёма пищи", "Приём")


def _index(cols: dict[str, int], names: tuple[str, ...]) -> int | None:
    """Номер колонки по первому подходящему заголовку-синониму."""
    for name in names:
        if name in cols:
            return cols[name]
    return None


def _parse_time(value, default: time) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%H:%M", "%H.%M", "%H:%M:%S"):
            try:
                from datetime import datetime

                return datetime.strptime(text, fmt).time()
            except ValueError:
                continue
    return default


def read_menu(path: Path, recipes: dict[str, object] | None = None) -> tuple[MenuLine, ...]:
    """Читает меню из книги. Пустой лист — это пустое меню, а не ошибка."""
    wb = load_workbook(path, data_only=True)
    try:
        if MENU_SHEET not in wb.sheetnames:
            return ()
        return _parse_sheet(wb[MENU_SHEET], recipes or {})
    finally:
        wb.close()


def _parse_sheet(ws, recipes: dict[str, object]) -> tuple[MenuLine, ...]:
    header_row, cols = _find_header(ws)
    if header_row is None:
        return ()

    dish_col = _index(cols, DISH_ALIASES)
    portions_col = _index(cols, PORTIONS_ALIASES)
    if dish_col is None or portions_col is None:
        return ()
    serve_col = _index(cols, SERVE_ALIASES)
    meal_col = _index(cols, MEAL_ALIASES)

    rows: list[MenuLine] = []
    for row in ws.iter_rows(min_row=header_row + 1):
        values = [c.value for c in row]
        dish = values[dish_col]
        if dish is None or not str(dish).strip():
            continue
        dish = str(dish).strip()

        if recipes and dish not in recipes:
            _warn(f"пропущено «{dish}» — нет такого блюда в ТТК")
            continue

        portions = _parse_int(values[portions_col])
        if portions is None or portions <= 0:
            # Ноль порций — это «блюдо убрали из меню», а не ошибка ввода.
            continue

        meal = str(values[meal_col]).strip() if meal_col and values[meal_col] else ""
        if meal not in MEAL_ORDER:
            meal = DEFAULT_MEAL

        serve = (
            _parse_time(values[serve_col], DEFAULT_SERVE)
            if serve_col
            else DEFAULT_SERVE
        )
        rows.append(MenuLine(dish, portions, serve, meal, date.today()))
    return tuple(rows)


def _find_header(ws) -> tuple[int | None, dict[str, int]]:
    for row in ws.iter_rows(min_row=1, max_row=HEADER_WINDOW):
        labels = [str(c.value).strip() if c.value else "" for c in row]
        if any(n in labels for n in DISH_ALIASES) and any(
            n in labels for n in PORTIONS_ALIASES
        ):
            return row[0].row, {name: labels.index(name) for name in labels if name}
    return None, {}


def _parse_int(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _warn(message: str) -> None:
    import sys

    print(f"  ! {message}", file=sys.stderr)


__all__ = ["MENU_SHEET", "read_menu"]
