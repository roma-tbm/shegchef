#!/usr/bin/env python3
"""Собрать книгу «Конструктор кухни» из данных проекта.

    python build.py                    # демо-меню на 50 порций
    python build.py --out Кухня.xlsx   # другой файл
    python build.py --day 2026-09-28   # другая дата в шапке
    python build.py --menu 60          # изменить порции демо-меню
    python build.py --week пн          # взять готовый день из «Плана меню»
    python build.py --no-open          # не открывать результат

Если шеф правил книгу руками, скрипт сначала подхватывает лист
«Конструктор меню» из существующего файла, затем пересобирает расчётные листы.

--week разворачивает в меню строки недельного плана за указанный день:
    python build.py --week пн
    python build.py --week Понедельник
    python build.py --week 1
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from kitchen.core import build_shift_sheet
from kitchen.excel import PLAN_SHEET, build_workbook, picker, plan_for, read_menu, read_plan
from kitchen.menu_week import WEEK_PLAN
from kitchen.models import KitchenData, MenuLine, PlanLine
from kitchen.seed import demo_data
from kitchen.web.scaling import ScaleError, scale_menu

DEFAULT_OUT = Path("Кухня_конструктор.xlsx")


def _known_recipes() -> dict:
    """Блюда, которые вообще можно поставить в меню."""
    return demo_data().recipes


def _parse_scale(value: str) -> float:
    """Проверка --scale: коэффициент строго больше нуля, иначе понятная ошибка."""
    try:
        factor = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"«{value}» — не число. Ожидается, например: --scale 1.3"
        ) from None
    if not math.isfinite(factor) or factor <= 0:
        raise argparse.ArgumentTypeError(
            "коэффициент должен быть больше нуля (например 1.3 или 0.8)"
        )
    return factor


def _apply_scale(data: KitchenData, factor: float) -> KitchenData:
    """Масштабирует порции выбранного источника меню.

    Вызывается после чтения меню/плана и до build_shift_sheet() — то есть
    весь дальнейший расчёт идёт существующим ядром по новым порциям.
    """
    try:
        menu = scale_menu(data.menu, factor)
    except ScaleError as exc:
        raise ValueError(str(exc)) from exc
    return _rebuilt(data, menu)


def _reopen(path: Path) -> None:
    """Открыть книгу программой по умолчанию — у каждой ОС своя команда."""
    if sys.platform.startswith("win"):
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except OSError:
            pass
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.run([opener, str(path)], check=False, capture_output=True)
    except OSError:
        pass


def _rebuilt(
    data: KitchenData,
    menu: tuple[MenuLine, ...],
    plan: tuple[PlanLine, ...] | None = None,
) -> KitchenData:
    """Та же кухня, но с другим меню.

    План кладётся тот, по которому собрали смену, а не заготовка из кода:
    иначе правка порций в «Плане меню» стёрлась бы при следующей сборке.
    """
    return KitchenData(
        products=data.products,
        recipes=data.recipes,
        tasks=data.tasks,
        menu=menu,
        plan=data.plan if plan is None else plan,
        writeoffs=data.writeoffs,
        inventory=data.inventory,
        assumptions=data.assumptions,
        kitchen=data.kitchen,
        shift=data.shift,
        chief=data.chief,
    )


def _from_menu(
    data: KitchenData, args: argparse.Namespace, day: date
) -> tuple[KitchenData, str]:
    """Меню из книги, если шеф его правил, иначе из --menu или демо-набора."""
    if not args.fresh and args.out.exists():
        rows = read_menu(args.out, _known_recipes())
        if rows:
            menu = tuple(
                MenuLine(r.recipe, r.portions, r.serve_at, r.meal, day) for r in rows
            )
            return _rebuilt(data, menu), f"из файла {args.out.name}"
    if args.menu:
        menu = tuple(
            MenuLine(l.recipe, args.menu, l.serve_at, l.meal, day) for l in data.menu
        )
        return _rebuilt(data, menu), f"{args.menu} порций"
    return data, "демо-меню"


def _with_week_plan(
    data: KitchenData, weekday: str, out: Path, fresh: bool, day: date
) -> tuple[KitchenData, str]:
    """Меню за день недели.

    Приоритет у книги: если шеф правил «План меню» руками, берём его, иначе —
    заготовку из kitchen/menu_week.py. Так ключ --week работает и с книгой
    из репозитория, и с отредактированной.
    """
    plan = WEEK_PLAN
    source = "меню Пн–Пт из kitchen/menu_week.py"
    if not fresh and out.exists():
        from_book = read_plan(out, _known_recipes())
        if from_book:
            plan = from_book
            source = f"лист «{PLAN_SHEET}» книги {out.name}"

    try:
        rows = plan_for(plan, weekday)
    except ValueError as exc:
        raise ValueError(f"{exc}\nИсточник: {source}.") from exc
    if not rows:
        raise ValueError(
            f"В недельном плане нет блюд за «{weekday}».\nИсточник: {source}."
        )
    menu = tuple(MenuLine(r.recipe, r.portions, r.serve_at, r.meal, day) for r in rows)
    return _rebuilt(data, menu, plan), f"{weekday}, {source}"


def _with_search(
    data: KitchenData, args: argparse.Namespace, day: date
) -> tuple[KitchenData, str, picker.Report | None]:
    """Подбор блюд из ТТК по запросу из окна поиска.

    Книга должна существовать, чтобы в ней было что заполнять, поэтому при
    первом запуске она собирается, а потом уже ищет. Меню после подбора
    читается обратно из окон — так шеф видит ровно то, что окажется в расчёте.
    """
    if args.menu:
        print(
            "  ! --menu проигнорирован: блюда подбираются из ТТК в окна книги.",
            file=sys.stderr,
        )
    query = args.search
    if not query and not args.fresh and args.out.exists():
        query = picker.read_query(args.out)
    if not query and not args.out.exists():
        print(
            "  ! Книги ещё нет и запрос не задан. Соберите её один раз "
            "(python build.py), впишите запрос в окно поиска и повторите "
            "python build.py --search",
            file=sys.stderr,
        )
        return data, "демо-меню", None

    if args.fresh or not args.out.exists():
        # Окна позиций нужны до подбора: собираем книгу, иначе искать негде.
        build_workbook(data, build_shift_sheet(data), args.out)

    report = picker.fill(args.out, query, data.recipes)
    if not report.placed and not report.skipped and not report.note:
        report.note = "Ничего не найдено."
    menu = read_menu(args.out, data.recipes)
    if not menu:
        print(
            "  ! Подбор ничего не дал и меню осталось пустым. Проверьте запрос.",
            file=sys.stderr,
        )
        return data, "демо-меню", None
    return _rebuilt(data, menu), f"подбор «{query}»", report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Собрать книгу конструктора кухни."
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="файл книги")
    parser.add_argument("--day", type=str, default="", help="дата, DD.MM.YYYY")
    parser.add_argument("--menu", type=int, default=0,
                        help="порции для всех блюд демо-меню")
    parser.add_argument("--week", type=str, default="",
                        help="день недели из «Плана меню»: пн…пт, полное "
                             "название или 1…5")
    parser.add_argument("--fresh", action="store_true",
                        help="игнорировать существующий файл, взять демо-меню")
    parser.add_argument("--search", nargs="?", const="", default=None, metavar="ЗАПРОС",
                        help="подставить блюда из ТТК в пустые строки окон "
                             "«Конструктора меню». Без значения берётся запрос из "
                             "окна поиска самой книги")
    parser.add_argument("--scale", type=_parse_scale, default=1.0, metavar="КОЭФ",
                        help="единый коэффициент масштабирования порций "
                             "(например 1.3). Применяется к выбранному меню перед "
                             "расчётом смены; округление порций до целых, минимум 1")
    parser.add_argument("--no-open", action="store_true",
                        help="не открывать результат")
    args = parser.parse_args(argv)

    day = (
        datetime.strptime(args.day, "%d.%m.%Y").date() if args.day else date.today()
    )
    data: KitchenData = demo_data(day)

    if args.week and args.search is not None:
        print(
            "  ! --search и --week задают меню из разных источников. Уберите "
            "один из ключей.",
            file=sys.stderr,
        )
        return 2

    if args.week:
        if args.menu:
            print(
                "  ! --menu проигнорирован: порции берутся из «Плана меню». "
                "Правьте план или колонку «Порций» в самой книге.",
                file=sys.stderr,
            )
        try:
            data, source = _with_week_plan(data, args.week, args.out, args.fresh, day)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    else:
        data, source = _from_menu(data, args, day)

    if args.search is not None:
        data, source, report = _with_search(data, args, day)
        if report is None:
            return 2
    else:
        report = None

    if args.scale != 1.0:
        try:
            data = _apply_scale(data, args.scale)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2

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

    print(f"Меню: {len(data.menu)} строк ({source}), дата {day:%d.%m.%Y}")
    if args.scale != 1.0:
        print(f"Масштаб: {args.scale:g} × — порции округлены до целых, минимум 1")
    if report is not None:
        print(str(report))
        if report.query:
            picker.set_query(path, report.query)
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
