"""Тесты печатных форм в CLI.

Формы должны появляться рядом с книгой, а не в случайном каталоге, и не
разлетаться по рабочей папке при запуске из репозитория.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import build as cli


def _run(out: Path, *argv: str) -> int:
    return cli.main(["--out", str(out), "--no-open", *argv])


def test_printouts_go_next_to_the_workbook(tmp_path, capsys) -> None:
    """Формы пишутся в папку рядом с книгой, по файлу на сотрудника."""
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, "--day", "05.10.2026") == 0

    forms = out.parent / cli.PRINT_DIR_NAME
    written = sorted(p.name for p in forms.glob("*.txt"))
    assert written
    assert any(name.startswith("Общий план") for name in written)
    assert any(name.startswith("Шеф-повар") for name in written)
    assert "Печатные формы:" in capsys.readouterr().out


def test_no_print_leaves_no_folder(tmp_path) -> None:
    """--no-print не создаёт папку вовсе."""
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, "--day", "05.10.2026", "--no-print") == 0
    assert not (out.parent / cli.PRINT_DIR_NAME).exists()


def test_print_dir_can_be_chosen_explicitly(tmp_path) -> None:
    """Папку выгрузки можно задать самому."""
    out = tmp_path / "Кухня.xlsx"
    target = tmp_path / "для-печати"
    assert _run(out, "--day", "05.10.2026", "--print-dir", str(target)) == 0
    assert sorted(p.name for p in target.glob("*.txt"))
    assert not (out.parent / cli.PRINT_DIR_NAME).exists()


def test_saturday_gets_a_report(tmp_path) -> None:
    """За субботу дополнительно строится субботний отчёт."""
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, "--day", "10.10.2026", "--menu", "50") == 0

    written = {p.name for p in (out.parent / cli.PRINT_DIR_NAME).glob("*.txt")}
    assert any(name.startswith("Субботний отчёт") for name in written)


def test_weekday_has_no_saturday_report(tmp_path) -> None:
    """В будний день субботний отчёт не появляется."""
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, "--day", "05.10.2026") == 0

    written = {p.name for p in (out.parent / cli.PRINT_DIR_NAME).glob("*.txt")}
    assert not any(name.startswith("Субботний отчёт") for name in written)


def test_cli_reports_what_is_not_filled(tmp_path, capsys) -> None:
    """Незаполненные поля видны в выводе командной строки."""
    out = tmp_path / "Кухня.xlsx"
    assert _run(out, "--day", "05.10.2026", "--no-print") == 0

    text = capsys.readouterr().out
    assert "План смены:" in text
    assert "Не заполнено:" in text
    assert "длительность в источнике не задана" in text


def test_empty_menu_fails_clearly_and_writes_nothing(tmp_path, capsys, monkeypatch) -> None:
    """Пустое меню — понятная ошибка, а не пустая книга и не мусор на диске."""
    from kitchen.seed import demo_data

    out = tmp_path / "Кухня.xlsx"
    monkeypatch.setattr(
        cli, "_from_menu", lambda data, args, day: (cli._rebuilt(data, ()), "пусто")
    )

    assert cli.main(["--out", str(out), "--no-open", "--fresh"]) == 1
    assert "Меню пустое" in capsys.readouterr().err
    assert not out.exists()
    assert not (tmp_path / cli.PRINT_DIR_NAME).exists()