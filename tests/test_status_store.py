"""Тесты хранения пользовательских статусов работ.

Статусы вводятся человеком, поэтому файл должен переживать перезапуск,
не терять другие дни и не принимать выдуманные значения.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from kitchen.core.planner import STATUS_DONE, STATUS_IN_PROGRESS
from kitchen.web.status_store import (
    default_status_path_for,
    load_statuses,
    normalize_overrides,
    overrides_for_day,
    save_statuses,
    StatusStoreError,
)

MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)


def test_statuses_survive_a_reload(tmp_path) -> None:
    """Отметка «сделано» не теряется между запусками интерфейса."""
    path = tmp_path / "statuses.json"
    save_statuses({MONDAY.isoformat(): (("З-001", STATUS_DONE),)}, path)

    assert load_statuses(path)[MONDAY.isoformat()] == (("З-001", STATUS_DONE),)
    assert overrides_for_day(MONDAY, path) == (("З-001", STATUS_DONE),)
    assert overrides_for_day(TUESDAY, path) == ()


def test_other_days_are_not_erased(tmp_path) -> None:
    """Новая запись за один день не стирает отметки за другие."""
    path = tmp_path / "statuses.json"
    save_statuses({MONDAY.isoformat(): (("З-001", STATUS_DONE),)}, path)
    save_statuses({TUESDAY.isoformat(): (("З-009", STATUS_IN_PROGRESS),)}, path)

    stored = load_statuses(path)
    assert stored[MONDAY.isoformat()] == (("З-001", STATUS_DONE),)
    assert stored[TUESDAY.isoformat()] == (("З-009", STATUS_IN_PROGRESS),)


def test_same_day_update_replaces_only_that_item(tmp_path) -> None:
    """Повторная замена статуса меняет один пункт, а не весь день."""
    path = tmp_path / "statuses.json"
    save_statuses(
        {MONDAY.isoformat(): (("З-001", STATUS_DONE), ("З-002", STATUS_DONE))},
        path,
    )
    save_statuses({MONDAY.isoformat(): (("З-002", STATUS_IN_PROGRESS),)}, path)

    stored = dict(load_statuses(path)[MONDAY.isoformat()])
    assert stored == {"З-001": STATUS_DONE, "З-002": STATUS_IN_PROGRESS}


def test_unknown_status_and_junk_are_dropped() -> None:
    """Битое значение не должно попасть в расчёт."""
    pairs = normalize_overrides(
        {
            "З-001": "DONE",
            "З-002": "почти готово",
            "З-003": None,
            "": "DONE",
            "З-004": "done",
        }
    )

    assert dict(pairs) == {"З-001": "DONE", "З-004": "DONE"}
    assert normalize_overrides(["З-001"]) == ()
    assert normalize_overrides("DONE") == ()


def test_missing_file_reads_as_empty(tmp_path) -> None:
    """Отсутствие файла — допустимое состояние, а не ошибка."""
    missing = tmp_path / "нет.json"
    assert load_statuses(missing) == {}

    empty = tmp_path / "пустой.json"
    empty.write_text(json.dumps({"days": {"не-дата": {"З-001": "DONE"}}}), encoding="utf-8")
    assert load_statuses(empty) == {}


def test_broken_file_raises_a_clear_error(tmp_path) -> None:
    """Повреждённый файл статусов — понятная ошибка, а не потеря отметок (AC06)."""
    broken = tmp_path / "битый.json"
    broken.write_text("{не json", encoding="utf-8")

    with pytest.raises(StatusStoreError):
        load_statuses(broken)
    assert broken.read_text(encoding="utf-8") == "{не json"


def test_wrong_structure_raises(tmp_path) -> None:
    """Корректный JSON неверной структуры тоже ошибка."""
    wrong = tmp_path / "структура.json"
    wrong.write_text('["не", "словарь"]', encoding="utf-8")

    with pytest.raises(StatusStoreError):
        load_statuses(wrong)


def test_status_path_lives_next_to_the_menu_store(tmp_path) -> None:
    """Файл статусов не должен теряться отдельно от меню."""
    path = default_status_path_for(tmp_path / "меню.json")
    assert path.parent == tmp_path
    assert path.name == "statuses.json"


def test_file_stays_readable_by_hand(tmp_path) -> None:
    """Файл остаётся простым JSON, который можно открыть и поправить."""
    path = tmp_path / "statuses.json"
    save_statuses({MONDAY.isoformat(): (("З-001", STATUS_DONE),)}, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["days"][MONDAY.isoformat()] == {"З-001": "DONE"}