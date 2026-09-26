"""Общие помощники тестов: искать колонки по названию, а не по номеру.

Раскладка листов меняется (папки в ТТК, окна в «Конструкторе меню»), и тест,
зашитый на «колонку 10», ломается без причины. Здесь всё ищется по заголовку.
"""

from __future__ import annotations

DISH = "Блюдо"
PORTIONS = "Порций"


def header_of(ws, names: tuple[str, ...] = (DISH, PORTIONS),
              window: int = 40) -> tuple[int, dict[str, int]]:
    """Первая строка заголовков и её колонки. Номера колонок — с единицы."""
    for row in ws.iter_rows(min_row=1, max_row=window):
        labels = [str(c.value).strip() if c.value else "" for c in row]
        if all(n in labels for n in names):
            cols = {n: labels.index(n) + 1 for n in labels if n}
            return row[0].row, cols
    raise AssertionError(f"заголовки {names} на листе «{ws.title}» не найдены")


def column_of(ws, header_row: int, name: str) -> int:
    """Номер колонки по заголовку в строке заголовков."""
    for cell in ws[header_row]:
        if cell.value and str(cell.value).strip() == name:
            return cell.column
    raise AssertionError(f"колонка «{name}» не найдена в строке {header_row}")


def row_of(ws, column: int, value: str) -> int:
    for r in range(1, ws.max_row + 1):
        if str(ws.cell(row=r, column=column).value or "").strip() == value:
            return r
    raise AssertionError(f"«{value}» в колонке {column} листа «{ws.title}» не найдено")


def values_of(ws, header_row: int, name: str) -> dict[str, object]:
    """Значения колонки, сопоставленные с блюдом из колонки «Блюдо»."""
    header, cols = header_of(ws, (name,))
    dish_col = column_of(ws, header, DISH)
    target = column_of(ws, header_row, name)
    out: dict[str, object] = {}
    for r in range(header + 1, ws.max_row + 1):
        key = ws.cell(row=r, column=dish_col).value
        if key and not str(key).strip().isdigit():
            out[str(key)] = ws.cell(row=r, column=target).value
    return out
