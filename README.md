<div align="center">

<img src="app.png" width="110" alt="Duplicate Cleaner">

# Duplicate Cleaner

**Find duplicate files — especially duplicate music — even when the files have
no usable name and no tags.**

**English** · [فارسی](README.fa.md)

</div>

---

## The problem this solves

A music folder that has been merged, re-downloaded and backed up a few times
ends up with the same song stored several times over. Ordinary duplicate
finders compare file size or a hash of the whole file, and on music that fails
constantly:

- The same song saved twice with different tags has **different bytes and a
  different size**. A hash comparison says they are unrelated.
- Files arrive named `track_0001.mp3` or `00000123.mp3`, so name matching has
  nothing to work with.
- Persian tags are written inconsistently — Arabic `ي` against Persian `ی`,
  Arabic-Indic digits against Latin ones, a zero-width non-joiner here but not
  there. Two identical titles compare as different strings.

This tool attacks the problem from seven directions at once and tells you, per
file, which one found it.

<div align="center">
<img src="docs/screenshot.png" width="900" alt="Scan results grouped by confidence">
<br><em>Persian, dark. The app is fully bilingual — <a href="docs/screenshot-en.png">English, light</a>.</em>
</div>

---

## The interesting part: hashing audio without the tags

This is the detector that catches what other tools miss.

An MP3 is not just audio. It is an ID3v2 tag block, then the audio frames, then
possibly an ID3v1 tag, an APEv2 tag and a Lyrics3 block. Edit the artist name
or embed a cover image and the tag region changes size — so the file size
changes, and every byte offset shifts. The audio itself is untouched, but the
file now looks completely different to any hash.

So the scanner parses the container, locates the byte range that is *only*
audio, and hashes that range alone:

| Format | What gets skipped |
|---|---|
| MP3 | ID3v2 header (syncsafe length, optional footer), ID3v1, APEv2, Lyrics3v2 |
| FLAC | every metadata block, walked until the last-block flag |
| MP4 / M4A | everything except the `mdat` box |
| WAV | every RIFF chunk except `data` |

Two copies of one song with completely different tags produce **the same
hash**. That is a 98% match, and no amount of size or name comparison would
have found it.

Anything else falls back to hashing the whole file.

## How it decides

Seven detectors run strongest first. Each file is claimed by the strongest
method that found it, and the results table shows that method per file — so a
group is never a black box.

| Confidence | Method | What it compares |
|---|---|---|
| 100% | Identical content | blake2b over the whole file; byte-for-byte identical |
| 98% | Same audio, different tags | the tag-stripped audio range described above |
| 85% | Music tags | artist + title, after Persian normalization |
| 72% | Title only | when the artist field is empty |
| 70% | File name | name after stripping `Copy`, `(1)`, `[320]`, site names |
| 58% | Duration + size | for untagged, badly named files: ±1.5 s and ±25% |
| 40% | Size only | last resort, within your tolerance — flagged as suspect |

### Persian normalization

Before any text comparison, tags and names are folded so these all match:
Arabic `ي ك ة` against Persian `ی ک ه`; Persian, Arabic and Latin digits;
zero-width non-joiners, diacritics and bidi control characters; and filler like
`Official Video`, `[320]`, `HQ` and download-site names.

### Confidence is the weakest link, not the strongest

If a group was built by more than one method it is labelled *combined*, and its
percentage is that of the **weakest** connection in it.

Three files that are byte-identical plus a fourth that joined only by matching
tags is an **85%** group, not a 100% one — because that fourth file is not
certain. Reporting the strongest method there would be an invitation to delete
something unverified.

A method is only credited to a group if it actually brought a new member in.
A detector that merely re-confirms files another detector already grouped does
not drag the displayed confidence down.

### Why the fuzzy detectors never extend a group

The 58% and 40% detectors can create groups only out of files that no stronger
method claimed. They can never attach a file to an existing group.

This is deliberate. During testing, allowing it merged two unrelated 30-second
songs that happened to share a size band. Clustering is also anchored rather
than chained — each window is measured from the cluster's first file, so many
small consecutive differences cannot snowball into one enormous group.

## Choosing which copy to keep

`Auto-select` keeps one file per group and marks the rest, scoring in this
order: name shows no copy marker → not sitting in a temp/download folder →
higher bitrate → more complete tags → has cover art → larger → shallower path
and older file. The survivor is marked with a star.

Low-confidence groups are **left untouched by default** (threshold 70%,
adjustable). A 40% size-only match is a guess, and pre-ticking guesses is how
accidents happen.

## Safety

- Deletions go to the **Recycle Bin** by default. Permanent deletion is a
  separate option behind two confirmations.
- **At least one file always survives each group.** Marking the last remaining
  copy is blocked; deleting an entire group needs a specific right-click action
  with its own confirmation.
- Every deletion is logged to `logs/deleted-YYYY-MM-DD.csv`.
- Windows system folders (`Windows`, `Program Files`, `$RECYCLE.BIN`,
  `System Volume Information`, `AppData`, …) are never scanned.
- Hidden and system files are skipped by default.

## The interface

- **Persian and English**, switched instantly — both dictionaries live in the
  page, so nothing reloads. Dates follow the language: Jalali with Persian
  digits in Persian, ISO in English.
- **Dark, light, or match Windows.**
- **Show or hide any column.** Ten are available; six are on by default so each
  one stays wide enough to read. Click a header to sort.
- **Drag folders onto the window** to add them.
- Vazirmatn is embedded in the page, so Persian looks the same on any machine.

## Speed

Two things keep a large library manageable:

- **A cache.** Hashes, audio ranges and tags are stored in SQLite keyed by
  path, size and modification time. Re-scanning the same folder skips
  everything that has not changed — in testing, a second pass ran roughly 70×
  faster. Any edit to a file invalidates just that row. Clear it from Settings.
- **Parallel tag reading.** Tag and container parsing run in a thread pool
  (8 workers by default, adjustable — use fewer on a spinning disk).

Neither changes what the scan finds; that is checked by a test which runs the
same library with the cache on and off, and with one worker and eight.

## Running it

**From a release** — download `DuplicateCleaner.exe` and run it. Single file,
no installer, no Python. Needs the Microsoft Edge WebView2 runtime, which is
already present on Windows 10 and 11.

**From source** — Windows, Python 3.10+ (developed on 3.12):

```bash
pip install -r requirements.txt
python run.pyw
```

Or double-click `اجرا.bat`, which finds a working interpreter and installs the
dependencies if they are missing.

`mutagen` and `send2trash` are optional: without the first, the tag and
duration detectors switch off and the app says so; without the second, the
Recycle Bin option is unavailable.

## Building the executable

```bash
build_tools/build_exe.bat
```

Produces `dist/DuplicateCleaner.exe` — about 15 MB, one file, no console
window. The icon is drawn by `build_tools/make_icon.py` with Pillow, so there
is no binary image asset to keep in sync.

> If the build fails with `PermissionError`, a previous copy of the executable
> is still running. Close it and delete `dist/DuplicateCleaner.exe` first.

## Layout

| Path | Responsibility |
|---|---|
| `run.pyw` | Entry point |
| `dupcleaner/scanner.py` | Scan engine: walking, the seven detectors, grouping |
| `dupcleaner/audio.py` | Tag reading and locating the audio byte range |
| `dupcleaner/hashing.py` | blake2b over a byte range, plus a cheap pre-filter |
| `dupcleaner/cache.py` | SQLite cache of hashes, tags and audio ranges |
| `dupcleaner/keeper.py` | "Best copy" scoring, safe deletion, CSV report |
| `dupcleaner/util.py` | Persian normalization, locale-aware formatting |
| `dupcleaner/jalali.py` | Gregorian → Jalali conversion, dependency-free |
| `dupcleaner/i18n.py` | Every string, in both languages |
| `dupcleaner/webapp.py` | The window: pywebview + WebView2, and the JS bridge |
| `dupcleaner/ui/` | index.html, style.css, app.js, Vazirmatn |
| `build_tools/` | Icon generation and PyInstaller configuration |

The window is an HTML page rendered by WebView2 through pywebview. Version 1
used tkinter; it could not align Persian mixed with Latin, and this table is
nothing but that — Latin file names and paths, `320 kbps`, Persian titles and
Persian-digit sizes, all in one row.

## Working with the results table

| Action | How |
|---|---|
| Mark one file for deletion | Click its checkbox |
| Mark a whole group | Click the checkbox on the group row |
| Collapse a group | Click the group row |
| Play a file | Double-click it |
| Show it in Explorer | Right-click → show in Explorer |
| Keep only this one | Right-click → keep only this |
| Sort | Click any column header |
| Filter by confidence | The dropdown above the table |
| Search | Matches name, path, artist, title and album |
| Re-scan | `F5` |
| Export a report | `Save report` — CSV in UTF-8 with BOM, so Excel reads Persian |

## Notes on correctness

The engine is tested against a generated library built with ffmpeg that covers
every detector, plus deliberate near-misses that must *not* group. Deletion is
tested for the Recycle Bin, permanent removal, Persian filenames, locked files
and missing files; the scanner against corrupt MP3s, zero-byte files, empty and
nonexistent folders, the same folder supplied twice, and cancellation mid-scan.
The interface has its own suite of 21 checks driven through the real page,
covering column toggling, sorting, search, filtering, the survivor guard,
the auto-select threshold, language and theme switching, settings persistence
and an actual deletion.

Not implemented: acoustic fingerprinting. The same song from a different encode
or source is caught by tags, name or duration, but never by hash. Adding it
would mean shipping an external binary such as `fpcalc`, so it would have to be
optional.

## Credits

The interface embeds [Vazirmatn](https://github.com/rastikerdar/vazirmatn)
by Saber Rastikerdar, licensed under the SIL Open Font License 1.1 — the
licence travels with the font in
[`dupcleaner/ui/fonts/OFL.txt`](dupcleaner/ui/fonts/OFL.txt).

## License

MIT — see [LICENSE](LICENSE). The bundled font keeps its own licence.
