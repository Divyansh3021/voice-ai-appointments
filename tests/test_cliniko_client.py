import base64

import httpx
import pytest
import respx

from clinic_agent.cliniko.client import ClinikoClient, _parse_shard
from clinic_agent.cliniko.errors import ClinikoConflict, ClinikoNotFound


def test_parse_shard_from_key_suffix():
    assert _parse_shard("abc123-au4") == "au4"


def test_parse_shard_rejects_key_without_shard():
    with pytest.raises(ValueError):
        _parse_shard("nodasheshere")


def test_client_builds_correct_base_url_and_auth_header():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    assert client._base_url == "https://api.au2.cliniko.com/v1"
    expected_token = base64.b64encode(b"mykey-au2:").decode()
    assert client._headers["Authorization"] == f"Basic {expected_token}"
    assert "contact@example.com" in client._headers["User-Agent"]


@pytest.mark.asyncio
async def test_get_returns_json():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.get("/businesses").mock(return_value=httpx.Response(200, json={"businesses": []}))
        result = await client.get("/businesses")
    assert result == {"businesses": []}
    await client.aclose()


@pytest.mark.asyncio
async def test_404_raises_not_found():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.get("/patients/999").mock(return_value=httpx.Response(404))
        with pytest.raises(ClinikoNotFound):
            await client.get("/patients/999")
    await client.aclose()


@pytest.mark.asyncio
async def test_409_raises_conflict():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.post("/individual_appointments").mock(return_value=httpx.Response(409, text="conflict"))
        with pytest.raises(ClinikoConflict):
            await client.post("/individual_appointments", json={})
    await client.aclose()


@pytest.mark.asyncio
async def test_paginate_follows_next_link():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.get("/businesses", params={"per_page": "100"}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "businesses": [{"id": 1, "name": "Branch A"}],
                    "links": {"next": "https://api.au2.cliniko.com/v1/businesses?page=2"},
                },
            )
        )
        mock.get("/businesses", params={"page": "2"}).mock(
            return_value=httpx.Response(
                200, json={"businesses": [{"id": 2, "name": "Branch B"}], "links": {}}
            )
        )
        items = [item async for item in client.paginate("/businesses", key="businesses")]
    assert [i["id"] for i in items] == [1, 2]
    await client.aclose()
