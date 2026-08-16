"""Working directory for hosted web dashboards.

No child process. The host writes the validated artifact plus an injected
``fluxito.js`` SDK. The dash origin serves those files statically.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.dashboards.artifact import ValidatedArtifact
from app.dashboards.origin import app_origin

logger = logging.getLogger(__name__)

SDK_NAME = "fluxito.js"
RUNTIME_STATE_NAME = ".fluxito_runtime.json"
_BLOCKED_SERVE_NAMES = frozenset(
    {
        RUNTIME_STATE_NAME,
        "manifest.json",
        ".env",
        ".env.local",
        ".env.production",
    }
)


def dashboards_root() -> Path:
    raw = (settings.DASHBOARDS_LOCAL_DIR or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".fluxito" / "dashboards"


def workdir_for(user_id: UUID | str, dashboard_id: UUID | str) -> Path:
    return dashboards_root() / str(user_id) / str(dashboard_id)


def _sdk_source(parent_origin: str) -> str:
    helper = Path(__file__).with_name(SDK_NAME)
    text = helper.read_text(encoding="utf-8")
    return text.replace("__FLUXITO_PARENT_ORIGIN__", parent_origin)


def inject_sdk_tag(html: str) -> str:
    """Ensure index.html loads the host SDK from the dash origin root."""
    if "/fluxito.js" in html:
        return html
    tag = '<script src="/fluxito.js"></script>'
    lower = html.lower()
    idx = lower.rfind("</body>")
    if idx >= 0:
        return html[:idx] + tag + "\n" + html[idx:]
    idx = lower.rfind("</head>")
    if idx >= 0:
        return html[:idx] + tag + "\n" + html[idx:]
    return html + "\n" + tag + "\n"


def rewrite_absolute_assets(html: str, slug: str) -> str:
    prefix = f"/s/{slug}"
    return html.replace('src="/assets/', f'src="{prefix}/assets/').replace(
        'href="/assets/', f'href="{prefix}/assets/'
    )


def write_artifact(
    workdir: Path,
    artifact: ValidatedArtifact,
    *,
    bindings: list[dict],
    dashboard_id: str,
    slug: str,
    **_ignored: object,
) -> None:
    """Replace the working directory with the validated artifact + SDK."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    entry = artifact.manifest.entrypoint
    for rel, content in artifact.files.items():
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if rel == entry and rel.endswith(".html"):
            content = inject_sdk_tag(content)
        dest.write_text(content, encoding="utf-8")

    ensure_house_files(workdir)

    (workdir / RUNTIME_STATE_NAME).write_text(
        json.dumps(
            {
                "dashboard_id": dashboard_id,
                "slug": slug,
                "entrypoint": entry,
                "bindings": [
                    {"alias": b.get("alias"), "type": b.get("type"), "status": b.get("status")}
                    for b in bindings
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def ensure_house_files(workdir: Path) -> None:
    """Refresh the injected SDK without wiping the artifact."""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / SDK_NAME).write_text(_sdk_source(app_origin()), encoding="utf-8")


def resolve_artifact_path(workdir: Path, rel: str) -> Path | None:
    """Return a file inside workdir, or None if the path is unsafe / blocked."""
    raw = (rel or "").lstrip("/")
    if not raw or raw.endswith("/"):
        return None
    posix = Path(raw)
    if ".." in posix.parts or posix.is_absolute():
        return None
    if posix.name.startswith("."):
        return None
    if posix.name in _BLOCKED_SERVE_NAMES:
        return None
    dest = (workdir / posix).resolve()
    try:
        dest.relative_to(workdir.resolve())
    except ValueError:
        return None
    if not dest.is_file():
        return None
    return dest


def delete_workdir(user_id: UUID | str, dashboard_id: UUID | str) -> None:
    path = workdir_for(user_id, dashboard_id)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
