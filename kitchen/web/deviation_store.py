"""Отклонения и комментарии, которые вводит человек (V2-06, AC12).

Зачем это отдельный модуль, а не ещё одно поле в плане:

  * отклонение — причина с типом («задержка», «замена продукта», «порции»,
    «оборудование», «другая причина»), а не свободный комментарий: по типу
    понятно, что делать дальше;
  * отклонение и комментарий живут рядом с фактом смены, но отдельно от
    статуса работы: отметка «сделано» не объясняет, почему работа сдвинулась;
  * всё привязано к устойчивому `item_id`: замена блюда не переносит чужое
    отклонение на новую операцию;
  * файл пользователя лежит рядом с меню и не смешивается с исходными данными
    книги (AC16).

Модуль чистый: ни Streamlit, ни Excel.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

from kitchen.core.deviations import (
    DEVIATION_KINDS,
    DEVIATION_LABELS,
    REASON_DELAY,
    Deviation,
)

NOTES_VERSION = 1

#: Пустой выбор типа отклонения в форме: отклонения не было.
NO_DEVIATION_KIND = ""


def default_notes_path(menu_path: Path | str | None = None) -> Path:
    """Путь к файлу отклонений рядом с хранилищем пользователя."""
    if menu_path is None:
        return Path.home() / ".shegchef" / "notes.json"
    return Path(menu_path).with_name("notes.json")


def deviation_kinds() -> tuple[tuple[str, str], ...]:
    """Варианты выбора типа отклонения: (значение, подпись)."""
    return tuple((kind, DEVIATION_LABELS[kind]) for kind in DEVIATION_KINDS)


def deviation_from_values(
    item_id: str,
    kind: str,
    comment: str,
    minutes: object = "",
) -> Deviation:
    """Собирает отклонение из полей формы ввода.

    Минуты читаются как «0» и «» в обоих случаях: стёртое поле и явный ноль —
    одно и то же «не заполнено», и `validate()` такие записи отвергает.
    """
    text = str(minutes).strip()
    parsed = int(text) if text.isdigit() or (
        text.startswith("-") and text[1:].isdigit()
    ) else 0
    return Deviation(
        item_id=item_id.strip(),
        kind=kind.strip(),
        comment=comment.strip(),
        minutes=parsed,
    )


def deviation_form(item_id: str = "") -> dict[str, Any]:
    """Описание полей формы отклонения для интерфейса.

    Пустое значение минут хранится как пустая строка, чтобы форма не
    подставляла ноль и не превращала его в «опоздал на 0 минут».
    """
    return {
        "item_id": item_id,
        "kind": "",
        "comment": "",
        "minutes": "",
        "kinds": deviation_kinds(),
    }


def save_deviations(
    day: date,
    records: Mapping[str, Deviation],
    path: Path | str | None = None,
    *,
    replace: bool = False,
) -> Path:
    """Сохраняет отклонения дня, сохраняя записи остальных дней.

    Некорректная запись отвергается целиком: частично сохранённое отклонение
    без пояснения выглядело бы как факт.

    `replace=True` означает «форма показывает весь день»: записи, которых нет
    в `records`, удаляются. Без этого снятое отклонение осталось бы в файле
    навсегда, и следующая печать показала бы то, чего человек уже убрал.
    """
    target = Path(path) if path is not None else default_notes_path()
    for item_id, deviation in records.items():
        if not str(item_id).strip():
            raise ValueError("отклонение должно быть привязано к работе")
        deviation.validate()
    payload = _load_raw(target)
    payload.setdefault("version", NOTES_VERSION)
    days = payload.setdefault("days", {})
    table = dict(days.get(day.isoformat(), {}))
    stored = {} if replace else dict(table.get("deviations") or {})
    for item_id, deviation in records.items():
        stored[str(item_id)] = {
            "kind": deviation.kind,
            "comment": deviation.comment,
            "minutes": int(deviation.minutes),
        }
    if stored:
        table["deviations"] = stored
    else:
        table.pop("deviations", None)
    if table:
        days[day.isoformat()] = table
    else:
        days.pop(day.isoformat(), None)
    payload["version"] = NOTES_VERSION
    _write_json(target, payload)
    return target


def load_deviations(
    day: date, path: Path | str | None = None
) -> dict[str, Deviation]:
    """Отклонения дня: item_id → причина. Нет дня — пустой словарь."""
    target = Path(path) if path is not None else default_notes_path()
    table = _load_raw(target).get("days", {}).get(day.isoformat(), {})
    raw = table.get("deviations") if isinstance(table, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Deviation] = {}
    for item_id, value in raw.items():
        if not isinstance(value, Mapping):
            continue
        try:
            out[str(item_id)] = Deviation(
                item_id=str(item_id),
                kind=str(value.get("kind") or ""),
                comment=str(value.get("comment") or ""),
                minutes=int(value.get("minutes") or 0),
            )
        except (TypeError, ValueError):
            continue
    return out


def save_day_notes(
    day: date,
    notes: Mapping[str, str],
    path: Path | str | None = None,
    *,
    replace: bool = False,
) -> Path:
    """Сохраняет комментарии дня: item_id → текст.

    Пустой текст означает «комментарий снят», поэтому такая запись удаляется
    вместо того, чтобы оставаться пустой строкой в отчёте.

    `replace=True` означает «форма показывает весь день»: комментарии, которых
    нет в `notes`, удаляются, чтобы снятое не воскресло при следующем расчёте.
    """
    target = Path(path) if path is not None else default_notes_path()
    payload = _load_raw(target)
    payload.setdefault("version", NOTES_VERSION)
    days = payload.setdefault("days", {})
    table = dict(days.get(day.isoformat(), {}))
    stored = {} if replace else dict(table.get("notes") or {})
    for item_id, text in notes.items():
        if not str(item_id).strip():
            raise ValueError("комментарий должен быть привязан к работе")
        value = str(text).strip()
        if value:
            stored[str(item_id)] = value
        else:
            stored.pop(str(item_id), None)
    if stored:
        table["notes"] = stored
    else:
        table.pop("notes", None)
    if table:
        days[day.isoformat()] = table
    else:
        days.pop(day.isoformat(), None)
    payload["version"] = NOTES_VERSION
    _write_json(target, payload)
    return target


def load_day_notes(day: date, path: Path | str | None = None) -> dict[str, str]:
    """Комментарии дня: item_id → текст."""
    target = Path(path) if path is not None else default_notes_path()
    table = _load_raw(target).get("days", {}).get(day.isoformat(), {})
    raw = table.get("notes") if isinstance(table, Mapping) else None
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(k): str(v)
        for k, v in raw.items()
        if str(k).strip() and str(v).strip()
    }


def notes_from_rows(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, str], dict[str, Deviation], tuple[str, ...]]:
    """Собирает комментарии и отклонения из строк формы.

    Возвращает `(комментарии, отклонения, проблемы)`: незаполненные причины не
    отбрасываются молча, а возвращаются списком проблем, чтобы интерфейс мог
    показать, что именно не так. Ничего не пишется в файл, пока список проблем
    не пуст.
    """
    notes: dict[str, str] = {}
    deviations: dict[str, Deviation] = {}
    problems: list[str] = []
    for row in rows:
        item_id = str(row.get("id") or "").strip()
        comment = str(row.get("comment") or "").strip()
        if item_id:
            notes[item_id] = comment
        kind = str(row.get("kind") or "").strip()
        if not kind or kind == NO_DEVIATION_KIND:
            continue
        deviation = deviation_from_values(
            item_id, kind, comment, row.get("minutes") or ""
        )
        try:
            deviation.validate()
        except ValueError as exc:
            problems.append(f"{item_id or '(без работы)'}: {exc}")
            continue
        deviations[item_id] = deviation
    return notes, deviations, tuple(problems)


def save_from_rows(
    day: date,
    rows: Iterable[Mapping[str, Any]],
    path: Path | str | None = None,
) -> tuple[int, tuple[str, ...]]:
    """Сохраняет форму целиком либо ничего: частичная запись вводит в заблуждение.

    Возвращает `(сколько отклонений сохранено, проблемы)`.
    """
    notes, deviations, problems = notes_from_rows(rows)
    if problems:
        return 0, problems
    # Форма показывает все работы дня, поэтому запись заменяет день целиком:
    # снятый комментарий и снятое отклонение должны исчезнуть, а не остаться
    # в файле и неожиданно вернуться в следующей печати.
    save_day_notes(day, notes, path, replace=True)
    save_deviations(day, deviations, path, replace=True)
    return len(deviations), ()


def saved_rows(
    day: date, path: Path | str | None = None
) -> tuple[dict[str, str], ...]:
    """Сохранённые записи дня для показа человеку: работа, причина, минуты."""
    notes = load_day_notes(day, path)
    deviations = load_deviations(day, path)
    out: list[dict[str, str]] = []
    for item_id in sorted(set(notes) | set(deviations)):
        record = deviations.get(item_id)
        out.append(
            {
                "id": item_id,
                "comment": notes.get(item_id, ""),
                "kind": record.kind if record else "",
                "label": record.label if record else "",
                #: Минуты показываются только для задержки: у остальных типов
                #: их нет, и «0» выглядело бы как измеренное значение.
                "minutes": str(record.minutes)
                if record and record.kind == REASON_DELAY
                else "",
            }
        )
    return tuple(out)


def note_inputs(
    plan_items: tuple[Any, ...],
    notes: Mapping[str, str],
    deviations: Mapping[str, Deviation],
) -> tuple[dict[str, str], dict[str, tuple[str, str, str]]]:
    """Готовое состояние формы для маршрутного листа.

    Возвращает (`comment` по работам, `deviation` как тройки ключ/тип/текст):
    так интерфейс не вычисляет значения сам и не может разойтись с хранилищем.
    """
    comments = {
        item.item_id: notes.get(item.item_id, item.comment)
        for item in plan_items
    }
    marks = {
        item.item_id: (
            deviations[item.item_id].kind,
            deviations[item.item_id].comment,
            str(deviations[item.item_id].minutes or ""),
        )
        if item.item_id in deviations
        else (item.deviation, "", "")
        for item in plan_items
    }
    return comments, marks


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


__all__ = [
    "NOTES_VERSION",
    "default_notes_path",
    "deviation_form",
    "deviation_from_values",
    "deviation_kinds",
    "load_day_notes",
    "load_deviations",
    "NO_DEVIATION_KIND",
    "note_inputs",
    "notes_from_rows",
    "save_from_rows",
    "saved_rows",
    "save_day_notes",
    "save_deviations",
]
