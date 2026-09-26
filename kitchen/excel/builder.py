"""Сборка книги Excel из посчитанной смены.

Один модуль — один слой представления. Доменная логика сюда не попадает:
сюда приходит уже готовый ShiftSheet и KitchenData.
"""

from __future__ import annotations

from datetime import time
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from kitchen.core.shiftsheet import ShiftSheet
from kitchen.excel import styles as st
from kitchen.models import KitchenData, STAGES

TIME_FMT = "HH:MM"
NUM_FMT = "#,##0.00"

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
    "Задачи",
    "Списания",
    "Инвентаризация",
    "Инструкция",
)


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


def _menu(ws: Worksheet, sheet: ShiftSheet) -> None:
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
    for i, r in enumerate(sheet.menu):
        row = st.write_row(
            ws,
            row,
            (r.meal, r.category, r.subcategory, r.dish, r.portions,
             r.serve_at, r.shop, r.note),
            zebra=bool(i % 2),
        )
        ws.cell(row=row - 1, column=6).number_format = TIME_FMT
        ws.cell(row=row - 1, column=5).alignment = Alignment(horizontal="center")

    row += 1
    total = sum(r.portions for r in sheet.menu)
    row = st.merge_note(ws, row, f"Итого порций: {total}", 8)
    row = st.merge_note(
        ws, row, "Правьте меню в листе «Конструктор меню» и пересобирайте книгу.", 8
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


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
# ---------------------------------------------------------------------------


def _recipes(ws: Worksheet, data: KitchenData) -> None:
    st.set_widths(
        ws,
        {"A": 4, "B": 44, "C": 11, "D": 11, "E": 20, "F": 15, "G": 11, "H": 11,
         "I": 9, "J": 10},
    )
    row = st.write_title(
        ws, "ТТК", "Норма — на одну порцию. Правьте здесь, потом пересоберите книгу."
    )
    row = st.table_header(
        ws,
        row,
        ("№", "Блюдо", "Приём пищи", "Категория", "Подгруппа", "Цех",
         "Подготовка, мин", "Готовность, мин", "Позиций", "В меню"),
    )
    for i, (name, r) in enumerate(data.recipes.items(), start=1):
        row = st.write_row(
            ws,
            row,
            (i, name, r.meal, r.category, r.subcategory, r.shop, r.prep_min,
             r.cook_min, len(r.ingredients), "да" if r.in_menu else "нет"),
            zebra=bool(i % 2 == 0),
        )
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
        ws.cell(row=row - 1, column=10).alignment = Alignment(horizontal="center")
    row += 1
    row = st.merge_note(
        ws, row, "Состав блюд — на листе «Ингредиенты». Один продукт в нескольких "
                 "блюдах автоматически сложится в одну строку матрицы.", 10,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


def _ingredients(ws: Worksheet, data: KitchenData) -> None:
    st.set_widths(ws, {"A": 4, "B": 44, "C": 30, "D": 8, "E": 12, "F": 30, "G": 22})
    row = st.write_title(
        ws, "Ингредиенты", "Норма на 1 порцию в базовой единице (г / мл / шт)"
    )
    row = st.table_header(
        ws, row, ("№", "Блюдо", "Продукт", "Ед.", "Кол-во на порцию",
                  "Действие", "Категория продукта"),
    )
    n = 0
    for name, r in data.recipes.items():
        for ing in r.ingredients:
            n += 1
            product = data.products.get(ing.product)
            row = st.write_row(
                ws,
                row,
                (n, name, ing.product,
                 product.base_unit if product else "",
                 round(ing.qty_per_portion, 3), ing.action,
                 product.category if product else ""),
                zebra=bool(n % 2 == 0),
            )
            ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
            ws.cell(row=row - 1, column=5).number_format = NUM_FMT
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


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
# ---------------------------------------------------------------------------


def _constructor(ws: Worksheet, data: KitchenData) -> None:
    st.set_widths(ws, {"A": 4, "B": 12, "C": 44, "D": 10, "E": 11, "F": 10, "G": 34})
    row = st.write_title(
        ws,
        "Конструктор меню",
        "Впишите блюдо, порции и время выдачи → запустите «python build.py» → "
        "получите продукты, задачи, тайминг и лист смены",
    )
    row = st.merge_note(
        ws,
        row,
        "Правила: порция считается по каждой строке отдельно, поэтому на один "
        "приём пищи можно поставить разные порции. Время выдачи — формат ЧЧ:ММ.",
        7,
    )
    row += 1
    header_row = row
    row = st.table_header(
        ws, row, ("№", "Приём пищи", "Блюдо", "Порций", "Выдача", "Статус",
                  "Примечание")
    )

    dv_recipe = DataValidation(
        type="list",
        formula1=f'"{",".join(data.recipes)}"',
        allow_blank=True,
        showDropDown=False,
    )
    dv_meal = DataValidation(
        type="list", formula1='"Завтрак,Обед,Ужин,Перекус"', allow_blank=True
    )
    dv_time = DataValidation(type="time", operator="between",
                             formula1="00:00", formula2="23:59", allow_blank=True)
    for dv in (dv_recipe, dv_meal, dv_time):
        ws.add_data_validation(dv)
    last = header_row + 40
    dv_recipe.add(f"C{header_row + 1}:C{last}")
    dv_meal.add(f"B{header_row + 1}:B{last}")
    dv_time.add(f"E{header_row + 1}:E{last}")

    for i, line in enumerate(data.menu, start=1):
        row = st.write_row(
            ws,
            row,
            (i, line.meal, line.recipe, line.portions, line.serve_at,
             "в расчёте", line.note),
            zebra=bool(i % 2 == 0),
        )
        ws.cell(row=row - 1, column=1).alignment = Alignment(horizontal="center")
        ws.cell(row=row - 1, column=5).number_format = TIME_FMT
        ws.cell(row=row - 1, column=6).font = st.SMALL

    row += 1
    row = st.merge_note(
        ws, row, "Чтобы убрать блюдо — очистите ячейку с названием. Чтобы "
                 "добавить — впишите название в выпадающий список.", 7,
    )
    st.print_setup(ws)
    ws.sheet_view.showGridLines = False


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
         "Вписать блюдо из выпадающего списка, порции и время выдачи. "
         "Сохранить файл."),
        ("2. Пересобрать книгу",
         "В папке проекта выполнить:  python build.py  "
         "Книга перезапишет все расчётные листы, ручные листы останутся "
         "нетронутыми."),
        ("3. Открыть «Лист смены»",
         "Готовый документ: меню, продуктовая матрица, задачи по ролям, "
         "контрольные точки. Печатается и идёт в чат кухни."),
        ("4. Раздать задачи",
         "«Карта задач» — распечатать по ролям, колонка «Готово» для отметок. "
         "«Тайминг» — общая хронология для всех."),
        ("5. После смены",
         "Заполнить «Списания», раз в неделю — «Инвентаризацию». "
         "Сверить расход с «Продуктовой матрицей»."),
        ("6. Новые блюда",
         "Добавить в «ТТК» (название, цех, время) и «Ингредиенты» "
         "(норма на порцию). Затем добавить операции в «Задачи»."),
    )
    row = st.table_header(ws, row, ("№", "Шаг", "Что делать"))
    for i, (step, detail) in enumerate(steps, start=1):
        row = st.write_row(ws, row, (i, step, detail), zebra=bool(i % 2 == 0))
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
        ("3", "Длительность операций", "Лист «Задачи», колонка «Мин.»"),
        ("4", "Привязка к выдаче", "Лист «Задачи», колонка «Отступ, мин»"),
        ("5", "Остатки и неснижаемый запас", "Лист «Продукты»"),
        ("6", "Время начала смены", "Константа 06:00 в kitchen/core/timing.py — "
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
    """Собирает книгу и сохраняет по path."""
    wb = Workbook()
    wb.remove(wb.active)

    builders = (
        ("Лист смены", lambda ws: _shift_sheet(ws, sheet)),
        ("Меню", lambda ws: _menu(ws, sheet)),
        ("Продукты", lambda ws: _products(ws, sheet)),
        ("Карта задач", lambda ws: _task_map(ws, sheet)),
        ("Тайминг", lambda ws: _timeline(ws, sheet)),
        ("ТТК", lambda ws: _recipes(ws, data)),
        ("Ингредиенты", lambda ws: _ingredients(ws, data)),
        ("Конструктор меню", lambda ws: _constructor(ws, data)),
        ("Задачи", lambda ws: _tasks(ws, data, sheet)),
        ("Списания", lambda ws: _writeoffs(ws, data, sheet)),
        ("Инвентаризация", lambda ws: _inventory(ws, data, sheet)),
        ("Инструкция", lambda ws: _guide(ws, data, sheet)),
    )
    for name, fn in builders:
        ws = wb.create_sheet(name)
        fn(ws)

    for name in SHEET_ORDER:
        if name in wb.sheetnames:
            wb[name].sheet_properties.tabColor = st.HEAD
    wb["Конструктор меню"].sheet_properties.tabColor = "C9A227"
    wb["Лист смены"].sheet_properties.tabColor = st.ACCENT
    wb.active = 0

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
