# app/services/tracking_plan/drift/resolve.py
"""Resolve a project's drift targets: which GA4 property + BigQuery export dataset.

Runs outside any request context (a background job), so it resolves credentials
straight from the DB rather than the request-scoped context vars used by the
interactive tools. Persists best-effort discoveries back onto ``TPDriftConfig``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bq_connection import BQConnection
from app.models.connection import OAuthConnection
from app.models.token import GA4Property
from app.models.tracking_plan import TPDriftConfig


@dataclass
class DriftTargets:
    config: TPDriftConfig
    ga4_connection_id: str | None = None
    ga4_property_id: str | None = None
    bq_conn: BQConnection | None = None
    bq_dataset: str | None = None

    @property
    def has_ga4(self) -> bool:
        return bool(self.ga4_connection_id and self.ga4_property_id)

    @property
    def has_bq(self) -> bool:
        return bool(self.bq_conn and self.bq_dataset)


async def get_or_create_config(session: AsyncSession, project_id: uuid.UUID) -> TPDriftConfig:
    cfg = (
        await session.execute(select(TPDriftConfig).where(TPDriftConfig.project_id == project_id))
    ).scalar_one_or_none()
    if cfg is None:
        cfg = TPDriftConfig(project_id=project_id)
        session.add(cfg)
        await session.flush()
    return cfg


async def _resolve_ga4(
    session: AsyncSession, project_id: uuid.UUID, cfg: TPDriftConfig
) -> tuple[str | None, str | None]:
    conn = (
        await session.execute(
            select(OAuthConnection)
            .where(
                OAuthConnection.project_id == project_id,
                OAuthConnection.provider == "google",
                OAuthConnection.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if conn is None:
        return None, None
    # Prefer an explicitly configured property; else the connection's first active one.
    property_id = cfg.ga4_property_id
    if not property_id:
        prop = (
            await session.execute(
                select(GA4Property)
                .where(GA4Property.connection_id == conn.id, GA4Property.is_active.is_(True))
                .limit(1)
            )
        ).scalar_one_or_none()
        property_id = prop.property_id if prop else None
    return str(conn.id), property_id


def _derive_export_dataset(property_id: str | None, candidates: list[str] | None) -> str | None:
    """GA4 BigQuery export datasets are named ``analytics_<numeric property id>``."""
    if not property_id:
        return None
    digits = re.sub(r"\D", "", property_id)  # "properties/123" → "123"
    if not digits:
        return None
    guess = f"analytics_{digits}"
    if candidates:
        # Honour the connection's allowlist when it names the export dataset.
        if guess in candidates:
            return guess
        for c in candidates:
            if c.startswith("analytics_"):
                return c
        return None
    return guess


async def _resolve_bq(
    session: AsyncSession, project_id: uuid.UUID, cfg: TPDriftConfig, property_id: str | None
) -> tuple[BQConnection | None, str | None]:
    conn: BQConnection | None = None
    if cfg.bq_connection_id:
        conn = (
            await session.execute(select(BQConnection).where(BQConnection.id == cfg.bq_connection_id))
        ).scalar_one_or_none()
    if conn is None:
        conn = (
            await session.execute(
                select(BQConnection)
                .where(BQConnection.fluxito_project_id == project_id, BQConnection.is_active.is_(True))
                .limit(1)
            )
        ).scalar_one_or_none()
    if conn is None:
        return None, None
    dataset = cfg.bq_dataset or _derive_export_dataset(property_id, conn.datasets)
    return conn, dataset


async def resolve_drift_targets(session: AsyncSession, project_id: uuid.UUID) -> DriftTargets:
    cfg = await get_or_create_config(session, project_id)
    ga4_conn_id, property_id = await _resolve_ga4(session, project_id, cfg)
    bq_conn, dataset = await _resolve_bq(session, project_id, cfg, property_id)

    # Persist discoveries so later runs / the UI can see/override them.
    if property_id and not cfg.ga4_property_id:
        cfg.ga4_property_id = property_id
    if bq_conn and not cfg.bq_connection_id:
        cfg.bq_connection_id = bq_conn.id
    if dataset and not cfg.bq_dataset:
        cfg.bq_dataset = dataset

    return DriftTargets(
        config=cfg,
        ga4_connection_id=ga4_conn_id,
        ga4_property_id=property_id,
        bq_conn=bq_conn,
        bq_dataset=dataset,
    )
