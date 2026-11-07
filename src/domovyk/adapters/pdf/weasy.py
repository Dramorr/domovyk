"""PDF через WeasyPrint. Потребує Pango (Linux: пакет libpango; Windows: MSYS2).

На Windows шлях до DLL задає `WEASYPRINT_DLL_DIRECTORIES` (наприклад,
`C:\\msys64\\mingw64\\bin`). Імпорт відкладений: застосунок стартує й без Pango,
падає лише генерація PDF — зрозумілою помилкою.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from domovyk.adapters.pdf.base import PdfError


class WeasyPrintRenderer:
    def __init__(self, dll_directories: str = "") -> None:
        self._dll_directories = dll_directories
        self._html_cls: Any = None

    def _load(self) -> Any:
        if self._html_cls is None:
            if self._dll_directories:
                os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", self._dll_directories)
            try:
                from weasyprint import HTML
            except (ImportError, OSError) as exc:
                raise PdfError("WeasyPrint недоступний: немає бібліотек Pango") from exc
            self._html_cls = HTML
        return self._html_cls

    def _render_sync(self, html: str) -> bytes:
        html_cls = self._load()
        try:
            data: bytes = html_cls(string=html).write_pdf()
        except Exception as exc:
            raise PdfError("Не вдалося згенерувати PDF") from exc
        return data

    async def render(self, html: str) -> bytes:
        return await asyncio.to_thread(self._render_sync, html)
