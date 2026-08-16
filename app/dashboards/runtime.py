"""Supervised Streamlit host for model-authored dashboards.

Each dashboard gets a dedicated working directory and a child process bound
to 127.0.0.1. The child environment is a whitelist — Fluxito process secrets
(DATABASE_URL, TOKEN_ENCRYPTION_KEY, OAuth tokens) are never inherited.
Connection material is injected only as a data-plane URL + runtime token.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.dashboards.artifact import ValidatedArtifact

logger = logging.getLogger(__name__)

HOST_PORT_MIN = 14100
HOST_PORT_MAX = 14599
START_TIMEOUT_S = 25
# Virtual address space, not RSS. Streamlit + pandas comfortably exceed 512MiB
# of VAS; a tight RLIMIT_AS makes `python -m streamlit` die with code 1.
MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
NOFILE_LIMIT = 1024
HOST_STATE_NAME = ".fluxito_host.json"
HOST_LOG_NAME = ".fluxito_host.log"

# Env keys that must never reach an untrusted Streamlit process.
_BLOCKED_ENV = frozenset(
    {
        "DATABASE_URL",
        "TOKEN_ENCRYPTION_KEY",
        "APP_SECRET_KEY",
        "REDIS_URL",
        "SMTP_PASSWORD",
        "SMTP_USERNAME",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_CLIENT_ID",
        "GCS_SERVICE_ACCOUNT_JSON",
        "SENTRY_DSN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "FERNET_KEY",
    }
)

ProcessFactory = Callable[..., subprocess.Popen]


@dataclass
class HostedProcess:
    dashboard_id: str
    slug: str
    port: int
    pid: int
    workdir: Path
    proc: subprocess.Popen
    started_at: float = field(default_factory=time.time)

    def is_alive(self) -> bool:
        return self.proc.poll() is None


_processes: dict[str, HostedProcess] = {}
_process_factory: ProcessFactory | None = None


def set_process_factory(factory: ProcessFactory | None) -> None:
    """Tests inject a dummy HTTP server here so CI does not need Streamlit."""
    global _process_factory
    _process_factory = factory


def dashboards_root() -> Path:
    raw = (settings.DASHBOARDS_LOCAL_DIR or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".fluxito" / "dashboards"


def workdir_for(user_id: UUID | str, dashboard_id: UUID | str) -> Path:
    return dashboards_root() / str(user_id) / str(dashboard_id)


def _helper_source() -> str:
    helper = Path(__file__).with_name("fluxito_data.py")
    return helper.read_text(encoding="utf-8")


def write_artifact(
    workdir: Path,
    artifact: ValidatedArtifact,
    *,
    bindings: list[dict],
    data_url: str,
    runtime_token: str,
    dashboard_id: str,
    slug: str,
) -> None:
    """Replace the working directory with the validated artifact + helper."""
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    for rel, content in artifact.files.items():
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    (workdir / "fluxito_data.py").write_text(_helper_source(), encoding="utf-8")

    streamlit_dir = workdir / ".streamlit"
    streamlit_dir.mkdir(exist_ok=True)
    config_path = streamlit_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(
            "[server]\nheadless = true\nenableCORS = false\nenableXsrfProtection = false\n"
            "gatherUsageStats = false\n",
            encoding="utf-8",
        )

    # Runtime contract for operators / debugging — no secrets.
    (workdir / ".fluxito_runtime.json").write_text(
        __import__("json").dumps(
            {
                "dashboard_id": dashboard_id,
                "slug": slug,
                "data_url": data_url,
                "bindings": [
                    {"alias": b.get("alias"), "type": b.get("type"), "status": b.get("status")}
                    for b in bindings
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def build_child_env(
    *,
    workdir: Path,
    data_url: str,
    runtime_token: str,
    dashboard_id: str,
    bindings: list[dict],
    port: int,
    base_path: str,
) -> dict[str, str]:
    import json

    path = os.environ.get("PATH", "/usr/bin:/bin")
    env = {
        "PATH": path,
        "HOME": str(workdir),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(workdir),
        "FLUXITO_DATA_URL": data_url,
        "FLUXITO_RUNTIME_TOKEN": runtime_token,
        "FLUXITO_DASHBOARD_ID": dashboard_id,
        "FLUXITO_CONNECTION_ALIASES": json.dumps(
            [
                {
                    "alias": b.get("alias"),
                    "type": b.get("type"),
                    "status": b.get("status"),
                    "label": b.get("label"),
                }
                for b in bindings
            ]
        ),
        "STREAMLIT_SERVER_PORT": str(port),
        "STREAMLIT_SERVER_ADDRESS": "127.0.0.1",
        "STREAMLIT_SERVER_HEADLESS": "true",
        "STREAMLIT_SERVER_ENABLE_CORS": "false",
        "STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION": "false",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        "STREAMLIT_SERVER_BASE_URL_PATH": base_path,
    }
    # TLS / locale from the parent — never secrets. Streamlit and httpx need
    # the container CA bundle; a fully empty env breaks HTTPS to Fluxito.
    for key in (
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "TZ",
        "PYTHONHOME",
    ):
        val = os.environ.get(key)
        if val:
            env[key] = val
    # Never copy blocked keys even if a caller merged os.environ by mistake.
    for key in _BLOCKED_ENV:
        env.pop(key, None)
    return env


def _preexec() -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_NOFILE, (NOFILE_LIMIT, NOFILE_LIMIT))
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        os.setsid()
    except Exception:
        try:
            os.setsid()
        except Exception:
            pass


def pick_free_port() -> int:
    used = {hp.port for hp in _processes.values() if hp.is_alive()}
    for port in range(HOST_PORT_MIN, HOST_PORT_MAX + 1):
        if port in used:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"No free dashboard host port in {HOST_PORT_MIN}-{HOST_PORT_MAX}")


def _wait_for_port(port: int, proc: subprocess.Popen, timeout: float = START_TIMEOUT_S) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Host process exited during startup (code {proc.returncode})")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            try:
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.15)
    raise RuntimeError(f"Host did not accept connections on 127.0.0.1:{port} within {timeout}s")


def streamlit_command(entrypoint: str, port: int, base_path: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        entrypoint,
        "--server.port",
        str(port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.baseUrlPath",
        base_path,
        "--server.enableCORS",
        "false",
        "--server.enableXsrfProtection",
        "false",
    ]


def _host_state_path(workdir: Path) -> Path:
    return workdir / HOST_STATE_NAME


def _write_host_state(workdir: Path, *, dashboard_id: str, slug: str, port: int, pid: int) -> None:
    import json

    _host_state_path(workdir).write_text(
        json.dumps(
            {"dashboard_id": dashboard_id, "slug": slug, "port": port, "pid": pid},
            indent=2,
        ),
        encoding="utf-8",
    )


def read_host_state(workdir: Path) -> dict | None:
    import json

    path = _host_state_path(workdir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
    return True


class _ExternalProc:
    """Stand-in for a Streamlit child started by another Gunicorn worker."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None

    def poll(self):
        if _pid_alive(self.pid):
            return None
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        try:
            os.kill(self.pid, 15)
        except OSError:
            pass

    def kill(self) -> None:
        try:
            os.kill(self.pid, 9)
        except OSError:
            pass

    def wait(self, timeout=None):
        deadline = time.time() + (timeout if timeout is not None else 5)
        while time.time() < deadline:
            if not _pid_alive(self.pid):
                self.returncode = 0
                return 0
            time.sleep(0.1)
        raise subprocess.TimeoutExpired(f"pid {self.pid}", timeout)


def attach_existing(dashboard_id: str, workdir: Path) -> HostedProcess | None:
    """Reuse a live child started by another worker (shared workdir state)."""
    existing = _processes.get(str(dashboard_id))
    if existing is not None and existing.is_alive():
        return existing
    state = read_host_state(workdir)
    if not state:
        return None
    try:
        pid = int(state.get("pid") or 0)
        port = int(state.get("port") or 0)
    except (TypeError, ValueError):
        return None
    slug = str(state.get("slug") or "")
    if pid <= 0 or port <= 0 or not _pid_alive(pid) or not _port_open(port):
        return None
    handle = HostedProcess(
        dashboard_id=str(dashboard_id),
        slug=slug,
        port=port,
        pid=pid,
        workdir=workdir,
        proc=_ExternalProc(pid),
    )
    _processes[str(dashboard_id)] = handle
    return handle


def _read_host_log_tail(workdir: Path, limit: int = 1500) -> str:
    path = workdir / HOST_LOG_NAME
    if not path.is_file():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    text = data[-limit:].decode("utf-8", errors="replace").strip()
    return text


def stop_dashboard(dashboard_id: str, workdir: Path | None = None) -> None:
    handle = _processes.pop(str(dashboard_id), None)
    if handle is None and workdir is not None:
        handle = attach_existing(dashboard_id, workdir)
        _processes.pop(str(dashboard_id), None)
    if handle is None:
        return
    proc = handle.proc
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    except Exception as exc:
        logger.warning("Failed to stop hosted dashboard %s: %s", dashboard_id, exc)
    if handle.workdir:
        try:
            _host_state_path(handle.workdir).unlink(missing_ok=True)
        except OSError:
            pass


def start_dashboard(
    *,
    dashboard_id: str,
    slug: str,
    workdir: Path,
    entrypoint: str,
    env: dict[str, str],
    port: int | None = None,
) -> HostedProcess:
    """Start (or restart) the isolated Streamlit process."""
    attached = attach_existing(dashboard_id, workdir)
    if attached is not None:
        return attached
    stop_dashboard(dashboard_id, workdir=workdir)
    port = port or pick_free_port()
    base_path = f"/hosted/{slug}"
    env = dict(env)
    env["STREAMLIT_SERVER_PORT"] = str(port)
    env["STREAMLIT_SERVER_BASE_URL_PATH"] = base_path

    log_path = workdir / HOST_LOG_NAME
    log_file = None

    factory = _process_factory
    if factory is not None:
        proc = factory(
            dashboard_id=dashboard_id,
            slug=slug,
            workdir=str(workdir),
            entrypoint=entrypoint,
            env=env,
            port=port,
            base_path=base_path,
        )
    else:
        cmd = streamlit_command(entrypoint, port, base_path)
        log_file = open(log_path, "ab")  # noqa: SIM115 — kept open for the child
        kwargs: dict = {
            "cwd": str(workdir),
            "env": env,
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
            "stdin": subprocess.DEVNULL,
        }
        if os.name == "posix":
            kwargs["preexec_fn"] = _preexec
        try:
            proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 — argv is our streamlit_command()
        except FileNotFoundError as exc:
            log_file.close()
            raise RuntimeError(
                "Streamlit is not installed. Install the `streamlit` package on the Fluxito host."
            ) from exc

    try:
        _wait_for_port(port, proc)
    except Exception as exc:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        if log_file is not None:
            try:
                log_file.flush()
            except Exception:
                pass
        tail = _read_host_log_tail(workdir)
        detail = f"{exc}"
        if tail:
            detail = f"{detail}: {tail[-400:]}"
        raise RuntimeError(detail[:500]) from exc
    finally:
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass

    handle = HostedProcess(
        dashboard_id=str(dashboard_id),
        slug=slug,
        port=port,
        pid=proc.pid or 0,
        workdir=workdir,
        proc=proc,
    )
    _processes[str(dashboard_id)] = handle
    try:
        _write_host_state(
            workdir,
            dashboard_id=str(dashboard_id),
            slug=slug,
            port=port,
            pid=handle.pid,
        )
    except OSError as exc:
        logger.warning("Could not persist host state for %s: %s", dashboard_id, exc)
    return handle


def get_handle(dashboard_id: str, workdir: Path | None = None) -> HostedProcess | None:
    handle = _processes.get(str(dashboard_id))
    if handle is not None:
        if handle.is_alive():
            return handle
        _processes.pop(str(dashboard_id), None)
    if workdir is not None:
        return attach_existing(dashboard_id, workdir)
    return None


def delete_workdir(user_id: UUID | str, dashboard_id: UUID | str) -> None:
    stop_dashboard(str(dashboard_id))
    path = workdir_for(user_id, dashboard_id)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def stop_all() -> None:
    for dash_id in list(_processes):
        stop_dashboard(dash_id)


atexit.register(stop_all)


def child_env_is_clean(env: dict[str, str]) -> bool:
    return not any(k in env for k in _BLOCKED_ENV)
