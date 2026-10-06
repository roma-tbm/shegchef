"""Факт смены: расход сырья, выход полуфабрикатов и маркировка (AC09).

Почему это отдельный модуль, а не поля `Printable`:

  * расход сырья и выход полуфабриката — разные величины с разными единицами;
    приравнивать их нельзя, а нормы выхода в источнике нет;
  * маркировка полуфабриката обязана содержать название, дату производства
    и срок годности (п. 44) — без одного из полей запись не считается
    заполненной;
  * отчёт должен переживать перезапуск приложения, поэтому факт хранится
    рядом с меню и не смешивается со статусом выполнения работ;
  * отчёт ничего не списывает: складские остатки не меняются ни при
    сохранении, ни при загрузке.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping

MARKING_FIELDS: tuple[str, ...] = ("name", "made", "expires")


@dataclass(frozen=True, slots=True)
class SemifinishedOutput:
    """Фактический выход одного полуфабриката за смену."""

    qty: float
    unit: str

    def __post_init__(self) -> None:
        if not self.unit.strip():
            raise ValueError("для выхода полуфабриката нужна единица измерения")
        if self.qty < 0:
            raise ValueError("выход полуфабриката не может быть отрицательным")


@dataclass(frozen=True, slots=True)
class SemifinishedMarking:
    """Маркировка полуфабриката: название, дата производства, срок годности."""

    name: str
    made: date
    expires: date

    @property
    def is_complete(self) -> bool:
        """Все обязательные поля заполнены (п. 44)."""
        return bool(self.name.strip()) and self.made is not None and self.expires is not None

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, value in (
                ("name", self.name),
                ("made", self.made),
                ("expires", self.expires),
            )
            if value is None or (isinstance(value, str) and not value.strip())
        )


class IncompleteReportError(ValueError):
    """Отчёт нельзя сохранить: полуфабрикат остался без маркировки.

    Раньше такой отчёт молча выбрасывался из файла, и человек терял
    накопленный за смену факт, не получая ни слова об ошибке. Теперь запись
    остаётся нетронутой, а в сообщении названы конкретные полуфабрикаты —
    иначе «отчёт не сохранён» невозможно исправить (AC09, AC15).
    """

    def __init__(self, day: date, products: tuple[str, ...]) -> None:
        self.day = day
        self.products = products
        listed = ", ".join(products)
        super().__init__(
            f"отчёт за {day:%d.%m.%Y} неполон: нет выхода или маркировки — {listed}"
        )


@dataclass(frozen=True, slots=True)
class SaturdayReport:
    """Отчёт за субботу целиком."""

    day: date
    consumption: Mapping[str, float] = field(default_factory=dict)
    """Фактический расход сырья: продукт → количество."""

    consumption_units: Mapping[str, str] = field(default_factory=dict)
    """Единица фактического расхода: продукт → единица (п. 42, AC09).

    Единица хранится рядом с количеством, а не берётся из текущего меню: иначе
    после смены или очистки меню расход «5» теряет единицу, а при совпадении
    ключа с выходом полуфабриката подменяется чужой единицей. Пустая единица
    значит «источник не задал» и показывается прочерком, а не догадкой.
    """

    outputs: Mapping[str, SemifinishedOutput] = field(default_factory=dict)
    """Фактический выход полуфабрикатов: продукт → количество и единица."""

    markings: Mapping[str, SemifinishedMarking] = field(default_factory=dict)
    """Маркировка каждого произведённого полуфабриката."""

    @property
    def incomplete_markings(self) -> tuple[str, ...]:
        """Полуфабрикаты без полной маркировки и выхода без маркировки.

        Каждый произведённый полуфабрикат обязан быть назван, иметь дату
        производства и срок годности (п. 44), поэтому отсутствующая
        маркировка — такая же незаполненная ячейка, как и пустая дата.
        """
        broken = {k for k, m in self.markings.items() if not m.is_complete}
        broken |= set(self.outputs) - set(self.markings)
        return tuple(sorted(broken))

    def is_complete(self) -> bool:
        """Отчёт заполнен, если выходы и маркировки согласованы между собой."""
        return not self.incomplete_markings and set(self.outputs) == set(self.markings)


# ---------------------------------------------------------------------------
# Хранение
# ---------------------------------------------------------------------------

REPORT_VERSION = 1


def default_report_path(menu_path: Path | str | None = None) -> Path:
    """Путь к файлу отчётов рядом с хранилищем меню."""
    if menu_path is None:
        return Path.home() / ".shegchef" / "reports.json"
    return Path(menu_path).with_name("reports.json")


def save_report(
    day: date,
    report: SaturdayReport,
    path: Path | str | None = None,
) -> Path:
    """Сохраняет отчёт за день. Складские остатки не затрагиваются.

    Неполный отчёт — ошибка, а не «отменить запись»: файл не меняется вовсе,
    поэтому предыдущий корректный факт и отчёты соседних дней остаются целы.
    Запись файла атомарная: временный файл рядом, затем замена, поэтому
    прерванная запись не оставит битый JSON (AC09).
    """
    target = Path(path) if path is not None else default_report_path()
    if not report.is_complete():
        raise IncompleteReportError(day, report.incomplete_markings)
    payload = _load_raw(target)
    payload.setdefault("version", REPORT_VERSION)
    payload.setdefault("days", {})
    payload["days"][day.isoformat()] = _dump(report)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(target)
    return target


def load_report(
    day: date, path: Path | str | None = None
) -> SaturdayReport | None:
    """Читает сохранённый отчёт; отсутствующий день даёт None, а не ошибку."""
    target = Path(path) if path is not None else default_report_path()
    raw = _load_raw(target).get("days", {}).get(day.isoformat())
    if raw is None:
        return None
    return SaturdayReport(
        day=day,
        consumption={k: float(v) for k, v in (raw.get("consumption") or {}).items()},
        # Старый JSON без единиц читается совместимо: расход остаётся, единица
        # пустая и будет показана как незаполненная, а не подставлена из меню.
        consumption_units={
            str(k): str(v)
            for k, v in (raw.get("consumption_units") or {}).items()
        },
        outputs={
            k: SemifinishedOutput(
                qty=float(v["qty"]), unit=str(v.get("unit") or "")
            )
            for k, v in (raw.get("outputs") or {}).items()
        },
        markings={
            k: SemifinishedMarking(
                name=str(v.get("name") or ""),
                made=_as_date(v.get("made")),
                expires=_as_date(v.get("expires")),
            )
            for k, v in (raw.get("markings") or {}).items()
        },
    )


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _dump(report: SaturdayReport) -> dict[str, Any]:
    return {
        "consumption": {k: float(v) for k, v in report.consumption.items()},
        "consumption_units": {
            k: str(v) for k, v in report.consumption_units.items()
        },
        "outputs": {
            k: {"qty": float(v.qty), "unit": v.unit}
            for k, v in report.outputs.items()
        },
        "markings": {
            k: {
                "name": v.name,
                "made": v.made.isoformat() if v.made else "",
                "expires": v.expires.isoformat() if v.expires else "",
            }
            for k, v in report.markings.items()
        },
    }


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def normalize_report(day: date, report: SaturdayReport) -> SaturdayReport:
    """Отбрасывает незаполненные записи, чтобы они не выглядели готовыми."""
    kept = {k for k, v in report.consumption.items() if float(v) > 0}
    return SaturdayReport(
        day=day,
        consumption={k: float(report.consumption[k]) for k in kept},
        consumption_units={
            k: str(v) for k, v in report.consumption_units.items() if k in kept
        },
        outputs={k: v for k, v in report.outputs.items() if v.qty > 0},
        markings={
            k: v for k, v in report.markings.items() if v.is_complete
        },
    )


__all__ = [
    "MARKING_FIELDS",
    "REPORT_VERSION",
    "IncompleteReportError",
    "SaturdayReport",
    "SemifinishedMarking",
    "SemifinishedOutput",
    "default_report_path",
    "load_report",
    "normalize_report",
    "save_report",
]
