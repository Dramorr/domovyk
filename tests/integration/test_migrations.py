"""Міграції на реальному PostgreSQL + PostGIS (testcontainers). Потрібен Docker."""

from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.skip("Docker недоступний")
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgis/postgis:16-3.5", driver="psycopg") as pg:
        yield pg.get_connection_url()


def test_upgrade_and_downgrade(pg_url: str) -> None:
    async_url = pg_url.replace("+psycopg", "+asyncpg")
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = async_url

    command.upgrade(cfg, "head")
    engine = create_engine(pg_url)
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname='postgis'")
        ).scalar()

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine.dispose()
