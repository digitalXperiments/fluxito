"""Hosted web dashboard artifact contract and validation.

Fluxito hosts a model-authored production frontend (HTML/JS/CSS). It does
not compile JSX and does not run Streamlit. Validation is the gate before
any file is written to disk.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urlsplit

# v2 makes the uploaded file inventory explicit. A manifest must list exactly
# the paths present in the outer `files` object, including optional unreferenced
# lazy chunks that a static parser cannot discover.
ARTIFACT_SCHEMA_VERSION = 2
ARTIFACT_KIND = "web"

MAX_FILES = 80
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 8_000_000
MAX_PATH_DEPTH = 8

ALLOWED_SUFFIXES = frozenset({".html", ".js", ".css", ".svg", ".json", ".txt", ".md", ".map"})
SOURCE_ONLY_SUFFIXES = frozenset({".jsx", ".tsx", ".ts", ".py", ".vue", ".svelte"})
FORBIDDEN_FILENAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "service-account.json",
        "service_account.json",
        "secrets.toml",
        "id_rsa",
        "id_ed25519",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
    }
)
FORBIDDEN_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".crt", ".der"})
FORBIDDEN_DIR_NAMES = frozenset({"node_modules", ".git", ".streamlit"})

CONNECTION_TYPES = frozenset(
    {
        "ga4",
        "bigquery",
        "redshift",
        "snowflake",
        "meta_ads",
        "tiktok_ads",
        "snap_ads",
        "apple_ads",
        "google_ads",
        "amplitude",
        "mixpanel",
        "posthog",
        "adobe_analytics",
        "search_console",
        "gtm",
        "adobe_launch",
        "adobe_marketo",
        "linkedin_ads",
        "pinterest_ads",
        "reddit_ads",
        "x_ads",
        "bing_webmaster",
        "branch",
        "appsflyer",
        "adjust",
        "braze",
        "moengage",
    }
)

CONNECTION_TOOL: dict[str, str] = {
    "ga4": "analytics_read",
    "amplitude": "analytics_read",
    "mixpanel": "analytics_read",
    "posthog": "analytics_read",
    "adobe_analytics": "analytics_read",
    "bigquery": "warehouse_query",
    "redshift": "warehouse_query",
    "snowflake": "warehouse_query",
    "meta_ads": "marketing_read",
    "tiktok_ads": "marketing_read",
    "snap_ads": "marketing_read",
    "apple_ads": "marketing_read",
    "google_ads": "marketing_read",
    "linkedin_ads": "marketing_read",
    "pinterest_ads": "marketing_read",
    "reddit_ads": "marketing_read",
    "x_ads": "marketing_read",
    "search_console": "seo_read",
    "bing_webmaster": "seo_read",
    "gtm": "tagmanager_read",
    "adobe_launch": "tagmanager_read",
    "adobe_marketo": "marketing_read",
    "branch": "analytics_read",
    "appsflyer": "analytics_read",
    "adjust": "analytics_read",
    "braze": "analytics_read",
    "moengage": "analytics_read",
}

_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_REMOTE_SCRIPT_RE = re.compile(
    r"""<script[^>]+src\s*=\s*['"](?:https?:)?//""",
    re.IGNORECASE,
)
_QUERY_CALL_RE = re.compile(r"fluxito\s*\.\s*query\s*\(")

# A hosted dashboard is a static build. A missing script or stylesheet should
# fail validation instead of producing a page that looks present but has blank
# charts or unstyled sections. These are deliberately limited to browser
# resource references; normal links to SPA routes are not treated as assets.
_HTML_RESOURCE_ATTRS: dict[str, tuple[str, ...]] = {
    "audio": ("src",),
    "embed": ("src",),
    "iframe": ("src",),
    "img": ("src", "srcset"),
    "input": ("src",),
    "link": ("href",),
    "object": ("data",),
    "script": ("src",),
    "source": ("src", "srcset"),
    "track": ("src",),
    "video": ("poster", "src"),
}
_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_JS_IMPORT_RE = re.compile(
    r"\bimport\s*(?:[^\"'()]*?\s+from\s+)?[\(\s]*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_JS_NEW_URL_RE = re.compile(
    r"\bnew\s+URL\(\s*['\"]([^'\"]+)['\"]\s*,\s*import\.meta\.url\s*\)",
    re.IGNORECASE,
)


class _HtmlAssetParser(HTMLParser):
    """Collect resource-bearing HTML attributes without interpreting links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str, str]] = []

    def _collect(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        wanted = _HTML_RESOURCE_ATTRS.get(tag.lower(), ())
        if not wanted:
            return
        for name, value in attrs:
            if name.lower() in wanted and value:
                self.references.append((tag.lower(), name.lower(), value))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._collect(tag, attrs)


def _is_non_file_reference(value: str) -> bool:
    """Whether a browser URL is not a file that belongs to the artifact."""
    value = value.strip()
    if not value or value.startswith("#") or value == "/fluxito.js":
        return True
    parsed = urlsplit(value)
    return bool(parsed.scheme or value.startswith("//"))


def _asset_path(referencing_file: str, value: str) -> str | None:
    """Resolve a relative/absolute artifact URL to its normalized file path."""
    value = value.strip()
    if _is_non_file_reference(value):
        return None
    parsed = urlsplit(value)
    raw_path = parsed.path
    if not raw_path:
        return None
    if raw_path.startswith("/"):
        return posixpath.normpath(raw_path.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(referencing_file), raw_path))


def _srcset_values(value: str) -> list[str]:
    """Return the URL part of each srcset candidate."""
    values: list[str] = []
    for candidate in value.split(","):
        url = candidate.strip().split(None, 1)[0] if candidate.strip() else ""
        if url:
            values.append(url)
    return values


def _local_asset_references(path: str, content: str) -> list[str]:
    """Find local browser assets referenced by HTML/CSS/JS source."""
    suffix = PurePosixPath(path).suffix.lower()
    values: list[str] = []
    if suffix == ".html":
        parser = _HtmlAssetParser()
        try:
            parser.feed(content)
        except Exception:
            # HTMLParser is intentionally best-effort. The HTML itself is
            # still served, but malformed markup must not crash validation.
            return values
        for _tag, attr, value in parser.references:
            values.extend(_srcset_values(value) if attr == "srcset" else [value])
    elif suffix == ".css":
        values.extend(match.group(2).strip() for match in _CSS_URL_RE.finditer(content))
    elif suffix == ".js":
        values.extend(match.group(1).strip() for match in _JS_IMPORT_RE.finditer(content))
        values.extend(match.group(1).strip() for match in _JS_NEW_URL_RE.finditer(content))
    return [value for value in values if value]


def _check_local_asset_references(file_map: dict[str, str]) -> list[str]:
    """Reject builds that reference a local file that was not sent."""
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for path, content in file_map.items():
        suffix = PurePosixPath(path).suffix.lower()
        for value in _local_asset_references(path, content):
            # Bare JS imports are package names. A production build should
            # have bundled them, but they are not artifact-relative paths.
            if suffix == ".js" and not value.startswith((".", "/")):
                continue
            target = _asset_path(path, value)
            if target is None or target in file_map:
                continue
            key = (path, value)
            if key in seen:
                continue
            seen.add(key)
            errors.append(
                f"{path}: local asset {value!r} is referenced but missing from files. "
                "Send the complete production build, including every supported JS/CSS/SVG asset."
            )
    return errors


_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pem_private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    (
        "assignment_secret",
        re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"password|passwd|private[_-]?key|fernet[_-]?key|encryption[_-]?key|"
            r"client[_-]?secret|auth[_-]?token)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]"
        ),
    ),
    (
        "connection_string",
        re.compile(r"(?i)\b(postgres(?:ql)?|mysql|mongodb|redis|amqp)://[^\s'\"]+:[^\s'\"]+@"),
    ),
    ("fernet_literal", re.compile(r"\bTOKEN_ENCRYPTION_KEY\b\s*[:=]")),
    ("database_url_secret", re.compile(r"\bDATABASE_URL\b\s*[:=]\s*['\"][^'\"]+['\"]")),
]

_RETIRED_DASHBOARD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "retired_tool",
        re.compile(r"\bdashboard_(?:deploy_batch|create|card_upsert|card_preview|card_remove)\b"),
    ),
    ("streamlit", re.compile(r"\b(?:import streamlit|from streamlit|streamlit as st)\b")),
    ("fluxito_data", re.compile(r"\bfluxito_data\b")),
]


class ArtifactError(ValueError):
    """Structured validation failure. ``errors`` is a list of messages."""

    def __init__(self, errors: list[str]):
        self.errors = [str(e) for e in errors if str(e).strip()]
        super().__init__(self.format())

    def format(self) -> str:
        if len(self.errors) == 1:
            return self.errors[0]
        return "Artifact validation failed:\n  - " + "\n  - ".join(self.errors)


@dataclass
class ConnectionRequirement:
    alias: str
    type: str
    required: bool = True


@dataclass
class ArtifactManifest:
    schema_version: int
    title: str
    entrypoint: str
    artifact_files: list[str]
    connections: list[ConnectionRequirement] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "kind": ARTIFACT_KIND,
            "title": self.title,
            "entrypoint": self.entrypoint,
            "artifact_files": list(self.artifact_files),
            "connections": [
                {"alias": c.alias, "type": c.type, "required": c.required} for c in self.connections
            ],
        }


@dataclass
class ValidatedArtifact:
    manifest: ArtifactManifest
    files: dict[str, str]
    digest: str
    warnings: list[str] = field(default_factory=list)

    @property
    def entrypoint_source(self) -> str:
        return self.files[self.manifest.entrypoint]


def normalize_files(files: dict | list | None) -> dict[str, str]:
    """Accept ``{path: content}`` or ``[{path, content}, ...]`` and return a dict."""
    if files is None:
        raise ArtifactError(["files is required — pass the production build as path → source."])
    out: dict[str, str] = {}
    if isinstance(files, dict):
        items = files.items()
    elif isinstance(files, list):
        items = []
        for i, entry in enumerate(files):
            if not isinstance(entry, dict):
                raise ArtifactError([f"files[{i}] must be an object with path and content"])
            items.append((entry.get("path"), entry.get("content")))
    else:
        raise ArtifactError(["files must be an object of path → content or a list of {path, content}"])

    for path, content in items:
        if not isinstance(path, str) or not path.strip():
            raise ArtifactError(["every file path must be a non-empty string"])
        if content is None:
            content = ""
        if not isinstance(content, str):
            raise ArtifactError([f"{path}: file content must be a UTF-8 string, not binary"])
        out[path.strip().lstrip("/")] = content
    return out


def _check_path(path: str) -> list[str]:
    errors: list[str] = []
    if path.startswith("/") or path.startswith("\\"):
        errors.append(f"{path}: absolute paths are not allowed")
        return errors
    posix = PurePosixPath(path)
    if ".." in posix.parts:
        errors.append(f"{path}: path traversal ('..') is not allowed")
    if any(part in FORBIDDEN_DIR_NAMES for part in posix.parts):
        errors.append(f"{path}: {posix.parts} includes a forbidden directory")
    if any(part.startswith(".") for part in posix.parts[:-1]):
        errors.append(f"{path}: hidden directories are not allowed")
    if len(posix.parts) > MAX_PATH_DEPTH:
        errors.append(f"{path}: too many path segments (max {MAX_PATH_DEPTH})")
    name = posix.name.lower()
    if name in FORBIDDEN_FILENAMES or name.startswith(".env"):
        errors.append(f"{path}: credential / env / lock files are forbidden")
    suffix = posix.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        errors.append(f"{path}: certificate / key files are forbidden")
    if suffix in SOURCE_ONLY_SUFFIXES:
        errors.append(
            f"{path}: Fluxito does not compile {suffix}. Send the production build "
            "(index.html + hashed .js/.css). Set Vite base: './'."
        )
    elif suffix not in ALLOWED_SUFFIXES:
        errors.append(
            f"{path}: file type {suffix or '(none)'} is not allowed. "
            f"Use one of: {', '.join(sorted(ALLOWED_SUFFIXES))}"
        )
    return errors


def _scan_secrets(path: str, content: str) -> list[str]:
    errors: list[str] = []
    if PurePosixPath(path).name.lower().startswith(".env"):
        errors.append(f"{path}: .env files are forbidden")
        return errors
    for label, pat in _SECRET_PATTERNS:
        if pat.search(content):
            errors.append(
                f"{path}: looks like a secret ({label}). Do not put credentials in the artifact. "
                "Declare a connection alias in the manifest and call fluxito.query(alias, ...)."
            )
    return errors


def _scan_retired_dashboard_shape(path: str, content: str) -> list[str]:
    errors: list[str] = []
    for label, pat in _RETIRED_DASHBOARD_PATTERNS:
        if pat.search(content):
            errors.append(
                f"{path}: looks like a retired dashboard shape ({label}). "
                "Write a production HTML/JS/CSS app that calls fluxito.query. "
                "Do not emit Streamlit, card JSON, chart_type, or ECharts specs."
            )
    return errors


def _scan_remote_scripts(path: str, content: str) -> list[str]:
    if not path.endswith(".html") and not path.endswith(".js"):
        return []
    if _REMOTE_SCRIPT_RE.search(content):
        return [f"{path}: remote <script src> is not allowed. Bundle every script in the artifact."]
    return []


def parse_manifest(raw: dict | str | None, *, fallback_title: str | None = None) -> ArtifactManifest:
    if raw is None:
        raise ArtifactError(
            [
                "manifest is required. Include manifest.json in files or pass a manifest object. "
                "Call get_dashboard_authoring_guide first."
            ]
        )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArtifactError([f"manifest.json is not valid JSON: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise ArtifactError(["manifest must be a JSON object"])

    errors: list[str] = []
    kind = str(raw.get("kind") or ARTIFACT_KIND).strip().lower()
    if kind in {"streamlit", "python"}:
        errors.append(
            "manifest.kind 'streamlit' is not supported. Fluxito hosts a production "
            "HTML/JS/CSS build (kind=web). Call get_dashboard_authoring_guide."
        )
    elif kind not in {ARTIFACT_KIND, "hosted", ""}:
        errors.append(f"manifest.kind must be {ARTIFACT_KIND!r} (got {kind!r})")

    version = raw.get("schema_version", ARTIFACT_SCHEMA_VERSION)
    try:
        version = int(version)
    except (TypeError, ValueError):
        errors.append("manifest.schema_version must be an integer")
        version = ARTIFACT_SCHEMA_VERSION
    if version != ARTIFACT_SCHEMA_VERSION:
        errors.append(f"manifest.schema_version must be {ARTIFACT_SCHEMA_VERSION} (got {version})")

    title = raw.get("title") or fallback_title or ""
    if not isinstance(title, str) or not title.strip():
        errors.append("manifest.title must be a non-empty string")
    title = str(title).strip()[:120]

    entrypoint = raw.get("entrypoint") or "index.html"
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        errors.append("manifest.entrypoint must be a non-empty string")
        entrypoint = "index.html"
    entrypoint = entrypoint.strip().lstrip("/")
    if not entrypoint.endswith(".html"):
        errors.append(f"manifest.entrypoint must be an .html file (got {entrypoint!r})")

    artifact_files_raw = raw.get("artifact_files")
    artifact_files: list[str] = []
    if artifact_files_raw is None:
        errors.append(
            "manifest.artifact_files is required — list every uploaded path explicitly, "
            "including manifest.json and lazy-loaded chunks."
        )
    elif not isinstance(artifact_files_raw, list):
        errors.append("manifest.artifact_files must be a list of uploaded paths")
    else:
        seen_files: set[str] = set()
        for i, item in enumerate(artifact_files_raw):
            if not isinstance(item, str) or not item.strip():
                errors.append(f"manifest.artifact_files[{i}] must be a non-empty path string")
                continue
            path = item.strip()
            if path != item:
                errors.append(f"manifest.artifact_files[{i}] must not have surrounding whitespace")
            if path in seen_files:
                errors.append(f"manifest.artifact_files[{i}] duplicates {path!r}")
                continue
            seen_files.add(path)
            artifact_files.append(path)

    conns_raw = raw.get("connections")
    if conns_raw is None:
        errors.append(
            "manifest.connections is required — list every live data source as "
            '{"alias": "ga4", "type": "ga4"}. Secrets must never be inlined.'
        )
        conns_raw = []
    if not isinstance(conns_raw, list):
        errors.append("manifest.connections must be a list")
        conns_raw = []

    connections: list[ConnectionRequirement] = []
    seen_aliases: set[str] = set()
    for i, item in enumerate(conns_raw):
        if not isinstance(item, dict):
            errors.append(f"manifest.connections[{i}] must be an object")
            continue
        alias = str(item.get("alias") or "").strip()
        ctype = str(item.get("type") or "").strip()
        required = item.get("required", True)
        if not isinstance(required, bool):
            required = str(required).strip().lower() not in ("0", "false", "no")
        if not _ALIAS_RE.match(alias):
            errors.append(
                f"manifest.connections[{i}].alias must be snake_case starting with a letter "
                f"(got {alias!r})"
            )
        elif alias in seen_aliases:
            errors.append(f"manifest.connections[{i}].alias {alias!r} is duplicated")
        if ctype not in CONNECTION_TYPES:
            errors.append(
                f"manifest.connections[{i}].type {ctype!r} is not a bindable Fluxito connection. "
                f"Known types: {', '.join(sorted(CONNECTION_TYPES))}"
            )
        seen_aliases.add(alias)
        if alias and ctype in CONNECTION_TYPES:
            connections.append(ConnectionRequirement(alias=alias, type=ctype, required=bool(required)))

    if errors:
        raise ArtifactError(errors)
    return ArtifactManifest(
        schema_version=version,
        title=title,
        entrypoint=entrypoint,
        artifact_files=sorted(artifact_files),
        connections=connections,
    )


def validate_artifact(
    files: dict | list | None,
    manifest: dict | str | None = None,
    *,
    fallback_title: str | None = None,
) -> ValidatedArtifact:
    """Validate a model-authored web artifact.

    Raises ``ArtifactError`` with every problem aggregated so the model can
    fix them in one retry. Never writes to disk.
    """
    file_map = normalize_files(files)
    errors: list[str] = []
    warnings: list[str] = []

    if not file_map:
        raise ArtifactError(["files must contain at least the HTML entrypoint (index.html)"])
    if len(file_map) > MAX_FILES:
        errors.append(f"too many files ({len(file_map)}). Maximum is {MAX_FILES}.")

    total = 0
    for path, content in file_map.items():
        errors.extend(_check_path(path))
        size = len(content.encode("utf-8", errors="replace"))
        total += size
        if size > MAX_FILE_BYTES:
            errors.append(f"{path}: file is {size} bytes (max {MAX_FILE_BYTES})")
        errors.extend(_scan_secrets(path, content))
        errors.extend(_scan_remote_scripts(path, content))
    errors.extend(_check_local_asset_references(file_map))
    if total > MAX_TOTAL_BYTES:
        errors.append(f"artifact is {total} bytes (max {MAX_TOTAL_BYTES})")

    if manifest is None and "manifest.json" in file_map:
        manifest = file_map["manifest.json"]
    try:
        parsed = parse_manifest(manifest, fallback_title=fallback_title)
    except ArtifactError as exc:
        errors.extend(exc.errors)
        parsed = None

    if parsed is not None:
        actual_files = set(file_map)
        declared_files = set(parsed.artifact_files)
        missing_from_manifest = sorted(actual_files - declared_files)
        not_uploaded = sorted(declared_files - actual_files)
        if missing_from_manifest:
            errors.append(
                "manifest.artifact_files must explicitly list every uploaded file. "
                f"Missing from manifest: {', '.join(missing_from_manifest)}"
            )
        if not_uploaded:
            errors.append(
                "manifest.artifact_files declares files that were not uploaded: " f"{', '.join(not_uploaded)}"
            )
        if parsed.entrypoint not in file_map:
            errors.append(
                f"entrypoint {parsed.entrypoint!r} is not in files. "
                "Send the production index.html from your build."
            )
        else:
            src = file_map[parsed.entrypoint]
            errors.extend(_scan_retired_dashboard_shape(parsed.entrypoint, src))
            if 'src="/assets/' in src or "src='/assets/" in src:
                warnings.append(
                    f"{parsed.entrypoint}: absolute /assets/ URLs break on the dash host. "
                    "Set Vite `base: './'` (relative asset URLs)."
                )
        blob = "\n".join(file_map.values())
        if parsed.connections and not _QUERY_CALL_RE.search(blob):
            warnings.append(
                "No fluxito.query(...) call found. Live data only works through "
                "fluxito.query(alias, action, params). The host injects /fluxito.js."
            )
        for path, content in file_map.items():
            if path == parsed.entrypoint:
                continue
            if path.endswith((".js", ".html")):
                errors.extend(_scan_retired_dashboard_shape(path, content))

    if errors:
        raise ArtifactError(errors)

    assert parsed is not None
    digest = hashlib.sha256(
        json.dumps(
            {"manifest": parsed.to_dict(), "files": file_map},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return ValidatedArtifact(manifest=parsed, files=file_map, digest=digest, warnings=warnings)
