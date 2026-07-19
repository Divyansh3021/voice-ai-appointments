"""Per-call state, carried on the LiveKit AgentSession's userdata for the
life of one phone call. This is the only state that crosses tool-call
boundaries - each tool reads/writes it directly rather than re-deriving
context from the transcript."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from clinic_agent.cliniko.client import ClinikoClient
from clinic_agent.cliniko.models import Appointment
from clinic_agent.refdata.cache import RefDataStore


@dataclass
class BookingDraft:
    """What the caller has confirmed so far for an in-progress booking, so
    partial progress survives across several conversational turns."""

    branch_id: int | None = None
    practitioner_id: int | None = None
    appointment_type_id: int | None = None


@dataclass
class CallState:
    room_name: str
    caller_number: str | None
    cliniko: ClinikoClient
    refdata: RefDataStore

    call_id: uuid.UUID | None = None
    branch_id: int | None = None
    branch_name: str | None = None

    patient_id: int | None = None
    patient_first_name: str | None = None
    patient_last_name: str | None = None

    draft: BookingDraft | None = None
    outcome: str = "no_action"

    # Appointments this call has already fetched via find_upcoming_appointments,
    # keyed by id, so reschedule/cancel don't need to re-query Cliniko just to
    # resolve "the Tuesday one". Scoped to this call - never shared across calls.
    known_appointments: dict[int, Appointment] = None  # type: ignore[assignment]

    # Slots most recently read out by check_availability, keyed by the small
    # spoken number ("option 1", "option 2") the caller picks from. The LLM
    # passes that number back to book_appointment/reschedule_appointment
    # rather than an ISO timestamp it would otherwise have to reconstruct
    # itself from spoken text - which was silently producing wrong/ambiguous
    # timestamps (no timezone, hand-typed by the model instead of copied).
    offered_slots: dict[int, datetime] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.known_appointments is None:
            self.known_appointments = {}
        if self.offered_slots is None:
            self.offered_slots = {}
