"""Read-only browsing tools. These never touch Cliniko directly - they read
the in-memory RefDataStore that refdata/sync.py keeps warm, so browsing
never eats into the 200 req/min Cliniko budget."""
from __future__ import annotations

import logging

from livekit.agents import RunContext, function_tool

from clinic_agent.state import CallState

logger = logging.getLogger(__name__)


@function_tool
async def list_branches(context: RunContext[CallState]) -> str:
    """List the clinic's branches/locations so the caller can pick one."""
    logger.info("[call=%s] list_branches called", context.userdata.call_id)
    branches = context.userdata.refdata.businesses()
    if not branches:
        logger.warning("[call=%s] list_branches: refdata cache is empty", context.userdata.call_id)
        return "No branches are configured right now."
    logger.info("[call=%s] list_branches -> %s", context.userdata.call_id, [b.name for b in branches])
    return "Branches: " + ", ".join(b.name for b in branches)


@function_tool
async def list_appointment_types(context: RunContext[CallState]) -> str:
    """List the services/reasons-for-visit (appointment types) patients can book."""
    logger.info("[call=%s] list_appointment_types called", context.userdata.call_id)
    types = context.userdata.refdata.appointment_types()
    if not types:
        logger.warning("[call=%s] list_appointment_types: refdata cache is empty", context.userdata.call_id)
        return "No appointment types are configured right now."
    logger.info("[call=%s] list_appointment_types -> %s", context.userdata.call_id, [t.name for t in types])
    return "Appointment types: " + ", ".join(t.name for t in types)


@function_tool
async def list_doctors(context: RunContext[CallState], appointment_type_name: str | None = None) -> str:
    """List doctors, optionally filtered to ones who can be booked for a
    given service/appointment type name (e.g. 'General Consultation')."""
    logger.info(
        "[call=%s] list_doctors called (appointment_type_name=%r)",
        context.userdata.call_id, appointment_type_name,
    )
    refdata = context.userdata.refdata
    appointment_type_id = None
    if appointment_type_name:
        appointment_type = refdata.appointment_type_by_name(appointment_type_name)
        if appointment_type is None:
            logger.info(
                "[call=%s] list_doctors: no appointment type matched %r",
                context.userdata.call_id, appointment_type_name,
            )
            return f"I couldn't find an appointment type called '{appointment_type_name}'."
        appointment_type_id = appointment_type.id

    doctors = refdata.doctors_for(appointment_type_id)
    if not doctors:
        logger.warning(
            "[call=%s] list_doctors: no doctors found for appointment_type_id=%s",
            context.userdata.call_id, appointment_type_id,
        )
        return "No doctors are available for that right now."
    logger.info("[call=%s] list_doctors -> %s", context.userdata.call_id, [d.full_name for d in doctors])
    return "Doctors: " + ", ".join(d.full_name for d in doctors)
