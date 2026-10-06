"""Настройки смены, которые вводит человек, а не код (V2-04, AC04, AC05).

Что именно нужно настроить руками — перечислено в `TASK.md` и в ревью:

  * длительность обязанности (в источнике её нет ни у одной работы);
  * окно работы, если в источнике оно не названо (суббота, воскресенье);
  * численность и персональные графики персонала;
  * исполнитель бракеража;
  * выключение лишней обязанности.

Принцип тот же, что у каталога обязанностей: незаполненное поле остаётся
незаполненным и попадает в план как видимый пропуск, а не заменяется
выдуманным числом минут. Поэтому пустой ввод даёт `DutySettings()` и
`StaffSettings()`, а расчёт — честные пропуски.

Модуль чистый: ни Streamlit, ни Excel. Он только переводит то, что ввёл
человек, в доменные настройки `kitchen.duties` и `kitchen.staff`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any

from kitchen.duties import DutySettings
from kitchen.staff import (
    DEFAULT_BREAKS,
    Employee,
    StaffSettings,
    to_minutes,
)

SETTINGS_VERSION = 1

#: Разделитель диапазона времени в настройках: «13:30–14:30».
RANGE_SEP = "–"
#: Разделитель полей одной строки персонала: «Повар|07:00–20:00|».
FIELD_SEP = "|"

#: Численность будней задаётся этими ключами (п. 4 stage3.md).
COUNT_KEYS: tuple[str, ...] = ("chefs", "cooks", "helper", "cleaners")

#: Ключи настроек для выходных дней.
WEEKEND_KEYS: tuple[str, ...] = ("saturday", "sunday")


def default_settings_path(menu_path: Path | str | None = None) -> Path:
    """Путь к настройкам рядом с хранилищем пользователя.

    Настройки — его данные, поэтому они лежат рядом с меню, а не в репозитории
    приложения.
    """
    if menu_path is None:
        return Path.home() / ".shegchef" / "settings.json"
    return Path(menu_path).with_name("settings.json")


# ---------------------------------------------------------------------------
# Разбор значений, введённых человеком
# ---------------------------------------------------------------------------


def parse_minutes(value: object, what: str = "длительность") -> int:
    """Минуты из строки ввода. Мусор отвергается, а не превращается в 0.

    Ноль длительности означает «человек стёр поле», и это должно быть видно,
    а не тихо съедать работу.
    """
    if isinstance(value, bool):
        raise ValueError(f"{what}: нужно число минут")
    if isinstance(value, int):
        minutes = value
    elif isinstance(value, float):
        minutes = int(value)
    else:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{what}: значение не заполнено")
        try:
            minutes = int(text)
        except ValueError as exc:
            raise ValueError(
                f"{what}: «{text}» — это не число минут"
            ) from exc
    if minutes <= 0:
        raise ValueError(f"{what}: нужно положительное число минут, получено {minutes}")
    return minutes


def parse_clock(value: object, what: str = "время") -> time:
    """Время суток из строки «ЧЧ:ММ».

    Принимаются и «12:30», и «12.30»: точка — обычная опечатка на русской
    раскладке, а не повод отвергать настройку целиком.
    """
    text = str(value).strip()
    if not text:
        raise ValueError(f"{what}: время не заполнено")
    normalized = text.replace(".", ":")
    parts = normalized.split(":")
    if len(parts) in (2, 3) and all(p.isdigit() for p in parts):
        hours = int(parts[0])
        minutes = int(parts[1])
        if 0 <= hours < 24 and 0 <= minutes < 60:
            return time(hours, minutes)
    raise ValueError(f"{what}: «{text}» — это не время в формате ЧЧ:ММ")


def parse_range(value: object, what: str = "окно") -> tuple[time, time]:
    """Диапазон «начало–конец» как пара времён.

    Окно, начинающееся позже конца, — опечатка: такое окно нельзя поставить
    в расписание, поэтому оно отвергается, а не переворачивается.
    """
    text = str(value).strip()
    if RANGE_SEP not in text:
        raise ValueError(f"{what}: нужно «начало–конец», получено «{text}»")
    start_text, _, end_text = text.partition(RANGE_SEP)
    start = parse_clock(start_text, f"{what}: начало")
    end = parse_clock(end_text, f"{what}: конец")
    if to_minutes(start) >= to_minutes(end):
        raise ValueError(
            f"{what}: начало {start:%H:%M} позже конца {end:%H:%M}"
        )
    return start, end


# ---------------------------------------------------------------------------
# Разбор персонала
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StaffRow:
    """Строка ввода персонала до превращения в `Employee`."""

    name: str
    role: str
    start: time
    end: time
    breaks: tuple[tuple[time, time], ...] = DEFAULT_BREAKS

    @property
    def shift(self) -> tuple[time, time]:
        return (self.start, self.end)


def parse_breaks(value: str) -> tuple[tuple[time, time], ...]:
    """Перерывы из строки «09:00–09:30|13:30–14:00». Пусто — как у всех."""
    text = value.strip()
    if not text:
        return DEFAULT_BREAKS
    return tuple(parse_range(chunk, "перерыв") for chunk in text.split(FIELD_SEP) if chunk.strip())


def parse_staff_row(
    key: str, value: str, order: int = 0
) -> StaffRow:
    """Строка «Роль|смена|перерывы», где смена — «07:00–20:00».

    Имя берётся из ключа: маршрут печатается на человека, а не на роль.
    """
    name = key.strip()
    if not name:
        raise ValueError("Персонал: строка без имени")
    parts = [chunk.strip() for chunk in str(value).split(FIELD_SEP)]
    if len(parts) < 2:
        raise ValueError(
            f"Персонал «{name}»: нужно «роль|смена» и, например, «07:00–20:00»"
        )
    role = parts[0]
    if not role:
        raise ValueError(f"Персонал «{name}»: не указана роль")
    start, end = parse_range(parts[1], f"Персонал «{name}»: смена")
    raw_breaks = parts[2] if len(parts) > 2 else ""
    return StaffRow(
        name=name,
        role=role,
        start=start,
        end=end,
        breaks=parse_breaks(raw_breaks),
    )


def parse_staff_block(value: str, order_start: int = 0) -> tuple[StaffRow, ...]:
    """Несколько строк персонала: по одной на строке, состав выходного дня."""
    rows: list[StaffRow] = []
    for offset, line in enumerate(str(value).splitlines()):
        if line.strip():
            rows.append(_row_from_line(line, order_start + offset))
    return tuple(rows)


def _row_from_line(line: str, order: int) -> StaffRow:
    parts = [chunk.strip() for chunk in line.split(FIELD_SEP)]
    if len(parts) < 3:
        raise ValueError(
            f"Персонал: «{line}» — нужно «имя|роль|смена», например "
            "«Повар 1|Повар|07:00–20:00»"
        )
    name, role = parts[0], parts[1]
    if not name or not role:
        raise ValueError(f"Персонал: «{line}» — нужно имя и роль")
    start, end = parse_range(parts[2], f"Персонал «{name}»: смена")
    breaks = parse_breaks(parts[3] if len(parts) > 3 else "")
    return StaffRow(name=name, role=role, start=start, end=end, breaks=breaks)


def employees_from_rows(rows: Sequence[StaffRow]) -> tuple[Employee, ...]:
    """Строки ввода → сотрудники с порядком обхода."""
    return tuple(
        Employee(
            name=row.name,
            role=row.role,
            shift_start=row.start,
            shift_end=row.end,
            breaks=row.breaks,
            order=index,
        )
        for index, row in enumerate(rows)
    )


# ---------------------------------------------------------------------------
# Перевод ввода в доменные настройки
# ---------------------------------------------------------------------------


def _text(raw: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value not in (None, "", (), []):
            return str(value).strip()
    return ""


def _mapping(raw: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def duty_settings_from(raw: Mapping[str, Any]) -> DutySettings:
    """Длительности, окна и выключенные обязанности из ввода пользователя.

    Чистая функция: настройки читаются из того же словаря, который сохранил
    и загрузил `settings_store`, поэтому форма и расчёт не могут разойтись.
    """
    durations: dict[str, int] = {}
    for key, value in _mapping(raw, "durations").items():
        text = str(value).strip()
        if not text:
            continue
        durations[str(key)] = parse_minutes(text, f"Длительность «{key}»")

    windows: dict[str, tuple[time, time]] = {}
    for key, value in _mapping(raw, "windows").items():
        if isinstance(value, (list, tuple)) and len(value) == 2:
            text = f"{value[0]}{RANGE_SEP}{value[1]}"
        else:
            text = str(value).strip()
        if not text:
            continue
        windows[str(key)] = parse_range(text, f"Окно «{key}»")

    disabled = {
        str(key)
        for key in (raw.get("disabled") or ())
        if str(key).strip()
    }
    return DutySettings(
        duration_overrides=durations,
        window_overrides=windows,
        disabled=frozenset(disabled),
        proposals_confirmed=frozenset(
            str(key) for key in (raw.get("confirmed") or ()) if str(key).strip()
        ),
    )


def staff_settings_from(raw: Mapping[str, Any]) -> StaffSettings:
    """Персонал из ввода пользователя: численность, графики, выходные, бракераж.

    Пустой ввод даёт пустой состав: подставлять «Повар 1» без спроса нельзя,
    план обязан показать, что состав не задан.
    """
    staff_raw = _mapping(raw, "staff")
    counts: dict[str, int] = {}
    rows: list[StaffRow] = []
    for key, value in staff_raw.items():
        name = str(key).strip()
        if name in COUNT_KEYS:
            text = str(value).strip()
            if not text:
                continue
            counts[name] = parse_minutes(text, f"Численность «{name}»")
            continue
        if name in WEEKEND_KEYS:
            continue
        rows.append(parse_staff_row(name, value, len(rows)))

    for key in COUNT_KEYS:
        text = _text(raw, key)
        if text and key not in counts:
            counts[key] = parse_minutes(text, f"Численность «{key}»")

    #: Состав выходного дня вводится блоком: несколько строк, по одной на человека.
    weekend: dict[str, tuple[Employee, ...]] = {}
    for key in WEEKEND_KEYS:
        text = str(staff_raw.get(key) or "").strip()
        if text:
            weekend[key] = employees_from_rows(parse_staff_block(text))

    return StaffSettings(
        cooks=counts.get("cooks", 0),
        helper=counts.get("helper", 0),
        chefs=counts.get("chefs", 0),
        cleaners=counts.get("cleaners", 0),
        staff=employees_from_rows(rows),
        tasting_executor=_text(raw, "tasting", "tasting_executor"),
        **(
            {
                "saturday": weekend["saturday"],
                "sunday": weekend["sunday"],
            }
            if weekend
            else {}
        ),
    )


def tasting_executor_of(raw: Mapping[str, Any]) -> str:
    """Исполнитель бракеража из ввода пользователя (п. 15)."""
    return _text(raw, "tasting", "tasting_executor")


def day_inputs_to_settings(
    raw: Mapping[str, Any],
) -> tuple[DutySettings, StaffSettings]:
    """Оба набора настроек разом — так их и использует расчёт смены."""
    return duty_settings_from(raw), staff_settings_from(raw)


def settings_form(day: date | None = None) -> tuple[tuple[str, str], ...]:
    """Подсказки формы: (ключ обязанности, что показать пользователю).

    Список берётся из каталога, а не из кода интерфейса: новая обязанность
    появляется в форме сама и не требует правки UI.
    """
    from kitchen.duties import DUTIES_BY_KEY

    return tuple(
        (key, f"{rule.source} · {rule.operation}")
        for key, rule in DUTIES_BY_KEY.items()
    )


# ---------------------------------------------------------------------------
# Хранение
# ---------------------------------------------------------------------------


def _empty() -> dict[str, Any]:
    """Пустой ввод настроек: те же ключи, что и заполненный.

    Списки `disabled` и `confirmed` присутствуют всегда, иначе «не выключено
    ничего» и «не подтверждено ничего» выглядели бы по-разному при чтении.
    """
    return {
        "durations": {},
        "windows": {},
        "staff": {},
        "tasting": "",
        "disabled": [],
        "confirmed": [],
    }


#: Ключи, которые человек заполняет в форме настроек.
INPUT_KEYS: tuple[str, ...] = ("durations", "windows", "staff", "tasting")
#: Списки, а не скаляры: несколько обязанностей можно выключить или подтвердить.
LIST_KEYS: tuple[str, ...] = ("disabled", "confirmed")


def normalize_settings(raw: object) -> dict[str, Any]:
    """Приводит произвольные данные к структуре ввода настроек.

    Одна структура и для общих настроек, и для настроек дня: человек видит
    одинаковые поля, значит и разбор должен быть один. Битая или чужая запись
    отбрасывается, а не ломает пересчёт смены.
    """
    out = _empty()
    if not isinstance(raw, Mapping):
        return out
    for key in ("durations", "windows", "staff"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            out[key] = {
                str(k): _as_text(v)
                for k, v in value.items()
                if str(k).strip() and str(v).strip()
            }
    for key in LIST_KEYS:
        value = raw.get(key)
        if isinstance(value, (list, tuple, set)):
            out[key] = sorted({str(v).strip() for v in value if str(v).strip()})
    for key in ("tasting", "tasting_executor"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out["tasting"] = value.strip()
    return out


def load_settings(path: Path | str | None = None) -> dict[str, Any]:
    """Общие настройки. Отсутствующий или повреждённый файл — пустые настройки."""
    target = Path(path) if path is not None else default_settings_path()
    payload = _load_raw(target)
    return normalize_settings({k: v for k, v in payload.items() if k != "days"})


def save_settings(raw: Mapping[str, Any], path: Path | str | None = None) -> Path:
    """Сохраняет общие настройки, не трогая настройки отдельных дней.

    Дни и общие настройки живут в одном файле, но это разные записи: запись
    общих настроек не имеет права стирать дневные переопределения, иначе
    сохранение формы одного дня тихо ломает настройку соседнего.
    """
    target = Path(path) if path is not None else default_settings_path()
    payload = _load_raw(target)
    days = payload.get("days")
    payload.clear()
    payload.update(normalize_settings(raw))
    if isinstance(days, dict) and days:
        payload["days"] = days
    payload["version"] = SETTINGS_VERSION
    _write_json(target, payload)
    return target


def day_inputs_of(
    raw: Mapping[str, Any], shared: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Дневной ввод: общие настройки как основа, свои поля их перекрывают.

    Так день можно настроить отдельно, не дублируя всё общее.
    """
    base = normalize_settings(shared)
    own = normalize_settings(raw)
    merged = _empty()
    for key in ("durations", "windows", "staff"):
        merged[key] = {**base[key], **own[key]}
    merged["tasting"] = own["tasting"] or base["tasting"]
    for key in LIST_KEYS:
        merged[key] = own[key] or base[key]
    return merged


def save_day_inputs(
    day: date, raw: Mapping[str, Any], path: Path | str | None = None
) -> Path:
    """Сохраняет ввод дня, сохраняя настройки остальных дней."""
    target = Path(path) if path is not None else default_settings_path()
    payload = _load_raw(target)
    days = payload.setdefault("days", {})
    days[day.isoformat()] = normalize_settings(raw)
    payload["version"] = SETTINGS_VERSION
    _write_json(target, payload)
    return target


def load_day_inputs(day: date, path: Path | str | None = None) -> dict[str, Any]:
    """Ввод дня; без своих полей берутся общие настройки."""
    target = Path(path) if path is not None else default_settings_path()
    payload = _load_raw(target)
    shared = {k: v for k, v in payload.items() if k not in ("days", "version")}
    raw = (payload.get("days") or {}).get(day.isoformat())
    return day_inputs_of(raw or {}, shared)


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{value[0]}{RANGE_SEP}{value[1]}"
    return str(value)


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
    "COUNT_KEYS",
    "FIELD_SEP",
    "RANGE_SEP",
    "SETTINGS_VERSION",
    "StaffRow",
    "WEEKEND_KEYS",
    "day_inputs_of",
    "day_inputs_to_settings",
    "default_settings_path",
    "duty_settings_from",
    "employees_from_rows",
    "load_day_inputs",
    "load_settings",
    "normalize_settings",
    "parse_breaks",
    "parse_clock",
    "parse_minutes",
    "parse_range",
    "parse_staff_block",
    "parse_staff_row",
    "save_day_inputs",
    "save_settings",
    "settings_form",
    "staff_settings_from",
    "tasting_executor_of",
]
