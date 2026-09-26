"""Проверки ключа --week: день недели превращается в полноценную смену.

Это третий пользовательский сценарий: «а что бы я приготовил в среду?».
Проверяется не только разворот дня, но и то, что ручная правка плана
в книге не теряется при пересборке.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

import build as cli
from kitchen.excel import iter_blocks
from kitchen.menu_week import WEEKDAYS

DAY = date(2026, 9, 26)


@pytest.fixture()
def out(tmp_path: Path) -> Path:
    return tmp_path / "Кухня.xlsx"


def _run(out: Path, *argv: str) -> int:
    return cli.main(["--out", str(out), "--no-open", *argv])


def _menu_portions(path: Path) -> dict[str, int]:
    ws = load_workbook(path)["Меню"]
    out: dict[str, int] = {}
    for r in range(1, ws.max_row + 1):
        dish, portions = ws.cell(row=r, column=4).value, ws.cell(row=r, column=5).value
        if dish and isinstance(portions, int):
            out[str(dish)] = portions
    return out


# ---------------------------------------------------------------------------
# Разворот дня
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("day", WEEKDAYS)
def test_каждый_день_недели_собирается(out: Path, day: str):
    assert _run(out, "--week", day) == 0
    assert _menu_portions(out), f"{day}: меню пустое"


def test_неизвестный_день_не_падает_и_не_пишет_книгу(out: Path):
    assert _run(out, "--week", "суббота") == 2
    assert not out.exists(), "книга не должна перезаписываться при ошибке"


def test_пустой_выход_остаётся_пустым(out: Path):
    _run(out, "--week", "пн")
    before = out.stat().st_mtime_ns
    assert _run(out, "--week", "суббота") == 2
    assert out.stat().st_mtime_ns == before


def test_дата_меняет_шапку_книги(out: Path):
    assert _run(out, "--week", "пн", "--day", "01.10.2026") == 0
    ws = load_workbook(out)["Лист смены"]
    assert "01.10.2026" in str(ws.cell(row=1, column=1).value)


# ---------------------------------------------------------------------------
# Приоритет источников
# ---------------------------------------------------------------------------


def test_fresh_берёт_план_из_кода_а_не_из_книги(out: Path):
    """Правка книги не должна «залипать», если шеф её откатил."""
    _run(out, "--week", "пн")
    _set_portions(out, "Понедельник", 33)
    assert _run(out, "--week", "пн") == 0
    assert set(_menu_portions(out).values()) == {33}, "правка книги не подхватилась"

    assert _run(out, "--week", "пн", "--fresh") == 0
    assert set(_menu_portions(out).values()) == {50}, "--fresh не вернул заготовку"


def test_меню_из_книги_имеет_приоритет_над_демо(out: Path):
    """Смена дня недели не должна затирать то, что шеф написал руками."""
    _run(out)
    _write_constructor(out, "Салат из квашеной капусты и зелёного горошка", 17)
    assert _run(out) == 0
    assert _menu_portions(out)["Салат из квашеной капусты и зелёного горошка"] == 17


def test_week_перебивает_menu(out: Path):
    """--week и --menu несовместимы: день недели важнее, но молчать нельзя."""
    _run(out, "--week", "пн", "--menu", "999")
    assert set(_menu_portions(out).values()) == {50}


def test_день_недели_становится_текущей_сменой(out: Path):
    """После --week понедельник живёт в «Конструкторе меню» — и обычный запуск
    продолжает его. Так и задумано: конструктор отвечает за текущую смену.
    Демо-меню возвращается только через --fresh.
    """
    _run(out, "--week", "пн")
    assert _run(out) == 0
    assert len(_menu_portions(out)) == 6, "понедельник должен удержаться в конструкторе"

    assert _run(out, "--fresh") == 0
    assert len(_menu_portions(out)) == 5


# ---------------------------------------------------------------------------
# Ручные правки плана
# ---------------------------------------------------------------------------


def _plan_rows(path: Path) -> list[tuple[int, str, str]]:
    ws = load_workbook(path)["План меню"]
    header = next(
        r for r in range(1, ws.max_row + 1)
        if ws.cell(row=r, column=2).value == "День недели"
    )
    return [
        (r, str(ws.cell(row=r, column=2).value), str(ws.cell(row=r, column=3).value))
        for r in range(header + 1, ws.max_row + 1)
        if ws.cell(row=r, column=3).value
    ]


def _set_portions(path: Path, day: str, portions: int) -> None:
    wb = load_workbook(path)
    ws = wb["План меню"]
    for r, row_day, _ in _plan_rows(path):
        if row_day == day:
            ws.cell(row=r, column=4, value=portions)
    wb.save(path)


def _write_constructor(path: Path, dish: str, portions: int) -> None:
    """Шеф правит окно позиций: находит блюдо и меняет порции."""
    wb = load_workbook(path)
    ws = wb["Конструктор меню"]
    for block in iter_blocks(ws):
        if block.dish is None or block.portions is None:
            continue
        for r in range(block.first, block.last + 1):
            if ws.cell(row=r, column=block.dish).value == dish:
                ws.cell(row=r, column=block.portions, value=portions)
    wb.save(path)


def test_правка_порций_в_плане_доходит_до_меню(out: Path):
    _run(out, "--week", "пн")
    _set_portions(out, "Понедельник", 33)
    assert _run(out, "--week", "пн") == 0
    assert set(_menu_portions(out).values()) == {33}


def test_правка_одного_дня_не_трогает_другие(out: Path):
    _run(out, "--week", "пн")
    _set_portions(out, "Пятница", 12)
    assert _run(out, "--week", "пн") == 0
    assert set(_menu_portions(out).values()) == {50}

    assert _run(out, "--week", "пт") == 0
    assert set(_menu_portions(out).values()) == {12}


def test_убранное_из_плана_блюдо_не_попадает_в_смену(out: Path):
    """Ноль порций в плане — это «убрали», а не «посчитать как есть»."""
    assert _run(out, "--week", "пн") == 0
    wb = load_workbook(out)
    ws = wb["План меню"]
    first = _plan_rows(out)[0]
    ws.cell(row=first[0], column=4, value=0)
    wb.save(out)

    assert _run(out, "--week", "пн") == 0
    assert first[2] not in _menu_portions(out)
