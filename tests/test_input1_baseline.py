"""Эталон из input1.md: 50 порций, завтрак к 08:00, обед к 12:00.

Это главный регрессионный тест проекта: если цифры в матрице разойдутся
с примером из input1.md, сломалась арифметика, а не «каприза шефа».
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from kitchen.core import build_shift_sheet
from kitchen.seed import demo_data

DAY = date(2026, 9, 26)


@pytest.fixture(scope="module")
def sheet():
    return build_shift_sheet(demo_data(DAY))


# ---------------------------------------------------------------------------
# Эталонная продуктовая матрица из input1.md (50 порций)
# ---------------------------------------------------------------------------

EXPECTED: dict[str, float] = {
    # Задача разнорабочему (овощи)
    "Картофель": 10.0,
    "Лук репчатый": 2.5,
    # Соленья
    "Капуста квашеная": 3.0,
    "Огурцы солёные": 1.5,
    # Задача повару (мясной блок)
    "Фарш (говядина/свинина)": 6.0,
    "Мясо для солянки (набор)": 2.5,
    # Бакалея и молочка
    "Овсяные хлопья": 2.5,
    "Арахис солёный": 0.25,
    "Горошек консервированный": 1.2,
    "Молоко": 5.0,
    # Хлеб и холодильник — поштучно
    "Ветчина": 50.0,
    "Сыр": 50.0,
    "Хлеб тостовый": 50.0,
}


def test_матрица_совпадает_с_input1(sheet):
    got = {n.product: round(n.qty_display, 4) for n in sheet.needs_flat}
    for product, expected in EXPECTED.items():
        assert got[product] == pytest.approx(expected), product


def test_картофель_схлопывается_в_одну_строку(sheet):
    """Картофель из солянки и из пюре — одна строка, а не две."""
    potato = [n for n in sheet.needs_flat if n.product == "Картофель"]
    assert len(potato) == 1
    assert {s.recipe for s in potato[0].sources} == {
        "Солянка мясная",
        "Пюре с котлетами",
    }


def test_картофель_разложен_по_блюдам(sheet):
    potato = next(n for n in sheet.needs_flat if n.product == "Картофель")
    assert "Солянка мясная — 2 кг" in potato.breakdown
    assert "Пюре с котлетами — 8 кг" in potato.breakdown


def test_молоко_суммируется_из_двух_блюд(sheet):
    milk = next(n for n in sheet.needs_flat if n.product == "Молоко")
    assert len(milk.sources) == 2
    assert "Каша овсяная с солёным арахисом — 2 л" in milk.breakdown
    assert "Пюре с котлетами — 3 л" in milk.breakdown


def test_группы_по_типу_обработки(sheet):
    """Не по блюдам, а по тому, кто и что делает с продуктом."""
    groups = {g.category: g.owner for g in sheet.needs}
    assert groups["Овощи"] == "Разнорабочий"
    assert groups["Соленья"] == "Разнорабочий"
    assert groups["Мясо и рыба"] == "Повар"
    assert groups["Хлеб и холодильник"] == "Разнорабочий"


def test_бакалея_помечена_как_проверка_остатка(sheet):
    grocery = next(g for g in sheet.needs if g.category == "Бакалея")
    assert grocery.is_check_stock
    # Масло и сахар в меню не входят, но видны из-за неснижаемого остатка.
    assert {"Масло подсолнечное", "Соль", "Сахар"} <= {n.product for n in grocery.items}


def test_нехватка_считается_с_учётом_минимального_остатка(sheet):
    """Фарш: нужно 6, остаток 8, минимальный запас 3 → не хватает 1 кг."""
    farsh = next(n for n in sheet.needs_flat if n.product == "Фарш (говядина/свинина)")
    assert farsh.shortage == pytest.approx(1.0)
    assert farsh.status == "Не хватает"

    potato = next(n for n in sheet.needs_flat if n.product == "Картофель")
    assert potato.shortage == pytest.approx(0.0)
    assert potato.status == "Хватает"


# ---------------------------------------------------------------------------
# Тайминг
# ---------------------------------------------------------------------------


def test_каждое_блюдо_заканчивается_к_времени_выдачи(sheet):
    """Обратный отсчёт обязан приводить каждое блюдо ровно к дедлайну."""
    for row in sheet.menu:
        # Цепочка блюда, без общих работ смены (приёмка, списания, уборка).
        mine = [
            t
            for t in sheet.tasks
            if row.dish in t.recipes and t.stage != "Закрытие"
        ]
        if not mine:
            continue
        last = max(mine, key=lambda t: t.end)
        assert last.end.time() <= row.serve_at, f"{row.dish} не успевает"


def test_блюдо_ждёт_только_свою_заготовку(sheet):
    """Каша не ждёт подготовку ветчины, поэтому стартует с 07:20, а не с 07:30."""
    porridge = [
        t
        for t in sheet.tasks
        if t.source == "Каша овсяная с солёным арахисом"
    ]
    assert porridge
    assert min(t.start for t in porridge).time() == time(7, 20)
    assert max(t.end for t in porridge).time() == time(8, 0)


def test_перегрузка_поднимает_предупреждение():
    """Цепочка длиннее доступного времени до выдачи → overrun, а не тихий срыв."""
    from kitchen.core import calculate_backward_timing
    from kitchen.core.tasks import Task, TaskBoard
    from kitchen.core.timing import SHIFT_START
    from kitchen.models import MenuLine

    # Одна задача на 12 часов при смене с 07:15 и выдаче в 12:00.
    huge = Task(
        task_id="З-99",
        role="Повар",
        operation="Варка бесконечного супа",
        stage="Приготовление",
        meal="Обед",
        duration_min=12 * 60,
        source="Солянка мясная",
        recipes=("Солянка мясная",),
    )
    board = TaskBoard(
        tasks=(huge,),
        prep={},
        chains={"Солянка мясная": (huge,)},
        anchors=(),
    )
    menu = (MenuLine("Солянка мясная", 50, time(12, 0), "Обед", DAY),)

    timing = calculate_backward_timing(DAY, menu, board)
    assert timing.overruns == ("Солянка мясная",)
    # Невлезающая задача не уводит смену в ноль — только до SHIFT_START.
    assert timing.shift_start.time() == SHIFT_START


def test_подготовка_картофеля_одна_задача_на_три_блюда(sheet):
    potato = [t for t in sheet.tasks if "картофел" in t.operation.lower()
              and t.stage == "Заготовка"]
    assert len(potato) == 1, "общая заготовка должна схлопнуться в одну задачу"
    assert set(potato[0].recipes) >= {"Солянка мясная", "Пюре с котлетами"}


def test_подготовка_лука_одна_задача(sheet):
    onion = [t for t in sheet.tasks if "лука" in t.operation.lower()
             and t.stage == "Заготовка"]
    assert len(onion) == 1
    assert set(onion[0].recipes) >= {
        "Солянка мясная",
        "Пюре с котлетами",
        "Салат из квашеной капусты и зелёного горошка",
    }


def test_контроль_повара_перед_контролем_шефа(sheet):
    """Двухступенчатая дегустация: повар раньше, шеф — последняя проверка."""
    lunch = [t for t in sheet.tasks if t.meal == "Обед" and t.stage == "Контроль"]
    cook = next(t for t in lunch if t.role == "Повар")
    chief = next(t for t in lunch if t.role == "Шеф-повар")
    assert cook.end <= chief.start, "повар дегустирует первым"
    assert chief.end.time() < time(12, 0), "шеф заканчивает до выдачи"
    assert chief.end.time() >= time(11, 0), "шеф подходит к концу готовки"


def test_каждое_задание_имеет_положительную_длительность(sheet):
    for t in sheet.tasks:
        assert t.duration_min > 0, t.operation
        assert t.end > t.start, t.operation


def test_задачи_не_выходят_за_границы_смены(sheet):
    for t in sheet.tasks:
        assert t.start >= sheet.shift_start
        assert t.end <= sheet.shift_end


def test_мытьё_полов_сразу_после_блока_заготовки(sheet):
    """Клининг моет полы, когда разнорабочий освободил зону овощей."""
    veg = [t for t in sheet.tasks if t.stage == "Заготовка" and t.meal == "Обед"]
    floors = [t for t in sheet.tasks if "полов" in t.operation]
    assert veg and floors
    assert floors[0].start.time() >= max(t.end for t in veg).time()
    assert floors[0].start.time() < time(12, 0), "до выдачи, а не после неё"


def test_уборка_и_списания_после_последней_выдачи(sheet):
    """Закрывающие работы не могут начаться раньше, чем накормили.

    Исключение — мытьё полов: оно привязано к концу блока заготовок,
    а не к концу выдачи.
    """
    closing = [
        t for t in sheet.tasks if t.stage == "Закрытие" and "полов" not in t.operation
    ]
    assert closing
    assert min(t.start for t in closing) >= datetime.combine(DAY, time(12, 0))


def test_каждая_роль_из_structure_md_получила_задачу(sheet):
    roles = {t.role for t in sheet.tasks}
    assert {"Клининг", "Разнорабочий", "Повар", "Шеф-повар"} <= roles


# ---------------------------------------------------------------------------
# Масштабирование
# ---------------------------------------------------------------------------


def test_порции_линейно_масштабируют_продукты():
    """120 порций вместо 50 должны дать ровно вдвое больше всего."""
    from kitchen.core import calculate_ingredients
    from kitchen.models import MenuLine
    from kitchen.seed import PRODUCTS, RECIPES

    products = {p.name: p for p in PRODUCTS}
    base_menu = demo_data(DAY).menu

    def total(portions: int) -> dict[str, float]:
        menu = tuple(
            MenuLine(l.recipe, portions, l.serve_at, l.meal, DAY) for l in base_menu
        )
        needs = calculate_ingredients(menu, dict(RECIPES), products)
        return {n.product: n.qty_display for n in needs}

    base, double = total(50), total(100)
    for product, qty in EXPECTED.items():
        assert base[product] == pytest.approx(qty), product
        assert double[product] == pytest.approx(qty * 2), product


def test_блюдо_с_нулем_порций_выпадает_из_меню():
    from kitchen.core import calculate_ingredients
    from kitchen.models import MenuLine
    from kitchen.seed import PRODUCTS, RECIPES

    menu = tuple(MenuLine(l.recipe, 0, l.serve_at, l.meal, DAY) for l in demo_data(DAY).menu)
    needs = calculate_ingredients(menu, dict(RECIPES), {p.name: p for p in PRODUCTS})
    assert all(n.qty_display == 0 for n in needs)
