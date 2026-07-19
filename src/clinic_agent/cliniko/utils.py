"""Small formatting helpers for talking to Cliniko's API."""
from __future__ import annotations

from datetime import datetime, timezone


def to_cliniko_utc(dt: datetime) -> str:
    """Cliniko requires an explicit UTC timestamp with a literal 'Z' suffix -
    it rejects the '+00:00' offset notation Python's datetime.isoformat()
    produces by default. Confirmed empirically against the real API: a
    'Z'-suffixed string succeeds, the identical instant written as '+00:00'
    400s with {"message": "Timestamp needs to be in UTC format."}. Also
    drops microseconds, which Cliniko doesn't need and never returns itself.
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
