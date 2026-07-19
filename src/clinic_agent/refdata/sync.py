"""Pulls branches/practitioners/appointment-types (and their associations)
from Cliniko into Postgres + an in-memory snapshot, on a timer.

This is the ONLY place mid-call browsing tools (list_branches, list_doctors,
list_appointment_types) indirectly depend on - they read the in-memory
RefDataStore, never Cliniko directly, which is what keeps us well under the
200 req/min budget regardless of how many calls are active concurrently.
"""
from __future__ import annotations

import asyncio
import logging

from clinic_agent.cliniko.client import ClinikoClient
from clinic_agent.cliniko.errors import ClinikoNotFound
from clinic_agent.cliniko.models import AppointmentType, Business, Practitioner
from clinic_agent.db.repo import upsert_refdata
from clinic_agent.db.session import get_session
from clinic_agent.refdata.cache import RefDataSnapshot, RefDataStore

logger = logging.getLogger(__name__)

DEFAULT_REFRESH_INTERVAL_SECONDS = 30 * 60


async def _fetch_practitioner_appointment_types(
    client: ClinikoClient, practitioner_id: int
) -> list[int]:
    """Best-effort: the exact association endpoint shape wasn't confirmed
    against live docs, only inferred. If Cliniko doesn't expose this nested
    resource the way we expect, degrade to "this practitioner can be booked
    for any appointment type" rather than hard-failing the whole sync -
    a permissive default is safer than silently hiding a doctor."""
    try:
        ids = []
        async for item in client.paginate(
            f"/practitioners/{practitioner_id}/appointment_types", key="appointment_types"
        ):
            # Cliniko returns id as a JSON string (e.g. "1996495195331374163"),
            # but AppointmentType.id is int - compared against int elsewhere
            # (doctors_for's `appointment_type_id in assoc[...]` check), so a
            # str/int mismatch here silently made every practitioner look
            # unbookable for every appointment type. Coerce to match.
            ids.append(int(item["id"]))
        return ids
    except ClinikoNotFound:
        logger.warning(
            "practitioner %s appointment_types association endpoint not found; "
            "treating them as bookable for all appointment types",
            practitioner_id,
        )
        return []  # empty sentinel -> cache.py treats [] as "no restriction known"


async def refresh_refdata(client: ClinikoClient) -> RefDataSnapshot:
    businesses = [Business(**b) async for b in client.paginate("/businesses", key="businesses")]
    practitioners = [
        Practitioner(**p) async for p in client.paginate("/practitioners", key="practitioners")
    ]
    appointment_types = [
        AppointmentType(**a) async for a in client.paginate("/appointment_types", key="appointment_types")
    ]

    practitioner_appointment_types: dict[int, list[int]] = {}
    for practitioner in practitioners:
        practitioner_appointment_types[practitioner.id] = await _fetch_practitioner_appointment_types(
            client, practitioner.id
        )

    snapshot = RefDataSnapshot(
        businesses=businesses,
        practitioners=practitioners,
        appointment_types=appointment_types,
        practitioner_appointment_types=practitioner_appointment_types,
    )

    async with get_session() as session:
        await upsert_refdata(session, "businesses", {"items": [b.model_dump(mode="json") for b in businesses]})
        await upsert_refdata(session, "practitioners", {"items": [p.model_dump(mode="json") for p in practitioners]})
        await upsert_refdata(
            session, "appointment_types", {"items": [a.model_dump(mode="json") for a in appointment_types]}
        )
        await upsert_refdata(session, "associations", {"practitioner_appointment_types": practitioner_appointment_types})

    return snapshot


async def refresh_loop(client: ClinikoClient, store: RefDataStore, interval_seconds: int = DEFAULT_REFRESH_INTERVAL_SECONDS) -> None:
    """Runs forever as a background task on the worker process."""
    while True:
        try:
            snapshot = await refresh_refdata(client)
            store.update(snapshot)
            logger.info(
                "refdata refreshed: %d businesses, %d practitioners, %d appointment types",
                len(snapshot.businesses), len(snapshot.practitioners), len(snapshot.appointment_types),
            )
        except Exception:
            logger.exception("refdata refresh failed; keeping previous snapshot")
        await asyncio.sleep(interval_seconds)
