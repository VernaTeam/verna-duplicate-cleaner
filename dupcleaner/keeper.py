# -*- coding: utf-8 -*-
"""انتخاب «بهترین نسخه» در هر گروه و حذف امن فایل‌ها."""

from __future__ import annotations

import csv
import datetime as _dt
import os

from .scanner import FileRec, Group
from .util import long_path, looks_like_copy

try:
    from send2trash import send2trash
    HAVE_TRASH = True
except Exception:  # pragma: no cover
    send2trash = None
    HAVE_TRASH = False


def score(rec: FileRec, group: Group) -> tuple:
    """امتیاز نگه‌داشتن. هرچه بزرگ‌تر، شایسته‌تر برای ماندن."""
    meta = rec.meta
    bitrate = meta.bitrate if meta else 0
    tag_fields = meta.tag_fields if meta else 0
    has_cover = 1 if (meta and meta.has_cover) else 0
    duration = meta.duration if meta else 0.0

    stem = os.path.splitext(rec.name)[0]
    not_copy = 0 if looks_like_copy(stem) else 1

    # مسیر کم‌عمق‌تر معمولاً «کتابخانهٔ اصلی» است، نه پوشهٔ دانلود موقت
    depth = rec.path.replace("/", "\\").count("\\")
    folder_lower = rec.folder.lower()
    in_temp = any(
        mark in folder_lower
        for mark in ("\\downloads", "\\temp", "\\tmp", "\\new folder",
                     "\\telegram desktop", "\\دانلود")
    )

    return (
        not_copy,               # نامی که نشانهٔ کپی ندارد
        1 if not in_temp else 0,
        bitrate,                # کیفیت بالاتر
        tag_fields,             # تگ کامل‌تر
        has_cover,              # کاور دارد
        rec.size,               # حجم بیشتر
        round(duration, 1),
        -depth,                 # مسیر کوتاه‌تر
        -rec.mtime,             # قدیمی‌تر (نسخهٔ اصلی)
    )


def best_index(group: Group) -> int:
    """اندیس فایلی که باید نگه داشته شود."""
    if not group.files:
        return 0
    scores = [(score(rec, group), i) for i, rec in enumerate(group.files)]
    scores.sort(reverse=True)
    return scores[0][1]


def suggest_deletions(group: Group) -> list[int]:
    """اندیس فایل‌هایی که پیشنهاد می‌شود حذف شوند (همه جز بهترین)."""
    keep = best_index(group)
    return [i for i in range(len(group.files)) if i != keep]


def why_keep(group: Group, index: int) -> str:
    """توضیح کوتاه فارسی برای اینکه چرا این نسخه بهترین است."""
    rec = group.files[index]
    reasons = []
    others = [f for i, f in enumerate(group.files) if i != index]
    if not others:
        return "تنها فایل گروه"

    meta = rec.meta
    if meta and meta.bitrate:
        best_other = max((f.meta.bitrate if f.meta else 0) for f in others)
        if meta.bitrate > best_other:
            reasons.append(f"بیت‌ریت بالاتر ({meta.bitrate // 1000} kbps)")
    if rec.size > max(f.size for f in others):
        reasons.append("حجم بیشتر")
    if meta and meta.tag_fields > max((f.meta.tag_fields if f.meta else 0) for f in others):
        reasons.append("تگ کامل‌تر")
    if meta and meta.has_cover and not any(f.meta and f.meta.has_cover for f in others):
        reasons.append("کاور دارد")
    if not looks_like_copy(os.path.splitext(rec.name)[0]) and \
            any(looks_like_copy(os.path.splitext(f.name)[0]) for f in others):
        reasons.append("نامش نشانهٔ کپی ندارد")
    if rec.mtime < min(f.mtime for f in others):
        reasons.append("قدیمی‌تر است")

    return "، ".join(reasons) if reasons else "انتخاب پیش‌فرض"


# ---------------------------------------------------------------- حذف

class DeleteResult:
    def __init__(self):
        self.deleted: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.freed: int = 0


def delete_files(paths: list[str], to_recycle_bin: bool = True,
                 log_dir: str | None = None) -> DeleteResult:
    """فایل‌ها را حذف می‌کند. پیش‌فرض: انتقال به سطل بازیافت."""
    result = DeleteResult()
    for path in paths:
        try:
            size = os.path.getsize(long_path(path))
        except OSError:
            size = 0
        try:
            if to_recycle_bin:
                if not HAVE_TRASH:
                    raise RuntimeError("send2trash نصب نیست")
                send2trash(os.path.abspath(path))
            else:
                os.remove(long_path(path))
            result.deleted.append(path)
            result.freed += size
        except Exception as exc:
            result.failed.append((path, f"{type(exc).__name__}: {exc}"))

    if log_dir and result.deleted:
        _write_log(log_dir, result, to_recycle_bin)
    return result


def _write_log(log_dir: str, result: DeleteResult, to_recycle_bin: bool):
    try:
        os.makedirs(log_dir, exist_ok=True)
        stamp = _dt.datetime.now()
        path = os.path.join(log_dir, f"deleted-{stamp:%Y-%m-%d}.csv")
        is_new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            if is_new:
                writer.writerow(["زمان", "مسیر فایل", "روش حذف"])
            mode = "سطل بازیافت" if to_recycle_bin else "حذف دائمی"
            for item in result.deleted:
                writer.writerow([stamp.strftime("%Y-%m-%d %H:%M:%S"), item, mode])
    except Exception:
        pass


def export_report(groups, path: str, selected: dict | None = None):
    """گزارش کامل نتایج اسکن را در یک فایل CSV می‌نویسد."""
    from .util import human_duration, human_size
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "گروه", "روش تشخیص", "اطمینان", "وضعیت", "نام فایل", "پوشه",
            "حجم (بایت)", "حجم", "مدت", "بیت‌ریت", "آرتیست", "عنوان", "آلبوم",
            "تاریخ تغییر",
        ])
        for group in groups:
            keep = best_index(group)
            for i, rec in enumerate(group.files):
                meta = rec.meta
                if selected is not None:
                    state = "برای حذف" if selected.get(rec.path) else "نگه‌داشتن"
                else:
                    state = "نگه‌داشتن" if i == keep else "پیشنهاد حذف"
                writer.writerow([
                    group.gid,
                    group.method_label,
                    f"{group.confidence}%",
                    state,
                    rec.name,
                    rec.folder,
                    rec.size,
                    human_size(rec.size, persian=False),
                    human_duration(meta.duration if meta else 0, persian=False),
                    f"{meta.bitrate // 1000} kbps" if meta and meta.bitrate else "",
                    meta.artist if meta else "",
                    meta.title if meta else "",
                    meta.album if meta else "",
                    _dt.datetime.fromtimestamp(rec.mtime).strftime("%Y-%m-%d %H:%M"),
                ])
