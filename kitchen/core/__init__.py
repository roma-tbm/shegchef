"""Ядро расчётов. Каждый шаг — отдельный модуль с чистыми функциями.

    ТТК → Меню → calculate_ingredients → generate_tasks
         → calculate_backward_timing → plan_shift → build_shift_sheet

`plan_shift` назначает рассчитанные операции конкретным людям и проверяет
жёсткие ограничения. Модуль не зависит от Excel и Streamlit.
"""

from kitchen.core.constraints import Interval, Violation, check_all
from kitchen.core.ingredients import (
    NeedGroup,
    ProductNeed,
    calculate_ingredients,
    group_by_category,
)
from kitchen.core.planner import (
    Gap,
    PlannedItem,
    Route,
    ShiftPlan,
    plan_shift,
)
from kitchen.core.shiftsheet import ShiftSheet, build_shift_sheet
from kitchen.core.tasks import Task, TaskBoard, generate_tasks
from kitchen.core.timing import (
    RoleLoad,
    ScheduledTask,
    Timing,
    calculate_backward_timing,
)

__all__ = [
    "Gap",
    "Interval",
    "NeedGroup",
    "PlannedItem",
    "ProductNeed",
    "RoleLoad",
    "Route",
    "ScheduledTask",
    "ShiftPlan",
    "ShiftSheet",
    "Task",
    "TaskBoard",
    "Timing",
    "Violation",
    "build_shift_sheet",
    "calculate_backward_timing",
    "calculate_ingredients",
    "check_all",
    "generate_tasks",
    "group_by_category",
    "plan_shift",
]
