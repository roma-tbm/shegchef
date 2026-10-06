"""Прикладная логика веб-интерфейса: чистые функции без Streamlit.

Модули этого пакета не знают про Streamlit и Excel. Они работают с обычными
доменными объектами из kitchen.models и результатами kitchen.core, поэтому
их можно тестировать и переиспользовать из любого интерфейса.

    scaling.py    масштабирование порций всего меню
    messages.py   текстовые задания по ролям для выдачи сотрудникам
    menu_store.py локальное хранение меню в JSON (Excel — только экспорт)
    catalog.py    глобальный поиск по ТТК и добавление блюда в меню
    shift_plan.py сквозной сценарий смены и печатные формы
"""

from kitchen.web.catalog import (
    MEAL_ORDER,
    default_serve_at,
    make_menu_line,
    meal_of,
    meal_order,
    search_recipes,
)
from kitchen.web.menu_store import load_days, plan_to_menu, save_days
from kitchen.web.messages import RoleMessage, all_messages_text, messages_by_role
from kitchen.web.scaling import (
    ScaleError,
    scaled_portion,
    scaled_preview,
    scale_menu,
    validate_factor,
)
from kitchen.web.shift_plan import (
    PRINTOUT_FORMS,
    Printable,
    ShiftScenario,
    build_scenario,
    gaps_text,
    general_plan_form,
    route_form,
    routes_by_form,
    saturday_report_form,
)

__all__ = [
    "MEAL_ORDER",
    "PRINTOUT_FORMS",
    "Printable",
    "RoleMessage",
    "ScaleError",
    "ShiftScenario",
    "all_messages_text",
    "build_scenario",
    "default_serve_at",
    "gaps_text",
    "general_plan_form",
    "load_days",
    "make_menu_line",
    "meal_of",
    "meal_order",
    "messages_by_role",
    "plan_to_menu",
    "route_form",
    "routes_by_form",
    "save_days",
    "saturday_report_form",
    "scaled_portion",
    "scaled_preview",
    "scale_menu",
    "search_recipes",
    "validate_factor",
]