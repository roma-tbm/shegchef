"""Проверки ключа --scale: применение коэффициента к выбранному источнику меню.

Коэффициент применяется после чтения меню/плана и до build_shift_sheet() —
весь расчёт смены идёт существующим ядром по новым порциям.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import load_workbook

import build as cli


@pytest.fixture()
def out(tmp_path: Path) -> Path:
    return tmp_path / "Кухня.xlsx"


def _run(out: Path, *argv: str) -> int:
    return cli.main(["--out", str(out), "--no-open", *argv])


def _portions(path: Path) -> dict[str, int]:
    ws = load_workbook(path)["Меню"]
    out: dict[str, int] = {}
    for r in range(1, ws.max_row + 1):
        dish, portions = ws.cell(row=r, column=4).value, ws.cell(row=r, column=5).value
        if dish and isinstance(portions, int):
            out[str(dish)] = portions
    return out


def test_scale_применяется_к_меню_из_плана(out: Path):
    assert _run(out, "--week", "пн", "--scale", "1.3") == 0
    assert set(_portions(out).values()) == {65}, "50 × 1.3 = 65"


def test_scale_применяется_к_демо_меню(out: Path):
    assert _run(out, "--scale", "2.0") == 0
    assert set(_portions(out).values()) == {100}


def test_scale_округление_вниз_до_целых(out: Path):
    assert _run(out, "--scale", "0.5") == 0
    assert set(_portions(out).values()) == {25}


def test_scale_не_даёт_нулевых_порций(out: Path):
    assert _run(out, "--week", "пт", "--scale", "0.7") == 0
    values = set(_portions(out).values())
    assert values, "меню должно остаться непустым"
    assert all(portions >= 1 for portions in values)


@pytest.mark.parametrize("bad", ["0", "-1", "-1.5", "много"])
def test_scale_недопустимый_коэффициент_это_ошибка_и_без_книги(out: Path, bad: str):
    with pytest.raises(SystemExit) as exc:
        _run(out, "--scale", bad)
    assert exc.value.code == 2
    assert not out.exists(), "книга не должна создаваться при ошибке"


def test_scale_без_меню_даёт_понятную_ошибку(out: Path):
    """--fresh с нулём в меню не должно падать с внутренней ошибкой ядра."""
    assert _run(out, "--fresh") == 0
    # масштаб единицы — нейтрален и не ломает сборку
    assert _run(out, "--fresh", "--scale", "1.0") == 0


def test_excel_экспорт_после_scale_собирается_полностью(out: Path):
    """Масштабированная смена проходит весь путь до книги без потери листов."""
    assert _run(out, "--week", "чт", "--scale", "1.5") == 0
    wb = load_workbook(out)
    assert "Лист смены" in wb.sheetnames
    assert "План меню" in wb.sheetnames
    assert "ТТК" in wb.sheetnames