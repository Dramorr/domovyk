"""Доменні правила договорів (FR-CTR-1..7). Чиста логіка без I/O.

Договір генерується з погоджених умов оренди; кожна сторона підписує той самий PDF
на порталі Дії. Оренда стає активною, коли прийнято обидва підписи (або орендар
підтвердив «договір поза Домовиком»).
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any

from domovyk.core.errors import InvalidInput
from domovyk.domain.state_machine import TransitionTable

TEMPLATE_CODE = "lease_v1"
SIGNATURE_MAX_BYTES = 5 * 1024 * 1024  # CTR-SIGN: файл підпису до 5 МБ
EXTERNAL_PDF_MAX_BYTES = 10 * 1024 * 1024  # CTR-EXT: готовий договір до 10 МБ
LEGAL_NAME_MIN = 5  # як CHECK у contract_parties.full_name
LEGAL_NAME_MAX = 120


class ContractSource(StrEnum):
    GENERATED = "generated"
    UPLOADED_EXTERNAL = "uploaded_external"


class ContractStatus(StrEnum):
    DRAFT = "draft"
    SENT = "sent"  # PDF у сторін, чекаємо підписи (або підтвердження орендаря — CTR-EXT)
    PARTIALLY_SIGNED = "partially_signed"
    SIGNED = "signed"
    VOID = "void"  # замінений новою версією, скасований або відхилений


class SignatureCheck(StrEnum):
    NOT_CHECKED = "not_checked"  # розібрали, але автоматично підтвердити не можемо → модератор
    AUTO_OK = "auto_ok"  # структура, документ і ПІБ збіглися
    MISMATCH = "mismatch"  # ПІБ не збігся з довідкою / договором → модератор
    MANUAL_OK = "manual_ok"  # модератор перевірив підпис (наприклад, на czo.gov.ua)
    INVALID = "invalid"  # модератор відхилив — сторона завантажує підпис знову


class PartyRole(StrEnum):
    TENANT = "tenant"
    LANDLORD = "landlord"


CONTRACT = TransitionTable[ContractStatus](
    "contract",
    {
        ContractStatus.DRAFT: frozenset({ContractStatus.SENT, ContractStatus.VOID}),
        ContractStatus.SENT: frozenset(
            {ContractStatus.PARTIALLY_SIGNED, ContractStatus.SIGNED, ContractStatus.VOID}
        ),
        ContractStatus.PARTIALLY_SIGNED: frozenset(
            {ContractStatus.SENT, ContractStatus.SIGNED, ContractStatus.VOID}
        ),
        ContractStatus.SIGNED: frozenset(),
        ContractStatus.VOID: frozenset(),
    },
)

LIVE_STATUSES = frozenset(
    {ContractStatus.DRAFT, ContractStatus.SENT, ContractStatus.PARTIALLY_SIGNED}
)


def accepted_checks(require_moderator: bool) -> frozenset[SignatureCheck]:
    """Які перевірки зараховують підпис. Поки немає криптоперевірки — лише модератор."""
    if require_moderator:
        return frozenset({SignatureCheck.MANUAL_OK})
    return frozenset({SignatureCheck.AUTO_OK, SignatureCheck.MANUAL_OK})


# ----------------------------------------------------------------- ПІБ сторін

_LETTERS = "A-Za-zА-Яа-яІіЇїЄєҐґ"
_NAME_WORD = re.compile(rf"^[{_LETTERS}]+(?:[-'][{_LETTERS}]+)*$")
_APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "`": "'", "‘": "'"})


def clean_legal_name(raw: str) -> str:
    """ПІБ для договору: 2–4 слова з літер, апостроф і дефіс усередині слова."""
    name = " ".join(raw.translate(_APOSTROPHES).split())
    words = name.split(" ")
    if not 2 <= len(words) <= 4 or not LEGAL_NAME_MIN <= len(name) <= LEGAL_NAME_MAX:
        raise InvalidInput("Вкажи прізвище, ім'я та по батькові", field="full_name")
    if any(len(w.replace("-", "").replace("'", "")) < 2 for w in words):
        # Ініціали («К. Р.») у договорі не підходять: потрібне повне ім'я, як у документах.
        raise InvalidInput("Вкажи повне ПІБ, без ініціалів", field="full_name")
    if not all(_NAME_WORD.fullmatch(w) for w in words):
        raise InvalidInput("ПІБ — лише літери, апостроф і дефіс", field="full_name")
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _name_tokens(name: str) -> list[str]:
    cleaned = re.sub(r"[.,]", " ", name.translate(_APOSTROPHES)).casefold()
    return sorted(cleaned.split())


def names_match(a: str | None, b: str | None) -> bool:
    """ПІБ із сертифіката й з договору/довідки: регістр, порядок слів і апостроф не важливі.

    Якщо в одному з імен немає по батькові (2 слова проти 3), достатньо збігу прізвища й імені.
    """
    if not a or not b:
        return False
    ta, tb = _name_tokens(a), _name_tokens(b)
    if ta == tb:
        return True
    short, long_ = (ta, tb) if len(ta) < len(tb) else (tb, ta)
    return len(short) >= 2 and len(long_) - len(short) == 1 and set(short) <= set(long_)


# ----------------------------------------------------------- відбиток умов


def conditions_fingerprint(conditions: dict[str, Any]) -> str:
    """Відбиток погоджених умов: змінилися умови — договір треба сформувати заново."""
    canonical = json.dumps(conditions, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def looks_like_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"
