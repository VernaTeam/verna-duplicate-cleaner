# -*- coding: utf-8 -*-
"""موتور پیدا کردن فایل‌های تکراری.

روش‌ها از قطعی‌ترین به مشکوک‌ترین اجرا می‌شوند و هر فایل با قوی‌ترین روشی
که پیدایش کرده برچسب می‌خورد:

  ۱۰۰٪  exact     — بایت‌به‌بایت یکسان
   ۹۸٪  audio     — بایت‌های صوتی یکسان، فقط تگ‌ها فرق دارند
   ۸۵٪  tag       — آرتیست و عنوان یکسان
   ۷۲٪  title     — فقط عنوان یکسان (آرتیست خالی)
   ۷۰٪  name      — نام فایل بعد از نرمال‌سازی یکسان
   ۵۸٪  duration  — مدت و حجم بسیار نزدیک (برای فایل‌های بی‌تگ و بی‌نام)
   ۴۰٪  size      — فقط حجم مشابه (مشکوک)
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

from . import audio as audio_mod
from .audio import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, AudioMeta
from .hashing import hash_range, quick_signature
from .util import long_path, name_key

# ---------------------------------------------------------------- ثابت‌ها

METHOD_INFO = {
    "exact":    ("محتوای کاملاً یکسان", 100),
    "audio":    ("صوت یکسان، تگ متفاوت", 98),
    "tag":      ("تگ موزیک: آرتیست و عنوان", 85),
    "title":    ("عنوان یکسان", 72),
    "name":     ("نام فایل یکسان", 70),
    "duration": ("مدت و حجم نزدیک", 58),
    "size":     ("حجم مشابه (مشکوک)", 40),
}

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


# ---------------------------------------------------------------- داده‌ها

@dataclass
class FileRec:
    path: str
    size: int
    mtime: float
    ext: str
    meta: AudioMeta | None = None
    payload: tuple[int, int] | None = None   # بازهٔ بایت‌های صوتی
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
    members: list[int] = field(default_factory=list)   # فقط حین اسکن

    @property
    def primary_method(self) -> str:
        """قوی‌ترین روشی که در این گروه دخیل بوده."""
        return max(self.methods, key=lambda m: METHOD_INFO[m][1], default="size")

    @property
    def weakest_method(self) -> str:
        return min(self.methods, key=lambda m: METHOD_INFO[m][1], default="size")

    @property
    def confidence(self) -> int:
        """اطمینان گروه = ضعیف‌ترین حلقه.

        اگر عضوی فقط با تگ به گروه اضافه شده باشد، نمایش «۱۰۰٪ قطعی» برای کل
        گروه گمراه‌کننده است؛ کاربر باید بداند سست‌ترین پیوند چقدر است.
        """
        return METHOD_INFO[self.weakest_method][1]

    @property
    def method_label(self) -> str:
        label = METHOD_INFO[self.primary_method][0]
        if len(self.methods) > 1:
            return f"{label} ‹ترکیبی›"
        return label

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def reclaimable(self) -> int:
        """اگر فقط یکی نگه داشته شود، چقدر فضا آزاد می‌شود."""
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
    size_tolerance: float = 2.0               # درصد
    skip_hidden: bool = True
    use_exact: bool = True
    use_audio_hash: bool = True
    use_tags: bool = True
    use_name: bool = True
    use_duration: bool = True
    use_size: bool = True

    def wants_meta(self) -> bool:
        return self.use_tags or self.use_duration or self.mode == "audio"

    def accepts(self, ext: str) -> bool:
        if self.mode == "audio":
            return ext in AUDIO_EXTS
        if self.mode == "custom":
            return ext in set(self.custom_exts)
        return True


class Cancelled(Exception):
    """اسکن توسط کاربر متوقف شد."""


# ---------------------------------------------------------------- موتور

class Engine:
    def __init__(self, options: ScanOptions, progress=None, cancel=None):
        self.opt = options
        self._progress = progress or (lambda *a, **k: None)
        self.cancel = cancel or threading.Event()
        self.files: list[FileRec] = []
        self.owner: dict[int, int] = {}
        self.groups: dict[int, Group] = {}
        self.file_method: dict[int, str] = {}
        self._next_gid = 1
        self.stats = {"scanned": 0, "skipped": 0, "unreadable": 0}

    # ----------------------------------------------------------- کمکی

    def _tick(self, phase: str, done: int, total: int):
        if self.cancel.is_set():
            raise Cancelled()
        self._progress(phase, done, total)

    def _check(self):
        if self.cancel.is_set():
            raise Cancelled()

    # ----------------------------------------------------------- ۱) پیمایش

    def collect(self):
        seen: set[str] = set()
        roots = [os.path.abspath(f) for f in self.opt.folders]
        for root in roots:
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
                self._tick("در حال جست‌وجوی فایل‌ها", len(self.files), 0)

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
        return FileRec(path=full, size=st.st_size, mtime=st.st_mtime, ext=ext)

    # ----------------------------------------------------------- ۲) تگ‌ها

    def load_metadata(self):
        targets = [f for f in self.files if f.is_audio]
        total = len(targets)
        for i, rec in enumerate(targets):
            if i % 25 == 0:
                self._tick("در حال خواندن تگ‌های موسیقی", i, total)
            rec.meta = audio_mod.read_meta(rec.path)
        self._tick("در حال خواندن تگ‌های موسیقی", total, total)

    def load_payload_ranges(self):
        targets = [f for f in self.files if f.is_audio]
        total = len(targets)
        for i, rec in enumerate(targets):
            if i % 50 == 0:
                self._tick("در حال یافتن بخش صوتی فایل‌ها", i, total)
            rng = audio_mod.audio_payload_range(rec.path, rec.ext, rec.size)
            # اگر تگی وجود ندارد، این روش چیزی جز روش «یکسان کامل» نمی‌دهد
            if rng and not (rng[0] == 0 and rng[1] == rec.size):
                rec.payload = rng
        self._tick("در حال یافتن بخش صوتی فایل‌ها", total, total)

    # ----------------------------------------------------------- گروه‌بندی

    def _apply_bucket(self, idxs: list[int], method: str, allow_extend: bool = True):
        """یک دستهٔ هم‌کلید را به گروه تبدیل یا به گروه موجود اضافه می‌کند."""
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
        # فقط روشی ثبت می‌شود که واقعاً عضوی را وارد گروه کرده باشد. روشی که
        # صرفاً چیزی از قبل پیداشده را دوباره تأیید می‌کند نباید اطمینان گروه
        # را پایین بکشد — تأیید دوباره نشانهٔ ضعف نیست.
        # ادغام دو گروهِ از قبل موجود هم یک پیوند واقعی است و باید ثبت شود
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

    # ------------------------------------------------- روش ۱: یکسان کامل

    def detect_exact(self):
        by_size = self._bucket_by(lambda f: f.size or None)
        candidates = [i for group in by_size.values() for i in group]
        total = len(candidates)
        if not total:
            return
        done = 0
        for indices in by_size.values():
            self._check()
            by_quick: dict = {}
            for i in indices:
                rec = self.files[i]
                sig = quick_signature(rec.path, rec.size)
                done += 1
                if done % 20 == 0:
                    self._tick("مقایسهٔ محتوای فایل‌ها", done, total)
                if sig:
                    by_quick.setdefault(sig, []).append(i)
            for sub in by_quick.values():
                if len(sub) < 2:
                    continue
                by_full: dict = {}
                for i in sub:
                    rec = self.files[i]
                    digest = hash_range(rec.path, cancel=self.cancel)
                    if digest:
                        by_full.setdefault(digest, []).append(i)
                for final in by_full.values():
                    self._apply_bucket(final, "exact")
        self._tick("مقایسهٔ محتوای فایل‌ها", total, total)

    # ------------------------------------------- روش ۲: هش صوت بدون تگ

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
                rec = self.files[i]
                start, end = rec.payload
                digest = hash_range(rec.path, start, end, cancel=self.cancel)
                done += 1
                if done % 10 == 0:
                    self._tick("مقایسهٔ بخش صوتی (بدون تگ)", done, total)
                if digest:
                    by_hash.setdefault(digest, []).append(i)
            for final in by_hash.values():
                self._apply_bucket(final, "audio")
        self._tick("مقایسهٔ بخش صوتی (بدون تگ)", total, total)

    # ------------------------------------------------- روش ۳ و ۴: تگ‌ها

    def detect_tags(self):
        self._tick("مقایسهٔ تگ‌های موسیقی", 0, 3)
        buckets = self._bucket_by(
            lambda f: f.meta.tag_key() if (f.meta and f.is_audio) else ""
        )
        for indices in buckets.values():
            self._apply_bucket(indices, "tag")

        self._tick("مقایسهٔ تگ‌های موسیقی", 1, 3)
        # عنوان یکسان ولی آرتیست خالی
        buckets = self._bucket_by(
            lambda f: f.meta.title_key()
            if (f.meta and f.is_audio and not f.meta.tag_key()) else ""
        )
        for indices in buckets.values():
            self._apply_bucket(indices, "title")
        self._tick("مقایسهٔ تگ‌های موسیقی", 3, 3)

    # ------------------------------------------------- روش ۵: نام فایل

    def detect_name(self):
        self._tick("مقایسهٔ نام فایل‌ها", 0, 1)

        def key(f: FileRec) -> str:
            base = name_key(f.name)
            return f"{f.category}|{base}" if len(base) >= 3 else ""

        for indices in self._bucket_by(key).values():
            self._apply_bucket(indices, "name")
        self._tick("مقایسهٔ نام فایل‌ها", 1, 1)

    # --------------------------------------- روش ۶: مدت و حجم نزدیک

    def detect_duration(self):
        """برای فایل‌های بی‌نام و بی‌تگ: مدت پخش تقریباً برابر و حجم نزدیک."""
        self._tick("مقایسهٔ مدت پخش", 0, 1)
        pool = [
            i for i, f in enumerate(self.files)
            if i not in self.owner and f.is_audio and f.meta
            and f.meta.duration >= 20
        ]
        pool.sort(key=lambda i: self.files[i].meta.duration)
        for cluster in _anchor_clusters(
            pool, lambda i: self.files[i].meta.duration, abs_window=1.5
        ):
            # داخل هر خوشهٔ زمانی، حجم هم باید نزدیک باشد
            cluster.sort(key=lambda i: self.files[i].size)
            for sub in _anchor_clusters(
                cluster, lambda i: self.files[i].size, ratio=0.25
            ):
                self._apply_bucket(sub, "duration", allow_extend=False)
        self._tick("مقایسهٔ مدت پخش", 1, 1)

    # ------------------------------------------------- روش ۷: حجم مشابه

    def detect_size(self):
        self._tick("مقایسهٔ حجم فایل‌ها", 0, 1)
        tol = max(0.0, self.opt.size_tolerance) / 100.0
        pool = [
            i for i, f in enumerate(self.files)
            if i not in self.owner and f.size >= self.opt.min_size
        ]
        pool.sort(key=lambda i: self.files[i].size)
        for cluster in _anchor_clusters(
            pool, lambda i: self.files[i].size, ratio=tol
        ):
            by_cat: dict = {}
            for i in cluster:
                by_cat.setdefault(self.files[i].category, []).append(i)
            for sub in by_cat.values():
                self._apply_bucket(sub, "size", allow_extend=False)
        self._tick("مقایسهٔ حجم فایل‌ها", 1, 1)

    # ----------------------------------------------------------- اجرا

    def run(self) -> list[Group]:
        self.collect()
        if not self.files:
            return []

        need_meta = self.opt.use_tags or self.opt.use_duration
        if need_meta and any(f.is_audio for f in self.files):
            self.load_metadata()
        if self.opt.use_audio_hash and any(f.is_audio for f in self.files):
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

        for group in self.groups.values():
            group.files = [self.files[i] for i in group.members]

        result = [g for g in self.groups.values() if len(g.files) > 1]
        for group in result:
            group.files.sort(key=lambda f: (-f.size, f.path))
        result.sort(key=lambda g: (-g.confidence, -g.reclaimable))
        for index, group in enumerate(result, start=1):
            group.gid = index
        self._tick("پایان", 1, 1)
        return result


# ---------------------------------------------------------------- خوشه‌بندی

def _anchor_clusters(indices, valuefunc, ratio: float | None = None,
                     abs_window: float | None = None):
    """خوشه‌بندی لنگری روی لیستِ مرتب‌شده.

    برخلاف خوشه‌بندی زنجیره‌ای، پنجره نسبت به «لنگر» سنجیده می‌شود، پس
    اختلاف‌های کوچکِ پشت‌سرهم نمی‌توانند کل کتابخانه را در یک گروه ادغام کنند.
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
