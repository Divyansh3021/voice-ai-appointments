"""One-off manual trigger to prime the reference-data cache before the first
call, so `list_branches`/`list_doctors` aren't empty on a freshly deployed
worker waiting for its first 30-minute refresh cycle.

Usage: python scripts/seed_refdata.py
"""
import asyncio

from clinic_agent.cliniko.client import ClinikoClient
from clinic_agent.config import settings
from clinic_agent.refdata.sync import refresh_refdata


async def main() -> None:
    client = ClinikoClient(settings.cliniko_api_key, settings.cliniko_contact_email)
    try:
        snapshot = await refresh_refdata(client)
    finally:
        await client.aclose()

    print(f"Businesses: {len(snapshot.businesses)}")
    for b in snapshot.businesses:
        print(f"  - [{b.id}] {b.name}")
    print(f"Practitioners: {len(snapshot.practitioners)}")
    for p in snapshot.practitioners:
        print(f"  - [{p.id}] {p.full_name}")
    print(f"Appointment types: {len(snapshot.appointment_types)}")
    for a in snapshot.appointment_types:
        print(f"  - [{a.id}] {a.name}")


if __name__ == "__main__":
    asyncio.run(main())
