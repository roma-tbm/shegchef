"""Чистое масштабирование меню: одна строка меню или весь набор.

Коэффициент применяется к порциям каждой строки, все остальные поля
(дата, блюдо, приём пищи, время выдачи, примечание) остаются на месте.
Исходные объекты не изменяются — возвращаются новые MenuLine.

Правило округления
------------------
Новые порции считаются как floor(порции × коэффициент + 0,5), но не меньше 1:

* дробная часть 0,5 и более округляется ВВЕРХ (обычное математическое
  округление, без банковского);
* дробная часть меньше 0,5 округляется ВНИЗ;
* результат никогда не бывает нулевым или отрицательным — даже 0 порций
  на входе превращается в 1 порцию.

Небольшая эпсилон-добавка (+1e-9) защищает от ошибок двоичной арифметики
вида 50 × 1.3 = 64,9999... вместо 65.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from math import isfinite

from kitchen.models import MenuLine


class ScaleError(ValueError):
    """Недопустимый коэффициент масштабирования."""


def validate_factor(factor: object) -> float:
    """Проверяет коэффициент и возвращает его числом.

    Допускается только конечное число строго больше нуля; ноль, отрицательные
    значения, нечисловые строки и NaN/Inf отклоняются с понятной ошибкой.
    """
    try:
        value = float(factor)
    except (TypeError, ValueError):
        raise ScaleError(
            "Некорректный коэффициент: ожидается число, например 1.3."
        ) from None
    if not isfinite(value):
        raise ScaleError(
            "Некорректный коэффициент: количество должно быть конечным числом."
        )
    if value <= 0:
        raise ScaleError(
            "Коэффициент должен быть больше нуля: ноль и отрицательные "
            "значения недопустимы."
        )
    return value


def scaled_portion(portions: int, factor: float, *, minimum: int = 1) -> int:
    """Порции после применения коэффициента (правило округления — в школе)."""
    if minimum < 1:
        raise ScaleError("Минимум порций должен быть не меньше 1.")
    return max(minimum, int(portions * factor + 0.5 + 1e-9))


def scale_menu(
    menu: Iterable[MenuLine],
    factor: object,
    *,
    minimum: int = 1,
) -> tuple[MenuLine, ...]:
    """Возвращает новое меню с масштабированными порциями.

    Исходные строки не изменяются. Поля, кроме порций, сохраняются.
    """
    value = validate_factor(factor)
    return tuple(
        replace(line, portions=scaled_portion(line.portions, value, minimum=minimum))
        for line in menu
    )


def scaled_preview(
    menu: Iterable[MenuLine],
    factor: object,
    *,
    minimum: int = 1,
) -> tuple[tuple[MenuLine, MenuLine], ...]:
    """Пары «было → стало» для показа сравнения в интерфейсе.

    Возвращает кортеж пар (исходная строка, масштабированная строка),
    чтобы UI мог показать каждую позицию до и после применения коэффициента.
    """
    value = validate_factor(factor)
    out: list[tuple[MenuLine, MenuLine]] = []
    for line in menu:
        scaled = replace(
            line,
            portions=scaled_portion(line.portions, value, minimum=minimum),
        )
        out.append((line, scaled))
    return tuple(out)


__all__ = [
    "ScaleError",
    "scaled_portion",
    "scaled_preview",
    "scale_menu",
    "validate_factor",
]