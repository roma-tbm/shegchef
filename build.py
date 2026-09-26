#!/usr/bin/env python3
"""Собрать книгу «Конструктор кухни» из данных проекта.

    python build.py                    # демо-меню на 50 порций
    python build.py --out Кухня.xlsx   # другой файл
    python build.py --day 2026-09-28   # другая дата в шапке
    python build.py --menu 60          # изменить порции демо-меню
    python build.py --no-open          # не открывать результат

Если шеф правил книгу руками, скрипт сначала подхватывает лист
«Конструктор меню» из существующего файла, затем пересобирает расчётные листы.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from kitchen.core import build_shift_sheet
from kitchen.excel import build_workbook, read_menu
from kitchen.models import KitchenData, MenuLine
from kitchen.seed import demo_data

DEFAULT_OUT = Path("Кухня_конструктор.xlsx")


def _known_recipes() -> dict:
    """Блюда, которые вообще можно поставить в меню."""
    return demo_data().recipes


def _reopen(path: Path) -> None:
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.run([opener, str(path)], check=False, capture_output=True)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Собрать книгу конструктора кухни."
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="файл книги")
    parser.add_argument("--day", type=str, default="", help="дата, DD.MM.YYYY")
    parser.add_argument("--menu", type=int, default=0,
                        help="порции для всех блюд демо-меню")
    parser.add_argument("--fresh", action="store_true",
                        help="игнорировать существующий файл, взять демо-меню")
    parser.add_argument("--no-open", action="store_true",
                        help="не открывать результат")
    args = parser.parse_args(argv)

    day = (
        datetime.strptime(args.day, "%d.%m.%Y").date() if args.day else date.today()
    )
    data: KitchenData = demo_data(day)

    reused = False
    if not args.fresh and args.out.exists():
        rows = read_menu(args.out, _known_recipes())
        if rows:
            data = KitchenData(
                products=data.products,
                recipes=data.recipes,
                tasks=data.tasks,
                menu=tuple(
                    MenuLine(r.recipe, r.portions, r.serve_at, r.meal, day) for r in rows
                ),
                writeoffs=data.writeoffs,
                inventory=data.inventory,
                assumptions=data.assumptions,
                kitchen=data.kitchen,
                shift=data.shift,
                chief=data.chief,
            )
            reused = True

    if not reused and args.menu:
        data = KitchenData(
            products=data.products,
            recipes=data.recipes,
            tasks=data.tasks,
            menu=tuple(
                MenuLine(r.recipe, args.menu, l.serve_at, l.meal, day)
                for r, l in zip(data.recipes, data.menu)
            ),
            writeoffs=data.writeoffs,
            inventory=data.inventory,
            assumptions=data.assumptions,
            kitchen=data.kitchen,
            shift=data.shift,
            chief=data.chief,
        )

    if not data.menu:
        print("Меню пустое: заполните лист «Конструктор меню».", file=sys.stderr)
        return 1

    sheet = build_shift_sheet(data)
    try:
        path = build_workbook(data, sheet, args.out)
    except PermissionError:
        print(
            f"Не удалось записать {args.out}: файл занят другой программой.\n"
            "Закройте его в Excel/LibreOffice (или скопируйте книгу под другим "
            "именем через --out) и запустите ещё раз.",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"Не удалось записать {args.out}: {exc}", file=sys.stderr)
        return 1

    source = "из файла" if reused else ("демо-меню" if not args.menu else f"{args.menu} порций")
    print(f"Меню: {len(data.menu)} строк ({source}), дата {day:%d.%m.%Y}")
    print(f"Продуктов в матрице: {len(sheet.needs_flat)}")
    print(f"Задач: {len(sheet.tasks)}, смена {sheet.shift_start:%H:%M}–{sheet.shift_end:%H:%M}")
    for warning in sheet.warnings:
        print(f"  ! {warning}")
    print(f"Готово: {path.resolve()}")

    if not args.no_open:
        _reopen(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
