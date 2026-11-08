"""Квитанція monobank (АТ «Універсал Банк»): текст PDF → поля.

Формат 2026 р. (PDF від Apache FOP у контейнері CMS, підписаному КЕП банку):

    Квитанція № XXXX-XXXX-XXXX-XXXX від ДД.ММ.РРРР
    Відправник … Одержувач
    Ім'я Ірина К.
    Платіжний інструмент 4111111111111111
    Деталі транзакції
    Сума (грн) 18000.00
    Комісія (грн) 0.00
    Призначення платежу …
    Дата і час операції 05.12.2026 10:15

Дані відправника не читаємо й не зберігаємо — для звірки вони не потрібні (правило 7).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from domovyk.adapters.receipts.base import ParsedReceipt, SignedBy

BANK = "monobank"
_SIGNER = "УНІВЕРСАЛ БАНК"
_KYIV = ZoneInfo("Europe/Kyiv")

_REF = re.compile(r"Квитанція\s*№\s*([0-9A-Z]{4}(?:-[0-9A-Z]{4}){3})")
_AMOUNT = re.compile(r"Сума\s*\(грн\)\s*([\d  ]+[.,]\d{2})")
_FEE = re.compile(r"Комісія\s*\(грн\)\s*([\d  ]+[.,]\d{2})")
_WHEN = re.compile(r"Дата і час операції\s*(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})")
_NAME = re.compile(r"Ім.я\s+([^\n]+)")
_INSTRUMENT = re.compile(r"Платіжний інструмент\s+([^\n]+)")
_PURPOSE = re.compile(r"Призначення платежу\s+([^\n]+)")


def is_monobank(text: str) -> bool:
    return _SIGNER in text and _REF.search(text) is not None


def parse(text: str, signed_by: SignedBy | None) -> ParsedReceipt | None:
    """None — це не квитанція monobank або бракує обов'язкових полів."""
    if not is_monobank(text):
        return None
    ref, amount, when = _REF.search(text), _AMOUNT.search(text), _WHEN.search(text)
    if ref is None or amount is None or when is None:
        return None
    recipient = _section(text, "Одержувач", "Деталі транзакції")
    if recipient is None:
        return None
    day, month, year, hour, minute = (int(x) for x in when.groups())
    try:
        operated = datetime(year, month, day, hour, minute, tzinfo=_KYIV)
        value = _money(amount.group(1))
    except (ValueError, InvalidOperation):
        return None
    fee = _FEE.search(text)
    card, iban = _instrument(recipient)
    name = _NAME.search(recipient)
    purpose = _PURPOSE.search(text)
    return ParsedReceipt(
        bank=BANK,
        reference=ref.group(1),
        amount=value,
        fee=_money(fee.group(1)) if fee else None,
        operated_at=operated.astimezone(ZoneInfo("UTC")),
        recipient_card=card,
        recipient_iban=iban,
        recipient_name=name.group(1).strip()[:80] if name else None,
        purpose=purpose.group(1).strip()[:200] if purpose else None,
        bank_signed=signed_by is not None and _SIGNER in signed_by.organization.upper(),
    )


def _section(text: str, start: str, end: str) -> str | None:
    i = text.find(f"\n{start}")
    if i < 0:
        return None
    j = text.find(end, i)
    return text[i : j if j > 0 else None]


def _instrument(section: str) -> tuple[str | None, str | None]:
    found = _INSTRUMENT.search(section)
    if found is None:
        return None, None
    raw = found.group(1).replace(" ", "").replace(" ", "")
    iban = re.search(r"UA\d{27}", raw)
    card = re.search(r"\d{4,6}[\d*]{4,9}\d{4}", raw.replace("•", "*"))
    return (card.group(0) if card and not iban else None), (iban.group(0) if iban else None)


def _money(raw: str) -> Decimal:
    return Decimal(raw.replace(" ", "").replace(" ", "").replace(",", "."))
