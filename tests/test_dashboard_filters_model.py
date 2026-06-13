"""Persistence tests for the dashboard-level `filters` column."""

from __future__ import annotations

import uuid

import pytest

from app.models.dashboard import Dashboard
from app.models.user import User


def test_dashboard_has_filters_attr():
    assert hasattr(Dashboard(), "filters")


@pytest.mark.asyncio
async def test_dashboard_filters_roundtrip(db_session_factory):
    spec = [
        {
            "key": "country",
            "label": "Country",
            "type": "single_select",
            "options": {"source": "static", "values": ["", "US", "AE"]},
            "default": "",
            "ui": {},
        }
    ]
    async with db_session_factory() as db:
        u = User(email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
        db.add(u)
        await db.flush()
        d = Dashboard(
            user_id=u.id,
            title="Filtered",
            share_slug=uuid.uuid4().hex[:12],
            filters=spec,
        )
        db.add(d)
        await db.flush()
        did = d.id
        await db.commit()

    async with db_session_factory() as db:
        got = await db.get(Dashboard, did)
        assert got is not None
        assert got.filters[0]["type"] == "single_select"
        assert got.filters[0]["options"]["values"] == ["", "US", "AE"]


@pytest.mark.asyncio
async def test_dashboard_filters_defaults_to_empty(db_session_factory):
    async with db_session_factory() as db:
        u = User(email=f"owner-{uuid.uuid4().hex[:8]}@example.com")
        db.add(u)
        await db.flush()
        d = Dashboard(user_id=u.id, title="NoFilters", share_slug=uuid.uuid4().hex[:12])
        db.add(d)
        await db.flush()
        await db.commit()
        await db.refresh(d)
        assert d.filters == []
