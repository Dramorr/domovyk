"""Генерація PDF з HTML (правило 4): WeasyPrint зараз, будь-що інше пізніше."""

from __future__ import annotations

from typing import Protocol


class PdfError(Exception):
    """Не вдалося згенерувати PDF (немає системних бібліотек, помилка верстки)."""


class PdfRenderer(Protocol):
    async def render(self, html: str) -> bytes: ...
