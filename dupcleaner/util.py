# -*- coding: utf-8 -*-
"""ابزارهای عمومی: نرمال‌سازی متن فارسی، نمایش حجم و زمان، مسیرهای طولانی ویندوز."""

from __future__ import annotations

import os
import re
import unicodedata

# ---------------------------------------------------------------- متن فارسی

# حروف عربی که باید به معادل فارسی تبدیل شوند
_AR_TO_FA = str.maketrans({
    "ي": "ی", "ﻱ": "ی", "ﻲ": "ی", "ی": "ی",
    "ك": "ک", "ﻙ": "ک", "ﻚ": "ک",
    "ة": "ه", "ۀ": "ه",
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ؤ": "و", "ئ": "ی",
    "ٲ": "ا", "ٳ": "ا",
})

# ارقام فارسی و عربی -> لاتین
_DIGITS_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# ارقام لاتین -> فارسی (فقط برای نمایش در رابط کاربری)
_DIGITS_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

# نویسه‌های نامرئی: نیم‌فاصله، کنترل جهت، اعراب
_INVISIBLE = re.compile(
    "[​‌‍‎‏‪-‮⁦-⁩﻿"
    "ً-ٰٟۖ-ۭ]"
)

# عبارت‌هایی که در نام آهنگ‌های دانلودی زیاد دیده می‌شوند و معنایی ندارند
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

# نشانه‌های «کپی» در نام فایل
_COPY_MARKS = [
    r"\(\s*\d{1,3}\s*\)\s*$",          # song (1)
    r"\[\s*\d{1,3}\s*\]\s*$",          # song [1]
    r"[-–_\s]+copy(\s*\(\s*\d+\s*\))?\s*$",
    r"\bcopy\s*of\b",
    r"[-–_\s]+کپی(\s*\(?\s*\d*\s*\)?)?\s*$",
    r"[-–_\s]+نسخه\s*\d*\s*$",
    r"[-–_\s]+duplicate\s*$",
    r"\s*-\s*نسخه\s*کپی\s*$",
]
_COPY_RE = re.compile("|".join(_COPY_MARKS), re.IGNORECASE)

_PUNCT_RE = re.compile(r"[^\w\s؀-ۿ]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def fa_digits(text) -> str:
    """ارقام لاتین را برای نمایش به فارسی تبدیل می‌کند."""
    return str(text).translate(_DIGITS_TO_FA)


def normalize_text(s: str) -> str:
    """متن را برای مقایسه یکدست می‌کند (عربی/فارسی، اعراب، ارقام، فاصله)."""
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
    """نشانه‌های «کپی»/«(۱)» را از انتهای نام فایل حذف می‌کند."""
    prev = None
    out = stem
    while prev != out:
        prev = out
        out = _COPY_RE.sub("", out).strip(" -–_.")
    return out


def looks_like_copy(stem: str) -> bool:
    """آیا نام فایل نشانهٔ کپی بودن دارد؟"""
    return bool(_COPY_RE.search(stem)) or "copy" in stem.casefold() or "کپی" in stem


def name_key(filename: str) -> str:
    """کلید مقایسهٔ نام فایل، بدون پسوند و بدون نشانه‌های کپی."""
    stem = os.path.splitext(filename)[0]
    stem = strip_copy_marks(stem)
    return normalize_text(stem)


# ---------------------------------------------------------------- نمایش

_UNITS = ["بایت", "کیلوبایت", "مگابایت", "گیگابایت", "ترابایت"]


def human_size(n: int, persian: bool = True) -> str:
    """حجم را به صورت خوانا برمی‌گرداند."""
    if n is None:
        return "-"
    value = float(n)
    idx = 0
    while value >= 1024 and idx < len(_UNITS) - 1:
        value /= 1024.0
        idx += 1
    text = f"{value:.0f}" if idx == 0 or value >= 100 else f"{value:.1f}"
    text = f"{text} {_UNITS[idx]}"
    return fa_digits(text) if persian else text


def human_duration(seconds: float | None, persian: bool = True) -> str:
    """مدت زمان را به شکل m:ss یا h:mm:ss برمی‌گرداند."""
    if not seconds or seconds <= 0:
        return "-"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return fa_digits(text) if persian else text


# ---------------------------------------------------------------- مسیر ویندوز

def long_path(path: str) -> str:
    """برای مسیرهای بلندتر از حد ویندوز، پیشوند \\\\?\\ اضافه می‌کند."""
    if os.name != "nt":
        return path
    if len(path) < 240 or path.startswith("\\\\?\\"):
        return path
    abs_path = os.path.abspath(path)
    if abs_path.startswith("\\\\"):
        return "\\\\?\\UNC" + abs_path[1:]
    return "\\\\?\\" + abs_path


def short_folder(path: str, max_len: int = 60) -> str:
    """مسیر پوشه را برای نمایش کوتاه می‌کند."""
    folder = os.path.dirname(path)
    if len(folder) <= max_len:
        return folder
    return folder[:20] + " … " + folder[-(max_len - 23):]
