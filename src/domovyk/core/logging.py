"""Логування з маскуванням персональних даних (телефони, картки, IBAN)."""

from __future__ import annotations

import logging
import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # IBAN України: UA + 27 цифр (можливо з пробілами)
    (re.compile(r"\bUA\d{2}(?:\s?\d){25}\b", re.IGNORECASE), "UA**IBAN**"),
    # Картки: 13–19 цифр, можливо з пробілами/дефісами; лишаємо останні 4
    (re.compile(r"(?<!\d)(?:\d[ -]?){9,15}(\d{4})(?!\d)"), r"****\1"),
    # Телефони України: +380XXXXXXXXX, 380XXXXXXXXX, 0XXXXXXXXX
    (
        re.compile(r"(?<!\d)(?:\+?38)?0\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?(\d{2})(?!\d)"),
        r"+380*******\1",
    ),
]


def mask_pii(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class PiiMaskingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = mask_pii(message)
        if masked != message:
            record.msg = masked
            record.args = None
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(PiiMaskingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
