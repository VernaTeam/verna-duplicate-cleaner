# -*- coding: utf-8 -*-
"""Picking the copy to keep, deleting safely, and writing the report."""

from __future__ import annotations

import csv
import datetime as _dt
import os

from .i18n import t
from .scanner import FileRec, Group
from .util import (format_datetime, human_duration, human_size, long_path,
                   looks_like_copy)

try:
    from send2trash import send2trash
    HAVE_TRASH = True
except Exception:  # pragma: no cover
    send2trash = None
    HAVE_TRASH = False

# Folders whose contents are usually a transient copy, not the library
_TEMP_MARKS = ("\\downloads", "\\temp", "\\tmp", "\\new folder",
               "\\telegram desktop", "\\دانلود", "\\بارگیری")


def score(rec: FileRec) -> tuple:
    """Keep-worthiness. Larger is better; compared as a tuple, in priority order."""
    meta = rec.meta
    bitrate = meta.bitrate if meta else 0
    tag_fields = meta.tag_fields if meta else 0
    has_cover = 1 if (meta and meta.has_cover) else 0
    duration = meta.duration if meta else 0.0

    stem = os.path.splitext(rec.name)[0]
    not_copy = 0 if looks_like_copy(stem) else 1
    depth = rec.path.replace("/", "\\").count("\\")
    folder_lower = rec.folder.lower()
    in_temp = any(mark in folder_lower for mark in _TEMP_MARKS)

    return (
        not_copy,
        0 if in_temp else 1,
        bitrate,
        tag_fields,
        has_cover,
        rec.size,
        round(duration, 1),
        -depth,
        -rec.mtime,
    )


def best_index(group: Group) -> int:
    """Index of the file that should survive."""
    if not group.files:
        return 0
    ranked = [(score(rec), i) for i, rec in enumerate(group.files)]
    ranked.sort(reverse=True)
    return ranked[0][1]


def suggest_deletions(group: Group) -> list[int]:
    keep = best_index(group)
    return [i for i in range(len(group.files)) if i != keep]


def why_keep(group: Group, index: int, lang: str = "fa") -> str:
    """Short localized explanation of why this copy won."""
    rec = group.files[index]
    others = [f for i, f in enumerate(group.files) if i != index]
    if not others:
        return t(lang, "why.only")

    reasons = []
    meta = rec.meta
    if meta and meta.bitrate:
        best_other = max((f.meta.bitrate if f.meta else 0) for f in others)
        if meta.bitrate > best_other:
            reasons.append(t(lang, "why.bitrate", n=meta.bitrate // 1000))
    if rec.size > max(f.size for f in others):
        reasons.append(t(lang, "why.size"))
    if meta and meta.tag_fields > max(
            (f.meta.tag_fields if f.meta else 0) for f in others):
        reasons.append(t(lang, "why.tags"))
    if meta and meta.has_cover and not any(
            f.meta and f.meta.has_cover for f in others):
        reasons.append(t(lang, "why.cover"))
    if not looks_like_copy(os.path.splitext(rec.name)[0]) and any(
            looks_like_copy(os.path.splitext(f.name)[0]) for f in others):
        reasons.append(t(lang, "why.notcopy"))
    if rec.mtime < min(f.mtime for f in others):
        reasons.append(t(lang, "why.older"))

    separator = "، " if lang == "fa" else ", "
    return separator.join(reasons) if reasons else t(lang, "why.default")


# ---------------------------------------------------------------- deletion

class DeleteResult:
    def __init__(self):
        self.deleted: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.freed: int = 0


def delete_files(paths: list[str], to_recycle_bin: bool = True,
                 log_dir: str | None = None, lang: str = "fa") -> DeleteResult:
    """Delete files, to the Recycle Bin unless told otherwise."""
    result = DeleteResult()
    for path in paths:
        try:
            size = os.path.getsize(long_path(path))
        except OSError:
            size = 0
        try:
            if to_recycle_bin:
                if not HAVE_TRASH:
                    raise RuntimeError("send2trash is not installed")
                send2trash(os.path.abspath(path))
            else:
                os.remove(long_path(path))
            result.deleted.append(path)
            result.freed += size
        except Exception as exc:
            result.failed.append((path, f"{type(exc).__name__}: {exc}"))

    if log_dir and result.deleted:
        _write_log(log_dir, result, to_recycle_bin, lang)
    return result


def _write_log(log_dir: str, result: DeleteResult, to_recycle_bin: bool,
               lang: str):
    try:
        os.makedirs(log_dir, exist_ok=True)
        stamp = _dt.datetime.now()
        path = os.path.join(log_dir, f"deleted-{stamp:%Y-%m-%d}.csv")
        is_new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            if is_new:
                writer.writerow([t(lang, "csv.log.time"), t(lang, "csv.log.path"),
                                 t(lang, "csv.log.how")])
            how = t(lang, "dlg.delete.trash") if to_recycle_bin \
                else t(lang, "dlg.delete.perm")
            for item in result.deleted:
                writer.writerow([stamp.strftime("%Y-%m-%d %H:%M:%S"), item, how])
    except Exception:
        pass


# ------------------------------------------------------------------ report

def export_report(groups, path: str, selected: dict | None = None,
                  lang: str = "fa"):
    """Write the full scan result as CSV (UTF-8 with BOM, so Excel is happy)."""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            t(lang, "csv.group"), t(lang, "csv.method"), t(lang, "csv.confidence"),
            t(lang, "csv.state"), t(lang, "csv.name"), t(lang, "csv.folder"),
            t(lang, "csv.bytes"), t(lang, "csv.size"), t(lang, "csv.duration"),
            t(lang, "csv.bitrate"), t(lang, "csv.artist"), t(lang, "csv.title"),
            t(lang, "csv.album"), t(lang, "csv.modified"), t(lang, "csv.reason"),
        ])
        for group in groups:
            keep = best_index(group)
            reason = why_keep(group, keep, lang)
            label = t(lang, "method." + group.primary_method)
            if group.combined:
                label += " (" + t(lang, "method.combined") + ")"
            for i, rec in enumerate(group.files):
                meta = rec.meta
                if selected is not None:
                    state = t(lang, "csv.state.drop") if selected.get(rec.path) \
                        else t(lang, "csv.state.keep")
                else:
                    state = t(lang, "csv.state.keep") if i == keep \
                        else t(lang, "csv.state.suggest")
                writer.writerow([
                    group.gid,
                    label,
                    f"{group.confidence}%",
                    state,
                    rec.name,
                    rec.folder,
                    rec.size,
                    human_size(rec.size, "en"),
                    human_duration(meta.duration if meta else 0, "en"),
                    f"{meta.bitrate // 1000} kbps" if meta and meta.bitrate else "",
                    meta.artist if meta else "",
                    meta.title if meta else "",
                    meta.album if meta else "",
                    format_datetime(rec.mtime, "en"),
                    reason if i == keep else "",
                ])
