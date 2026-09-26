"""Сборка книги Excel из посчитанной смены.

Один модуль — один слой представления. Доменная логика сюда не попадает:
сюда приходит уже готовый ShiftSheet и KitchenData.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path
import re
import zipfile

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from kitchen.core.shiftsheet import ShiftSheet
from kitchen.excel import folders
from kitchen.excel import styles as st
from kitchen.excel.styles import NUM_FMT, TIME_FMT
from kitchen.models import KitchenData, MenuLine, STAGES

#: формат для strftime (заголовки) и для number_format (ячейки)
DAY_FMT = "%d.%m.%Y"
CELL_DAY_FMT = "DD.MM.YYYY"

SHEET_ORDER = (
    "Лист смены",
    "Меню",
    "Продукты",
    "Карта задач",
    "Тайминг",
    "ТТК",
    "Ингредиенты",
    "Конструктор меню",
    "План меню",
    "Задачи",
    "Списания",
    "Инвентаризация",
    "Инструкция",
)

RECIPE_REF_SHEET = "Справочник блюд"
"""Скрытый лист со списком блюд для проверки ввода в «Конструкторе меню»."""


def _hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


def _hm(dt) -> str:
    return f"{dt.hour:02d}:{dt.minute:02d}"


def _span(start, end) -> str:
    return f"{_hm(start)}–{_hm(end)}"


# ---------------------------------------------------------------------------
# Лист смены — то, что печатают и вешают на доску
# ---------------------------------------------------------------------------


def _shift_sheet(ws: Worksheet, sheet: ShiftSheet) -> None:
    st.set_widths(ws, {"A": 13, "B": 15, "C": 42, "D": 12, "E": 12, "F": 10, "G": 16})
    row = st.write_title(
        ws,
        f"Лист смены · {sheet.day.strftime(DAY_FMT)}",
        f"{sheet.kitchen} · {sheet.shift} · {sheet.shift_start:%H:%M}–"
        f"{sheet.shift_end:%H:%M} · {sheet.dishes} блюд · {sheet.portions} порций",
    )

    for block in sheet.blocks:
        title = block.title
        if block.kind == "need:Бакалея":
            title += "  — эти позиции не готовят, только сверяют остаток"
        row = st.section_bar(ws, row, title, len(block.headers))
        row = st.table_header(ws, row, block.headers)
        for i, line in enumerate(block.lines):
            row = st.write_row(ws, row, line, zebra=bool(i % 2))
        row += 1

    for warning in sheet.warnings:
        row = st.merge_note(ws, row, f"!  {warning}", 7, kind="danger")

    if sheet.notes:
        row += 1
        row = st.section_bar(ws, row, "Примечания и предположения", 7)
        for i, note in enumerate(sheet.notes, start=1):
            row = st.merge_note(ws, row, f"{i}. {note}", 7)

    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


# ---------------------------------------------------------------------------
# Меню смены
# ---------------------------------------------------------------------------


def _menu(ws: Worksheet, sheet: ShiftSheet) -> list[int]:
    st.set_widths(
        ws, {"A": 12, "B": 12, "C": 20, "D": 44, "E": 10, "F": 10, "G": 16, "H": 26}
    )
    row = st.write_title(ws, "Меню смены", f"{sheet.day.strftime(DAY_FMT)}")
    row = st.table_header(
        ws,
        row,
        ("Приём пищи", "Категория", "Подгруппа", "Блюдо", "Порций",
         "Выдача", "Цех", "Примечание"),
    )
    rows: list[int] = []
    for i, r in enumerate(sheet.menu):
        row = st.write_row(
            ws,
            row,
            (r.meal, r.category, r.subcategory, r.dish, r.portions,
             r.serve_at, r.shop, r.note),
            zebra=bool(i % 2),
        )
        rows.append(row - 1)
        ws.cell(row=row - 1, column=6).number_format = TIME_FMT
        ws.cell(row=row - 1, column=5).alignment = Alignment(horizontal="center")

    row += 1
    total = sum(r.portions for r in sheet.menu)
    row = st.merge_note(ws, row, f"Итого порций: {total}", 8)
    row = st.merge_note(
        ws, row, "Правьте меню в листе «Конструктор меню» и пересобирайте книгу. "
                 "Название блюда — ссылка на его состав.", 8
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False
    return rows


# ---------------------------------------------------------------------------
# Продуктовая матрица
# ---------------------------------------------------------------------------


def _products(ws: Worksheet, sheet: ShiftSheet) -> None:
    st.set_widths(
        ws,
        {"A": 22, "B": 26, "C": 12, "D": 10, "E": 14, "F": 14, "G": 10,
         "H": 10, "I": 12, "J": 26, "K": 18},
    )
    row = st.write_title(
        ws,
        "Продуктовая матрица",
        f"{sheet.day.strftime(DAY_FMT)} · сгруппировано по типу обработки, "
        "а не по блюдам",
    )

    for group in sheet.needs:
        row = st.section_bar(ws, row, f"{group.category} — {group.owner}", 11)
        row = st.table_header(
            ws,
            row,
            ("Продукт", "Категория", "Место хранения", "Нужно", "Ед.",
             "Остаток", "Мин. запас", "Не хватает", "Статус", "Действие",
             "Из каких блюд"),
        )
        for i, n in enumerate(group.items):
            row = st.write_row(
                ws,
                row,
                (n.product, n.category, n.storage, round(n.qty_display, 3), n.unit,
                 n.stock, n.min_stock, round(n.shortage, 3), n.status,
                 n.action or "—", n.breakdown or "—"),
                zebra=bool(i % 2),
                fonts={9: Font(name="Calibri", size=10, bold=n.shortage > 0,
                               color=st.DANGER_FG if n.shortage > 0 else st.OK_FG)},
                fills={9: st.DANGER_BG} if n.shortage > 0 else None,
            )
            for col in (4, 6, 7, 8):
                ws.cell(row=row - 1, column=col).number_format = NUM_FMT
        row += 1

    short = [n for n in sheet.needs_flat if n.shortage > 0]
    if short:
        row = st.section_bar(ws, row, "К закупке", 11)
        row = st.table_header(
            ws, row,
            ("Продукт", "Нужно", "Ед.", "Не хватает", "Упаковка", "Место хранения"),
        )
        for i, n in enumerate(short):
            row = st.write_row(
                ws,
                row,
                (n.product, round(n.qty_display, 3), n.unit, round(n.shortage, 3),
                 n.purchase_unit or "—", n.storage),
                zebra=bool(i % 2),
            )
        row += 1

    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


# ---------------------------------------------------------------------------
# Карта задач по ролям
# ---------------------------------------------------------------------------


def _task_map(ws: Worksheet, sheet: ShiftSheet) -> None:
    st.set_widths(ws, {"A": 15, "B": 14, "C": 52, "D": 28, "E": 30, "F": 8, "G": 30})
    row = st.write_title(
        ws,
        "Карта задач и обязанностей",
        f"{sheet.day.strftime(DAY_FMT)} · отметьте «Готово» в колонке F",
    )

    for role, items in sheet.tasks_by_role:
        row = st.section_bar(ws, row, role, 7)
        row = st.table_header(
            ws,
            row,
            ("Время", "Длит., мин", "Операция", "Блюдо", "Продукты", "Готово",
             "Примечание"),
        )
        for i, t in enumerate(items):
            row = st.write_row(
                ws,
                row,
                (_span(t.start, t.end), t.duration_min, t.operation, t.source,
                 t.products or "—", "☐", t.note),
                zebra=bool(i % 2),
            )
            ws.cell(row=row - 1, column=6).alignment = Alignment(horizontal="center")
            ws.cell(row=row - 1, column=6).font = Font(name="Calibri", size=12)
        row += 1

    row = st.section_bar(ws, row, "Нагрузка по ролям", 7)
    row = st.table_header(
        ws, row, ("Роль", "Задач", "Пик одновременных", "Нужно человек", "Минут")
    )
    for i, load in enumerate(sheet.role_load):
        row = st.write_row(
            ws,
            row,
            (load.role, load.tasks, load.simultaneous, load.people, load.total_min),
            zebra=bool(i % 2),
            fonts={4: Font(name="Calibri", size=10, bold=load.simultaneous > 1,
                           color=st.DANGER_FG if load.simultaneous > 1 else st.OK_FG)},
        )
    row += 1
    row = st.merge_note(
        ws,
        row,
        "Пик выше 1 означает, что в этот момент нужны несколько человек роли. "
        "Роли заданы без имён — распределите людей на magnets-доске.",
        7,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


# ---------------------------------------------------------------------------
# Хронология смены
# ---------------------------------------------------------------------------


def _timeline(ws: Worksheet, sheet: ShiftSheet) -> None:
    st.set_widths(ws, {"A": 9, "B": 9, "C": 14, "D": 15, "E": 50, "F": 26, "G": 30})
    row = st.write_title(
        ws,
        "Хронология смены",
        f"{sheet.day.strftime(DAY_FMT)} · "
        f"{sheet.shift_start:%H:%M}–{sheet.shift_end:%H:%M} · "
        "обратный отсчёт от времени выдачи",
    )
    row = st.table_header(
        ws,
        row,
        ("Начало", "Конец", "Роль", "Приём пищи", "Операция", "Блюдо",
         "Продукты"),
    )

    last = None
    for i, t in enumerate(sheet.tasks):
        band = bool(i % 2)
        if t.stage != last:
            last = t.stage
        row = st.write_row(
            ws,
            row,
            (t.start.time(), t.end.time(), t.role, t.meal, t.operation, t.source,
             t.products or "—"),
            zebra=band,
        )
        for col in (1, 2):
            ws.cell(row=row - 1, column=col).number_format = TIME_FMT
        ws.cell(row=row - 1, column=3).font = st.BOLD
        if t.stage == "Контроль":
            ws.cell(row=row - 1, column=5).fill = PatternFill("solid", fgColor=st.WARN_BG)
        elif t.stage == "Закрытие":
            ws.cell(row=row - 1, column=5).fill = PatternFill("solid", fgColor=st.BAND)

    row += 1
    row = st.section_bar(ws, row, "Контрольные точки шеф-повара", 7)
    row = st.table_header(ws, row, ("Начало", "Конец", "Роль", "Операция", "Блюдо", "", ""))
    for i, c in enumerate(sheet.controls):
        row = st.write_row(
            ws,
            row,
            (c.start.time(), c.end.time(), c.role, c.operation, c.source),
            zebra=bool(i % 2),
        )
        for col in (1, 2):
            ws.cell(row=row - 1, column=col).number_format = TIME_FMT

    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


# ---------------------------------------------------------------------------
# ТТК + ингредиенты
#
# Обе функции возвращают словарь «блюдо → строка в листе». Он нужен, чтобы
# после записи всех листов повесить взаимные ссылки: из ТТК и меню — на состав
# блюда, из состава — обратно на карточку.
# ---------------------------------------------------------------------------


TTK_COLS = (
    "№", "Блюдо", "Приём пищи", "Цех", "Подготовка, мин", "Готовность, мин",
    "Позиций", "В меню",
)
"""Колонки ТТК. Категории блюда больше нет отдельной колонкой — она стала
папкой, поэтому в таблице только то, чего в заголовке папки не видно."""

IN_MENU_COL = 8
DISH_COL = 2


def _recipes(ws: Worksheet, data: KitchenData) -> dict[str, int]:
    """Каталог блюд, разложенный по папкам: приём пищи, затем подгруппа.

    Два уровня нужны не для красоты: в «Конструкторе меню» список блюд
    выпадает из тех же папок, и шеф ищет «супы», а не «блюдо с индексом 7».
    Каждая подпапка сворачивается кнопкой в левом поле, поэтому длинные
    списки каш и супов не мешают видеть остальные.
    """
    st.set_widths(
        ws, {"A": 4, "B": 44, "C": 12, "D": 20, "E": 15, "F": 15, "G": 11, "H": 9}
    )
    row = st.write_title(
        ws, "ТТК",
        "Норма — на одну порцию. Каталог разложен по папкам: приём пищи, "
        "затем подгруппа. Нажмите на блюдо, чтобы открыть состав.",
    )
    row = st.table_header(ws, row, TTK_COLS)
    st.folding(ws)

    rows: dict[str, int] = {}
    in_menu = {line.recipe for line in data.menu}
    n = 0
    for folder in folders.grouped(data.recipes):
        row = st.section_bar(ws, row, f"{folder.meal} · {folder.count} блюд", 8)
        for sub, names in folder.subfolders:
            row = st.sub_bar(ws, row, f"{sub} · {len(names)}", 8, indent=2)
            st.fold(ws, row - 1, 1)
            for name in names:
                r = data.recipes[name]
                n += 1
                used = name in in_menu
                row = st.write_row(
                    ws,
                    row,
                    (n, name, r.meal, r.shop, r.prep_min, r.cook_min,
                     len(r.ingredients), "да" if used else "нет"),
                    zebra=bool(n % 2 == 0),
                    fonts={IN_MENU_COL: st.BOLD} if used else None,
                )
                rows[name] = row - 1
                st.fold(ws, row - 1, 2)
                ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
                ws.cell(row=row - 1, column=IN_MENU_COL).alignment = Alignment(
                    horizontal="center"
                )
            row += 1

    row = st.merge_note(
        ws, row, "Название блюда — ссылка на лист «Ингредиенты». Один продукт в "
                 "нескольких блюдах автоматически сложится в одну строку "
                 "продуктовой матрицы.", 8,
    )
    if data.menu:
        row = st.merge_note(
            ws, row, f"«В меню» — это смена на {data.menu[0].day:%d.%m.%Y}, "
                     f"а не весь каталог: сегодня в работе {len(in_menu)} из "
                     f"{len(data.recipes)} блюд. Папки сворачиваются кнопкой "
                     f"«−» в левом поле.", 8,
        )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False
    return rows


def _ingredients(ws: Worksheet, data: KitchenData) -> dict[str, int]:
    """Состав по блюдам, сгруппированный блоками.

    Название блюда пишется один раз на весь блок — иначе пришлось бы искать
    карточку среди сотни строк. Само название ведёт обратно в ТТК.
    """
    st.set_widths(
        ws,
        {"A": 4, "B": 44, "C": 30, "D": 8, "E": 12, "F": 30, "G": 22, "H": 12},
    )
    row = st.write_title(
        ws,
        "Ингредиенты",
        "Норма на 1 порцию в базовой единице (г / мл / шт). "
        "Название блюда — ссылка обратно в ТТК.",
    )
    row = st.table_header(
        ws,
        row,
        ("№", "Блюдо", "Продукт", "Ед.", "Кол-во на порцию", "Действие",
         "Категория продукта", "Карточка"),
    )
    rows: dict[str, int] = {}
    n = 0
    for name, r in data.recipes.items():
        first = row
        for ing in r.ingredients:
            n += 1
            product = data.products.get(ing.product)
            row = st.write_row(
                ws,
                row,
                (n, name if row == first else "", ing.product,
                 product.base_unit if product else "",
                 round(ing.qty_per_portion, 3), ing.action,
                 product.category if product else "", "к ТТК"),
                zebra=bool(n % 2 == 0),
            )
            ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
            ws.cell(row=row - 1, column=5).number_format = NUM_FMT
            ws.cell(row=row - 1, column=8).font = st.LINK_SMALL
        rows[name] = first
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False
    return rows


def _link_dishes(
    ws: Worksheet,
    column: int,
    rows: list[int],
    targets: dict[str, int],
    sheet: str,
    font: st.Font = st.LINK,
) -> None:
    """Вешает ссылки из колонки с названием блюда на блок его состава.

    Вызывается вторым проходом: к этому моменту известны и строки-цели, и
    строки источника, поэтому порядок записи листов не важен.
    """
    for i in rows:
        name = ws.cell(row=i, column=column).value
        target = targets.get(str(name)) if name else None
        if target:
            st.anchor(ws, i, column, st.ref(sheet, f"B{target}"), font=font)


# ---------------------------------------------------------------------------
# План меню на неделю
# ---------------------------------------------------------------------------

PLAN_DISH_COL = 3
"""Колонка «Блюдо» в листе «План меню» — на неё вешаем ссылку на ТТК."""


def _plan_menu(ws: Worksheet, data: KitchenData) -> list[int]:
    st.set_widths(
        ws, {"A": 4, "B": 14, "C": 44, "D": 10, "E": 11, "F": 10, "G": 34}
    )
    row = st.write_title(
        ws,
        "План меню на неделю",
        "Что готовим в каждый день. Название блюда — ссылка на его состав.",
    )
    row = st.table_header(
        ws,
        row,
        ("№", "День недели", "Блюдо", "Порций", "Выдача", "Статус", "Примечание"),
    )
    if not data.plan:
        row = st.merge_note(
            ws, row, "План пуст. Добавьте строки в kitchen/menu_week.py и "
                     "пересоберите книгу.", 7,
        )
        st.print_setup(ws)
        ws.sheet_view.showGridLines = False
        return []

    rows: list[int] = []
    day = ""
    i = 0
    for line in data.plan:
        if line.weekday != day:
            day = line.weekday
            row = st.section_bar(ws, row, day, 7)
        i += 1
        row = st.write_row(
            ws,
            row,
            (i, line.weekday, line.recipe, line.portions, line.serve_at,
             "в неделю", line.note),
            zebra=bool(i % 2 == 0),
        )
        rows.append(row - 1)
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
        ws.cell(row=row - 1, column=5).number_format = TIME_FMT
        ws.cell(row=row - 1, column=6).font = st.SMALL

    row += 1
    row = st.merge_note(
        ws,
        row,
        "Чтобы собрать смену за конкретный день:  python build.py --week Понедельник",
        7,
    )
    row = st.merge_note(
        ws,
        row,
        "День недели можно указать сокращением: пн, понедельник или 1.",
        7,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False
    return rows


@dataclass(frozen=True)
class RecipeRef:
    """Скрытый справочник блюд: что в нём есть и где лежат приёмы пищи.

    Нужен «Конструктору меню»: у каждого окна свой выпадающий список, и
    чтобы в окне «Обед» не предлагались каши, блюда одного приёма пищи
    должны идти подряд. Заодно на листе лежит колонка «Папка» — по ней
    формулы в окне поиска считают, сколько блюд нашлось в каждой папке.
    """

    count: int
    meals: dict[str, tuple[int, int]]

    @property
    def last(self) -> int:
        return self.count + 1

    def range_of(self, meal: str) -> str | None:
        """Адрес диапазона блюд приёма пищи для проверки ввода."""
        span = self.meals.get(meal)
        if not span:
            return None
        first, last = span
        return f"$A${first}:$A${last}" if first == last else f"$A${first}:$A${last}"

    @property
    def names(self) -> str:
        return f"$A$2:$A${self.last}"

    @property
    def folders(self) -> str:
        return f"$B$2:$B${self.last}"

    @property
    def meals_col(self) -> str:
        return f"$C$2:$C${self.last}"


def _recipe_ref(wb: Workbook, data: KitchenData) -> RecipeRef:
    """Скрытый список блюд для выпадающих списков и для счётчиков поиска.

    Excel не даёт засунуть в одну формулу больше 255 символов, а 29 названий
    блюд в строку не помещаются. Поэтому список живёт на отдельном листе, а
    проверка ввода ссылается на диапазон. Порядок строк — порядок папок из
    kitchen/excel/folders.py, чтобы он совпадал с тем, что видит шеф в ТТК.
    """
    ws = wb.create_sheet(RECIPE_REF_SHEET)
    for i, name in enumerate(("Блюдо", "Папка", "Приём пищи"), start=1):
        ws.cell(row=1, column=i, value=name).font = st.HEADFONT
    meals: dict[str, tuple[int, int]] = {}
    row = 1
    for folder in folders.grouped(data.recipes):
        start = row + 1
        for sub, names in folder.subfolders:
            for name in names:
                row += 1
                recipe = data.recipes[name]
                ws.cell(row=row, column=1, value=name).font = st.BODY
                ws.cell(row=row, column=2, value=sub).font = st.BODY
                ws.cell(row=row, column=3, value=recipe.meal).font = st.BODY
        first, last = meals.get(folder.meal, (start, start))
        meals[folder.meal] = (min(first, start), max(last, row))
    for col, width in (("A", 44), ("B", 20), ("C", 12)):
        ws.column_dimensions[col].width = width
    ws.sheet_state = "hidden"
    return RecipeRef(row - 1, meals)


def _catalog(ws: Worksheet, data: KitchenData) -> None:
    st.set_widths(
        ws,
        {"A": 4, "B": 30, "C": 10, "D": 18, "E": 20, "F": 10, "G": 11, "H": 26,
         "I": 14},
    )
    row = st.write_title(
        ws,
        "Продукты",
        "Единица, остаток и неснижаемый запас. Кто отвечает — определяется категорией.",
    )
    row = st.table_header(
        ws,
        row,
        ("№", "Продукт", "Ед.", "Категория", "Место хранения", "Остаток",
         "Мин. запас", "Упаковка", "Ответственный"),
    )
    for i, p in enumerate(data.products.values(), start=1):
        row = st.write_row(
            ws,
            row,
            (i, p.name, p.unit, p.category, p.storage, p.stock, p.min_stock,
             p.purchase_unit or "—", p.owner),
            zebra=bool(i % 2 == 0),
        )
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
        for col in (6, 7):
            ws.cell(row=row - 1, column=col).number_format = NUM_FMT
    row += 1
    row = st.merge_note(
        ws, row, "Категории «Бакалея» и «Молочка» попадают в матрицу даже если "
                 "блюда нет — так виден неснижаемый остаток.", 9,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


# ---------------------------------------------------------------------------
# Конструктор меню — единственный лист, который шеф правит руками
#
# Раскладка сверху вниз:
#   1. окно поиска по ТТК — запрос и счётчики по папкам;
#   2. окно позиций на каждый приём пищи, который есть в каталоге;
#   3. заметки. Всё, что между первым заголовком таблицы и концом листа, —
#      рабочая форма; кнопок и макросов в .xlsx нет, поэтому «поиск» — это
#      формулы COUNTIF/COUNTIFS по скрытому справочнику, а подстановка —
#      ключ --search при пересборке.
# ---------------------------------------------------------------------------

CONSTRUCTOR_COLS = ("№", "Блюдо", "Порций", "Выдача", "Примечание")
CONSTRUCTOR_SPAN = 5
MENU_DISH_COL = 2
MENU_PORTIONS_COL = 3
MENU_SERVE_COL = 4
SEARCH_VALUE_COL = 3
"""Колонка панели поиска, куда шеф пишет запрос и где живут счётчики."""

SEARCH_LABEL = "Запрос"
"""Подпись ячейки поиска в колонке B. По ней же ключ --search находит
ячейку запроса в книге, поэтому менять её молча нельзя."""

TOTAL_LABEL = "Найдено блюд"
FREE_SLOTS = 6
"""Сколько пустых строк оставить в каждом окне. Они и есть «рабочее окно»:
выбрал блюдо из списка — вписал порции — собрал книгу."""


def _search_panel(
    ws: Worksheet, row: int, data: KitchenData, ref_data: RecipeRef
) -> int:
    """Окно поиска: запрос в одной ячейке, счётчики по папкам ТТК.

    Счётчики считают формулами, а не скриптом, чтобы шеф увидел результат
    сразу, не запуская build.py. Совпадение ищется как в Excel: любая часть
    названия, без учёта регистра, поэтому «суп» находит и «Гороховый суп»,
    и «Том-ям». Подсчёт по папке идёт через COUNTIFS по колонкам «Папка» и
    «Приём пищи» скрытого справочника.
    """
    row = st.section_bar(
        ws, row,
        "Поиск по ТТК — впишите запрос и посмотрите, в каких папках есть совпадения",
        CONSTRUCTOR_SPAN,
    )
    label = ws.cell(row=row, column=MENU_DISH_COL, value=SEARCH_LABEL)
    label.font = st.BOLD
    label.alignment = Alignment(vertical="center")

    box = Border(
        top=Side(style="medium", color=st.ACCENT),
        bottom=Side(style="medium", color=st.ACCENT),
        left=Side(style="medium", color=st.ACCENT),
        right=Side(style="medium", color=st.ACCENT),
    )
    for col in range(SEARCH_VALUE_COL, CONSTRUCTOR_SPAN + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = PatternFill("solid", fgColor=st.INPUT_BG)
        cell.border = box
    input_cell = ws.cell(row=row, column=SEARCH_VALUE_COL)
    input_cell.alignment = Alignment(vertical="center", indent=1)
    input_cell.comment = Comment(
        "Напишите часть названия блюда: суп, каша, салат, творог.\n"
        "Счётчики ниже пересчитаются сами. Чтобы подставить найденное "
        "в пустые строки окон, сохраните книгу и запустите:\n"
        "python build.py --search\n"
        "\n"
        "Если счётчики пустые, а список блюд не появляется — вы открыли "
        "книгу в Numbers: он не показывает проверку ввода. Тогда впишите "
        "название блюда в окно руками — регистр и лишние пробелы не важны.",
        "Кухня",
        width=360,
        height=170,
    )
    ws.merge_cells(start_row=row, start_column=SEARCH_VALUE_COL,
                   end_row=row, end_column=CONSTRUCTOR_SPAN)
    ws.row_dimensions[row].height = 22
    query = f"${get_column_letter(SEARCH_VALUE_COL)}${row}"
    row += 1

    total = ws.cell(row=row, column=MENU_DISH_COL, value=TOTAL_LABEL)
    total.font = st.BOLD
    total.alignment = Alignment(vertical="center")
    found = ws.cell(
        row=row,
        column=SEARCH_VALUE_COL,
        value=(
            f"=COUNTIF({st.ref(RECIPE_REF_SHEET, ref_data.names)},"
            f'"*"&{query}&"*")'
        ),
    )
    found.font = st.BOLD
    found.alignment = Alignment(horizontal="center", vertical="center")
    found.number_format = NUM_FMT
    for col in range(1, CONSTRUCTOR_SPAN + 1):
        ws.cell(row=row, column=col).border = Border(bottom=st.THIN)
    row += 2

    for folder in folders.grouped(data.recipes):
        row = st.sub_bar(ws, row, folder.meal, CONSTRUCTOR_SPAN)
        meal_ref = f"$A${row - 1}"
        for sub, names in folder.subfolders:
            name_cell = ws.cell(row=row, column=MENU_DISH_COL, value=sub)
            name_cell.font = st.BODY
            name_cell.alignment = Alignment(vertical="center", indent=2)
            count = ws.cell(
                row=row,
                column=SEARCH_VALUE_COL,
                value=(
                    f"=COUNTIFS({st.ref(RECIPE_REF_SHEET, ref_data.folders)},"
                    f"$B{row},"
                    f'{st.ref(RECIPE_REF_SHEET, ref_data.meals_col)},{meal_ref},'
                    f'{st.ref(RECIPE_REF_SHEET, ref_data.names)},"*"&{query}&"*")'
                ),
            )
            count.alignment = Alignment(horizontal="center", vertical="center")
            count.font = st.SMALL
            row += 1
        row += 1
    return row


def _menu_window(
    ws: Worksheet,
    row: int,
    meal: str,
    lines: list[MenuLine],
    ref_data: RecipeRef,
) -> tuple[int, list[int]]:
    """Окно позиций одного приёма пищи.

    Заполненные строки идут первыми, ниже — пустые слоты. У каждого окна свой
    выпадающий список, ограниченный блюдами этого приёма пищи: в «Обед» не
    предлагаются каши, искать не нужно.
    """
    row = st.section_bar(ws, row, meal, CONSTRUCTOR_SPAN)
    header_row = row
    row = st.table_header(ws, row, CONSTRUCTOR_COLS, freeze=False)

    dish_range = ref_data.range_of(meal)
    dv_dish = DataValidation(
        type="list",
        formula1=f"={st.ref(RECIPE_REF_SHEET, dish_range)}",
        allow_blank=True,
        showDropDown=False,
    )
    dv_portions = DataValidation(
        type="whole", operator="between", formula1="1", formula2="10000",
        allow_blank=True,
    )
    dv_serve = DataValidation(
        type="time", operator="between", formula1="00:00", formula2="23:59",
        allow_blank=True,
    )
    for dv in (dv_dish, dv_portions, dv_serve):
        ws.add_data_validation(dv)

    rows: list[int] = []
    last = row + len(lines) + FREE_SLOTS - 1
    dv_dish.add(f"{get_column_letter(MENU_DISH_COL)}{row}:"
                f"{get_column_letter(MENU_DISH_COL)}{last}")
    dv_portions.add(f"{get_column_letter(MENU_PORTIONS_COL)}{row}:"
                    f"{get_column_letter(MENU_PORTIONS_COL)}{last}")
    dv_serve.add(f"{get_column_letter(MENU_SERVE_COL)}{row}:"
                 f"{get_column_letter(MENU_SERVE_COL)}{last}")

    for i, line in enumerate(lines, start=1):
        row = st.write_row(
            ws, row,
            (i, line.recipe, line.portions, line.serve_at, line.note),
            zebra=bool(i % 2 == 0),
        )
        rows.append(row - 1)
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
        ws.cell(row=row - 1, column=MENU_PORTIONS_COL).number_format = NUM_FMT
        ws.cell(row=row - 1, column=MENU_SERVE_COL).number_format = TIME_FMT

    for _ in range(FREE_SLOTS):
        for col in range(1, CONSTRUCTOR_SPAN + 1):
            cell = ws.cell(row=row, column=col)
            cell.border = Border(bottom=st.THIN)
            cell.fill = PatternFill("solid", fgColor=st.INPUT_BG)
        ws.cell(row=row, column=MENU_SERVE_COL,
                value=folders.SERVED_AT.get(meal, time(12, 0))
                ).number_format = TIME_FMT
        ws.cell(row=row, column=MENU_SERVE_COL).alignment = Alignment(
            horizontal="center"
        )
        row += 1
    st.fold_rows(ws, header_row, row)
    return row + 1, rows


def _constructor(ws: Worksheet, data: KitchenData, ref_data: RecipeRef) -> list[int]:
    st.set_widths(ws, {"A": 4, "B": 44, "C": 10, "D": 11, "E": 34})
    row = st.write_title(
        ws,
        "Конструктор меню",
        "Рабочее окно смены: найдите блюдо в ТТК, выберите его в списке, "
        "впишите порции → запустите «python build.py»",
    )
    row = st.merge_note(
        ws,
        row,
        "Правила: порция считается по каждой строке отдельно, поэтому на один "
        "приём пищи можно поставить разные порции. Время выдачи — формат ЧЧ:ММ.",
        CONSTRUCTOR_SPAN,
    )
    row = st.merge_note(
        ws,
        row,
        "Взять готовое меню за день:  python build.py --week Понедельник  "
        "(подробности — на листе «План меню»). Подставить найденное по "
        "запросу в пустые строки:  python build.py --search",
        CONSTRUCTOR_SPAN,
    )
    row += 1
    row = _search_panel(ws, row, data, ref_data)

    rows: list[int] = []
    for meal in folders.meal_folders(data.recipes):
        lines = [line for line in data.menu if line.meal == meal]
        row, filled = _menu_window(ws, row, meal, lines, ref_data)
        rows.extend(filled)
    st.folding(ws)

    row = st.merge_note(
        ws, row,
        f"Добавить блюдо — выберите его в списке колонки «Блюдо» и впишите "
        f"порции. Убрать — очистите ячейку с названием. Пустых строк в каждом "
        f"окне {FREE_SLOTS}; нужно больше — скопируйте строку. Всё, что в "
        f"окне, попадёт в расчёт при следующей сборке.", CONSTRUCTOR_SPAN,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False
    return rows


# ---------------------------------------------------------------------------
# Задачи — типовые операции
# ---------------------------------------------------------------------------


def _tasks(ws: Worksheet, data: KitchenData, sheet: ShiftSheet) -> None:
    st.set_widths(
        ws,
        {"A": 4, "B": 30, "C": 6, "D": 14, "E": 13, "F": 46, "G": 9, "H": 11,
         "I": 30, "J": 30, "K": 10, "L": 26},
    )
    row = st.write_title(
        ws,
        "Задачи",
        "Типовые операции. «*» в блюде — операция на весь приём пищи; "
        "«Общая» в поле «Ключ» — делается один раз на несколько блюд.",
    )
    row = st.table_header(
        ws,
        row,
        ("№", "Блюдо", "Порядок", "Этап", "Ответственный", "Операция",
         "Мин.", "Привязка", "Продукты", "Ключ", "Отступ, мин", "Примечание"),
    )
    for i, tpl in enumerate(data.tasks, start=1):
        anchor_label = {
            "": "по цепочке блюда",
            "deadline": "от времени выдачи",
            "prep_end": "после заготовки",
            "last_deadline": "после последней выдачи",
        }.get(tpl.anchor, tpl.anchor or "—")
        row = st.write_row(
            ws,
            row,
            (i, tpl.recipe, tpl.order, tpl.stage, tpl.role, tpl.operation,
             tpl.duration_min, anchor_label, tpl.products or "—",
             tpl.shared_key or "—", tpl.lead_min or "—", tpl.note),
            zebra=bool(i % 2 == 0),
        )
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
        ws.cell(row=row - 1, column=3).alignment = Alignment(horizontal="center")
    row += 1

    stages = {t.stage for t in data.tasks}
    unused = stages - set(STAGES)
    if unused:
        row = st.merge_note(ws, row, f"Неизвестные этапы: {sorted(unused)}", 12)
    row = st.merge_note(
        ws,
        row,
        "Чтобы добавить операцию — впишите строку. Этап «Заготовка» собирает "
        "блок подготовки сырья перед горячими цехами; одинаковый «Ключ» "
        "объединяет операции разных блюд в одну.",
        12,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


# ---------------------------------------------------------------------------
# Списания и инвентаризация
# ---------------------------------------------------------------------------


def _writeoffs(ws: Worksheet, data: KitchenData, sheet: ShiftSheet) -> None:
    st.set_widths(
        ws, {"A": 4, "B": 12, "C": 28, "D": 8, "E": 11, "F": 34, "G": 16, "H": 18,
             "I": 30}
    )
    row = st.write_title(
        ws,
        "Списания",
        "Дата · продукт · вес · причина · повар · подпись шефа. "
        "Сверьте с листом «Продукты»: расхождение больше 5% — повод для проверки.",
    )
    reasons = (
        "Истёк срок,Испортился,Ошибка приготовления,Пересорт,Бой,Прочее"
    )
    dv = DataValidation(type="list", formula1=f'"{reasons}"', allow_blank=True)
    ws.add_data_validation(dv)
    row = st.table_header(
        ws,
        row,
        ("№", "Дата", "Продукт", "Кол-во", "Ед.", "Причина", "Исполнитель",
         "Подпись шефа", "Комментарий"),
    )
    for i, w in enumerate(data.writeoffs, start=1):
        row = st.write_row(
            ws,
            row,
            (i, w.day, w.product, w.qty, data.products[w.product].unit
             if w.product in data.products else "",
             w.reason, w.cook, w.confirmed_by, ""),
            zebra=bool(i % 2 == 0),
        )
        ws.cell(row=row - 1, column=2).number_format = CELL_DAY_FMT
    dv.add(f"F{max(row, 2)}:F{max(row, 200)}")
    row += 1
    row = st.merge_note(
        ws, row, "Готовых строк нет — лист создан под структуру. Заполняйте "
                 "после смены, сверяясь с «Продуктовой матрицей».", 9,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


def _inventory(ws: Worksheet, data: KitchenData, sheet: ShiftSheet) -> None:
    st.set_widths(
        ws, {"A": 4, "B": 12, "C": 20, "D": 30, "E": 8, "F": 12, "G": 12, "H": 12,
             "I": 12, "J": 26, "K": 18}
    )
    row = st.write_title(
        ws,
        "Инвентаризация",
        "Микро-инвентаризация по категориям: пн — молочка и бакалея, "
        "ср — мясо и рыба, пт — овощи и заморозка.",
    )
    row = st.table_header(
        ws,
        row,
        ("№", "Дата", "Категория", "Продукт", "Ед.", "Учётный остаток",
         "Факт", "Расхождение", "Расхождение, %", "Причина", "Подпись шефа"),
    )
    dv = DataValidation(
        type="list",
        formula1='"Норма,Брак,Пересорт,Не найдено,Прочее"',
        allow_blank=True,
    )
    ws.add_data_validation(dv)

    for i, inv in enumerate(data.inventory, start=1):
        row = st.write_row(
            ws,
            row,
            (i, inv.day, inv.category, inv.product, inv.unit, inv.expected,
             inv.actual, round(inv.diff, 3), round(inv.diff_pct, 1), "", ""),
            zebra=bool(i % 2 == 0),
        )
        ws.cell(row=row - 1, column=2).number_format = CELL_DAY_FMT
        ws.cell(row=row - 1, column=8).number_format = NUM_FMT
        ws.cell(row=row - 1, column=9).number_format = '0.0"%"'
    dv.add(f"J{max(row, 2)}:J{max(row, 200)}")
    row += 1
    row = st.merge_note(
        ws, row, "«Учётный остаток» берётся из листа «Продукты». "
                 "Расхождение больше 5% — точечная проверка, а не общая "
                 "инвентаризация.", 11,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


# ---------------------------------------------------------------------------
# Инструкция
# ---------------------------------------------------------------------------


def _guide(ws: Worksheet, data: KitchenData, sheet: ShiftSheet) -> None:
    st.set_widths(ws, {"A": 4, "B": 34, "C": 96})
    row = st.write_title(ws, "Как пользоваться", "Порядок работы на каждый день")

    steps = (
        ("1. Открыть «Конструктор меню»",
         "Сверху окно поиска, ниже — отдельное окно на каждый приём пищи. "
         "Вписать блюдо из выпадающего списка, порции и время выдачи. "
         "Сохранить файл."),
        ("2. Найти блюдо в ТТК",
         "Вписать запрос в жёлтую ячейку «Запрос» — счётчики покажут, сколько "
         "блюд найдено всего и в каждой папке. Подсказка с перечнем — "
         "наведите курсор на ячейку."),
        ("3. Подобрать и поставить блюда",
         "В папке проекта:  python build.py --search  "
         "Команда читает запрос из книги и ставит найденные блюда в свободные "
         "строки своего окна, порции копирует у соседей. Можно вызвать сразу "
         "с запросом:  python build.py --search суп"),
        ("4. Или взять день из «Плана меню»",
         "В папке проекта:  python build.py --week пн  "
         "День недели можно указать как пн, Понедельник или 1. "
         "С --search ключ не сочетается: берите день из плана или ищите блюда."),
        ("5. Пересобрать книгу",
         "В папке проекта выполнить:  python build.py  "
         "Книга перезапишет все расчётные листы, ручные листы останутся "
         "нетронутыми."),
        ("6. Открыть «Лист смены»",
         "Готовый документ: меню, продуктовая матрица, задачи по ролям, "
         "контрольные точки. Печатается и идёт в чат кухни."),
        ("7. Раздать задачи",
         "«Карта задач» — распечатать по ролям, колонка «Готово» для отметок. "
         "«Тайминг» — общая хронология для всех."),
        ("8. Найти состав блюда",
         "Название блюда в «Меню», «ТТК», «Конструкторе меню» или «Плане "
         "меню» — ссылка на блок этого блюда в «Ингредиентах»."),
        ("9. После смены",
         "Заполнить «Списания», раз в неделю — «Инвентаризацию». "
         "Сверить расход с «Продуктовой матрицей»."),
        ("10. Новые блюда",
         "Добавить в «ТТК» (название, цех, время) и «Ингредиенты» "
         "(норма на порцию). Затем добавить операции в «Задачи»."),
    )
    row = st.table_header(ws, row, ("№", "Шаг", "Что делать"))
    for i, (step, detail) in enumerate(steps, start=1):
        row = st.write_row(ws, row, (i, step, detail), zebra=bool(i % 2 == 0))
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")

    row += 1
    row = st.section_bar(ws, row, "Какие листы за что отвечают", 3)
    row = st.table_header(ws, row, ("№", "Лист", "Зачем он"))
    sheets = (
        ("1", "Конструктор меню", "Единственный лист для ручных правок смены: "
                                 "окно поиска и окно позиций на каждый приём "
                                 "пищи. Всё остальное пересобирается из него."),
        ("2", "План меню", "Меню на Пн–Пт. Правьте его, чтобы менять неделю, "
                            "а не отдельный день."),
        ("3", "Справочник блюд", "Скрыт. Список блюд для выпадающих списков и "
                                 "счётчиков поиска: в одну формулу Excel "
                                 "помещается не больше 255 символов, а названий "
                                 "больше. Блюда одного приёма пищи идут подряд."),
        ("4", "ТТК и Ингредиенты", "Справочник блюд, разложенный по папкам: "
                                    "сначала приём пищи, потом подгруппа. "
                                    "Папки сворачиваются кнопкой «−» в левом "
                                    "поле. Название блюда — ссылка на состав "
                                    "и обратно."),
        ("5", "Лист смены", "Печатный документ для доски и чата кухни."),
    )
    for i, (n, name, why) in enumerate(sheets):
        row = st.write_row(ws, row, (n, name, why), zebra=bool(i % 2 == 0))
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")

    row += 1
    row = st.section_bar(ws, row, "Логика расчёта", 3)
    row = st.table_header(ws, row, ("№", "Шаг", "Что происходит"))
    logic = (
        ("1", "calculate_ingredients",
         "Нормы ТТК × порции, одинаковые продукты складываются в одну строку, "
         "потом группируются по типу обработки."),
        ("2", "generate_tasks",
         "Из меню и типовых операций собираются задачи. Общие операции "
         "(одинаковый ключ) делаются один раз."),
        ("3", "calculate_backward_timing",
         "Отсчёт назад от времени выдачи: цепочка блюда заканчивается к "
         "дедлайну, блок заготовок — перед самым ранним стартом."),
        ("4", "build_shift_sheet",
         "Собирается печатный лист и предупреждения о нехватке продуктов "
         "и перегруженных ролях."),
    )
    for i, (n, name, text) in enumerate(logic):
        row = st.write_row(ws, row, (n, name, text), zebra=bool(i % 2 == 0))
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")

    row += 1
    row = st.section_bar(ws, row, "Что зашито в данных, а что считается", 3)
    row = st.table_header(ws, row, ("№", "Параметр", "Где задаётся"))
    facts = (
        ("1", "Нормы ингредиентов", "Лист «Ингредиенты», на 1 порцию"),
        ("2", "Время выдачи и порции", "Лист «Конструктор меню»"),
        ("3", "Какие блюда в неделе", "Лист «План меню» + ключ --week"),
        ("4", "Длительность операций", "Лист «Задачи», колонка «Мин.»"),
        ("5", "Привязка к выдаче", "Лист «Задачи», колонка «Отступ, мин»"),
        ("6", "Остатки и неснижаемый запас", "Лист «Продукты»"),
        ("7", "Время начала смены", "Константа 06:00 в kitchen/core/timing.py — "
                                    "раньше этого задачи не ставятся"),
    )
    for i, (n, param, where) in enumerate(facts):
        row = st.write_row(ws, row, (n, param, where), zebra=bool(i % 2 == 0))
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")

    row += 1
    row = st.section_bar(ws, row, "Предположения этой версии", 3)
    row = st.table_header(ws, row, ("№", "Тема", "Предположение"))
    for i, a in enumerate(data.assumptions, start=1):
        row = st.write_row(ws, row, (i, a.topic, a.text), zebra=bool(i % 2 == 0))
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")

    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------


def build_workbook(data: KitchenData, sheet: ShiftSheet, path: Path) -> Path:
    """Собирает книгу и сохраняет по path.

    Листы записываются в два прохода. Сначала — содержимое всех таблиц, во
    втором — внутренние ссылки: адрес строки на «Ингредиентах» известен только
    после того, как записан этот лист, а «ТТК» и «Меню» идут раньше него.
    """
    wb = Workbook()
    wb.remove(wb.active)

    # Первый проход: пустые листы в нужном порядке.
    for name in SHEET_ORDER:
        wb.create_sheet(name)

    ttk = wb["ТТК"]
    ingredients = wb["Ингредиенты"]
    constructor = wb["Конструктор меню"]
    menu = wb["Меню"]

    recipe_rows = _recipes(ttk, data)
    ingredient_rows = _ingredients(ingredients, data)

    _shift_sheet(wb["Лист смены"], sheet)
    menu_rows = _menu(menu, sheet)
    plan_rows = _plan_menu(wb["План меню"], data)
    recipe_ref = _recipe_ref(wb, data)
    constructor_rows = _constructor(constructor, data, recipe_ref)
    _products(wb["Продукты"], sheet)
    _task_map(wb["Карта задач"], sheet)
    _timeline(wb["Тайминг"], sheet)
    _tasks(wb["Задачи"], data, sheet)
    _writeoffs(wb["Списания"], data, sheet)
    _inventory(wb["Инвентаризация"], data, sheet)
    _guide(wb["Инструкция"], data, sheet)

    # Второй проход: ссылки. Из ТТК, меню, конструктора и плана — на состав.
    _link_dishes(ttk, DISH_COL, list(recipe_rows.values()), ingredient_rows, "Ингредиенты")
    _link_dishes(menu, 4, menu_rows, ingredient_rows, "Ингредиенты")
    _link_dishes(
        constructor, MENU_DISH_COL, constructor_rows, ingredient_rows, "Ингредиенты"
    )
    _link_dishes(
        wb["План меню"], PLAN_DISH_COL, plan_rows, ingredient_rows, "Ингредиенты"
    )
    # Из состава — обратно на карточку ТТК.
    for name, target in recipe_rows.items():
        if name in ingredient_rows:
            first = ingredient_rows[name]
            st.anchor(ingredients, first, 2, st.ref("ТТК", f"B{target}"))
            st.anchor(
                ingredients, first, 8, st.ref("ТТК", f"B{target}"), font=st.LINK_SMALL
            )

    for name in SHEET_ORDER:
        wb[name].sheet_properties.tabColor = st.HEAD
    constructor.sheet_properties.tabColor = "C9A227"
    wb["Лист смены"].sheet_properties.tabColor = st.ACCENT
    wb["План меню"].sheet_properties.tabColor = st.ACCENT
    wb.active = 0

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    _drop_empty_formula_values(path)
    return path


#: ячейка с формулой и пустым кэшем: openpyxl пишет `<f>..</f><v />` всегда
_FORMULA_VOID = re.compile(r"(</f>)<v\s*/>")
_SHEET_XML = re.compile(r"^xl/worksheets/sheet\d+\.xml$")


def _drop_empty_formula_values(path: Path) -> None:
    """Убирает пустое `<v/>` из ячеек с формулами.

    openpyxl 3.1 пишет после каждой формулы `<v />` — значение, которого нет.
    Excel такой файл чинит молча и выбрасывает формулы вместе с проверками
    ввода, а Numbers и Google Таблицы показывают пустую ячейку и не
    пересчитывают её. Настоящий Excel пишет `<f>` без `<v>`, и пересчитывает
    книгу сам — так и оставляем.
    """
    src = zipfile.ZipFile(path)
    try:
        items = [(i, src.read(i.filename)) for i in src.infolist()]
    finally:
        src.close()
    changed = False
    out = []
    for info, blob in items:
        if _SHEET_XML.match(info.filename):
            text = blob.decode("utf-8")
            fixed = _FORMULA_VOID.sub(r"\1", text)
            if fixed != text:
                blob, changed = fixed.encode("utf-8"), True
        out.append((info, blob))
    if not changed:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info, blob in out:
            dst.writestr(info, blob)
    tmp.replace(path)

