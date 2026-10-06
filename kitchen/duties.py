"""Каталог обязанностей персонала и типы задач.

Единственный источник правды о том, что и когда делает кухня. Каждая строка
каталога — цитата stage3.md с номером пункта: `source` хранит его рядом с
правилом, чтобы маршрутный лист сам показывал происхождение задачи
(AC04, требование п. 50 — не выдумывать обязанности).

Пять типов задач из п. 34:

  FIXED_TIME          фиксированная точка: 08:00 завтрак, 11:45 бракераж,
                      12:00 обед, 15:00 выдача для раскройного цеха;
  TIME_WINDOW         работа внутри интервала: 14:00–17:00 подготовка;
  CALENDAR_CONDITIONAL условие по дню недели: пн/ср — вечерняя выпечка;
  MENU_DEPENDENT      порождается меню и ТТК: вечерняя и воскресная подготовка;
  OPERATIONAL         текущая вспомогательная работа по ситуации.

Каталог НЕ включает производственные операции из ТТК: они уже порождаются
существующим кодом (kitchen/core/tasks.py из листа «Задачи»). Здесь только
обязанности людей, которых нет в ТТК, плюс якоря, к которым привязывается
производственный расчёт.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

# ---------------------------------------------------------------------------
# Типы задач (п. 34 stage3.md)
# ---------------------------------------------------------------------------

FIXED_TIME = "FIXED_TIME"
TIME_WINDOW = "TIME_WINDOW"
CALENDAR_CONDITIONAL = "CALENDAR_CONDITIONAL"
MENU_DEPENDENT = "MENU_DEPENDENT"
OPERATIONAL = "OPERATIONAL"

TASK_KINDS = (
    FIXED_TIME,
    TIME_WINDOW,
    CALENDAR_CONDITIONAL,
    MENU_DEPENDENT,
    OPERATIONAL,
)

# ---------------------------------------------------------------------------
# Режимы дня (п. 27, 46 stage3.md)
# ---------------------------------------------------------------------------

MODE_NORMAL = "Обычный"
MODE_FRIDAY = "Пятница"
MODE_SATURDAY = "Суббота"
MODE_SUNDAY = "Воскресенье"

DAY_MODES = (MODE_NORMAL, MODE_FRIDAY, MODE_SATURDAY, MODE_SUNDAY)

GENERAL_CLEANING = "GENERAL_CLEANING"
"""Режим окна 14:00–17:00 в пятницу (п. 27)."""

# ---------------------------------------------------------------------------
# Зоны и характер операции (п. 11, 17, 22, 23)
# ---------------------------------------------------------------------------
# Ограничения безопасности должны опираться на явный признак операции и зоны,
# а не на угадывание по буквам в тексте: переформулировка описания не должна
# снимать запрет мыть горячую кухню до 12:00 (п. 17).

ZONE_HOT_KITCHEN = "Горячая кухня"
"""Зона, которую нельзя мыть до 12:00 (п. 17) и моют последней 16:30–16:40 (п. 23)."""

ZONE_HALL = "Обеденный зал"
ZONE_CORRIDOR = "Коридор"
ZONE_PRODUCTION = "Производственные помещения"
ZONE_DISHES = "Посуда"
ZONE_SERVING = "Место выдачи"
ZONE_STORAGE = "Хранение"

ZONES: tuple[str, ...] = (
    ZONE_HOT_KITCHEN,
    ZONE_HALL,
    ZONE_CORRIDOR,
    ZONE_PRODUCTION,
    ZONE_DISHES,
    ZONE_SERVING,
    ZONE_STORAGE,
)

ACT_FLOOR_WASH = "Мойка пола"
"""Мойка пола — единственный вид работы, запрещённый горячей кухне до 12:00."""

ACT_DISH_WASH = "Мойка посуды"
ACT_SURFACE = "Поверхностная уборка"
ACT_DUST = "Сухая уборка"
ACT_GENERAL_WASH = "Генеральная мойка"
ACT_PRODUCTION = "Производство"
ACT_CONTROL = "Контроль"
ACT_SERVICE = "Обслуживание"
ACT_STOCK = "Снабжение"

ACTIONS: tuple[str, ...] = (
    ACT_FLOOR_WASH,
    ACT_DISH_WASH,
    ACT_SURFACE,
    ACT_DUST,
    ACT_GENERAL_WASH,
    ACT_PRODUCTION,
    ACT_CONTROL,
    ACT_SERVICE,
    ACT_STOCK,
)

#: Действия, которые запрещены горячей кухне до 12:00 (п. 17, 40).
HOT_BLOCKED_ACTIONS: frozenset[str] = frozenset({ACT_FLOOR_WASH})

#: Зоны, моющиеся по полу.
FLOOR_ZONES: frozenset[str] = frozenset({ZONE_HOT_KITCHEN})

#: Основы слов, означающих пол. Проверяются по началу, а не вхождением подстроки.
_FLOOR_STEMS: tuple[str, ...] = (
    "пол",
    "полов",
    "пола",
    "полы",
    "полу",
    "пользу",
)

#: Основы слов, означающих мытьё или уборку. Проверяются по началу слова,
#: поэтому одна основа покрывает всё семейство: мыть/мытьё, мойка/мойки,
#: моет/моют, протереть/протер, протирать/протирка.
_WASH_STEMS: tuple[str, ...] = (
    "мыть",
    "моет",
    "мою",
    "мойк",
    "убор",
    "убира",
    "чист",
    "чищ",
    "протер",
    "протир",
    "отмыв",
    "смыв",
    "дезинфек",
)


def _starts_with_stem(text: str, stems: tuple[str, ...]) -> bool:
    """Есть ли в тексте слово, начинающееся с одной из основ."""
    for word in text.replace("-", " ").split():
        if any(word.startswith(stem) for stem in stems):
            return True
    return False


def is_floor_wash_text(operation: str) -> bool:
    """Запасное распознавание мойки пола по тексту описания.

    Нужно только для правил, созданных вне каталога (импорт чужого
    расписания, тестовые правила). Каталог обязан проставлять `zone` и
    `action`, чтобы безопасность не зависела от формулировки.
    """
    text = operation.lower()
    return _starts_with_stem(text, _FLOOR_STEMS) and _starts_with_stem(text, _WASH_STEMS)


@dataclass(frozen=True, slots=True)
class DutyRule:
    """Одно правило из stage3.md.

    duration_min = None означает «длительность в источнике не задана». Такая
    обязанность попадает в план как незаполненное поле (AC15), а не с
    выдуманным числом минут.
    """

    key: str
    source: str
    """Пункт stage3.md, например «п. 7»."""

    operation: str
    role: str
    kind: str
    stage: str
    duration_min: int | None
    roles: tuple[str, ...] = ()
    """Все роли работы. Пусто — одна основная роль."""

    brigade: bool = False
    """True — нужны все роли `roles` одновременно, а не одна из них.

    Выдача завтрака и обеда (п. 6, 16) требует шефа, повара и разнорабочего
    вместе; для остальных работ `roles` — список подходящих ролей, из которых
    планировщик выбирает одну.
    """

    per_role: int = 1
    """Сколько человек на роль. 1 — по п. 6 «1–2 повара» берём одного."""

    fixed_at: time | None = None
    window: tuple[time, time] | None = None
    weekdays: tuple[int, ...] = ()
    """Для CALENDAR_CONDITIONAL: 0 — понедельник … 6 — воскресенье."""

    day_modes: tuple[str, ...] = ()
    """Пусто — действует во всех режимах, кроме субботы и воскресенья."""

    control_point: str = ""
    """Именованная контрольная точка (бракераж)."""

    needs_ttk: bool = False
    """Задача опирается на ТТК и порции, а не только на текст пункта."""

    assign: bool = True
    """False — показывается в общем плане без исполнителя."""

    hard_constraint: str = ""
    """Идентификатор жёсткого ограничения, если это оно."""

    zone: str = ""
    """Зона работы: горячая кухня, зал, коридор, посуда, ..."""

    action: str = ""
    """Характер операции: мойка пола, мойка посуды, производство, ..."""

    note: str = ""

    @property
    def is_production(self) -> bool:
        """Операция производства, а не обслуживание и не контроль.

        Приоритет у явно проставленного `action`: хозяйственная поддержка
        формально числится заготовкой, но производством не является.
        """
        if self.action:
            return self.action == ACT_PRODUCTION
        return self.stage in ("Заготовка", "Приготовление")

    @property
    def is_hot_floor_wash(self) -> bool:
        """Мойка пола горячей кухни — то, что запрещено до 12:00 (п. 17).

        Явный признак операции и зоны. Текст описания используется только как
        запасной путь для правил, созданных вне каталога, и не является
        источником истины: смена формулировки не должна снимать запрет.
        """
        if self.action:
            if self.action not in HOT_BLOCKED_ACTIONS:
                return False
            return not self.zone or self.zone in FLOOR_ZONES
        return is_floor_wash_text(self.operation)

    def applies_to(self, weekday: int, mode: str) -> bool:
        if self.weekdays and weekday not in self.weekdays:
            return False
        if not self.day_modes:
            return mode in (MODE_NORMAL, MODE_FRIDAY)
        return mode in self.day_modes

    def involved_roles(self) -> tuple[str, ...]:
        return self.roles or (self.role,)


def _d(
    key: str,
    source: str,
    operation: str,
    role: str,
    kind: str,
    stage: str,
    duration: int | None,
    *,
    roles: tuple[str, ...] = (),
    per_role: int = 1,
    brigade: bool = False,
    at: time | None = None,
    window: tuple[time, time] | None = None,
    weekdays: tuple[int, ...] = (),
    day_modes: tuple[str, ...] = (),
    control_point: str = "",
    needs_ttk: bool = False,
    assign: bool = True,
    hard_constraint: str = "",
    zone: str = "",
    action: str = "",
    note: str = "",
) -> DutyRule:
    return DutyRule(
        key=key,
        source=source,
        operation=operation,
        role=role,
        kind=kind,
        stage=stage,
        duration_min=duration,
        roles=roles,
        per_role=per_role,
        brigade=brigade,
        fixed_at=at,
        window=window,
        weekdays=weekdays,
        day_modes=day_modes,
        control_point=control_point,
        needs_ttk=needs_ttk,
        assign=assign,
        hard_constraint=hard_constraint,
        zone=zone,
        action=action,
        note=note,
    )


# ---------------------------------------------------------------------------
# Пн–Чт: обычный рабочий день (п. 5–26)
# ---------------------------------------------------------------------------
# Время и окна взяты из stage3.md и потому подтверждены источником. Длительности
# в источнике не заданы (ни одна из обязанностей не имеет числа минут), поэтому
# `duration_min` везде None: длительность приходит из ТТК для порождаемых
# работ или задаётся пользователем в DutySettings (R5, AC15).

DUTIES: tuple[DutyRule, ...] = (
    # ---------------- 07:00 — начало смены поваров (п. 5) ----------------
    _d("breakfast-prep", "п. 5", "Начать подготовку завтрака по меню и ТТК",
       "Повар", MENU_DEPENDENT, "Заготовка", None, at=time(7, 0), needs_ttk=True,
       action=ACT_PRODUCTION,
       note="операции порождаются из ТТК и порций, а не из фиксированной нормы"),
    _d("breakfast-stock", "п. 5",
       "Проверить термосы и воду для чая, чай, сахар, салфетки, чашки, "
       "посуду для завтрака",
       "Повар", TIME_WINDOW, "Заготовка", None, window=(time(7, 0), time(8, 0)),
       zone=ZONE_SERVING, action=ACT_STOCK),

    # ---------------- 08:00 — выдача завтрака (п. 6) ----------------
    _d("breakfast-serving", "п. 6", "Выдача завтрака", "Шеф-повар",
       FIXED_TIME, "Приготовление", None, roles=("Шеф-повар", "Повар", "Разнорабочий"),
       brigade=True, at=time(8, 0), zone=ZONE_SERVING, action=ACT_SERVICE,
       note="участвуют шеф-повар, повар и разнорабочий (п. 6)"),
    _d("breakfast-supply", "п. 6",
       "Принести из хранения полуфабрикаты, бакалею, хлебобулочные изделия",
       "Разнорабочий", TIME_WINDOW, "Заготовка", None,
       window=(time(8, 0), time(8, 30)), zone=ZONE_STORAGE, action=ACT_STOCK),
    _d("dishes-after-breakfast", "п. 6", "Мытьё посуды после завтрака",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(8, 30), time(10, 0)),
       zone=ZONE_DISHES, action=ACT_DISH_WASH),

    # ---------------- 08:30 — после начала завтрака (п. 7) ----------------
    _d("chef-checklists", "п. 7",
       "Проверить чек-листы и запущенные производственные процессы",
       "Шеф-повар", FIXED_TIME, "Контроль", None, at=time(8, 30), action=ACT_CONTROL),
    _d("clean-after-breakfast", "п. 7", "Уборка после завтрака",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(8, 30), time(9, 30)),
       action=ACT_SURFACE,
       note="зона в источнике не названа — уточните в настройках персонала"),
    _d("compote-strain", "п. 7", "Процеживание компотов",
       "Повар", TIME_WINDOW, "Приготовление", None,
       window=(time(8, 30), time(11, 30)), action=ACT_PRODUCTION,
       note="окно начинается в 08:30, но персонал кухни обедает до 09:00 (п. 8)"),

    # ---------------- 09:00–11:30 — подготовка обеда (п. 9) ----------------
    _d("lunch-prep-window", "п. 9",
       "Основная подготовка обеда по меню и ТТК", "Повар",
       MENU_DEPENDENT, "Приготовление", None, window=(time(9, 0), time(11, 30)),
       needs_ttk=True, action=ACT_PRODUCTION,
       note="конкретные операции порождаются из ТТК, а не зашиты в код"),
    _d("lunch-soup-main", "п. 9",
       "Приготовление супа и второго блюда", "Повар",
       MENU_DEPENDENT, "Приготовление", None, window=(time(10, 0), time(11, 0)),
       needs_ttk=True, action=ACT_PRODUCTION),

    # ---------------- Разнорабочий с 09:00 (п. 10) ----------------
    _d("juice-dispenser", "п. 10", "Наполнение сокоохладителя компотом",
       "Разнорабочий", TIME_WINDOW, "Заготовка", None,
       window=(time(9, 0), time(11, 0)), zone=ZONE_SERVING, action=ACT_STOCK),
    _d("metal-dishes", "п. 10", "Мытьё металлической/стальной посуды после завтрака",
       "Разнорабочий", TIME_WINDOW, "Закрытие", None, window=(time(9, 0), time(11, 0)),
       zone=ZONE_DISHES, action=ACT_DISH_WASH),
    _d("stock-up", "п. 10, п. 22",
       "Пополнение кухни инвентарём, посудой и вспомогательными предметами",
       "Разнорабочий", OPERATIONAL, "Заготовка", None,
       window=(time(9, 0), time(14, 0)), zone=ZONE_STORAGE, action=ACT_STOCK),

    # ---------------- Уборка с 09:00 (п. 11) ----------------
    _d("clean-hall", "п. 11", "Уборка обеденного зала",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(9, 0), time(12, 0)),
       zone=ZONE_HALL, action=ACT_SURFACE),
    _d("clean-corridor", "п. 11", "Уборка коридора",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(9, 0), time(12, 0)),
       zone=ZONE_CORRIDOR, action=ACT_SURFACE),
    _d("clean-production", "п. 11", "Уборка производственных помещений",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(9, 0), time(12, 0)),
       zone=ZONE_PRODUCTION, action=ACT_SURFACE),
    _d("wipe-tables", "п. 11", "Протирание столов",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(9, 0), time(12, 0)),
       zone=ZONE_HALL, action=ACT_SURFACE),
    _d("refill-tables", "п. 11",
       "Пополнение солонок, перечниц и салфеток на столах",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(9, 0), time(12, 0)),
       zone=ZONE_HALL, action=ACT_STOCK),

    # ---------------- 10:30 — проверка перед обедом (п. 12) ----------------
    _d("lunch-stock", "п. 12",
       "Проверить термосы, воду для чая, чай, сахар, салфетки, чашки, "
       "посуду для обеда",
       "Разнорабочий", FIXED_TIME, "Заготовка", None, at=time(10, 30),
       zone=ZONE_SERVING, action=ACT_STOCK),

    # ---------------- 11:00 — багеты (п. 13) ----------------
    _d("baguettes", "п. 13", "Выпечь/доготовить багеты и нарезать для обеда",
       "Разнорабочий", FIXED_TIME, "Заготовка", None, at=time(11, 0),
       action=ACT_PRODUCTION),

    # ---------------- 11:00–12:00 — контроль шефа (п. 14) ----------------
    _d("chef-lunch-control", "п. 14",
       "Проверка блюд, доведение вкуса и консистенции, готовность кухни "
       "к выдаче",
       "Шеф-повар", TIME_WINDOW, "Контроль", None,
       window=(time(11, 0), time(12, 0)), action=ACT_CONTROL,
       note="контрольная задача шефа, не операция ТТК"),

    # ---------------- 11:45 — финальный бракераж (п. 15) ----------------
    _d("tasting", "п. 15", "Финальный бракераж обеда",
       "Шеф-повар", FIXED_TIME, "Контроль", None, at=time(11, 45),
       control_point="Бракераж", action=ACT_CONTROL,
       note="исполнитель настраивается; без настройки точка неназначенная"),

    # ---------------- 12:00 — выдача обеда (п. 16) ----------------
    _d("lunch-serving", "п. 16", "Выдача обеда", "Шеф-повар",
       FIXED_TIME, "Приготовление", None, roles=("Шеф-повар", "Повар", "Разнорабочий"),
       brigade=True, at=time(12, 0), zone=ZONE_SERVING, action=ACT_SERVICE,
       note="участвуют шеф-повар, повар и разнорабочий (п. 16)"),
    _d("dishes-during-lunch", "п. 16",
       "Сбор грязной посуды, возврат посуды, пополнение чистой белой посуды, "
       "чашек, ложек и вилок",
       "Разнорабочий", TIME_WINDOW, "Закрытие", None,
       window=(time(12, 0), time(13, 0)), zone=ZONE_DISHES, action=ACT_DISH_WASH),

    # ---------------- 12:15 — сбор посуды (п. 18) ----------------
    _d("dirty-dishes", "п. 18", "Сбор грязной посуды в зоне возврата",
       "Клининг", FIXED_TIME, "Закрытие", None, at=time(12, 15),
       zone=ZONE_DISHES, action=ACT_DISH_WASH),

    # ---------------- 13:00 — конец обеда (п. 19) ----------------
    _d("clean-hall-after-lunch", "п. 19", "Уборка обеденного зала",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(13, 0), time(13, 30)),
       zone=ZONE_HALL, action=ACT_SURFACE),
    _d("white-dishes", "п. 19, п. 20", "Мойка белой посуды",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(13, 0), time(14, 30)),
       zone=ZONE_DISHES, action=ACT_DISH_WASH),
    _d("cleaner-surface", "п. 20", "Поверхностная уборка обеденного зала",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(13, 0), time(13, 30)),
       zone=ZONE_HALL, action=ACT_SURFACE),

    # ---------------- 13:30–14:00 — обед и планирование (п. 21) ----------------
    _d("next-day-planning", "п. 21",
       "Планирование задач на следующий день, анализ необходимой подготовки, "
       "проверка чек-листов",
       "Шеф-повар", MENU_DEPENDENT, "Контроль", None,
       window=(time(13, 30), time(14, 0)), assign=False, needs_ttk=True,
       action=ACT_CONTROL,
       note="окно совпадает с перерывом персонала кухни (п. 21); "
            "назначение не выполняется автоматически"),

    # ---------------- 15:00 — выдача для раскройного цеха (п. 3) ----------------
    _d("extra-serving-prep", "п. 3",
       "Привести в порядок место выдачи, вымыть сокоохладители, "
       "выключить/наполнить чайники и термосы водой",
       "Разнорабочий", FIXED_TIME, "Закрытие", None, at=time(15, 0),
       zone=ZONE_SERVING, action=ACT_SERVICE),
    _d("extra-serving", "п. 3",
       "Выдача питания сотрудникам раскройного цеха (3–6 человек)",
       "Повар", FIXED_TIME, "Приготовление", None, roles=("Повар", "Разнорабочий"),
       at=time(15, 0), zone=ZONE_SERVING, action=ACT_SERVICE,
       note="обычное обслуживание выдачи, отдельной линии и смены нет"),

    # ---------------- 14:00–17:00 — подготовка следующего дня (п. 22) ----------------
    _d("next-day-prep", "п. 22",
       "Заготовка и нарезка продуктов на следующий день по меню, ТТК, "
       "полуфабрикатам и чек-листам",
       "Повар", MENU_DEPENDENT, "Заготовка", None,
       window=(time(14, 0), time(17, 0)), needs_ttk=True, action=ACT_PRODUCTION),
    _d("chef-next-day", "п. 22",
       "Организация подготовки на следующий день, контроль чек-листов, "
       "готовности производства и выполнения задач",
       "Шеф-повар", TIME_WINDOW, "Контроль", None,
       window=(time(14, 0), time(17, 0)), action=ACT_CONTROL),
    _d("helper-support", "п. 22",
       "Хозяйственная поддержка: пополнение инвентаря, помощь по текущим задачам",
       "Разнорабочий", OPERATIONAL, "Заготовка", None,
       window=(time(14, 0), time(17, 0)), zone=ZONE_STORAGE, action=ACT_STOCK),
    _d("clean-all-rooms", "п. 22", "Уборка всех помещений",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(14, 0), time(17, 0)),
       action=ACT_SURFACE,
       note="перечень помещений в источнике не приведён — подтвердите состав"),
    _d("final-kitchen-clean", "п. 22, п. 23", "Финальная мойка горячей кухни",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(16, 30), time(16, 40)),
       zone=ZONE_HOT_KITCHEN, action=ACT_FLOOR_WASH,
       hard_constraint="final_wash_window"),

    # ---------------- 17:00–20:00 — вечерняя смена-продолжение (п. 24, 25, 26) --
    _d("evening-prep", "п. 25",
       "Вечерняя подготовка: компоты, обжаривание костей и каркасов с овощами "
       "для бульонов, разморозка мясных полуфабрикатов, прочее по чек-листам",
       "Повар", MENU_DEPENDENT, "Приготовление", None,
       window=(time(17, 0), time(20, 0)), needs_ttk=True,
       action=ACT_PRODUCTION, day_modes=(MODE_NORMAL,)),
    _d("evening-baking", "п. 25",
       "Дополнительная подготовка/выпечка для завтраков вторника и четверга",
       "Повар", CALENDAR_CONDITIONAL, "Приготовление", None,
       window=(time(17, 0), time(20, 0)), weekdays=(0, 2), needs_ttk=True,
       action=ACT_PRODUCTION, day_modes=(MODE_NORMAL,)),
    _d("evening-helper", "п. 26",
       "Вынос мусорных пакетов, помощь поварам в закрытии смены, пополнение "
       "инвентаря, уборка рабочих мест вместе с командой",
       "Разнорабочий", OPERATIONAL, "Закрытие", None,
       window=(time(17, 0), time(20, 0)), action=ACT_SERVICE,
       day_modes=(MODE_NORMAL, MODE_FRIDAY)),
)

# ---------------------------------------------------------------------------
# Пятница: обычная работа до 14:00, дальше только генеральная уборка (п. 27)
# ---------------------------------------------------------------------------

FRIDAY_DUTIES: tuple[DutyRule, ...] = (
    _d("friday-general-cleaning", "п. 27",
       "Генеральная уборка по режиму GENERAL_CLEANING",
       "Клининг", TIME_WINDOW, "Закрытие", None, window=(time(14, 0), time(17, 0)),
       action=ACT_GENERAL_WASH, day_modes=(MODE_FRIDAY,),
       note="состав работ в источнике не перечислен — см. ПРЕДЛОЖЕНИЕ"),
    _d("friday-clean-support", "п. 27",
       "Помощь по генеральной уборке — только связанные разрешённые работы",
       "Разнорабочий", OPERATIONAL, "Закрытие", None,
       window=(time(14, 0), time(17, 0)), action=ACT_GENERAL_WASH,
       day_modes=(MODE_FRIDAY,)),
)

# ---------------------------------------------------------------------------
# Суббота: отдельный производственный цикл (п. 28) и отчёт (п. 29, 30)
# Окно смены и длительности в источнике не заданы: заполняются настройками.
# ---------------------------------------------------------------------------

SATURDAY_DUTIES: tuple[DutyRule, ...] = (
    _d("sat-vegetables", "п. 28",
       "Чистка и вакуумирование овощей на неделю",
       "Разнорабочий", MENU_DEPENDENT, "Заготовка", None,
       window=None, day_modes=(MODE_SATURDAY,),
       needs_ttk=True, action=ACT_PRODUCTION,
       note="график и длительность в источнике не заданы — заполните в настройках"),
    _d("sat-meat", "п. 28",
       "Обработка мясных заготовок и формирование мясных полуфабрикатов",
       "Повар", MENU_DEPENDENT, "Заготовка", None,
       window=None, day_modes=(MODE_SATURDAY,),
       needs_ttk=True, action=ACT_PRODUCTION,
       note="график и длительность в источнике не заданы — заполните в настройках"),
)

# ---------------------------------------------------------------------------
# Воскресенье: план порождается меню понедельника (п. 31, 47)
# Статического списка работ нет: операции приходят из ТТК понедельника.
# ---------------------------------------------------------------------------

SUNDAY_DUTIES: tuple[DutyRule, ...] = (
    _d("sunday-prep", "п. 31",
       "Подготовка полуфабрикатов и сырья к меню понедельника",
       "Повар", MENU_DEPENDENT, "Заготовка", None,
       window=None, day_modes=(MODE_SUNDAY,),
       needs_ttk=True, action=ACT_PRODUCTION,
       note="список работ выводится из меню понедельника, ТТК и порций"),
)

ALL_DUTIES: tuple[DutyRule, ...] = DUTIES + FRIDAY_DUTIES + SATURDAY_DUTIES + SUNDAY_DUTIES

DUTIES_BY_KEY: dict[str, DutyRule] = {d.key: d for d in ALL_DUTIES}


# ---------------------------------------------------------------------------
# ПРЕДЛОЖЕНИЕ — ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ (п. 5, 26, 50)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Proposal:
    """Обязанность, которой нет в stage3.md.

    Такие пункты показываются отдельно и никогда не назначаются людям
    автоматически (п. 26 про нарезку лука, п. 50 про новые обязанности).
    """

    topic: str
    text: str
    role: str = ""


PROPOSALS: tuple[Proposal, ...] = (
    Proposal(
        "Нарезка лука разнорабочим",
        "Пункт 26 приводит нарезку лука как пример вспомогательной операции и "
        "прямо запрещает делать её обязательной ежедневной задачей. В "
        "автоматический план не включено.",
        "Разнорабочий",
    ),
    Proposal(
        "Распределение уборщиков 13:00–13:30",
        "Пункт 20 разводит уборщика №1 на поверхностную уборку зала и "
        "уборщика №2 на мойку белой посуды, но состав бригады настраивается. "
        "При одном уборщике обе работы получает он; при двух и более "
        "распределение идёт по номеру в списке персонала.",
        "Клининг",
    ),
    Proposal(
        "Перерывы уборщиков",
        "Пункты 8 и 21 описывают перерыв для «персонала кухни», а уборщики в "
        "нём не названы. Уборщикам перерывы назначены по умолчанию тем же "
        "расписанием; если у них другой график — поменяйте в настройках "
        "персонала.",
        "Клининг",
    ),
    Proposal(
        "Детализация генеральной уборки в пятницу",
        "Пункт 27 задаёт окно 14:00–17:00 и режим GENERAL_CLEANING, но не "
        "перечисляет состав работ. В плане одна задача на окно с пометкой, "
        "что перечень требует подтверждения.",
        "Клининг",
    ),
    Proposal(
        "Состав воскресной смены",
        "Пункт 31 определяет содержание воскресной работы через меню "
        "понедельника, но не называет людей и их часы. По умолчанию "
        "используются повара и разнорабочий с будними часами.",
    ),
    Proposal(
        "Субботние длительности",
        "Пункт 28 перечисляет работы субботы, но не их длительность. В плане "
        "эти поля пустые и должны быть заполнены шефом.",
    ),
)

PROPOSAL_MARK = "ПРЕДЛОЖЕНИЕ — ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ"


# ---------------------------------------------------------------------------
# Контрольные точки (п. 37, 41)
# ---------------------------------------------------------------------------

CONTROL_POINTS: tuple[tuple[str, str], ...] = (
    ("breakfast", "08:00"),
    ("tasting", "11:45"),
    ("lunch", "12:00"),
    ("extra", "15:00"),
    ("final-wash", "16:30"),
    ("evening", "17:00"),
)


@dataclass(frozen=True, slots=True)
class DutySettings:
    """Настройки каталога: что пользователь может переопределить.

    В источнике не заданы длительности части работ и выдач, сроки годности
    полуфабрикатов и исполнитель бракеража. Эти поля здесь — пустые, и план
    показывает их незаполненными, а не подставляет выдуманные значения.

    Окна по понедельникам–пятницам подтверждены stage3.md, поэтому лежат прямо
    в каталоге. Окна субботы (п. 28) и воскресенья (п. 31) в источнике не
    названы, поэтому у соответствующих обязанностей `window is None` и окно
    появляется только после `with_window`.
    """

    duration_overrides: dict[str, int] = field(default_factory=dict)
    window_overrides: dict[str, tuple[time, time]] = field(default_factory=dict)
    disabled: frozenset[str] = frozenset()
    proposals_confirmed: frozenset[str] = frozenset()

    def duration_of(self, duty: DutyRule) -> int | None:
        return self.duration_overrides.get(duty.key, duty.duration_min)

    def window_of(self, duty: DutyRule) -> tuple[time, time] | None:
        return self.window_overrides.get(duty.key, duty.window)

    def with_duration(self, key: str, minutes: int) -> "DutySettings":
        if minutes < 0:
            raise ValueError("длительность не может быть отрицательной")
        merged = dict(self.duration_overrides)
        merged[key] = minutes
        return DutySettings(
            duration_overrides=merged,
            window_overrides=self.window_overrides,
            disabled=self.disabled,
            proposals_confirmed=self.proposals_confirmed,
        )

    def with_window(
        self, key: str, start: time, end: time
    ) -> "DutySettings":
        if start >= end:
            raise ValueError("начало окна должно быть раньше конца")
        merged = dict(self.window_overrides)
        merged[key] = (start, end)
        return DutySettings(
            duration_overrides=self.duration_overrides,
            window_overrides=merged,
            disabled=self.disabled,
            proposals_confirmed=self.proposals_confirmed,
        )

    def with_disabled(self, *keys: str) -> "DutySettings":
        return DutySettings(
            duration_overrides=self.duration_overrides,
            window_overrides=self.window_overrides,
            disabled=frozenset(self.disabled | set(keys)),
            proposals_confirmed=self.proposals_confirmed,
        )


def duties_for(
    weekday: int,
    mode: str,
    settings: DutySettings | None = None,
) -> tuple[DutyRule, ...]:
    """Каталог обязанностей, применимых к дню и режиму.

    Суббота и воскресенье не наследуют будний шаблон: у них отдельный набор
    работ (п. 28, 31).
    """
    cfg = settings or DutySettings()
    out = [
        d
        for d in ALL_DUTIES
        if d.key not in cfg.disabled and d.applies_to(weekday, mode)
    ]
    out.sort(
        key=lambda d: (
            (cfg.window_of(d) or (d.fixed_at or time(0, 0), time(0, 0)))[0],
            # Фиксированные точки (выдача, бракераж) получают исполнителя
            # раньше работ «внутри окна» с тем же началом: иначе занятая
            # выдачами минута помешала бы обязательному участию в них.
            0 if d.kind == FIXED_TIME else 1,
            d.key,
        )
    )
    return tuple(out)


def duties_with_missing_durations(
    weekday: int,
    mode: str,
    settings: DutySettings | None = None,
) -> tuple[DutyRule, ...]:
    """Обязанности, для которых длительность так и не задана.

    Порождаемые работы (`needs_ttk`) сюда не попадают: их длительность
    складывается из операций ТТК и порций, а не из нормы в каталоге.
    """
    cfg = settings or DutySettings()
    return tuple(
        d
        for d in duties_for(weekday, mode, cfg)
        if cfg.duration_of(d) is None and not d.needs_ttk
    )


def duties_with_missing_windows(
    weekday: int,
    mode: str,
    settings: DutySettings | None = None,
) -> tuple[DutyRule, ...]:
    """Работы, для которых окно ещё не подтверждено источником.

    Отдельно от `duties_with_missing_durations`: без окна работу нельзя поставить
    в расписание, без длительности — только посчитать её конец.
    """
    cfg = settings or DutySettings()
    return tuple(
        d
        for d in duties_for(weekday, mode, cfg)
        if d.fixed_at is None
        and cfg.window_of(d) is None
        and d.kind in (TIME_WINDOW, MENU_DEPENDENT, OPERATIONAL)
    )


def control_points_of(duties: tuple[DutyRule, ...]) -> tuple[DutyRule, ...]:
    """Контрольные точки дня — их нельзя превращать в операции ТТК (п. 14)."""
    return tuple(
        d
        for d in duties
        if d.kind == FIXED_TIME and d.stage == "Контроль"
    )


__all__ = [
    "ACTIONS",
    "ACT_CONTROL",
    "ACT_DISH_WASH",
    "ACT_DUST",
    "ACT_FLOOR_WASH",
    "ACT_GENERAL_WASH",
    "ACT_PRODUCTION",
    "ACT_SERVICE",
    "ACT_STOCK",
    "ACT_SURFACE",
    "ALL_DUTIES",
    "CALENDAR_CONDITIONAL",
    "CONTROL_POINTS",
    "DUTIES",
    "DUTIES_BY_KEY",
    "DutyRule",
    "DutySettings",
    "FRIDAY_DUTIES",
    "FIXED_TIME",
    "FLOOR_ZONES",
    "GENERAL_CLEANING",
    "HOT_BLOCKED_ACTIONS",
    "MENU_DEPENDENT",
    "MODE_FRIDAY",
    "MODE_NORMAL",
    "MODE_SATURDAY",
    "MODE_SUNDAY",
    "OPERATIONAL",
    "PROPOSALS",
    "PROPOSAL_MARK",
    "Proposal",
    "SATURDAY_DUTIES",
    "SUNDAY_DUTIES",
    "TASK_KINDS",
    "TIME_WINDOW",
    "ZONES",
    "ZONE_CORRIDOR",
    "ZONE_DISHES",
    "ZONE_HALL",
    "ZONE_HOT_KITCHEN",
    "ZONE_PRODUCTION",
    "ZONE_SERVING",
    "ZONE_STORAGE",
    "control_points_of",
    "duties_for",
    "duties_with_missing_durations",
    "duties_with_missing_windows",
    "is_floor_wash_text",
]