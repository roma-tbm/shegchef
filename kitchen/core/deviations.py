"""Отклонения и факт выполнения работ смены (п. 42, AC12).

Отклонение — это не комментарий в свободной форме, а причина с типом:
задержка, отсутствие или замена продукта, изменение порций, проблема
оборудования, другая причина. Тип задан явно, потому что по нему шеф
решает, что делать дальше: перенести работу, заказать продукт или вызвать
мастера.

Комментарий и отклонение хранятся отдельно от статуса работы: отметка
«сделано» не заменяет причину, а статус пользователя не имеет права снять
нарушение ограничения (см. `kitchen.core.planner`).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Задержка начала или окончания работы.
REASON_DELAY = "delay"
#: Продукт отсутствует или заменён на другой.
REASON_PRODUCT = "product"
#: Порции изменились по факту.
REASON_PORTIONS = "portions"
#: Оборудование не работает или работает не так.
REASON_EQUIPMENT = "equipment"
#: Причина, не попавшая в четыре типа выше.
REASON_OTHER = "other"

DEVIATION_KINDS: tuple[str, ...] = (
    REASON_DELAY,
    REASON_PRODUCT,
    REASON_PORTIONS,
    REASON_EQUIPMENT,
    REASON_OTHER,
)

DEVIATION_LABELS: dict[str, str] = {
    REASON_DELAY: "Задержка",
    REASON_PRODUCT: "Отсутствие или замена продукта",
    REASON_PORTIONS: "Изменение порций",
    REASON_EQUIPMENT: "Проблема оборудования",
    REASON_OTHER: "Другая причина",
}


@dataclass(frozen=True, slots=True)
class Deviation:
    """Причина отклонения по конкретной работе смены.

    `item_id` — устойчивый идентификатор работы из `PlannedItem`, а не её
    позиция: замена блюда не должна приписывать отклонение другой операции.
    """

    item_id: str
    kind: str
    comment: str = ""
    minutes: int = 0
    """Отклонение времени, мин. Заполняется для задержки."""

    @property
    def label(self) -> str:
        return DEVIATION_LABELS.get(self.kind, self.kind)

    @property
    def is_complete(self) -> bool:
        """Отклонение считается заполненным, если есть тип и пояснение."""
        return self.kind in DEVIATION_KINDS and bool(self.comment.strip())

    def validate(self) -> None:
        if not self.item_id.strip():
            raise ValueError("отклонение должно быть привязано к работе")
        if self.kind not in DEVIATION_KINDS:
            raise ValueError(f"неизвестный тип отклонения: {self.kind}")
        if not self.comment.strip():
            raise ValueError("отклонение требует пояснения: что именно произошло")
        if self.kind == REASON_DELAY and self.minutes <= 0:
            raise ValueError("для задержки нужно указать минуты")
        if self.kind != REASON_DELAY and self.minutes:
            raise ValueError("минуты задержки указываются только для типа «Задержка»")


__all__ = [
    "DEVIATION_KINDS",
    "DEVIATION_LABELS",
    "REASON_DELAY",
    "REASON_EQUIPMENT",
    "REASON_OTHER",
    "REASON_PORTIONS",
    "REASON_PRODUCT",
    "Deviation",
]
