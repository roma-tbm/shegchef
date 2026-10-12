"""V2-04 и V2-06 в CLI: настройки смены и записи человека доходят до печати.

Файлы те же, что у интерфейса: иначе расчёт на бумаге и на экране разошёлся бы
при одинаковом вводе.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import build as cli
from kitchen.duties import DUTIES_BY_KEY, duties_for
from kitchen.web import deviation_store, settings_store
from kitchen.web.deviation_store import REASON_DELAY, deviation_from_values

MONDAY = date(2026, 10, 5)
#: Обязанность с уникальной формулировкой в каталоге: по ней можно найти
#: именно свою строку печати, не зацепив одноимённую работу.
STOCK_UP = "stock-up"

#: Идентификатор работы из каталога: не зависит от меню, поэтому стабилен.
STOCK_UP_ITEM = "П-stock-up"


def _run(out: Path, *argv: str) -> int:
    return cli.main(["--out", str(out), "--no-open", *argv])


def _day_inputs(**overrides) -> dict:
    base = {"durations": {}, "windows": {}, "staff": {}, "tasting": ""}
    base.update(overrides)
    return base


def _plan_text(tmp_path: Path) -> str:
    """Текст общего плана. Имя файла даёт сам CLI, как и в реальном запуске."""
    return next((tmp_path / cli.PRINT_DIR_NAME).glob("Общий план*.txt")).read_text(
        encoding="utf-8"
    )


def test_cli_uses_durations_from_the_settings_file(tmp_path, capsys) -> None:
    """Длительность из файла настроек попадает в расчёт.

    Проверяется одна и та же обязанность до и после: без настроек её время
    неизвестно и CLI об этом говорит, с настройкой — посчитано. Иначе тест
    прошёл бы и при полностью сломанном чтении файла.
    """
    from kitchen import seed

    import build

    out = tmp_path / "Кухня.xlsx"
    operation = DUTIES_BY_KEY[STOCK_UP].operation
    assert _run(out, "--day", "05.10.2026") == 0
    assert f"{operation}: длительность в источнике не задана" in capsys.readouterr().out

    settings = settings_store.save_day_inputs(
        MONDAY, _day_inputs(durations={STOCK_UP: "20"}), tmp_path / "settings.json"
    )
    capsys.readouterr()
    assert _run(out, "--day", "05.10.2026", "--settings", str(settings)) == 0
    assert f"{operation}: длительность в источнике не задана" not in (
        capsys.readouterr().out
    )

    scenario = build._scenario_with_settings(
        seed.demo_data(MONDAY), MONDAY, settings, None
    )
    item = next(i for i in scenario.plan.items if i.item_id == STOCK_UP_ITEM)
    assert item.duration_min == 20
    assert item.span != "—", "посчитанная работа должна получить время"


def test_cli_prints_a_missing_duration_honestly(tmp_path, capsys) -> None:
    """Без настроек длительность остаётся видимым пропуском, а не выдумкой."""
    out = tmp_path / "Кухня.xlsx"

    assert _run(out, "--day", "05.10.2026") == 0

    printed = capsys.readouterr().out
    assert "Не заполнено:" in printed
    assert "Печатные формы:" in printed


def test_cli_reads_deviations_and_comments(tmp_path) -> None:
    """Комментарий и отклонение человека доходят до печатных форм."""
    out = tmp_path / "Кухня.xlsx"
    notes = tmp_path / "notes.json"
    deviation_store.save_deviations(
        MONDAY,
        {
            STOCK_UP_ITEM: deviation_from_values(
                STOCK_UP_ITEM, REASON_DELAY, "опоздали", "20"
            )
        },
        notes,
    )
    deviation_store.save_day_notes(MONDAY, {STOCK_UP_ITEM: "пояснение шефа"}, notes)

    assert _run(out, "--day", "05.10.2026", "--notes", str(notes)) == 0

    text = _plan_text(tmp_path)
    # Комментарий и отклонение печатаются в одной ячейке своей работы, поэтому
    # достаточно проверить печать и отдельно — что запись легла на свою работу.
    # Печатная форма переносит длинные ячейки по словам, чтобы не выходить за
    # лист, поэтому сравнивается текст без учёта этих переносов, а не сырые
    # строки: иначе проверка зависела бы от ширины колонки, а не от факта печати.
    assert "пояснение шефа" in " ".join(text.split())
    assert "Задержка" in text

    from kitchen import seed

    import build

    scenario = build._scenario_with_settings(
        seed.demo_data(MONDAY), MONDAY, None, notes
    )
    item = next(i for i in scenario.plan.items if i.item_id == STOCK_UP_ITEM)
    assert item.comment == "пояснение шефа"
    assert item.deviation == REASON_DELAY


def test_cli_ignores_a_note_for_an_unknown_work(tmp_path) -> None:
    """Запись по несуществующей работе не ломает расчёт и не печатается."""
    out = tmp_path / "Кухня.xlsx"
    notes = tmp_path / "notes.json"
    deviation_store.save_day_notes(MONDAY, {"З-нет-такой": "призрак"}, notes)

    assert _run(out, "--day", "05.10.2026", "--notes", str(notes)) == 0
    assert "призрак" not in _plan_text(tmp_path)


def test_bad_settings_file_is_reported_not_ignored(tmp_path, capsys) -> None:
    """Ошибочный файл настроек — понятная ошибка, а не молчаливый расчёт."""
    out = tmp_path / "Кухня.xlsx"
    bad = tmp_path / "settings.json"
    bad.write_text(
        '{"days": {"2026-10-05": {"durations": {"clean-hall": "не число"}}}}',
        encoding="utf-8",
    )

    code = _run(out, "--day", "05.10.2026", "--settings", str(bad))

    assert code == 2
    assert "Настройки смены не разобраны" in capsys.readouterr().err


def test_settings_flag_is_optional(tmp_path) -> None:
    """Файл настроек можно не задавать: расчёт работает и без него."""
    out = tmp_path / "Кухня.xlsx"

    assert _run(out, "--day", "05.10.2026") == 0
    assert out.exists()
