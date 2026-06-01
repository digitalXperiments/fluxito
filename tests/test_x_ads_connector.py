import re

import pytest

from app.connectors.x_ads import XAdsConnector, XOAuth1Token


@pytest.mark.asyncio
async def test_list_accounts_sends_oauth1_authorization_header(monkeypatch):
    """X Ads API requests are signed with OAuth 1.0a credentials."""
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"data":[]}'

        def json(self):
            return {"data": [{"id": "abc123", "name": "Main account", "approval_status": "ACCEPTED"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, *, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["params"] = params or {}
            return FakeResponse()

    monkeypatch.setattr("app.connectors.x_ads.httpx.AsyncClient", FakeClient)

    connector = XAdsConnector(consumer_key="ck", consumer_secret="cs")
    result = await connector.list_accounts(XOAuth1Token(token="at", token_secret="ats"))

    assert result["accounts"] == [{"account_id": "abc123", "name": "Main account", "status": "ACCEPTED"}]
    auth = captured["headers"]["Authorization"]
    assert auth.startswith("OAuth ")
    assert 'oauth_consumer_key="ck"' in auth
    assert 'oauth_token="at"' in auth
    assert re.search(r'oauth_signature="[^"]+"', auth)
