# -*- coding: utf-8 -*-
"""The window: an HTML page rendered by WebView2 through pywebview.

Replaces the tkinter UI of v1. Tk could not align Persian mixed with Latin —
and this app's results table is nothing but: Latin file names and paths,
`320 kbps`, Persian titles and Persian-digit sizes, all in one row.

CSS, JS and the Vazirmatn fonts are inlined into a single page, so nothing is
fetched at runtime and the frozen exe needs no side files.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import webview

from . import __version__
from . import keeper
from .audio import HAVE_MUTAGEN
from .cache import FileCache
from .i18n import DEFAULT_LANG, STRINGS
from .scanner import METHODS, Cancelled, Engine, ScanOptions
from .util import (_UNITS, format_date, human_bitrate, human_duration,
                   human_eta, human_size, short_folder)

FONTS = ((400, "Vazirmatn-Regular.woff2"), (500, "Vazirmatn-Medium.woff2"),
         (700, "Vazirmatn-Bold.woff2"))

# Groups sent to the page in one go. A very large library would otherwise
# serialize tens of megabytes of JSON through the bridge and stall the window.
MAX_GROUPS = 4000


def app_dir() -> Path:
    """Folder beside the app — next to the exe when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def resource(*parts) -> Path:
    """A bundled file; PyInstaller unpacks these into _MEIPASS."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


SETTINGS_PATH = app_dir() / "settings.json"
LOG_DIR = app_dir() / "logs"
CACHE_PATH = app_dir() / "cache" / "scan-cache.db"

DEFAULTS = {
    "lang": DEFAULT_LANG,
    "theme": "dark",
    "folders": [],
    "mode": "audio",
    "custom_exts": [".mp3", ".flac", ".m4a"],
    "min_kb": 64,
    "tolerance": 2,
    "workers": 8,
    "recursive": True,
    "skip_hidden": True,
    "use_cache": True,
    "to_trash": True,
    "auto_level": 70,
    "columns": [],
    "methods": {k: True for k in METHODS},
}


def load_settings() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    data["folders"] = [f for f in data.get("folders", []) if os.path.isdir(f)]
    return data


def save_settings(data: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ------------------------------------------------------------------- page

def build_html() -> str:
    """One self-contained page: CSS, JS and the fonts inlined."""
    ui = resource("ui")
    html = (ui / "index.html").read_text(encoding="utf-8")
    style = (ui / "style.css").read_text(encoding="utf-8")
    script = (ui / "app.js").read_text(encoding="utf-8")

    faces = []
    for weight, name in FONTS:
        font = ui / "fonts" / name
        if font.is_file():
            faces.append(
                "@font-face{font-family:'Vazirmatn';font-weight:%d;"
                "font-display:block;src:url(data:font/woff2;base64,%s) "
                "format('woff2')}"
                % (weight, base64.b64encode(font.read_bytes()).decode("ascii")))

    injected = (
        "window.I18N=%s;\nwindow.UNITS=%s;\nwindow.METHODS=%s;\n"
        "window.APP_VERSION=%s;\nwindow.HAVE_MUTAGEN=%s;"
        % (json.dumps(STRINGS, ensure_ascii=False),
           json.dumps(_UNITS, ensure_ascii=False),
           json.dumps([{"key": k, "confidence": v} for k, v in
                       sorted(METHODS.items(), key=lambda kv: -kv[1])]),
           json.dumps(__version__),
           "true" if HAVE_MUTAGEN else "false"))

    html = html.replace("/*__STYLE__*/", "".join(faces) + style)
    html = html.replace("/*__I18N__*/", injected)
    html = html.replace("/*__APP__*/", script)
    return html


# -------------------------------------------------------------------- api

class Api:
    """Called from the page as window.pywebview.api.<method>(...).

    Only public methods are exposed; everything else is underscored so
    pywebview does not walk into it.
    """

    def __init__(self, settings: dict):
        self._settings = settings
        self._window = None
        self._events: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._busy = False
        self._guard = threading.Lock()
        self._groups = []
        self._lang = settings.get("lang", DEFAULT_LANG)
        self._cache = FileCache(str(CACHE_PATH), settings.get("use_cache", True))

    # ------------------------------------------------------- plumbing

    def _push(self, event: dict) -> None:
        self._events.put(event)

    def _options(self, raw: dict) -> ScanOptions:
        methods = raw.get("methods") or {}
        return ScanOptions(
            folders=[f for f in raw.get("folders", []) if os.path.isdir(f)],
            recursive=bool(raw.get("recursive", True)),
            mode=raw.get("mode", "audio"),
            custom_exts=[e.lower() for e in raw.get("custom_exts", [])],
            min_size=max(0, int(raw.get("min_kb", 64) or 0)) * 1024,
            size_tolerance=max(0.0, float(raw.get("tolerance", 2) or 0)),
            skip_hidden=bool(raw.get("skip_hidden", True)),
            workers=max(1, min(32, int(raw.get("workers", 8) or 8))),
            use_cache=bool(raw.get("use_cache", True)),
            use_exact=bool(methods.get("exact", True)),
            use_audio_hash=bool(methods.get("audio", True)),
            use_tags=bool(methods.get("tag", True)),
            use_feat=bool(methods.get("feat", True)),
            use_name=bool(methods.get("name", True)),
            use_duration=bool(methods.get("duration", True)),
            use_size=bool(methods.get("size", True)),
        )

    def _payload(self, groups) -> list:
        """Groups as plain dicts, already formatted for the current language."""
        lang = self._lang
        out = []
        for group in groups[:MAX_GROUPS]:
            best = keeper.best_index(group)
            files = []
            for i, rec in enumerate(group.files):
                meta = rec.meta
                files.append({
                    "path": rec.path,
                    "name": rec.name,
                    "folder": short_folder(rec.folder),
                    "ext": rec.ext.lstrip(".").upper(),
                    "method": rec.match_method,
                    "method_label": _method_label(lang, rec.match_method),
                    "tags": meta.display if meta else "",
                    "album": (meta.album if meta else "") or "",
                    "size": human_size(rec.size, lang),
                    "size_raw": rec.size,
                    "duration": human_duration(meta.duration if meta else 0, lang),
                    "duration_raw": (meta.duration if meta else 0) or 0,
                    "bitrate": human_bitrate(meta.bitrate if meta else 0, lang),
                    "bitrate_raw": (meta.bitrate if meta else 0) or 0,
                    "date": format_date(rec.mtime, lang),
                    "date_raw": rec.mtime,
                    "best": i == best,
                })
            out.append({
                "gid": group.gid,
                "confidence": group.confidence,
                "combined": group.combined,
                "method": group.primary_method,
                "method_label": _method_label(lang, group.primary_method),
                "reclaimable": human_size(group.reclaimable, lang),
                "reclaimable_raw": group.reclaimable,
                "files": files,
            })
        return out

    # ------------------------------------------------------- read/boot

    def bootstrap(self) -> dict:
        return {"settings": self._settings, "version": __version__,
                "have_mutagen": HAVE_MUTAGEN}

    def poll(self) -> list:
        drained = []
        while True:
            try:
                drained.append(self._events.get_nowait())
            except queue.Empty:
                break
        return drained

    def set_lang(self, lang: str) -> dict:
        self._lang = lang if lang in STRINGS else DEFAULT_LANG
        self._settings["lang"] = self._lang
        # re-emit the current results so dates, sizes and labels follow
        if self._groups:
            self._push({"t": "relabel", "groups": self._payload(self._groups)})
        return {"ok": True}

    def save_settings(self, data: dict) -> dict:
        if isinstance(data, dict):
            for key in DEFAULTS:
                if key in data:
                    self._settings[key] = data[key]
            save_settings(self._settings)
        return {"ok": True}

    def cache_info(self) -> dict:
        info = self._cache.info()
        info["human"] = human_size(info["bytes"], self._lang)
        return info

    def clear_cache(self) -> dict:
        self._cache.clear()
        return {"ok": True}

    # ---------------------------------------------------------- dialogs

    def pick_folder(self) -> list:
        if self._window is None:
            return []
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None)
        if kind is None:                       # older pywebview
            kind = webview.FOLDER_DIALOG
        result = self._window.create_file_dialog(kind)
        return [str(p) for p in (result or [])]

    def folders_of(self, paths: list) -> list:
        """Map dropped items to folders — a dropped file means its folder."""
        out = []
        for path in paths or []:
            if os.path.isdir(path):
                out.append(os.path.normpath(path))
            elif os.path.isfile(path):
                out.append(os.path.dirname(os.path.abspath(path)))
        seen, unique = set(), []
        for path in out:
            key = os.path.normcase(path)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def confirm(self, message: str) -> bool:
        if self._window is None:
            return False
        return bool(self._window.create_confirmation_dialog(
            _t(self._lang, "dlg.confirm"), message))

    # ------------------------------------------------------------ scan

    def start_scan(self, raw: dict) -> dict:
        with self._guard:
            if self._busy:
                return {"ok": False, "message": "busy"}
            self._busy = True
        self._cancel = threading.Event()
        options = self._options(raw)
        self._cache.enabled = options.use_cache
        started = time.time()
        state = {"last": 0.0}

        def progress(phase: str, done: int, total: int):
            now = time.time()
            # throttle: the walk phase fires per directory
            if now - state["last"] < 0.12 and not (total and done >= total):
                return
            state["last"] = now
            eta = ""
            if total and done:
                elapsed = now - started
                if elapsed > 2 and done < total:
                    eta = human_eta(elapsed / done * (total - done), self._lang)
            self._push({"t": "progress", "phase": phase, "done": done,
                        "total": total, "eta": eta})

        def worker():
            try:
                engine = Engine(options, progress=progress, cancel=self._cancel,
                                cache=self._cache)
                groups = engine.run()
                self._groups = groups
                stats = dict(engine.stats)
                stats["dupes"] = sum(len(g.files) for g in groups)
                stats["reclaimable"] = human_size(
                    sum(g.reclaimable for g in groups), self._lang)
                self._push({
                    "t": "done",
                    "groups": self._payload(groups),
                    "total_groups": len(groups),
                    "capped": max(0, len(groups) - MAX_GROUPS),
                    "stats": stats,
                })
            except Cancelled:
                self._push({"t": "cancelled"})
            except Exception as exc:
                self._log(traceback.format_exc())
                self._push({"t": "failed",
                            "message": f"{type(exc).__name__}: {exc}"})
            finally:
                self._busy = False

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def cancel_scan(self) -> dict:
        self._cancel.set()
        return {"ok": True}

    # ---------------------------------------------------------- actions

    def play(self, path: str) -> dict:
        try:
            os.startfile(path)          # noqa: S606 - opening the user's own file
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

    def reveal(self, path: str) -> dict:
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

    def copy_path(self, path: str) -> dict:
        """Clipboard fallback for when the page's clipboard API is blocked."""
        try:
            subprocess.run("clip", input=path.encode("utf-16-le"), check=False)
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

    def delete_files(self, paths: list, to_trash: bool = True) -> dict:
        lang = self._lang
        paths = [p for p in (paths or []) if p]
        if not paths:
            return {"cancelled": True}

        if to_trash and not keeper.HAVE_TRASH:
            if not self.confirm(_t(lang, "dlg.notrash")):
                return {"cancelled": True}
            to_trash = False

        total = 0
        for path in paths:
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        how = _t(lang, "dlg.delete.trash" if to_trash else "dlg.delete.perm")
        body = _t(lang, "dlg.delete.body", n=len(paths),
                  size=human_size(total, lang), how=how)
        if not self.confirm(body):
            return {"cancelled": True}
        if not to_trash and not self.confirm(_t(lang, "dlg.delete.permconfirm")):
            return {"cancelled": True}

        result = keeper.delete_files(paths, to_recycle_bin=to_trash,
                                     log_dir=str(LOG_DIR), lang=lang)
        gone = set(result.deleted)
        for group in self._groups:
            group.files = [f for f in group.files if f.path not in gone]
        self._groups = [g for g in self._groups if len(g.files) > 1]
        for index, group in enumerate(self._groups, start=1):
            group.gid = index

        return {"deleted": result.deleted,
                "failed": [[p, e] for p, e in result.failed],
                "freed": human_size(result.freed, lang)}

    def export_report(self, selected: list) -> dict:
        if self._window is None or not self._groups:
            return {"ok": False}
        kind = getattr(getattr(webview, "FileDialog", None), "SAVE", None)
        if kind is None:
            kind = webview.SAVE_DIALOG
        stamp = time.strftime("%Y%m%d-%H%M")
        target = self._window.create_file_dialog(
            kind, save_filename=f"duplicates-{stamp}.csv",
            file_types=("CSV (*.csv)",))
        if not target:
            return {"ok": False}
        path = target if isinstance(target, str) else target[0]
        chosen = {p: True for p in (selected or [])}
        try:
            keeper.export_report(self._groups, path, chosen, self._lang)
            return {"ok": True, "path": path}
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

    # ------------------------------------------------------------ misc

    def _log(self, text: str) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_DIR / ("gui-%s.log" % time.strftime("%Y%m%d")), "a",
                      encoding="utf-8") as fh:
                fh.write("%s %s\n" % (time.strftime("%H:%M:%S"), str(text).rstrip()))
        except OSError:
            pass

    def _on_closing(self):
        if self._busy and self._window is not None:
            if not self._window.create_confirmation_dialog(
                    _t(self._lang, "app.title"),
                    _t(self._lang, "dlg.closebusy")):
                return False
        self._cancel.set()
        save_settings(self._settings)
        self._cache.close()
        return True


def _t(lang: str, key: str, **kw) -> str:
    from .i18n import t
    return t(lang, key, **kw)


def _method_label(lang: str, method: str) -> str:
    if not method:
        return "—"
    return _t(lang, "method." + method)


# ------------------------------------------------------------------ window

def _window_geometry() -> tuple:
    """(width, height, x, y) inside the work area — the screen minus the taskbar.

    Sizing from the full screen height put the bottom of the window under the
    taskbar on his 1366x768 screen.
    """
    try:
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        left, top = rect.left, rect.top
        aw, ah = rect.right - rect.left, rect.bottom - rect.top
    except (AttributeError, OSError):
        left, top, aw, ah = 0, 0, 1366, 728
    width, height = min(1280, aw - 40), min(820, ah - 16)
    return width, height, left + (aw - width) // 2, top + max(0, (ah - height) // 2)


def run() -> int:
    settings = load_settings()
    api = Api(settings)
    width, height, x, y = _window_geometry()
    background = "#0f1216" if settings.get("theme", "dark") != "light" else "#f4f6fa"
    window = webview.create_window(
        _t(settings.get("lang", DEFAULT_LANG), "app.title"),
        html=build_html(), js_api=api, width=width, height=height, x=x, y=y,
        min_size=(940, 600), background_color=background)
    api._window = window
    window.events.closing += api._on_closing
    webview.start(gui="edgechromium")
    return 0
