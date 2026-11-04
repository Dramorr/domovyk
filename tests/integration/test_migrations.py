"""Міграції на реальному PostgreSQL + PostGIS (testcontainers). Потрібен Docker."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_upgrade_downgrade_upgrade(migrated_url: str, pg_url: str) -> None:
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = migrated_url

    engine = create_engine(pg_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM pg_extension WHERE extname='postgis'")).scalar()
        assert conn.execute(text("SELECT to_regclass('public.users')")).scalar()

    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('public.users')")).scalar() is None
    command.upgrade(cfg, "head")
    engine.dispose()
