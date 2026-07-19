from datetime import date

import httpx
import pytest
import respx

from clinic_agent.cliniko.client import ClinikoClient
from clinic_agent.cliniko.operations import find_available_times


@pytest.mark.asyncio
async def test_find_available_times_stops_once_enough_results():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    path = "/businesses/1/practitioners/2/appointment_types/3/available_times"

    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.get(path, params={"from": "2026-07-17", "to": "2026-07-23"}).mock(
            return_value=httpx.Response(
                200,
                json={"available_times": [{"appointment_start": "2026-07-18T10:00:00Z"}]},
            )
        )
        slots = await find_available_times(
            client,
            business_id=1,
            practitioner_id=2,
            appointment_type_id=3,
            earliest=date(2026, 7, 17),
            max_results=1,
        )

    assert len(slots) == 1
    assert slots[0].appointment_start.year == 2026
    await client.aclose()


@pytest.mark.asyncio
async def test_find_available_times_pages_across_multiple_6_day_windows():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    path = "/businesses/1/practitioners/2/appointment_types/3/available_times"

    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.get(path, params={"from": "2026-07-17", "to": "2026-07-23"}).mock(
            return_value=httpx.Response(200, json={"available_times": []})
        )
        mock.get(path, params={"from": "2026-07-23", "to": "2026-07-29"}).mock(
            return_value=httpx.Response(
                200, json={"available_times": [{"appointment_start": "2026-07-25T09:00:00Z"}]}
            )
        )
        slots = await find_available_times(
            client,
            business_id=1,
            practitioner_id=2,
            appointment_type_id=3,
            earliest=date(2026, 7, 17),
            max_results=1,
            lookahead_days=12,
        )

    assert len(slots) == 1
    assert slots[0].appointment_start.day == 25
    await client.aclose()


@pytest.mark.asyncio
async def test_find_available_times_returns_empty_when_nothing_found():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    path = "/businesses/1/practitioners/2/appointment_types/3/available_times"

    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.get(path, params={"from": "2026-07-17", "to": "2026-07-23"}).mock(
            return_value=httpx.Response(200, json={"available_times": []})
        )
        slots = await find_available_times(
            client,
            business_id=1,
            practitioner_id=2,
            appointment_type_id=3,
            earliest=date(2026, 7, 17),
            max_results=3,
            lookahead_days=6,
        )

    assert slots == []
    await client.aclose()
