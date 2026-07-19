"""Tests the booking call sequence at the cliniko/operations layer (the
function_tool-wrapped versions in tools/ need a full LiveKit RunContext to
construct, which isn't worth mocking here - the business logic they call
straight into is what's under test)."""
from datetime import datetime

import httpx
import pytest
import respx

from clinic_agent.cliniko.client import ClinikoClient
from clinic_agent.cliniko.operations import book_with_conflict_retry, cancel_appointment


@pytest.mark.asyncio
async def test_book_with_conflict_retry_success_path():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.post("/individual_appointments").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 42,
                    "starts_at": "2026-07-20T10:00:00Z",
                    "business_id": 1,
                    "practitioner_id": 2,
                    "appointment_type_id": 3,
                    "patient_id": 9,
                },
            )
        )
        mock.get("/individual_appointments/42/conflicts").mock(
            return_value=httpx.Response(200, json={"conflicts": {"exist": False}})
        )

        appointment, alternatives = await book_with_conflict_retry(
            client,
            business_id=1,
            practitioner_id=2,
            appointment_type_id=3,
            patient_id=9,
            starts_at=datetime(2026, 7, 20, 10, 0),
        )

    assert appointment is not None
    assert appointment.id == 42
    assert alternatives == []
    await client.aclose()


@pytest.mark.asyncio
async def test_book_with_conflict_retry_offers_alternatives_on_409():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    availability_path = "/businesses/1/practitioners/2/appointment_types/3/available_times"

    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.post("/individual_appointments").mock(return_value=httpx.Response(409, text="taken"))
        mock.get(availability_path, params={"from": "2026-07-20", "to": "2026-07-26"}).mock(
            return_value=httpx.Response(
                200,
                json={
                    "available_times": [
                        {"appointment_start": "2026-07-20T11:00:00Z"},
                        {"appointment_start": "2026-07-20T14:00:00Z"},
                    ]
                },
            )
        )

        appointment, alternatives = await book_with_conflict_retry(
            client,
            business_id=1,
            practitioner_id=2,
            appointment_type_id=3,
            patient_id=9,
            starts_at=datetime(2026, 7, 20, 10, 0),
        )

    assert appointment is None
    assert len(alternatives) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_book_with_conflict_retry_cancels_when_conflicts_found_post_creation():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    availability_path = "/businesses/1/practitioners/2/appointment_types/3/available_times"

    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        mock.post("/individual_appointments").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 42,
                    "starts_at": "2026-07-20T10:00:00Z",
                    "business_id": 1,
                    "practitioner_id": 2,
                    "appointment_type_id": 3,
                    "patient_id": 9,
                },
            )
        )
        mock.get("/individual_appointments/42/conflicts").mock(
            return_value=httpx.Response(200, json={"conflicts": {"exist": True}})
        )
        cancel_route = mock.patch("/individual_appointments/42/cancel").mock(
            return_value=httpx.Response(200, json={"id": 42, "cancelled_at": "2026-07-19T00:00:00Z", "starts_at": "2026-07-20T10:00:00Z"})
        )
        mock.get(availability_path).mock(
            return_value=httpx.Response(200, json={"available_times": []})
        )

        appointment, alternatives = await book_with_conflict_retry(
            client,
            business_id=1,
            practitioner_id=2,
            appointment_type_id=3,
            patient_id=9,
            starts_at=datetime(2026, 7, 20, 10, 0),
        )

    assert appointment is None
    assert cancel_route.called
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_appointment_uses_patch_when_reasons_are_configured():
    client = ClinikoClient("mykey-au2", "contact@example.com")
    with respx.mock(base_url="https://api.au2.cliniko.com/v1", assert_all_called=False) as mock:
        cancel_route = mock.patch("/individual_appointments/42/cancel").mock(
            return_value=httpx.Response(200, json={"id": 42, "cancelled_at": "2026-07-19T00:00:00Z"})
        )
        delete_route = mock.delete("/individual_appointments/42")

        await cancel_appointment(client, 42, note="test")

    assert cancel_route.called
    assert not delete_route.called
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_appointment_falls_back_to_delete_when_no_reasons_configured():
    """Real-world case hit against the trial Cliniko account: a clinic with
    no cancellation reasons configured in Cliniko settings gets a 422 for
    *any* cancellation_reason value, including a blank one - PATCH can never
    succeed there, so we fall back to a hard DELETE."""
    client = ClinikoClient("mykey-au2", "contact@example.com")
    with respx.mock(base_url="https://api.au2.cliniko.com/v1") as mock:
        cancel_route = mock.patch("/individual_appointments/42/cancel").mock(
            return_value=httpx.Response(
                422, json={"errors": {"cancellation_reason": "is not included in the list"}}
            )
        )
        delete_route = mock.delete("/individual_appointments/42").mock(return_value=httpx.Response(204))

        await cancel_appointment(client, 42, note="test")

    assert cancel_route.called
    assert delete_route.called
    await client.aclose()
