"""Квитанції у PDF: звичайний PDF або PDF у контейнері CMS (.p7s), як у monobank.

Контейнер розбираємо asn1crypto: дістаємо PDF і дані підписувача. Сам підпис (ДСТУ 4145)
і хеш «Купина» не перевіряємо — надійної бібліотеки поки немає (ADR 0012).
"""

from __future__ import annotations

import asyncio
import io
import logging

from asn1crypto import cms

from domovyk.adapters.receipts import monobank
from domovyk.adapters.receipts.base import ParsedReceipt, ReceiptDocument, SignedBy

log = logging.getLogger(__name__)

MAX_PAGES = 2  # квитанція — одна сторінка; більше не читаємо


class PdfReceiptReader:
    def open(self, data: bytes) -> ReceiptDocument | None:
        if data[:5] == b"%PDF-":
            return ReceiptDocument(pdf=data, signed_by=None)
        if data[:1] == b"\x30":
            return _unwrap(data)
        return None

    async def parse(self, document: ReceiptDocument) -> ParsedReceipt | None:
        text = await asyncio.to_thread(_text, document.pdf)
        if not text:
            return None
        if monobank.is_monobank(text):
            return monobank.parse(text, document.signed_by)
        return None


def _text(pdf: bytes) -> str:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            return "\n".join(page.extract_text() or "" for page in doc.pages[:MAX_PAGES])
    except Exception:  # пошкоджений чи хитрий PDF — просто «не розпізнано»
        log.info("Квитанцію не вдалося прочитати як PDF", exc_info=True)
        return ""


def _unwrap(data: bytes) -> ReceiptDocument | None:
    try:
        info = cms.ContentInfo.load(data)
        if info["content_type"].native != "signed_data":
            return None
        signed = info["content"]
        content = signed["encap_content_info"]["content"].native
        if not isinstance(content, bytes) or content[:5] != b"%PDF-":
            return None
        return ReceiptDocument(pdf=content, signed_by=_signer(signed))
    except Exception:
        log.info("Контейнер квитанції не розібрано", exc_info=True)
        return None


def _signer(signed: cms.SignedData) -> SignedBy | None:
    """Сертифікат того, хто підписав (за серійним номером із SignerInfo)."""
    infos = signed["signer_infos"]
    if len(infos) == 0:
        return None
    signer = infos[0]
    sid = signer["sid"].chosen
    serial = sid["serial_number"].native if isinstance(sid, cms.IssuerAndSerialNumber) else None
    for choice in signed["certificates"] or []:
        cert = choice.chosen
        tbs = cert["tbs_certificate"]
        if serial is not None and tbs["serial_number"].native != serial:
            continue
        subject = tbs["subject"].native
        issuer = tbs["issuer"].native
        signed_at = None
        for attr in signer["signed_attrs"] or []:
            if attr["type"].native == "signing_time":
                signed_at = attr["values"][0].native
        return SignedBy(
            organization=str(subject.get("organization_name", "")),
            issuer=str(issuer.get("common_name", "")),
            signed_at=signed_at,
        )
    return None
