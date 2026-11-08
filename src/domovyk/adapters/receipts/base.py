"""Розпізнавання банківських квитанцій (FR-PAY-3, ADR 0012). Правило 4: за Protocol.

Адаптер лише читає файл: що за документ, хто його підписав і які в ньому поля.
Чи збігається це з орендою, вирішує модуль payments (через domain/payments.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class SignedBy:
    """Хто підписав контейнер CMS. Криптографічно підпис не перевіряється (ADR 0012)."""

    organization: str
    issuer: str
    signed_at: datetime | None


@dataclass(frozen=True)
class ReceiptDocument:
    """PDF квитанції — сам по собі або витягнутий із підписаного контейнера (.p7s)."""

    pdf: bytes
    signed_by: SignedBy | None


@dataclass(frozen=True)
class ParsedReceipt:
    bank: str  # "monobank"
    reference: str  # номер квитанції — один раз на всю систему
    amount: Decimal
    fee: Decimal | None
    operated_at: datetime  # UTC
    recipient_card: str | None  # цифри; замасковані позиції — "*"
    recipient_iban: str | None
    recipient_name: str | None  # як у квитанції, напр. «Ірина К.»
    purpose: str | None
    bank_signed: bool  # контейнер підписав саме цей банк


class ReceiptReader(Protocol):
    def open(self, data: bytes) -> ReceiptDocument | None:
        """PDF або підписаний PDF; інакше None."""
        ...

    async def parse(self, document: ReceiptDocument) -> ParsedReceipt | None:
        """Поля квитанції відомого банку; None — банк невідомий чи формат змінився."""
        ...
