from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from livekit.agents import RunContext, function_tool

from clinic_agent.cliniko.operations import book_with_conflict_retry
from clinic_agent.cliniko.utils import to_cliniko_utc
from clinic_agent.db.repo import log_appointment_action
from clinic_agent.db.session import get_session
from clinic_agent.state import CallState

logger = logging.getLogger(__name__)


@function_tool
async def book_appointment(context: RunContext[CallState], slot_number: int) -> str:
    """Book the appointment the caller just confirmed. `slot_number` is the
    option number (1, 2, 3...) they picked from check_availability's list -
    call check_availability first to populate the available options.
    Requires a patient to already be identified."""
    state = context.userdata
    logger.info("[call=%s] book_appointment called (slot_number=%s)", state.call_id, slot_number)

    if state.patient_id is None:
        logger.info("[call=%s] book_appointment: no patient identified yet", state.call_id)
        return "I need to identify the patient first - call identify_or_create_patient."
    if state.draft is None or None in (state.draft.branch_id, state.draft.practitioner_id, state.draft.appointment_type_id):
        logger.info("[call=%s] book_appointment: no booking draft (branch/doctor/service) yet", state.call_id)
        return "I don't have a doctor/service selected yet - call check_availability first."

    when = state.offered_slots.get(slot_number)
    if when is None:
        logger.info(
            "[call=%s] book_appointment: slot_number=%s not in offered_slots %s",
            state.call_id, slot_number, list(state.offered_slots),
        )
        return "That's not one of the options I offered - call check_availability again and use one of those option numbers."

    branch = state.refdata.business_by_id(state.draft.branch_id)
    tz = ZoneInfo(branch.time_zone_identifier) if branch else ZoneInfo("UTC")

    logger.info(
        "[call=%s] book_appointment: attempting create (business_id=%s, practitioner_id=%s, "
        "appointment_type_id=%s, patient_id=%s, starts_at=%s)",
        state.call_id, state.draft.branch_id, state.draft.practitioner_id,
        state.draft.appointment_type_id, state.patient_id, when,
    )
    appointment, alternatives = await book_with_conflict_retry(
        state.cliniko,
        business_id=state.draft.branch_id,
        practitioner_id=state.draft.practitioner_id,
        appointment_type_id=state.draft.appointment_type_id,
        patient_id=state.patient_id,
        starts_at=when,
    )
    logger.info(
        "[call=%s] book_appointment: result appointment_id=%s, alternatives=%s",
        state.call_id, appointment.id if appointment else None,
        [a.appointment_start.isoformat() for a in alternatives],
    )

    async with get_session() as session:
        await log_appointment_action(
            session,
            call_id=state.call_id,
            action="create" if appointment else "conflict_retry",
            cliniko_appointment_id=appointment.id if appointment else None,
            request_payload={"starts_at": to_cliniko_utc(when), "patient_id": state.patient_id},
            response_status=200 if appointment else 409,
            response_payload={"alternatives": [a.appointment_start.isoformat() for a in alternatives]} if not appointment else None,
        )

    if appointment:
        state.outcome = "booked"
        local_start = appointment.starts_at.astimezone(tz)
        return f"Booked for {local_start.strftime('%A %d %B at %I:%M %p')}. Confirmation number {appointment.id}."

    if alternatives:
        # Re-number against the fresh alternatives so a follow-up
        # book_appointment call can reference them the same way.
        state.offered_slots = {i: a.appointment_start for i, a in enumerate(alternatives, start=1)}
        options = ", ".join(
            f"option {i}: {dt.astimezone(tz).strftime('%A %d %B, %I:%M %p')}"
            for i, dt in state.offered_slots.items()
        )
        return f"That slot was just taken. Other options: {options}. Ask the caller to pick one and call book_appointment again with that option number."

    return "That slot was just taken and I couldn't find another nearby. Offer to take a callback request."
