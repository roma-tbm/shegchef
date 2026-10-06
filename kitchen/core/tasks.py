"""Шаг 3: генерация задач из меню и типовых операций.

Правила разбора листа «Задачи»:

  recipe == "*"    — операция относится ко всему приёму пищи и попадает
                     в смену один раз (контроль шефа, закрытие смены);
  shared_key != "" — операция общая для нескольких блюд и делается один раз
                     (например «Подготовка лука» для солянки, котлет, салата);
  anchor == ""     — операция входит в последовательную цепочку блюда;
  stage            — Заготовка / Приготовление / Контроль / Закрытие.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kitchen.models import MenuLine, Recipe, TaskTemplate


@dataclass(frozen=True, slots=True)
class Task:
    """Задача смены, готовая к планированию времени."""

    task_id: str
    role: str
    operation: str
    stage: str
    meal: str
    duration_min: int
    source: str
    """Блюдо для задач блюда; «Смена» для общих операций."""

    anchor: str = ""
    lead_min: int = 0
    shared_key: str = ""
    order: int = 0
    products: tuple[str, ...] = ()
    note: str = ""
    recipes: tuple[str, ...] = ()
    shop: str = ""

    @property
    def detail(self) -> str:
        return ", ".join(self.products)


@dataclass(slots=True)
class TaskBoard:
    """Все задачи смены, разложенные по этапам расчёта времени."""

    tasks: tuple[Task, ...]
    prep: dict[str, tuple[Task, ...]]
    """Заготовки по приёмам пищи, уже слитые по shared_key."""

    chains: dict[str, tuple[Task, ...]]
    """Цепочки приготовления по блюдам."""

    anchors: tuple[Task, ...]
    """Задачи, привязанные к времени выдачи или к концу смены."""

    by_meal: dict[str, tuple[Task, ...]] = field(default_factory=dict)


def _keywords(operation: str) -> tuple[str, ...]:
    """Резервный подбор продуктов, если колонка «Продукты» не заполнена."""
    return tuple(w.lower() for w in operation.split() if len(w) > 4)


def _product_hint(recipe: Recipe, keywords: tuple[str, ...]) -> tuple[str, ...]:
    found: dict[str, None] = {}
    for ing in recipe.ingredients:
        low = ing.product.lower()
        if any(k in low for k in keywords):
            found.setdefault(ing.product, None)
    return tuple(found)


def generate_tasks(
    menu: tuple[MenuLine, ...],
    recipes: dict[str, Recipe],
    templates: tuple[TaskTemplate, ...],
) -> TaskBoard:
    """Превращает меню + типовые операции в задачи смены.

    Общие операции схлопываются в одну задачу: «Подготовка лука репчатого»
    не превращается в три строки, даже если лук нужен трём блюдам.

    Пустое меню — это допустимое состояние: день без блюд даёт пустую доску,
    а не ошибку. Обязанности смены в этом случае планируются отдельно.
    """
    selected = [line for line in menu if line.portions > 0]
    if not selected:
        return TaskBoard((), {}, {}, ())
    by_recipe = {line.recipe: line for line in selected}
    first_by_meal: dict[str, MenuLine] = {}
    anchor_recipe: dict[str, str] = {}
    for line in selected:
        first_by_meal.setdefault(line.meal, line)
        anchor_recipe.setdefault(line.meal, line.recipe)
    meals = list(first_by_meal)

    prep: dict[str, list[Task]] = {}
    chains: dict[str, list[Task]] = {}
    anchors: list[Task] = []
    seen_shared: dict[str, Task] = {}
    all_tasks: list[Task] = []
    seq = 0

    for tpl in templates:
        if tpl.recipe == "*":
            if tpl.anchor == "last_deadline":
                # Закрытие смены — один раз за смену, не за приём пищи.
                targets = [(first_by_meal[meals[-1]], meals[-1], "Смена")]
            else:
                # Контроль и раздача — свой на каждый приём пищи.
                targets = [(first_by_meal[meal], meal, "Смена") for meal in meals]
        elif tpl.recipe in by_recipe:
            targets = [(by_recipe[tpl.recipe], tpl.recipe, recipes[tpl.recipe].name)]
        else:
            continue

        if tpl.meal:
            targets = [t for t in targets if t[0].meal == tpl.meal]

        for line, resolved, source in targets:
            recipe_name = resolved if tpl.recipe != "*" else anchor_recipe[line.meal]
            recipe = recipes[recipe_name]

            if tpl.products:
                products = tuple(p.strip() for p in tpl.products.split(",") if p.strip())
            elif tpl.stage == "Приготовление" and not tpl.anchor and tpl.recipe != "*":
                products = _product_hint(recipe, _keywords(tpl.operation))
            else:
                products = ()

            seq += 1
            task = Task(
                task_id=f"З-{seq:02d}",
                role=tpl.role,
                operation=tpl.operation,
                stage=tpl.stage,
                meal=line.meal,
                duration_min=tpl.duration_min,
                source=source,
                anchor=tpl.anchor,
                lead_min=tpl.lead_min,
                shared_key=tpl.shared_key,
                order=tpl.order,
                products=products,
                note=tpl.note,
                recipes=(recipe.name,),
                shop=recipe.shop,
            )

            if tpl.stage == "Заготовка":
                key = f"{line.meal}|{tpl.shared_key}"
                if key in seen_shared:
                    _absorb(seen_shared[key], task)
                    continue
                seen_shared[key] = task
                prep.setdefault(line.meal, []).append(task)
            elif tpl.anchor:
                anchors.append(task)
            else:
                key = f"{resolved}|{tpl.shared_key}"
                if tpl.shared_key and key in seen_shared:
                    _absorb(seen_shared[key], task)
                    continue
                if tpl.shared_key:
                    seen_shared[key] = task
                chains.setdefault(resolved, []).append(task)

            all_tasks.append(task)

    for chain in chains.values():
        chain.sort(key=lambda t: (t.order, t.task_id))

    merged_prep = {
        meal: tuple(sorted(tasks, key=lambda t: (t.order, t.task_id)))
        for meal, tasks in prep.items()
    }

    by_meal: dict[str, tuple[Task, ...]] = {}
    for meal in meals:
        by_meal[meal] = tuple(
            list(merged_prep.get(meal, ()))
            + [t for c in chains.values() for t in c if t.meal == meal]
            + [t for t in anchors if t.meal == meal]
        )

    return TaskBoard(
        tasks=tuple(all_tasks),
        prep=merged_prep,
        chains={k: tuple(v) for k, v in chains.items()},
        anchors=tuple(anchors),
        by_meal=by_meal,
    )


def _absorb(existing: Task, extra: Task) -> None:
    """Добавляет ещё одно блюдо в уже существующую общую задачу."""
    if existing.duration_min < extra.duration_min:
        object.__setattr__(existing, "duration_min", extra.duration_min)
    if extra.source not in existing.recipes:
        object.__setattr__(
            existing, "recipes", (*existing.recipes, extra.source)
        )
    products = existing.products
    for p in extra.products:
        if p not in products:
            products = (*products, p)
    object.__setattr__(existing, "products", products)
