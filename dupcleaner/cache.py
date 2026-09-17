# -*- coding: utf-8 -*-
"""SQLite cache for the expensive per-file work: hashes, tags, audio ranges.

Hashing a large music library is the slow part of a scan, and almost nothing in
it changes between two scans. A row is keyed by path and validated against the
file's size and modification time, so any edit — including one that leaves the
size alone — invalidates it.

Thread-safe: every method takes a lock and the connection is opened with
`check_same_thread=False`, because the scan engine reads tags from a thread
pool.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path      TEXT PRIMARY KEY,
    size      INTEGER NOT NULL,
    mtime_ns  INTEGER NOT NULL,
    quick     TEXT,
    full      TEXT,
    a_start   INTEGER,
    a_end     INTEGER,
    a_hash    TEXT,
    has_meta  INTEGER DEFAULT 0,
    artist    TEXT,
    title     TEXT,
    album     TEXT,
    track     TEXT,
    duration  REAL,
    bitrate   INTEGER,
    channels  INTEGER,
    samplerate INTEGER,
    has_cover INTEGER,
    tag_error TEXT,
    seen      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS files_seen ON files(seen);
"""

_FIELDS = ("quick", "full", "a_start", "a_end", "a_hash", "has_meta", "artist",
           "title", "album", "track", "duration", "bitrate", "channels",
           "samplerate", "has_cover", "tag_error")


class FileCache:
    """Keyed on (path, size, mtime_ns). A mismatch on either is a miss."""

    def __init__(self, db_path: str, enabled: bool = True):
        self.enabled = enabled
        self.path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self.hits = 0
        self.misses = 0
        if not enabled:
            return
        try:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False,
                                         timeout=10)
            self._conn.executescript(SCHEMA)
            # durability matters far less here than speed: a lost cache row
            # only costs a re-hash, never data
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=OFF")
            self._conn.commit()
        except (sqlite3.Error, OSError):
            self._conn = None
            self.enabled = False

    # ------------------------------------------------------------- read

    def get(self, path: str, size: int, mtime_ns: int) -> dict | None:
        if self._conn is None:
            return None
        key = os.path.normcase(path)
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT %s, size, mtime_ns FROM files WHERE path=?"
                    % ", ".join(_FIELDS), (key,)).fetchone()
            except sqlite3.Error:
                return None
        if row is None or row[-2] != size or row[-1] != mtime_ns:
            self.misses += 1
            return None
        self.hits += 1
        return dict(zip(_FIELDS, row))

    # ------------------------------------------------------------ write

    def put(self, path: str, size: int, mtime_ns: int, **fields) -> None:
        """Insert or update one file's cached values. Unknown keys are ignored."""
        if self._conn is None:
            return
        key = os.path.normcase(path)
        known = {k: v for k, v in fields.items() if k in _FIELDS}
        columns = ["path", "size", "mtime_ns", "seen"] + list(known)
        values = [key, size, mtime_ns, int(time.time())] + list(known.values())
        updates = ", ".join("%s=excluded.%s" % (c, c)
                            for c in ["size", "mtime_ns", "seen"] + list(known))
        sql = ("INSERT INTO files (%s) VALUES (%s) "
               "ON CONFLICT(path) DO UPDATE SET %s"
               % (", ".join(columns), ", ".join("?" * len(columns)), updates))
        with self._lock:
            try:
                self._conn.execute(sql, values)
            except sqlite3.Error:
                pass

    def commit(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------- maintenance

    def info(self) -> dict:
        """Row count and on-disk size, for the settings panel."""
        rows = 0
        if self._conn is not None:
            with self._lock:
                try:
                    rows = self._conn.execute(
                        "SELECT COUNT(*) FROM files").fetchone()[0]
                except sqlite3.Error:
                    rows = 0
        size = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                size += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        return {"rows": rows, "bytes": size, "hits": self.hits,
                "misses": self.misses, "enabled": bool(self._conn)}

    def prune(self, days: int = 90) -> int:
        """Drop rows not touched in `days`, and rows whose file is gone."""
        if self._conn is None:
            return 0
        cutoff = int(time.time()) - days * 86400
        removed = 0
        with self._lock:
            try:
                cur = self._conn.execute("DELETE FROM files WHERE seen < ?",
                                         (cutoff,))
                removed = cur.rowcount or 0
                self._conn.commit()
            except sqlite3.Error:
                pass
        return removed

    def clear(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.execute("DELETE FROM files")
                self._conn.commit()
                self._conn.execute("VACUUM")
            except sqlite3.Error:
                pass
        self.hits = self.misses = 0

    def close(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.commit()
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
