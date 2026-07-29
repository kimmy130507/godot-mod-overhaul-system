# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GMOS Patcher Test Suite
# Covers: Dependency Resolution, Manifest Validation, File Operations, and Smart Injection.

import os
import stat
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, cast

import pytest
from pytest import MonkeyPatch

from gmos.core.patcher import (
    ensure_within,
    patch_file_replace,
    patch_function,
    patch_smart_inject,
    patch_variable,
    resolve_mod_dependencies,
    run_patcher,
    validate_mod_config,
)
from gmos.utils import ModConfig, get_mod_name_from_config, safe_spawn

# --- Dependency Resolution Tests ---


def test_simple_dependency_order(
    tmp_path: Path, create_mod_config: Callable[..., ModConfig]
) -> None:
    a = create_mod_config(tmp_path, "A", deps=["B"])
    b = create_mod_config(tmp_path, "B", deps=[])

    ordered, errors = resolve_mod_dependencies([a, b])
    names = [get_mod_name_from_config(c) for c in ordered]
    assert names == ["B", "A"]
    assert not errors


def test_missing_dependency_reported(
    tmp_path: Path, create_mod_config: Callable[..., ModConfig]
) -> None:
    a = create_mod_config(tmp_path, "A", deps=["Missing"])
    _ordered, errors = resolve_mod_dependencies([a])
    assert "A" in errors
    assert any("missing dependency" in e for e in errors["A"])


def test_cycle_detected(
    tmp_path: Path, create_mod_config: Callable[..., ModConfig]
) -> None:
    a = create_mod_config(tmp_path, "A", deps=["B"])
    b = create_mod_config(tmp_path, "B", deps=["A"])
    _ordered, errors = resolve_mod_dependencies([a, b])
    # New behavior: cycle is broken by heuristic, so list is full size
    assert len(_ordered) == 2

    # At least one mod should report the cycle warning
    has_cycle_warning = False
    all_errs: List[List[str]] = list(errors.values())
    for err_list in all_errs:
        if any("Dependency cycle detected" in e for e in err_list):
            has_cycle_warning = True
            break
    assert has_cycle_warning


# --- Manifest Validation Tests ---


def test_manifest_valid_file_replace(
    tmp_path: Path, write_file: Callable[[Path, str], Path]
) -> None:
    mdir = tmp_path / "my_mod"

    write_file(mdir / "patches" / "file.tscn", "dummy")

    content = textwrap.dedent("""
        [ModInfo]
        Name = Valid Mod
        Version = 1.0.0

        [FileReplace]
        res://scenes/main.tscn = patches/file.tscn

        [Dependencies]
        requires = base_mod
        """).strip()
    write_file(mdir / "mod.mos", content)

    ok, errors = validate_mod_config(str(mdir / "mod.mos"))
    assert ok is True
    assert not errors


def test_manifest_strict_modinfo(
    tmp_path: Path, write_file: Callable[[Path, str], Path]
) -> None:
    """Verify strict enforcement of [ModInfo], Name, and Version."""
    # Case 1: Missing [ModInfo] section
    d1 = tmp_path / "no_section"
    write_file(d1 / "mod.mos", "[General]\nName=Foo")
    ok, errs = validate_mod_config(str(d1 / "mod.mos"))
    assert ok is False
    err_list = cast(List[Any], errs or [])
    assert any(
        "missing required section: [modinfo]" in str(e).lower() for e in err_list
    )

    # Case 2: Missing Name
    d2 = tmp_path / "no_name"
    write_file(d2 / "mod.mos", "[ModInfo]\nVersion=1.0")
    ok, errs = validate_mod_config(str(d2 / "mod.mos"))
    assert ok is False
    err_list = cast(List[Any], errs or [])
    assert any("missing required field: name" in str(e).lower() for e in err_list)

    # Case 3: Missing Version
    d3 = tmp_path / "no_version"
    write_file(d3 / "mod.mos", "[ModInfo]\nName=Foo")
    ok, errs = validate_mod_config(str(d3 / "mod.mos"))
    assert ok is False
    err_list = cast(List[Any], errs or [])
    assert any("missing required field: version" in str(e).lower() for e in err_list)

    # Case 4: Valid
    d4 = tmp_path / "valid"
    write_file(d4 / "mod.mos", "[ModInfo]\nName=Foo\nVersion=1.0")
    ok, errs = validate_mod_config(str(d4 / "mod.mos"))
    assert ok is True


def test_manifest_invalid_traversal(
    tmp_path: Path, write_file: Callable[[Path, str], Path]
) -> None:
    mdir: Path = tmp_path / "badmod"
    mdir.mkdir()
    (mdir / "patches").mkdir()

    content = textwrap.dedent("""
        [ModInfo]
        Name = Bad Mod
        Version = 1.0

        [FileReplace]
        res://scenes/main.tscn = ../outside/evil.tscn
        """).strip()
    mf: Path = mdir / "mod.mos"
    write_file(mf, content)
    ok, errors = validate_mod_config(str(mf))
    assert ok is False
    err_list = cast(List[Any], errors)
    assert any("outside mod" in str(e) for e in err_list)


def test_manifest_disallowed_section(
    tmp_path: Path, write_file: Callable[[Path, str], Path]
) -> None:
    mdir: Path = tmp_path / "x"
    mdir.mkdir()
    content = textwrap.dedent("""
        [ModInfo]
        Name = X
        Version = 1.0

        [ScriptReplace]
        something = x
        """).strip()
    mf: Path = mdir / "mod.mos"
    write_file(mf, content)
    ok, errors = validate_mod_config(str(mf))
    assert ok is False
    err_list = cast(List[Any], errors)
    assert any("disallowed section" in str(e) for e in err_list)


# --- File Operations & Execution Tests ---


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
    game_dir: Path = tmp_path / "game"
    game_dir.mkdir()
    mod_dir: Path = tmp_path / "mod"
    mod_dir.mkdir()

    src: Path = mod_dir / "patch.bin"
    src.write_bytes(b"\x00\x01NEW")

    target_res = "res://../../outside_dir/escape.bin"
    try:
        log: List[str] = patch_file_replace(str(game_dir), target_res, str(src))
    except RuntimeError:
        return
    else:
        assert isinstance(log, list)
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

    img: Path = game_dir / "assets" / "logo.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x00\x01OLD")

    script: Path = game_dir / "scripts" / "a.gd"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('old')")

    patch_img: Path = mod_dir / "patches/logo_fixed.png"
    patch_img.parent.mkdir(parents=True, exist_ok=True)
    patch_img.write_bytes(b"\x00\x01NEW")

    patch_script: Path = mod_dir / "patches/a.gd"
    patch_script.parent.mkdir(parents=True, exist_ok=True)
    patch_script.write_text("print('new')")

    log = patch_file_replace(str(game_dir), "res://assets/logo.png", str(patch_img))
    assert isinstance(log, list)
    assert img.exists()
    assert img.read_bytes() == patch_img.read_bytes()

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
    log = patch_file_replace(str(game_dir), target_res, str(patch))

    assert isinstance(log, list)
    assert orig_logo.read_bytes() == b"REPLACED"

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
    p = safe_spawn(cmd, cwd=str(game_dir))

    if isinstance(p, dict):
        rc = p.get("returncode")
        assert rc == 0
    else:
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
    game_dir: Path = tmp_path / "game"
    game_dir.mkdir()

    plan: List[Tuple[str, str, Tuple[Any, ...]]] = [
        ("BadMod", "UnknownOp", ("not", "enough"))
    ]
    logs: List[str] = run_patcher(str(game_dir), plan)
    assert isinstance(logs, list)
    assert len(logs) >= 0


def test_run_patcher_handles_functionpatch_exception(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
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
    assert any("FATAL" in s or "ERROR" in s or "WARNING" in s for s in logs)


# --- Smart Injection Tests ---


@pytest.fixture
def smart_env(
    game_project: Path, write_file: Callable[[Path, str], Path]
) -> Tuple[Path, Path]:
    """Sets up a game directory with a specific sample script for injection tests."""
    # We overwrite the default player.gd created by game_project to have specific structure
    script_content = (
        "extends Node\n\n"
        "var config = {\n"
        "\t'volume': 100,\n"
        "\t'fullscreen': true\n"
        "}\n\n"
        "func _ready():\n"
        "\tprint('Hello')\n"
        "\t# End of ready\n"
    )
    # game_project already has 'scripts' folder, but we write to root to match old tests
    script_path = game_project / "player.gd"
    write_file(script_path, script_content)

    return game_project, script_path


def test_smart_patch_inject_at_start(
    smart_env: Tuple[Path, Path],
    tmp_path: Path,
    write_file: Callable[[Path, str], Path],
) -> None:
    game_dir, script_path = smart_env

    # Source code to inject
    mod_src = write_file(tmp_path / "mod_start.gd", "print('Injected Start')")

    patch_smart_inject(
        str(game_dir), "player.gd", "_ready", str(mod_src), inject_at="start"
    )

    content = script_path.read_text(encoding="utf-8")

    assert "print('Injected Start')" in content
    # Verify placement: Start means BEFORE existing body code
    assert content.index("print('Injected Start')") < content.index("print('Hello')")
    # Verify indentation: The target used \t, so we expect \t
    assert "\tprint('Injected Start')" in content


def test_smart_patch_inject_at_end(
    smart_env: Tuple[Path, Path],
    tmp_path: Path,
    write_file: Callable[[Path, str], Path],
) -> None:
    game_dir, script_path = smart_env

    mod_src = write_file(tmp_path / "mod_end.gd", "print('Injected End')")

    patch_smart_inject(
        str(game_dir), "player.gd", "_ready", str(mod_src), inject_at="end"
    )

    content = script_path.read_text(encoding="utf-8")

    assert "print('Injected End')" in content
    # Verify placement: End means AFTER existing body code
    assert content.index("print('Hello')") < content.index("print('Injected End')")


def test_smart_patch_anchor_injection(
    smart_env: Tuple[Path, Path],
    tmp_path: Path,
    write_file: Callable[[Path, str], Path],
) -> None:
    """Test injecting a variable into a Dictionary using an anchor."""
    game_dir, script_path = smart_env

    mod_src = write_file(tmp_path / "mod_var.gd", "'difficulty': 'hard',")

    # Inject after 'volume': 100,
    patch_smart_inject(
        str(game_dir),
        "player.gd",
        "config",
        str(mod_src),
        inject_at=None,
        anchor="'volume': 100,",
    )

    content = script_path.read_text(encoding="utf-8")

    assert "'difficulty': 'hard'," in content
    # Verify order
    idx_anchor = content.index("'volume': 100,")
    idx_new = content.index("'difficulty': 'hard',")
    idx_next = content.index("'fullscreen': true")

    assert idx_anchor < idx_new < idx_next
    # Verify indentation
    assert "\t'difficulty': 'hard'," in content


def test_smart_patch_missing_function(
    smart_env: Tuple[Path, Path], tmp_path: Path
) -> None:
    game_dir, _ = smart_env
    mod_src = tmp_path / "mod.gd"
    mod_src.touch()

    # Try to patch a non-existent function
    log = patch_smart_inject(
        str(game_dir), "player.gd", "_process", str(mod_src), inject_at="start"
    )

    assert any("ERROR" in line for line in log)
    assert "Could not find" in str(log)


# --- Function & Variable Patching Tests ---


def test_patch_function_replace(
    smart_env: Tuple[Path, Path],
    tmp_path: Path,
    write_file: Callable[[Path, str], Path],
) -> None:
    game_dir, script_path = smart_env
    mod_src = write_file(
        tmp_path / "mod_func.gd", "func _ready():\n\tprint('Replaced')\n"
    )

    patch_function(
        game_dir=str(game_dir),
        target_res="player.gd",
        target_func="_ready",
        source_path=str(mod_src),
        source_func="_ready",
        mode="replace",
    )

    content = script_path.read_text(encoding="utf-8")
    assert "print('Replaced')" in content
    assert "print('Hello')" not in content


def test_patch_function_prefix(
    smart_env: Tuple[Path, Path],
    tmp_path: Path,
    write_file: Callable[[Path, str], Path],
) -> None:
    game_dir, script_path = smart_env
    mod_src = write_file(
        tmp_path / "mod_func.gd", "func prefix__ready():\n\tprint('Before')\n"
    )

    patch_function(
        game_dir=str(game_dir),
        target_res="player.gd",
        target_func="_ready",
        source_path=str(mod_src),
        source_func="prefix__ready",
        mode="prefix",
    )

    content = script_path.read_text(encoding="utf-8")
    assert "print('Before')" in content
    assert "print('Hello')" in content
    assert content.index("print('Before')") < content.index("print('Hello')")


def test_patch_function_create(
    smart_env: Tuple[Path, Path],
    tmp_path: Path,
    write_file: Callable[[Path, str], Path],
) -> None:
    game_dir, script_path = smart_env
    mod_src = write_file(
        tmp_path / "mod_func.gd", "func new_func():\n\tprint('Created')\n"
    )

    patch_function(
        game_dir=str(game_dir),
        target_res="player.gd",
        target_func="new_func",
        source_path=str(mod_src),
        source_func="new_func",
        mode="create",
    )

    content = script_path.read_text(encoding="utf-8")
    assert "func new_func():" in content
    assert "print('Created')" in content


def test_patch_variable_replace(
    smart_env: Tuple[Path, Path],
    tmp_path: Path,
    write_file: Callable[[Path, str], Path],
) -> None:
    game_dir, script_path = smart_env
    mod_src = write_file(
        tmp_path / "mod_var.gd", "var config = {\n\t'replaced': true\n}\n"
    )

    patch_variable(
        game_dir=str(game_dir),
        target_res="player.gd",
        target_var="config",
        source_path=str(mod_src),
        source_var="config",
        mode="replace",
    )

    content = script_path.read_text(encoding="utf-8")
    assert "'replaced': true" in content
    assert "'volume': 100" not in content


def test_patch_variable_add(
    smart_env: Tuple[Path, Path],
    tmp_path: Path,
    write_file: Callable[[Path, str], Path],
) -> None:
    game_dir, script_path = smart_env
    mod_src = write_file(
        tmp_path / "mod_var.gd", "var config = {\n\t'added_key': 999,\n}\n"
    )

    patch_variable(
        game_dir=str(game_dir),
        target_res="player.gd",
        target_var="config",
        source_path=str(mod_src),
        source_var="config",
        mode="add",
    )

    content = script_path.read_text(encoding="utf-8")
    assert "'volume': 100" in content
    assert "'added_key': 999," in content


# --- Pipeline Integration Tests (Packed vs Loose Mode) ---


def test_run_patcher_packed_mode_selective_deployment(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Verifies standard resources go to VFS/PCK and native binaries are symlinked in packed mode."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()

    (mod_dir / "script.gd").write_text("print('test')", encoding="utf-8")
    (mod_dir / "plugin.dll").write_bytes(b"MZ...")

    plan: List[Tuple[str, str, Tuple[Any, ...]]] = [
        ("MyMod", "FileReplace", ("res://script.gd", str(mod_dir / "script.gd"))),
        ("MyMod", "FileReplace", ("res://plugin.dll", str(mod_dir / "plugin.dll"))),
    ]

    symlinked_files: List[str] = []

    def mock_deploy(self: Any, rel_path: str, source_path: str) -> bool:
        symlinked_files.append(rel_path)
        return True

    packed_pck_path: Optional[str] = None
    packed_files: Dict[str, Any] = {}

    def mock_pack_pck(output_pck: str, files_to_pack: Dict[str, Any]) -> None:
        nonlocal packed_pck_path, packed_files
        packed_pck_path = output_pck
        packed_files = files_to_pack

    monkeypatch.setattr("gmos.core.patcher.SymlinkManager.deploy", mock_deploy)
    monkeypatch.setattr("gmos.core.patcher.pack_pck", mock_pack_pck)

    run_patcher(
        game_dir=str(game_dir),
        patch_plan=plan,
        game_executable="Brotato.exe",
        is_packed=True,
    )

    # Verify PCK Name is the fixed GMOS override
    assert packed_pck_path == str(game_dir / "gmos_override.pck")

    # Verify Selective Deployment
    assert "plugin.dll" in symlinked_files
    assert "script.gd" not in symlinked_files

    assert "res://script.gd" in packed_files
    assert "res://plugin.dll" not in packed_files


def test_run_patcher_loose_mode_deployment(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Verifies all modified assets bypass the PCK and are symlinked in loose mode."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()

    (mod_dir / "script.gd").write_text("print('test')", encoding="utf-8")
    (mod_dir / "plugin.dll").write_bytes(b"MZ...")

    plan: List[Tuple[str, str, Tuple[Any, ...]]] = [
        ("MyMod", "FileReplace", ("res://script.gd", str(mod_dir / "script.gd"))),
        ("MyMod", "FileReplace", ("res://plugin.dll", str(mod_dir / "plugin.dll"))),
    ]

    symlinked_files: List[str] = []

    def mock_deploy(self: Any, rel_path: str, source_path: str) -> bool:
        symlinked_files.append(rel_path)
        return True

    monkeypatch.setattr("gmos.core.patcher.SymlinkManager.deploy", mock_deploy)

    run_patcher(game_dir=str(game_dir), patch_plan=plan, is_packed=False)

    assert "script.gd" in symlinked_files
    assert "plugin.dll" in symlinked_files
