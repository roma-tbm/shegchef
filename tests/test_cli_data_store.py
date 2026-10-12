"""Единый набор данных в CLI: статусы, пустое меню, явные пути (AC03, AC06, AC07).

Здесь проверяется именно реальный путь `build.main`: что CLI читает тот же
каталог данных, что и интерфейс, передаёт сохранённые статусы в расчёт, не
подменяет сохранённое пустое меню следующего дня и даёт понятные ошибки.
"""

from __future__ import annotations

from datetime import date, time
from pathlib import Path

from kitchen import seed
from kitchen.core.planner import STATUS_DONE
from kitchen.duties import DUTIES_BY_KEY
from kitchen.models import MenuLine
from kitchen.web import menu_store, settings_store, status_store
from kitchen.web.dataset import Dataset

import build as cli

MONDAY = date(2026, 10, 5)
SUNDAY = date(2026, 10, 4)

STOCK_UP = "stock-up"
STOCK_UP_ITEM = "П-stock-up"


def _line(name: str, portions: int = 50, day: date = MONDAY) -> MenuLine:
    return MenuLine(name, portions, time(12, 0), "Обед", day)


def _run(out: Path, data_dir: Path, *argv: str) -> int:
    return cli.main(
        ["--out", str(out), "--data-dir", str(data_dir), "--no-open", *argv]
    )


def test_cli_passes_saved_statuses_into_calculation(tmp_path) -> None:
    """Отметка «сделано» из файла статусов доходит до плана CLI (AC03)."""
    data_dir = tmp_path / "data"
    statuses = Dataset.in_dir(data_dir).statuses
    status_store.save_statuses(
        {MONDAY.isoformat(): ((STOCK_UP_ITEM, STATUS_DONE),)}, statuses
    )

    scenario = cli._scenario_with_settings(
        seed.demo_data(MONDAY),
        MONDAY,
        None,
        None,
        statuses_file=statuses,
    )

    item = next(i for i in scenario.plan.items if i.item_id == STOCK_UP_ITEM)
    assert item.status == STATUS_DONE


def test_cli_uses_settings_from_the_data_dir_not_the_book(tmp_path, capsys) -> None:
    """Каталог данных определяет настройки, а выходной путь книги — нет."""
    data_dir = tmp_path / "data"
    settings = Dataset.in_dir(data_dir).settings
    settings_store.save_day_inputs(MONDAY, {"durations": {STOCK_UP: "20"}}, settings)

    out = tmp_path / "book" / "Кухня.xlsx"
    assert _run(out, data_dir, "--day", "05.10.2026") == 0

    operation = DUTIES_BY_KEY[STOCK_UP].operation
    printed = capsys.readouterr().out
    assert f"{operation}: длительность в источнике не задана" not in printed
    assert not (out.parent / "settings.json").exists()


def test_cli_saved_empty_next_day_is_not_replaced_by_book(tmp_path) -> None:
    """Сохранённое пустое меню следующего дня не подменяется планом книги."""
    data_dir = tmp_path / "data"
    ds = Dataset.in_dir(data_dir)
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, data_dir, "--week", "пн") == 0, "книга с планом понедельника"

    menu_store.save_days({MONDAY.isoformat(): ()}, ds.menu)
    scenario = cli._scenario_with_settings(
        seed.demo_data(SUNDAY),
        SUNDAY,
        None,
        None,
        out=out,
        dataset=ds,
    )

    assert scenario.next_menu == (), "пустая запись должна победить план книги"


def test_cli_absent_next_day_is_absence_not_book_plan(tmp_path) -> None:
    """Без сохранённой записи следующий день — отсутствие, а не план книги (R2).

    Это согласованный контракт D3: веб тоже не подставляет план выходной книги,
    поэтому одинаковые данные дают одинаковую подготовку.
    """
    data_dir = tmp_path / "data"
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, data_dir, "--week", "пн") == 0, "в книге есть план понедельника"

    scenario = cli._scenario_with_settings(
        seed.demo_data(SUNDAY),
        SUNDAY,
        None,
        None,
        out=out,
        dataset=Dataset.in_dir(data_dir),
    )

    assert scenario.next_menu == (), "книга сама меню не выбирает"


def test_cli_corrupt_menu_store_is_clear_error(tmp_path, capsys) -> None:
    """Повреждённое хранилище — понятная ошибка без перезаписи файла."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    broken = data_dir / "menu.json"
    broken.write_text("{это не json", encoding="utf-8")
    out = tmp_path / "Кухня.xlsx"

    assert _run(out, data_dir, "--day", "05.10.2026") == 2

    assert not out.exists()
    assert "поврежд" in capsys.readouterr().err
    assert broken.read_text(encoding="utf-8") == "{это не json"


def test_cli_conflicting_menu_files_error_before_write(tmp_path, capsys) -> None:
    """Конфликт menu.json и меню.json — ошибка до создания книги."""
    data_dir = tmp_path / "data"
    ds = Dataset.in_dir(data_dir)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu)
    menu_store.save_days({MONDAY.isoformat(): (_line("Рассольник"),)}, ds.menu_alt)
    out = tmp_path / "Кухня.xlsx"

    assert _run(out, data_dir, "--day", "05.10.2026") == 2

    assert not out.exists()
    assert "конфликт" in capsys.readouterr().err


def test_cli_menu_file_resolves_the_conflict(tmp_path) -> None:
    """--menu-file выбирает один источник и снимает конфликт."""
    data_dir = tmp_path / "data"
    ds = Dataset.in_dir(data_dir)
    menu_store.save_days({MONDAY.isoformat(): (_line("Солянка мясная"),)}, ds.menu)
    menu_store.save_days({MONDAY.isoformat(): (_line("Рассольник"),)}, ds.menu_alt)
    out = tmp_path / "Кухня.xlsx"

    assert _run(out, data_dir, "--day", "05.10.2026", "--menu-file", str(ds.menu)) == 0
    assert out.exists()


def test_cli_and_web_style_calculation_agree(tmp_path) -> None:
    """Одинаковые данные из общего каталога дают одинаковый план (AC03).

    Здесь повторяется путь расчёта веб-приложения: те же файлы настроек,
    статусов, комментариев и меню следующего дня, что и у CLI. Сравниваются
    работы, исполнители, интервалы, статусы, комментарии, отклонения и причины.
    """
    from kitchen.web import deviation_store
    from kitchen.web.settings_store import load_day_inputs, scenario_settings
    from kitchen.web.shift_plan import build_scenario

    data_dir = tmp_path / "data"
    ds = Dataset.in_dir(data_dir)
    settings_store.save_day_inputs(MONDAY, {"durations": {STOCK_UP: "20"}}, ds.settings)
    status_store.save_statuses(
        {MONDAY.isoformat(): ((STOCK_UP_ITEM, STATUS_DONE),)}, ds.statuses
    )
    deviation_store.save_day_notes(MONDAY, {STOCK_UP_ITEM: "пояснение"}, ds.notes)

    data = seed.demo_data(MONDAY)
    out = tmp_path / "none.xlsx"
    next_menu = cli._next_day_menu(data, MONDAY, out, dataset=ds)

    cli_scenario = cli._scenario_with_settings(
        data,
        MONDAY,
        ds.settings,
        ds.notes,
        statuses_file=ds.statuses,
        out=out,
        dataset=ds,
    )

    raw = load_day_inputs(MONDAY, ds.settings)
    duty_settings, staff_settings = scenario_settings(raw)
    web_scenario = build_scenario(
        data,
        MONDAY,
        menu=tuple(data.menu),
        duty_settings=duty_settings,
        staff_settings=staff_settings,
        overrides=status_store.overrides_for_day(MONDAY, ds.statuses),
        next_menu=next_menu,
        notes=tuple(sorted(deviation_store.load_day_notes(MONDAY, ds.notes).items())),
        deviations=tuple(
            (key, record.kind)
            for key, record in sorted(
                deviation_store.load_deviations(MONDAY, ds.notes).items()
            )
        ),
    )

    def signature(plan):
        return [
            (
                item.item_id,
                item.status,
                item.span,
                item.assignee,
                item.comment,
                item.deviation,
                item.reason,
            )
            for item in plan.items
        ]

    assert signature(cli_scenario.plan) == signature(web_scenario.plan)


# ---------------------------------------------------------------------------
# R4: выходная книга сама меню не выбирает
# ---------------------------------------------------------------------------


def test_out_book_does_not_choose_menu_for_another_date(tmp_path, capsys) -> None:
    """Одна книга для двух дат: меню не переносится неявно со старой даты (R4)."""
    data_dir = tmp_path / "data"
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, data_dir, "--day", "05.10.2026", "--menu", "50") == 0
    capsys.readouterr()

    assert _run(out, data_dir, "--day", "06.10.2026", "--no-print") == 0

    printed = capsys.readouterr().out
    assert "план «Вторник»" in printed, printed
    assert "из файла" not in printed


def test_saved_empty_current_date_beats_nonempty_book(tmp_path, capsys) -> None:
    """Сохранённая пустая дата не заменяется строками книги (R4, AC06)."""
    data_dir = tmp_path / "data"
    ds = Dataset.in_dir(data_dir)
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, data_dir, "--day", "05.10.2026", "--menu", "50") == 0
    menu_store.save_days({MONDAY.isoformat(): ()}, ds.menu)

    assert _run(out, data_dir, "--day", "05.10.2026", "--no-print") == 1

    assert "Меню пустое" in capsys.readouterr().err


def test_explicit_json_beats_existing_book(tmp_path, capsys) -> None:
    """Явно выбранный JSON используется даже при существующей книге (R4)."""
    data_dir = tmp_path / "data"
    ds = Dataset.in_dir(data_dir)
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, data_dir, "--day", "05.10.2026", "--menu", "50") == 0
    menu_store.save_days({MONDAY.isoformat(): (_line("Рассольник"),)}, ds.menu)
    capsys.readouterr()

    assert (
        _run(out, data_dir, "--day", "05.10.2026", "--menu-file", str(ds.menu), "--no-print")
        == 0
    )
    assert "сохранённое меню за 05.10.2026" in capsys.readouterr().out


def test_explicit_from_book_reads_the_workbook(tmp_path, capsys) -> None:
    """Явный Excel-источник остаётся доступным отдельным ключом (R4)."""
    data_dir = tmp_path / "data"
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, data_dir, "--menu", "50") == 0
    capsys.readouterr()

    assert _run(out, data_dir, "--from-book", "--no-print") == 0
    assert "из файла Кухня.xlsx" in capsys.readouterr().out