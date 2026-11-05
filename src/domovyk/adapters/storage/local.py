"""Файли в локальній папці (`STORAGE_DIR`, у git не потрапляє — див. .gitignore)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from domovyk.adapters.storage.base import FileNotFoundInStorage

# Ключ — лише безпечні символи й «/» між частинами: жодного виходу за межі папки.
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*$")


class LocalFileStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if not _KEY_RE.fullmatch(key) or ".." in key:
            raise ValueError(f"Некоректний ключ файлу: {key!r}")
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError("Ключ виходить за межі сховища")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise FileNotFoundInStorage(key) from exc

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)
