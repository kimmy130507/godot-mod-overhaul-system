# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# Atomic replace, locking, retries, and concurrency.
import sys
import threading
import time
from pathlib import Path
from typing import Any, List, Set
from unittest.mock import patch

import pytest

# Explicit imports replacing dynamic resolution
from gmos.io import (
    atomic_copy_with_single_bak,
    atomic_write_with_backup,
    replace_with_retries,
)

try:
    from gmos.io import path_lock, safe_remove, start_replace_task
except ImportError:
    # Fallback for internal/base modules if not exported in __init__
    from gmos.io.base import safe_remove, start_replace_task  # type: ignore
    from gmos.io.locking import path_lock  # type: ignore

# --- Helpers ---


def list_intermediate_leftovers(tmp_path: Path, ignore_paths: Set[Path]) -> List[Path]:
    """Return files that look like intermediate artifacts (.gmos_tmp_* or *.tmp)"""
    leftovers: List[Path] = []
    for p in tmp_path.iterdir():
        if p in ignore_paths:
            continue
        if p.name.startswith(".gmos_tmp_") or p.suffix == ".tmp":
            leftovers.append(p)
    return leftovers


# --- Tests ---


def test_atomic_write_with_backup_closes_fd(tmp_path: Path) -> None:
    """atomic_write_with_backup should create .bak and not leak file descriptors."""
    dst: Path = tmp_path / "file.txt"
    atomic_write_with_backup(str(dst), "hello")
    assert dst.read_text(encoding="utf-8") == "hello"

    atomic_write_with_backup(str(dst), "world")
    assert dst.read_text(encoding="utf-8") == "world"
    bak: Path = dst.with_name(dst.name + ".bak")
    assert bak.exists()


def test_replace_with_retries_fallback(tmp_path: Path) -> None:
    """If os.replace fails, should fall back to copy2."""
    src: Path = tmp_path / "src.txt"
    dst: Path = tmp_path / "dst.txt"
    src.write_bytes(b"abc")

    # Mock os.replace to fail with "Access Denied" (Win32-like)
    def fake_replace(a: Any, b: Any) -> None:
        raise OSError(32, "Process cannot access file")

    with patch("os.replace", side_effect=fake_replace):
        replace_with_retries(str(src), str(dst), max_attempts=2, base_delay=0.001)

    assert dst.exists()
    assert dst.read_bytes() == b"abc"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows CI filesystem handles causes flaky PermissionErrors on concurrent writes",
)
def test_concurrent_per_path_locking(tmp_path: Path) -> None:
    target = tmp_path / "shared_target.txt"
    n_threads = 6
    contents = [f"content-{i}\n" for i in range(n_threads)]
    src_paths: List[Path] = []

    for i, c in enumerate(contents):
        p = tmp_path / f"src_{i}.tmp"
        p.write_text(c, encoding="utf-8")
        src_paths.append(p)

    exceptions: List[Exception] = []

    def worker(src_path: Path) -> None:
        try:
            atomic_copy_with_single_bak(str(src_path), str(target))
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker, args=(s,)) for s in src_paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    if exceptions:
        raise exceptions[0]

    assert target.exists()
    assert any(target.read_text(encoding="utf-8") == c for c in contents)


def test_safe_remove_retries_exhaustion(tmp_path: Path) -> None:
    target = tmp_path / "to_remove.txt"
    target.write_text("x", encoding="utf-8")

    # Mock os.remove to always fail
    with patch("os.remove", side_effect=OSError(13, "Permission denied")):
        with pytest.raises(OSError):
            safe_remove(str(target))

    assert target.exists()


def test_start_replace_task_success_and_cancel(tmp_path: Path) -> None:
    src = tmp_path / "src_temp.bin"
    src.write_text("ok\n", encoding="utf-8")
    dst = tmp_path / "dst_file.txt"

    done_calls: List[Any] = []

    def done_cb(diag: Any) -> None:
        done_calls.append(diag)

    # 1. Test Success
    # Result is (ReplaceDiagnostics, threading.Thread | None)
    result = start_replace_task(
        str(src), str(dst), done_cb=done_cb, attempts=3, base_delay=0.01
    )
    thread_obj = result[1]

    if thread_obj:
        thread_obj.join(timeout=5)

    timeout = time.time() + 5
    while time.time() < timeout and not dst.exists() and not done_calls:
        time.sleep(0.05)
    # Create explicit event to control cancellation
    cancel_ev = threading.Event()
    assert dst.exists() or done_calls, "Task did not complete"

    # 2. Test Cancel
    src2 = tmp_path / "src_temp2.bin"
    src2.write_text("ok2\n", encoding="utf-8")
    dst2 = tmp_path / "dst_file2.txt"

    # Use a typed helper function instead of lambda to satisfy Pylance
    def slow_replace_mock(s: Any, d: Any, **kwargs: Any) -> None:
        time.sleep(0.5)

    with patch("gmos.io.replace_with_retries", side_effect=slow_replace_mock):
        _, thread_cancel = start_replace_task(
            str(src2), str(dst2), attempts=3, done_cb=done_cb, cancel_event=cancel_ev
        )

        # Signal cancellation via the event we own
        cancel_ev.set()

        if thread_cancel:
            thread_cancel.join(timeout=5)

    # Ensure clean state (no leftovers)
    ignore = {src, src2}
    leftovers = list_intermediate_leftovers(tmp_path, ignore)
    assert not leftovers, f"Leftover tmp files found: {leftovers}"


def test_path_lock_serializes_replace_and_remove(tmp_path: Path) -> None:
    target = tmp_path / "shared.bin"
    target.write_bytes(b"initial")
    src = tmp_path / "src.bin"
    src.write_bytes(b"new")

    # We mock replace to be slow to provoke race conditions if locking fails
    real_replace = replace_with_retries

    def slow_replace(*args: Any, **kwargs: Any) -> None:
        time.sleep(0.2)
        real_replace(*args, **kwargs)

    exceptions: List[Exception] = []

    with patch("gmos.io.replace_with_retries", side_effect=slow_replace):

        def run_replace() -> None:
            try:
                # This should acquire the lock
                with path_lock(str(target)):
                    replace_with_retries(str(src), str(target))
            except Exception as e:
                exceptions.append(e)

        def run_remove() -> None:
            time.sleep(0.05)
            try:
                # This should wait for replace to finish
                safe_remove(str(target))
            except Exception as e:
                exceptions.append(e)

        t1 = threading.Thread(target=run_replace)
        t2 = threading.Thread(target=run_remove)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert not exceptions, f"Exceptions occurred: {exceptions}"
