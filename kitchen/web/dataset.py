"""Единое определение путей и загрузка пользовательских данных ShegChef.

Зачем это модуль, а не пути в каждом интерфейсе:

  * CLI раньше искал настройки, комментарии и отклонения рядом с выходной
    книгой, а меню следующего дня — в файле ``меню.json`` рядом с книгой;
    Streamlit работал с ``~/.shegchef/menu.json`` и соседними хранилищами.
    Из-за этого одинаковые дата, меню и отметки давали разный план;
  * один каталог по умолчанию и один набор имён файлов убирают этот разрыв:
    смена выходного пути книги больше не меняет набор данных;
  * имена ``menu.json`` и ``меню.json`` поддерживаются вместе: даты, которых
    нет в одном файле, берутся из другого, а разные записи одной даты дают
    понятную ошибку до записи, а не молчаливый выбор одного источника.

Модуль чистый: ни Streamlit, ни Excel. Каталог по умолчанию можно переопределить
переменной окружения ``SHEGCHEF_DATA_DIR`` или явным путём у CLI (``--data-dir``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from kitchen.models import MenuLine
from kitchen.web import menu_store
from kitchen.web.store_errors import StoreError

#: Переменная окружения, которой можно переопределить общий каталог данных.
DATA_DIR_ENV = "SHEGCHEF_DATA_DIR"

#: Переменная окружения для явного выбора одного файла меню (снятие конфликта).
MENU_FILE_ENV = "SHEGCHEF_MENU_FILE"

#: Общий каталог по умолчанию для CLI и Streamlit.
DEFAULT_DATA_DIR = Path.home() / ".shegchef"

#: Имена файлов внутри каталога данных: одни и те же для обоих интерфейсов.
MENU_NAME = "menu.json"
MENU_ALT_NAME = "меню.json"
SETTINGS_NAME = "settings.json"
NOTES_NAME = "notes.json"
STATUSES_NAME = "statuses.json"
REPORTS_NAME = "reports.json"


class DataStoreError(StoreError):
    """Хранилище пользователя нельзя прочитать: понятная ошибка, а не потеря."""


class MenuConflictError(DataStoreError):
    """Одна дата описана по-разному в ``menu.json`` и ``меню.json``."""

    def __init__(
        self,
        day: str,
        first: Path,
        first_lines: tuple[MenuLine, ...],
        second: Path,
        second_lines: tuple[MenuLine, ...],
    ) -> None:
        self.day = day
        self.first = first
        self.second = second
        first_desc = _describe(first_lines)
        second_desc = _describe(second_lines)
        super().__init__(
            f"конфликт меню за {day}: «{first.name}» — {first_desc}, "
            f"«{second.name}» — {second_desc}. Уберите одну запись или выберите "
            "конкретный файл явно."
        )


def _describe(lines: tuple[MenuLine, ...]) -> str:
    if not lines:
        return "пустое меню"
    return ", ".join(f"{line.recipe} × {line.portions}" for line in lines)


def default_data_dir() -> Path:
    """Общий каталог данных: переменная окружения важнее каталога по умолчанию."""
    raw = os.environ.get(DATA_DIR_ENV)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return DEFAULT_DATA_DIR


def menu_file_override() -> Path | None:
    """Явно выбранный файл меню из окружения — разрешение конфликта без UI."""
    raw = os.environ.get(MENU_FILE_ENV)
    if raw and raw.strip():
        return Path(raw).expanduser()
    return None


def _same_lines(first: tuple[MenuLine, ...], second: tuple[MenuLine, ...]) -> bool:
    """Совпадение записей дня без учёта порядка строк.

    Порядок меню задаётся интерфейсом, а не хранилищем, поэтому две записи с
    одним и тем же составом блюд считаются одинаковыми: иначе копия одного
    меню в двух файлах выглядела бы конфликтом.
    """
    def key(line: MenuLine) -> tuple[Any, ...]:
        return (
            line.recipe,
            line.portions,
            line.serve_at,
            line.meal,
            line.note,
        )

    return sorted(first, key=key) == sorted(second, key=key)


@dataclass(frozen=True, slots=True)
class Dataset:
    """Набор путей пользовательских данных в одном каталоге."""

    root: Path
    menu: Path
    menu_alt: Path
    settings: Path
    notes: Path
    statuses: Path
    reports: Path

    @classmethod
    def in_dir(cls, root: Path | str) -> "Dataset":
        base = Path(root)
        return cls(
            root=base,
            menu=base / MENU_NAME,
            menu_alt=base / MENU_ALT_NAME,
            settings=base / SETTINGS_NAME,
            notes=base / NOTES_NAME,
            statuses=base / STATUSES_NAME,
            reports=base / REPORTS_NAME,
        )

    @classmethod
    def default(cls) -> "Dataset":
        return cls.in_dir(default_data_dir())

    # ------------------------------------------------------------------
    # Меню
    # ------------------------------------------------------------------

    def load_menu_days(
        self, *, only: Path | None = None
    ) -> dict[str, tuple[MenuLine, ...]]:
        """Меню по датам из одного файла или из обоих сразу.

        ``only`` — явно выбранный файл: конфликт с другим хранилищем не
        проверяется, потому что человек сам назвал источник.

        Повреждённый JSON — это ``DataStoreError``, а не пустой словарь:
        пустой словарь позволил бы затереть испорченный файл новыми данными
        (AC06). Дата, записанная пустой, остаётся в результате как пустой
        кортеж и отличима от отсутствующей даты (AC06).
        """
        if only is not None:
            return _load_one(Path(only))

        override = menu_file_override()
        if override is not None:
            return _load_one(override)

        days: dict[str, tuple[MenuLine, ...]] = {}
        owner: dict[str, Path] = {}
        for path in self._menu_candidates():
            parsed = _load_one(path)
            for key, lines in parsed.items():
                if key in days and not _same_lines(days[key], lines):
                    raise MenuConflictError(
                        key, owner[key], days[key], path, lines
                    )
                if key not in days:
                    days[key] = lines
                    owner[key] = path
        return days

    def _menu_candidates(self) -> tuple[Path, ...]:
        """Существующие файлы меню: ``menu.json`` и ``меню.json``."""
        return tuple(path for path in (self.menu, self.menu_alt) if path.exists())

    def menu_write_targets(self, day_key: str) -> tuple[Path, ...]:
        """Куда сохранять правку дня: тот источник, откуда запись прочитана.

        Явно выбранный файл (``SHEGCHEF_MENU_FILE``) — единственный адресат:
        человек сам назвал источник, второй файл не трогаем. Без явного выбора
        запись дня пишется в те существующие файлы, где она уже есть, — иначе
        правка создала бы вторую запись той же даты и конфликт двух имён
        (AC06, R7). Даты без записи сохраняются в основной ``menu.json``.
        """
        override = menu_file_override()
        if override is not None:
            return (Path(override),)
        owners = tuple(
            path for path in self._menu_candidates() if day_key in menu_store.load_days(path)
        )
        if owners:
            return owners
        return (self.menu,)


def _load_one(path: Path) -> dict[str, tuple[MenuLine, ...]]:
    """Читает одно хранилище меню, отвергая повреждённый JSON.

    Разбор строк переиспользует ``menu_store``: формат файла один и тот же,
    поэтому и семантика (пустая дата, битые строки) не может разойтись.

    Особая тонкость — JSON ``null``: ``json.loads`` даёт ``None`` и раньше это
    совпадало с «файла нет». Отсутствие проверяем отдельно по самому файлу,
    поэтому существующий ``null`` (и любой другой скаляр) — ошибка, а не пустое
    меню (R6). Иначе следующее сохранение молча заменило бы чужой файл.
    """
    path = Path(path)
    if not path.exists():
        return {}
    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        raise DataStoreError(
            f"файл меню {path} должен быть словарём JSON, а не {type(payload).__name__}"
        )
    return menu_store.load_days(path)


def _read_json(path: Path) -> Any:
    """Читает JSON существующего файла; синтаксическая ошибка — ``DataStoreError``."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise DataStoreError(
            f"хранилище {path} повреждено или недоступно: {exc}. "
            "Исправьте или уберите файл вручную — данные не перезаписываются."
        ) from exc


__all__ = [
    "DATA_DIR_ENV",
    "DEFAULT_DATA_DIR",
    "DataStoreError",
    "Dataset",
    "MENU_ALT_NAME",
    "MENU_FILE_ENV",
    "MENU_NAME",
    "MenuConflictError",
    "NOTES_NAME",
    "REPORTS_NAME",
    "SETTINGS_NAME",
    "STATUSES_NAME",
    "default_data_dir",
    "menu_file_override",
]