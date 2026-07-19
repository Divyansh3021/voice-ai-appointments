from __future__ import annotations

import logging

from livekit.agents import RunContext, function_tool

from clinic_agent.cliniko.models import Patient
from clinic_agent.db.repo import get_cached_patient, upsert_cached_patient
from clinic_agent.db.session import get_session
from clinic_agent.state import CallState

logger = logging.getLogger(__name__)


@function_tool
async def set_branch(context: RunContext[CallState], branch_name: str) -> str:
    """Record which clinic branch the caller wants, once they've told you.
    Call list_branches first if you're not sure of the exact names."""
    logger.info("[call=%s] set_branch called (branch_name=%r)", context.userdata.call_id, branch_name)
    branch = context.userdata.refdata.business_by_name(branch_name)
    if branch is None:
        logger.info("[call=%s] set_branch: no match for %r", context.userdata.call_id, branch_name)
        return f"I don't recognize a branch called '{branch_name}'. Could you confirm the name?"
    context.userdata.branch_id = branch.id
    context.userdata.branch_name = branch.name
    logger.info("[call=%s] set_branch -> branch_id=%s (%s)", context.userdata.call_id, branch.id, branch.name)
    return f"Got it, {branch.name}."


@function_tool
async def identify_or_create_patient(
    context: RunContext[CallState],
    first_name: str,
    last_name: str,
    date_of_birth: str | None = None,
) -> str:
    """Resolve the caller to a patient record. Call this once you have the
    caller's first and last name (ask for date of birth too if the name is
    common / there's more than one match). Creates a new patient record if
    no existing match is found."""
    state = context.userdata
    client = state.cliniko
    logger.info(
        "[call=%s] identify_or_create_patient called (first_name=%r, last_name=%r, date_of_birth=%r)",
        state.call_id, first_name, last_name, date_of_birth,
    )

    if state.caller_number:
        async with get_session() as session:
            cached = await get_cached_patient(session, state.caller_number)
        if cached:
            state.patient_id = cached.cliniko_patient_id
            state.patient_first_name = cached.first_name
            state.patient_last_name = cached.last_name
            logger.info(
                "[call=%s] identify_or_create_patient: phone cache hit -> patient_id=%s",
                state.call_id, cached.cliniko_patient_id,
            )
            return f"Welcome back, {cached.first_name}. I have you on file already."

    # Cliniko's q[] filter needs an operator between field and value
    # (field:=value), not bare field:value - the latter 400s.
    params = {"q[]": [f"first_name:={first_name}", f"last_name:={last_name}"]}
    logger.info("[call=%s] identify_or_create_patient: searching Cliniko GET /patients %s", state.call_id, params)
    matches = [Patient(**p) async for p in client.paginate("/patients", key="patients", params=params)]
    logger.info("[call=%s] identify_or_create_patient: %d match(es) found", state.call_id, len(matches))

    if date_of_birth:
        matches = [m for m in matches if m.date_of_birth == date_of_birth] or matches

    if len(matches) == 1:
        patient = matches[0]
    elif len(matches) > 1:
        logger.info(
            "[call=%s] identify_or_create_patient: %d ambiguous matches, asking for DOB",
            state.call_id, len(matches),
        )
        return (
            f"I found {len(matches)} patients named {first_name} {last_name}. "
            "Could you confirm the date of birth so I can find the right record?"
        )
    else:
        logger.info("[call=%s] identify_or_create_patient: no match, creating new patient", state.call_id)
        created = await client.post(
            "/patients",
            json={
                "first_name": first_name,
                "last_name": last_name,
                **({"date_of_birth": date_of_birth} if date_of_birth else {}),
                **(
                    {"patient_phone_numbers": [{"phone_type": "Mobile", "number": state.caller_number}]}
                    if state.caller_number
                    else {}
                ),
            },
        )
        patient = Patient(**created)
        logger.info("[call=%s] identify_or_create_patient: created patient_id=%s", state.call_id, patient.id)

    state.patient_id = patient.id
    state.patient_first_name = patient.first_name
    state.patient_last_name = patient.last_name

    if state.caller_number:
        async with get_session() as session:
            await upsert_cached_patient(session, state.caller_number, patient.id, patient.first_name, patient.last_name)

    logger.info("[call=%s] identify_or_create_patient -> patient_id=%s", state.call_id, patient.id)
    return f"Thanks {patient.first_name}, I've found your record."
