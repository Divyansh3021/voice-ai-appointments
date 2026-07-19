from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from livekit.agents import RunContext, function_tool

from clinic_agent.cliniko.errors import ClinikoBadRequest, ClinikoNotFound
from clinic_agent.cliniko.operations import find_available_times
from clinic_agent.state import CallState, BookingDraft

logger = logging.getLogger(__name__)


@function_tool
async def check_availability(
    context: RunContext[CallState],
    doctor_name: str,
    appointment_type_name: str,
    earliest_date: str,
) -> str:
    """Find open appointment slots for a doctor and service, starting from a
    given date. `earliest_date` must be an ISO date (YYYY-MM-DD) - use
    today's date from your instructions if the caller says "as soon as
    possible". Requires the branch to already be set via set_branch."""
    state = context.userdata
    logger.info(
        "[call=%s] check_availability called (doctor_name=%r, appointment_type_name=%r, earliest_date=%r)",
        state.call_id, doctor_name, appointment_type_name, earliest_date,
    )
    if state.branch_id is None:
        logger.info("[call=%s] check_availability: no branch set yet", state.call_id)
        return "I need to know which branch first - please ask the caller and call set_branch."

    if not doctor_name.strip():
        logger.info("[call=%s] check_availability: blank doctor_name rejected", state.call_id)
        return "I need a doctor's name to check availability - ask the caller, or call list_doctors first."
    doctor = state.refdata.practitioner_by_name(doctor_name)
    if doctor is None:
        logger.info("[call=%s] check_availability: no doctor matched %r", state.call_id, doctor_name)
        return f"I couldn't find a doctor called '{doctor_name}'. Try list_doctors first."

    if not appointment_type_name.strip():
        logger.info("[call=%s] check_availability: blank appointment_type_name rejected", state.call_id)
        return "I need a service/appointment type to check availability - ask the caller, or call list_appointment_types first."
    appointment_type = state.refdata.appointment_type_by_name(appointment_type_name)
    if appointment_type is None:
        logger.info(
            "[call=%s] check_availability: no appointment type matched %r",
            state.call_id, appointment_type_name,
        )
        return f"I couldn't find an appointment type called '{appointment_type_name}'. Try list_appointment_types first."

    try:
        earliest = date.fromisoformat(earliest_date)
    except ValueError:
        logger.info("[call=%s] check_availability: bad earliest_date %r", state.call_id, earliest_date)
        return "earliest_date must be an ISO date like 2026-07-20."

    logger.info(
        "[call=%s] check_availability: querying Cliniko (business_id=%s, practitioner_id=%s, "
        "appointment_type_id=%s, earliest=%s)",
        state.call_id, state.branch_id, doctor.id, appointment_type.id, earliest,
    )
    try:
        slots = await find_available_times(
            state.cliniko,
            business_id=state.branch_id,
            practitioner_id=doctor.id,
            appointment_type_id=appointment_type.id,
            earliest=earliest,
        )
    except ClinikoNotFound:
        logger.info(
            "[call=%s] check_availability: 404 - doctor_id=%s not linked to appointment_type_id=%s at branch_id=%s",
            state.call_id, doctor.id, appointment_type.id, state.branch_id,
        )
        return (
            f"Dr. {doctor.full_name} doesn't offer '{appointment_type.name}' at this branch. "
            "Try list_doctors with that appointment type to find someone who does."
        )
    except ClinikoBadRequest:
        # Unlike ClinikoNotFound (a valid "not offered here" outcome), this
        # means we sent Cliniko something it couldn't parse - a bug on our
        # side, not the caller's. Log it so it's debuggable, but don't crash
        # the call over it.
        logger.exception("[call=%s] check_availability: Cliniko rejected the request as malformed", state.call_id)
        return "I'm having trouble checking availability right now - offer to take a callback request instead."

    state.draft = BookingDraft(
        branch_id=state.branch_id,
        practitioner_id=doctor.id,
        appointment_type_id=appointment_type.id,
    )

    if not slots:
        logger.info("[call=%s] check_availability -> 0 slots found", state.call_id)
        return (
            f"No open slots for Dr. {doctor.full_name} in the next few weeks. "
            "Offer to take a callback request instead."
        )

    # Cliniko's timestamps are UTC; speak them in the branch's own timezone,
    # not the raw UTC clock numbers (which would be off by the UTC offset -
    # e.g. an 08:00 UTC slot is 13:30 in Asia/Kolkata, not "8:00 AM").
    branch = state.refdata.business_by_id(state.branch_id)
    tz = ZoneInfo(branch.time_zone_identifier) if branch else ZoneInfo("UTC")

    # Number the options and store the real (UTC) datetimes server-side.
    # book_appointment/reschedule_appointment take that number back, not an
    # ISO timestamp - the LLM was previously reconstructing timestamps by
    # hand from spoken text (e.g. "9:00 AM" -> "2026-07-18T09:00:00", with
    # no timezone and no guarantee it matched an actually-offered slot).
    state.offered_slots = {i: s.appointment_start for i, s in enumerate(slots, start=1)}
    logger.info(
        "[call=%s] check_availability -> %d slot(s) in %s: %s",
        state.call_id, len(slots), tz.key,
        {i: dt.astimezone(tz).isoformat() for i, dt in state.offered_slots.items()},
    )

    formatted = ", ".join(
        f"option {i}: {_speakable(dt.astimezone(tz))}" for i, dt in state.offered_slots.items()
    )
    return (
        f"Available times with Dr. {doctor.full_name}: {formatted}. "
        "Read these out to the caller, then call book_appointment with the option number they pick."
    )


def _speakable(dt: datetime) -> str:
    return dt.strftime("%A %d %B, %I:%M %p")
