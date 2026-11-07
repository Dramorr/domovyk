"""Перевірка файлу підпису (FR-CTR-5, правило 4).

Зараз — розбір файлу з порталу «Підписання документів» Дії (`diia_portal.py`).
Пізніше — повна криптоперевірка (ланцюг сертифікатів, OCSP, мітка часу) або пряма
інтеграція з API Дія.Підпису (FR-CTR-8): новий адаптер за цим самим інтерфейсом.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

DocumentMatch = Literal["match", "mismatch", "unknown"]


class SignatureParseError(Exception):
    """Файл не схожий на підпис (не CMS/CAdES і не контейнер ASiC)."""


@dataclass(frozen=True)
class SignerInfo:
    """Дані з сертифіката підписувача. РНОКПП — лише для хешування, ніде не зберігається."""

    full_name: str | None
    tax_id: str | None
    is_legal_entity: bool
    issuer: str | None
    serial: str | None
    valid_from: datetime | None
    valid_to: datetime | None


@dataclass(frozen=True)
class SignatureReport:
    container: Literal["cades", "asice"]
    signer: SignerInfo | None
    signed_at: datetime | None
    document_match: DocumentMatch
    digest_algorithm: str | None
    # True — лише коли адаптер справді перевірив підпис криптографічно (ланцюг, статус).
    crypto_verified: bool = False
    signatures_count: int = 1
    notes: list[str] = field(default_factory=list)


class SignatureVerifier(Protocol):
    async def inspect(self, signature: bytes, document: bytes) -> SignatureReport:
        """Розібрати підпис і звірити його з документом, який мали підписати."""
        ...
