# -*- coding: utf-8 -*-
"""Entry point for Duplicate Cleaner."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dupcleaner.webapp import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run())
