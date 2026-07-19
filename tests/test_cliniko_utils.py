from datetime import datetime, timedelta, timezone

from clinic_agent.cliniko.utils import to_cliniko_utc


def test_to_cliniko_utc_uses_z_suffix_not_offset():
    dt = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)
    assert to_cliniko_utc(dt) == "2026-07-18T08:00:00Z"


def test_to_cliniko_utc_strips_microseconds():
    dt = datetime(2026, 7, 18, 8, 0, 30, 776216, tzinfo=timezone.utc)
    assert to_cliniko_utc(dt) == "2026-07-18T08:00:30Z"


def test_to_cliniko_utc_converts_non_utc_timezone_to_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    dt = datetime(2026, 7, 18, 13, 30, tzinfo=ist)  # 13:30 IST == 08:00 UTC
    assert to_cliniko_utc(dt) == "2026-07-18T08:00:00Z"
