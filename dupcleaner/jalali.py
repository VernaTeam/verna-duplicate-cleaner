"""Gregorian to Jalali (Solar Hijri) date conversion, and Persian time phrasing.

Small and dependency-free on purpose: the alternatives pull in a package for
what is a fixed arithmetic conversion, and the server has 957MB of RAM shared
by seven services.

The algorithm is the standard 33-year-cycle conversion. It is verified in the
self-test against known anchor dates rather than trusted — an off-by-one here
would misdate every workout in the history.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

MONTHS = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)

_EN_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

_G_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_J_DAYS_IN_MONTH = (31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29)


def to_persian_digits(text: str) -> str:
    return str(text).translate(_EN_TO_FA)


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    """(year, month, day) Gregorian -> (year, month, day) Jalali."""
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1

    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    for i in range(gm2):
        g_day_no += _G_DAYS_IN_MONTH[i]
    # leap year, and we are past February
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        g_day_no += 1
    g_day_no += gd2

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    jm = 0
    for i in range(11):
        if j_day_no < _J_DAYS_IN_MONTH[i]:
            jm = i + 1
            break
        j_day_no -= _J_DAYS_IN_MONTH[i]
    else:
        jm = 12

    return jy, jm, j_day_no + 1


def parse_stored(value: str | None) -> datetime | None:
    """Parse a datetime as SQLite's datetime('now') writes it (UTC)."""
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
    return None


def format_date(value: str | datetime | None, *, with_month_name: bool = True) -> str:
    """'۱۶ مرداد ۱۴۰۵', or '۱۴۰۵/۰۵/۱۶' when with_month_name is False."""
    moment = parse_stored(value) if not isinstance(value, datetime) else value
    if moment is None:
        return "—"
    jy, jm, jd = gregorian_to_jalali(moment.year, moment.month, moment.day)
    if with_month_name:
        return to_persian_digits(f"{jd} {MONTHS[jm - 1]} {jy}")
    return to_persian_digits(f"{jy}/{jm:02d}/{jd:02d}")


def relative(value: str | datetime | None, *, now: datetime | None = None) -> str:
    """'امروز', 'دیروز', '۳ روز پیش', or the date once that stops being useful."""
    moment = parse_stored(value) if not isinstance(value, datetime) else value
    if moment is None:
        return "—"
    now = now or datetime.now(timezone.utc)

    days = (now.date() - moment.date()).days
    if days < 0:
        return format_date(moment)
    if days == 0:
        return "امروز"
    if days == 1:
        return "دیروز"
    if days < 7:
        return to_persian_digits(f"{days} روز پیش")
    if days < 30:
        weeks = days // 7
        return to_persian_digits(f"{weeks} هفته پیش")
    return format_date(moment)


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("jalali.py self-test")

    # Anchors: each is a Persian new year, which is the easiest date to be
    # certain about, plus a few ordinary dates.
    anchors = [
        ((2026, 3, 21), (1405, 1, 1)),
        ((2025, 3, 21), (1404, 1, 1)),
        ((2024, 3, 20), (1403, 1, 1)),
        ((2021, 3, 21), (1400, 1, 1)),
        ((2000, 3, 20), (1379, 1, 1)),
        ((2026, 8, 7), (1405, 5, 16)),
        ((2026, 12, 31), (1405, 10, 10)),
        ((2026, 1, 1), (1404, 10, 11)),
        ((2024, 2, 29), (1402, 12, 10)),   # Gregorian leap day
    ]
    for gregorian, expected in anchors:
        got = gregorian_to_jalali(*gregorian)
        status = "OK " if got == expected else "FAIL"
        print(f"  {status} {gregorian} -> {got}  (expected {expected})")
        assert got == expected, f"{gregorian}: got {got}, expected {expected}"

    # A year of consecutive days must never skip or repeat a Jalali date.
    seen = set()
    day = date(2026, 3, 21)
    previous = None
    for _ in range(366):
        current = gregorian_to_jalali(day.year, day.month, day.day)
        assert current not in seen, f"duplicate Jalali date at {day}: {current}"
        seen.add(current)
        assert 1 <= current[1] <= 12 and 1 <= current[2] <= 31, current
        if previous is not None:
            # month must never go backwards inside one Jalali year
            if current[0] == previous[0]:
                assert current[1] >= previous[1], f"{previous} -> {current}"
        previous = current
        day = date.fromordinal(day.toordinal() + 1)
    print(f"  366 consecutive days: {len(seen)} distinct Jalali dates, no gaps")

    print("  format:", format_date("2026-08-07 09:30:00"))
    print("  short :", format_date("2026-08-07 09:30:00", with_month_name=False))
    assert format_date("2026-08-07 09:30:00") == "۱۶ مرداد ۱۴۰۵"

    now = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)
    cases = {
        "2026-08-07 08:00:00": "امروز",
        "2026-08-06 08:00:00": "دیروز",
        "2026-08-04 08:00:00": "۳ روز پیش",
        "2026-07-28 08:00:00": "۱ هفته پیش",
    }
    for stored, expected in cases.items():
        got = relative(stored, now=now)
        print(f"  relative({stored[:10]}) -> {got}")
        assert got == expected, f"{stored}: got {got!r}, expected {expected!r}"

    # far past falls back to a real date rather than an absurd "N weeks"
    assert relative("2025-01-01 08:00:00", now=now) == format_date("2025-01-01")
    assert relative(None) == "—" and format_date(None) == "—"
    assert relative("not a date") == "—"
    print("  malformed input -> em dash OK")
    print("OK")
