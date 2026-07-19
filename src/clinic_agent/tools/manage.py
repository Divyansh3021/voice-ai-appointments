"""Reschedule/cancel flows. Both need find_upcoming_appointments first so the
caller can say "the one on Tuesday" rather than an appointment id."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from livekit.agents import RunContext, function_tool

from clinic_agent.cliniko.errors import ClinikoConflict, ClinikoValidationError
from clinic_agent.cliniko.models import Appointment
from clinic_agent.cliniko.operations import cancel_appointment as cliniko_cancel_appointment
from clinic_agent.cliniko.utils import to_cliniko_utc
from clinic_agent.db.repo import log_appointment_action
from clinic_agent.db.session import get_session
from clinic_agent.state import CallState

logger = logging.getLogger(__name__)


def _tz_for_business(state: CallState, business_id: int | None) -> ZoneInfo:
    branch = state.refdata.business_by_id(business_id) if business_id else None
    return ZoneInfo(branch.time_zone_identifier) if branch else ZoneInfo("UTC")


@function_tool
async def find_upcoming_appointments(context: RunContext[CallState]) -> str:
    """List the identified patient's upcoming appointments - use this before
    a reschedule or cancel request."""
    state = context.userdata
    logger.info("[call=%s] find_upcoming_appointments called", state.call_id)
    if state.patient_id is None:
        logger.info("[call=%s] find_upcoming_appointments: no patient identified yet", state.call_id)
        return "I need to identify the patient first - call identify_or_create_patient."

    now_iso = to_cliniko_utc(datetime.now(timezone.utc))
    params = {"q[]": [f"patient_id:={state.patient_id}", f"starts_at:>={now_iso}"]}
    logger.info("[call=%s] find_upcoming_appointments: querying Cliniko %s", state.call_id, params)
    appointments = [
        Appointment(**a)
        async for a in state.cliniko.paginate("/individual_appointments", key="individual_appointments", params=params)
        if a.get("cancelled_at") is None
    ]
    logger.info("[call=%s] find_upcoming_appointments -> %d appointment(s)", state.call_id, len(appointments))

    if not appointments:
        return "No upcoming appointments found for this patient."

    for appt in appointments:
        state.known_appointments[appt.id] = appt

    listing = "; ".join(
        f"#{a.id} on {a.starts_at.astimezone(_tz_for_business(state, a.business_id)).strftime('%A %d %B at %I:%M %p')}"
        for a in appointments
    )
    return f"Upcoming appointments: {listing}"


@function_tool
async def reschedule_appointment(context: RunContext[CallState], appointment_id: int, slot_number: int) -> str:
    """Move an existing appointment to a new time. Call
    find_upcoming_appointments first to get the appointment_id, and
    check_availability to get available option numbers - `slot_number` is
    the option number the caller picked from that list."""
    state = context.userdata
    logger.info(
        "[call=%s] reschedule_appointment called (appointment_id=%s, slot_number=%s)",
        state.call_id, appointment_id, slot_number,
    )
    when = state.offered_slots.get(slot_number)
    if when is None:
        logger.info(
            "[call=%s] reschedule_appointment: slot_number=%s not in offered_slots %s",
            state.call_id, slot_number, list(state.offered_slots),
        )
        return "That's not one of the options I offered - call check_availability again and use one of those option numbers."

    old = state.known_appointments.get(appointment_id)
    if old is None:
        logger.warning(
            "[call=%s] reschedule_appointment: appointment_id=%s not in known_appointments",
            state.call_id, appointment_id,
        )
        return "I don't have that appointment's details - call find_upcoming_appointments again first."

    # PATCHing only starts_at leaves Cliniko's old ends_at in place; if the
    # new start falls after that old end time, Cliniko 422s with "end time
    # must be greater than start time". Carry the original duration forward
    # (default 30 min if the source appointment had no ends_at for some reason).
    duration = (old.ends_at - old.starts_at) if old.ends_at else timedelta(minutes=30)
    new_ends_at = when + duration

    action = "reschedule"
    try:
        logger.info("[call=%s] reschedule_appointment: trying PATCH in place", state.call_id)
        updated = await state.cliniko.patch(
            f"/individual_appointments/{appointment_id}",
            json={"starts_at": to_cliniko_utc(when), "ends_at": to_cliniko_utc(new_ends_at)},
        )
        result_appointment = Appointment(**updated)
    except (ClinikoConflict, ClinikoValidationError):
        # Cliniko rejected moving the time in place - fall back to cancel + recreate.
        logger.info(
            "[call=%s] reschedule_appointment: in-place PATCH failed, falling back to cancel+recreate",
            state.call_id,
        )
        await cliniko_cancel_appointment(state.cliniko, appointment_id, note="Rescheduled via voice assistant")
        created = await state.cliniko.post(
            "/individual_appointments",
            json={
                "business_id": old.business_id,
                "practitioner_id": old.practitioner_id,
                "appointment_type_id": old.appointment_type_id,
                "patient_id": old.patient_id,
                "starts_at": to_cliniko_utc(when),
                "ends_at": to_cliniko_utc(new_ends_at),
            },
        )
        result_appointment = Appointment(**created)
        action = "reschedule_via_recreate"

    logger.info(
        "[call=%s] reschedule_appointment -> action=%s, new appointment_id=%s",
        state.call_id, action, result_appointment.id,
    )

    async with get_session() as session:
        await log_appointment_action(
            session,
            call_id=state.call_id,
            action=action,
            cliniko_appointment_id=result_appointment.id,
            request_payload={"appointment_id": appointment_id, "new_starts_at": to_cliniko_utc(when)},
            response_status=200,
            response_payload=None,
        )

    state.outcome = "rescheduled"
    local_start = result_appointment.starts_at.astimezone(_tz_for_business(state, result_appointment.business_id))
    return f"Rescheduled to {local_start.strftime('%A %d %B at %I:%M %p')}."


@function_tool
async def cancel_appointment(context: RunContext[CallState], appointment_id: int, reason: str | None = None) -> str:
    """Cancel an existing appointment. Call find_upcoming_appointments first
    to get the appointment_id."""
    state = context.userdata
    logger.info(
        "[call=%s] cancel_appointment called (appointment_id=%s, reason=%r)",
        state.call_id, appointment_id, reason,
    )

    await cliniko_cancel_appointment(state.cliniko, appointment_id, note=reason or "Cancelled via voice assistant")
    logger.info("[call=%s] cancel_appointment -> cancelled appointment_id=%s", state.call_id, appointment_id)

    async with get_session() as session:
        await log_appointment_action(
            session,
            call_id=state.call_id,
            action="cancel",
            cliniko_appointment_id=appointment_id,
            request_payload={"appointment_id": appointment_id, "reason": reason},
            response_status=200,
            response_payload=None,
        )

    state.outcome = "cancelled"
    return "That appointment has been cancelled."
