"""Шаг 2: расчёт потребности в продуктах.

Вход: меню (блюдо + порции) и ТТК.
Выход: одна строка на продукт с суммой по всем блюдам + группировка по обработке.
"""

from __future__ import annotations

from dataclasses import dataclass

from kitchen.models import CHECK_STOCK_CATEGORIES, MenuLine, Product, Recipe

#: Заголовок задания для группы категорий.
CATEGORY_ACTION: dict[str, str] = {
    "Овощи": "Подготовка и нарезка сырья",
    "Соленья": "Подготовка солений",
    "Зелень": "Перебор и промывка зелени",
    "Мясо и рыба": "Разделка и приготовление",
    "Грибы": "Перебор и промывка грибов",
    "Бакалея": "Проверить остаток, при необходимости заказать",
    "Молочка": "Проверить остаток, при необходимости заказать",
    "Хлеб и холодильник": "Подготовка к сборке",
}

#: Категории, которые идут в закупку в первую очередь.
SHOPPING_FIRST = ("Овощи", "Соленья", "Мясо и рыба", "Грибы", "Молочка", "Бакалея")


def fmt_qty(value: float) -> str:
    """Человеческий формат числа: 1000 -> «1 000», 2.5 -> «2,5»."""
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}".replace(",", " ")
    return f"{value:.2f}".replace(".", ",").rstrip("0").rstrip(",")


@dataclass(frozen=True, slots=True)
class NeedSource:
    """Из какого блюда и сколько пришло в эту строку."""

    recipe: str
    portions: int
    qty_base: float
    action: str

    def breakdown(self, per_base: float, unit: str) -> str:
        return f"{self.recipe} — {fmt_qty(self.qty_base / per_base)} {unit}"


@dataclass(frozen=True, slots=True)
class ProductNeed:
    """Сводная потребность в одном продукте на всю смену."""

    product: str
    unit: str
    category: str
    storage: str
    owner: str
    qty_base: float
    qty_display: float
    stock: float
    min_stock: float
    purchase_unit: str
    per_base: float
    sources: tuple[NeedSource, ...]

    @property
    def shortage(self) -> float:
        """Сколько не хватает с учётом неснижаемого остатка."""
        return max(0.0, self.qty_display + self.min_stock - self.stock)

    @property
    def is_check_stock(self) -> bool:
        return self.category in CHECK_STOCK_CATEGORIES

    @property
    def status(self) -> str:
        if self.is_check_stock:
            return "Проверить остаток" if self.shortage > 0 else "Остаток есть"
        return "Не хватает" if self.shortage > 0 else "Хватает"

    @property
    def action(self) -> str:
        """Сводное действие: кубик + кольцо + нарезка соломкой."""
        seen: dict[str, None] = {}
        for s in self.sources:
            if s.action:
                seen.setdefault(s.action, None)
        return " + ".join(seen)

    @property
    def breakdown(self) -> str:
        return "; ".join(
            s.breakdown(self.per_base, self.unit) for s in self.sources
        )


@dataclass(frozen=True, slots=True)
class NeedGroup:
    """Задание по одной категории продуктов — это карточка для роли."""

    category: str
    owner: str
    action_header: str
    is_check_stock: bool
    items: tuple[ProductNeed, ...]

    @property
    def shortage_count(self) -> int:
        return sum(1 for i in self.items if i.shortage > 0)

    @property
    def total(self) -> str:
        return f"{len(self.items)} поз."


def calculate_ingredients(
    menu: tuple[MenuLine, ...],
    recipes: dict[str, Recipe],
    products: dict[str, Product],
) -> tuple[ProductNeed, ...]:
    """Суммирует нормы ТТК, умноженные на порции, по названию продукта.

    Картофель из солянки и из пюре схлопывается в одну строку.
    Продукты, которых нет в меню, но у которых задан минимальный запас,
    тоже попадают в результат — чтобы был виден неснижаемый остаток.
    """
    totals: dict[str, float] = {}
    sources: dict[str, list[NeedSource]] = {}

    for line in menu:
        recipe = recipes[line.recipe]
        for ing in recipe.ingredients:
            qty = ing.qty_per_portion * line.portions
            totals[ing.product] = totals.get(ing.product, 0.0) + qty
            bucket = sources.setdefault(ing.product, [])
            merged = next(
                (
                    b
                    for b in bucket
                    if b.recipe == line.recipe and b.action == ing.action
                ),
                None,
            )
            if merged is not None:
                bucket[bucket.index(merged)] = NeedSource(
                    recipe=merged.recipe,
                    portions=merged.portions,
                    qty_base=merged.qty_base + qty,
                    action=merged.action,
                )
            else:
                bucket.append(
                    NeedSource(
                        recipe=line.recipe,
                        portions=line.portions,
                        qty_base=qty,
                        action=ing.action,
                    )
                )

    for name, product in products.items():
        if name not in totals and product.min_stock > 0 and product.is_check_stock:
            totals[name] = 0.0
            sources[name] = []

    needs: list[ProductNeed] = []
    for name, qty_base in totals.items():
        product = products[name]
        per_base = product.per_base or 1.0
        needs.append(
            ProductNeed(
                product=name,
                unit=product.unit,
                category=product.category,
                storage=product.storage,
                owner=product.owner,
                qty_base=qty_base,
                qty_display=qty_base / per_base,
                stock=product.stock,
                min_stock=product.min_stock,
                purchase_unit=product.purchase_unit,
                per_base=per_base,
                sources=tuple(sources.get(name, ())),
            )
        )

    return tuple(sorted(needs, key=lambda n: (n.category, n.product)))


def group_by_category(needs: tuple[ProductNeed, ...]) -> tuple[NeedGroup, ...]:
    """Группирует потребность по типу обработки, а не по блюдам."""
    buckets: dict[str, list[ProductNeed]] = {}
    for need in needs:
        buckets.setdefault(need.category, []).append(need)

    groups: list[NeedGroup] = []
    for category, items in buckets.items():
        groups.append(
            NeedGroup(
                category=category,
                owner=items[0].owner,
                action_header=CATEGORY_ACTION.get(category, "Подготовка"),
                is_check_stock=category in CHECK_STOCK_CATEGORIES,
                items=tuple(sorted(items, key=lambda n: -n.qty_display)),
            )
        )

    order = list(CATEGORY_ACTION)
    groups.sort(key=lambda g: order.index(g.category) if g.category in order else 99)
    return tuple(groups)
