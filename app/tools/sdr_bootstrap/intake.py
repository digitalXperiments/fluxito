"""Versioned SDR intake questions and helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

INTAKE_VERSION = "v1"

INTAKE_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "business_model",
        "question": "In one crisp sentence, what does the business sell, to whom, and how does it make money?",
        "why_it_matters": "Primary driver of template selection, KPI prioritization, and business context.",
        "example_good_answer": "B2B SaaS that sells compliance automation to mid-market finance teams on a freemium + usage-based model.",
        "bad_answer": "We sell software.",
        "optional": False,
    },
    {
        "key": "primary_kpis",
        "question": "What are the top 2-3 KPIs the entire analytics program is accountable for moving?",
        "why_it_matters": "Seeds Primary KPIs in Business Context and drives gap prioritization.",
        "example_good_answer": "Demo to paid conversion rate, time-to-value for new accounts, and feature adoption depth.",
        "bad_answer": "Revenue, users, engagement.",
        "optional": False,
    },
    {
        "key": "conversion_definition",
        "question": (
            "Concretely, what single user action (or small set) counts as a 'conversion' for your business? "
            "Be extremely specific - 'form submit with verified email + company size > 50' beats 'lead'."
        ),
        "why_it_matters": "Highest-leverage signal for event catalog prioritization and destination mapping.",
        "example_good_answer": "A qualified demo request with corporate email, known company size > 20, and Enterprise or Pro plan interest.",
        "bad_answer": "Any form submission or signup.",
        "optional": False,
    },
    {
        "key": "key_journeys",
        "question": (
            "Name the 2-4 most important user journeys or flows you need the event catalog to make visible end-to-end. "
            "For each, give the rough entry point and the completion signal."
        ),
        "why_it_matters": "Feeds User Journeys and helps cluster events and spot missing middle-funnel events.",
        "example_good_answer": "Free trial -> feature activation -> paid; content view -> demo request.",
        "bad_answer": "Awareness to purchase.",
        "optional": False,
    },
    {
        "key": "privacy_consent",
        "question": (
            "What is your current privacy / consent / regulatory posture? CMP in use? Any sensitive categories "
            "(health, finance, children)? Consent gating on tags? Data residency requirements?"
        ),
        "why_it_matters": "Populates Consent & Privacy and influences client-side versus server-side tagging strategy.",
        "example_good_answer": "We use OneTrust. Marketing tags are consent-gated behind analytics and advertising categories. GDPR + CCPA users.",
        "bad_answer": "We have a cookie banner.",
        "optional": False,
    },
    {
        "key": "ownership_complexity",
        "question": (
            "Who owns events vs destinations vs the overall taxonomy? Any non-obvious complexity "
            "(multi-brand, marketplace two-sided, heavy server-side tagging, warehouse events as source of truth, "
            "B2B vs B2C differences, regional variations)?"
        ),
        "why_it_matters": "Populates Ownership & Governance and explains implementation asymmetry.",
        "example_good_answer": "Analytics engineering owns taxonomy, marketing ops owns destination mappings, and Cloud Run server-side tagging fires revenue events.",
        "bad_answer": "Marketing owns it.",
        "optional": False,
    },
    {
        "key": "anything_else",
        "question": (
            "Is there anything else about how this business or your implementation actually works that a smart analytics "
            "person would be surprised by after looking at your tags?"
        ),
        "why_it_matters": "Catches domain-specific surprises before synthesis.",
        "example_good_answer": "Two brands share one GA4 property but use different conversion definitions.",
        "bad_answer": "",
        "optional": True,
    },
)


def get_intake_questions() -> list[dict[str, Any]]:
    return [dict(q) for q in INTAKE_QUESTIONS]


def build_intake_snapshot(answers: dict[str, str] | None) -> dict[str, Any]:
    cleaned = {str(k): str(v).strip() for k, v in (answers or {}).items() if str(v).strip()}
    return {
        "intake_version": INTAKE_VERSION,
        "answered_at": datetime.now(UTC).isoformat(),
        "answers": cleaned,
    }


def missing_required_answers(answers: dict[str, str] | None) -> list[str]:
    values = answers or {}
    return [q["key"] for q in INTAKE_QUESTIONS if not q["optional"] and not str(values.get(q["key"], "")).strip()]


def intake_interview_instructions(project_name: str) -> str:
    return (
        f'Before scanning "{project_name}", ask the SDR intake conversationally. '
        "Do not dump the questions as a form; ask one or two at a time. "
        "The six required answers become durable context for generation, refreshes, and auditability. "
        "Once all required answers are captured, call tracking_plan(action='generate', "
        "params={'intake_answers': {...}})."
    )
