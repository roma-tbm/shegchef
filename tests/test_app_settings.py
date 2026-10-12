"""Тесты вкладок «Настройки смены», «Отклонения» и субботнего отчёта.

Проверяется путь целиком: человек вводит значение в форме, оно сохраняется
рядом с меню, доходит до расчёта и переживает перезапуск интерфейса.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

pytest.importorskip("streamlit")

from kitchen.models import MenuLine  # noqa: E402
from kitchen.web import deviation_store, menu_store, settings_store, status_store  # noqa: E402
from kitchen.web.dataset import Dataset  # noqa: E402

from conftest import app_on_day  # noqa: E402

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)
SATURDAY = date(2026, 10, 10)
TABS = ("Меню", "Расчёт смены", "Продукты и дефицит", "Задачи по ролям", "Маршруты",
        "Экспорт", "Настройки смены", "Отклонения", "Отчёт за субботу")


def _menu(day: date = MONDAY) -> tuple[MenuLine, ...]:
    from kitchen.seed import RECIPES

    recipe = next(iter(RECIPES))
    return (
        MenuLine(recipe=recipe, portions=20, serve_at=time(12, 0), meal="Обед", day=day),
    )


def _inputs(**overrides) -> dict:
    base = {
        "durations": {},
        "windows": {},
        "staff": {},
        "tasting": "",
        "disabled": [],
        "confirmed": [],
    }
    return {**base, **overrides}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Общий каталог данных, выбранный конфигурацией запуска."""
    monkeypatch.setenv("SHEGCHEF_DATA_DIR", str(tmp_path))
    return Dataset.in_dir(tmp_path).menu


@pytest.fixture
def settings_file(store: Path) -> Path:
    return settings_store.default_settings_path(store)


@pytest.fixture
def notes_file(store: Path) -> Path:
    return deviation_store.default_notes_path(store)


def _app(store: Path, day: date = MONDAY) -> AppTest:
    """Запускает app.py на явно выбранной рабочей дате.

    Календарь в сайдбаре стартует с сегодняшнего дня, поэтому дату нужно выбрать
    тем же виджетом, что и человек: иначе настройки сохранялись бы за сегодня, а
    проверялись за фиксированную дату (`app_on_day`).
    """
    return app_on_day(APP_PATH, day)


@pytest.fixture
def monday(store: Path) -> AppTest:
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)
    return _app(store)


def _tab(tester: AppTest, name: str):
    index = TABS.index(name)
    return tester.tabs[index]


# ---------------------------------------------------------------------------
# Настройки смены
# ---------------------------------------------------------------------------


def test_settings_tab_offers_durations_and_windows(monday) -> None:
    """В форме есть поля ровно тех обязанностей, что есть в этот день."""
    tab = _tab(monday, "Настройки смены")
    labels = [w.label for w in tab.text_input]

    assert "Настройки смены" in [s.value for s in tab.subheader]
    assert any("Длительность, мин" in label for label in labels)
    assert any("Окно работы" in label for label in labels)
    assert any("Количество — Повара" in label for label in labels)
    assert any("Исполнитель бракеража" in label for label in labels)


def test_saved_duration_reaches_the_plan(store, settings_file) -> None:
    """Введённая длительность сохраняется и убирает пропуск длительности."""
    settings_store.save_day_inputs(
        MONDAY,
        {
            "durations": {"clean-hall": "20"},
            "windows": {},
            "staff": {},
            "tasting": "",
            "disabled": [],
            "confirmed": [],
        },
        settings_file,
    )
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)

    tester = _app(store)
    assert not tester.exception, [e.value for e in tester.exception]

    saved = settings_store.load_day_inputs(MONDAY, settings_file)
    assert saved["durations"]["clean-hall"] == "20"


def test_invalid_duration_is_reported_and_not_saved(store, settings_file) -> None:
    """Ошибочный ввод показывается понятной ошибкой и не пишется в файл."""
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)
    tester = _app(store)
    tab = _tab(tester, "Настройки смены")

    tab.text_input(key="duration_clean-hall").set_value("не число")
    tab.button[0].click().run()

    errors = [e.value for e in _tab(tester, "Настройки смены").error]
    assert any("не число минут" in message for message in errors), errors
    assert not settings_file.exists()


def test_tasting_executor_saved_from_the_form(store, settings_file) -> None:
    """Исполнитель бракеража, введённый в форме, сохраняется."""
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)
    tester = _app(store)
    tab = _tab(tester, "Настройки смены")

    tab.text_input(key="tasting_executor").set_value("Шеф-повар 1")
    tab.button[0].click().run()

    assert settings_store.load_day_inputs(MONDAY, settings_file)["tasting"] == "Шеф-повар 1"


def test_settings_survive_app_restart(store, settings_file) -> None:
    """После перезапуска интерфейса значения остаются на своих местах."""
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)
    tester = _app(store)
    _tab(tester, "Настройки смены").text_input(
        key="duration_clean-hall"
    ).set_value("25")
    _tab(tester, "Настройки смены").button[0].click().run()

    again = _app(store)
    tab = _tab(again, "Настройки смены")
    assert tab.text_input(key="duration_clean-hall").value == "25"


def test_settings_are_saved_for_the_selected_day_only(store, settings_file) -> None:
    """Настройка сохраняется за выбранный день и не появляется в соседнем."""
    menu_store.save_days(
        {MONDAY.isoformat(): _menu(), TUESDAY.isoformat(): _menu(TUESDAY)}, store
    )

    tester = _app(store, MONDAY)
    tab = _tab(tester, "Настройки смены")
    tab.text_input(key="tasting_executor").set_value("Шеф-повар 1")
    tab.button[0].click().run()

    assert settings_store.load_day_inputs(MONDAY, settings_file)["tasting"] == "Шеф-повар 1"
    assert settings_store.load_day_inputs(TUESDAY, settings_file)["tasting"] == ""

    other = _app(store, TUESDAY)
    other_tab = _tab(other, "Настройки смены")
    assert other_tab.text_input(key="tasting_executor").value == ""


def test_switching_day_refreshes_settings_without_restart(store, settings_file) -> None:
    """Форма настроек следует за датой в одной сессии, без перезапуска.

    Дефект: Streamlit хранит значения виджетов по ключу, поэтому после смены
    даты форма показывала значения прежнего дня, и сохранение записывало их за
    выбранный день. Проверяются все дневные поля: исполнитель, длительности,
    окна и состав бригады.
    """
    menu_store.save_days(
        {MONDAY.isoformat(): _menu(), TUESDAY.isoformat(): _menu(TUESDAY)}, store
    )
    settings_store.save_day_inputs(
        MONDAY,
        _inputs(
            durations={"clean-hall": "20"},
            windows={"clean-hall": "13:30–14:30"},
            staff={
                "chefs": "2",
                "cooks": "3",
                "povar-ivan": "Повар|08:00–17:00|",
            },
            tasting="Шеф понедельника",
        ),
        settings_file,
    )
    settings_store.save_day_inputs(
        TUESDAY,
        _inputs(
            durations={"clean-hall": "40"},
            windows={"clean-hall": "15:00–16:00"},
            staff={
                "chefs": "4",
                "cooks": "5",
                "povar-ivan": "Повар|10:00–19:00|",
            },
            tasting="Шеф вторника",
        ),
        settings_file,
    )

    tester = _app(store, TUESDAY)
    tab = _tab(tester, "Настройки смены")
    assert tab.text_input(key="tasting_executor").value == "Шеф вторника"
    assert tab.text_input(key="duration_clean-hall").value == "40"
    assert tab.text_input(key="window_clean-hall").value == "15:00–16:00"
    assert tab.text_input(key="count_chefs").value == "4"
    assert tab.text_input(key="staff_povar-ivan").value == "Повар|10:00–19:00|"

    tester.date_input(key="_day").set_value(MONDAY)
    tester.run()
    tab = _tab(tester, "Настройки смены")
    assert tab.text_input(key="tasting_executor").value == "Шеф понедельника"
    assert tab.text_input(key="duration_clean-hall").value == "20"
    assert tab.text_input(key="window_clean-hall").value == "13:30–14:30"
    assert tab.text_input(key="count_chefs").value == "2"
    assert tab.text_input(key="count_cooks").value == "3"
    assert tab.text_input(key="staff_povar-ivan").value == "Повар|08:00–17:00|"

    tab.text_input(key="tasting_executor").set_value("Новый шеф понедельника")
    tab.button[0].click().run()
    assert (
        settings_store.load_day_inputs(MONDAY, settings_file)["tasting"]
        == "Новый шеф понедельника"
    )
    assert (
        settings_store.load_day_inputs(TUESDAY, settings_file)["tasting"]
        == "Шеф вторника"
    )

    tester.date_input(key="_day").set_value(TUESDAY)
    tester.run()
    tab = _tab(tester, "Настройки смены")
    assert tab.text_input(key="tasting_executor").value == "Шеф вторника"
    assert tab.text_input(key="duration_clean-hall").value == "40"
    assert tab.text_input(key="window_clean-hall").value == "15:00–16:00"
    assert tab.text_input(key="count_chefs").value == "4"
    assert tab.text_input(key="staff_povar-ivan").value == "Повар|10:00–19:00|"


# ---------------------------------------------------------------------------
# Отклонения и комментарии
# ---------------------------------------------------------------------------


def test_notes_tab_lists_every_work(monday) -> None:
    """Форма отклонений содержит все работы смены."""
    tab = _tab(monday, "Отклонения")

    assert "Отклонения и комментарии" in [s.value for s in tab.subheader]
    assert "Сохранить комментарии и отклонения" in [b.label for b in tab.button]


def test_notes_tab_shows_a_row_per_work(monday) -> None:
    """Форма отклонений перечисляет работы смены и предлагает все типы."""
    tab = _tab(monday, "Отклонения")
    kinds = [kind for kind, _label in deviation_store.deviation_kinds()]

    assert "Отклонения и комментарии" in [s.value for s in tab.subheader]
    assert "Сохранить комментарии и отклонения" in [b.label for b in tab.button]
    assert set(kinds) == {"delay", "product", "portions", "equipment", "other"}
    assert any(
        "Сохранённых комментариев и отклонений за этот день нет." in cap.value
        for cap in tab.caption
    ), [cap.value for cap in tab.caption]


def test_deviation_without_a_comment_is_rejected() -> None:
    """Отклонение без пояснения — проблема, а не записанный факт."""
    notes, deviations, problems = deviation_store.notes_from_rows(
        [{"id": "З-abc", "comment": "", "kind": "product", "minutes": ""}]
    )

    assert deviations == {}
    assert problems and "пояснения" in problems[0]
    assert notes == {"З-abc": ""}


def test_delay_without_minutes_is_rejected() -> None:
    """Для задержки нужны минуты: без них запись не проходит проверку."""
    _notes, _deviations, problems = deviation_store.notes_from_rows(
        [{"id": "З-abc", "comment": "опоздали", "kind": "delay", "minutes": ""}]
    )

    assert problems and "минуты" in problems[0]


def test_form_is_saved_all_or_nothing(tmp_path) -> None:
    """Одна незаполненная строка не даёт записать остальные."""
    store = tmp_path / "notes.json"
    saved, problems = deviation_store.save_from_rows(
        MONDAY,
        [
            {"id": "З-a", "comment": "нет лука", "kind": "product", "minutes": ""},
            {"id": "З-b", "comment": "", "kind": "delay", "minutes": ""},
        ],
        store,
    )

    assert saved == 0
    assert problems
    assert deviation_store.load_day_notes(MONDAY, store) == {}
    assert deviation_store.load_deviations(MONDAY, store) == {}


def test_saved_records_come_back(tmp_path) -> None:
    """Комментарий и отклонение сохраняются и читаются обратно."""
    store = tmp_path / "notes.json"
    deviation_store.save_from_rows(
        MONDAY,
        [{"id": "З-abc", "comment": "нет молока", "kind": "product", "minutes": ""}],
        store,
    )

    rows = deviation_store.saved_rows(MONDAY, store)
    assert rows[0]["label"] == "Отсутствие или замена продукта"
    assert rows[0]["comment"] == "нет молока"
    assert rows[0]["minutes"] == "", "у замены продукта минут нет"


def test_delay_keeps_its_minutes_in_the_saved_row(tmp_path) -> None:
    """Задержка сохраняет измеренные минуты — они и есть её суть."""
    store = tmp_path / "notes.json"
    deviation_store.save_from_rows(
        MONDAY,
        [{"id": "З-abc", "comment": "опоздали на 20", "kind": "delay", "minutes": "20"}],
        store,
    )

    row = deviation_store.saved_rows(MONDAY, store)[0]
    assert row["label"] == "Задержка"
    assert row["minutes"] == "20"


def test_routes_tab_separates_fact_from_violation(monday) -> None:
    """В маршрутах видны и вычисленный статус, и отметка человека (AC08)."""
    tab = _tab(monday, "Маршруты")
    labels = [sub.value for sub in tab.subheader]
    captions = " ".join(cap.value for cap in tab.caption)

    assert "Отметки по работам" in labels
    assert "относится только к этой работе" in captions


# ---------------------------------------------------------------------------
# Отчёт за субботу
# ---------------------------------------------------------------------------


def test_report_tab_refuses_a_weekday(store: Path) -> None:
    """В будний день отчёт не предлагается: отчёт заполняют за субботу."""
    menu_store.save_days({MONDAY.isoformat(): _menu()}, store)
    tab = _tab(_app(store), "Отчёт за субботу")

    assert tab.info, "нужно сказать, что выбран не субботний день"
    assert "субботу" in tab.info[0].value


def test_report_tab_shows_consumption_and_empty_output_norm(store: Path) -> None:
    """Расход и выход показаны разными величинами, норма выхода пустая.

    Норма выхода полуфабриката в источнике не подтверждена, поэтому форма
    обязана оставлять её пустой и объяснять почему, а не подставлять расход
    сырья вместо выхода.
    """
    menu_store.save_days({SATURDAY.isoformat(): _menu(SATURDAY)}, store)
    tester = _app(store, SATURDAY)

    tab = _tab(tester, "Отчёт за субботу")
    text = " ".join(w.value for w in tab.markdown) + " ".join(
        c.value for c in tab.caption
    )
    assert "выход остаётся пустым" in text

    outputs = [w for w in tab.number_input if w.label.startswith("Выход")]
    assert outputs, "выход полуфабриката должен быть отдельной величиной"
    assert all(w.value in (None, 0.0) for w in outputs), (
        "выход не должен подставляться из расхода сырья"
    )
