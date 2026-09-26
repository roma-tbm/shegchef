"""Подбор блюд из ТТК в свободные строки «Конструктора меню».

Счётчики в панели поиска живые, но вставить блюдо в ячейку формулой нельзя —
в .xlsx нет макросов. Поэтому подстановка делается при пересборке: шеф пишет
запрос, сохраняет книгу и запускает `python build.py --search`.

Что именно делает подбор:

* находит совпадения в ТТК теми же правилами, что и COUNTIF в книге — любая
  часть названия, без учёта регистра;
* раскладывает их по окнам приёмов пищи в порядке папок;
* занимает только пустые строки: то, что шеф уже выбрал, не трогает;
* повторно выбранное блюдо пропускает, чтобы не задвоить порции.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from kitchen.excel import folders
from kitchen.excel.builder import (
    MENU_DISH_COL,
    MENU_PORTIONS_COL,
    MENU_SERVE_COL,
    SEARCH_LABEL,
    SEARCH_VALUE_COL,
)
from kitchen.excel.menu_io import MENU_SHEET, iter_blocks
from kitchen.excel.styles import TIME_FMT
from kitchen.models import Recipe

FALLBACK_PORTIONS = 50
"""Порции, если в книге не нашлось ни одной заполненной строки."""


@dataclass(frozen=True)
class Placed:
    """Блюдо, поставленное в окно."""

    meal: str
    dish: str
    row: int
    portions: int
    guessed_portions: bool = False


@dataclass(frozen=True)
class Skipped:
    """Блюдо, которое не попало в меню, и почему."""

    dish: str
    reason: str


@dataclass
class Report:
    """Итог подбора — build.py печатает его шефу."""

    query: str
    placed: list[Placed] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    free_left: int = 0
    note: str = ""

    def __str__(self) -> str:
        lines = [f"Подбор по запросу «{self.query}»: {len(self.placed)} в меню"]
        for item in self.placed:
            mark = " (порции скопированы — проверьте)" if item.guessed_portions else ""
            lines.append(f"    {item.meal}: {item.dish} — {item.portions} порц.{mark}")
        for item in self.skipped:
            lines.append(f"    пропущено «{item.dish}»: {item.reason}")
        if self.free_left:
            lines.append(
                f"  Свободных строк осталось: {self.free_left}. Нужно больше — "
                f"скопируйте строку в окне или уберите лишнее блюдо."
            )
        if self.note:
            lines.append(f"  {self.note}")
        return "\n".join(lines)


def _sheet(path: Path):
    wb = load_workbook(path)
    return wb, wb[MENU_SHEET] if MENU_SHEET in wb.sheetnames else None


def search_cell(ws) -> str | None:
    """Адрес ячейки запроса на листе «Конструктор меню»."""
    for r in range(1, ws.max_row + 1):
        if str(ws.cell(row=r, column=MENU_DISH_COL).value or "").strip() == SEARCH_LABEL:
            return ws.cell(row=r, column=SEARCH_VALUE_COL).coordinate
    return None


def read_query(path: Path) -> str:
    """Что шеф написал в окне поиска. Пустая строка — запроса нет."""
    wb, ws = _sheet(path)
    try:
        if ws is None:
            return ""
        cell = search_cell(ws)
        return str(ws[cell].value).strip() if cell else ""
    finally:
        wb.close()


def set_query(path: Path, query: str) -> None:
    """Возвращает запрос в ячейку после пересборки: счётчики остаются видимыми."""
    wb, ws = _sheet(path)
    try:
        if ws is None:
            return
        cell = search_cell(ws)
        if cell:
            ws[cell] = query
            wb.save(path)
    finally:
        wb.close()


def _portions_in(ws, block, first: int, last: int) -> list[int]:
    return [
        int(ws.cell(row=r, column=MENU_PORTIONS_COL).value)
        for r in range(first, last + 1)
        if isinstance(ws.cell(row=r, column=MENU_PORTIONS_COL).value, (int, float))
        and ws.cell(row=r, column=MENU_PORTIONS_COL).value > 0
    ]


def _fill_block(
    ws, block, folder, report: Report, fallback: int
) -> None:
    """Расставляет совпадения по пустым строкам одного окна."""
    rows = list(range(block.first, block.last + 1))
    free = [r for r in rows if not ws.cell(row=r, column=block.dish).value]
    report.free_left += len(free)
    if folder is None:
        return

    in_window = {
        str(ws.cell(row=r, column=block.dish).value).strip()
        for r in rows
        if ws.cell(row=r, column=block.dish).value
    }
    known = _portions_in(ws, block, block.first, block.last)
    for dish in folder.names:
        if dish in in_window:
            report.skipped.append(Skipped(dish, "уже стоит в этом окне"))
            continue
        if not free:
            report.skipped.append(Skipped(dish, "в окне закончились строки"))
            continue
        row = free.pop(0)
        guessed = not known
        portions = known[0] if known else fallback
        ws.cell(row=row, column=MENU_DISH_COL, value=dish)
        ws.cell(row=row, column=MENU_PORTIONS_COL, value=portions)
        ws.cell(
            row=row,
            column=MENU_SERVE_COL,
            value=folders.SERVED_AT.get(block.meal, folders.SERVED_AT["Обед"]),
        ).number_format = TIME_FMT
        report.placed.append(Placed(block.meal, dish, row, portions, guessed))
    report.free_left += len(free)


def fill(path: Path, query: str, recipes: dict[str, Recipe]) -> Report:
    """Подставляет найденные блюда в пустые строки окон и сохраняет книгу."""
    needle = query.strip()
    report = Report(query=needle)
    if not needle:
        report.note = "Запрос пустой — впишите его в окно поиска и повторите."
        return report

    wb, ws = _sheet(path)
    try:
        if ws is None:
            report.note = f"В книге нет листа «{MENU_SHEET}»."
            return report

        blocks = [b for b in iter_blocks(ws, recipes) if b.meal and b.dish is not None]
        if not blocks:
            report.note = "В книге нет окон позиций — пересоберите книгу заново."
            return report

        anywhere = _portions_in(
            ws, blocks[0], blocks[0].first, blocks[-1].last
        )
        hits = {f.meal: f for f in folders.search_hits(recipes, needle)}
        for block in blocks:
            _fill_block(
                ws, block, hits.get(block.meal), report,
                anywhere[0] if anywhere else FALLBACK_PORTIONS,
            )
        wb.save(path)
    finally:
        wb.close()
    return report


__all__ = ["FALLBACK_PORTIONS", "Placed", "Report", "Skipped", "fill", "read_query",
           "search_cell", "set_query"]
