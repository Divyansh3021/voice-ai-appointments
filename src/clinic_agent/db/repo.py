"""Thin repository functions - the only place tools/ and refdata/ talk SQL."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from clinic_agent.db.models import AppointmentAudit, Call, PatientCache, RefDataCache, TranscriptTurn


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- patients_cache ---------------------------------------------------

async def get_cached_patient(session: AsyncSession, phone_number: str) -> PatientCache | None:
    return await session.get(PatientCache, phone_number)


async def upsert_cached_patient(
    session: AsyncSession,
    phone_number: str,
    cliniko_patient_id: int,
    first_name: str | None,
    last_name: str | None,
) -> None:
    stmt = pg_insert(PatientCache).values(
        phone_number=phone_number,
        cliniko_patient_id=cliniko_patient_id,
        first_name=first_name,
        last_name=last_name,
        last_confirmed_at=_now(),
        created_at=_now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[PatientCache.phone_number],
        set_={
            "cliniko_patient_id": cliniko_patient_id,
            "first_name": first_name,
            "last_name": last_name,
            "last_confirmed_at": _now(),
        },
    )
    await session.execute(stmt)
    await session.commit()


# --- refdata_cache -------------------------------------------------------

async def get_refdata(session: AsyncSession, resource_type: str) -> RefDataCache | None:
    result = await session.execute(select(RefDataCache).where(RefDataCache.resource_type == resource_type))
    return result.scalar_one_or_none()


async def upsert_refdata(session: AsyncSession, resource_type: str, payload: dict) -> None:
    stmt = pg_insert(RefDataCache).values(
        resource_type=resource_type,
        payload=payload,
        fetched_at=_now(),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[RefDataCache.resource_type],
        set_={"payload": payload, "fetched_at": _now()},
    )
    await session.execute(stmt)
    await session.commit()


# --- calls -----------------------------------------------------------------

async def start_call(session: AsyncSession, room_name: str, caller_number: str | None) -> uuid.UUID:
    call = Call(room_name=room_name, caller_number=caller_number, started_at=_now())
    session.add(call)
    await session.commit()
    return call.id


async def end_call(
    session: AsyncSession,
    call_id: uuid.UUID,
    outcome: str,
    transcript_summary: str | None = None,
    branch_id: str | None = None,
    patient_id: int | None = None,
    error_detail: str | None = None,
) -> None:
    call = await session.get(Call, call_id)
    if call is None:
        return
    call.ended_at = _now()
    call.outcome = outcome
    call.transcript_summary = transcript_summary
    if branch_id:
        call.branch_id = branch_id
    if patient_id:
        call.patient_id = patient_id
    if error_detail:
        call.error_detail = error_detail
    await session.commit()


async def set_call_recording(session: AsyncSession, call_id: uuid.UUID, recording_url: str, egress_id: str) -> None:
    call = await session.get(Call, call_id)
    if call is None:
        return
    call.recording_url = recording_url
    call.egress_id = egress_id
    await session.commit()


# --- transcript_turns --------------------------------------------------

async def log_transcript_turn(
    session: AsyncSession,
    call_id: uuid.UUID,
    role: str,
    content: str,
    metrics: dict | None = None,
) -> None:
    session.add(
        TranscriptTurn(
            call_id=call_id,
            role=role,
            content=content,
            metrics=metrics,
            created_at=_now(),
        )
    )
    await session.commit()


async def get_transcript(session: AsyncSession, call_id: uuid.UUID) -> list[TranscriptTurn]:
    result = await session.execute(
        select(TranscriptTurn).where(TranscriptTurn.call_id == call_id).order_by(TranscriptTurn.created_at)
    )
    return list(result.scalars().all())


# --- appointment_audit -----------------------------------------------------

async def log_appointment_action(
    session: AsyncSession,
    call_id: uuid.UUID | None,
    action: str,
    cliniko_appointment_id: int | None,
    request_payload: dict | None,
    response_status: int | None,
    response_payload: dict | None,
) -> None:
    session.add(
        AppointmentAudit(
            call_id=call_id,
            action=action,
            cliniko_appointment_id=cliniko_appointment_id,
            request_payload=request_payload,
            response_status=response_status,
            response_payload=response_payload,
            created_at=_now(),
        )
    )
    await session.commit()
