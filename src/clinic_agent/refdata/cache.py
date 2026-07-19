"""In-memory reference-data snapshot the agent's browsing tools read from.

A dataclass swap on refresh (`store.update(...)`) means readers never see a
half-updated state, and there's no lock needed for the read path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from clinic_agent.cliniko.models import AppointmentType, Business, Practitioner
from clinic_agent.db.repo import get_refdata
from clinic_agent.db.session import get_session


@dataclass(frozen=True)
class RefDataSnapshot:
    businesses: list[Business] = field(default_factory=list)
    practitioners: list[Practitioner] = field(default_factory=list)
    appointment_types: list[AppointmentType] = field(default_factory=list)
    practitioner_appointment_types: dict[int, list[int]] = field(default_factory=dict)


class RefDataStore:
    """Thread/task-safe-enough for our purposes: a single reference swap."""

    def __init__(self) -> None:
        self._snapshot = RefDataSnapshot()

    def update(self, snapshot: RefDataSnapshot) -> None:
        self._snapshot = snapshot

    @property
    def snapshot(self) -> RefDataSnapshot:
        return self._snapshot

    def businesses(self) -> list[Business]:
        return self._snapshot.businesses

    def business_by_id(self, business_id: int) -> Business | None:
        for b in self._snapshot.businesses:
            if b.id == business_id:
                return b
        return None

    def business_by_name(self, name: str) -> Business | None:
        name_lower = name.strip().lower()
        # A blank query would substring-match every business (`"" in x` is
        # always True) and silently return the first one in the list -
        # treat it as "no match" instead so callers get an explicit prompt.
        if not name_lower:
            return None
        for b in self._snapshot.businesses:
            if name_lower in b.name.lower():
                return b
        return None

    def appointment_types(self) -> list[AppointmentType]:
        return self._snapshot.appointment_types

    def appointment_type_by_name(self, name: str) -> AppointmentType | None:
        name_lower = name.strip().lower()
        if not name_lower:
            return None
        for a in self._snapshot.appointment_types:
            if name_lower in a.name.lower():
                return a
        return None

    def doctors_for(self, appointment_type_id: int | None = None) -> list[Practitioner]:
        """All practitioners, optionally filtered to those bookable for a
        given appointment type. An empty association list for a
        practitioner means "no restriction known" (see refdata/sync.py),
        so they're included rather than excluded."""
        if appointment_type_id is None:
            return self._snapshot.practitioners
        assoc = self._snapshot.practitioner_appointment_types
        return [
            p
            for p in self._snapshot.practitioners
            if not assoc.get(p.id) or appointment_type_id in assoc.get(p.id, [])
        ]

    def practitioner_by_name(self, name: str) -> Practitioner | None:
        name_lower = name.strip().lower()
        if not name_lower:
            return None
        for p in self._snapshot.practitioners:
            if name_lower in p.full_name.lower():
                return p
        return None


async def load_snapshot_from_db() -> RefDataSnapshot:
    """Cold-start path: populate the in-memory store from the last DB
    snapshot so the worker isn't empty-cached before the first refresh
    completes (refresh can take a few seconds across many practitioners)."""
    async with get_session() as session:
        businesses_row = await get_refdata(session, "businesses")
        practitioners_row = await get_refdata(session, "practitioners")
        appointment_types_row = await get_refdata(session, "appointment_types")
        associations_row = await get_refdata(session, "associations")

    return RefDataSnapshot(
        businesses=[Business(**b) for b in (businesses_row.payload["items"] if businesses_row else [])],
        practitioners=[Practitioner(**p) for p in (practitioners_row.payload["items"] if practitioners_row else [])],
        appointment_types=[
            AppointmentType(**a) for a in (appointment_types_row.payload["items"] if appointment_types_row else [])
        ],
        practitioner_appointment_types=(
            {int(k): v for k, v in associations_row.payload["practitioner_appointment_types"].items()}
            if associations_row
            else {}
        ),
    )
