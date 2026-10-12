"""Единый набор данных: пути, слияние menu.json и меню.json, ошибки (AC06, AC07).

Проверяются не только успешные чтения, но и различия «дата отсутствует» и
«дата записана пустой», конфликт двух хранилищ и отказ перезаписывать
повреждённый файл.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from kitchen.models import MenuLine
from kitchen.web import menu_store
from kitchen.web.dataset import (
    DataStoreError,
    Dataset,
    MenuConflictError,
    default_data_dir,
)

DAY = date(2026, 10, 5)
OTHER = date(2026, 10, 6)


def _line(name: str, portions: int = 50, day: date = DAY) -> MenuLine:
    return MenuLine(name, portions, time(12, 0), "Обед", day)


def test_default_data_dir_uses_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    assert default_data_dir() == tmp_path


def test_non_overlapping_dates_merge_across_both_files(tmp_path) -> None:
    """Разные даты в menu.json и меню.json доступны совместно."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({DAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu)
    menu_store.save_days({OTHER.isoformat(): (_line("Рассольник", day=OTHER),)}, ds.menu_alt)

    days = ds.load_menu_days()

    assert set(days) == {DAY.isoformat(), OTHER.isoformat()}
    assert [line.recipe for line in days[DAY.isoformat()]] == ["Солянка мясная"]


def test_matching_records_in_both_files_merge(tmp_path) -> None:
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({DAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu)
    menu_store.save_days({DAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu_alt)

    days = ds.load_menu_days()

    assert set(days) == {DAY.isoformat()}


def test_conflicting_records_raise_and_keep_files(tmp_path) -> None:
    """Разные записи одной даты — понятная ошибка до записи, файлы целы."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({DAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu)
    menu_store.save_days({DAY.isoformat(): (_line("Рассольник"),)}, ds.menu_alt)

    with pytest.raises(MenuConflictError) as exc:
        ds.load_menu_days()

    assert DAY.isoformat() in str(exc.value)
    assert ds.menu.exists() and ds.menu_alt.exists()


def test_empty_vs_nonempty_is_a_conflict(tmp_path) -> None:
    """Сознательно пустая запись и непустая — конфликт, а не тихий выбор."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({DAY.isoformat(): ()}, ds.menu)
    menu_store.save_days({DAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu_alt)

    with pytest.raises(MenuConflictError):
        ds.load_menu_days()


def test_explicit_file_bypasses_the_other_store(tmp_path) -> None:
    """Явно выбранный файл разрешает конфликт источников."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({DAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu)
    menu_store.save_days({DAY.isoformat(): (_line("Рассольник"),)}, ds.menu_alt)

    days = ds.load_menu_days(only=ds.menu)

    assert [line.recipe for line in days[DAY.isoformat()]] == ["Солянка мясная"]


def test_corrupt_store_raises_without_touching_it(tmp_path) -> None:
    """Повреждённое хранилище — ошибка; файл остаётся как был."""
    ds = Dataset.in_dir(tmp_path)
    ds.menu.write_text("{это не json", encoding="utf-8")

    with pytest.raises(DataStoreError):
        ds.load_menu_days()

    assert ds.menu.read_text(encoding="utf-8") == "{это не json"


def test_saved_empty_date_differs_from_missing(tmp_path) -> None:
    """Пустая запись даты отличима от отсутствующей даты."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({DAY.isoformat(): ()}, ds.menu)

    days = ds.load_menu_days()

    assert DAY.isoformat() in days and days[DAY.isoformat()] == ()
    assert OTHER.isoformat() not in days


# --- R6: null и другие скаляры — это ошибка, а не отсутствие файла ----------


@pytest.mark.parametrize("text", ["null", "false", "true", "12", "1.5", '"строка"', "[]"])
def test_scalar_json_is_error_not_missing_file(tmp_path, text: str) -> None:
    """Существующий файл со скаляром (включая null) — ошибка, а не пустота (R6)."""
    ds = Dataset.in_dir(tmp_path)
    ds.menu.write_text(text, encoding="utf-8")

    with pytest.raises(DataStoreError):
        ds.load_menu_days()

    assert ds.menu.read_text(encoding="utf-8") == text


def test_scalar_json_in_alt_name_is_error(tmp_path) -> None:
    """null во втором имени меню виден так же, а не считается отсутствием (R6)."""
    ds = Dataset.in_dir(tmp_path)
    menu_store.save_days({DAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu)
    ds.menu_alt.write_text("null", encoding="utf-8")

    with pytest.raises(DataStoreError):
        ds.load_menu_days()


def test_explicit_scalar_file_is_error(tmp_path) -> None:
    """Явно выбранный файл со скаляром тоже ошибка, а не пустое меню (R6)."""
    ds = Dataset.in_dir(tmp_path)
    ds.menu.write_text("null", encoding="utf-8")

    with pytest.raises(DataStoreError):
        ds.load_menu_days(only=ds.menu)


def test_missing_file_still_is_empty(tmp_path) -> None:
    """Истинное отсутствие файла остаётся допустимым состоянием (R6)."""
    ds = Dataset.in_dir(tmp_path)

    assert ds.load_menu_days() == {}
    assert ds.load_menu_days(only=ds.menu) == {}