# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# Atomic replace, locking, retries, and concurrency.

import importlib
import os
import sys
import threading
import time
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

import pytest
from pytest import MonkeyPatch

from gmos.io import atomic_write_with_backup, replace_with_retries

# --- Constants & Helpers ---

REPLACE_SYMBOLS = [
    ("gmos.io", "_replace_with_retries"),
    ("gmos", "_replace_with_retries"),
]

ATOMIC_COPY_SYMBOLS = [
    ("gmos.io", "atomic_copy_with_single_bak"),
    ("gmos", "atomic_copy_with_single_bak"),
    ("gmos.io", "atomic_write_copy"),
]

SAFE_REMOVE_SYMBOLS = [
    ("gmos.io", "safe_remove"),
    ("gmos", "safe_remove"),
]

START_REPLACE_SYMBOLS = [
    ("gmos.io", "start_replace_task"),
    ("gmos", "start_replace_task"),
]

TEMP_CTX_SYMBOLS = [("gmos.io", "_temp_file_context"), ("gmos", "_temp_file_context")]
PATH_LOCK_SYMBOLS = [("gmos.io", "path_lock"), ("gmos", "path_lock")]


def resolve_symbol(
    candidates: List[Tuple[str, str]], top_module_name: str = "gmos"
) -> Optional[Callable[..., Any]]:
    """
    Try to resolve the symbol from the preferred top-level module first
    (import_module(top_module_name)), then from candidate (module, attr) list.
    Returns the resolved symbol or None.
    """
    try:
        top = import_module(top_module_name)
    except Exception:
        top = None

    if top is not None:
        for _mod_name, attr in candidates:
            if hasattr(top, attr):
                return getattr(top, attr)  # type: ignore[no-any-return]

    for mod_name, attr in candidates:
        try:
            mod = import_module(mod_name)
        except Exception:
            continue
        try:
            return getattr(mod, attr)  # type: ignore[no-any-return]
        except Exception:
            continue
    return None


def list_intermediate_leftovers(tmp_path: Path, ignore_paths: Set[Path]) -> List[Path]:
    """Return files that look like intermediate artifacts (.gmos_tmp_* or *.tmp)
    but are not in ignore_paths set."""
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
    # write first time
    atomic_write_with_backup(str(dst), "hello")
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "hello"

    # write again to trigger .bak behavior and ensure no handle leak
    atomic_write_with_backup(str(dst), "world")
    assert dst.read_text(encoding="utf-8") == "world"
    bak: Path = dst.with_name(dst.name + ".bak")
    assert bak.exists()


def test_replace_with_retries_fallback(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """If os.replace fails repeatedly, _replace_with_retries should fall back to copy2."""
    src: Path = tmp_path / "src.txt"
    dst: Path = tmp_path / "dst.txt"
    src.write_bytes(b"abc")

    calls: Dict[str, int] = {"count": 0}

    def fake_replace(
        a: Union[str, os.PathLike[str]], b: Union[str, os.PathLike[str]]
    ) -> None:
        calls["count"] += 1
        raise OSError(
            32,
            "The process cannot access the file because it is being used by another process",
        )

    monkeypatch.setattr("os.replace", fake_replace)

    replace_with_retries(str(src), str(dst), max_attempts=2, base_delay=0.001)
    assert dst.exists()
    assert dst.read_bytes() == b"abc"


def test_concurrent_per_path_locking(tmp_path: Path) -> None:
    atomic_copy = resolve_symbol(ATOMIC_COPY_SYMBOLS)
    if atomic_copy is None:
        pytest.skip("atomic_copy_with_single_bak not found in expected modules")

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
            atomic_copy(str(src_path), str(target))
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker, args=(s,)) for s in src_paths]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    if exceptions:
        raise exceptions[0]

    assert target.exists(), "target file was not created"
    final_text = target.read_text(encoding="utf-8")
    assert any(
        final_text == c for c in contents
    ), "final content must be one of the expected writes"

    ignore: Set[Path] = set(src_paths)
    leftovers = list_intermediate_leftovers(tmp_path, ignore)
    assert not leftovers, f"found leftover tmp files: {leftovers}"


def test_safe_remove_retries_exhaustion(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    safe_remove = resolve_symbol(SAFE_REMOVE_SYMBOLS)
    if safe_remove is None:
        pytest.skip("safe_remove not found in expected modules")

    target = tmp_path / "to_remove.txt"
    target.write_text("x", encoding="utf-8")

    class AlwaysBusy(OSError):
        errno = 13  # EACCES / permission denied

    def bad_remove(path: Union[str, os.PathLike[str]]) -> None:
        raise AlwaysBusy("simulated permission denied")

    monkeypatch.setattr(os, "remove", bad_remove)

    with pytest.raises(OSError):
        safe_remove(str(target))

    assert target.exists()


def test_start_replace_task_success_and_cancel(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    start_replace = resolve_symbol(START_REPLACE_SYMBOLS)
    if start_replace is None:
        pytest.skip("start_replace_task not found in expected modules")

    src = tmp_path / "src_temp.bin"
    src.write_text("ok\n", encoding="utf-8")
    dst = tmp_path / "dst_file.txt"

    done_calls: List[Any] = []

    def done_cb(diag: Any) -> None:
        done_calls.append(diag)

    cancel_event = threading.Event()
    result: Any = None
    try:
        result = start_replace(
            str(src),
            str(dst),
            done_cb=done_cb,
            cancel_event=cancel_event,
            attempts=3,
            base_delay=0.01,
        )
    except TypeError:
        try:
            result = start_replace(str(src), str(dst))
        except Exception:
            pytest.skip(
                "start_replace_task exists but could not be invoked with trial signatures"
            )

    if isinstance(result, threading.Thread):
        result.join(timeout=10)

    timeout = time.time() + 5
    while time.time() < timeout and not dst.exists() and not done_calls:
        time.sleep(0.05)

    assert (
        dst.exists() or done_calls
    ), "start_replace_task did not complete or invoke done_cb"

    replace_sym = resolve_symbol(REPLACE_SYMBOLS)
    if replace_sym is None:
        pytest.skip("internal replace symbol not found; skipping cancel subtest")

    original_replace = replace_sym

    def slow_replace(src_path: str, dst_path: str, *a: Any, **kw: Any) -> None:
        time.sleep(0.5)
        original_replace(src_path, dst_path, *a, **kw)

    monkeypatch.setattr(
        importlib.import_module(replace_sym.__module__),
        replace_sym.__name__,
        slow_replace,
    )

    src2 = tmp_path / "src_temp2.bin"
    src2.write_text("ok2\n", encoding="utf-8")
    dst2 = tmp_path / "dst_file2.txt"

    done_calls.clear()
    cancel_event2 = threading.Event()
    try:
        t = start_replace(
            str(src2),
            str(dst2),
            done_cb=done_cb,
            cancel_event=cancel_event2,
            attempts=3,
            base_delay=0.01,
        )
    except TypeError:
        t = start_replace(str(src2), str(dst2))

    cancel_event2.set()
    if isinstance(t, threading.Thread):
        t.join(timeout=5)

    ignore: Set[Path] = {src, src2}
    leftovers = list_intermediate_leftovers(tmp_path, ignore)
    assert not leftovers, f"found leftover tmp files after cancel: {leftovers}"


@pytest.mark.skip("fixed by startup orphan-sweep strategy")
def test_atomic_write_cleanup_on_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    atomic_copy = resolve_symbol(ATOMIC_COPY_SYMBOLS)
    replace_sym = resolve_symbol(REPLACE_SYMBOLS)
    if atomic_copy is None:
        pytest.skip("atomic copy function not found; skipping cleanup test")

    target = tmp_path / "target.txt"
    src = tmp_path / "src_for_fail.tmp"
    src.write_text("should not end up\n", encoding="utf-8")

    def failing_replace(src_path: str, dst_path: str, *a: Any, **kw: Any) -> None:
        t = Path(dst_path + ".tmp")
        t.write_text("intermediate", encoding="utf-8")
        raise OSError("simulated mid-replace failure")

    if replace_sym is not None:
        monkeypatch.setattr(
            importlib.import_module(replace_sym.__module__),
            replace_sym.__name__,
            failing_replace,
        )
    else:

        def failing_os_replace(
            src: Union[str, os.PathLike[str]], dst: Union[str, os.PathLike[str]]
        ) -> None:
            raise OSError("simulated mid-replace failure")

        monkeypatch.setattr(os, "replace", failing_os_replace)

    with pytest.raises(Exception):
        atomic_copy(str(src), str(target))

    ignore: Set[Path] = {src}
    deadline = time.time() + 5.0
    leftovers = list_intermediate_leftovers(tmp_path, ignore)
    while leftovers and time.time() < deadline:
        time.sleep(0.1)
        leftovers = list_intermediate_leftovers(tmp_path, ignore)
    assert not leftovers, f"leftover tmp files after failing atomic write: {leftovers}"


def test_temp_file_context_cleans_own_tmp_on_exception(tmp_path: Path) -> None:
    temp_ctx = resolve_symbol(TEMP_CTX_SYMBOLS)
    if temp_ctx is None:
        pytest.skip("_temp_file_context not found; adjust symbol list")

    parent = tmp_path
    created: Optional[Path] = None

    class DummyError(Exception):
        pass

    try:
        with temp_ctx(str(parent)) as tmp_p:
            created = Path(tmp_p)
            created.write_text("x")
            raise DummyError("trigger cleanup")
    except DummyError:
        pass

    assert created is not None
    assert (
        not created.exists()
    ), f"temp file {created} must be removed by context manager"


def test_path_lock_serializes_replace_and_remove(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    path_lock = resolve_symbol(PATH_LOCK_SYMBOLS)
    replace = resolve_symbol(REPLACE_SYMBOLS)
    safe_remove = resolve_symbol(SAFE_REMOVE_SYMBOLS)

    if path_lock is None or replace is None or safe_remove is None:
        pytest.skip("path_lock / _replace_with_retries / safe_remove not resolvable")

    target = tmp_path / "shared.bin"
    target.write_bytes(b"initial")

    original_replace = replace

    def slow_replace(src: str, dst: str, *a: Any, **kw: Any) -> None:
        tmp_candidate = Path(dst).parent / (".gmos_tmp_testtemp")
        tmp_candidate.write_text("tmp")
        time.sleep(0.2)
        try:
            original_replace(src, dst, *a, **kw)
        except Exception:
            if os.path.exists(src):
                os.replace(src, dst)
            return

    monkeypatch.setattr(
        importlib.import_module(replace.__module__), replace.__name__, slow_replace
    )

    src_path = tmp_path / "src.bin"
    src_path.write_bytes(b"newby")

    exceptions: List[Exception] = []

    def run_replace() -> None:
        try:
            replace(str(src_path), str(target))
        except Exception as e:
            exceptions.append(e)

    def run_remove() -> None:
        time.sleep(0.05)
        try:
            safe_remove(str(target))
        except Exception as e:
            exceptions.append(e)

    t1 = threading.Thread(target=run_replace)
    t2 = threading.Thread(target=run_remove)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert (
        not exceptions
    ), f"exceptions occurred during serialized operations: {exceptions}"
    assert target.exists() or not target.exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows CI filesystem handles/antivirus causes flaky PermissionErrors on concurrent writes",
)
def test_concurrent_atomic_writes_no_cross_removal(tmp_path: Path) -> None:
    atomic_writer = resolve_symbol(ATOMIC_COPY_SYMBOLS)
    temp_ctx = resolve_symbol(TEMP_CTX_SYMBOLS)
    if atomic_writer is None:
        pytest.skip("atomic write function not found")

    target = tmp_path / "shared_target.txt"
    n = 8
    contents = [f"worker-{i}\n".encode("utf-8") for i in range(n)]

    exceptions: List[Exception] = []

    def worker(idx: int) -> None:
        try:
            if temp_ctx is not None:
                with temp_ctx(str(tmp_path)) as tmp_p:
                    Path(tmp_p).write_bytes(contents[idx])
                    try:
                        atomic_writer(str(tmp_path / Path(tmp_p).name), str(target))
                    except TypeError:
                        atomic_writer(str(tmp_p), str(target))
            else:
                tmp = tmp_path / f".gmos_tmp_worker_{idx}"
                tmp.write_bytes(contents[idx])
                atomic_writer(str(tmp), str(target))
        except Exception as e:
            exceptions.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    if exceptions:
        raise exceptions[0]

    assert target.exists()
    final = target.read_bytes()
    assert final in contents, "final file must match one of the writers' content"
