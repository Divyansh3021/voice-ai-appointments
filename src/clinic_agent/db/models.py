from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RefDataCache(Base):
    """Periodic snapshot of read-mostly Cliniko reference data (branches,
    practitioners, appointment types, associations). Read at call-time from
    the in-memory copy in refdata/cache.py; this table is what survives a
    worker restart so we're not empty-cached on cold start."""

    __tablename__ = "refdata_cache"
    __table_args__ = (UniqueConstraint("resource_type", name="uq_refdata_resource_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PatientCache(Base):
    """Caller phone number -> Cliniko patient id, built up as callers are
    identified. Lets repeat callers skip the name/DOB verification step."""

    __tablename__ = "patients_cache"

    phone_number: Mapped[str] = mapped_column(String(32), primary_key=True)
    cliniko_patient_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Call(Base):
    """One row per inbound call, for ops visibility and debugging."""

    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    room_name: Mapped[str] = mapped_column(String(255), nullable=False)
    branch_id: Mapped[str | None] = mapped_column(String(64))
    caller_number: Mapped[str | None] = mapped_column(String(32))
    patient_id: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(32))  # booked | rescheduled | cancelled | no_action | error | abandoned
    transcript_summary: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    # Populated by egress if call recording is configured (AZURE_STORAGE_*
    # env vars set) - null otherwise, recording is opt-in.
    recording_url: Mapped[str | None] = mapped_column(Text)
    egress_id: Mapped[str | None] = mapped_column(String(64))


class TranscriptTurn(Base):
    """Full per-turn transcript, independent of the LLM's own end-of-call
    summary (calls.transcript_summary) - the model's self-reported summary
    can be wrong or incomplete, so this is the actual record of what was
    said, written incrementally as the call happens rather than buffered in
    memory (so it survives a mid-call crash)."""

    __tablename__ = "transcript_turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Per-turn latency data straight from LiveKit's ChatMessage.metrics
    # (llm_node_ttft, tts latency, end_of_turn_delay, etc.) when present.
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AppointmentAudit(Base):
    """Every write action against Cliniko, independent of Cliniko's own
    record - so we can answer "what did we actually do" without depending
    on Cliniko's dashboard/history being reachable."""

    __tablename__ = "appointment_audit"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("calls.id"))
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # create | reschedule | cancel | conflict_retry
    cliniko_appointment_id: Mapped[int | None] = mapped_column(BigInteger)
    request_payload: Mapped[dict | None] = mapped_column(JSONB)
    response_status: Mapped[int | None] = mapped_column()
    response_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
