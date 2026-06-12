# app/services/tracking_plan/validation.py
"""validate_plan — a completeness/consistency report over a branch.

Built on plan_to_dict so it sees exactly what consumers see."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tracking_plan import TPBranch, TPPlan

from .serializer import plan_to_dict


def _finding(severity: str, code: str, message: str, entity: str | None = None) -> dict:
    return {"severity": severity, "code": code, "message": message, "entity": entity}


async def validate_plan(session: AsyncSession, plan: TPPlan, branch: TPBranch) -> dict:
    data = await plan_to_dict(session, plan, branch)
    findings: list[dict] = []

    used_event_props: set[str] = set()
    for event in data["events"]:
        name = event["name"]
        if not event["sources"]:
            findings.append(
                _finding("warning", "event_no_source", f"Event '{name}' is not scoped to any source", name)
            )
        if not event["destinations"]:
            findings.append(
                _finding(
                    "warning", "event_no_destination", f"Event '{name}' is mapped to no destination", name
                )
            )
        if not event["properties"]:
            findings.append(
                _finding("info", "event_no_properties", f"Event '{name}' has no properties", name)
            )
        for prop in event["properties"]:
            used_event_props.add(prop["name"])
            if prop["required"] and not prop["example"]:
                findings.append(
                    _finding(
                        "info",
                        "required_property_no_example",
                        f"Required property '{prop['name']}' on '{name}' has no example",
                        name,
                    )
                )

    for prop in data["properties"]["event"]:
        if prop["name"] not in used_event_props:
            findings.append(
                _finding(
                    "info",
                    "unused_property",
                    f"Event property '{prop['name']}' is attached to no event",
                    prop["name"],
                )
            )

    return {
        "findings": findings,
        "counts": {
            "events": len(data["events"]),
            "event_properties": len(data["properties"]["event"]),
            "user_properties": len(data["properties"]["user"]),
            "sources": len(data["sources"]),
            "destinations": len(data["destinations"]),
            "metrics": len(data["metrics"]),
        },
        "is_publishable": not any(f["severity"] == "warning" for f in findings),
    }
