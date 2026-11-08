"""Фото об'єкта на справжньому Postgres: порядок, обкладинка, каскад, обмеження."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from domovyk.container import Services
from tests.integration.test_leases_db import OWNER, TENANT, _setup
from tests.unit.test_property_photos import picture

pytestmark = pytest.mark.integration


async def test_photos_db(services: Services, engine: AsyncEngine) -> None:
    s = services
    data = await _setup(s, engine)
    pid = data.property_id
    await s.properties.add_photo(OWNER, pid, picture())
    photos = await s.properties.add_photo(OWNER, pid, picture(900, 1200, fmt="PNG"))
    assert [(p.position, p.width, p.height) for p in photos] == [(0, 1200, 900), (1, 900, 1200)]
    photos = await s.properties.make_cover(OWNER, pid, photos[1].id)
    assert photos[0].height == 1200

    lease, invite = await s.leases.create_lease(OWNER, data)
    await s.leases.accept_invite(TENANT, invite.token)
    assert len(await s.leases.photos(TENANT, lease.id)) == 2

    async with engine.connect() as conn:
        assert (
            await conn.scalar(text("SELECT count(*) FROM property_photos WHERE phash IS NULL")) == 0
        )
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE property_photos SET position = -1"))
        await conn.rollback()

    photos = await s.properties.delete_photo(OWNER, pid, photos[0].id)
    assert [p.position for p in photos] == [0]
