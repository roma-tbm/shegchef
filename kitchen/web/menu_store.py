"""Локальное хранение меню в JSON.

Веб-интерфейс не использует Excel как базу данных: книгу он читает только
для экспорта результата, а правки меню хранит в простом JSON-файле. Формат
одноуровневый — словарь «дата → строки меню», поэтому день за днём не
перетирают друг друга.

JSON намеренно прост: его можно открыть и поправить руками, а модуль остаётся
чистым (никакого Streamlit, никакого Excel).

Также здесь живёт разворот недельного плана в строки меню на конкретную дату
(plan_to_menu) — это чистая функция, которая нужна UI и не зависит от Excel.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, time
from pathlib import Path

from kitchen.menu_week import normalize_weekday
from kitchen.models import MenuLine, PlanLine

STORE_VERSION = 1

#: Где web-интерфейс хранит меню. Локальный файл пользователя, не в репозитории.
default_store_path = Path.home() / ".shegchef" / "menu.json"


def _iso(day: date) -> str:
    return day.isoformat()


def serve_at_to_string(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def parse_serve_at(value: object, default: time) -> time:
    if isinstance(value, time):
        return value
    text = str(value).strip()
    for fmt in ("%H:%M", "%H.%M", "%H:%M:%S"):
        try:
            from datetime import datetime

            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return default


def line_to_dict(line: MenuLine) -> dict:
    return {
        "recipe": line.recipe,
        "portions": line.portions,
        "serve_at": serve_at_to_string(line.serve_at),
        "meal": line.meal,
        "note": line.note,
    }


def line_from_dict(raw: Mapping[str, object], day: date, default_meal: str = "Обед") -> MenuLine | None:
    """Строка меню из словаря JSON. Некорректные порции — это «убрали из меню»."""
    recipe = str(raw.get("recipe", "")).strip()
    if not recipe:
        return None
    try:
        portions = int(raw.get("portions", 0))
    except (TypeError, ValueError):
        portions = 0
    if portions <= 0:
        return None
    meal = str(raw.get("meal", "")).strip() or default_meal
    serve = parse_serve_at(raw.get("serve_at"), time(12, 0))
    note = str(raw.get("note", "")).strip()
    return MenuLine(recipe, portions, serve, meal, day, note)


def save_days(
    days: Mapping[str, Iterable[MenuLine]],
    path: Path = default_store_path,
) -> None:
    """Записывает меню по датам в JSON. Порядок строк сохраняется.

    Уже сохранённые дни не теряются: новые записываются поверх, остальные
    остаются как были — так разные дни не затирают друг друга.
    """
    path = Path(path)
    merged = dict(load_days(path))
    for key, lines in days.items():
        merged[key] = tuple(lines)
    payload = {
        "version": STORE_VERSION,
        "days": {
            key: [line_to_dict(line) for line in lines]
            for key, lines in merged.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def load_days(path: Path = default_store_path) -> dict[str, tuple[MenuLine, ...]]:
    """Читает сохранённые меню.

    Дата, которая есть в файле, всегда присутствует в результате — даже если
    строк в ней не осталось (пустой кортеж). Так «сознательно очищенное меню»
    отличается от даты, которой в файле нет вовсе. Некорректные строки внутри
    даты пропускаются, но сама дата остаётся. Отсутствующий или битый файл —
    пустой словарь.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    out: dict[str, tuple[MenuLine, ...]] = {}
    raw_days = payload.get("days", {}) if isinstance(payload, dict) else {}
    for key, lines in raw_days.items():
        try:
            day = date.fromisoformat(str(key))
        except ValueError:
            continue
        if not isinstance(lines, list):
            continue
        out[key] = tuple(
            line
            for raw in lines
            if isinstance(raw, dict)
            and (line := line_from_dict(raw, day)) is not None
        )
    return out


def plan_to_menu(
    plan: Iterable[PlanLine],
    weekday: str,
    day: date,
) -> tuple[MenuLine, ...]:
    """Строки недельного плана за день недели, привязанные к дате.

    То же, что делает CLI с ключом --week, но без чтения книги: для
    веб-интерфейса заготовкой служит kitchen/menu_week.WEEK_PLAN.
    """
    name = normalize_weekday(weekday)
    if name is None:
        raise ValueError(
            f"Неизвестный день недели: «{weekday}». Ожидается один из: "
            "Понедельник…Пятница (или сокращение: пн, вт, ср, чт, пт)."
        )
    return tuple(
        MenuLine(
            recipe=line.recipe,
            portions=line.portions,
            serve_at=line.serve_at,
            meal=line.meal,
            day=day,
            note=line.note,
        )
        for line in plan
        if line.weekday == name
    )


__all__ = [
    "default_store_path",
    "line_from_dict",
    "line_to_dict",
    "load_days",
    "parse_serve_at",
    "plan_to_menu",
    "save_days",
    "serve_at_to_string",
]