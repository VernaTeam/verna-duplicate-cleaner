# -*- coding: utf-8 -*-
"""Text normalization, locale-aware formatting, and Windows long-path support."""

from __future__ import annotations

import datetime as _dt
import os
import re
import unicodedata

from .jalali import gregorian_to_jalali

# --------------------------------------------------------------- Persian text

# Arabic letter forms folded to their Persian equivalents
_AR_TO_FA = str.maketrans({
    "ي": "ی", "ﻱ": "ی", "ﻲ": "ی",
    "ك": "ک", "ﻙ": "ک", "ﻚ": "ک",
    "ة": "ه", "ۀ": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ؤ": "و", "ئ": "ی",
    "ٲ": "ا", "ٳ": "ا",
})

# Persian and Arabic-Indic digits to Latin
_DIGITS_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Latin digits to Persian, for display only
_DIGITS_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# zero-width joiners, bidi controls and diacritics
_INVISIBLE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff"
    "\u064b-\u0670\u06d6-\u06ed]"
)

# Filler that shows up in downloaded track names and carries no meaning
_NOISE = [
    r"\bofficial\s*(music\s*)?(video|audio|lyrics?|visualizer)\b",
    r"\blyrics?\b",
    r"\b(hq|hd|4k|full\s*hd)\b",
    r"\b(320|256|192|160|128|96|64)\s*k(bps)?\b",
    r"\[\s*(320|256|192|160|128|96|64)\s*\]",
    r"\bfull\s*album\b",
    r"\bremaster(ed)?(\s*\d{4})?\b",
    r"\bexplicit\b",
    r"\bwww\.[\w\-]+\.[a-z]{2,4}\b",
    r"\b[\w\-]{3,}\.(com|ir|net|org|info|me|co|tv)\b",
    r"\b(downloaded|download)\s*(from|by)\b",
    r"\bمیکس\s*شده\b",
    r"\bدانلود\s*(آهنگ|موزیک|از)?\b",
    r"\bکیفیت\s*\d{2,3}\b",
]
_NOISE_RE = re.compile("|".join(_NOISE), re.IGNORECASE)

# "copy" markers at the end of a file name
_COPY_MARKS = [
    r"\(\s*\d{1,3}\s*\)\s*$",
    r"\[\s*\d{1,3}\s*\]\s*$",
    r"[-–_\s]+copy(\s*\(\s*\d+\s*\))?\s*$",
    r"\bcopy\s*of\b",
    r"[-–_\s]+کپی(\s*\(?\s*\d*\s*\)?)?\s*$",
    r"[-–_\s]+نسخه\s*\d*\s*$",
    r"[-–_\s]+duplicate\s*$",
]
_COPY_RE = re.compile("|".join(_COPY_MARKS), re.IGNORECASE)

_PUNCT_RE = re.compile(r"[^\w\s\u0600-\u06ff]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def fa_digits(text) -> str:
    """Latin digits to Persian, for display."""
    return str(text).translate(_DIGITS_TO_FA)


def localize_digits(text, lang: str) -> str:
    return fa_digits(text) if lang == "fa" else str(text)


def normalize_text(s: str) -> str:
    """Fold a string for comparison: letter forms, diacritics, digits, spacing."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_AR_TO_FA)
    s = s.translate(_DIGITS_TO_EN)
    s = _INVISIBLE.sub("", s)
    s = s.casefold()
    s = _NOISE_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _SPACE_RE.sub(" ", s).strip()
    return s


def strip_copy_marks(stem: str) -> str:
    prev = None
    out = stem
    while prev != out:
        prev = out
        out = _COPY_RE.sub("", out).strip(" -–_.")
    return out


def looks_like_copy(stem: str) -> bool:
    return bool(_COPY_RE.search(stem)) or "copy" in stem.casefold() or "کپی" in stem


def name_key(filename: str) -> str:
    """Comparison key for a file name: no extension, no copy markers."""
    stem = os.path.splitext(filename)[0]
    stem = strip_copy_marks(stem)
    return normalize_text(stem)


# ---------------------------------------------------------------- formatting

_UNITS = {
    "fa": ["بایت", "کیلوبایت", "مگابایت", "گیگابایت", "ترابایت"],
    "en": ["B", "KB", "MB", "GB", "TB"],
}


def human_size(n: int | None, lang: str = "fa") -> str:
    if n is None:
        return "—"
    units = _UNITS.get(lang, _UNITS["en"])
    value = float(n)
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    text = f"{value:.0f}" if idx == 0 or value >= 100 else f"{value:.1f}"
    if lang == "fa":
        # Persian uses the Arabic decimal separator
        return fa_digits(text).replace(".", "٫") + " " + units[idx]
    return f"{text} {units[idx]}"


def human_duration(seconds: float | None, lang: str = "fa") -> str:
    if not seconds or seconds <= 0:
        return "—"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return fa_digits(text) if lang == "fa" else text


def human_bitrate(bits: int | None, lang: str = "fa") -> str:
    if not bits:
        return "—"
    kbps = int(bits) // 1000
    return f"{fa_digits(kbps)} kbps" if lang == "fa" else f"{kbps} kbps"


def format_date(timestamp: float, lang: str = "fa") -> str:
    """Jalali with Persian digits in Persian, ISO in English."""
    try:
        when = _dt.datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return "—"
    if lang == "fa":
        jy, jm, jd = gregorian_to_jalali(when.year, when.month, when.day)
        return fa_digits(f"{jy}/{jm:02d}/{jd:02d}")
    return when.strftime("%Y-%m-%d")


def format_datetime(timestamp: float, lang: str = "fa") -> str:
    try:
        when = _dt.datetime.fromtimestamp(timestamp)
    except (OSError, OverflowError, ValueError):
        return "—"
    if lang == "fa":
        jy, jm, jd = gregorian_to_jalali(when.year, when.month, when.day)
        return fa_digits(f"{jy}/{jm:02d}/{jd:02d} {when:%H:%M}")
    return when.strftime("%Y-%m-%d %H:%M")


def human_eta(seconds: float | None, lang: str = "fa") -> str:
    """Rough remaining time, rounded to something a person would say."""
    if not seconds or seconds <= 0:
        return ""
    total = int(seconds)
    if total < 60:
        n = max(5, (total // 5) * 5)
        return f"{fa_digits(n)} ثانیه" if lang == "fa" else f"{n} seconds"
    if total < 3600:
        n = max(1, round(total / 60))
        return f"{fa_digits(n)} دقیقه" if lang == "fa" else (
            "1 minute" if n == 1 else f"{n} minutes")
    n = round(total / 3600, 1)
    text = f"{n:g}"
    return f"{fa_digits(text)} ساعت" if lang == "fa" else f"{text} hours"


# ------------------------------------------------------------ Windows paths

def short_folder(folder: str, segments: int = 2) -> str:
    """Last few path segments, e.g. "…\\MusicDemo\\Unsorted".

    Shortening in Python rather than clipping with CSS keeps the column narrow
    and reads the same in both text directions — an LTR path truncated inside
    an RTL table otherwise hides whichever end the browser feels like.
    """
    if not folder:
        return ""
    parts = [p for p in folder.replace("/", "\\").split("\\") if p]
    if len(parts) <= segments:
        return folder
    return "…\\" + "\\".join(parts[-segments:])


def long_path(path: str) -> str:
    r"""Prefix \\?\ for paths past the Windows MAX_PATH limit."""
    if os.name != "nt":
        return path
    if len(path) < 240 or path.startswith("\\\\?\\"):
        return path
    abs_path = os.path.abspath(path)
    if abs_path.startswith("\\\\"):
        return "\\\\?\\UNC" + abs_path[1:]
    return "\\\\?\\" + abs_path
