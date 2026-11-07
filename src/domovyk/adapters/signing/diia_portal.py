"""Розбір файлу підпису з порталу «Підписання документів» Дії (MVP, FR-CTR-5).

Що робимо:
- розпізнаємо окремий підпис CAdES (`.p7s`: DER, base64 або PEM) і контейнер ASiC-E (`.asice`);
- дістаємо сертифікат підписувача: ПІБ, РНОКПП (з `serialNumber` «TINUA-…» або з
  атрибута ДРФО в subjectDirectoryAttributes), видавця, строк дії;
- звіряємо, що підписано саме наш PDF: вкладений документ — побайтово; окремий підпис —
  через messageDigest, якщо алгоритм хешу нам відомий (SHA-2/3). Національні хеші
  (ГОСТ 34.311, ДСТУ 7564 «Купина») поки не рахуємо — тоді результат `unknown`, і
  рішення приймає модератор.

Чого НЕ робимо (ADR 0010): криптографічної перевірки самого підпису й ланцюга сертифікатів
(ДСТУ 4145, ЦЗО, OCSP). Тому `crypto_verified` завжди False.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from asn1crypto import cms, core, x509

from domovyk.adapters.signing.base import (
    DocumentMatch,
    SignatureParseError,
    SignatureReport,
    SignerInfo,
)

_DRFO_OID = "1.2.804.2.1.1.1.11.1.4.1.1"  # код ДРФО (РНОКПП) в атрибутах суб'єкта
_SDA_OID = "2.5.29.9"  # subjectDirectoryAttributes
_TAX_RE = re.compile(r"(?<!\d)(\d{10})(?!\d)")
_HASHLIB: dict[str, str] = {
    "sha1": "sha1",
    "sha224": "sha224",
    "sha256": "sha256",
    "sha384": "sha384",
    "sha512": "sha512",
    "sha3_256": "sha3_256",
    "sha3_384": "sha3_384",
    "sha3_512": "sha3_512",
}
_UA_HASHES = {
    "1.2.804.2.1.1.1.1.2.1": "gost34311",
    "1.2.804.2.1.1.1.1.2.2.1": "dstu7564-256",
    "1.2.804.2.1.1.1.1.2.2.2": "dstu7564-384",
    "1.2.804.2.1.1.1.1.2.2.3": "dstu7564-512",
}
_XADES_CERT = re.compile(rb"<(?:[\w-]+:)?X509Certificate>\s*([A-Za-z0-9+/=\s]+?)\s*</", re.S)
_XADES_TIME = re.compile(rb"<(?:[\w-]+:)?SigningTime>\s*([^<]+?)\s*</")


class DiiaPortalVerifier:
    async def inspect(self, signature: bytes, document: bytes) -> SignatureReport:
        if signature[:4] == b"PK\x03\x04":
            return _inspect_asice(signature, document)
        return _inspect_cades(_load_cms(signature), document, container="cades")


# ------------------------------------------------------------------ CAdES


def _candidates(data: bytes) -> Iterator[bytes]:
    yield data
    text = data.strip()
    if text.startswith(b"-----BEGIN"):
        text = b"".join(
            line for line in text.splitlines() if line and not line.startswith(b"-----")
        )
    try:
        yield base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        return


def _load_cms(data: bytes) -> cms.SignedData:
    for candidate in _candidates(data):
        try:
            info = cms.ContentInfo.load(candidate)
            if info["content_type"].native != "signed_data":
                continue
            signed: cms.SignedData = info["content"]
            # Лише те, що потрібно: повне `.native` падає на розширеннях CAdES-X Long
            # (атрибути з національними алгоритмами), хоча сам підпис коректний.
            if len(signed["signer_infos"]) == 0:
                continue
            signed["signer_infos"][0]["sid"].chosen  # noqa: B018 — перевірка структури
            return signed
        except (ValueError, TypeError, KeyError, IndexError):
            continue
    raise SignatureParseError("Файл не схожий на підпис CAdES (.p7s)")


def _inspect_cades(signed: cms.SignedData, document: bytes, *, container: Any) -> SignatureReport:
    infos = signed["signer_infos"]
    if len(infos) == 0:
        raise SignatureParseError("У файлі немає жодного підпису")
    info: cms.SignerInfo = infos[0]
    cert = _signer_cert(signed, info)
    attrs = _signed_attrs(info)
    algorithm = info["digest_algorithm"]["algorithm"]
    algo_name = algorithm.native if algorithm.native in _HASHLIB else algorithm.dotted
    notes: list[str] = []
    content = signed["encap_content_info"]["content"].native
    if content is not None:
        match: DocumentMatch = "match" if content == document else "mismatch"
    else:
        match = _digest_match(algo_name, attrs.get("message_digest"), document, notes)
    return SignatureReport(
        container=container,
        signer=_signer(cert) if cert is not None else None,
        signed_at=attrs.get("signing_time"),
        document_match=match,
        digest_algorithm=_UA_HASHES.get(algo_name, algo_name),
        signatures_count=len(infos),
        notes=notes,
    )


def _digest_match(
    algo: str, expected: bytes | None, document: bytes, notes: list[str]
) -> DocumentMatch:
    if expected is None:
        notes.append("no_message_digest")
        return "unknown"
    name = _HASHLIB.get(algo)
    if name is None:
        notes.append("digest_unsupported")
        return "unknown"
    return "match" if hashlib.new(name, document).digest() == expected else "mismatch"


def _signed_attrs(info: cms.SignerInfo) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        attrs = list(info["signed_attrs"])
    except (ValueError, TypeError):
        return out
    for attr in attrs:
        kind = attr["type"].native
        if kind not in ("message_digest", "signing_time"):
            continue  # інші атрибути (signing_certificate_v2 тощо) не розбираємо
        try:
            values = attr["values"]
            value = values[0].native if len(values) else None
        except (ValueError, TypeError, IndexError):
            continue
        if kind == "message_digest" and isinstance(value, bytes):
            out["message_digest"] = value
        elif kind == "signing_time" and isinstance(value, datetime):
            out["signing_time"] = value
    return out


def _signer_cert(signed: cms.SignedData, info: cms.SignerInfo) -> x509.Certificate | None:
    try:
        certs = [c.chosen for c in signed["certificates"] if c.name == "certificate"]
    except (ValueError, TypeError):
        certs = []
    if not certs:
        return None
    sid = info["sid"]
    for cert in certs:
        if sid.name == "issuer_and_serial_number":
            ias = sid.chosen
            if ias["serial_number"].native == cert.serial_number and ias["issuer"] == cert.issuer:
                return cert
        elif sid.name == "subject_key_identifier" and cert.key_identifier == sid.chosen.native:
            return cert
    return certs[0]


# ------------------------------------------------------------- сертифікат


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip() if value else None


def _signer(cert: x509.Certificate) -> SignerInfo:
    subject = cert.subject.native
    cn = _first(subject.get("common_name"))
    surname = _first(subject.get("surname"))
    given = _first(subject.get("given_name"))
    if cn and len(cn.split()) >= 2:
        full_name: str | None = cn
    else:
        full_name = " ".join(p for p in (surname, given) if p) or cn
    tax_id = _tax_from_serial(_first(subject.get("serial_number"))) or _tax_from_sda(cert)
    org_id = _first(subject.get("organization_identifier")) or ""
    is_legal = org_id.upper().startswith("NTRUA") or (
        bool(_first(subject.get("organization_name"))) and tax_id is None
    )
    validity = cert["tbs_certificate"]["validity"]
    return SignerInfo(
        full_name=full_name,
        tax_id=tax_id,
        is_legal_entity=is_legal,
        issuer=_first(cert.issuer.native.get("common_name"))
        or _first(cert.issuer.native.get("organization_name")),
        serial=format(cert.serial_number, "x"),
        valid_from=validity["not_before"].native,
        valid_to=validity["not_after"].native,
    )


def _tax_from_serial(serial: str | None) -> str | None:
    if not serial:
        return None
    upper = serial.upper()
    if upper.startswith("TINUA-") or serial.isdigit():
        found = _TAX_RE.search(serial)
        return found.group(1) if found else None
    return None


def _tax_from_sda(cert: x509.Certificate) -> str | None:
    """РНОКПП з subjectDirectoryAttributes (атрибут ДРФО) — у старіших сертифікатах."""
    for ext in cert["tbs_certificate"]["extensions"]:
        if ext["extn_id"].dotted != _SDA_OID:
            continue
        der = ext["extn_value"].contents
        # OCTET STRING → (може бути вкладений) SEQUENCE OF Attribute.
        for attr in _walk_sequence(_unwrap_octet(der)):
            children = list(_walk_sequence(attr))
            if len(children) < 2:
                continue
            try:
                oid = core.ObjectIdentifier.load(children[0]).dotted
            except ValueError:
                continue
            if oid != _DRFO_OID:
                continue
            for value in _walk_sequence(children[1]):
                text = _string_value(value)
                found = _TAX_RE.search(text or "")
                if found:
                    return found.group(1)
    return None


def _tlv(data: bytes, pos: int) -> tuple[int, int, int]:
    """(tag, start of content, end of element) для DER-елемента на позиції pos."""
    if pos + 2 > len(data):
        raise ValueError("Обрізаний DER")
    tag = data[pos]
    length = data[pos + 1]
    start = pos + 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or start + n > len(data):
            raise ValueError("Некоректна довжина DER")
        length = int.from_bytes(data[start : start + n], "big")
        start += n
    end = start + length
    if end > len(data):
        raise ValueError("Обрізаний DER")
    return tag, start, end


def _walk_sequence(element: bytes) -> Iterator[bytes]:
    """Дочірні елементи SEQUENCE / SET (повні DER-елементи)."""
    try:
        tag, start, end = _tlv(element, 0)
    except ValueError:
        return
    if tag not in (0x30, 0x31):
        return
    pos = start
    while pos < end:
        try:
            _, _, child_end = _tlv(element, pos)
        except ValueError:
            return
        yield element[pos:child_end]
        pos = child_end


def _unwrap_octet(der: bytes) -> bytes:
    try:
        tag, start, end = _tlv(der, 0)
    except ValueError:
        return der
    return der[start:end] if tag == 0x04 else der


def _string_value(element: bytes) -> str | None:
    try:
        tag, start, end = _tlv(element, 0)
    except ValueError:
        return None
    if tag in (0x0C, 0x13, 0x16, 0x12):  # UTF8, Printable, IA5, Numeric
        return element[start:end].decode("utf-8", errors="ignore")
    return None


# ------------------------------------------------------------------ ASiC-E


def _inspect_asice(data: bytes, document: bytes) -> SignatureReport:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise SignatureParseError("Пошкоджений контейнер .asice") from exc
    if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
        raise SignatureParseError("Контейнер завеликий")
    data_files = [n for n in names if not n.startswith("META-INF/") and n != "mimetype"]
    cades = [n for n in names if n.startswith("META-INF/") and n.lower().endswith((".p7s", ".p7m"))]
    xades = [
        n
        for n in names
        if n.startswith("META-INF/") and "signature" in n.lower() and n.lower().endswith(".xml")
    ]
    contents = [archive.read(n) for n in data_files]
    if not contents:
        match: DocumentMatch = "unknown"
    else:
        match = "match" if any(c == document for c in contents) else "mismatch"
    if cades:
        # CAdES в ASiC-E підписує маніфест, а не сам файл: документ звіряємо з вкладеним
        # файлом контейнера (вище), а з підпису беремо лише підписувача й час.
        report = _inspect_cades(_load_cms(archive.read(cades[0])), document, container="asice")
        return SignatureReport(
            container="asice",
            signer=report.signer,
            signed_at=report.signed_at,
            document_match=match,
            digest_algorithm=report.digest_algorithm,
            signatures_count=len(cades),
            notes=["asice_cades"],
        )
    if xades:
        xml = archive.read(xades[0])
        cert_match = _XADES_CERT.search(xml)
        if cert_match is None:
            raise SignatureParseError("У контейнері немає сертифіката підписувача")
        try:
            cert = x509.Certificate.load(base64.b64decode(b"".join(cert_match.group(1).split())))
            signer = _signer(cert)
        except (ValueError, binascii.Error) as exc:
            raise SignatureParseError("Не вдалося прочитати сертифікат") from exc
        time_match = _XADES_TIME.search(xml)
        signed_at = None
        if time_match:
            try:
                signed_at = datetime.fromisoformat(
                    time_match.group(1).decode().replace("Z", "+00:00")
                )
            except ValueError:
                signed_at = None
        return SignatureReport(
            container="asice",
            signer=signer,
            signed_at=signed_at,
            document_match=match,
            digest_algorithm=None,
            signatures_count=len(xades),
            notes=["asice_xades"],
        )
    raise SignatureParseError("У контейнері немає підпису")
