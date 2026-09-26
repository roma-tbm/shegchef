"""Доменные сущности конструктора кухни.

Правило: этот модуль не знает про Excel. В нём только данные,
которыми описывается кухня, и чистые функции, которые их преобразуют.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

# ---------------------------------------------------------------------------
# Единицы измерения
# ---------------------------------------------------------------------------
# Внутри системы количество всегда хранится в БАЗОВОЙ единице (г, мл, шт).
# Отображаемая единица (кг, л, ломт) получается делением на PER_BASE.
# Так каша «250 г» и «0,25 кг» — это одно и то же число, а сложение
# картофеля из трёх блюд не теряет точность.

BASE_UNITS = ("г", "мл", "шт")

PER_BASE: dict[str, float] = {
    "кг": 1000.0,
    "г": 1.0,
    "л": 1000.0,
    "мл": 1.0,
    "шт": 1.0,
    "ломт": 1.0,
    "банка": 1.0,
}

# ---------------------------------------------------------------------------
# Категории обработки продуктов -> кто и что делает с ними
# ---------------------------------------------------------------------------

#: Кто отвечает за обработку категории. Из structure.md:
#: разнорабочий — овощи/приёмка/склад, повар — мясо и приготовление.
CATEGORY_OWNER: dict[str, str] = {
    "Овощи": "Разнорабочий",
    "Соленья": "Разнорабочий",
    "Зелень": "Разнорабочий",
    "Мясо и рыба": "Повар",
    "Бакалея": "Повар",
    "Молочка": "Повар",
    "Хлеб и холодильник": "Разнорабочий",
}

#: Категории, которые не нужно готовить — только проверить остаток.
CHECK_STOCK_CATEGORIES = frozenset({"Бакалея"})

#: Этап работы смены.
STAGES = ("Заготовка", "Приготовление", "Контроль", "Закрытие")

#: Роли сотрудников из structure.md (только роли, без имён).
ROLES = ("Клининг", "Разнорабочий", "Повар", "Шеф-повар")


@dataclass(frozen=True, slots=True)
class Product:
    """Справочник продуктов."""

    name: str
    unit: str
    """Отображаемая единица: кг, л, ломт, шт."""

    category: str
    storage: str
    """Место хранения: Овощная, Холодильник, Сухой склад, ..."""

    base_unit: str = "г"
    per_base: float = 1000.0
    """Сколько базовых единиц в одной отображаемой."""

    stock: float = 0.0
    """Текущий остаток в отображаемой единице."""

    min_stock: float = 0.0
    """Неснижаемый остаток в отображаемой единице."""

    purchase_unit: str = ""
    """В чём закупают: коробка 5 кг, ящик 12 банок и т. п."""

    def to_display(self, qty_base: float) -> float:
        return qty_base / self.per_base

    def to_base(self, qty_display: float) -> float:
        return qty_display * self.per_base

    @property
    def owner(self) -> str:
        return CATEGORY_OWNER.get(self.category, "Повар")

    @property
    def is_check_stock(self) -> bool:
        return self.category in CHECK_STOCK_CATEGORIES


@dataclass(frozen=True, slots=True)
class Ingredient:
    """Норма ингредиента на одну порцию блюда."""

    product: str
    qty_per_portion: float
    """Количество в БАЗОВОЙ единице (г/мл/шт) на 1 порцию."""

    action: str = ""
    """Действие с продуктом: «кубик 2 см», «кольцо», «в фарш»."""


@dataclass(frozen=True, slots=True)
class Recipe:
    """Технологическая карта блюда (одна порция — база расчёта)."""

    name: str
    meal: str
    """Завтрак / Обед / Ужин."""

    category: str
    """Например «Обед»."""

    subcategory: str
    """Супы / Горячее / Салаты и закуски / Каши / Выпечка."""

    shop: str
    """Цех исполнения: Горячий цех, Холодный цех, Раздача."""

    prep_min: int
    """Подготовка (чистка/нарезка) до начала варки, минут."""

    cook_min: int
    """Полная готовность, минут."""

    ingredients: tuple[Ingredient, ...] = ()
    in_menu: bool = True
    """Показывать ли блюдо в конструкторе меню."""


@dataclass(frozen=True, slots=True)
class MenuLine:
    """Строка конструктора меню — то, что шеф вводит руками."""

    recipe: str
    portions: int
    serve_at: time
    meal: str
    day: date
    note: str = ""


@dataclass(frozen=True, slots=True)
class TaskTemplate:
    """Типовая операция из листа «Задачи».

    stage:
        Заготовка     — общая подготовка сырья, выполняется до горячих цехов;
        Приготовление — цепочка операций конкретного блюда;
        Контроль      — точки контроля шеф-повара;
        Закрытие      — уборка, мойка, списания после выдачи.

    shared_key: если задан, операции с одинаковым ключом у разных блюд
    объединяются в одну общую задачу (например «Подготовка лука» для
    солянки, котлет и салата — это одна и та же работа разнорабочего).
    """

    recipe: str
    order: int
    stage: str
    role: str
    operation: str
    duration_min: int
    shared_key: str = ""
    anchor: str = ""
    """deadline — до времени выдачи; prep_end — сразу после заготовки."""

    lead_min: int = 0
    """Отступ от дедлайна, мин. Для anchor=last_deadline — старт ПОСЛЕ выдачи."""

    products: str = ""
    """Продукты задачи через запятую. Заполняется вручную в листе «Задачи»."""

    meal: str = ""
    """Ограничить приёмом пищи. Пусто — для всех приёмов."""

    note: str = ""


@dataclass(frozen=True, slots=True)
class StockLine:
    """Строка журнала списаний."""

    day: date
    product: str
    qty: float
    reason: str
    cook: str
    confirmed_by: str = ""


@dataclass(frozen=True, slots=True)
class InventoryLine:
    """Строка микро-инвентаризации."""

    day: date
    category: str
    product: str
    expected: float
    actual: float
    unit: str

    @property
    def diff(self) -> float:
        return self.actual - self.expected

    @property
    def diff_pct(self) -> float:
        if self.expected == 0:
            return 0.0
        return self.diff / self.expected * 100.0


@dataclass(frozen=True, slots=True)
class Assumption:
    """Явно зафиксированное предположение — печатается в книге."""

    topic: str
    text: str


@dataclass(slots=True)
class KitchenData:
    """Всё, что лежит в книге, в виде обычных объектов."""

    products: dict[str, Product] = field(default_factory=dict)
    recipes: dict[str, Recipe] = field(default_factory=dict)
    tasks: tuple[TaskTemplate, ...] = ()
    menu: tuple[MenuLine, ...] = ()
    writeoffs: tuple[StockLine, ...] = ()
    inventory: tuple[InventoryLine, ...] = ()
    assumptions: tuple[Assumption, ...] = ()
    kitchen: str = "Кухня"
    shift: str = "Смена"
    chief: str = "Шеф-повар"
