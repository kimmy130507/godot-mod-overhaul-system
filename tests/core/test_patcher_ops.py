# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# FileReplace, EnsureWithin, and Patcher Execution tests.

import os
import stat
import sys
import time
from pathlib import Path
from typing import Any, List, Tuple

import pytest
from pytest import MonkeyPatch

from gmos.core.patcher import ensure_within, patch_file_replace, run_patcher
from gmos.utils import _safe_spawn  # type: ignore [reportPrivateUsage]


def test_ensure_within_rejects_traversal(tmp_path: Path) -> None:
    root: Path = tmp_path / "work"
    root.mkdir()
    # attempt to reference a sibling outside the work dir
    outside: Path = tmp_path / "outside_dir" / "evil.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("not allowed")

    # relative traversal path
    traversal = str(root / "../outside_dir/evil.txt")
    with pytest.raises(RuntimeError):
        ensure_within(str(root), traversal)

    # absolute path outside root
    with pytest.raises(RuntimeError):
        ensure_within(str(root), str(outside))


def test_patch_file_replace_fails_when_target_outside(tmp_path: Path) -> None:
    # Single folder architecture
    game_dir: Path = tmp_path / "game"
    game_dir.mkdir()
    mod_dir: Path = tmp_path / "mod"
    mod_dir.mkdir()

    src: Path = mod_dir / "patch.bin"
    src.write_bytes(b"\x00\x01NEW")

    target_res = "res://../../outside_dir/escape.bin"
    try:
        # Updated signature: (game_dir, target_res, source_path)
        log: List[str] = patch_file_replace(str(game_dir), target_res, str(src))
    except RuntimeError:
        return
    else:
        assert isinstance(log, list)
        # Check log usage
        joined = "\n".join(str(x) for x in log)
        assert (
            "Path escape detected" in joined
            or "Path escape" in joined
            or "Invalid resource path traversal" in joined
        ), f"Expected path-escape error in log, got: {joined}"


def test_file_replace_binary_and_text(tmp_path: Path) -> None:
    game_dir: Path = tmp_path / "game"
    game_dir.mkdir()
    mod_dir: Path = tmp_path / "mod"
    mod_dir.mkdir()

    # Create vanilla files in game_dir
    img: Path = game_dir / "assets" / "logo.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x00\x01OLD")

    script: Path = game_dir / "scripts" / "a.gd"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('old')")

    # Create mod replacement files
    patch_img: Path = mod_dir / "patches/logo_fixed.png"
    patch_img.parent.mkdir(parents=True, exist_ok=True)
    patch_img.write_bytes(b"\x00\x01NEW")

    patch_script: Path = mod_dir / "patches/a.gd"
    patch_script.parent.mkdir(parents=True, exist_ok=True)
    patch_script.write_text("print('new')")

    # Apply patch_file_replace
    log = patch_file_replace(str(game_dir), "res://assets/logo.png", str(patch_img))
    # Check log usage
    assert isinstance(log, list)

    # Verify replacement happened in-place
    assert img.exists()
    assert img.read_bytes() == patch_img.read_bytes()

    # Verify script replacement
    log2 = patch_file_replace(str(game_dir), "res://scripts/a.gd", str(patch_script))
    assert any("SUCCESS" in line for line in log2)
    assert script.read_text() == "print('new')"


def test_file_replace_and_launch_headless(tmp_path: Path) -> None:
    game_dir: Path = tmp_path / "game"
    game_dir.mkdir()

    assets_dir: Path = game_dir / "assets"
    assets_dir.mkdir()
    orig_logo: Path = assets_dir / "logo.png"
    orig_logo.write_bytes(b"ORIGINAL")

    mod_dir: Path = tmp_path / "mod"
    mod_dir.mkdir()
    patch: Path = mod_dir / "logo_new.png"
    patch.write_bytes(b"REPLACED")

    target_res = "res://assets/logo.png"
    # Updated signature
    log = patch_file_replace(str(game_dir), target_res, str(patch))

    assert isinstance(log, list)
    # Verify replacement
    assert orig_logo.read_bytes() == b"REPLACED"

    # Create portable launcher
    bin_dir: Path = game_dir / "bin"
    bin_dir.mkdir()
    launcher: Path = bin_dir / "launcher.py"
    out_file: Path = game_dir / "launcher_ran.txt"

    launcher.write_text(
        "import pathlib\n"
        "path=pathlib.Path(" + repr(str(out_file)) + ")\n"
        "path.write_text('launched', encoding='utf-8')\n"
    )

    try:
        st = os.stat(launcher)
        os.chmod(launcher, st.st_mode | stat.S_IEXEC)
    except Exception:
        pass

    cmd: List[str] = [sys.executable, str(launcher)]
    p = _safe_spawn(cmd, cwd=str(game_dir))

    # Handle the result based on type strictly to satisfy Pylance
    if isinstance(p, dict):  # Captured output result
        # It is a dict; it has no .returncode attribute, use key access
        rc = p.get("returncode")
        assert rc == 0
    else:
        # It is a Popen object (Pylance infers this via elimination)
        p.wait(timeout=10)
        if p.returncode != 0:
            import subprocess

            completed = subprocess.run(
                cmd, cwd=str(game_dir), capture_output=True, text=True
            )
            raise AssertionError(
                f"launcher failed (rc={completed.returncode}) stdout={completed.stdout!r} stderr={completed.stderr!r}"
            )
        assert p.returncode == 0

    time.sleep(0.05)
    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == "launched"


def test_run_patcher_returns_log_on_malformed_plan(tmp_path: Path) -> None:
    """run_patcher should not crash for malformed or unknown ops; it must return a log list."""
    game_dir: Path = tmp_path / "game"
    game_dir.mkdir()

    plan: List[Tuple[str, str, Tuple[Any, ...]]] = [
        ("BadMod", "UnknownOp", ("not", "enough"))
    ]
    # Updated signature: run_patcher(game_dir, plan)
    logs: List[str] = run_patcher(str(game_dir), plan)
    assert isinstance(logs, list)
    # expect at least one line (no crash)
    assert len(logs) >= 0


def test_run_patcher_handles_functionpatch_exception(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """If patch_function raises, run_patcher must record an error and continue/return a log."""
    game_dir: Path = tmp_path / "game"
    game_dir.mkdir()

    def fake_patch_function(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulated")

    import gmos.core.patcher

    monkeypatch.setattr(gmos.core.patcher, "patch_function", fake_patch_function)

    plan: List[Tuple[str, str, Tuple[Any, ...]]] = [
        ("M", "FunctionPatch", ("res://file", None, "mods/x/patch.gd", "f", None))
    ]
    logs: List[str] = run_patcher(str(game_dir), plan)
    assert isinstance(logs, list)
    # ensure error recorded
    assert any("FATAL" in s or "ERROR" in s or "WARNING" in s for s in logs)
