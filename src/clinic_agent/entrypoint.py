"""LiveKit agent worker entrypoint. Run with:
    python -m clinic_agent.entrypoint dev    # local, LiveKit Playground
    python -m clinic_agent.entrypoint start  # production
"""
from __future__ import annotations

import asyncio
import logging

from livekit import rtc
from livekit.agents import (
    AgentSession,
    JobContext,
    JobProcess,
    RoomInputOptions,
    WorkerOptions,
    cli,
)
from livekit.plugins import azure, noise_cancellation, openai, silero
from livekit.plugins.azure.tts import ProsodyConfig
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from clinic_agent.agent import ReceptionistAgent
from clinic_agent.alerting import install_alert_handler
from clinic_agent.cliniko.client import ClinikoClient
from clinic_agent.config import settings
from clinic_agent.db.repo import end_call, log_transcript_turn, set_call_recording, start_call
from clinic_agent.db.session import get_session, reset_engine_for_current_loop
from clinic_agent.logging_setup import install_file_handler
from clinic_agent.recording import start_recording, stop_recording
from clinic_agent.refdata.cache import RefDataStore, load_snapshot_from_db
from clinic_agent.refdata.sync import refresh_loop, refresh_refdata
from clinic_agent.state import CallState

logger = logging.getLogger("clinic_agent")


def prewarm(proc: JobProcess) -> None:
    # The one model load worth doing once per worker process rather than
    # per call - everything else (STT/LLM/TTS clients) is cheap to construct.
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext) -> None:
    # Must happen before any get_session() call this job makes (including
    # the ones a moment away in load_snapshot_from_db). See
    # reset_engine_for_current_loop's docstring - a worker process handles
    # many calls across its lifetime, each on its own event loop, and the
    # DB engine has to be rebound to whichever loop is running right now.
    reset_engine_for_current_loop()

    await ctx.connect()

    vad = ctx.proc.userdata["vad"]

    cliniko = ClinikoClient(settings.cliniko_api_key, settings.cliniko_contact_email)
    refdata = RefDataStore()

    # Cold start: load whatever we synced last time so the first call of a
    # freshly-deployed worker isn't stuck with an empty branch/doctor list.
    refdata.update(await load_snapshot_from_db())
    # Then kick a synchronous refresh once (in case this is a truly first
    # run with nothing cached yet) before starting the periodic background loop.
    try:
        refdata.update(await refresh_refdata(cliniko))
    except Exception:
        logger.exception("initial refdata refresh failed; continuing with DB snapshot (if any)")
    refresh_task = asyncio.create_task(refresh_loop(cliniko, refdata))

    caller_number = await _wait_for_caller_number(ctx.room)

    async with get_session() as session:
        call_id = await start_call(session, room_name=ctx.room.name, caller_number=caller_number)

    recording = await start_recording(ctx.room.name)
    if recording is not None:
        egress_id, recording_url = recording
        async with get_session() as session:
            await set_call_recording(session, call_id, recording_url, egress_id)

    state = CallState(
        room_name=ctx.room.name,
        caller_number=caller_number,
        cliniko=cliniko,
        refdata=refdata,
        call_id=call_id,
    )

    session_ = AgentSession[CallState](
        userdata=state,
        vad=vad,
        # Azure STT auto-detects between the candidate locales per utterance,
        # which is what actually gives us Hindi/English code-switch support
        # without needing Sarvam for the first iteration.
        stt=azure.STT(
            speech_key=settings.azure_speech_key,
            speech_region=settings.azure_speech_region,
            language=["en-IN", "hi-IN"],
        ),
        llm=openai.LLM.with_azure(
            model=settings.azure_openai_deployment,
            azure_deployment=settings.azure_openai_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            temperature=0.3,
        ),
        tts=azure.TTS(
            speech_key=settings.azure_speech_key,
            speech_region=settings.azure_speech_region,
            voice="hi-IN-SwaraNeural",  # female voice, matches the persona in agent.py's prompt
            prosody=ProsodyConfig(rate=1.05),  # a bit brisker than default - less draggy on a phone call
        ),
        turn_detection=MultilingualModel(),
        max_tool_steps=8,
    )

    def _on_conversation_item(ev) -> None:
        # Fired synchronously by the session's event emitter, so the actual
        # DB write is farmed out to a task rather than awaited inline here.
        # Full transcript, independent of the LLM's own end-of-call summary
        # (state.outcome/summary can be wrong or incomplete) - written
        # incrementally per turn so it survives a mid-call crash rather than
        # being buffered in memory until the end.
        item = ev.item
        if getattr(item, "type", None) != "message":
            return  # skip AgentHandoff/other non-chat-message items
        text = item.text_content
        if not text:
            return
        metrics = dict(item.metrics) if item.metrics else None

        async def _persist() -> None:
            async with get_session() as session:
                await log_transcript_turn(session, call_id, role=item.role, content=text, metrics=metrics)

        asyncio.create_task(_persist())

    session_.on("conversation_item_added", _on_conversation_item)

    async def _on_shutdown() -> None:
        refresh_task.cancel()
        async with get_session() as session:
            await end_call(
                session,
                call_id,
                outcome=state.outcome,
                branch_id=str(state.branch_id) if state.branch_id else None,
                patient_id=state.patient_id,
                error_detail=None,
            )
        if recording is not None:
            await stop_recording(recording[0])
        await cliniko.aclose()

    ctx.add_shutdown_callback(_on_shutdown)

    await session_.start(
        agent=ReceptionistAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVCTelephony()),
    )

    # Speak first rather than waiting for the caller to say something -
    # instructions just point back at the prompt's own opening-line
    # guidance (agent.py) so the actual wording has one source of truth.
    session_.generate_reply(instructions="Greet the caller now, following your opening line guidance.")


async def _wait_for_caller_number(room: rtc.Room, timeout_seconds: float = 3.0) -> str | None:
    """Standard LiveKit SIP attributes carry the caller's ANI once the SIP
    participant has joined. The SIP participant is usually already present
    by job dispatch, but poll briefly in case it lands a beat late rather
    than silently identifying every caller as unknown."""

    def _find() -> str | None:
        for participant in room.remote_participants.values():
            number = participant.attributes.get("sip.phoneNumber")
            # LiveKit's local console/dev test harness (mock_room.py) uses an
            # unconfigured autospec mock for a fake participant's attributes,
            # so .get() can return a MagicMock rather than None outside of a
            # real SIP call - guard against writing that into Postgres.
            if isinstance(number, str) and number:
                return number
        return None

    elapsed = 0.0
    interval = 0.25
    while elapsed < timeout_seconds:
        number = _find()
        if number:
            return number
        await asyncio.sleep(interval)
        elapsed += interval
    return None


if __name__ == "__main__":
    # Once per process, not per job - the alert handler is a plain
    # background thread, not tied to any particular asyncio event loop, so
    # it keeps working correctly across every job this worker process
    # handles over its lifetime (unlike the DB engine - see
    # reset_engine_for_current_loop).
    install_alert_handler(settings.alert_webhook_url, source="agent-worker")
    install_file_handler("agent-worker", log_dir=settings.log_dir)

    # Passed explicitly rather than relying on the LiveKit CLI's own
    # os.environ lookup: pydantic-settings reads .env into our Settings
    # object without exporting those values to the actual process
    # environment, so LIVEKIT_URL etc. would otherwise appear unset here
    # even though .env has them.
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            ws_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
    )
