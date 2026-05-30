# tests/test_sdr_source_xlsx.py
"""Source-xlsx storage + download tests."""

import base64
import uuid
from io import BytesIO

import pytest
from openpyxl import Workbook

import app.app_state as app_state
import app.models.sdr  # noqa: F401  — register SDR tables in Base.metadata for create_all


@pytest.fixture
def _patch_db(db_session_factory):
    original = app_state.db_session_factory
    app_state.db_session_factory = db_session_factory
    import app.tools.sdr_tools as sdr_tools

    orig_state = sdr_tools.state.db_session_factory
    sdr_tools.state.db_session_factory = db_session_factory
    yield
    app_state.db_session_factory = original
    sdr_tools.state.db_session_factory = orig_state


def _xlsx_b64() -> str:
    wb = Workbook()
    wb.active["A1"] = "hello"
    buf = BytesIO()
    wb.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.asyncio
async def test_store_source_xlsx_persists_bytes(_patch_db, db_session_factory):
    from sqlalchemy import select

    from app.models.project import Project
    from app.models.sdr import SDR
    from app.models.user import User
    from app.tools.sdr_tools import _store_source_xlsx

    user_id = uuid.uuid4()
    project_id = uuid.uuid4()
    async with db_session_factory() as db:
        # SDR.project_id / created_by carry FK constraints — seed the parents.
        db.add(User(id=user_id, email=f"sdr-xlsx-{user_id}@example.com"))
        await db.flush()
        db.add(Project(id=project_id, name="X", slug=f"sdr-xlsx-{project_id}", owner_id=user_id))
        await db.flush()
        sdr = SDR(project_id=project_id, name="X", markdown_content="# X", created_by=user_id)
        db.add(sdr)
        await db.flush()
        sdr_id = sdr.id
        await db.commit()

    async with db_session_factory() as db:
        sdr = (await db.execute(select(SDR).where(SDR.id == sdr_id))).scalar_one()
        ok, err = _store_source_xlsx(sdr, _xlsx_b64(), "VAST_Data_SDR.xlsx")
        assert ok is True and err is None
        await db.commit()

    async with db_session_factory() as db:
        sdr = (await db.execute(select(SDR).where(SDR.id == sdr_id))).scalar_one()
        assert sdr.source_xlsx and len(sdr.source_xlsx) > 0
        assert sdr.source_xlsx_filename == "VAST_Data_SDR.xlsx"
        assert sdr.source_xlsx_at is not None


def test_store_source_xlsx_rejects_non_xlsx():
    from app.models.sdr import SDR
    from app.tools.sdr_tools import _store_source_xlsx

    sdr = SDR(project_id=uuid.uuid4(), name="X", markdown_content="# X", created_by=uuid.uuid4())
    ok, err = _store_source_xlsx(sdr, base64.b64encode(b"not a workbook").decode(), "x.xlsx")
    assert ok is False and err


def test_store_source_xlsx_rejects_oversize():
    from app.models.sdr import SDR
    from app.tools.sdr_tools import _store_source_xlsx

    sdr = SDR(project_id=uuid.uuid4(), name="X", markdown_content="# X", created_by=uuid.uuid4())
    big = base64.b64encode(b"\x00" * (2 * 1024 * 1024 + 1)).decode()
    ok, err = _store_source_xlsx(sdr, big, "x.xlsx")
    assert ok is False and "size" in err.lower()


async def _seed_user_project(db_session_factory, pid):
    """Seed FK parents so an SDR row can be inserted under project `pid`."""
    from app.models.project import Project
    from app.models.user import User

    async with db_session_factory() as db:
        u = User(email=f"o-{uuid.uuid4().hex[:8]}@example.com")
        db.add(u)
        await db.flush()
        db.add(Project(id=pid, name="P", slug=f"p-{uuid.uuid4().hex[:8]}", owner_id=u.id))
        await db.flush()
        await db.commit()
        return u.id


@pytest.mark.asyncio
async def test_download_source_xlsx_endpoint(_patch_db, db_session_factory):
    from unittest.mock import AsyncMock, patch

    import httpx
    from httpx import ASGITransport

    from app.auth.csrf import _generate_csrf_token
    from app.main import app
    from app.models.sdr import SDR
    from app.tools.sdr_tools import _store_source_xlsx

    pid = uuid.uuid4()
    uid = await _seed_user_project(db_session_factory, pid)
    async with db_session_factory() as db:
        sdr = SDR(project_id=pid, name="X", markdown_content="# X", created_by=uid)
        _store_source_xlsx(sdr, _xlsx_b64(), "VAST_Data_SDR.xlsx")
        db.add(sdr)
        await db.commit()

    csrf = _generate_csrf_token()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"csrf_token": csrf},
        headers={"x-csrf-token": csrf},
    ) as client:
        with patch(
            "app.api.sdr_routes._require_user_and_project",
            new=AsyncMock(return_value=(None, None, pid)),
        ):
            resp = await client.get(f"/api/projects/{pid}/solution-design/source.xlsx")
    assert resp.status_code == 200, resp.text
    assert "VAST_Data_SDR.xlsx" in resp.headers.get("content-disposition", "")
    assert resp.content[:2] == b"PK"  # xlsx is a zip


@pytest.mark.asyncio
async def test_download_source_xlsx_sanitizes_filename(_patch_db, db_session_factory):
    from unittest.mock import AsyncMock, patch

    import httpx
    from httpx import ASGITransport

    from app.auth.csrf import _generate_csrf_token
    from app.main import app
    from app.models.sdr import SDR
    from app.tools.sdr_tools import _store_source_xlsx

    pid = uuid.uuid4()
    uid = await _seed_user_project(db_session_factory, pid)
    async with db_session_factory() as db:
        sdr = SDR(project_id=pid, name="X", markdown_content="# X", created_by=uid)
        _store_source_xlsx(sdr, _xlsx_b64(), 'ev"il\nname.xlsx')
        db.add(sdr)
        await db.commit()

    csrf = _generate_csrf_token()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"csrf_token": csrf},
        headers={"x-csrf-token": csrf},
    ) as client:
        with patch(
            "app.api.sdr_routes._require_user_and_project",
            new=AsyncMock(return_value=(None, None, pid)),
        ):
            resp = await client.get(f"/api/projects/{pid}/solution-design/source.xlsx")
    assert resp.status_code == 200, resp.text
    cd = resp.headers.get("content-disposition", "")
    # Extract the filename value between the surrounding double-quotes.
    # Header looks like: attachment; filename="<value>"
    assert "filename=" in cd
    after_eq = cd.split("filename=", 1)[1]
    # Strip the outer quotes to get the bare sanitized name.
    inner = after_eq.strip('"')
    # The sanitized name must contain no embedded double-quote or newline.
    assert '"' not in inner, f"Embedded quote leaked into Content-Disposition: {cd!r}"
    assert "\n" not in inner, f"Newline leaked into Content-Disposition: {cd!r}"
    assert "\r" not in inner, f"CR leaked into Content-Disposition: {cd!r}"


@pytest.mark.asyncio
async def test_download_source_xlsx_404_when_none(_patch_db, db_session_factory):
    from unittest.mock import AsyncMock, patch

    import httpx
    from httpx import ASGITransport

    from app.auth.csrf import _generate_csrf_token
    from app.main import app
    from app.models.sdr import SDR

    pid = uuid.uuid4()
    uid = await _seed_user_project(db_session_factory, pid)
    async with db_session_factory() as db:
        db.add(SDR(project_id=pid, name="X", markdown_content="# X", created_by=uid))
        await db.commit()

    csrf = _generate_csrf_token()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        cookies={"csrf_token": csrf},
        headers={"x-csrf-token": csrf},
    ) as client:
        with patch(
            "app.api.sdr_routes._require_user_and_project",
            new=AsyncMock(return_value=(None, None, pid)),
        ):
            resp = await client.get(f"/api/projects/{pid}/solution-design/source.xlsx")
    assert resp.status_code == 404
