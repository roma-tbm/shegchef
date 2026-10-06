"""Локальное хранение статусов работ смены в JSON.

Статусы набирает не калькулятор, а человек: он отмечает выполненное прямо в
интерфейсе. Поэтому они хранятся отдельно от меню — свой файл, своя дата, тот
же принцип «уже сохранённые дни не затираются».

Ключ статуса — устойчивый `item_id` работы. Статус никогда не переносится на
другую работу: следующий пересчёт восстановит ровно те отметки, которые
пользователь поставил, и ничего не припишет лишнего.

Модуль чистый: ни Streamlit, ни Excel.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

from kitchen.core.planner import STATUSES

STORE_VERSION = 1

#: Отдельный файл рядом с меню: ~/.shegchef/statuses.json
default_status_path = Path.home() / ".shegchef" / "statuses.json"


def default_status_path_for(menu_store: Path) -> Path:
    """Путь к статусам рядом с файлом меню пользователя."""
    return Path(menu_store).parent / "statuses.json"


def normalize_overrides(raw: object) -> tuple[tuple[str, str], ...]:
    """Приводит произвольные данные к списку пар «работа → статус».

    Всё, что не является строкой или не является известным статусом, отбрасывается:
    битая запись не должна ломать пересчёт смены.
    """
    if not isinstance(raw, Mapping):
        return ()
    out: list[tuple[str, str]] = []
    for item_id, status in raw.items():
        key = str(item_id).strip()
        value = str(status).strip().upper()
        if key and value in STATUSES:
            out.append((key, value))
    return tuple(out)


def save_statuses(
    days: Mapping[str, Iterable[tuple[str, str]]],
    path: Path = default_status_path,
) -> None:
    """Записывает статусы по датам, сохраняя остальные дни как были."""
    path = Path(path)
    merged: dict[str, dict[str, str]] = {
        key: dict(pairs) for key, pairs in load_statuses(path).items()
    }
    for key, pairs in days.items():
        table = dict(merged.get(key, {}))
        for item_id, status in pairs:
            table[item_id] = status
        merged[key] = table

    payload = {
        "version": STORE_VERSION,
        "days": {key: table for key, table in sorted(merged.items()) if table},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_statuses(
    path: Path = default_status_path,
) -> dict[str, tuple[tuple[str, str], ...]]:
    """Читает статусы: дата → пары «работа → статус».

    Отсутствующий или повреждённый файл — пустой словарь, а не ошибка.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    raw_days = payload.get("days", {}) if isinstance(payload, Mapping) else {}
    if not isinstance(raw_days, Mapping):
        return {}
    out: dict[str, tuple[tuple[str, str], ...]] = {}
    for key, table in raw_days.items():
        try:
            date.fromisoformat(str(key))
        except ValueError:
            continue
        pairs = normalize_overrides(table)
        if pairs:
            out[str(key)] = pairs
    return out


def overrides_for_day(
    day: date,
    path: Path = default_status_path,
) -> tuple[tuple[str, str], ...]:
    """Статусы одного дня — их и передаёт plan_shift(overrides=...)."""
    return load_statuses(path).get(day.isoformat(), ())


__all__ = [
    "STORE_VERSION",
    "default_status_path",
    "default_status_path_for",
    "load_statuses",
    "normalize_overrides",
    "overrides_for_day",
    "save_statuses",
]