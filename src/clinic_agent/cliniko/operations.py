"""Cliniko call sequences that are more than a single request: availability
paging across Cliniko's available_times window cap, and booking with a
conflict check + retry. Kept separate from `client.py` (transport) and the
`tools/` layer (LLM-facing) so each has one job.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from clinic_agent.cliniko.client import ClinikoClient
from clinic_agent.cliniko.errors import ClinikoConflict, ClinikoValidationError
from clinic_agent.cliniko.models import Appointment, AvailableTime
from clinic_agent.cliniko.utils import to_cliniko_utc

logger = logging.getLogger(__name__)

# Cliniko's docs describe this as a "7 day" cap, but empirically a `to - from`
# span of 7 days (e.g. 07-18 -> 07-25) 400s with "Invalid time frame
# definition" while 6 days (07-18 -> 07-24) succeeds - the limit is inclusive
# on both ends, so 6 is the real max span.
AVAILABILITY_WINDOW_DAYS = 6
DEFAULT_LOOKAHEAD_DAYS = 18  # 3 windows; beyond this, offer a callback instead


async def find_available_times(
    client: ClinikoClient,
    business_id: int,
    practitioner_id: int,
    appointment_type_id: int,
    earliest: date,
    max_results: int = 3,
    lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
) -> list[AvailableTime]:
    """Search for open slots, paging across Cliniko's 7-day request cap until
    we have enough results or run out of lookahead."""
    path = (
        f"/businesses/{business_id}/practitioners/{practitioner_id}"
        f"/appointment_types/{appointment_type_id}/available_times"
    )
    results: list[AvailableTime] = []
    window_start = earliest
    latest = earliest + timedelta(days=lookahead_days)

    while window_start < latest and len(results) < max_results:
        window_end = min(window_start + timedelta(days=AVAILABILITY_WINDOW_DAYS), latest)
        logger.info("find_available_times: GET %s from=%s to=%s", path, window_start, window_end)
        page = await client.get(
            path,
            params={"from": window_start.isoformat(), "to": window_end.isoformat()},
        )
        found = page.get("available_times", [])
        logger.info("find_available_times: window %s-%s -> %d slot(s)", window_start, window_end, len(found))
        results.extend(AvailableTime(**item) for item in found)
        window_start = window_end

    return results[:max_results]


async def cancel_appointment(client: ClinikoClient, appointment_id: int, note: str | None = None) -> None:
    """Cancel an appointment. Tries Cliniko's proper soft-cancel first (which
    needs `cancellation_reason` to match one of the reasons configured in
    that clinic's Cliniko settings), falling back to a hard DELETE if the
    clinic hasn't configured any reasons - PATCH .../cancel then 422s with
    "cancellation_reason is not included in the list" for *every* value,
    including a blank one, since there's nothing valid to match against.
    DELETE has no such dependency. Either way, our own appointment_audit
    table (not Cliniko's) is the source of truth for why/when."""
    logger.info("cancel_appointment: trying PATCH .../cancel for appointment_id=%s", appointment_id)
    try:
        await client.patch(
            f"/individual_appointments/{appointment_id}/cancel",
            json={"cancellation_reason": "Other", **({"cancellation_note": note} if note else {})},
        )
        logger.info("cancel_appointment: PATCH succeeded for appointment_id=%s", appointment_id)
    except ClinikoValidationError as e:
        if "cancellation_reason" not in e.body:
            raise
        logger.info(
            "cancel_appointment: PATCH 422'd on cancellation_reason (no reasons configured in "
            "Cliniko?) for appointment_id=%s, falling back to DELETE",
            appointment_id,
        )
        await client.delete(f"/individual_appointments/{appointment_id}")
        logger.info("cancel_appointment: DELETE succeeded for appointment_id=%s", appointment_id)


async def book_with_conflict_retry(
    client: ClinikoClient,
    *,
    business_id: int,
    practitioner_id: int,
    appointment_type_id: int,
    patient_id: int,
    starts_at: datetime,
    notes: str | None = None,
    alternatives_on_conflict: int = 2,
) -> tuple[Appointment | None, list[AvailableTime]]:
    """Try to book the requested slot. If it's taken (409, or a non-empty
    conflicts list on the just-created appointment), return no appointment
    plus a short list of alternative times for the caller to choose from
    instead of silently failing or double-booking."""
    payload = {
        "business_id": business_id,
        "practitioner_id": practitioner_id,
        "appointment_type_id": appointment_type_id,
        "patient_id": patient_id,
        "starts_at": to_cliniko_utc(starts_at),
    }
    if notes:
        payload["notes"] = notes

    logger.info("book_with_conflict_retry: POST /individual_appointments %s", payload)
    try:
        created = await client.post("/individual_appointments", json=payload)
        appointment_id = created["id"]
        logger.info("book_with_conflict_retry: created appointment_id=%s, checking conflicts", appointment_id)

        # The real response shape is {"conflicts": {"exist": bool}} - a dict,
        # not a list. `.get("conflicts")` alone is always truthy (non-empty
        # dict) regardless of `exist`, which was flagging every successful
        # booking as conflicted. Check the actual boolean.
        conflicts = await client.get(f"/individual_appointments/{appointment_id}/conflicts")
        exist = conflicts.get("conflicts", {}).get("exist")
        logger.info("book_with_conflict_retry: conflicts.exist=%s for appointment_id=%s", exist, appointment_id)
        if exist:
            await cancel_appointment(
                client, appointment_id, note="Auto-cancelled: conflict detected post-booking"
            )
            raise ClinikoConflict("Slot conflicted after creation")

        return Appointment(**created), []
    except ClinikoConflict:
        logger.info("book_with_conflict_retry: conflict, searching for alternatives")
        alternatives = await find_available_times(
            client,
            business_id=business_id,
            practitioner_id=practitioner_id,
            appointment_type_id=appointment_type_id,
            earliest=starts_at.date() + timedelta(days=0),
            max_results=alternatives_on_conflict,
        )
        # Don't re-offer the exact slot that just failed.
        alternatives = [a for a in alternatives if a.appointment_start != starts_at]
        logger.info("book_with_conflict_retry: offering %d alternative(s)", len(alternatives))
        return None, alternatives[:alternatives_on_conflict]
