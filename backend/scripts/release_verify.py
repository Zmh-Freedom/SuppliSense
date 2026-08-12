#!/usr/bin/env python3
"""CLI wrapper for the Task 15 release checklist."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.release_check import main


if __name__ == "__main__":
    raise SystemExit(main())
