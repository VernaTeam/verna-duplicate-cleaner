# -*- coding: utf-8 -*-
"""خواندن تگ‌های موسیقی و پیدا کردن محدودهٔ «محتوای صوتی» فایل.

نکتهٔ مهم: دو نسخه از یک آهنگ که فقط تگ‌هایشان فرق دارد، بایت‌های صوتی
یکسانی دارند. با نادیده گرفتن بخش تگ و هش گرفتن از بقیهٔ فایل، این دو
به‌عنوان تکراری قطعی شناسایی می‌شوند — کاری که مقایسهٔ ساده انجام نمی‌دهد.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .util import long_path, normalize_text

try:
    import mutagen
    HAVE_MUTAGEN = True
except Exception:  # pragma: no cover - محیط بدون mutagen
    mutagen = None
    HAVE_MUTAGEN = False


AUDIO_EXTS = {
    ".mp3", ".flac", ".m4a", ".mp4a", ".aac", ".ogg", ".oga", ".opus",
    ".wav", ".wma", ".alac", ".aiff", ".aif", ".ape", ".wv", ".mpc",
    ".m4b", ".mka", ".dsf", ".tta",
}

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic", ".raw", ".cr2", ".nef"}


@dataclass
class AudioMeta:
    """اطلاعات استخراج‌شده از یک فایل صوتی."""
    artist: str = ""
    title: str = ""
    album: str = ""
    track: str = ""
    duration: float = 0.0        # ثانیه
    bitrate: int = 0             # بیت بر ثانیه
    sample_rate: int = 0
    channels: int = 0
    has_cover: bool = False
    tag_fields: int = 0          # تعداد فیلدهای پرشده — برای انتخاب بهترین نسخه
    error: str = ""

    @property
    def has_tags(self) -> bool:
        return bool(self.title.strip())

    @property
    def display(self) -> str:
        if self.artist and self.title:
            return f"{self.artist} — {self.title}"
        return self.title or self.artist or ""

    def tag_key(self) -> str:
        """کلید مقایسه بر اساس آرتیست + عنوان."""
        a, t = normalize_text(self.artist), normalize_text(self.title)
        if not t:
            return ""
        return f"{a}|{t}" if a else ""

    def title_key(self) -> str:
        """کلید مقایسه فقط بر اساس عنوان (وقتی آرتیست خالی است)."""
        t = normalize_text(self.title)
        return t if len(t) >= 3 else ""


_EASY_KEYS = {
    "artist": ("artist", "albumartist", "performer", "TPE1", "©ART", "Author"),
    "title": ("title", "TIT2", "©nam", "Title"),
    "album": ("album", "TALB", "©alb", "WM/AlbumTitle"),
    "track": ("tracknumber", "TRCK", "trkn"),
}


def _first(value) -> str:
    """اولین مقدار یک فیلد تگ را به رشته تبدیل می‌کند."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, (int, float)):
        return str(value)
    try:
        text = str(value)
    except Exception:
        return ""
    return text.strip()


def read_meta(path: str) -> AudioMeta:
    """تگ‌ها و مشخصات فنی فایل صوتی را می‌خواند. هرگز استثنا پرتاب نمی‌کند."""
    meta = AudioMeta()
    if not HAVE_MUTAGEN:
        meta.error = "mutagen نصب نیست"
        return meta
    try:
        audio = mutagen.File(long_path(path), easy=True)
        if audio is None:
            meta.error = "قالب ناشناخته"
            return meta

        tags = audio.tags or {}
        for field_name, keys in _EASY_KEYS.items():
            for key in keys:
                if key in tags:
                    text = _first(tags[key])
                    if text:
                        setattr(meta, field_name, text)
                        break

        info = getattr(audio, "info", None)
        if info is not None:
            meta.duration = float(getattr(info, "length", 0.0) or 0.0)
            meta.bitrate = int(getattr(info, "bitrate", 0) or 0)
            meta.sample_rate = int(getattr(info, "sample_rate", 0) or 0)
            meta.channels = int(getattr(info, "channels", 0) or 0)

        meta.tag_fields = sum(
            1 for v in (meta.artist, meta.title, meta.album, meta.track) if v
        )
        meta.has_cover = _detect_cover(path)
    except Exception as exc:
        meta.error = type(exc).__name__
    return meta


def _detect_cover(path: str) -> bool:
    """آیا فایل کاور جاسازی‌شده دارد؟ (برای انتخاب بهترین نسخه)"""
    if not HAVE_MUTAGEN:
        return False
    try:
        raw = mutagen.File(long_path(path))
        if raw is None:
            return False
        if getattr(raw, "pictures", None):
            return True
        tags = raw.tags
        if tags is None:
            return False
        keys = list(getattr(tags, "keys", lambda: [])())
        return any(str(k).startswith(("APIC", "covr")) for k in keys)
    except Exception:
        return False


# ------------------------------------------------------- محدودهٔ محتوای صوتی

def _syncsafe(data: bytes) -> int:
    """عدد syncsafe هدر ID3v2 (هر بایت فقط ۷ بیت مفید دارد)."""
    value = 0
    for byte in data:
        value = (value << 7) | (byte & 0x7F)
    return value


def _mp3_range(fh, size: int):
    fh.seek(0)
    head = fh.read(10)
    start = 0
    if len(head) >= 10 and head[:3] == b"ID3":
        start = 10 + _syncsafe(head[6:10])
        if head[5] & 0x10:          # فوتر ID3v2.4
            start += 10
    end = size

    # ID3v1 در ۱۲۸ بایت آخر
    if end - start > 128:
        fh.seek(end - 128)
        if fh.read(3) == b"TAG":
            end -= 128

    # APEv2 که فوترش ۳۲ بایت است
    if end - start > 32:
        fh.seek(end - 32)
        footer = fh.read(32)
        if footer[:8] == b"APETAGEX":
            tag_size = int.from_bytes(footer[12:16], "little")
            flags = int.from_bytes(footer[20:24], "little")
            total = tag_size + (32 if flags & 0x80000000 else 0)
            if 0 < total < end - start:
                end -= total

    # Lyrics3v2 که با LYRICS200 تمام می‌شود
    if end - start > 15:
        fh.seek(end - 9)
        if fh.read(9) == b"LYRICS200":
            fh.seek(end - 15)
            try:
                length = int(fh.read(6))
                if 0 < length + 15 < end - start:
                    end -= length + 15
            except ValueError:
                pass

    return (start, end) if end > start else None


def _flac_range(fh, size: int):
    fh.seek(0)
    if fh.read(4) != b"fLaC":
        return None
    pos = 4
    for _ in range(256):            # سقف امن برای بلوک‌های متادیتا
        fh.seek(pos)
        header = fh.read(4)
        if len(header) < 4:
            return None
        is_last = header[0] & 0x80
        length = int.from_bytes(header[1:4], "big")
        pos += 4 + length
        if pos > size:
            return None
        if is_last:
            return (pos, size) if size > pos else None
    return None


def _mp4_range(fh, size: int):
    """برای m4a/mp4 فقط باکس mdat (خود صوت) اهمیت دارد؛ تگ‌ها در moov هستند."""
    pos = 0
    for _ in range(64):
        fh.seek(pos)
        header = fh.read(8)
        if len(header) < 8:
            return None
        box_size = int.from_bytes(header[0:4], "big")
        box_type = header[4:8]
        header_len = 8
        if box_size == 1:
            box_size = int.from_bytes(fh.read(8), "big")
            header_len = 16
        elif box_size == 0:
            box_size = size - pos
        if box_type == b"mdat":
            start, end = pos + header_len, min(pos + box_size, size)
            return (start, end) if end > start else None
        if box_size < 8:
            return None
        pos += box_size
        if pos >= size:
            return None
    return None


def _wav_range(fh, size: int):
    fh.seek(0)
    header = fh.read(12)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return None
    pos = 12
    for _ in range(64):
        fh.seek(pos)
        chunk = fh.read(8)
        if len(chunk) < 8:
            return None
        chunk_id = chunk[:4]
        chunk_size = int.from_bytes(chunk[4:8], "little")
        if chunk_id == b"data":
            start, end = pos + 8, min(pos + 8 + chunk_size, size)
            return (start, end) if end > start else None
        pos += 8 + chunk_size + (chunk_size & 1)
        if pos >= size:
            return None
    return None


_RANGE_READERS = {
    ".mp3": _mp3_range,
    ".flac": _flac_range,
    ".m4a": _mp4_range,
    ".m4b": _mp4_range,
    ".mp4": _mp4_range,
    ".aac": _mp3_range,
    ".wav": _wav_range,
}


def audio_payload_range(path: str, ext: str, size: int):
    """محدودهٔ بایت‌های صوتی (بدون تگ) را برمی‌گرداند، یا None اگر ممکن نبود."""
    reader = _RANGE_READERS.get(ext)
    if reader is None or size <= 0:
        return None
    try:
        with open(long_path(path), "rb") as fh:
            result = reader(fh, size)
    except Exception:
        return None
    if not result:
        return None
    start, end = result
    if start < 0 or end > size or end - start < 4096:
        return None
    # اگر عملاً چیزی حذف نشده، ارزش محاسبهٔ جدا ندارد
    return (start, end)
