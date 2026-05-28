"""Registry and scan result shapes for SDR v2 source gathering."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import app.app_state as state
from app.auth.mcp_session_manager import ProjectContext
from app.tools.sdr_parser import ParsedDestination, ParsedEvent

# Capability roles a data source can fill. The diagnostic engine reasons about
# these roles, never about named platforms — so GTM↔GA4, Adobe Launch↔Adobe
# Analytics, or tags↔warehouse all diagnose through the same logic.
ROLE_TAG_INVENTORY = "tag_inventory"        # is the event configured to collect?
ROLE_EVENT_VOLUME = "event_volume"          # is the event actually flowing?
ROLE_CONVERSION_CONFIG = "conversion_config"  # is it set up for activation/ROAS?
DIAGNOSTIC_ROLES: tuple[str, ...] = (ROLE_TAG_INVENTORY, ROLE_EVENT_VOLUME, ROLE_CONVERSION_CONFIG)


@dataclass
class SDRSourceScan:
    source: str
    status: str
    events: list[ParsedEvent] = field(default_factory=list)
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    resource_count: int = 0
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)
    roles: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "events": [_to_plain(e) for e in self.events],
            "raw_metadata": _to_plain(self.raw_metadata),
            "resource_count": self.resource_count,
            "duration_ms": self.duration_ms,
            "errors": list(self.errors),
            "roles": sorted(self.roles),
        }


@runtime_checkable
class SDRDataSource(Protocol):
    name: str
    display_name: str
    provides: frozenset[str]

    async def is_available(self, project_ctx: ProjectContext) -> bool:
        ...

    async def scan(self, project_ctx: ProjectContext, timeout_s: float = 45.0) -> SDRSourceScan:
        ...


class GA4DataSource:
    name = "ga4"
    display_name = "Google Analytics 4"
    provides = frozenset({ROLE_EVENT_VOLUME, ROLE_CONVERSION_CONFIG})

    async def is_available(self, project_ctx: ProjectContext) -> bool:
        return bool(getattr(project_ctx, "has_ga4", False) and getattr(project_ctx, "ga4_properties", []))

    async def scan(self, project_ctx: ProjectContext, timeout_s: float = 45.0) -> SDRSourceScan:
        started = time.perf_counter()
        errors: list[str] = []
        events: list[ParsedEvent] = []
        custom_dims: list[dict[str, Any]] = []
        conversions: list[dict[str, Any]] = []
        ga4 = getattr(state, "ga4_connector", None)
        conn_id = _first_connection_id(project_ctx, "google")
        if not ga4 or not conn_id:
            return _scan_result(self.name, "failed", started, errors=["GA4 connector or Google connection is unavailable."])

        for prop in getattr(project_ctx, "ga4_properties", []) or []:
            prop_id = prop.get("property_id") or prop.get("id")
            if not prop_id:
                continue
            try:
                conv_result = await asyncio.wait_for(ga4.get_conversion_events(conn_id, prop_id), timeout=timeout_s)
                conv_events = conv_result.get("conversion_events") or conv_result.get("events") or []
                conversions.extend(conv_events)
                for ce in conv_events:
                    event_name = ce.get("event_name") or ce.get("name", "")
                    if event_name:
                        events.append(
                            ParsedEvent(
                                name=event_name,
                                purpose=f"GA4 conversion event discovered from property {prop_id}.",
                                status="implemented",
                                destinations=[
                                    ParsedDestination(
                                        platform="ga4",
                                        platform_account_id=str(prop_id),
                                        dest_event_name=event_name,
                                    )
                                ],
                            )
                        )
            except Exception as exc:
                errors.append(f"Property {prop_id} conversion events: {exc}")

            try:
                dims_result = await asyncio.wait_for(ga4.list_custom_dimensions(conn_id, prop_id), timeout=timeout_s)
                custom_dims.extend(dims_result.get("custom_dimensions") or dims_result.get("dimensions") or [])
            except Exception as exc:
                errors.append(f"Property {prop_id} custom dimensions: {exc}")

        # Event volumes — the signal that proves "configured but never fires".
        event_volumes: dict[str, int] = {}
        for prop in getattr(project_ctx, "ga4_properties", []) or []:
            prop_id = prop.get("property_id") or prop.get("id")
            if not prop_id:
                continue
            try:
                vol = await asyncio.wait_for(
                    ga4.list_events(conn_id, prop_id, "30daysAgo", "today"), timeout=timeout_s
                )
                for ev in vol.get("events", []):
                    name = ev.get("event_name")
                    if name:
                        event_volumes[name] = event_volumes.get(name, 0) + int(ev.get("event_count", 0))
            except Exception as exc:
                errors.append(f"Property {prop_id} event volumes: {exc}")

        status = "partial" if errors and events else "failed" if errors and not events else "success"
        return _scan_result(
            self.name,
            status,
            started,
            events=events,
            resource_count=len(getattr(project_ctx, "ga4_properties", []) or []),
            errors=errors,
            raw_metadata={
                "custom_dimensions": custom_dims,
                "conversion_events": conversions,
                "event_volumes": event_volumes,
            },
        )


class GTMDataSource:
    name = "gtm"
    display_name = "Google Tag Manager"
    provides = frozenset({ROLE_TAG_INVENTORY})

    async def is_available(self, project_ctx: ProjectContext) -> bool:
        return bool(getattr(project_ctx, "has_gtm", False) and getattr(project_ctx, "gtm_containers", []))

    async def scan(self, project_ctx: ProjectContext, timeout_s: float = 45.0) -> SDRSourceScan:
        started = time.perf_counter()
        errors: list[str] = []
        events: list[ParsedEvent] = []
        tags_seen: list[dict[str, Any]] = []
        triggers_seen: list[dict[str, Any]] = []
        consent_detected = False
        gtm = getattr(state, "gtm_connector", None)
        conn_id = _first_connection_id(project_ctx, "google")
        if not gtm or not conn_id:
            return _scan_result(self.name, "failed", started, errors=["GTM connector or Google connection is unavailable."])

        for container in getattr(project_ctx, "gtm_containers", []) or []:
            account_id = container.get("account_id")
            container_id = container.get("container_id")
            if not account_id or not container_id:
                continue
            try:
                tags_result = await asyncio.wait_for(gtm.list_tags(conn_id, account_id, container_id), timeout=timeout_s)
                tags = tags_result.get("tags") or []
                tags_seen.extend(tags)
                triggers_result = await asyncio.wait_for(
                    gtm.list_triggers(conn_id, account_id, container_id), timeout=timeout_s
                )
                triggers = triggers_result.get("triggers") or []
                triggers_seen.extend(triggers)
                trigger_map = {t.get("triggerId"): t for t in triggers if t.get("triggerId")}

                for tag in tags:
                    tag_name = tag.get("name", "")
                    tag_type = tag.get("type", "")
                    if tag_type in ("cvt_", "consent") or "consent" in tag_name.lower():
                        consent_detected = True
                        continue
                    event_name = _infer_event_name_from_tag(tag)
                    if not event_name:
                        continue
                    trigger_type, trigger_config = _infer_trigger(tag, trigger_map)
                    dest_platform = "google_ads" if _is_ads_tag(tag_type) else "ga4"
                    events.append(
                        ParsedEvent(
                            name=event_name,
                            purpose=f"Discovered from GTM tag '{tag_name}' (type: {tag_type}).",
                            trigger_type=trigger_type,
                            trigger_config=trigger_config,
                            status="implemented",
                            destinations=[ParsedDestination(platform=dest_platform, dest_event_name=event_name)],
                        )
                    )
            except Exception as exc:
                errors.append(f"Container {container_id}: {exc}")

        status = "partial" if errors and events else "failed" if errors and not events else "success"
        return _scan_result(
            self.name,
            status,
            started,
            events=events,
            resource_count=len(getattr(project_ctx, "gtm_containers", []) or []),
            errors=errors,
            raw_metadata={"tags": tags_seen, "triggers": triggers_seen, "consent_detected": consent_detected},
        )


class GoogleAdsDataSource:
    name = "google_ads"
    display_name = "Google Ads"
    provides = frozenset({ROLE_CONVERSION_CONFIG})

    async def is_available(self, project_ctx: ProjectContext) -> bool:
        return bool(getattr(project_ctx, "has_ads", False) and getattr(project_ctx, "ads_accounts", []))

    async def scan(self, project_ctx: ProjectContext, timeout_s: float = 45.0) -> SDRSourceScan:
        started = time.perf_counter()
        errors: list[str] = []
        events: list[ParsedEvent] = []
        raw_actions: list[dict[str, Any]] = []
        ads = getattr(state, "ads_connector", None)
        conn_id = _first_connection_id(project_ctx, "google")
        if not ads or not conn_id:
            return _scan_result(self.name, "failed", started, errors=["Google Ads connector or Google connection is unavailable."])

        for acct in getattr(project_ctx, "ads_accounts", []) or []:
            customer_id = acct.get("customer_id")
            if not customer_id:
                continue
            try:
                conv_result = await asyncio.wait_for(ads.get_conversion_actions(conn_id, customer_id), timeout=timeout_s)
                conv_actions = conv_result.get("conversion_actions") or conv_result.get("conversions") or []
                raw_actions.extend(conv_actions)
                for conv in conv_actions:
                    conv_name = conv.get("name", "")
                    if conv_name:
                        event_name = conv_name.lower().replace(" ", "_").replace("-", "_")
                        events.append(
                            ParsedEvent(
                                name=event_name,
                                purpose=f"Google Ads conversion action '{conv_name}'.",
                                status="implemented",
                                destinations=[
                                    ParsedDestination(
                                        platform="google_ads",
                                        platform_account_id=str(customer_id),
                                        dest_event_name=conv_name,
                                    )
                                ],
                            )
                        )
            except Exception as exc:
                errors.append(f"Customer {customer_id}: {exc}")

        status = "partial" if errors and events else "failed" if errors and not events else "success"
        return _scan_result(
            self.name,
            status,
            started,
            events=events,
            resource_count=len(getattr(project_ctx, "ads_accounts", []) or []),
            errors=errors,
            raw_metadata={"conversion_actions": raw_actions},
        )


DATA_SOURCES: tuple[SDRDataSource, ...] = (GA4DataSource(), GTMDataSource(), GoogleAdsDataSource())
DATA_SOURCE_REGISTRY: dict[str, SDRDataSource] = {source.name: source for source in DATA_SOURCES}

# Stable names of every source the scanner can actually read today. New
# connectors become first-class by appending an SDRDataSource above — the rest
# of the system (summaries, deltas, instructions) keys off this set.
SUPPORTED_SOURCE_NAMES: tuple[str, ...] = tuple(DATA_SOURCE_REGISTRY.keys())


async def _available_sources_async(project_ctx: ProjectContext, requested: list[str] | None = None) -> list[SDRDataSource]:
    sources = [DATA_SOURCE_REGISTRY[name] for name in requested or DATA_SOURCE_REGISTRY.keys() if name in DATA_SOURCE_REGISTRY]
    available: list[SDRDataSource] = []
    for source in sources:
        try:
            if await source.is_available(project_ctx):
                available.append(source)
        except Exception:
            continue
    return available


def get_available_sources(project_ctx: ProjectContext, requested: list[str] | None = None) -> list[str]:
    sources = [DATA_SOURCE_REGISTRY[name] for name in requested or DATA_SOURCE_REGISTRY.keys() if name in DATA_SOURCE_REGISTRY]
    available: list[str] = []
    for source in sources:
        flag_name = "has_ads" if source.name == "google_ads" else f"has_{source.name}"
        if bool(getattr(project_ctx, flag_name, False)):
            available.append(source.name)
    return available


async def scan_sources(
    project_ctx: ProjectContext,
    requested: list[str] | None = None,
    timeout_s: float = 45.0,
) -> dict[str, SDRSourceScan]:
    sources = await _available_sources_async(project_ctx, requested)
    tasks = [_scan_one(source, project_ctx, timeout_s) for source in sources]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    scans: dict[str, SDRSourceScan] = {}
    for source, result in zip(sources, results, strict=False):
        if isinstance(result, SDRSourceScan):
            scans[source.name] = result
        elif isinstance(result, Exception):
            scans[source.name] = SDRSourceScan(source=source.name, status="failed", errors=[str(result)])
    return scans


def compute_source_fingerprint(project_ctx: ProjectContext) -> str:
    resources = {
        "connections": sorted(
            (
                {"id": str(getattr(c, "id", "")), "provider": str(getattr(c, "provider", ""))}
                for c in getattr(project_ctx, "connections", []) or []
            ),
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
        "ga4_properties": _sorted_dicts(getattr(project_ctx, "ga4_properties", []) or [], ["property_id", "id"]),
        "gtm_containers": _sorted_dicts(getattr(project_ctx, "gtm_containers", []) or [], ["account_id", "container_id"]),
        "ads_accounts": _sorted_dicts(getattr(project_ctx, "ads_accounts", []) or [], ["customer_id", "id"]),
        "search_console_sites": _sorted_dicts(getattr(project_ctx, "search_console_sites", []) or [], ["site_url", "url"]),
    }
    payload = json.dumps(resources, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def connected_but_unsupported(project_ctx: ProjectContext) -> list[str]:
    """Platforms the project has connected that the SDR scanner cannot read yet.

    These are invisible to ``scan_sources`` (no registered SDRDataSource), so we
    surface them explicitly — otherwise Claude reports "fully scanned" while a
    connected Meta or warehouse silently contributes nothing.
    """
    return [name for name in _connected_platforms(project_ctx) if name not in DATA_SOURCE_REGISTRY]


def connected_sources_summary(project_ctx: ProjectContext, scans: dict[str, SDRSourceScan]) -> dict[str, Any]:
    failures = []
    for scan in scans.values():
        if scan.status in {"partial", "failed"}:
            failures.append(
                {
                    "source": scan.source,
                    "status": scan.status,
                    "error": "; ".join(scan.errors) if scan.errors else scan.status,
                    "duration_ms": scan.duration_ms,
                }
            )
    connected = _connected_platforms(project_ctx)
    return {
        "total_connected_platforms": len(connected),
        "connected_platforms": connected,
        "supported_sources": list(SUPPORTED_SOURCE_NAMES),
        "scanned_successfully": [name for name, scan in scans.items() if scan.status == "success"],
        "partial_failures": failures,
        # Connected platforms with no scanner yet — real data Claude must not
        # pretend it analysed. Drives an honest "connected but not yet covered" caveat.
        "connected_but_unsupported": connected_but_unsupported(project_ctx),
        "resources_discovered": {
            "ga4_properties": len(getattr(project_ctx, "ga4_properties", []) or []),
            "gtm_containers": len(getattr(project_ctx, "gtm_containers", []) or []),
            "google_ads_accounts": len(getattr(project_ctx, "ads_accounts", []) or []),
            "search_console_sites": len(getattr(project_ctx, "search_console_sites", []) or []),
        },
    }


def scan_summary(scans: dict[str, SDRSourceScan]) -> dict[str, Any]:
    all_events = _merge_events([event for scan in scans.values() for event in scan.events])
    todo_events = sum(1 for event in all_events if not event.purpose or "[TODO" in (event.purpose or ""))
    return {
        "events_discovered": len(all_events),
        "source_count": len(scans),
        "successful_source_count": sum(1 for scan in scans.values() if scan.status == "success"),
        "partial_or_failed_source_count": sum(1 for scan in scans.values() if scan.status != "success"),
        "todo_like_event_count": todo_events,
    }


def reproducibility_info(project_ctx: ProjectContext, intake_id: str | None = None) -> dict[str, Any]:
    return {
        "intake_id": intake_id,
        "source_fingerprint": compute_source_fingerprint(project_ctx),
        "scan_timestamp": datetime.now(UTC).isoformat(),
    }


def scans_to_dict(scans: dict[str, SDRSourceScan]) -> dict[str, dict[str, Any]]:
    return {name: scan.to_dict() for name, scan in scans.items()}


def merge_scan_events(scans: dict[str, SDRSourceScan]) -> list[ParsedEvent]:
    return _merge_events([event for scan in scans.values() for event in scan.events])


async def _scan_one(source: SDRDataSource, project_ctx: ProjectContext, timeout_s: float) -> SDRSourceScan:
    roles = getattr(source, "provides", frozenset())
    try:
        result = await asyncio.wait_for(source.scan(project_ctx, timeout_s), timeout=timeout_s + 1)
        result.roles = roles
        return result
    except Exception as exc:
        return SDRSourceScan(source=source.name, status="failed", errors=[str(exc)], roles=roles)


def _scan_result(
    source: str,
    status: str,
    started: float,
    *,
    events: list[ParsedEvent] | None = None,
    raw_metadata: dict[str, Any] | None = None,
    resource_count: int = 0,
    errors: list[str] | None = None,
) -> SDRSourceScan:
    return SDRSourceScan(
        source=source,
        status=status,
        events=events or [],
        raw_metadata=raw_metadata or {},
        resource_count=resource_count,
        duration_ms=round((time.perf_counter() - started) * 1000),
        errors=errors or [],
    )


def _first_connection_id(project_ctx: ProjectContext, provider: str) -> str | None:
    for connection in getattr(project_ctx, "connections", []) or []:
        if getattr(connection, "provider", None) == provider:
            return str(connection.id)
    return None


def _infer_event_name_from_tag(tag: dict[str, Any]) -> str | None:
    tag_name = tag.get("name", "")
    for param in tag.get("parameter") or []:
        key = param.get("key", "")
        val = param.get("value", "")
        if key in ("eventName", "event_name", "event") and val:
            return str(val).lower().replace(" ", "_")
    name = tag_name
    for prefix in ("GA4 -", "GA4-", "GA4:", "UA -", "UA-", "Ads -", "Ads:"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    name = name.strip().lower().replace(" ", "_").replace("-", "_")
    return None if name in ("", "tag", "pixel", "script", "html", "custom_html") else name


def _infer_trigger(tag: dict[str, Any], trigger_map: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    trigger_type = "custom"
    trigger_config: dict[str, Any] = {}
    for trigger_id in tag.get("firingTriggerId") or []:
        trigger = trigger_map.get(trigger_id)
        if not trigger:
            continue
        trigger_kind = str(trigger.get("type", "")).lower()
        if "pageview" in trigger_kind:
            trigger_type = "pageview"
        elif "click" in trigger_kind:
            trigger_type = "click"
        elif "form" in trigger_kind:
            trigger_type = "form_submit"
        elif "custom" in trigger_kind or "event" in trigger_kind:
            trigger_type = "datalayer_event"
        trigger_config["trigger_name"] = trigger.get("name")
        break
    return trigger_type, trigger_config or None


def _is_ads_tag(tag_type: str) -> bool:
    tag_type_l = tag_type.lower()
    return "ads" in tag_type_l or "awct" in tag_type_l or "floodlight" in tag_type_l


def _merge_events(events: list[ParsedEvent]) -> list[ParsedEvent]:
    by_name: dict[str, ParsedEvent] = {}
    for event in events:
        if event.name not in by_name:
            by_name[event.name] = event
            continue
        existing = by_name[event.name]
        existing_platforms = {destination.platform for destination in existing.destinations}
        for destination in event.destinations:
            if destination.platform not in existing_platforms:
                existing.destinations.append(destination)
                existing_platforms.add(destination.platform)
        if not existing.trigger_type and event.trigger_type:
            existing.trigger_type = event.trigger_type
            existing.trigger_config = event.trigger_config
    return list(by_name.values())


def _sorted_dicts(items: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    normalized = []
    for item in items:
        normalized.append({key: item.get(key) for key in keys if item.get(key) is not None})
    return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))


def _connected_platforms(project_ctx: ProjectContext) -> list[str]:
    flags = {
        "ga4": "has_ga4",
        "gtm": "has_gtm",
        "google_ads": "has_ads",
        "search_console": "has_gsc",
        "bigquery": "has_bq",
        "meta": "has_meta",
        "tiktok": "has_tiktok",
        "snap": "has_snap",
        "linkedin": "has_linkedin",
        "pinterest": "has_pinterest",
        "amplitude": "has_amplitude",
        "adobe_analytics": "has_adobe_analytics",
        "adobe_launch": "has_adobe_launch",
        "redshift": "has_redshift",
        "snowflake": "has_snowflake",
    }
    return [name for name, flag in flags.items() if getattr(project_ctx, flag, False)]


def _to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _to_plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value
