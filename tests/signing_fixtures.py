"""Вигадані підписи CAdES/ASiC для тестів (FR-CTR-5). Жодних справжніх сертифікатів.

Ключі й сертифікати генеруються на льоту (ECDSA P-256 і SHA-256). Справжні підписи Дії
використовують ДСТУ 4145, але структура CMS та сама — для розбору цього досить.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

FAKE_TAX_ID = "1234567890"  # вигаданий РНОКПП


def make_cert(
    full_name: str,
    tax_id: str | None = FAKE_TAX_ID,
    *,
    organization: str | None = None,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    surname, _, given = full_name.partition(" ")
    attrs = [
        x509.NameAttribute(NameOID.COMMON_NAME, full_name),
        x509.NameAttribute(NameOID.SURNAME, surname),
        x509.NameAttribute(NameOID.GIVEN_NAME, given or surname),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "UA"),
    ]
    if tax_id:
        attrs.append(x509.NameAttribute(NameOID.SERIAL_NUMBER, f"TINUA-{tax_id}"))
    if organization:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization))
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Тестовий КНЕДП")])
    now = datetime(2026, 11, 1, tzinfo=UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name(attrs))
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=730))
        .sign(key, hashes.SHA256())
    )
    return cert, key


def cades(document: bytes, full_name: str, *, detached: bool = True, **kw: object) -> bytes:
    """Підпис .p7s (DER) під документом від імені вигаданої людини."""
    cert, key = make_cert(full_name, **kw)  # type: ignore[arg-type]
    options = [pkcs7.PKCS7Options.Binary]
    if detached:
        options.append(pkcs7.PKCS7Options.DetachedSignature)
    return (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(document)
        .add_signer(cert, key, hashes.SHA256())
        .sign(serialization.Encoding.DER, options)
    )


def asice(document: bytes, full_name: str, filename: str = "contract.pdf") -> bytes:
    """Контейнер ASiC-E з документом і CAdES-підписом."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/vnd.etsi.asic-e+zip")
        archive.writestr(filename, document)
        archive.writestr("META-INF/signature001.p7s", cades(b"manifest", full_name))
    return buffer.getvalue()
