"""Повреждённые хранилища: понятная ошибка, исходные байты целы (AC06, R3).

Проверяются все согласованные хранилища — меню, настройки, комментарии и
отклонения, статусы, отчёты — и отдельно, что отсутствие файла остаётся
допустимым состоянием, а не ошибкой.
"""

from __future__ import annotations

from datetime import date

import pytest

from kitchen.web import deviation_store, menu_store, report_store, settings_store, status_store
from kitchen.web.dataset import Dataset, DataStoreError
from kitchen.web.report_store import SaturdayReport, SemifinishedOutput, SemifinishedMarking

DAY = date(2026, 10, 5)
BROKEN = "{не json"
WRONG = '["не", "словарь"]'


def _prepare(path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return text


# --- меню -----------------------------------------------------------------


def test_menu_missing_is_empty(tmp_path):
    assert menu_store.load_days(tmp_path / "нет.json") == {}


def test_menu_broken_and_wrong_structure_raise(tmp_path):
    broken = tmp_path / "menu.json"
    assert _prepare(broken, BROKEN) == BROKEN
    with pytest.raises(menu_store.MenuStoreError):
        menu_store.load_days(broken)
    assert broken.read_text(encoding="utf-8") == BROKEN

    wrong = tmp_path / "wrong.json"
    _prepare(wrong, WRONG)
    with pytest.raises(menu_store.MenuStoreError):
        menu_store.load_days(wrong)


def test_menu_save_over_broken_keeps_bytes(tmp_path):
    broken = tmp_path / "menu.json"
    _prepare(broken, BROKEN)
    with pytest.raises(menu_store.MenuStoreError):
        menu_store.save_days({DAY.isoformat(): ()}, broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


# --- настройки ------------------------------------------------------------


def test_settings_missing_is_empty(tmp_path):
    loaded = settings_store.load_settings(tmp_path / "нет.json")
    assert loaded["durations"] == {}
    assert loaded["staff"] == {}


def test_settings_broken_raises_and_keeps_bytes(tmp_path):
    broken = tmp_path / "settings.json"
    _prepare(broken, BROKEN)
    with pytest.raises(settings_store.SettingsStoreError):
        settings_store.load_settings(broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


def test_settings_wrong_structure_raises(tmp_path):
    wrong = tmp_path / "settings.json"
    _prepare(wrong, WRONG)
    with pytest.raises(settings_store.SettingsStoreError):
        settings_store.load_settings(wrong)


def test_settings_save_over_broken_keeps_bytes(tmp_path):
    broken = tmp_path / "settings.json"
    _prepare(broken, BROKEN)
    with pytest.raises(settings_store.SettingsStoreError):
        settings_store.save_day_inputs(DAY, {}, broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


# --- комментарии и отклонения --------------------------------------------

def test_notes_missing_is_empty(tmp_path):
    assert deviation_store.load_day_notes(DAY, tmp_path / "нет.json") == {}
    assert deviation_store.load_deviations(DAY, tmp_path / "нет.json") == {}


def test_notes_broken_raises_and_keeps_bytes(tmp_path):
    broken = tmp_path / "notes.json"
    _prepare(broken, BROKEN)
    with pytest.raises(deviation_store.NotesStoreError):
        deviation_store.load_day_notes(DAY, broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


def test_notes_save_over_broken_keeps_bytes(tmp_path):
    broken = tmp_path / "notes.json"
    _prepare(broken, BROKEN)
    with pytest.raises(deviation_store.NotesStoreError):
        deviation_store.save_day_notes(DAY, {"З-1": "комментарий"}, broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


def test_deviations_wrong_structure_raises(tmp_path):
    wrong = tmp_path / "notes.json"
    _prepare(wrong, WRONG)
    with pytest.raises(deviation_store.NotesStoreError):
        deviation_store.load_deviations(DAY, wrong)


# --- статусы --------------------------------------------------------------


def test_statuses_missing_is_empty(tmp_path):
    assert status_store.load_statuses(tmp_path / "нет.json") == {}


def test_statuses_broken_raises_and_keeps_bytes(tmp_path):
    broken = tmp_path / "statuses.json"
    _prepare(broken, BROKEN)
    with pytest.raises(status_store.StatusStoreError):
        status_store.load_statuses(broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


def test_statuses_save_over_broken_keeps_bytes(tmp_path):
    broken = tmp_path / "statuses.json"
    _prepare(broken, BROKEN)
    with pytest.raises(status_store.StatusStoreError):
        status_store.save_statuses({DAY.isoformat(): (("З-1", "DONE"),)}, broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


def test_statuses_wrong_structure_raises(tmp_path):
    wrong = tmp_path / "statuses.json"
    _prepare(wrong, WRONG)
    with pytest.raises(status_store.StatusStoreError):
        status_store.load_statuses(wrong)


# --- отчёты ---------------------------------------------------------------


def _report(day: date) -> SaturdayReport:
    return SaturdayReport(
        day=day,
        consumption={"Картофель": 5.0},
        consumption_units={"Картофель": "кг"},
        outputs={"Пюре": SemifinishedOutput(qty=5.0, unit="кг")},
        markings={"Пюре": SemifinishedMarking("Пюре", day, day)},
    )


def test_report_missing_is_none(tmp_path):
    assert report_store.load_report(DAY, tmp_path / "нет.json") is None


def test_report_broken_raises_and_keeps_bytes(tmp_path):
    broken = tmp_path / "reports.json"
    _prepare(broken, BROKEN)
    with pytest.raises(report_store.ReportStoreError):
        report_store.load_report(DAY, broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


def test_report_save_over_broken_keeps_bytes(tmp_path):
    broken = tmp_path / "reports.json"
    _prepare(broken, BROKEN)
    with pytest.raises(report_store.ReportStoreError):
        report_store.save_report(DAY, _report(DAY), broken)
    assert broken.read_text(encoding="utf-8") == BROKEN


def test_report_wrong_structure_raises(tmp_path):
    wrong = tmp_path / "reports.json"
    _prepare(wrong, WRONG)
    with pytest.raises(report_store.ReportStoreError):
        report_store.load_report(DAY, wrong)


# --- датасет (оба имени меню) --------------------------------------------

def test_dataset_corrupt_keeps_bytes(tmp_path):
    ds = Dataset.in_dir(tmp_path)
    _prepare(ds.menu_alt, BROKEN)
    with pytest.raises(DataStoreError):
        ds.load_menu_days()
    assert ds.menu_alt.read_text(encoding="utf-8") == BROKEN


# --- R6: null и скаляры — ошибка, а не отсутствие файла --------------------
#
# Существующий файл с текстом null, false, числа или строки — это не «нет
# записи»: иначе следующее сохранение молча заменило бы чужой файл. Проверяем
# и чтение, и отказ сохранять поверх, с сохранностью исходных байтов.

SCALARS = ("null", "false", "12", '"строка"')


@pytest.mark.parametrize("text", SCALARS)
def test_menu_scalar_raises_and_save_keeps_bytes(tmp_path, text):
    target = tmp_path / "menu.json"
    _prepare(target, text)
    with pytest.raises(menu_store.MenuStoreError):
        menu_store.load_days(target)
    with pytest.raises(menu_store.MenuStoreError):
        menu_store.save_days({DAY.isoformat(): ()}, target)
    assert target.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text", SCALARS)
def test_settings_scalar_raises_and_save_keeps_bytes(tmp_path, text):
    target = tmp_path / "settings.json"
    _prepare(target, text)
    with pytest.raises(settings_store.SettingsStoreError):
        settings_store.load_settings(target)
    with pytest.raises(settings_store.SettingsStoreError):
        settings_store.save_day_inputs(DAY, {}, target)
    assert target.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text", SCALARS)
def test_notes_scalar_raises_and_save_keeps_bytes(tmp_path, text):
    target = tmp_path / "notes.json"
    _prepare(target, text)
    with pytest.raises(deviation_store.NotesStoreError):
        deviation_store.load_day_notes(DAY, target)
    with pytest.raises(deviation_store.NotesStoreError):
        deviation_store.save_day_notes(DAY, {"З-1": "комментарий"}, target)
    assert target.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text", SCALARS)
def test_statuses_scalar_raises_and_save_keeps_bytes(tmp_path, text):
    target = tmp_path / "statuses.json"
    _prepare(target, text)
    with pytest.raises(status_store.StatusStoreError):
        status_store.load_statuses(target)
    with pytest.raises(status_store.StatusStoreError):
        status_store.save_statuses({DAY.isoformat(): (("З-1", "DONE"),)}, target)
    assert target.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("text", SCALARS)
def test_report_scalar_raises_and_save_keeps_bytes(tmp_path, text):
    target = tmp_path / "reports.json"
    _prepare(target, text)
    with pytest.raises(report_store.ReportStoreError):
        report_store.load_report(DAY, target)
    with pytest.raises(report_store.ReportStoreError):
        report_store.save_report(DAY, _report(DAY), target)
    assert target.read_text(encoding="utf-8") == text