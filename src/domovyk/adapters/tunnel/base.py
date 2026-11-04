"""Тунель: робить локальний порт доступним з інтернету (лише для dev)."""

from __future__ import annotations

from typing import Protocol


class TunnelError(RuntimeError):
    pass


class Tunnel(Protocol):
    async def start(self, port: int) -> str:
        """Відкрити тунель на 127.0.0.1:<port>, повернути публічний https-URL."""
        ...

    async def stop(self) -> None:
        """Закрити тунель (і дочірній процес, якщо він є)."""
        ...
