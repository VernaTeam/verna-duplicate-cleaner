# -*- coding: utf-8 -*-
"""هش گرفتن از فایل — با امکان محدود کردن به بازه‌ای از بایت‌ها."""

from __future__ import annotations

import hashlib

from .util import long_path

CHUNK = 1 << 20          # ۱ مگابایت
QUICK_WINDOW = 64 * 1024  # اندازهٔ پنجرهٔ هش سریع


def hash_range(path: str, start: int = 0, end: int | None = None,
               cancel=None) -> str | None:
    """هش blake2b از بازهٔ [start, end) فایل. در صورت خطا None."""
    try:
        with open(long_path(path), "rb") as fh:
            digest = hashlib.blake2b(digest_size=20)
            fh.seek(start)
            remaining = None if end is None else max(0, end - start)
            while True:
                if cancel is not None and cancel.is_set():
                    return None
                want = CHUNK if remaining is None else min(CHUNK, remaining)
                if want == 0:
                    break
                block = fh.read(want)
                if not block:
                    break
                digest.update(block)
                if remaining is not None:
                    remaining -= len(block)
            return digest.hexdigest()
    except (OSError, ValueError):
        return None


def quick_signature(path: str, size: int, start: int = 0,
                    end: int | None = None) -> str | None:
    """امضای سریع: ابتدا و انتهای بازه. برای رد کردن نامزدهای بی‌ربط."""
    end = size if end is None else end
    length = end - start
    if length <= 0:
        return None
    try:
        with open(long_path(path), "rb") as fh:
            digest = hashlib.blake2b(digest_size=16)
            digest.update(str(length).encode())
            fh.seek(start)
            digest.update(fh.read(min(QUICK_WINDOW, length)))
            if length > QUICK_WINDOW * 2:
                fh.seek(end - QUICK_WINDOW)
                digest.update(fh.read(QUICK_WINDOW))
            return digest.hexdigest()
    except (OSError, ValueError):
        return None
