"""Транзакції (unit of work) без прив'язки до SQLAlchemy в сервісах.

Сервіс відкриває транзакцію через `TxManager.begin()` і передає `Tx`
у фабрики репозиторіїв. Для SQL `Tx` несе `AsyncSession`, у юніт-тестах —
порожній об'єкт, а репозиторії фейкові.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class Tx:
    """Маркер відкритої транзакції. Сервіси його не розглядають, лише передають далі."""


class TxManager(Protocol):
    def begin(self) -> AbstractAsyncContextManager[Tx]: ...


class SqlTx(Tx):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session


def session_of(tx: Tx) -> AsyncSession:
    if not isinstance(tx, SqlTx):
        raise TypeError("SQL-репозиторій отримав не SqlTx")
    return tx.session


class SqlTxManager:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def _begin(self) -> AsyncIterator[Tx]:
        async with self._sessions() as session, session.begin():
            yield SqlTx(session)

    def begin(self) -> AbstractAsyncContextManager[Tx]:
        return self._begin()


class NullTxManager:
    """Для юніт-тестів з фейковими репозиторіями."""

    @asynccontextmanager
    async def _begin(self) -> AsyncIterator[Tx]:
        yield Tx()

    def begin(self) -> AbstractAsyncContextManager[Tx]:
        return self._begin()
