#!/usr/bin/env python3
"""CLI utility to query live vendor platforms for the latest AI models.

Usage:
    python -m app.ask.cli_refresh_models
    python -m app.ask.cli_refresh_models --provider anthropic
    python -m app.ask.cli_refresh_models --json
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.ask.public_catalog_fetcher import (
    ScrapedModel,
    fetch_all_public_vendor_models,
    fetch_public_models_for_provider,
)


def _format_model_row(m: ScrapedModel) -> str:
    ctx_str = f"{m.context_window:,}" if m.context_window else "—"
    caps_str = ", ".join(m.capabilities) if m.capabilities else "—"
    name = m.display_name or m.model_id
    return f"  • {m.model_id:<32} {name:<26} {ctx_str:<10} [{caps_str}]"


async def _run(provider: str | None, as_json: bool) -> None:
    if provider:
        models = await fetch_public_models_for_provider(provider)
        data = {provider: models}
    else:
        data = await fetch_all_public_vendor_models()

    if as_json:
        serializable = {
            p: [
                {
                    "model_id": m.model_id,
                    "display_name": m.display_name,
                    "context_window": m.context_window,
                    "capabilities": m.capabilities,
                    "description": m.description,
                }
                for m in models
            ]
            for p, models in data.items()
        }
        print(json.dumps(serializable, indent=2))
        return

    print("=" * 80)
    print("  FLUXITO AI MODEL CATALOG REFRESH (LIVE PLATFORM VENDORS)")
    print("=" * 80)

    total_models = sum(len(m_list) for m_list in data.values())
    for prov, models in data.items():
        print(f"\n[{prov.upper()}] ({len(models)} models found):")
        print(f"  {'Model ID':<34} {'Display Name':<26} {'Context':<10} Capabilities")
        print("  " + "-" * 76)
        for m in models[:12]:  # Show top 12 per vendor
            print(_format_model_row(m))
        if len(models) > 12:
            print(f"  ... and {len(models) - 12} more models.")

    print("\n" + "=" * 80)
    print(f"Total models discovered across {len(data)} providers: {total_models}")
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch latest AI models from vendor platforms.")
    parser.add_argument(
        "--provider", "-p", help="Filter by specific provider (anthropic, openai, gemini, etc.)"
    )
    parser.add_argument("--json", "-j", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    asyncio.run(_run(args.provider, args.json))


if __name__ == "__main__":
    main()
