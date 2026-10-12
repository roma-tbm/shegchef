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
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from kitchen.core import build_shift_sheet
from kitchen.core.planner import ShiftPlan
from kitchen.excel import PLAN_SHEET, build_workbook, picker, plan_for, read_menu, read_plan
from kitchen.menu_week import WEEKDAYS, WEEK_PLAN, normalize_weekday
from kitchen.models import KitchenData, MenuLine, PlanLine
from kitchen.seed import demo_data
from kitchen.web import deviation_store, menu_store, report_store, settings_store
from kitchen.web import status_store
from kitchen.web.dataset import Dataset
from kitchen.web.store_errors import StoreError
from kitchen.web.report_store import SaturdayReport
from kitchen.web.scaling import ScaleError, scale_menu
from kitchen.web.shift_plan import (
    Printable,
    ShiftScenario,
    build_scenario,
    gaps_text,
    general_plan_form,
    prep_target_day,
    route_form,
    saturday_report_form,
)

DEFAULT_OUT = Path("Кухня_конструктор.xlsx")

#: Имя папки с печатными формами. Класть её рядом с книгой — чтобы формы и
#: расчёт лежали рядом, а не разлетались по рабочему каталогу.
PRINT_DIR_NAME = "Печатные формы"


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
    """Меню дня по общей политике D3; книга — только при явном выборе.

    Приоритет: явные ключи (`--menu`, `--fresh`), затем явный Excel-источник
    (`--from-book`) или явный JSON (`--menu-file`), затем сохранённая запись
    даты в общем каталоге данных и, наконец, автоматическая заготовка.

    Наличие выходной книги само по себе меню не выбирает: иначе при смене
    даты старое меню книги переносилось бы на новый день и перебивало явно
    выбранный JSON (R4).
    """
    if args.menu:
        menu = tuple(
            MenuLine(l.recipe, args.menu, l.serve_at, l.meal, day) for l in data.menu
        )
        return _rebuilt(data, menu), f"{args.menu} порций"
    if args.fresh:
        return data, "демо-меню"
    if args.from_book and args.out.exists():
        rows = read_menu(args.out, _known_recipes())
        if rows:
            menu = tuple(
                MenuLine(r.recipe, r.portions, r.serve_at, r.meal, day) for r in rows
            )
            return _rebuilt(data, menu), f"из файла {args.out.name}"

    days = _dataset_of(args).load_menu_days(only=args.menu_file)
    key = day.isoformat()
    if key in days:
        return _rebuilt(data, days[key]), f"сохранённое меню за {day:%d.%m.%Y}"
    if day.weekday() < len(WEEKDAYS):
        wd = WEEKDAYS[day.weekday()]
        return _rebuilt(data, menu_store.plan_to_menu(WEEK_PLAN, wd, day)), f"план «{wd}»"
    return _rebuilt(data, ()), "пустое меню: выходной"


def _dataset_of(args: argparse.Namespace) -> Dataset:
    """Набор данных из аргументов CLI: явный каталог или общий по умолчанию."""
    dataset = getattr(args, "dataset", None)
    return dataset if dataset is not None else Dataset.default()


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


def _resolve_settings(args) -> Path | None:
    """Путь файла настроек: заданный пользователем или в наборе данных.

    Настройки добровольны: их нет — расчёт идёт с видимыми пропусками по
    длительностям, а не с выдуманными нормами. По умолчанию файл лежит в общем
    каталоге данных (``~/.shegchef``), а не рядом с книгой: смена выходного
    пути книги не должна менять набор настроек.
    """
    if args.settings:
        return args.settings
    candidate = _dataset_of(args).settings
    return candidate if candidate.exists() else None


def _resolve_notes(args) -> Path | None:
    """Путь файла комментариев и отклонений, если он уже есть."""
    if args.notes:
        return args.notes
    candidate = _dataset_of(args).notes
    return candidate if candidate.exists() else None


def _resolve_statuses(args) -> Path:
    """Путь файла статусов: явный или в наборе данных."""
    if args.statuses:
        return args.statuses
    return _dataset_of(args).statuses


def _resolve_reports(args) -> Path:
    """Путь файла субботних отчётов: явный или в наборе данных."""
    if args.reports:
        return args.reports
    return _dataset_of(args).reports


def _stored_menu(day: date, path: Path) -> tuple[MenuLine, ...]:
    """Меню даты из конкретного файла меню.

    Используется точками входа, которым файл задан явно (``--menu-file`` и
    проверки сквозного сценария): тогда конфликт с другим хранилищем не
    проверяется, потому что источник назвал сам человек.
    """
    if not Path(path).exists():
        return ()
    return tuple(menu_store.load_days(path).get(day.isoformat(), ()))


def _next_day_menu(
    data: KitchenData,
    day: date,
    out: Path,
    *,
    dataset: Dataset | None = None,
    menu_file: Path | None = None,
) -> tuple[MenuLine, ...]:
    """Меню дня, к которому готовит смена `day`.

    Источник один и тот же у CLI и веба: сохранённая запись целевой даты в
    общем каталоге данных. Если записи нет, меню считается отсутствующим —
    та же видимая причина неготовности, что и на экране. Заготовка выходной
    книги сюда намеренно не подставляется (R2): иначе воскресная подготовка
    разошлась бы между CLI и вебом. Сохранённая пустая запись остаётся пустой
    и не подменяется данными (AC06).
    """
    target = prep_target_day(day)
    ds = dataset or Dataset.default()
    days = ds.load_menu_days(only=menu_file) if menu_file else ds.load_menu_days()
    key = target.isoformat()
    if key in days:
        return days[key]
    return tuple(line for line in data.menu if line.day == target and line.portions > 0)


def _scenario_with_settings(
    data: KitchenData,
    day: date,
    settings_file: Path | None,
    notes_file: Path | None,
    *,
    statuses_file: Path | None = None,
    next_menu: tuple[MenuLine, ...] | None = None,
    out: Path | None = None,
    dataset: Dataset | None = None,
    menu_file: Path | None = None,
) -> ShiftScenario:
    """Сценарий смены с настройками, статусами и записями человека.

    CLI и интерфейс читают одни и те же файлы (общий каталог данных), поэтому
    расчёт на бумаге и на экране не может разойтись из-за разных источников
    настроек. Статусы передаются в расчёт наравне с интерфейсом: отметка
    «сделано» должна переживать экспорт из CLI (AC03).
    """
    notes, deviations = _stored_records(day, notes_file)
    if next_menu is None:
        next_menu = _next_day_menu(
            data, day, out or Path(DEFAULT_OUT), dataset=dataset, menu_file=menu_file
        )
    overrides = (
        tuple(status_store.overrides_for_day(day, statuses_file))
        if statuses_file is not None
        else ()
    )
    records = {
        "next_menu": next_menu,
        "overrides": overrides,
        "notes": notes,
        "deviations": deviations,
    }
    if not settings_file:
        # Настроек нет: расчёт идёт с обычными настройками, а не с пустым
        # составом — иначе CLI тихо печатал бы пустые маршруты. Записи
        # человека при этом сохраняются: они не часть настроек.
        return build_scenario(data, day, **records)
    raw = settings_store.load_day_inputs(day, settings_file)
    duty_settings, staff_settings = settings_store.scenario_settings(raw)
    return build_scenario(
        data,
        day,
        duty_settings=duty_settings,
        staff_settings=staff_settings,
        **records,
    )


def _stored_records(
    day: date, notes_file: Path | None
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """Комментарии и типы отклонений, введённые человеком, по устойчивым ID."""
    if notes_file is None:
        return (), ()
    notes = tuple(deviation_store.load_day_notes(day, notes_file).items())
    deviations = tuple(
        (item_id, record.kind)
        for item_id, record in deviation_store.load_deviations(day, notes_file).items()
    )
    return notes, deviations


def _scenario_for(day: date, store: Path, settings_file: Path | None = None) -> ShiftScenario:
    """Сценарий смены по сохранённому меню пользователя (AC10, AC11).

    Отдельная точка входа для проверок сквозного сценария: меню и хранилище
    задаются явно, поэтому «воскресенье видит понедельник» можно проверить
    без запуска книги.
    """
    data = replace(demo_data(day), menu=tuple(menu_store.load_days(store).get(day.isoformat(), ())))
    return build_scenario(
        data, day, menu=tuple(data.menu), next_menu=_stored_menu(prep_target_day(day), store)
    )


def _saved_report_for(day: date, folder: Path) -> "SaturdayReport | None":
    """Сохранённый субботний отчёт пользователя, если он уже заполнен (AC09).

    `folder` — каталог рядом с меню/книгой, а не путь к файлу меню:
    `default_report_path` заменяет последний компонент пути, поэтому для
    каталога он указал бы на файл рядом с каталогом. Здесь файл отчётов
    берётся внутри каталога — там же, где его кладёт интерфейс.
    """
    base = Path(folder)
    target = base / "reports.json" if base.is_dir() else base
    return report_store.load_report(day, target)


def _saved_facts(report: "SaturdayReport | None") -> dict:
    """Аргументы печатной формы из сохранённого отчёта.

    Отсутствие отчёта — это пустые факты, а не ошибка: форму субботнего отчёта
    можно напечатать и до заполнения, просто она будет пустой.
    """
    if report is None:
        return {}
    return {
        "actuals": dict(report.consumption),
        "actual_units": dict(report.consumption_units),
        "outputs": dict(report.outputs),
        "markings": dict(report.markings),
    }


def _printout_files(
    scenario: ShiftScenario,
    out_dir: Path,
    *,
    report: "SaturdayReport | None" = None,
) -> list[Path]:
    """Пишет печатные формы по одному файлу на сотрудника и на отчёт.

    Субботний отчёт печатается по сохранённому факту, а не по пустому
    шаблону: иначе после перезапуска отчёт, который человек заполнил,
    выглядел бы потерянным (AC09, AC14).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    forms: list[tuple[Printable, str]] = [(general_plan_form(scenario), "Общий план")]
    for employee in scenario.plan.staff:
        forms.append((route_form(scenario, employee), _file_name(employee.name)))
    if scenario.day.weekday() == 5:
        forms.append(
            (
                saturday_report_form(scenario, **_saved_facts(report)),
                "Субботний отчёт",
            )
        )

    written: list[Path] = []
    for form, name in forms:
        path = out_dir / f"{name} — {scenario.day:%Y-%m-%d}.txt"
        path.write_text(form.to_text(), encoding="utf-8")
        written.append(path)
    return written


def _file_name(name: str) -> str:
    """Имя сотрудника в имени файла: пробелы заменены, спецсимволы убраны."""
    safe = "".join(ch if ch.isalnum() or ch in " -" else "_" for ch in name)
    return "-".join(safe.split()) or "Сотрудник"


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
                        help="игнорировать сохранённую запись: взять демо-меню, "
                             "а с --week — план из кода")
    parser.add_argument("--from-book", action="store_true",
                        help="взять меню из книги --out как явный Excel-источник. "
                             "Без этого ключа книга сама меню не выбирает")
    parser.add_argument("--search", nargs="?", const="", default=None, metavar="ЗАПРОС",
                        help="подставить блюда из ТТК в пустые строки окон "
                             "«Конструктора меню». Без значения берётся запрос из "
                             "окна поиска самой книги")
    parser.add_argument("--scale", type=_parse_scale, default=1.0, metavar="КОЭФ",
                        help="единый коэффициент масштабирования порций "
                             "(например 1.3). Применяется к выбранному меню перед "
                             "расчётом смены; округление порций до целых, минимум 1")
    parser.add_argument("--settings", type=Path, default=None,
                        help="файл настроек смены (по умолчанию settings.json "
                             "в каталоге данных ~/.shegchef). Там длительности, "
                             "окна, состав и исполнитель бракеража — те же данные, "
                             "что вводятся во вкладке «Настройки смены»")
    parser.add_argument("--notes", type=Path, default=None,
                        help="файл комментариев и отклонений (по умолчанию "
                             "notes.json в каталоге данных)")
    parser.add_argument("--statuses", type=Path, default=None,
                        help="файл статусов работ (по умолчанию statuses.json "
                             "в каталоге данных). CLI передаёт их в расчёт так же, "
                             "как интерфейс, — отметка «сделано» не теряется")
    parser.add_argument("--reports", type=Path, default=None,
                        help="файл субботних отчётов (по умолчанию reports.json "
                             "в каталоге данных)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="общий каталог данных: menu.json, settings.json, "
                             "notes.json, statuses.json, reports.json. По умолчанию "
                             "~/.shegchef или переменная SHEGCHEF_DATA_DIR")
    parser.add_argument("--menu-file", type=Path, default=None, metavar="МЕНЮ.JSON",
                        help="явный файл меню-хранилища; вместо menu.json и "
                             "меню.json берётся только он (разрешает конфликт дат)")
    parser.add_argument("--print-dir", type=Path, default=None,
                        help="папка для печатных форм (по умолчанию «"
                             + PRINT_DIR_NAME + "» рядом с книгой)")
    parser.add_argument("--no-print", action="store_true",
                        help="не выгружать печатные формы")
    parser.add_argument("--no-open", action="store_true",
                        help="не открывать результат")
    args = parser.parse_args(argv)
    args.dataset = (
        Dataset.in_dir(args.data_dir) if args.data_dir else Dataset.default()
    )

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
        try:
            data, source = _from_menu(data, args, day)
        except StoreError as exc:
            print(str(exc), file=sys.stderr)
            return 2

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

    settings_file = _resolve_settings(args)
    notes_file = _resolve_notes(args)
    statuses_file = _resolve_statuses(args)
    try:
        scenario = _scenario_with_settings(
            data,
            day,
            settings_file,
            notes_file,
            statuses_file=statuses_file,
            out=args.out,
            dataset=args.dataset,
            menu_file=args.menu_file,
        )
    except StoreError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Настройки смены не разобраны: {exc}", file=sys.stderr)
        return 2

    # Книга собирается по тому же авторитетному плану, что и маршруты: иначе
    # Excel показывал бы старое расписание, расходясь с бумажным маршрутом.
    sheet = build_shift_sheet(data, plan=scenario.plan)
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

    plan = scenario.plan
    counts = plan.status_counts()
    print(
        "План смены: "
        + ", ".join(f"{name} — {counts.get(name, 0)}" for name in sorted(counts))
    )
    if plan.gaps:
        print("Не заполнено:")
        for line in gaps_text(plan.gaps).splitlines():
            print(f"  ! {line.lstrip('• ')}")

    if not args.no_print:
        print_dir = args.print_dir or args.out.parent / PRINT_DIR_NAME
        try:
            saved_report = report_store.load_report(day, _resolve_reports(args))
        except StoreError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        try:
            forms = _printout_files(scenario, print_dir, report=saved_report)
        except OSError as exc:
            print(f"Печатные формы не записаны: {exc}", file=sys.stderr)
        else:
            print(f"Печатные формы: {len(forms)} шт. в {print_dir}")

    print(f"Готово: {path.resolve()}")

    if not args.no_open:
        _reopen(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
