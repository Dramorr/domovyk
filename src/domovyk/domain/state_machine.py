"""Явні таблиці дозволених переходів станів. Перехід — лише через `transition`."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from domovyk.core.errors import InvalidTransition


class TransitionTable[S: StrEnum]:
    def __init__(self, name: str, allowed: Mapping[S, frozenset[S]]) -> None:
        self.name = name
        self._allowed = dict(allowed)

    def can(self, current: S, target: S) -> bool:
        return target in self._allowed.get(current, frozenset())

    def transition(self, current: S, target: S) -> S:
        if not self.can(current, target):
            raise InvalidTransition(
                f"{self.name}: перехід {current.value} → {target.value} заборонено",
                machine=self.name,
                current=current.value,
                target=target.value,
            )
        return target
