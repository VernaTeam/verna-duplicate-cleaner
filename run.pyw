# -*- coding: utf-8 -*-
"""نقطهٔ شروع برنامهٔ حذف فایل‌های تکراری."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dupcleaner.gui import main  # noqa: E402

if __name__ == "__main__":
    main()
