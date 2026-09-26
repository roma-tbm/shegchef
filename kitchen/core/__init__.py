"""Ядро расчётов. Каждый шаг — отдельный модуль с чистыми функциями.

    ТТК → Меню → calculate_ingredients → generate_tasks
         → calculate_backward_timing → build_shift_sheet
"""

from kitchen.core.ingredients import (
    NeedGroup,
    ProductNeed,
    calculate_ingredients,
    group_by_category,
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
    "NeedGroup",
    "ProductNeed",
    "RoleLoad",
    "ScheduledTask",
    "ShiftSheet",
    "Task",
    "TaskBoard",
    "Timing",
    "build_shift_sheet",
    "calculate_backward_timing",
    "calculate_ingredients",
    "generate_tasks",
    "group_by_category",
]
