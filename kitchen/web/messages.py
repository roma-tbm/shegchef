"""Текстовые задания по ролям для сотрудников.

Чистые функции: на входе посчитанная смена (ShiftSheet из kitchen.core),
на выходе — готовые текстовые сообщения. Модуль не знает про Telegram API,
токены и сеть. Формат — обычный текст: выравнивание по времени, без сложной
Markdown-разметки, которая могла бы сломаться на названиях продуктов
со «звёздочками» и подчёркиваниями.

Каждое сообщение содержит дату и границы смены, имя роли, задачи по времени
с операцией, блюдами, продуктами, продолжительностью и примечаниями, а также
предупреждения, относящиеся к работе этой роли.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from kitchen.core.shiftsheet import RoleLoad, ShiftSheet, TaskLine

#: Полный набор дней недели (Суббота и Воскресенье тоже возможны у дат смены).
RUSSIAN_WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)

#: Имя файла для скачивания по роли, без расширения.
ROLE_SLUGS: dict[str, str] = {
    "Клининг": "клининг",
    "Разнорабочий": "разнорабочий",
    "Повар": "повар",
    "Шеф-повар": "шеф-повар",
    "Смена": "смена",
}


@dataclass(frozen=True, slots=True)
class RoleMessage:
    """Готовое текстовое сообщение для одной роли."""

    role: str
    text: str
    filename: str
    """Слаг для имени файла (кириллица), без расширения."""


def role_filename(role: str) -> str:
    return ROLE_SLUGS.get(role, "сотрудник")


def _hhmm(value: datetime) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _span(start: datetime, end: datetime) -> str:
    if start.date() != end.date():
        return f"{_hhmm(start)} → {_hhmm(end)} (+1 сут)"
    return f"{_hhmm(start)}–{_hhmm(end)}"


def _day_label(day: date) -> str:
    return f"{day:%d.%m.%Y} ({RUSSIAN_WEEKDAYS[day.weekday()]})"


def sorted_tasks(tasks: Iterable[TaskLine]) -> tuple[TaskLine, ...]:
    """Задачи по возрастанию начала (хронологический порядок).

    Стабильная сортировка: задачи с одинаковым началом сохраняют исходный
    порядок друг относительно друга.
    """
    return tuple(sorted(tasks, key=lambda t: (t.start, t.end)))


def tasks_by_role(sheet: ShiftSheet) -> tuple[tuple[str, tuple[TaskLine, ...]], ...]:
    """Задачи, сгруппированные по ролям, внутри каждой роли — по времени."""
    out: list[tuple[str, tuple[TaskLine, ...]]] = []
    for role, items in sheet.tasks_by_role:
        out.append((role, sorted_tasks(items)))
    return tuple(out)


def role_load(sheet: ShiftSheet, role: str) -> RoleLoad | None:
    for load in sheet.role_load:
        if load.role == role:
            return load
    return None


def role_warnings(sheet: ShiftSheet, role: str) -> tuple[str, ...]:
    """Предупреждения, относящиеся к работе роли.

    Предупреждения про конкретную роль («Роль «Повар»: пик ...») попадают
    только в сообщение этой роли. Общие предупреждения (нет операций на блюдо,
    блюдо не влезает в смену, не хватает по остаткам) получают все роли.
    """
    return tuple(
        warning
        for warning in sheet.warnings
        if not warning.startswith("Роль") or f"«{role}»" in warning
    )


def _task_block(index: int, task: TaskLine) -> str:
    lines = [
        f"{index}) {_span(task.start, task.end)} · {task.stage} · "
        f"{task.duration_min} мин",
        f"   {task.operation}",
    ]
    subjects = ", ".join(task.recipes) if task.recipes else task.source
    if subjects:
        lines.append(f"   Блюдо: {subjects}")
    if task.products:
        lines.append(f"   Продукты: {task.products}")
    if task.note:
        lines.append(f"   Примечание: {task.note}")
    return "\n".join(lines)


def _warnings_block(sheet: ShiftSheet, role: str) -> str:
    relevant = role_warnings(sheet, role)
    if not relevant:
        return ""
    body = "\n".join(f"- {w}" for w in relevant)
    return f"\n\nВАЖНО\n{body}"


def _header_block(sheet: ShiftSheet, role: str, count: int, load: RoleLoad | None) -> str:
    lines = [
        f"Смена: {_day_label(sheet.day)} · «{sheet.shift}»",
        f"Кухня: {sheet.kitchen}",
        f"Время смены: {_span(sheet.shift_start, sheet.shift_end)}",
        "",
        f"Роль: {role}",
    ]
    if count == 0:
        lines.append("Задач: нет")
    elif load is not None and load.simultaneous > 1:
        lines.append(
            f"Нагрузка: {load.tasks} задач · до {load.simultaneous} "
            f"одновременных — нужно минимум {load.people} чел."
        )
    else:
        lines.append(f"Задач: {count}")
    return "\n".join(lines)


def render_role_message(
    sheet: ShiftSheet,
    role: str,
    tasks: Sequence[TaskLine] = (),
) -> str:
    """Текст задания для одной роли.

    Если задачи не переданы, берутся из смены по роли. Пустая смена —
    без ошибок: в тексте будет «Задач: нет».
    """
    ordered = sorted_tasks(tasks) if tasks else _tasks_of(sheet, role)
    load = role_load(sheet, role)
    blocks = [_task_block(i, t) for i, t in enumerate(ordered, start=1)]
    parts = [_header_block(sheet, role, len(ordered), load)]
    parts.extend(blocks)
    warning_text = _warnings_block(sheet, role)
    if warning_text:
        parts.append(warning_text)
    return "\n".join(parts)


def _tasks_of(sheet: ShiftSheet, role: str) -> tuple[TaskLine, ...]:
    for candidate, items in sheet.tasks_by_role:
        if candidate == role:
            return items
    return ()


def messages_by_role(sheet: ShiftSheet) -> tuple[RoleMessage, ...]:
    """Отдельное сообщение на каждую роль смены.

    Если в смене нет ни одной задачи, возвращается одно сообщение на смену
    в целом — так приложение не падает на пустом меню.
    """
    if not sheet.tasks:
        text = "\n".join(
            [
                f"Смена: {_day_label(sheet.day)} · «{sheet.shift}»",
                f"Кухня: {sheet.kitchen}",
                f"Время смены: {_span(sheet.shift_start, sheet.shift_end)}",
                "",
                "Роль: Смена",
                "Задач: нет",
            ]
        )
        return (
            RoleMessage(role="Смена", text=text, filename=role_filename("Смена")),
        )

    out: list[RoleMessage] = []
    grouped = tasks_by_role(sheet)
    if grouped:
        for role, tasks in grouped:
            out.append(
                RoleMessage(
                    role=role,
                    text=render_role_message(sheet, role, tasks),
                    filename=role_filename(role),
                )
            )
        return tuple(out)

    # Задачи есть, но не привязаны к ролям — должны быть в messages_by_role.
    text = render_role_message(sheet, "Смена", sheet.tasks)
    return (RoleMessage(role="Смена", text=text, filename=role_filename("Смена")),)


def all_messages_text(sheet: ShiftSheet) -> str:
    """Все сообщения одним файлом, разделённые линией."""
    messages = messages_by_role(sheet)
    parts = [message.text for message in messages]
    if not parts:
        return ""
    separator = "\n\n" + "=" * 44 + "\n\n"
    return separator.join(parts) + "\n"


__all__ = [
    "RoleMessage",
    "ROLE_SLUGS",
    "all_messages_text",
    "messages_by_role",
    "render_role_message",
    "role_filename",
    "role_warnings",
    "sorted_tasks",
    "tasks_by_role",
]