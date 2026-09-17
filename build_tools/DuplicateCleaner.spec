# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller configuration for the single-file executable.

The UI folder must ship inside the bundle: webapp.build_html() reads
index.html, style.css, app.js and the Vazirmatn fonts out of _MEIPASS and
inlines them into one page.
"""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
UI = os.path.join(ROOT, "dupcleaner", "ui")

a = Analysis(
    [os.path.join(ROOT, "run.pyw")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "app.ico"), "."),
        (os.path.join(UI, "index.html"), "ui"),
        (os.path.join(UI, "style.css"), "ui"),
        (os.path.join(UI, "app.js"), "ui"),
        (os.path.join(UI, "fonts"), os.path.join("ui", "fonts")),
    ],
    hiddenimports=[
        "webview", "webview.platforms.edgechromium",
        "clr_loader", "pythonnet",
        "mutagen", "send2trash",
    ],
    hookspath=[],
    runtime_hooks=[],
    # Heavy modules the app never touches. tkinter is gone with the old GUI.
    excludes=["tkinter", "numpy", "PIL", "matplotlib", "scipy", "pandas",
              "pytest", "unittest", "pydoc_data"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DuplicateCleaner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,               # no console window
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "app.ico"),
)
