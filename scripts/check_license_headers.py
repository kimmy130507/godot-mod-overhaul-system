#!/usr/bin/env python3
"""
Simple license header checker.

Rules:
- A file is considered compliant if one of these appears in the first 20 lines:
  - "SPDX-License-Identifier"
  - "SPDX-FileCopyrightText"
  - "This file is part of GMOS"
- Skips non-.py files and the virtual environment / common auto-generated dirs.
Exit code 0 on success, 1 on failure (and prints missing files).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
SEARCH_DIRS = ["gmos", "tests"]  # <--- The "Whitelist"
MAX_HEADER_LINES = 20

PATTERN = re.compile(
    r"(SPDX-License-Identifier|SPDX-FileCopyrightText|This file is part of GMOS)",
    re.IGNORECASE,
)

# We only need to exclude things that might appear INSIDE the source folders.
# .git and .venv are at the root, so we don't need to list them here.
EXCLUDE_DIRS = {"__pycache__"}


def iter_py_files() -> Iterator[Path]:
    """
    Yields all .py files found inside SEARCH_DIRS, skipping internal
    cache directories.
    """
    for base in SEARCH_DIRS:
        basep = ROOT / base
        if not basep.exists():
            continue

        # Walk allows us to prune sub-directories efficiently
        for dirpath, dirnames, filenames in os.walk(basep):
            # Modify dirnames in-place to prevent os.walk from entering cache dirs
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

            for fn in filenames:
                if fn.endswith(".py"):
                    yield Path(dirpath) / fn


def check_file(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as fh:
            # Read only what we need
            lines = [next(fh) for _ in range(MAX_HEADER_LINES)]
    except StopIteration:
        # Handle files shorter than MAX_HEADER_LINES
        try:
            with path.open("r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except Exception:
            return False
    except Exception:
        print(f"ERROR: could not read {path}", file=sys.stderr)
        return False

    head = "\n".join(lines)
    return bool(PATTERN.search(head))


def main() -> int:
    missing: list[str] = []

    for p in iter_py_files():
        if not check_file(p):
            missing.append(str(p.relative_to(ROOT)))

    if missing:
        print("License header check FAILED. The following files are missing headers:")
        for m in missing:
            print(f" - {m}")
        print(
            f"\nRequired in first {MAX_HEADER_LINES} lines: "
            "'SPDX-License-Identifier', 'SPDX-FileCopyrightText', or 'This file is part of GMOS'"
        )
        return 1

    print("License header check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
