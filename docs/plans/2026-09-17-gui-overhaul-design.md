# Duplicate Cleaner v2 — GUI overhaul design

> 2026-09-17 · agreed before implementation

## Why replace the GUI

v1 shipped a tkinter UI. Experience on an earlier Persian desktop app showed
that Tk is acceptable only for short, pure-Persian labels; anything mixing
Persian with Latin breaks alignment, and the available fonts were poor.

This app's results table is the worst case for that: Latin file names and paths,
`320 kbps`, Persian artist/title, Persian-digit sizes and dates, all in one row.
On top of that v2 adds an embedded Persian font, a real dark theme and a
language switch — three things Tk either cannot do or does badly.

**Decision: replace the GUI layer with pywebview + WebView2 + HTML.**

Verified before writing any of it (`scratchpad/webview_probe.py`, screenshot):
RTL column order, `<bdi>`-wrapped Latin paths, `320 kbps` not reversed, a Latin
path inline in a Persian sentence, and a sentence starting with a Persian digit
all render correctly. Those are exactly the four cases that failed in Tk.

The scan engine is untouched. Only `gui.py` (1069 lines) is deleted.

## Scope

Asked for:
1. Show/hide result columns.
2. More professional, better-looking UI.
3. A real Persian font (Vazirmatn, embedded).
4. Persian + English.
5. Dark/light theme.

Added alongside:
6. Hash cache so a re-scan of the same folder is fast.
7. Parallel tag reading.
8. Jalali dates in Persian, Gregorian in English.

Explicitly **not** doing: undo-last-delete, quarantine-move mode.
Still not doing: acoustic fingerprinting (needs an external `fpcalc` binary).

## Architecture

```
dupcleaner/
  scanner.py    engine — modified: cache + thread pool, method keys not labels
  audio.py      unchanged
  hashing.py    unchanged
  keeper.py     modified: localized CSV export
  util.py       modified: locale-aware size/duration/date formatting
  jalali.py     NEW  — Gregorian to Jalali, self-test passes
  i18n.py       NEW  — fa/en strings, the single source of truth
  cache.py      NEW  — SQLite hash/meta cache
  webapp.py     NEW  — pywebview window + Api bridge (replaces gui.py)
  ui/
    index.html  NEW
    style.css   NEW
    app.js      NEW
    fonts/      Vazirmatn Regular/Medium/Bold woff2, inlined as base64
```

### Strings

`i18n.py` holds both languages. `build_html()` injects **both** dictionaries
into the page, so switching language is instant with no IPC round-trip. Python
uses the same dictionary for CSV headers and native dialog text, so the two
sides cannot drift.

Detector labels move out of `scanner.METHOD_INFO` into i18n keys
(`method.exact`, `method.audio`, …). The engine returns keys; the UI localizes.

### Async

The worker thread pushes dicts onto a `queue.Queue` and the page drains it
with `api.poll()` on a timer. Chosen over
`evaluate_js` from a worker thread because it keeps all UI updates on one path
and survives a slow renderer.

### Hash cache

SQLite next to the app, keyed by `(path, size, mtime_ns)`. Stores the quick
signature, full hash, audio-payload range and hash, and the music tags. Any
size or mtime change invalidates the row. This is the difference between a
re-scan costing minutes and costing seconds on a large library.

Exposed in the UI: cache size, row count, and a clear button.

### Parallelism

`ThreadPoolExecutor` for tag reading and payload-range detection — both are
I/O-bound, and mutagen releases the GIL on reads. Worker count is a setting
(default 8) because too many threads hurt on a spinning disk. Hashing stays
serial; the cache is the answer there, not more threads.

## What stays the same

Every safety property of v1 is unchanged and must remain: recycle bin by
default, at least one file surviving every group, weakest-link confidence,
fuzzy detectors never extending a group, and the deletion log.
