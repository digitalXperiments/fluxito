"""
SDR Markdown Parser, Projection Builder, and Generator.

Responsibilities:
  1. **Parse** — Read SDR markdown into structured Python objects.
  2. **Project** — Rebuild sdr_events / sdr_parameters / sdr_destinations
     from parsed data (called on every save).
  3. **Generate** — Produce SDR markdown from structured data (bootstrap).

The markdown schema follows the canonical template defined in the SDR spec
(§6). The parser is intentionally robust to partial/malformed documents —
during refinement a section may be incomplete or missing.

Dependencies: python-frontmatter (for YAML frontmatter parsing).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import frontmatter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsed data structures
# ---------------------------------------------------------------------------


@dataclass
class ParsedParameter:
    name: str
    type: str | None = None
    required: bool = False
    source: str | None = None
    example: str | None = None
    validation_rule: str | None = None


@dataclass
class ParsedDestination:
    platform: str
    platform_account_id: str | None = None
    dest_event_name: str | None = None
    mapping: dict | None = None


@dataclass
class ParsedEvent:
    name: str
    purpose: str | None = None
    trigger_type: str | None = None
    trigger_config: dict | None = None
    status: str | None = None
    owner_business: str | None = None
    owner_technical: str | None = None
    consent_required: list[str] | None = None
    kpi_links: list[str] | None = None
    parameters: list[ParsedParameter] = field(default_factory=list)
    destinations: list[ParsedDestination] = field(default_factory=list)


@dataclass
class ParsedSDR:
    """Full parsed representation of an SDR markdown document."""

    # Frontmatter
    sdr_name: str | None = None
    sdr_version: str | None = None
    sdr_status: str | None = None
    project_id: str | None = None
    business_type: str | None = None
    last_updated: str | None = None
    last_approved_by: str | None = None
    last_approved_at: str | None = None

    # Sections (raw markdown)
    business_context: str | None = None
    user_journeys: str | None = None
    data_layer_schema: str | None = None
    user_properties: str | None = None
    destinations_matrix: str | None = None
    consent_and_privacy: str | None = None
    ownership: str | None = None
    changelog: str | None = None

    # Structured
    events: list[ParsedEvent] = field(default_factory=list)

    # Gaps
    todo_markers: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Valid values
# ---------------------------------------------------------------------------
VALID_EVENT_STATUSES = {"planned", "implemented", "verified", "deprecated"}
VALID_TRIGGER_TYPES = {"pageview", "click", "form_submit", "datalayer_event", "scroll", "timer", "custom"}
VALID_PARAM_TYPES = {"string", "number", "boolean", "array", "object"}

# Known destination platforms
KNOWN_PLATFORMS = {"ga4", "google_ads", "meta", "tiktok", "linkedin", "snap", "custom"}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_sdr_markdown(markdown_text: str) -> ParsedSDR:
    """
    Parse an SDR markdown document into structured data.

    Robust to partial/malformed documents — missing sections are None,
    malformed tables are skipped with warnings.
    """
    result = ParsedSDR()

    if not markdown_text or not markdown_text.strip():
        return result

    # Parse frontmatter
    try:
        post = frontmatter.loads(markdown_text)
        meta = post.metadata or {}
        result.sdr_name = meta.get("sdr_name")
        result.sdr_version = meta.get("sdr_version")
        result.sdr_status = meta.get("sdr_status")
        result.project_id = meta.get("project_id")
        result.business_type = meta.get("business_type")
        result.last_updated = meta.get("last_updated")
        result.last_approved_by = meta.get("last_approved_by")
        result.last_approved_at = meta.get("last_approved_at")
        content = post.content
    except Exception as e:
        logger.warning(f"SDR frontmatter parse error: {e}")
        content = markdown_text

    # Split into H2 sections
    sections = _split_h2_sections(content)

    # Map sections
    for heading, body in sections.items():
        heading_lower = heading.lower().strip()
        if "business context" in heading_lower:
            result.business_context = body
        elif "user journeys" in heading_lower or "user journey" in heading_lower:
            result.user_journeys = body
        elif "data layer" in heading_lower:
            result.data_layer_schema = body
        elif "event catalog" in heading_lower:
            result.events = _parse_event_catalog(body)
        elif "user properties" in heading_lower or "custom dimensions" in heading_lower:
            result.user_properties = body
        elif "destinations matrix" in heading_lower or "destination matrix" in heading_lower:
            result.destinations_matrix = body
        elif "consent" in heading_lower:
            result.consent_and_privacy = body
        elif "ownership" in heading_lower or "governance" in heading_lower:
            result.ownership = body
        elif "changelog" in heading_lower:
            result.changelog = body

    # Find all [TODO: ...] markers
    result.todo_markers = _find_todo_markers(markdown_text)

    return result


def _split_h2_sections(content: str) -> dict[str, str]:
    """Split markdown content by H2 headings. Returns {heading: body}."""
    sections: dict[str, str] = {}
    pattern = re.compile(r"^## (.+)$", re.MULTILINE)
    matches = list(pattern.finditer(content))

    for i, match in enumerate(matches):
        heading = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        sections[heading] = body

    return sections


def _parse_event_catalog(catalog_body: str) -> list[ParsedEvent]:
    """Parse the Event Catalog section into structured events."""
    events: list[ParsedEvent] = []

    # Split by H3 headings — each is an event
    pattern = re.compile(r"^### (.+)$", re.MULTILINE)
    matches = list(pattern.finditer(catalog_body))

    for i, match in enumerate(matches):
        event_name = match.group(1).strip()
        # Strip backticks from event name
        event_name = event_name.strip("`")
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(catalog_body)
        event_body = catalog_body[start:end].strip()

        try:
            event = _parse_single_event(event_name, event_body)
            events.append(event)
        except Exception as e:
            logger.warning(f"Failed to parse event '{event_name}': {e}")
            # Still add a minimal event entry
            events.append(ParsedEvent(name=event_name))

    return events


def _parse_single_event(name: str, body: str) -> ParsedEvent:
    """Parse a single event section into a ParsedEvent."""
    event = ParsedEvent(name=name)

    # Status line: *Status:* `implemented` | *Last verified:* `2024-01-15`
    status_match = re.search(r"\*Status:\*\s*`?(\w+)`?", body)
    if status_match:
        s = status_match.group(1).lower()
        if s in VALID_EVENT_STATUSES:
            event.status = s

    # Business Purpose
    purpose_match = re.search(
        r"\*\*Business Purpose:\*\*\s*(.+?)(?=\n\*\*|\n---|\Z)",
        body,
        re.DOTALL,
    )
    if purpose_match:
        event.purpose = purpose_match.group(1).strip()

    # Triggers
    trigger_section = _extract_section(body, "Triggers")
    if trigger_section:
        type_match = re.search(r"Type:\s*`?(\w+)`?", trigger_section)
        if type_match:
            t = type_match.group(1).lower()
            event.trigger_type = t if t in VALID_TRIGGER_TYPES else "custom"

        config_match = re.search(r"Configuration:\s*(.+)", trigger_section)
        conditions_match = re.search(r"Conditions:\s*(.+)", trigger_section)
        config = {}
        if config_match:
            config["configuration"] = config_match.group(1).strip()
        if conditions_match:
            config["conditions"] = conditions_match.group(1).strip()
        if config:
            event.trigger_config = config

    # Parameters table
    event.parameters = _parse_parameters_table(body)

    # Destinations
    event.destinations = _parse_destinations(body)

    # Consent Requirements
    consent_match = re.search(r"\*\*Consent Requirements:\*\*\s*`?(.+?)`?\s*$", body, re.MULTILINE)
    if consent_match:
        raw = consent_match.group(1).strip().strip("`")
        event.consent_required = [c.strip() for c in raw.split("|") if c.strip()]
        if not event.consent_required:
            event.consent_required = [c.strip() for c in raw.split(",") if c.strip()]

    # Owners
    owners_match = re.search(r"\*\*Owners:\*\*\s*(.+)", body)
    if owners_match:
        owners_text = owners_match.group(1)
        biz_match = re.search(r"Business:\s*(.+?)(?:\s*[·|]\s*Technical:|\Z)", owners_text)
        tech_match = re.search(r"Technical:\s*(.+)", owners_text)
        if biz_match:
            event.owner_business = biz_match.group(1).strip()
        if tech_match:
            event.owner_technical = tech_match.group(1).strip()

    # Related KPIs
    kpi_match = re.search(r"\*\*Related KPIs:\*\*\s*(.+)", body)
    if kpi_match:
        raw = kpi_match.group(1).strip()
        event.kpi_links = [k.strip() for k in raw.split(",") if k.strip()]

    return event


def _extract_section(body: str, heading: str) -> str | None:
    """Extract content under a bold heading like **Triggers:**."""
    pattern = re.compile(
        rf"\*\*{re.escape(heading)}(?::\*\*|\*\*:)\s*\n?(.*?)(?=\n\*\*[A-Z]|\n---|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(body)
    if match:
        return match.group(1).strip()
    return None


def _parse_parameters_table(body: str) -> list[ParsedParameter]:
    """Parse a markdown table of parameters."""
    params: list[ParsedParameter] = []

    # Find the parameters section
    params_section = _extract_section(body, "Parameters")
    if not params_section:
        return params

    # Find markdown table rows
    table_lines = [
        line.strip()
        for line in params_section.split("\n")
        if line.strip().startswith("|")
        and not line.strip().startswith("|---")
        and not line.strip().startswith("| ---")
    ]

    if len(table_lines) < 2:
        return params

    # Parse header to find column indices
    header = table_lines[0]
    cols = [c.strip().lower() for c in header.split("|")]
    cols = [c for c in cols if c]  # remove empty

    # Find column indices
    col_map: dict[str, int] = {}
    for i, c in enumerate(cols):
        if "name" in c:
            col_map["name"] = i
        elif "type" in c:
            col_map["type"] = i
        elif "required" in c:
            col_map["required"] = i
        elif "source" in c:
            col_map["source"] = i
        elif "example" in c:
            col_map["example"] = i
        elif "validation" in c:
            col_map["validation"] = i

    if "name" not in col_map:
        return params

    # Parse data rows (skip header + separator)
    for line in table_lines[1:]:
        # Skip separator rows
        if re.match(r"^\|[\s\-|]+\|$", line):
            continue

        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c != ""]  # remove empty from leading/trailing |

        if len(cells) <= col_map["name"]:
            continue

        name_val = cells[col_map["name"]].strip().strip("`{}")
        if not name_val or name_val.startswith("---"):
            continue

        param = ParsedParameter(name=name_val)
        if "type" in col_map and len(cells) > col_map["type"]:
            param.type = cells[col_map["type"]].strip().lower() or None
        if "required" in col_map and len(cells) > col_map["required"]:
            r = cells[col_map["required"]].strip().lower()
            param.required = r in ("yes", "true", "required", "✓", "x")
        if "source" in col_map and len(cells) > col_map["source"]:
            param.source = cells[col_map["source"]].strip() or None
        if "example" in col_map and len(cells) > col_map["example"]:
            param.example = cells[col_map["example"]].strip().strip("`") or None
        if "validation" in col_map and len(cells) > col_map["validation"]:
            param.validation_rule = cells[col_map["validation"]].strip() or None

        params.append(param)

    return params


def _parse_destinations(body: str) -> list[ParsedDestination]:
    """Parse destination list items from an event section."""
    dests: list[ParsedDestination] = []

    dest_section = _extract_section(body, "Destinations")
    if not dest_section:
        return dests

    # Pattern: - **GA4** (property `G-XXX`): event name `purchase`, ...
    # Or simpler: - **GA4**: ...
    pattern = re.compile(
        r"-\s*\*\*(.+?)\*\*\s*(?:\((?:property|customer|pixel|account)\s*`?([^)]*?)`?\))?\s*:\s*(.+)",
        re.IGNORECASE,
    )

    for match in pattern.finditer(dest_section):
        platform_raw = match.group(1).strip().lower()
        account_id = match.group(2).strip() if match.group(2) else None
        details = match.group(3).strip()

        # Normalize platform name
        platform = _normalize_platform(platform_raw)

        dest = ParsedDestination(
            platform=platform,
            platform_account_id=account_id,
        )

        # Try to extract dest event name
        event_name_match = re.search(r"event name\s*`?([^`,]+)`?", details, re.IGNORECASE)
        if event_name_match:
            dest.dest_event_name = event_name_match.group(1).strip()

        # Extract param mapping if present
        mapping_match = re.search(r"param mapping:\s*(.+)", details, re.IGNORECASE)
        if mapping_match:
            dest.mapping = {"raw": mapping_match.group(1).strip()}

        dests.append(dest)

    return dests


def _normalize_platform(raw: str) -> str:
    """Normalize a platform name to our canonical form."""
    raw = raw.lower().strip()
    if "ga4" in raw or "google analytics" in raw:
        return "ga4"
    if "google ads" in raw or "gads" in raw:
        return "google_ads"
    if "meta" in raw or "facebook" in raw:
        return "meta"
    if "tiktok" in raw:
        return "tiktok"
    if "linkedin" in raw:
        return "linkedin"
    if "snap" in raw:
        return "snap"
    return "custom"


def _find_todo_markers(text: str) -> list[dict]:
    """Find all [TODO: ...] markers in the document."""
    todos: list[dict] = []
    pattern = re.compile(r"\[TODO:\s*(.+?)\]", re.IGNORECASE)
    for match in pattern.finditer(text):
        # Find the line number
        line_num = text[: match.start()].count("\n") + 1
        # Find the nearest H2 or H3 heading above
        section = _find_nearest_heading(text, match.start())
        todos.append(
            {
                "description": match.group(1).strip(),
                "line": line_num,
                "section": section,
                "raw": match.group(0),
            }
        )
    return todos


def _find_nearest_heading(text: str, position: int) -> str:
    """Find the nearest heading above a position."""
    preceding = text[:position]
    headings = re.findall(r"^(#{2,3})\s+(.+)$", preceding, re.MULTILINE)
    if headings:
        return headings[-1][1].strip()
    return "document"


# ---------------------------------------------------------------------------
# Projection Builder — rebuild DB projections from parsed markdown
# ---------------------------------------------------------------------------


async def rebuild_projections(session: Any, sdr: Any) -> None:
    """
    Parse markdown_content and rebuild sdr_events, sdr_parameters,
    sdr_destinations in the database. Called on every save. Atomic.

    Args:
        session: AsyncSession
        sdr: SDR model instance (with .id and .markdown_content)
    """
    from sqlalchemy import delete

    from app.models.sdr import SDRDestination, SDREvent, SDRParameter

    parsed = parse_sdr_markdown(sdr.markdown_content)

    # Delete existing projections
    await session.execute(
        delete(SDRParameter).where(
            SDRParameter.event_id.in_(
                session.query(SDREvent.id).filter(SDREvent.sdr_id == sdr.id).subquery()  # type: ignore
            )
        )
    )
    await session.execute(
        delete(SDRDestination).where(
            SDRDestination.event_id.in_(
                session.query(SDREvent.id).filter(SDREvent.sdr_id == sdr.id).subquery()  # type: ignore
            )
        )
    )
    await session.execute(delete(SDREvent).where(SDREvent.sdr_id == sdr.id))

    # Re-insert from parsed
    for event_data in parsed.events:
        event = SDREvent(
            sdr_id=sdr.id,
            name=event_data.name,
            purpose=event_data.purpose,
            trigger_type=event_data.trigger_type,
            trigger_config=event_data.trigger_config,
            status=event_data.status,
            owner_business=event_data.owner_business,
            owner_technical=event_data.owner_technical,
            consent_required=event_data.consent_required,
            kpi_links=event_data.kpi_links,
        )
        session.add(event)
        await session.flush()  # get event.id

        for param in event_data.parameters:
            session.add(
                SDRParameter(
                    event_id=event.id,
                    name=param.name,
                    type=param.type,
                    required=param.required,
                    source=param.source,
                    example=param.example,
                    validation_rule=param.validation_rule,
                )
            )

        for dest in event_data.destinations:
            session.add(
                SDRDestination(
                    event_id=event.id,
                    platform=dest.platform,
                    platform_account_id=dest.platform_account_id,
                    dest_event_name=dest.dest_event_name,
                    mapping=dest.mapping,
                )
            )

    sdr.parsed_at = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Projection Builder (async-compatible, using raw SQL for subqueries)
# ---------------------------------------------------------------------------


async def rebuild_projections_async(session: Any, sdr: Any) -> None:
    """
    Async-safe version of rebuild_projections using raw select() subqueries
    instead of session.query() (which is sync-only).
    """
    from sqlalchemy import delete, select

    from app.models.sdr import SDRDestination, SDREvent, SDRParameter

    parsed = parse_sdr_markdown(sdr.markdown_content)

    # Delete in correct order (children first)
    await session.execute(
        delete(SDRParameter).where(
            SDRParameter.event_id.in_(select(SDREvent.id).where(SDREvent.sdr_id == sdr.id))
        )
    )
    await session.execute(
        delete(SDRDestination).where(
            SDRDestination.event_id.in_(select(SDREvent.id).where(SDREvent.sdr_id == sdr.id))
        )
    )
    await session.execute(delete(SDREvent).where(SDREvent.sdr_id == sdr.id))

    # Re-insert from parsed
    for event_data in parsed.events:
        event = SDREvent(
            sdr_id=sdr.id,
            name=event_data.name,
            purpose=event_data.purpose,
            trigger_type=event_data.trigger_type,
            trigger_config=event_data.trigger_config,
            status=event_data.status,
            owner_business=event_data.owner_business,
            owner_technical=event_data.owner_technical,
            consent_required=event_data.consent_required,
            kpi_links=event_data.kpi_links,
        )
        session.add(event)
        await session.flush()

        for param in event_data.parameters:
            session.add(
                SDRParameter(
                    event_id=event.id,
                    name=param.name,
                    type=param.type,
                    required=param.required,
                    source=param.source,
                    example=param.example,
                    validation_rule=param.validation_rule,
                )
            )

        for dest in event_data.destinations:
            session.add(
                SDRDestination(
                    event_id=event.id,
                    platform=dest.platform,
                    platform_account_id=dest.platform_account_id,
                    dest_event_name=dest.dest_event_name,
                    mapping=dest.mapping,
                )
            )

    sdr.parsed_at = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Markdown Generator — produce SDR markdown from structured data
# ---------------------------------------------------------------------------


def generate_sdr_markdown(
    *,
    project_name: str,
    project_id: str,
    business_type: str | None = None,
    events: list[ParsedEvent] | None = None,
    business_context: str | None = None,
    user_journeys: str | None = None,
    data_layer_schema: str | None = None,
    user_properties_md: str | None = None,
    destinations_matrix_md: str | None = None,
    consent_md: str | None = None,
    ownership_md: str | None = None,
) -> str:
    """
    Generate a full SDR markdown document from structured data.

    Used during bootstrap (generate_sdr) and when applying refinement changes.
    """
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    bt = business_type or "unknown"

    lines: list[str] = []

    # Frontmatter
    lines.append("---")
    lines.append(f"sdr_name: {project_name} Solution Design Reference")
    lines.append("sdr_version: 0.1-draft")
    lines.append("sdr_status: draft")
    lines.append(f"project_id: {project_id}")
    lines.append(f"business_type: {bt}")
    lines.append(f"last_updated: {now}")
    lines.append("last_approved_by: null")
    lines.append("last_approved_at: null")
    lines.append("---")
    lines.append("")
    lines.append(f"# {project_name} — Solution Design Reference")
    lines.append("")

    # Business Context
    lines.append("## Business Context")
    lines.append("")
    if business_context:
        lines.append(business_context)
    else:
        lines.append("[TODO: Describe the business model, primary KPIs, and conversion definitions]")
    lines.append("")
    lines.append("**Primary KPIs:**")
    lines.append("- [TODO: KPI 1]")
    lines.append("- [TODO: KPI 2]")
    lines.append("")
    lines.append("**Key conversions:**")
    lines.append("- [TODO: Conversion 1 — qualifying definition]")
    lines.append("")
    lines.append("---")
    lines.append("")

    # User Journeys
    lines.append("## User Journeys")
    lines.append("")
    if user_journeys:
        lines.append(user_journeys)
    else:
        lines.append("[TODO: Define 2-5 core user journeys with entry points and completion markers]")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Data Layer Schema
    lines.append("## Data Layer Schema")
    lines.append("")
    if data_layer_schema:
        lines.append(data_layer_schema)
    else:
        lines.append("[TODO: Document naming convention, standard shape, ecommerce shape]")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Event Catalog
    lines.append("## Event Catalog")
    lines.append("")

    if events:
        for event in events:
            lines.append(_generate_event_markdown(event))
    else:
        lines.append("[TODO: No events discovered yet]")

    lines.append("")
    lines.append("---")
    lines.append("")

    # User Properties
    lines.append("## User Properties / Custom Dimensions")
    lines.append("")
    if user_properties_md:
        lines.append(user_properties_md)
    else:
        lines.append("| Name | Scope | Source | Example | Platforms |")
        lines.append("|---|---|---|---|---|")
        lines.append("| [TODO] | | | | |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Destinations Matrix
    lines.append("## Destinations Matrix")
    lines.append("")
    if destinations_matrix_md:
        lines.append(destinations_matrix_md)
    else:
        lines.append(_generate_destinations_matrix(events or []))
    lines.append("")
    lines.append("---")
    lines.append("")

    # Consent
    lines.append("## Consent & Privacy")
    lines.append("")
    if consent_md:
        lines.append(consent_md)
    else:
        lines.append("[TODO: Document consent management platform, categories, gating rules]")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Ownership
    lines.append("## Ownership & Governance")
    lines.append("")
    if ownership_md:
        lines.append(ownership_md)
    else:
        lines.append("[TODO: Define business and technical owners per area]")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Changelog
    lines.append("## Changelog")
    lines.append("")
    lines.append(f"- **v0.1-draft** ({now[:10]}) — Initial bootstrap draft")
    lines.append("")

    return "\n".join(lines)


def _generate_event_markdown(event: ParsedEvent) -> str:
    """Generate markdown for a single event."""
    lines: list[str] = []
    status = event.status or "planned"
    lines.append(f"### `{event.name}`")
    lines.append("")
    lines.append(f"*Status:* `{status}` | *Last verified:* `never`")
    lines.append("")

    # Purpose
    lines.append(f"**Business Purpose:** {event.purpose or '[TODO: describe why this event matters]'}")
    lines.append("")

    # Triggers
    lines.append("**Triggers:**")
    trigger = event.trigger_type or "[TODO: specify]"
    lines.append(f"- Type: `{trigger}`")
    config = event.trigger_config or {}
    if config.get("configuration"):
        lines.append(f"- Configuration: {config['configuration']}")
    else:
        lines.append("- Configuration: [TODO: specify trigger configuration]")
    if config.get("conditions"):
        lines.append(f"- Conditions: {config['conditions']}")
    lines.append("")

    # Parameters table
    lines.append("**Parameters:**")
    lines.append("")
    lines.append("| Name | Type | Required | Source | Example | Validation |")
    lines.append("|---|---|---|---|---|---|")
    if event.parameters:
        for p in event.parameters:
            req = "yes" if p.required else "no"
            lines.append(
                f"| `{p.name}` | {p.type or '[TODO]'} | {req} "
                f"| {p.source or '[TODO]'} | `{p.example or '[TODO]'}` "
                f"| {p.validation_rule or ''} |"
            )
    else:
        lines.append("| [TODO] | | | | | |")
    lines.append("")

    # Destinations
    lines.append("**Destinations:**")
    lines.append("")
    if event.destinations:
        for d in event.destinations:
            acct = f" ({d.platform_account_id})" if d.platform_account_id else ""
            dest_name = f"event name `{d.dest_event_name}`" if d.dest_event_name else ""
            lines.append(f"- **{d.platform.upper()}**{acct}: {dest_name}")
    else:
        lines.append("- [TODO: specify destinations]")
    lines.append("")

    # Consent
    if event.consent_required:
        lines.append(f"**Consent Requirements:** `{'` | `'.join(event.consent_required)}`")
    else:
        lines.append("**Consent Requirements:** [TODO: specify consent categories]")
    lines.append("")

    # Owners
    biz = event.owner_business or "[TODO]"
    tech = event.owner_technical or "[TODO]"
    lines.append(f"**Owners:** Business: {biz} · Technical: {tech}")
    lines.append("")

    # KPIs
    if event.kpi_links:
        lines.append(f"**Related KPIs:** {', '.join(event.kpi_links)}")
    else:
        lines.append("**Related KPIs:** [TODO: link to relevant KPIs]")
    lines.append("")

    lines.append("**Edge Cases & Notes:** [TODO: user-provided clarifications]")
    lines.append("")

    return "\n".join(lines)


def _generate_destinations_matrix(events: list[ParsedEvent]) -> str:
    """Generate the cross-reference destinations matrix table."""
    if not events:
        return "| Event | GA4 | Google Ads | Meta | TikTok | LinkedIn | Custom |\n|---|---|---|---|---|---|---|\n| [TODO] | | | | | | |"

    lines = ["| Event | GA4 | Google Ads | Meta | TikTok | LinkedIn | Custom |"]
    lines.append("|---|---|---|---|---|---|---|")

    for event in events:
        dest_map: dict[str, str] = {}
        for d in event.destinations:
            mark = "✓"
            if d.dest_event_name:
                mark = f"✓ ({d.dest_event_name})"
            dest_map[d.platform] = mark

        row = f"| `{event.name}` "
        for platform in ["ga4", "google_ads", "meta", "tiktok", "linkedin", "custom"]:
            row += f"| {dest_map.get(platform, '—')} "
        row += "|"
        lines.append(row)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Gap computation
# ---------------------------------------------------------------------------


def compute_gaps(parsed: ParsedSDR) -> list[dict]:
    """
    Categorize [TODO] markers into structured gaps that drive the
    refinement roadmap.
    """
    gaps: list[dict] = []

    for todo in parsed.todo_markers:
        desc = todo["description"].lower()
        section = todo["section"]

        if "event" in desc or "fire" in desc:
            category = "missing_events"
        elif "parameter" in desc or "param" in desc:
            category = "incomplete_parameters"
        elif "consent" in desc:
            category = "no_consent_mgmt"
        elif "destination" in desc or "platform" in desc:
            category = "missing_destinations"
        elif "kpi" in desc or "conversion" in desc:
            category = "missing_business_context"
        elif "trigger" in desc or "configuration" in desc:
            category = "incomplete_triggers"
        elif "owner" in desc:
            category = "missing_ownership"
        else:
            category = "general"

        gaps.append(
            {
                "category": category,
                "description": todo["description"],
                "suggested_section": section,
            }
        )

    return gaps
