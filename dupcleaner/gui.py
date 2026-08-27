# -*- coding: utf-8 -*-
"""رابط کاربری فارسی برنامهٔ حذف فایل‌های تکراری."""

from __future__ import annotations

import datetime as _dt
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __app_name__, __version__
from . import keeper
from .audio import HAVE_MUTAGEN
from .scanner import METHOD_INFO, Cancelled, Engine, Group, ScanOptions
from .util import fa_digits, human_duration, human_size

UI_FONT = ("Segoe UI", 10)
UI_FONT_BOLD = ("Segoe UI", 10, "bold")
UI_FONT_SMALL = ("Segoe UI", 9)
TITLE_FONT = ("Segoe UI", 11, "bold")

BG = "#f4f6f9"
CARD = "#ffffff"
ACCENT = "#2563eb"
DANGER = "#dc2626"
OK = "#059669"

CHECKED = "☑"
UNCHECKED = "☐"
PARTIAL = "◪"

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
LOG_DIR = os.path.join(APP_DIR, "logs")


def _app_dir() -> str:
    """پوشهٔ کنار برنامه — هم در حالت اسکریپت و هم در حالت exe."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return APP_DIR


def _resource(name: str) -> str:
    """مسیر فایل همراه برنامه؛ در حالت exe داخل بستهٔ موقت PyInstaller است."""
    base = getattr(sys, "_MEIPASS", APP_DIR)
    return os.path.join(base, name)


class DuplicateCleanerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{__app_name__} — نسخهٔ {fa_digits(__version__)}")
        self._fit_to_screen(1280, 780)
        self.minsize(980, 600)
        self.configure(bg=BG)
        try:
            self.iconbitmap(_resource("app.ico"))
        except Exception:
            pass

        self.groups: list[Group] = []
        self.selected: dict[str, bool] = {}
        self.item_of_path: dict[str, str] = {}
        self.path_of_item: dict[str, str] = {}
        self.group_of_item: dict[str, Group] = {}
        self.item_of_group: dict[int, str] = {}
        self.file_of_path: dict[str, object] = {}

        self.msg_queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.scan_thread: threading.Thread | None = None
        self.scanning = False

        self._build_styles()
        self._build_ui()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._pump)

        if not HAVE_MUTAGEN:
            self.after(400, lambda: messagebox.showwarning(
                "کتابخانهٔ تگ‌خوان یافت نشد",
                "کتابخانهٔ mutagen نصب نیست، بنابراین تگ‌های موسیقی خوانده "
                "نمی‌شوند و مقایسه فقط بر اساس محتوا، نام و حجم انجام می‌شود.\n\n"
                "برای نصب:\n  pip install mutagen",
                parent=self))

    # ------------------------------------------------------------ ظاهر

    def _fit_to_screen(self, want_w: int, want_h: int):
        """پنجره را در صفحه جا می‌دهد؛ روی نمایشگرهای کوچک هم لبه‌ها نمی‌برد."""
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(want_w, screen_w - 60)
        height = min(want_h, screen_h - 90)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _build_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=UI_FONT, background=BG)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="flat")
        style.configure("TLabel", background=BG, font=UI_FONT)
        style.configure("Card.TLabel", background=CARD, font=UI_FONT)
        style.configure("Title.TLabel", background=CARD, font=TITLE_FONT,
                        foreground="#111827")
        style.configure("Hint.TLabel", background=CARD, font=UI_FONT_SMALL,
                        foreground="#6b7280")
        style.configure("TButton", font=UI_FONT, padding=(12, 6))
        style.configure("Accent.TButton", font=UI_FONT_BOLD, padding=(16, 8))
        style.map("Accent.TButton",
                  background=[("!disabled", ACCENT), ("disabled", "#9ca3af")],
                  foreground=[("!disabled", "white")])
        style.configure("Danger.TButton", font=UI_FONT_BOLD, padding=(16, 8))
        style.map("Danger.TButton",
                  background=[("!disabled", DANGER), ("disabled", "#9ca3af")],
                  foreground=[("!disabled", "white")])
        style.configure("TCheckbutton", background=CARD, font=UI_FONT)
        style.configure("TRadiobutton", background=CARD, font=UI_FONT)
        style.configure("TLabelframe", background=CARD, relief="solid",
                        borderwidth=1, bordercolor="#e5e7eb")
        style.configure("TLabelframe.Label", background=CARD, font=UI_FONT_BOLD,
                        foreground="#374151")
        style.configure("Treeview", font=UI_FONT, rowheight=26,
                        background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=UI_FONT_BOLD, padding=(4, 6))
        style.configure("TProgressbar", background=ACCENT, thickness=14)

    # ------------------------------------------------------------ چیدمان

    def _build_ui(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        top = ttk.Frame(outer)
        top.pack(fill="x")
        self._build_folders(top)
        self._build_options(top)

        self._build_actionbar(outer)
        # نوارهای پایین قبل از جدول ساخته می‌شوند تا Tk فضایشان را تضمین کند؛
        # وگرنه روی نمایشگرهای کوتاه، جدولِ کشسان آن‌ها را از پنجره بیرون می‌اندازد.
        self._build_statusbar(outer)
        self._build_bottombar(outer)
        self._build_results(outer)

    # -------------------------------------------------- بخش پوشه‌ها

    def _build_folders(self, parent):
        box = ttk.Labelframe(parent, text="  پوشه‌های مورد جست‌وجو  ", padding=10)
        box.pack(side="left", fill="both", expand=True, padx=(0, 8))

        row = ttk.Frame(box, style="Card.TFrame")
        row.pack(fill="both", expand=True)

        self.folder_list = tk.Listbox(
            row, height=5, font=UI_FONT_SMALL, activestyle="none",
            selectmode="extended", bg="white", relief="solid", bd=1,
            highlightthickness=0)
        self.folder_list.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(row, orient="vertical",
                               command=self.folder_list.yview)
        scroll.pack(side="left", fill="y")
        self.folder_list.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(row, style="Card.TFrame")
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="افزودن پوشه…",
                   command=self.add_folder).pack(fill="x", pady=(0, 4))
        ttk.Button(buttons, text="حذف انتخاب‌شده",
                   command=self.remove_folder).pack(fill="x", pady=(0, 4))
        ttk.Button(buttons, text="پاک کردن همه",
                   command=self.clear_folders).pack(fill="x")

    # -------------------------------------------------- بخش تنظیمات

    def _build_options(self, parent):
        box = ttk.Labelframe(parent, text="  تنظیمات اسکن  ", padding=10)
        box.pack(side="left", fill="both")

        left = ttk.Frame(box, style="Card.TFrame")
        left.pack(side="left", fill="y", padx=(0, 18))

        ttk.Label(left, text="چه فایل‌هایی بررسی شوند؟",
                  style="Card.TLabel", font=UI_FONT_BOLD).pack(anchor="e")
        self.mode_var = tk.StringVar(value="audio")
        for value, label in (("audio", "فقط فایل‌های موسیقی"),
                             ("all", "همهٔ فایل‌ها"),
                             ("custom", "پسوند دلخواه:")):
            ttk.Radiobutton(left, text=label, value=value,
                            variable=self.mode_var,
                            command=self._sync_mode).pack(anchor="e", pady=1)
        self.custom_ext = tk.StringVar(value=".mp3 .flac .m4a")
        self.custom_entry = ttk.Entry(left, textvariable=self.custom_ext,
                                      width=24, font=UI_FONT_SMALL)
        self.custom_entry.pack(anchor="e", pady=(2, 8))

        size_row = ttk.Frame(left, style="Card.TFrame")
        size_row.pack(anchor="e", pady=(4, 0))
        self.min_size_var = tk.StringVar(value="64")
        ttk.Label(size_row, text="کیلوبایت", style="Card.TLabel").pack(side="left")
        ttk.Entry(size_row, textvariable=self.min_size_var, width=7,
                  justify="center").pack(side="left", padx=4)
        ttk.Label(size_row, text=": حداقل حجم فایل",
                  style="Card.TLabel").pack(side="left")

        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="بررسی زیرپوشه‌ها", variable=self.recursive_var
                        ).pack(anchor="e", pady=(6, 0))
        self.hidden_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="نادیده گرفتن فایل‌های مخفی و سیستمی",
                        variable=self.hidden_var).pack(anchor="e")

        right = ttk.Frame(box, style="Card.TFrame")
        right.pack(side="left", fill="y")
        ttk.Label(right, text="روش‌های تشخیص تکراری",
                  style="Card.TLabel", font=UI_FONT_BOLD).pack(anchor="e")

        self.m_exact = tk.BooleanVar(value=True)
        self.m_audio = tk.BooleanVar(value=True)
        self.m_tags = tk.BooleanVar(value=True)
        self.m_name = tk.BooleanVar(value=True)
        self.m_duration = tk.BooleanVar(value=True)
        self.m_size = tk.BooleanVar(value=True)

        checks = [
            (self.m_exact, "محتوای کاملاً یکسان (۱۰۰٪)"),
            (self.m_audio, "صوت یکسان با تگ متفاوت (۹۸٪)"),
            (self.m_tags, "تگ موسیقی: آرتیست و عنوان (۸۵٪)"),
            (self.m_name, "نام فایل مشابه (۷۰٪)"),
            (self.m_duration, "مدت پخش و حجم نزدیک (۵۸٪)"),
            (self.m_size, "فقط حجم مشابه (۴۰٪)"),
        ]
        for var, label in checks:
            ttk.Checkbutton(right, text=label, variable=var).pack(anchor="e", pady=1)

        tol_row = ttk.Frame(right, style="Card.TFrame")
        tol_row.pack(anchor="e", pady=(6, 0))
        self.tol_var = tk.StringVar(value="2")
        ttk.Label(tol_row, text="درصد", style="Card.TLabel").pack(side="left")
        ttk.Entry(tol_row, textvariable=self.tol_var, width=5,
                  justify="center").pack(side="left", padx=4)
        ttk.Label(tol_row, text="± : اختلاف مجاز حجم",
                  style="Card.TLabel").pack(side="left")

        self._sync_mode()

    def _sync_mode(self):
        state = "normal" if self.mode_var.get() == "custom" else "disabled"
        self.custom_entry.configure(state=state)

    # -------------------------------------------------- نوار اجرا

    def _build_actionbar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(10, 6))

        self.scan_btn = ttk.Button(bar, text="شروع اسکن", style="Accent.TButton",
                                   command=self.start_scan)
        self.scan_btn.pack(side="right")
        self.stop_btn = ttk.Button(bar, text="توقف", command=self.stop_scan,
                                   state="disabled")
        self.stop_btn.pack(side="right", padx=6)

        self.progress = ttk.Progressbar(bar, mode="determinate", length=340)
        self.progress.pack(side="right", padx=12)

        self.phase_label = ttk.Label(bar, text="آمادهٔ اسکن", foreground="#374151")
        self.phase_label.pack(side="right", padx=6)

    # -------------------------------------------------- جدول نتایج

    def _build_results(self, parent):
        box = ttk.Labelframe(parent, text="  نتیجهٔ اسکن  ", padding=8)
        box.pack(fill="both", expand=True)

        filt = ttk.Frame(box, style="Card.TFrame")
        filt.pack(fill="x", pady=(0, 6))

        ttk.Label(filt, text="جست‌وجو در نتایج:", style="Card.TLabel"
                  ).pack(side="right")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(filt, textvariable=self.search_var, width=32)
        entry.pack(side="right", padx=6)
        entry.bind("<KeyRelease>", lambda e: self._render_tree())

        ttk.Label(filt, text="نمایش:", style="Card.TLabel").pack(side="right", padx=(14, 0))
        self.conf_filter = tk.StringVar(value="همه")
        combo = ttk.Combobox(filt, textvariable=self.conf_filter, width=26,
                             state="readonly", values=[
                                 "همه",
                                 "فقط قطعی (۹۵٪ به بالا)",
                                 "قابل اعتماد (۷۰٪ به بالا)",
                                 "فقط مشکوک (کمتر از ۷۰٪)",
                             ])
        combo.pack(side="right", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda e: self._render_tree())

        self.summary_label = ttk.Label(filt, text="", style="Hint.TLabel")
        self.summary_label.pack(side="left")

        wrap = ttk.Frame(box, style="Card.TFrame")
        wrap.pack(fill="both", expand=True)

        columns = ("sel", "match", "info", "size", "dur", "br", "folder", "date")
        self.tree = ttk.Treeview(wrap, columns=columns, show="tree headings",
                                 selectmode="extended")
        headings = {
            "sel": ("حذف؟", 55, "center"),
            "match": ("روش تطبیق", 165, "e"),
            "info": ("آرتیست — عنوان", 230, "e"),
            "size": ("حجم", 95, "e"),
            "dur": ("مدت", 70, "center"),
            "br": ("کیفیت", 85, "center"),
            "folder": ("پوشه", 330, "w"),
            "date": ("تاریخ", 120, "center"),
        }
        self.tree.heading("#0", text="نام فایل / گروه", anchor="w")
        self.tree.column("#0", width=330, minwidth=200, stretch=True, anchor="w")
        for key, (label, width, anchor) in headings.items():
            self.tree.heading(key, text=label, anchor="center")
            self.tree.column(key, width=width, minwidth=45, anchor=anchor,
                             stretch=False)

        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        self.tree.tag_configure("group", font=UI_FONT_BOLD, background="#eef2ff")
        self.tree.tag_configure("keep", foreground="#065f46")
        self.tree.tag_configure("drop", foreground="#991b1b")
        self.tree.tag_configure("sure", background="#f0fdf4")
        self.tree.tag_configure("maybe", background="#fffbeb")

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

        self.menu = tk.Menu(self, tearoff=0, font=UI_FONT)
        self.menu.add_command(label="پخش فایل", command=self.play_selected)
        self.menu.add_command(label="باز کردن محل فایل",
                              command=self.open_location)
        self.menu.add_command(label="کپی مسیر", command=self.copy_path)
        self.menu.add_separator()
        self.menu.add_command(label="فقط همین را نگه دار (بقیه حذف شوند)",
                              command=self.keep_only_this)
        self.menu.add_command(label="این فایل نگه داشته شود",
                              command=lambda: self._set_selection(False))
        self.menu.add_command(label="این فایل حذف شود",
                              command=lambda: self._set_selection(True))
        self.menu.add_separator()
        self.menu.add_command(label="حذف کل این گروه (هیچ‌کدام نمی‌ماند)",
                              command=self.select_whole_group)

    # -------------------------------------------------- نوار پایین

    def _build_bottombar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(side="bottom", fill="x", pady=(8, 4))

        self.delete_btn = ttk.Button(bar, text="حذف فایل‌های انتخاب‌شده",
                                     style="Danger.TButton",
                                     command=self.delete_selected,
                                     state="disabled")
        self.delete_btn.pack(side="right")

        self.trash_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="انتقال به سطل بازیافت (قابل بازگردانی)",
                        variable=self.trash_var).pack(side="right", padx=10)

        ttk.Button(bar, text="انتخاب خودکار بهترین‌ها",
                   command=self.auto_select).pack(side="left")
        self.auto_level = tk.StringVar(value="گروه‌های قابل اعتماد (۷۰٪ به بالا)")
        ttk.Combobox(bar, textvariable=self.auto_level, width=30,
                     state="readonly", values=[
                         "فقط قطعی‌ها (۹۵٪ به بالا)",
                         "گروه‌های قابل اعتماد (۷۰٪ به بالا)",
                         "شامل مشکوک‌ها هم بشود (همه)",
                     ]).pack(side="left", padx=6)
        ttk.Button(bar, text="پاک کردن انتخاب‌ها",
                   command=self.clear_selection).pack(side="left", padx=6)
        ttk.Button(bar, text="باز کردن همه", command=lambda: self._expand(True)
                   ).pack(side="left")
        ttk.Button(bar, text="بستن همه", command=lambda: self._expand(False)
                   ).pack(side="left", padx=6)
        ttk.Button(bar, text="ذخیرهٔ گزارش", command=self.export_csv
                   ).pack(side="left", padx=6)

    def _build_statusbar(self, parent):
        self.status = ttk.Label(parent, text="پوشه‌ای اضافه کنید و اسکن را شروع کنید.",
                                anchor="e", foreground="#374151")
        self.status.pack(side="bottom", fill="x", pady=(2, 3))

    # ------------------------------------------------------------ پوشه‌ها

    def add_folder(self):
        folder = filedialog.askdirectory(title="پوشه‌ای برای جست‌وجو انتخاب کنید",
                                         parent=self)
        if not folder:
            return
        folder = os.path.normpath(folder)
        current = list(self.folder_list.get(0, "end"))
        if folder in current:
            return
        self.folder_list.insert("end", folder)

    def remove_folder(self):
        for index in reversed(self.folder_list.curselection()):
            self.folder_list.delete(index)

    def clear_folders(self):
        self.folder_list.delete(0, "end")

    # ------------------------------------------------------------ اسکن

    def _read_options(self) -> ScanOptions | None:
        folders = list(self.folder_list.get(0, "end"))
        if not folders:
            messagebox.showinfo("پوشه‌ای انتخاب نشده",
                                "برای شروع، دست‌کم یک پوشه اضافه کنید.",
                                parent=self)
            return None
        try:
            min_kb = max(0, int(float(self.min_size_var.get())))
        except ValueError:
            min_kb = 64
            self.min_size_var.set("64")
        try:
            tol = max(0.0, float(self.tol_var.get()))
        except ValueError:
            tol = 2.0
            self.tol_var.set("2")

        exts = []
        for token in self.custom_ext.get().replace(",", " ").split():
            token = token.strip().lower()
            if token:
                exts.append(token if token.startswith(".") else "." + token)

        opts = ScanOptions(
            folders=folders,
            recursive=self.recursive_var.get(),
            mode=self.mode_var.get(),
            custom_exts=exts,
            min_size=min_kb * 1024,
            size_tolerance=tol,
            skip_hidden=self.hidden_var.get(),
            use_exact=self.m_exact.get(),
            use_audio_hash=self.m_audio.get(),
            use_tags=self.m_tags.get(),
            use_name=self.m_name.get(),
            use_duration=self.m_duration.get(),
            use_size=self.m_size.get(),
        )
        if not any([opts.use_exact, opts.use_audio_hash, opts.use_tags,
                    opts.use_name, opts.use_duration, opts.use_size]):
            messagebox.showinfo("روشی انتخاب نشده",
                                "دست‌کم یک روش تشخیص را فعال کنید.", parent=self)
            return None
        if opts.mode == "custom" and not exts:
            messagebox.showinfo("پسوندی وارد نشده",
                                "برای حالت «پسوند دلخواه» باید پسوندها را "
                                "بنویسید؛ مثلاً: .mp3 .flac", parent=self)
            return None
        return opts

    def start_scan(self):
        if self.scanning:
            return
        opts = self._read_options()
        if opts is None:
            return

        self.groups = []
        self.selected.clear()
        self.tree.delete(*self.tree.get_children())
        self.cancel_event = threading.Event()
        self.scanning = True
        self.scan_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.delete_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self._set_status("اسکن آغاز شد…")

        def progress(phase, done, total):
            self.msg_queue.put(("progress", phase, done, total))

        def work():
            engine = Engine(opts, progress=progress, cancel=self.cancel_event)
            try:
                groups = engine.run()
                self.msg_queue.put(("done", groups, engine.stats))
            except Cancelled:
                self.msg_queue.put(("cancelled", None, None))
            except Exception as exc:  # خطای پیش‌بینی‌نشده
                import traceback
                self.msg_queue.put(("error", f"{exc}", traceback.format_exc()))

        self.scan_thread = threading.Thread(target=work, daemon=True)
        self.scan_thread.start()

    def stop_scan(self):
        if self.scanning:
            self.cancel_event.set()
            self._set_status("در حال توقف…")

    def _pump(self):
        """پیام‌های رشتهٔ اسکن را می‌گیرد و رابط را به‌روز می‌کند."""
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, phase, done, total = msg
                    self.phase_label.configure(text=phase)
                    if total:
                        self.progress.configure(mode="determinate",
                                                maximum=total, value=done)
                    else:
                        self.progress.configure(mode="determinate",
                                                maximum=100, value=0)
                        self.phase_label.configure(
                            text=f"{phase} — {fa_digits(done)} فایل")
                elif kind == "done":
                    self._finish_scan(msg[1], msg[2])
                elif kind == "cancelled":
                    self._reset_scan_ui()
                    self._set_status("اسکن متوقف شد.")
                elif kind == "error":
                    self._reset_scan_ui()
                    messagebox.showerror("خطا در اسکن", msg[1], parent=self)
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _reset_scan_ui(self):
        self.scanning = False
        self.scan_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.phase_label.configure(text="آمادهٔ اسکن")
        self.progress.configure(value=0)

    def _finish_scan(self, groups: list[Group], stats: dict):
        self._reset_scan_ui()
        self.groups = groups
        self.file_of_path = {
            rec.path: rec for g in groups for rec in g.files}
        self.selected = {rec.path: False for g in groups for rec in g.files}

        if not groups:
            self._render_tree()
            self._set_status(
                f"هیچ فایل تکراری پیدا نشد. "
                f"{fa_digits(stats.get('scanned', 0))} فایل بررسی شد.")
            messagebox.showinfo("نتیجهٔ اسکن",
                                "هیچ فایل تکراری‌ای پیدا نشد.", parent=self)
            return

        self.auto_select()
        self.delete_btn.configure(state="normal")
        total_files = sum(len(g.files) for g in groups)
        reclaim = sum(g.reclaimable for g in groups)
        self._set_status(
            f"{fa_digits(stats.get('scanned', 0))} فایل بررسی شد — "
            f"{fa_digits(len(groups))} گروه تکراری با {fa_digits(total_files)} "
            f"فایل پیدا شد — تا {human_size(reclaim)} قابل آزادسازی.")

    # ------------------------------------------------------------ جدول

    def _passes_filter(self, group: Group) -> bool:
        mode = self.conf_filter.get()
        conf = group.confidence
        if mode.startswith("فقط قطعی") and conf < 95:
            return False
        if mode.startswith("قابل اعتماد") and conf < 70:
            return False
        if mode.startswith("فقط مشکوک") and conf >= 70:
            return False

        needle = self.search_var.get().strip().casefold()
        if not needle:
            return True
        for rec in group.files:
            haystack = rec.path.casefold()
            if rec.meta:
                haystack += " " + rec.meta.display.casefold()
                haystack += " " + (rec.meta.album or "").casefold()
            if needle in haystack:
                return True
        return False

    def _render_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.item_of_path.clear()
        self.path_of_item.clear()
        self.group_of_item.clear()
        self.item_of_group.clear()

        shown = 0
        shown_files = 0
        for group in self.groups:
            if not self._passes_filter(group):
                continue
            shown += 1
            shown_files += len(group.files)
            gid = self.tree.insert(
                "", "end",
                text=f"گروه {fa_digits(group.gid)} — {fa_digits(len(group.files))} فایل",
                values=("", group.method_label,
                        f"اطمینان {fa_digits(group.confidence)}٪",
                        human_size(group.reclaimable), "", "",
                        f"قابل آزادسازی: {human_size(group.reclaimable)}", ""),
                open=(group.confidence < 95 or len(self.groups) < 40),
                tags=("group",))
            self.group_of_item[gid] = group
            self.item_of_group[group.gid] = gid

            best = keeper.best_index(group)
            for i, rec in enumerate(group.files):
                item = self.tree.insert(gid, "end", text="  " + rec.name,
                                        values=self._row_values(rec),
                                        tags=self._row_tags(rec, i == best))
                self.item_of_path[rec.path] = item
                self.path_of_item[item] = rec.path
            self._refresh_group_row(group)

        self.summary_label.configure(
            text=f"نمایش {fa_digits(shown)} گروه / {fa_digits(shown_files)} فایل")

    def _row_values(self, rec):
        meta = rec.meta
        mark = CHECKED if self.selected.get(rec.path) else UNCHECKED
        method = METHOD_INFO.get(rec.match_method, ("—", 0))[0]
        return (
            mark,
            method,
            meta.display if meta else "",
            human_size(rec.size),
            human_duration(meta.duration if meta else 0),
            f"{fa_digits(meta.bitrate // 1000)} kbps" if (meta and meta.bitrate) else "",
            rec.folder,
            fa_digits(_dt.datetime.fromtimestamp(rec.mtime).strftime("%Y/%m/%d")),
        )

    def _row_tags(self, rec, is_best: bool):
        tags = ["drop" if self.selected.get(rec.path) else "keep"]
        tags.append("sure" if is_best else "maybe")
        return tuple(tags)

    def _refresh_row(self, path: str):
        item = self.item_of_path.get(path)
        if not item:
            return
        rec = self.file_of_path.get(path)
        if rec is None:
            return
        parent = self.tree.parent(item)
        group = self.group_of_item.get(parent)
        best = keeper.best_index(group) if group else -1
        is_best = bool(group) and group.files.index(rec) == best
        self.tree.item(item, values=self._row_values(rec),
                       tags=self._row_tags(rec, is_best))
        if group:
            self._refresh_group_row(group)

    def _refresh_group_row(self, group: Group):
        item = self.item_of_group.get(group.gid)
        if not item:
            return
        marked = sum(1 for rec in group.files if self.selected.get(rec.path))
        if marked == 0:
            glyph = UNCHECKED
        elif marked == len(group.files):
            glyph = CHECKED
        else:
            glyph = PARTIAL
        freed = sum(rec.size for rec in group.files if self.selected.get(rec.path))
        values = list(self.tree.item(item, "values"))
        values[0] = glyph
        values[6] = (f"{fa_digits(marked)} فایل برای حذف — {human_size(freed)}"
                     if marked else
                     f"قابل آزادسازی: {human_size(group.reclaimable)}")
        self.tree.item(item, values=values)

    # ------------------------------------------------------------ تعامل

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        item = self.tree.identify_row(event.y)
        if not item:
            return
        if item in self.group_of_item:
            self._toggle_group(self.group_of_item[item])
        else:
            self._toggle_path(self.path_of_item.get(item))
        return "break"

    def _on_space(self, event):
        for item in self.tree.selection():
            if item in self.group_of_item:
                self._toggle_group(self.group_of_item[item])
            else:
                self._toggle_path(self.path_of_item.get(item))
        return "break"

    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        if item and item in self.path_of_item:
            self.play_selected()
            return "break"

    def _on_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            self.tree.focus(item)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _toggle_path(self, path: str | None, force: bool | None = None):
        if not path:
            return
        item = self.item_of_path.get(path)
        group = self.group_of_item.get(self.tree.parent(item)) if item else None
        new_value = (not self.selected.get(path)) if force is None else force

        if new_value and group:
            others_kept = [
                rec for rec in group.files
                if rec.path != path and not self.selected.get(rec.path)]
            if not others_kept:
                self._set_status(
                    "از هر گروه دست‌کم یک فایل باید بماند. برای حذف کل گروه از "
                    "منوی راست‌کلیک استفاده کنید.")
                return
        self.selected[path] = new_value
        self._refresh_row(path)
        self._update_totals()

    def _toggle_group(self, group: Group):
        marked = sum(1 for rec in group.files if self.selected.get(rec.path))
        if marked:
            for rec in group.files:
                self.selected[rec.path] = False
        else:
            best = keeper.best_index(group)
            for i, rec in enumerate(group.files):
                self.selected[rec.path] = (i != best)
        for rec in group.files:
            self._refresh_row_quiet(rec)
        self._refresh_group_row(group)
        self._update_totals()

    def _refresh_row_quiet(self, rec):
        item = self.item_of_path.get(rec.path)
        if not item:
            return
        parent = self.tree.parent(item)
        group = self.group_of_item.get(parent)
        best = keeper.best_index(group) if group else -1
        is_best = bool(group) and group.files.index(rec) == best
        self.tree.item(item, values=self._row_values(rec),
                       tags=self._row_tags(rec, is_best))

    def _set_selection(self, value: bool):
        for item in self.tree.selection():
            path = self.path_of_item.get(item)
            if path:
                self._toggle_path(path, force=value)

    def keep_only_this(self):
        items = [i for i in self.tree.selection() if i in self.path_of_item]
        if not items:
            return
        item = items[0]
        group = self.group_of_item.get(self.tree.parent(item))
        keep_path = self.path_of_item[item]
        if not group:
            return
        for rec in group.files:
            self.selected[rec.path] = (rec.path != keep_path)
            self._refresh_row_quiet(rec)
        self._refresh_group_row(group)
        self._update_totals()

    def select_whole_group(self):
        items = self.tree.selection()
        if not items:
            return
        item = items[0]
        group = (self.group_of_item.get(item)
                 or self.group_of_item.get(self.tree.parent(item)))
        if not group:
            return
        if not messagebox.askyesno(
                "حذف کل گروه",
                f"همهٔ {fa_digits(len(group.files))} فایل این گروه برای حذف "
                "علامت بخورند؟ هیچ نسخه‌ای باقی نمی‌ماند.",
                icon="warning", parent=self):
            return
        for rec in group.files:
            self.selected[rec.path] = True
            self._refresh_row_quiet(rec)
        self._refresh_group_row(group)
        self._update_totals()

    def auto_select(self):
        """بهترین نسخه را نگه می‌دارد و بقیه را علامت می‌زند.

        گروه‌های کم‌اطمینان (مدت/حجم مشابه) عمداً دست‌نخورده می‌مانند؛ آن‌ها
        حدس‌اند نه قطعیت، و باید با چشم کاربر بررسی شوند.
        """
        threshold = self._auto_threshold()
        auto, skipped = 0, 0
        for group in self.groups:
            if group.confidence < threshold:
                skipped += 1
                for rec in group.files:
                    self.selected[rec.path] = False
                continue
            auto += 1
            for index in keeper.suggest_deletions(group):
                self.selected[group.files[index].path] = True
            self.selected[group.files[keeper.best_index(group)].path] = False
        self._render_tree()
        self._update_totals()
        message = (f"در {fa_digits(auto)} گروه، بهترین نسخه نگه داشته شد و "
                   f"بقیه علامت خوردند.")
        if skipped:
            message += (f" {fa_digits(skipped)} گروه کم‌اطمینان دست‌نخورده ماند "
                        f"— آن‌ها را خودتان بررسی کنید.")
        self._set_status(message + " قبل از حذف حتماً مرور کنید.")

    def _auto_threshold(self) -> int:
        text = self.auto_level.get()
        if text.startswith("فقط قطعی"):
            return 95
        if text.startswith("شامل مشکوک"):
            return 0
        return 70

    def clear_selection(self):
        for path in self.selected:
            self.selected[path] = False
        self._render_tree()
        self._update_totals()

    def _expand(self, open_: bool):
        for item in self.tree.get_children():
            self.tree.item(item, open=open_)

    def _update_totals(self):
        marked = [p for p, v in self.selected.items() if v]
        freed = sum(self.file_of_path[p].size for p in marked
                    if p in self.file_of_path)
        self.delete_btn.configure(
            state="normal" if marked else "disabled",
            text=(f"حذف {fa_digits(len(marked))} فایل انتخاب‌شده"
                  if marked else "حذف فایل‌های انتخاب‌شده"))
        if marked:
            self._set_status(
                f"{fa_digits(len(marked))} فایل برای حذف علامت خورده — "
                f"{human_size(freed)} آزاد می‌شود.")

    def _set_status(self, text: str):
        self.status.configure(text=text)

    # ------------------------------------------------------------ عملیات

    def _first_selected_path(self) -> str | None:
        for item in self.tree.selection():
            if item in self.path_of_item:
                return self.path_of_item[item]
        return None

    def play_selected(self):
        path = self._first_selected_path()
        if not path:
            return
        try:
            os.startfile(path)
        except Exception as exc:
            messagebox.showerror("خطا در پخش", str(exc), parent=self)

    def open_location(self):
        path = self._first_selected_path()
        if not path:
            return
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except Exception as exc:
            messagebox.showerror("خطا", str(exc), parent=self)

    def copy_path(self):
        path = self._first_selected_path()
        if not path:
            return
        self.clipboard_clear()
        self.clipboard_append(path)
        self._set_status("مسیر فایل کپی شد.")

    def delete_selected(self):
        marked = [p for p, v in self.selected.items() if v]
        if not marked:
            return
        freed = sum(self.file_of_path[p].size for p in marked
                    if p in self.file_of_path)
        to_trash = self.trash_var.get()

        if to_trash and not keeper.HAVE_TRASH:
            if not messagebox.askyesno(
                    "سطل بازیافت در دسترس نیست",
                    "کتابخانهٔ send2trash نصب نیست، پس انتقال به سطل بازیافت "
                    "ممکن نیست.\n\nآیا فایل‌ها به‌صورت دائمی حذف شوند؟",
                    icon="warning", parent=self):
                return
            to_trash = False

        where = "سطل بازیافت" if to_trash else "حذف دائمی (غیرقابل بازگشت)"
        if not messagebox.askyesno(
                "تأیید حذف",
                f"{fa_digits(len(marked))} فایل حذف شود؟\n"
                f"فضای آزادشده: {human_size(freed)}\n"
                f"روش: {where}",
                icon="warning", parent=self):
            return
        if not to_trash and not messagebox.askyesno(
                "تأیید نهایی حذف دائمی",
                "این فایل‌ها به سطل بازیافت نمی‌روند و بازگردانی نمی‌شوند.\n"
                "مطمئن هستید؟", icon="warning", parent=self):
            return

        result = keeper.delete_files(marked, to_recycle_bin=to_trash,
                                     log_dir=os.path.join(_app_dir(), "logs"))
        self._apply_deletions(result.deleted)

        message = (f"{fa_digits(len(result.deleted))} فایل حذف شد.\n"
                   f"{human_size(result.freed)} آزاد شد.")
        if result.failed:
            preview = "\n".join(f"• {os.path.basename(p)} — {e}"
                                for p, e in result.failed[:8])
            message += (f"\n\n{fa_digits(len(result.failed))} فایل حذف نشد:\n"
                        f"{preview}")
            messagebox.showwarning("حذف با خطا", message, parent=self)
        else:
            messagebox.showinfo("حذف انجام شد", message, parent=self)
        self._set_status(f"{fa_digits(len(result.deleted))} فایل حذف شد — "
                         f"{human_size(result.freed)} آزاد شد.")

    def _apply_deletions(self, deleted: list[str]):
        gone = set(deleted)
        for group in self.groups:
            group.files = [rec for rec in group.files if rec.path not in gone]
        self.groups = [g for g in self.groups if len(g.files) > 1]
        for index, group in enumerate(self.groups, start=1):
            group.gid = index
        for path in gone:
            self.selected.pop(path, None)
            self.file_of_path.pop(path, None)
        self._render_tree()
        self._update_totals()
        if not self.groups:
            self.delete_btn.configure(state="disabled")

    def export_csv(self):
        if not self.groups:
            messagebox.showinfo("نتیجه‌ای نیست",
                                "ابتدا یک اسکن انجام دهید.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="ذخیرهٔ گزارش", defaultextension=".csv",
            initialfile=f"duplicates-{_dt.datetime.now():%Y%m%d-%H%M}.csv",
            filetypes=[("فایل CSV", "*.csv")], parent=self)
        if not path:
            return
        try:
            keeper.export_report(self.groups, path, self.selected)
            self._set_status(f"گزارش ذخیره شد: {path}")
            messagebox.showinfo("ذخیره شد",
                                f"گزارش در این مسیر ذخیره شد:\n{path}",
                                parent=self)
        except Exception as exc:
            messagebox.showerror("خطا در ذخیره", str(exc), parent=self)

    # ------------------------------------------------------------ تنظیمات

    def _settings_path(self) -> str:
        return os.path.join(_app_dir(), "settings.json")

    def _load_settings(self):
        try:
            with open(self._settings_path(), encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return
        for folder in data.get("folders", []):
            if os.path.isdir(folder):
                self.folder_list.insert("end", folder)
        self.mode_var.set(data.get("mode", "audio"))
        self.custom_ext.set(data.get("custom_exts", ".mp3 .flac .m4a"))
        self.min_size_var.set(str(data.get("min_kb", 64)))
        self.tol_var.set(str(data.get("tolerance", 2)))
        self.recursive_var.set(data.get("recursive", True))
        self.hidden_var.set(data.get("skip_hidden", True))
        self.trash_var.set(data.get("recycle_bin", True))
        methods = data.get("methods", {})
        for key, var in (("exact", self.m_exact), ("audio", self.m_audio),
                         ("tags", self.m_tags), ("name", self.m_name),
                         ("duration", self.m_duration), ("size", self.m_size)):
            var.set(methods.get(key, True))
        self._sync_mode()

    def _save_settings(self):
        data = {
            "folders": list(self.folder_list.get(0, "end")),
            "mode": self.mode_var.get(),
            "custom_exts": self.custom_ext.get(),
            "min_kb": self.min_size_var.get(),
            "tolerance": self.tol_var.get(),
            "recursive": self.recursive_var.get(),
            "skip_hidden": self.hidden_var.get(),
            "recycle_bin": self.trash_var.get(),
            "methods": {
                "exact": self.m_exact.get(), "audio": self.m_audio.get(),
                "tags": self.m_tags.get(), "name": self.m_name.get(),
                "duration": self.m_duration.get(), "size": self.m_size.get(),
            },
        }
        try:
            with open(self._settings_path(), "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        if self.scanning:
            if not messagebox.askyesno("خروج",
                                       "اسکن در حال اجراست. بسته شود؟",
                                       parent=self):
                return
            self.cancel_event.set()
        self._save_settings()
        self.destroy()


def main():
    app = DuplicateCleanerApp()
    app.mainloop()
