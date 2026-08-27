# -*- mode: python ; coding: utf-8 -*-
"""پیکربندی PyInstaller برای ساخت یک فایل اجرایی تک‌تکه."""

import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "run.pyw")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "app.ico"), ".")],
    hiddenimports=["mutagen", "send2trash", "tkinter", "tkinter.ttk"],
    hookspath=[],
    runtime_hooks=[],
    # ماژول‌های سنگینی که برنامه به آن‌ها نیاز ندارد
    excludes=["numpy", "PIL", "matplotlib", "scipy", "pandas",
              "pytest", "setuptools", "pip", "unittest", "email",
              "http", "xml", "pydoc_data"],
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
    console=False,               # بدون پنجرهٔ کنسول
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "app.ico"),
)
