# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# For general purpose functions, IO, locking, and basic path utilities.
import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

from gmos import utils
from gmos.core.patcher import (
    _res_to_path,  # type: ignore [reportPrivateUsage]
    ensure_within,
)
from gmos.io import atomic_copy_with_single_bak, atomic_write_bytes, atomic_write_copy
from gmos.io.locking import (
    acquire_app_lock,
    acquire_workroot_lock,
    release_app_lock,
    release_platform_lock,
)


def test_gmos_importable() -> None:
    """Smoke test to ensure the main module is accessible."""
    import gmos

    assert gmos is not None


# --- Test Core Functionality ---


def test_atomic_write_bytes_and_permissions(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    data = b"hello-bytes"
    atomic_write_bytes(str(dest), data, mode=0o640)
    assert dest.exists()
    assert dest.read_bytes() == data
    mode = dest.stat().st_mode & 0o777
    # Windows often defaults to 0o666 (rw-rw-rw-) even if we request restrictive.
    allowed_modes = (0o640, 0o644, 0o600)
    if sys.platform == "win32":
        allowed_modes += (0o666,)

    assert (
        mode in allowed_modes
    ), f"Got mode {oct(mode)}, expected one of {[oct(m) for m in allowed_modes]}"


def test_atomic_write_copy(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("line1\nline2\n", encoding="utf-8")
    atomic_write_copy(str(src), str(dst))
    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "line1\nline2\n"


def test_ensure_within(tmp_path: Path) -> None:
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


def test_file_lock_acquire_and_release(tmp_path: Path) -> None:
    lock_path = str(tmp_path / "testgmos.lock")
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


def test_acquire_workroot_lock_and_release(tmp_path: Path) -> None:
    wr = str(tmp_path / "workroot")
    os.makedirs(wr, exist_ok=True)
    ok = acquire_workroot_lock(wr)
    assert ok is True
    # check for common lock artifacts but accept platform variance
    lockfile = os.path.join(wr, ".gmos.lock")
    sockfile = os.path.join(wr, ".gmos.sock")
    # either a platform lock exists OR a lock file exists. We simply assert we can release.
    release_app_lock()
    release_platform_lock()
    # Allow time for cleanup; some systems may release locks asynchronously
    deadline = time.time() + 0.5
    while time.time() < deadline and (
        os.path.exists(lockfile) or os.path.exists(sockfile)
    ):
        time.sleep(0.02)

    # after release, neither file should be left behind (best-effort)
    assert not os.path.exists(lockfile)
    assert not os.path.exists(sockfile)


# --- Test Backup & Restore ---


def test_backup_and_restore(tmp_path: Path) -> None:
    orig = tmp_path / "orig"
    work = tmp_path / "work"
    orig.mkdir()
    work.mkdir()
    target = work / "data.txt"
    target.write_text("original")
    # simulate replacement that should create .bak
    src = tmp_path / "new.txt"
    src.write_text("replacement")
    atomic_copy_with_single_bak(str(src), str(target))
    bak = Path(str(target) + ".bak")
    # bak of original must exist and contain original content
    assert bak.exists()
    assert bak.read_text() == "original"
    # target should now contain replacement content
    assert target.read_text() == "replacement"


# --- Test Resource Path Conversion ---


def test_res_to_path_rejects_traversal() -> None:
    with pytest.raises(RuntimeError):
        _res_to_path("res://../outside/file.txt")
    with pytest.raises(RuntimeError):
        _res_to_path("res://../../escape.bin")


def test_res_to_path_normalizes_dots() -> None:
    # '.' should be removed
    out = _res_to_path("res://scenes/./main.tscn")
    assert out == os.path.join("scenes", "main.tscn")

    # inner '..' should collapse
    out2 = _res_to_path("res://scenes/sub/../main.tscn")
    assert out2 == os.path.join("scenes", "main.tscn")


def test_res_to_path_accepts_plain_relative_and_empty() -> None:
    # plain relative path (no res:// prefix)
    p = "scenes/main.tscn"
    assert _res_to_path(p) == os.path.join("scenes", "main.tscn")

    # res:// with no trailing path returns empty string
    assert _res_to_path("res://") == ""
    assert _res_to_path("") == ""


# --- Test Utilities ---


def test_run_checked_simple_print() -> None:
    # use sys.executable for portability
    code = "import sys; print('hello-utils')"
    proc = utils.run_checked([sys.executable, "-c", code])
    assert "hello-utils" in proc.stdout


def test_run_checked_error_raises() -> None:
    # non-zero exit should raise CalledProcessError
    with pytest.raises(subprocess.CalledProcessError):
        utils.run_checked([sys.executable, "-c", "import sys; sys.exit(2)"])


def test_run_stream_iterates_lines() -> None:
    # Create a small one-shot python script that prints multiple lines
    script = textwrap.dedent(
        """
        import sys
        for i in range(3):
            print(f"line-{i}")
        """
    )
    lines = list(utils.run_stream([sys.executable, "-c", script]))
    assert len(lines) == 3
    assert lines[0].strip() == "line-0"


def test_safe_norm_expanduser() -> None:
    v = utils.safe_norm("~")
    assert os.path.normpath(os.path.expanduser("~")) == v


def test_run_checked_sequence_simple_print() -> None:
    code = "import sys; print('hello-utils-extended')"
    proc = utils.run_checked([sys.executable, "-c", code])
    assert "hello-utils-extended" in proc.stdout


def test_run_checked_string_no_shell() -> None:
    # provide a single string; run_checked will shlex.split() it
    # On Windows, sys.executable contains backslashes (C:\...).
    # shlex.split() treats backslashes as escapes, mangling the path.
    exe = sys.executable.replace("\\", "/")

    proc = utils.run_checked(f'{exe} -c "print(12345)"')
    assert proc.returncode == 0
    assert "12345" in proc.stdout


@pytest.mark.skipif(os.name == "nt", reason="shell quoting differs on Windows/cmd.exe")
def test_run_checked_shell_true_string() -> None:
    # shell=True path (POSIX-only test)
    cmd = "echo SHELL_OK"
    proc = utils.run_checked(cmd, shell=True)  # nosec B604
    assert "SHELL_OK" in proc.stdout


def test_run_checked_timeout_raises() -> None:
    with pytest.raises(subprocess.TimeoutExpired):
        utils.run_checked(
            [sys.executable, "-c", "import time; time.sleep(2)"], timeout=0.2
        )


def test_run_checked_env_and_cwd() -> None:
    script = textwrap.dedent(
        """\
        import os,sys
        print(os.environ.get('GMOS_TEST_ENV'))
        print(os.getcwd())
    """
    )
    # On Windows CI, Python 3.10 needs SYSTEMROOT to initialize entropy.
    # We must merge the current environment with our test env.
    env = os.environ.copy()
    env["GMOS_TEST_ENV"] = "1"
    with tempfile.TemporaryDirectory() as td:
        proc = utils.run_checked(
            [sys.executable, "-c", script],
            env=env,  # Use the merged environment
            cwd=td,
        )
        out = proc.stdout.strip().splitlines()
        assert out[0].strip() == "1"
        assert os.path.normpath(out[1].strip()) == os.path.normpath(td)


def test_run_stream_nonzero_exit_reports_stderr() -> None:
    # Process writes to stderr then exits non-zero. run_stream should raise CalledProcessError
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        list(
            utils.run_stream(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stderr.write('ERRMSG'); sys.exit(2)",
                ]
            )
        )
    err = excinfo.value.stderr
    assert err is not None and "ERRMSG" in err


def test_run_stream_large_output_streaming() -> None:
    # Stream many lines to ensure streaming works and memory stays bounded.
    lines = 1000
    script = "for i in range(%d): print(f'line-{i}')" % (lines,)
    got = list(utils.run_stream([sys.executable, "-c", script]))
    assert len(got) == lines
    assert got[0] == "line-0"
    assert got[-1] == f"line-{lines-1}"
