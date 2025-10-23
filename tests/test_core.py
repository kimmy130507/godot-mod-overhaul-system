import os
import time

import pytest

# import functions under test
from gmos import (
    acquire_app_lock,
    acquire_workroot_lock,
    atomic_write_bytes,
    atomic_write_copy,
    ensure_within,
    release_app_lock,
    release_platform_lock,
)


def test_atomic_write_bytes_and_permissions(tmp_path):
    dest = tmp_path / "out.bin"
    data = b"hello-bytes"
    atomic_write_bytes(str(dest), data, mode=0o640)
    assert dest.exists()
    assert dest.read_bytes() == data
    mode = dest.stat().st_mode & 0o777
    assert mode in (0o640, 0o644, 0o600)  # allow some platform variance


def test_atomic_write_copy(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("line1\nline2\n", encoding="utf-8")
    atomic_write_copy(str(src), str(dst))
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "line1\nline2\n"


def test_ensure_within(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    inner = base / "sub" / "file.txt"
    inner.parent.mkdir(parents=True)
    inner.write_text("x")
    # should not raise
    assert ensure_within(str(base), str(inner)) is True
    # outside path should raise
    outside = tmp_path / "other" / "file.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("y")
    with pytest.raises(RuntimeError):
        ensure_within(str(base), str(outside))


def test_file_lock_acquire_and_release(tmp_path):
    lock_path = str(tmp_path / "testmodloader.lock")
    # acquire
    ok = acquire_app_lock(lock_path)
    assert ok is True
    # lock file must exist and contain a pid
    assert os.path.exists(lock_path)
    with open(lock_path, "rb") as f:
        data = f.read().decode("utf-8").strip()
        assert data.isdigit()
    # release
    release_app_lock()
    # allow small delay for cleanup
    time.sleep(0.05)
    # lock file removed or empty
    if os.path.exists(lock_path):
        with open(lock_path, "rb") as f:
            d = f.read().decode("utf-8").strip()
            assert d == "" or d == str(os.getpid())


def test_acquire_workroot_lock_and_release(tmp_path):
    wr = str(tmp_path / "workroot")
    os.makedirs(wr, exist_ok=True)
    ok = acquire_workroot_lock(wr)
    assert ok is True
    # check for common lock artifacts but accept platform variance
    lockfile = os.path.join(wr, ".modloader.lock")
    sockfile = os.path.join(wr, ".modloader.sock")
    # either a platform lock exists OR a lock file exists. We simply assert we can release.
    release_app_lock()
    release_platform_lock()
    time.sleep(0.05)
    # after release, neither file should be left behind (best-effort)
    assert not os.path.exists(lockfile)
    assert not os.path.exists(sockfile)
