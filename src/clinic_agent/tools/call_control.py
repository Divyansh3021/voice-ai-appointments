from __future__ import annotations

import logging

from livekit.agents import RunContext, function_tool, get_job_context

from clinic_agent.db.repo import end_call as record_call_end
from clinic_agent.db.session import get_session
from clinic_agent.state import CallState

logger = logging.getLogger(__name__)


@function_tool
async def end_call(context: RunContext[CallState], summary: str) -> str:
    """Wrap up and end the call once you've already said a warm goodbye to
    the caller. `summary` is a short note of what happened, for the call
    log - e.g. 'booked with Dr. Sharma for Tuesday 4pm'."""
    state = context.userdata
    logger.info(
        "[call=%s] end_call called (outcome=%s, branch_id=%s, patient_id=%s, summary=%r)",
        state.call_id, state.outcome, state.branch_id, state.patient_id, summary,
    )

    if state.call_id is not None:
        async with get_session() as session:
            await record_call_end(
                session,
                state.call_id,
                outcome=state.outcome,
                transcript_summary=summary,
                branch_id=str(state.branch_id) if state.branch_id else None,
                patient_id=state.patient_id,
            )

    # The room used to be deleted immediately here, which could cut the
    # caller's goodbye off mid-sentence - TTS playback runs concurrently
    # with tool execution, not strictly before it. Wait for the farewell
    # the agent just said (per the prompt's instruction to say goodbye
    # before calling this tool) to actually finish playing before tearing
    # the room down. Must be RunContext.wait_for_playout(), not
    # SpeechHandle.wait_for_playout() - the latter waits for this whole
    # turn (including end_call itself) to finish, which is exactly this
    # tool call, so it deadlocks waiting on itself.
    await context.wait_for_playout()
    logger.info("[call=%s] end_call: farewell playout complete, deleting room", state.call_id)

    job_ctx = get_job_context()
    job_ctx.delete_room()  # ends the call by tearing down the LiveKit room

    return "Call ended."
