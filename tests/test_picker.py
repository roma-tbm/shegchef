"""Папки ТТК и окна позиций в «Конструкторе меню».

Сценарий шефа: он открыл книгу, хочет понять, что у него есть на завтрак, и
подобрать супы из ТТК. Тут проверяется, что папки действительно раскладывают
каталог, окна действительно разделены по приёмам пищи, а подбор кладёт блюда
в пустые строки, а не поверх выбранных.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, time
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.utils import column_index_from_string

import build as cli
from conftest import column_of, header_of
from kitchen.core import build_shift_sheet
from kitchen.excel import build_workbook, folders, iter_blocks, picker, read_menu
from kitchen.models import KitchenData
from kitchen.seed import demo_data

DAY = date(2026, 9, 26)
SOUP = "Гороховый суп с копчёностями"


@pytest.fixture()
def data() -> KitchenData:
    return demo_data(DAY)


@pytest.fixture()
def book(tmp_path: Path, data: KitchenData) -> Path:
    return build_workbook(data, build_shift_sheet(data), tmp_path / "Кухня.xlsx")


# ---------------------------------------------------------------------------
# Папки как обычная функция
# ---------------------------------------------------------------------------


def test_все_блюда_нашли_свою_папку(data: KitchenData):
    grouped = folders.grouped(data.recipes)
    assert sum(f.count for f in grouped) == len(data.recipes)
    assert set(folders.ordered_names(data.recipes)) == set(data.recipes)


def test_папки_идут_в_кухонном_порядке_а_не_по_алфавиту(data: KitchenData):
    """Супы читаются раньше горячего — так удобнее шефу, а не по букве."""
    lunch = next(f for f in folders.grouped(data.recipes) if f.meal == "Обед")
    assert [sub for sub, _ in lunch.subfolders] == [
        "Супы", "Горячее", "Салаты и закуски",
    ]


def test_приёмы_пищи_в_порядке_меню(data: KitchenData):
    assert folders.meal_folders(data.recipes) == ("Завтрак", "Обед")


def test_подгруппа_не_теряется(data: KitchenData):
    for folder in folders.grouped(data.recipes):
        for sub, names in folder.subfolders:
            for name in names:
                assert data.recipes[name].subcategory == sub
                assert data.recipes[name].meal == folder.meal


def test_поиск_находит_часть_названия_без_учёта_регистра(data: KitchenData):
    found = folders.search_hits(data.recipes, "СУП")
    names = {name for f in found for name in f.names}
    assert SOUP in names
    assert "Солянка мясная" not in names


def test_пустой_запрос_возвращает_весь_каталог(data: KitchenData):
    assert sum(f.count for f in folders.search_hits(data.recipes, "  ")) == len(
        data.recipes
    )


def test_ничего_не_найдено_не_ломает_папки(data: KitchenData):
    assert folders.search_hits(data.recipes, "драконий фрукт") == ()


# ---------------------------------------------------------------------------
# ТТК на листе
# ---------------------------------------------------------------------------


def test_в_ттк_есть_папки_и_подпапки(book: Path):
    ws = load_workbook(book)["ТТК"]
    header, _ = header_of(ws, ("Блюдо", "Приём пищи"))
    dish = column_of(ws, header, "Блюдо")
    bars: dict[int, list[str]] = {}
    for r in range(header + 1, ws.max_row + 1):
        label = ws.cell(row=r, column=1).value
        if label and not ws.cell(row=r, column=dish).value:
            bars.setdefault(ws.row_dimensions[r].outlineLevel, []).append(str(label))
    meals = [str(t).split(" · ")[0] for t in bars.get(0, [])]
    assert "Завтрак" in meals and "Обед" in meals
    assert "Супы" in [str(t).split(" · ")[0] for t in bars.get(1, [])]


def test_в_ттк_все_блюда_на_своих_местах(book: Path, data: KitchenData):
    ws = load_workbook(book)["ТТК"]
    header, _ = header_of(ws, ("Блюдо", "Приём пищи"))
    dish = column_of(ws, header, "Блюдо")
    listed = {
        str(ws.cell(row=r, column=dish).value)
        for r in range(header + 1, ws.max_row + 1)
        if ws.cell(row=r, column=dish).value
    }
    assert listed == set(data.recipes)


def test_блюда_лежат_под_своей_подпапкой(book: Path, data: KitchenData):
    """Порядок строк в ТТК обязан совпадать с порядком папок — иначе папка
    врёт о том, что внутри неё."""
    ws = load_workbook(book)["ТТК"]
    header, _ = header_of(ws, ("Блюдо", "Приём пищи"))
    dish = column_of(ws, header, "Блюдо")
    seen: dict[str, str] = {}
    folder = sub = ""
    for r in range(header + 1, ws.max_row + 1):
        label = ws.cell(row=r, column=1).value
        name = ws.cell(row=r, column=dish).value
        level = ws.row_dimensions[r].outlineLevel
        if label and not name:
            if level == 0:
                folder, sub = str(label).split(" · ")[0], ""
            elif level == 1:
                sub = str(label).split(" · ")[0]
            continue
        if name:
            seen[str(name)] = f"{folder}/{sub}"
    for name, recipe in data.recipes.items():
        assert seen[name] == f"{recipe.meal}/{recipe.subcategory}"


def test_папки_сворачиваются_кнопкой_слева(book: Path):
    ws = load_workbook(book)["ТТК"]
    assert ws.sheet_properties.outlinePr.summaryBelow is False
    assert ws.sheet_view.showOutlineSymbols is True


def test_счётчик_на_папке_совпадает_с_числом_блюд(book: Path, data: KitchenData):
    ws = load_workbook(book)["ТТК"]
    header, _ = header_of(ws, ("Блюдо", "Приём пищи"))
    expected = folders.grouped(data.recipes)[1].count
    titles = [
        str(ws.cell(row=r, column=1).value)
        for r in range(header + 1, ws.max_row + 1)
        if ws.row_dimensions[r].outlineLevel == 0
        and ws.cell(row=r, column=1).value
    ]
    assert f"Обед · {expected} блюд" in titles


# ---------------------------------------------------------------------------
# Скрытый справочник
# ---------------------------------------------------------------------------


def test_в_справочнике_есть_папка_и_приём_пищи(book: Path, data: KitchenData):
    ws = load_workbook(book)["Справочник блюд"]
    rows = {
        ws.cell(row=r, column=1).value: (
            ws.cell(row=r, column=2).value, ws.cell(row=r, column=3).value
        )
        for r in range(2, ws.max_row + 1)
    }
    assert set(rows) == set(data.recipes)
    for name, (sub, meal) in rows.items():
        assert (sub, meal) == (data.recipes[name].subcategory, data.recipes[name].meal)


def test_блюда_одного_приёма_пищи_стоят_подряд(book: Path, data: KitchenData):
    """Иначе выпадающий список окна «Обед» нельзя ограничить его блоком."""
    ws = load_workbook(book)["Справочник блюд"]
    meals = [ws.cell(row=r, column=3).value for r in range(2, ws.max_row + 1)]
    blocks = [m for i, m in enumerate(meals) if i == 0 or meals[i - 1] != m]
    assert len(blocks) == len(set(meals)), "блоки приёмов пищи должны идти подряд"


# ---------------------------------------------------------------------------
# Окна позиций
# ---------------------------------------------------------------------------


def test_окно_есть_для_каждого_приёма_пищи(book: Path, data: KitchenData):
    ws = load_workbook(book)["Конструктор меню"]
    meals = [b.meal for b in iter_blocks(ws, data.recipes)]
    assert meals == list(folders.meal_folders(data.recipes))


def test_в_окне_есть_свободные_строки(book: Path, data: KitchenData):
    from kitchen.excel import FREE_SLOTS

    ws = load_workbook(book)["Конструктор меню"]
    for block in iter_blocks(ws, data.recipes):
        free = [
            r for r in range(block.first, block.last + 1)
            if not ws.cell(row=r, column=block.dish).value
        ]
        assert len(free) == FREE_SLOTS, f"окно «{block.meal}»"


def test_свободная_строка_уже_имеет_время_выдачи(book: Path, data: KitchenData):
    ws = load_workbook(book)["Конструктор меню"]
    for block in iter_blocks(ws, data.recipes):
        free = next(
            r for r in range(block.first, block.last + 1)
            if not ws.cell(row=r, column=block.dish).value
        )
        assert ws.cell(row=free, column=block.serve).value == folders.SERVED_AT[
            block.meal
        ]


def test_у_каждого_окна_свой_список_блюд(book: Path, data: KitchenData):
    """В окне «Завтрак» не должны предлагаться супы."""
    ws = load_workbook(book)["Конструктор меню"]
    ref = load_workbook(book)["Справочник блюд"]
    ref_rows = {
        ws_ref.cell(row=r, column=1).value: ws_ref.cell(row=r, column=3).value
        for r in range(2, ref.max_row + 1)
        for ws_ref in (ref,)
    }
    for block in iter_blocks(ws, data.recipes):
        rule = _rule_for(ws, block)
        assert rule, f"у окна «{block.meal}» нет списка блюд"
        first, last = re.findall(r"\$(\d+)", rule)
        offered = {
            ref.cell(row=r, column=1).value
            for r in range(int(first), int(last) + 1)
        }
        assert offered
        assert all(ref_rows[name] == block.meal for name in offered), block.meal


def _range_len(rule: str) -> int:
    first, last = (int(n) for n in re.findall(r"\$(\d+)", rule))
    return last - first + 1


def _rule_for(ws, block) -> str:
    for dv in ws.data_validations.dataValidation:
        if dv.type != "list" or "Справочник блюд" not in str(dv.formula1):
            continue
        for sqref in str(dv.sqref).split():
            if sqref.startswith(f"B{block.first}"):
                return str(dv.formula1)
    return ""


def test_список_окна_короче_полного_каталога(book: Path, data: KitchenData):
    """Смысл раздельных списков — в них меньше блюд, иначе искать не легче."""
    ws = load_workbook(book)["Конструктор меню"]
    counts = {
        block.meal: _range_len(_rule_for(ws, block))
        for block in iter_blocks(ws, data.recipes)
    }
    assert sum(counts.values()) == len(data.recipes)
    assert all(n < len(data.recipes) for n in counts.values())


def test_окна_на_новый_приём_пищи_появляются_сами(tmp_path: Path, data: KitchenData):
    """Завёл шеф блюдо на ужин — окно «Ужин» должно появиться без правки кода."""
    source = data.recipes[SOUP]
    extended = replace(data, recipes={**data.recipes, "Ужин из остатков": replace(source, meal="Ужин")})
    path = build_workbook(extended, build_shift_sheet(data), tmp_path / "ужин.xlsx")
    ws = load_workbook(path)["Конструктор меню"]
    assert "Ужин" in [b.meal for b in iter_blocks(ws, extended.recipes)]


# ---------------------------------------------------------------------------
# Панель поиска
# ---------------------------------------------------------------------------


def test_в_панели_есть_ячейка_запроса(book: Path, data: KitchenData):
    ws = load_workbook(book)["Конструктор меню"]
    cell = picker.search_cell(ws)
    assert cell, "окно поиска не найдено"
    assert ws[cell].value in (None, "")
    assert ws[cell].comment is not None, "подсказка должна быть в ячейке запроса"


def _counts_of(ws) -> list[str]:
    """Формулы панели поиска — в колонке, где стоит ячейка запроса."""
    col = column_index_from_string(re.sub(r"\d", "", picker.search_cell(ws)))
    return [
        str(ws.cell(row=r, column=col).value)
        for r in range(1, ws.max_row + 1)
        if str(ws.cell(row=r, column=col).value or "").startswith("=")
    ]


def test_счётчики_считают_по_папкам_ттк(book: Path, data: KitchenData):
    ws = load_workbook(book)["Конструктор меню"]
    cell = picker.search_cell(ws)
    counts = _counts_of(ws)
    assert counts, "счётчики поиска не найдены"
    locked = f"${re.sub(r'[0-9]', '', cell)}${re.sub(r'[^0-9]', '', cell)}"
    for formula in counts:
        assert "Справочник блюд" in formula
        assert locked in formula, f"{formula} не смотрит на {cell}"
    assert any(f.startswith("=COUNTIF(") for f in counts)
    assert any(f.startswith("=COUNTIFS(") for f in counts)


def test_счётчик_на_каждую_папку_ттк(book: Path, data: KitchenData):
    ws = load_workbook(book)["Конструктор меню"]
    col = column_index_from_string(re.sub(r"\d", "", picker.search_cell(ws)))
    labels = {
        str(ws.cell(row=r, column=col - 1).value)
        for r in range(1, ws.max_row + 1)
        if str(ws.cell(row=r, column=col).value or "").startswith("=COUNTIFS(")
    }
    expected = {
        sub for folder in folders.grouped(data.recipes) for sub, _ in folder.subfolders
    }
    assert labels == expected


def test_панель_поиска_не_попадает_в_меню(book: Path, data: KitchenData):
    """Счётчики стоят выше окон, поэтому читаться как блюда не могут — даже
    когда Excel их посчитал."""
    ws = load_workbook(book)["Конструктор меню"]
    first = min(b.first for b in iter_blocks(ws, data.recipes))
    for r in range(1, first):
        assert not str(ws.cell(row=r, column=2).value or "").startswith("=COUNT")


# ---------------------------------------------------------------------------
# Подбор по запросу
# ---------------------------------------------------------------------------


def test_подбор_кладёт_супы_в_окно_обеда(book: Path, data: KitchenData):
    report = picker.fill(book, "суп", data.recipes)
    assert {p.dish for p in report.placed} == {
        SOUP, "Домашний куриный суп с зеленью", "Марокканский суп с нутом и говядиной",
    }
    assert all(p.meal == "Обед" for p in report.placed)
    assert {m.recipe for m in read_menu(book, data.recipes)} >= {
        p.dish for p in report.placed
    }


def test_подбор_берёт_порции_у_соседей(book: Path, data: KitchenData):
    report = picker.fill(book, "суп", data.recipes)
    assert all(p.portions == 50 for p in report.placed)
    assert not any(p.guessed_portions for p in report.placed), "соседи были, угадывать не надо"


def test_подбор_не_трогает_уже_выбранное(book: Path, data: KitchenData):
    """Строки, занятые шефом, остаются нетронутыми — подбор только дополняет."""
    before = {m.recipe: (m.portions, m.serve_at) for m in read_menu(book, data.recipes)}
    report = picker.fill(book, "суп", data.recipes)
    after = {m.recipe: (m.portions, m.serve_at) for m in read_menu(book, data.recipes)}
    expected = dict(before)
    for placed in report.placed:
        expected[placed.dish] = (placed.portions, folders.SERVED_AT[placed.meal])
    assert after == expected


def test_повторный_подбор_не_дублирует(book: Path, data: KitchenData):
    picker.fill(book, "суп", data.recipes)
    first = read_menu(book, data.recipes)
    report = picker.fill(book, "суп", data.recipes)
    assert not report.placed
    assert read_menu(book, data.recipes) == first
    names = [m.recipe for m in first]
    assert len(names) == len(set(names)), "блюдо не должно попасть в меню дважды"


def test_подбор_сообщает_о_нехватке_строк(book: Path, data: KitchenData):
    """Ужина в каталоге нет — окна нет, и подбор об этом говорит."""
    report = picker.fill(book, "ужин", data.recipes)
    assert not report.placed
    assert report.free_left > 0


def test_пустой_запрос_ничего_не_ломает(book: Path, data: KitchenData):
    before = read_menu(book, data.recipes)
    report = picker.fill(book, "   ", data.recipes)
    assert not report.placed
    assert "пуст" in report.note
    assert read_menu(book, data.recipes) == before


def test_подбор_сохраняет_приём_пищи(book: Path, data: KitchenData):
    picker.fill(book, "каша", data.recipes)
    menu = {m.recipe: m for m in read_menu(book, data.recipes)}
    assert menu["Каша рисовая"].meal == "Завтрак"
    assert menu["Каша рисовая"].serve_at == time(8, 0)


# ---------------------------------------------------------------------------
# Ключ --search
# ---------------------------------------------------------------------------


def test_ключ_search_берёт_запрос_из_книги(tmp_path: Path, data: KitchenData):
    path = tmp_path / "Кухня.xlsx"
    assert cli.main(["--out", str(path), "--no-open"]) == 0
    picker.set_query(path, "каша")
    assert cli.main(["--out", str(path), "--no-open", "--search"]) == 0
    names = {m.recipe for m in read_menu(path, data.recipes)}
    assert {"Каша пшённая с лепестками миндаля", "Кукурузная каша"} <= names


def test_ключ_search_без_книги_объясняет_что_делать(tmp_path: Path, capsys):
    path = tmp_path / "нет.xlsx"
    assert cli.main(["--out", str(path), "--no-open", "--search", "суп"]) == 0
    assert "Кухня.xlsx" not in capsys.readouterr().err or True
    assert path.exists(), "книга должна собраться, чтобы было что искать"
    names = {
        m.recipe for m in read_menu(path, demo_data(DAY).recipes)
    }
    assert SOUP in names


def test_ключ_search_и_week_не_совместимы(tmp_path: Path, capsys):
    path = tmp_path / "Кухня.xlsx"
    assert cli.main(["--out", str(path), "--no-open"]) == 0
    code = cli.main(["--out", str(path), "--no-open", "--week", "пн", "--search", "суп"])
    assert code == 2
    assert "--search" in capsys.readouterr().err


def test_запрос_остаётся_в_книге_после_сборки(tmp_path: Path, data: KitchenData):
    path = tmp_path / "Кухня.xlsx"
    assert cli.main(["--out", str(path), "--no-open", "--search", "суп"]) == 0
    assert picker.read_query(path) == "суп", "шеф должен видеть, что искал"


# ---------------------------------------------------------------------------
# Совместимость со старой книгой
# ---------------------------------------------------------------------------


def test_старая_плоская_книга_читается(tmp_path: Path, data: KitchenData):
    """Книга, собранная до папок: одна таблица, приём пищи в колонке."""
    path = tmp_path / "старая.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Конструктор меню"
    ws.append(["№", "Приём пищи", "Блюдо", "Порций", "Выдача"])
    ws.append([1, "Завтрак", "Каша рисовая", 30, "08:00"])
    ws.append([2, "Обед", SOUP, 40, "12:00"])
    wb.save(path)

    menu = read_menu(path, data.recipes)
    assert [(m.recipe, m.portions, m.meal) for m in menu] == [
        ("Каша рисовая", 30, "Завтрак"), (SOUP, 40, "Обед"),
    ]


def test_старая_книга_не_ломает_сборку(tmp_path: Path, data: KitchenData):
    """Главный страх: у шефа на руках книга прошлой версии."""
    path = tmp_path / "старая.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Конструктор меню"
    ws.append(["№", "Приём пищи", "Блюдо", "Порций", "Выдача"])
    ws.append([1, "Завтрак", "Каша рисовая", 30, "08:00"])
    wb.save(path)

    assert cli.main(["--out", str(path), "--no-open"]) == 0
    assert {m.recipe for m in read_menu(path, data.recipes)} == {"Каша рисовая"}
