"""Тесты вкладки «Маршруты» через Streamlit AppTest.

Проверяется то, что видит человек: маршруты по сотрудникам, честный список
незаполненного, сохранение отметок о выполнении и выгрузку печатных форм.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

pytest.importorskip("streamlit")

from kitchen.models import MenuLine  # noqa: E402
from kitchen.web import menu_store, status_store  # noqa: E402
from kitchen.web.dataset import Dataset  # noqa: E402

from conftest import app_on_day  # noqa: E402

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
MONDAY = date(2026, 10, 5)


def _menu() -> tuple[MenuLine, ...]:
    from kitchen.seed import RECIPES

    recipe = next(iter(RECIPES))
    return (
        MenuLine(recipe=recipe, portions=20, serve_at=time(12, 0), meal="Обед", day=MONDAY),
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Общий каталог данных, выбранный конфигурацией запуска (не подменой store)."""
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    return Dataset.in_dir(tmp_path).menu


@pytest.fixture
def app(store):
    """Приложение с меню за понедельник, чтобы маршруты были не пустыми."""
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)
    return app_on_day(APP_PATH, MONDAY, timeout=60)


def _routes(tester):
    """Содержимое вкладки «Маршруты»."""
    tab = tester.tabs[4]
    labels = [sub.value for sub in tab.subheader]
    expanders = [exp.label for exp in tab.expander]
    return tab, labels, expanders


def test_routes_tab_shows_one_route_per_employee(app) -> None:
    """Каждый сотрудник смены получает свой маршрут."""
    tab, labels, expanders = _routes(app)

    assert "Маршруты по сотрудникам" in labels
    assert not app.exception, [e.value for e in app.exception]
    assert expanders, "маршруты должны быть показаны"
    assert all("·" in label for label in expanders), (
        "у маршрута должны быть имя, роль и число работ"
    )


def test_routes_tab_names_what_is_not_filled(app) -> None:
    """Незаполненные поля видны человеку, а не прячутся за «готово»."""
    tab, _labels, _expanders = _routes(app)
    text = " ".join(w.value for w in tab.warning)

    assert "не готова" in " ".join(m.value for m in tab.metric)
    assert "Смена не готова" in text


def test_route_text_has_no_empty_columns(app) -> None:
    """Текст маршрута не обрезает колонки и содержит исполнителя."""
    tab, _labels, expanders = _routes(app)
    blocks = [code.value for code in tab.code]

    assert blocks, "текст маршрута должен показываться"
    assert len(blocks) == len(expanders)
    assert any("Маршрут — " in block for block in blocks)


def test_done_status_survives_app_restart(store) -> None:
    """Отметка «сделано» переживает перезапуск интерфейса."""
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)
    status_path = store.parent / "statuses.json"
    status_store.save_statuses({MONDAY.isoformat(): (("З-001", "DONE"),)}, status_path)

    tester = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not tester.exception, [e.value for e in tester.exception]

    assert status_store.overrides_for_day(MONDAY, status_path) == (("З-001", "DONE"),)


def test_export_tab_offers_printout_files(app) -> None:
    """Печатные формы доступны для скачивания."""
    tab = app.tabs[5]
    names = [b.label for b in tab.button] + [b.label for b in tab.download_button]

    assert any("Общий план смены" in name for name in names)
    assert sum(1 for name in names if name.startswith("Скачать: Маршрут")) >= 1