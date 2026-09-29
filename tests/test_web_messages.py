"""Проверки текстовых заданий по ролям (kitchen/web/messages.py).

Модуль чистый: на входе посчитанная смена, на выходе обычный текст.
Telegram-API и сети здесь нет по построению.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

from kitchen.core import build_shift_sheet
from kitchen.seed import demo_data
from kitchen.web.messages import (
    all_messages_text,
    messages_by_role,
    render_role_message,
    role_warnings,
    sorted_tasks,
    tasks_by_role,
)

DAY = date(2026, 9, 28)  # понедельник


def _sheet():
    return build_shift_sheet(demo_data(DAY))


# ---------------------------------------------------------------------------
# Одно сообщение
# ---------------------------------------------------------------------------


def test_сообщение_для_одной_роли_содержит_дату_смену_и_роль():
    text = render_role_message(_sheet(), "Повар")

    assert "Роль: Повар" in text
    assert "28.09.2026" in text
    assert "Время смены:" in text
    assert "«Дневная смена»" in text


def test_сообщение_содержит_операции_блюда_продукты_длительность():
    text = render_role_message(_sheet(), "Повар")

    assert "мин" in text
    assert "Блюдо:" in text
    assert "Продукты:" in text
    assert "Отмерить хлопья и молоко, поставить на огонь" in text


def test_сообщение_содержит_примечание_когда_оно_есть():
    text = render_role_message(_sheet(), "Повар")
    assert "Правило двух ступеней дегустации" in text


# ---------------------------------------------------------------------------
# Сортировка и разделение
# ---------------------------------------------------------------------------


def test_задачи_сортированы_по_времени():
    sheet = _sheet()
    ordered = sorted_tasks(sheet.tasks)

    starts = [task.start for task in ordered]
    assert starts == sorted(starts)
    for role, items in tasks_by_role(sheet):
        assert [task.start for task in items] == sorted(
            task.start for task in items
        ), f"роль {role}: задачи не упорядочены"


def test_в_сообщении_задачи_идут_по_возрастанию_времени():
    text = render_role_message(_sheet(), "Повар")
    # Только строки «N) ЧЧ:ММ–ЧЧ:ММ» — это блоки задач; диапазоны внутри
    # предупреждений к хронологии задач не относятся.
    stamps = [
        (int(sh) * 60 + int(sm), int(eh) * 60 + int(em))
        for sh, sm, eh, em in re.findall(
            r"^\d+\) (\d{2}):(\d{2})–(\d{2}):(\d{2})", text, flags=re.MULTILINE
        )
    ]
    starts = [start for start, _ in stamps]
    assert starts == sorted(starts), "задачи в сообщении не по времени"


def test_сообщения_разделены_по_ролям():
    messages = messages_by_role(_sheet())

    roles = [message.role for message in messages]
    assert len(roles) == len(set(roles)), "одна роль встречается в двух сообщениях"
    assert set(roles) == {"Клининг", "Разнорабочий", "Повар", "Шеф-повар"}

    for message in messages:
        assert f"Роль: {message.role}" in message.text
        assert re.search(r"\d+\)", message.text), message.role


def test_задача_роли_не_попадает_в_чужое_сообщение():
    messages = {m.role: m.text for m in messages_by_role(_sheet())}

    assert "Замес фарша с луком, формовка котлет" in messages["Повар"]
    assert "Замес фарша с луком, формовка котлет" not in messages["Разнорабочий"]
    assert "Сборка тостов, раскладка по подносам" in messages["Разнорабочий"]
    assert "Сборка тостов, раскладка по подносам" not in messages["Повар"]


# ---------------------------------------------------------------------------
# Пустой список задач
# ---------------------------------------------------------------------------


def test_пустой_список_задач_даёт_понятное_сообщение():
    base = _sheet()
    empty = replace(
        base,
        tasks=(),
        tasks_by_role=(),
        controls=(),
        role_load=(),
        blocks=(),
    )

    messages = messages_by_role(empty)
    assert len(messages) == 1
    assert messages[0].role == "Смена"
    assert "Задач: нет" in messages[0].text
    assert "Задач: нет" in all_messages_text(empty)


# ---------------------------------------------------------------------------
# Предупреждения
# ---------------------------------------------------------------------------


def test_предупреждения_роли_попадают_в_её_текст():
    text = render_role_message(_sheet(), "Повар")
    assert "пик 3 одновременных" in text
    assert "Не хватает по остаткам" in text


def test_ролевое_предупреждение_не_попадает_к_другой_роли():
    messages = {m.role: m.text for m in messages_by_role(_sheet())}

    assert "пик 3 одновременных" in messages["Повар"]
    assert "пик 3 одновременных" not in messages["Разнорабочий"]
    # дефицит по остаткам — общее для всех ролей
    assert "Не хватает по остаткам" in messages["Разнорабочий"]


def test_role_warnings_фильтрует_по_роли():
    sheet = _sheet()
    for warning in role_warnings(sheet, "Повар"):
        assert "«Повар»" in warning or not warning.startswith("Роль")
    for warning in role_warnings(sheet, "Разнорабочий"):
        assert "«Разнорабочий»" in warning or not warning.startswith("Роль")


# ---------------------------------------------------------------------------
# Общий файл
# ---------------------------------------------------------------------------


def test_все_сообщения_одним_файлом():
    text = all_messages_text(_sheet())
    assert text.count("Роль:") >= 4
    assert "=" * 40 in text


def test_файл_без_сложной_markdown_разметки():
    text = all_messages_text(_sheet())
    assert "**" not in text
    assert "##" not in text
    assert "# " not in text