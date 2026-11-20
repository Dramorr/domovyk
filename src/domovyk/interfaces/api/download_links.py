"""Короткоживучі посилання на файли для Telegram.WebApp.downloadFile.

Telegram завантажує файл сам, без нашого заголовка Authorization, тож доступ перевіряємо
під час видачі посилання (initData), а в посиланні лише підписуємо: що за файл і до коли.
Ні Telegram ID, ні інших персональних даних у посиланні немає.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Kind = Literal["pdf", "package"]
_KINDS: tuple[Kind, ...] = ("pdf", "package")


@dataclass(frozen=True)
class Grant:
    contract_id: uuid.UUID
    what: Kind


class DownloadLinks:
    def __init__(self, bot_token: str, ttl_seconds: int) -> None:
        # Окремий ключ, похідний від токена бота: initData-підпис і посилання не змішуються.
        self._key = hmac.new(b"domovyk-download-links", bot_token.encode(), hashlib.sha256).digest()
        self._ttl = ttl_seconds

    def _sign(self, body: str) -> str:
        return hmac.new(self._key, body.encode(), hashlib.sha256).hexdigest()[:32]

    def issue(self, contract_id: uuid.UUID, what: Kind, now: datetime) -> str:
        body = f"{contract_id.hex}.{what}.{int(now.timestamp()) + self._ttl}"
        return f"{body}.{self._sign(body)}"

    def read(self, token: str, now: datetime) -> Grant | None:
        """None — підробка, чужий формат або посилання протухло."""
        parts = token.split(".")
        if len(parts) != 4:
            return None
        cid, what, exp, sig = parts
        if not hmac.compare_digest(sig, self._sign(f"{cid}.{what}.{exp}")):
            return None
        if what not in _KINDS or not exp.isdigit() or int(exp) < now.timestamp():
            return None
        try:
            return Grant(uuid.UUID(hex=cid), what)
        except ValueError:
            return None
