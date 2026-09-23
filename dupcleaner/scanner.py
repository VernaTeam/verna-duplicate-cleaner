# -*- coding: utf-8 -*-
"""The duplicate-finding engine.

Detectors run strongest first, and each file is labelled with the strongest
method that actually found it:

  100%  exact     byte-for-byte identical
   98%  audio     identical audio bytes, only the tags differ
   92%  tag       same artist and title
   90%  feat      same title once the guest credit is removed, credited
                  artists compatible, and the durations agree
   72%  title     same title, artist field empty
   70%  name      same file name after normalization
   58%  duration  near-identical duration and size (untagged, badly named files)
   40%  size      similar size only (a suspect, not a finding)

Method labels live in `i18n.py`; this module only ever returns keys, so the
engine is language-independent.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import audio as audio_mod
from .audio import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, AudioMeta
from .hashing import hash_range, quick_signature
from .util import long_path, name_key

# --------------------------------------------------------------- constants

# method key -> confidence percentage
METHODS = {
    "exact": 100,
    "audio": 98,
    "tag": 92,
    "feat": 90,
    "title": 72,
    "name": 70,
    "duration": 58,
    "size": 40,
}

# Progress phase keys, resolved through i18n on the UI side.
PHASES = ("scan.walking", "scan.meta", "scan.payload", "scan.exact",
          "scan.audiohash", "scan.tags", "scan.names", "scan.durations",
          "scan.sizes", "scan.done")

SKIP_DIR_NAMES = {
    "$recycle.bin", "system volume information", "windows", "winsxs",
    "program files", "program files (x86)", "programdata", "appdata",
    "node_modules", ".git", ".svn", "__pycache__", "$windows.~bt",
    "recovery", "perflogs",
}

_ATTR_HIDDEN = 0x2
_ATTR_SYSTEM = 0x4


def ext_category(ext: str) -> str:
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


# ------------------------------------------------------------------ models

@dataclass
class FileRec:
    path: str
    size: int
    mtime: float
    mtime_ns: int
    ext: str
    meta: AudioMeta | None = None
    payload: tuple[int, int] | None = None    # audio byte range, tags excluded
    match_method: str = ""

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def is_audio(self) -> bool:
        return self.ext in AUDIO_EXTS

    @property
    def category(self) -> str:
        return ext_category(self.ext)


@dataclass
class Group:
    gid: int
    files: list[FileRec] = field(default_factory=list)
    methods: set[str] = field(default_factory=set)
    members: list[int] = field(default_factory=list)   # during the scan only

    @property
    def primary_method(self) -> str:
        """The strongest method involved in this group."""
        return max(self.methods, key=lambda m: METHODS[m], default="size")

    @property
    def weakest_method(self) -> str:
        return min(self.methods, key=lambda m: METHODS[m], default="size")

    @property
    def confidence(self) -> int:
        """The weakest link, deliberately.

        If one member joined a group of byte-identical files only because its
        tags matched, showing "100% certain" for the whole group would invite
        deleting something unverified.
        """
        return METHODS[self.weakest_method]

    @property
    def combined(self) -> bool:
        return len(self.methods) > 1

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def reclaimable(self) -> int:
        """Bytes freed if only one copy is kept."""
        if len(self.files) < 2:
            return 0
        return self.total_size - max(f.size for f in self.files)


@dataclass
class ScanOptions:
    folders: list[str] = field(default_factory=list)
    recursive: bool = True
    mode: str = "audio"                       # audio | all | custom
    custom_exts: list[str] = field(default_factory=list)
    min_size: int = 64 * 1024
    size_tolerance: float = 2.0               # percent
    skip_hidden: bool = True
    workers: int = 8
    use_cache: bool = True
    use_exact: bool = True
    use_audio_hash: bool = True
    use_tags: bool = True
    use_feat: bool = True
    use_name: bool = True
    use_duration: bool = True
    use_size: bool = True

    def accepts(self, ext: str) -> bool:
        if self.mode == "audio":
            return ext in AUDIO_EXTS
        if self.mode == "custom":
            return ext in set(self.custom_exts)
        return True

    def any_method(self) -> bool:
        return any([self.use_exact, self.use_audio_hash, self.use_tags,
                    self.use_feat, self.use_name, self.use_duration,
                    self.use_size])


class Cancelled(Exception):
    """The user stopped the scan."""


# ------------------------------------------------------------------ engine

class Engine:
    def __init__(self, options: ScanOptions, progress=None, cancel=None,
                 cache=None):
        self.opt = options
        self._progress = progress or (lambda *a, **k: None)
        self.cancel = cancel or threading.Event()
        self.cache = cache if (cache is not None and options.use_cache) else None
        self.files: list[FileRec] = []
        self.owner: dict[int, int] = {}
        self.groups: dict[int, Group] = {}
        self._next_gid = 1
        self._rows: dict[str, dict | None] = {}     # memoized cache lookups
        self.stats = {"scanned": 0, "skipped": 0, "unreadable": 0,
                      "cache_hits": 0}

    # ------------------------------------------------------------ helpers

    def _tick(self, phase: str, done: int, total: int):
        if self.cancel.is_set():
            raise Cancelled()
        self._progress(phase, done, total)

    def _check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _row(self, rec: FileRec) -> dict | None:
        """Cached row for this file, looked up at most once per scan."""
        if self.cache is None:
            return None
        key = rec.path
        if key not in self._rows:
            self._rows[key] = self.cache.get(rec.path, rec.size, rec.mtime_ns)
        return self._rows[key]

    def _remember(self, rec: FileRec, **fields):
        if self.cache is None:
            return
        self.cache.put(rec.path, rec.size, rec.mtime_ns, **fields)
        row = self._rows.get(rec.path)
        if row is None:
            self._rows[rec.path] = dict(fields)
        else:
            row.update(fields)

    def _parallel(self, targets: list, fn, phase: str, step: int = 25):
        """Run fn over targets in a thread pool, ticking progress as they land.

        Tag reading and container parsing are I/O-bound and mutagen releases
        the GIL on reads, so threads genuinely help here. Worker count is a
        setting because too many threads hurt on a spinning disk.
        """
        total = len(targets)
        if not total:
            return
        workers = max(1, min(int(self.opt.workers or 1), 32))
        if workers == 1 or total < 4:
            for i, item in enumerate(targets):
                if i % step == 0:
                    self._tick(phase, i, total)
                fn(item)
            self._tick(phase, total, total)
            return

        pool = ThreadPoolExecutor(max_workers=workers)
        done = 0
        try:
            futures = [pool.submit(fn, item) for item in targets]
            for future in as_completed(futures):
                future.result()          # fn swallows its own errors
                done += 1
                if done % step == 0:
                    self._tick(phase, done, total)
            self._tick(phase, total, total)
        except Cancelled:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            pool.shutdown(wait=True)

    # --------------------------------------------------------- 1) walking

    def collect(self):
        seen: set[str] = set()
        for root in [os.path.abspath(f) for f in self.opt.folders]:
            if not os.path.isdir(root):
                continue
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                self._check()
                dirnames[:] = [
                    d for d in dirnames
                    if d.lower() not in SKIP_DIR_NAMES
                    and not (self.opt.skip_hidden and d.startswith("."))
                ]
                if not self.opt.recursive:
                    dirnames[:] = []
                for filename in filenames:
                    full = os.path.join(dirpath, filename)
                    key = os.path.normcase(full)
                    if key in seen:
                        continue
                    seen.add(key)
                    rec = self._make_record(full, filename)
                    if rec is not None:
                        self.files.append(rec)
                self._tick("scan.walking", len(self.files), 0)

    def _make_record(self, full: str, filename: str) -> FileRec | None:
        ext = os.path.splitext(filename)[1].lower()
        if not self.opt.accepts(ext):
            return None
        try:
            st = os.stat(long_path(full))
        except OSError:
            self.stats["unreadable"] += 1
            return None
        if not os.path.isfile(long_path(full)):
            return None
        if self.opt.skip_hidden:
            attrs = getattr(st, "st_file_attributes", 0)
            if attrs & (_ATTR_HIDDEN | _ATTR_SYSTEM):
                self.stats["skipped"] += 1
                return None
        if st.st_size < self.opt.min_size:
            self.stats["skipped"] += 1
            return None
        self.stats["scanned"] += 1
        return FileRec(path=full, size=st.st_size, mtime=st.st_mtime,
                       mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
                       ext=ext)

    # ------------------------------------------------------- 2) tags/ranges

    def load_metadata(self):
        targets = [f for f in self.files if f.is_audio]

        def work(rec: FileRec):
            if self.cancel.is_set():
                return
            row = self._row(rec)
            if row and row.get("has_meta"):
                rec.meta = AudioMeta(
                    artist=row.get("artist") or "",
                    title=row.get("title") or "",
                    album=row.get("album") or "",
                    track=row.get("track") or "",
                    duration=float(row.get("duration") or 0.0),
                    bitrate=int(row.get("bitrate") or 0),
                    sample_rate=int(row.get("samplerate") or 0),
                    channels=int(row.get("channels") or 0),
                    has_cover=bool(row.get("has_cover")),
                    error=row.get("tag_error") or "")
                rec.meta.tag_fields = sum(
                    1 for v in (rec.meta.artist, rec.meta.title,
                                rec.meta.album, rec.meta.track) if v)
                self.stats["cache_hits"] += 1
                return
            rec.meta = audio_mod.read_meta(rec.path)
            m = rec.meta
            self._remember(rec, has_meta=1, artist=m.artist, title=m.title,
                           album=m.album, track=m.track, duration=m.duration,
                           bitrate=m.bitrate, channels=m.channels,
                           samplerate=m.sample_rate,
                           has_cover=int(m.has_cover), tag_error=m.error)

        self._parallel(targets, work, "scan.meta")

    def load_payload_ranges(self):
        targets = [f for f in self.files if f.is_audio]

        def work(rec: FileRec):
            if self.cancel.is_set():
                return
            row = self._row(rec)
            if row and row.get("a_start") is not None:
                start, end = int(row["a_start"]), int(row["a_end"])
                if 0 <= start < end <= rec.size and not (start == 0 and end == rec.size):
                    rec.payload = (start, end)
                return
            rng = audio_mod.audio_payload_range(rec.path, rec.ext, rec.size)
            if rng is None:
                # remember "no range" as the whole file so we do not re-parse
                self._remember(rec, a_start=0, a_end=rec.size)
                return
            if not (rng[0] == 0 and rng[1] == rec.size):
                rec.payload = rng
            self._remember(rec, a_start=rng[0], a_end=rng[1])

        self._parallel(targets, work, "scan.payload", step=50)

    # ------------------------------------------------- cached hash helpers

    def _quick_sig(self, rec: FileRec) -> str | None:
        row = self._row(rec)
        if row and row.get("quick"):
            return row["quick"]
        sig = quick_signature(rec.path, rec.size)
        if sig:
            self._remember(rec, quick=sig)
        return sig

    def _full_hash(self, rec: FileRec) -> str | None:
        row = self._row(rec)
        if row and row.get("full"):
            return row["full"]
        digest = hash_range(rec.path, cancel=self.cancel)
        if digest:
            self._remember(rec, full=digest)
        return digest

    def _audio_digest(self, rec: FileRec) -> str | None:
        row = self._row(rec)
        if row and row.get("a_hash"):
            return row["a_hash"]
        start, end = rec.payload
        digest = hash_range(rec.path, start, end, cancel=self.cancel)
        if digest:
            self._remember(rec, a_hash=digest)
        return digest

    # ----------------------------------------------------------- grouping

    def _apply_bucket(self, idxs: list[int], method: str,
                      allow_extend: bool = True):
        """Turn one equal-key bucket into a group, or fold it into an existing one."""
        if not allow_extend:
            idxs = [i for i in idxs if i not in self.owner]
        if len(idxs) < 2:
            return
        existing = sorted({self.owner[i] for i in idxs if i in self.owner})
        if existing:
            target = existing[0]
            for other in existing[1:]:
                moving = self.groups.pop(other)
                for member in moving.members:
                    self.owner[member] = target
                    self.groups[target].members.append(member)
                self.groups[target].methods |= moving.methods
        else:
            target = self._next_gid
            self._next_gid += 1
            self.groups[target] = Group(gid=target)

        group = self.groups[target]
        claimed = 0
        for i in idxs:
            if i not in self.owner:
                self.owner[i] = target
                self.files[i].match_method = method
                group.members.append(i)
                claimed += 1
        # Only a method that actually brought a member in is recorded. A
        # detector that merely re-confirms an existing group must not drag the
        # group's displayed confidence down — re-confirmation is not weakness.
        if claimed or len(existing) > 1:
            group.methods.add(method)

    def _bucket_by(self, keyfunc, indices=None) -> dict:
        buckets: dict = {}
        pool = indices if indices is not None else range(len(self.files))
        for i in pool:
            key = keyfunc(self.files[i])
            if key:
                buckets.setdefault(key, []).append(i)
        return {k: v for k, v in buckets.items() if len(v) > 1}

    # ---------------------------------------------- detector 1: byte-equal

    def detect_exact(self):
        by_size = self._bucket_by(lambda f: f.size or None)
        total = sum(len(v) for v in by_size.values())
        if not total:
            return
        done = 0
        for indices in by_size.values():
            self._check()
            by_quick: dict = {}
            for i in indices:
                sig = self._quick_sig(self.files[i])
                done += 1
                if done % 20 == 0:
                    self._tick("scan.exact", done, total)
                if sig:
                    by_quick.setdefault(sig, []).append(i)
            for sub in by_quick.values():
                if len(sub) < 2:
                    continue
                by_full: dict = {}
                for i in sub:
                    digest = self._full_hash(self.files[i])
                    if digest:
                        by_full.setdefault(digest, []).append(i)
                for final in by_full.values():
                    self._apply_bucket(final, "exact")
        self._tick("scan.exact", total, total)

    # ------------------------------------ detector 2: audio without tags

    def detect_audio_content(self):
        pool = [i for i, f in enumerate(self.files) if f.payload]
        by_len: dict = {}
        for i in pool:
            start, end = self.files[i].payload
            by_len.setdefault(end - start, []).append(i)
        by_len = {k: v for k, v in by_len.items() if len(v) > 1}
        total = sum(len(v) for v in by_len.values())
        if not total:
            return
        done = 0
        for indices in by_len.values():
            self._check()
            by_hash: dict = {}
            for i in indices:
                digest = self._audio_digest(self.files[i])
                done += 1
                if done % 10 == 0:
                    self._tick("scan.audiohash", done, total)
                if digest:
                    by_hash.setdefault(digest, []).append(i)
            for final in by_hash.values():
                self._apply_bucket(final, "audio")
        self._tick("scan.audiohash", total, total)

    # ----------------------------------------- detectors 3 and 4: the tags

    # A bucket bigger than this is skipped: a title shared by that many files
    # is a generic one and says nothing, and the artist check below is
    # quadratic inside a bucket.
    MAX_TITLE_BUCKET = 60

    # Two copies of one recording never differ by more than a rounding error.
    # A radio edit against an album version does, and must not be called a
    # 90% match.
    FEAT_DURATION_WINDOW = 2.5

    def detect_tags(self):
        self._tick("scan.tags", 0, 3)
        for indices in self._bucket_by(
                lambda f: f.meta.tag_key() if (f.meta and f.is_audio) else "").values():
            self._apply_bucket(indices, "tag")

        self._tick("scan.tags", 1, 3)
        if self.opt.use_feat:
            self.detect_featuring()

        self._tick("scan.tags", 2, 3)
        # same title, artist field empty
        for indices in self._bucket_by(
                lambda f: f.meta.title_key()
                if (f.meta and f.is_audio and not f.meta.tag_key()) else "").values():
            self._apply_bucket(indices, "title")
        self._tick("scan.tags", 3, 3)

    # ------------------------------- detector 4: the guest-artist credit

    def _feat_compatible(self, i: int, j: int) -> bool:
        """Same recording, described differently?

        Requires the credited artists of one file to be a subset of the
        other's — so "Shayea" matches "Shayea & Daniyal & Mahyar" but
        "Song (feat. A)" never matches "Song (feat. B)" — and, when both
        durations are known, that the two lengths agree.
        """
        a, b = self.files[i].meta, self.files[j].meta
        artists_a, artists_b = a.artist_set(), b.artist_set()
        if not (artists_a <= artists_b or artists_b <= artists_a):
            return False
        if a.duration and b.duration:
            return abs(a.duration - b.duration) <= self.FEAT_DURATION_WINDOW
        return True

    def detect_featuring(self):
        """Same song, different guest-artist bookkeeping.

        "Ta Yeja (feat. Daniyal & Mahyar)" credited to "Shayea & Daniyal &
        Mahyar", and "Ta Yeja (Ft. Daniyal & Mahyar)" credited to "Shayea",
        are one recording — but comparing artist+title as strings says they
        are unrelated, and the file falls through to the weak size and
        duration detectors. Strip the guest credit from the title, then
        compare the credited artists as sets.
        """
        buckets = self._bucket_by(
            lambda f: f.meta.core_title_key()
            if (f.meta and f.is_audio and f.meta.artist_set()) else "")

        for indices in buckets.values():
            self._check()
            if len(indices) > self.MAX_TITLE_BUCKET:
                continue
            claimed: set = set()
            for position, i in enumerate(indices):
                if i in claimed:
                    continue
                cluster = [i]
                for j in indices[position + 1:]:
                    if j not in claimed and self._feat_compatible(i, j):
                        cluster.append(j)
                        claimed.add(j)
                if len(cluster) > 1:
                    claimed.add(i)
                    self._apply_bucket(cluster, "feat")

    # ------------------------------------------- detector 5: the file name

    def detect_name(self):
        self._tick("scan.names", 0, 1)

        def key(f: FileRec) -> str:
            base = name_key(f.name)
            return f"{f.category}|{base}" if len(base) >= 3 else ""

        for indices in self._bucket_by(key).values():
            self._apply_bucket(indices, "name")
        self._tick("scan.names", 1, 1)

    # ------------------------------------ detector 6: duration within 1.5s

    def detect_duration(self):
        """For files with no usable name and no tags."""
        self._tick("scan.durations", 0, 1)
        pool = [
            i for i, f in enumerate(self.files)
            if i not in self.owner and f.is_audio and f.meta
            and f.meta.duration >= 20
        ]
        pool.sort(key=lambda i: self.files[i].meta.duration)
        for cluster in _anchor_clusters(
                pool, lambda i: self.files[i].meta.duration, abs_window=1.5):
            cluster.sort(key=lambda i: self.files[i].size)
            for sub in _anchor_clusters(
                    cluster, lambda i: self.files[i].size, ratio=0.25):
                self._apply_bucket(sub, "duration", allow_extend=False)
        self._tick("scan.durations", 1, 1)

    # ------------------------------------------- detector 7: size only

    def detect_size(self):
        self._tick("scan.sizes", 0, 1)
        tol = max(0.0, self.opt.size_tolerance) / 100.0
        pool = [
            i for i, f in enumerate(self.files)
            if i not in self.owner and f.size >= self.opt.min_size
        ]
        pool.sort(key=lambda i: self.files[i].size)
        for cluster in _anchor_clusters(
                pool, lambda i: self.files[i].size, ratio=tol):
            by_cat: dict = {}
            for i in cluster:
                by_cat.setdefault(self.files[i].category, []).append(i)
            for sub in by_cat.values():
                self._apply_bucket(sub, "size", allow_extend=False)
        self._tick("scan.sizes", 1, 1)

    # --------------------------------------------------------------- run

    def run(self) -> list[Group]:
        try:
            self.collect()
            if not self.files:
                return []

            has_audio = any(f.is_audio for f in self.files)
            if (self.opt.use_tags or self.opt.use_duration) and has_audio:
                self.load_metadata()
            if self.opt.use_audio_hash and has_audio:
                self.load_payload_ranges()

            if self.opt.use_exact:
                self.detect_exact()
            if self.opt.use_audio_hash:
                self.detect_audio_content()
            if self.opt.use_tags:
                self.detect_tags()
            if self.opt.use_name:
                self.detect_name()
            if self.opt.use_duration:
                self.detect_duration()
            if self.opt.use_size:
                self.detect_size()
        finally:
            if self.cache is not None:
                self.cache.commit()

        for group in self.groups.values():
            group.files = [self.files[i] for i in group.members]

        result = [g for g in self.groups.values() if len(g.files) > 1]
        for group in result:
            group.files.sort(key=lambda f: (-f.size, f.path))
        result.sort(key=lambda g: (-g.confidence, -g.reclaimable))
        for index, group in enumerate(result, start=1):
            group.gid = index
        self._tick("scan.done", 1, 1)
        return result


# ------------------------------------------------------------- clustering

def _anchor_clusters(indices, valuefunc, ratio: float | None = None,
                     abs_window: float | None = None):
    """Anchor-based clustering over a sorted list.

    The window is measured from the cluster's anchor rather than from the
    previous item, so many small consecutive differences cannot chain an
    entire library into one group.
    """
    out = []
    i = 0
    n = len(indices)
    while i < n:
        anchor = valuefunc(indices[i])
        if abs_window is not None:
            limit = anchor + abs_window
        else:
            limit = anchor * (1.0 + (ratio or 0.0))
        j = i + 1
        while j < n and valuefunc(indices[j]) <= limit:
            j += 1
        if j - i >= 2:
            out.append(list(indices[i:j]))
        i = j
    return out
