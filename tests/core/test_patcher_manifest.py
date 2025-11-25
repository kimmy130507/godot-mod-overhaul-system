# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# Manifest parsing (INI) and validation rules.

import textwrap
from pathlib import Path
from typing import Any, List, cast

from gmos.core.patcher import validate_mod_config


def write_file(p: Path, data: str) -> None:
    """Helper to write file content."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding="utf-8")


def test_manifest_valid_file_replace(tmp_path: Path) -> None:
    mdir: Path = tmp_path / "my_mod"
    mdir.mkdir()
    # create a replacement file inside mod
    (mdir / "patches").mkdir()
    (mdir / "patches" / "file.tscn").write_text("dummy")

    # Updated to include required ModInfo
    content = textwrap.dedent(
        """
        [ModInfo]
        Name = Valid Mod
        Version = 1.0.0

        [FileReplace]
        res://scenes/main.tscn = patches/file.tscn

        [Dependencies]
        requires = base_mod
        """
    ).strip()
    mf: Path = mdir / "mod.mos"
    write_file(mf, content)
    ok, errors = validate_mod_config(str(mf))
    assert ok is True
    assert errors == []


def test_manifest_strict_modinfo(tmp_path: Path) -> None:
    """
    Verify strict enforcement of [ModInfo], Name, and Version.
    """
    # Case 1: Missing [ModInfo] section
    d1 = tmp_path / "no_section"
    write_file(d1 / "mod.mos", "[General]\nName=Foo")
    ok, errs = validate_mod_config(str(d1 / "mod.mos"))
    assert ok is False
    # Cast error list to handle Optional return type safely for tests
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


def test_manifest_invalid_traversal(tmp_path: Path) -> None:
    mdir: Path = tmp_path / "badmod"
    mdir.mkdir()
    (mdir / "patches").mkdir()
    # Include ModInfo to ensure we fail on traversal, not missing metadata
    content = textwrap.dedent(
        """
        [ModInfo]
        Name = Bad Mod
        Version = 1.0

        [FileReplace]
        res://scenes/main.tscn = ../outside/evil.tscn
        """
    ).strip()
    mf: Path = mdir / "mod.mos"
    write_file(mf, content)
    ok, errors = validate_mod_config(str(mf))
    assert ok is False
    err_list = cast(List[Any], errors)
    assert any("outside mod" in str(e) for e in err_list)


def test_manifest_disallowed_section(tmp_path: Path) -> None:
    mdir: Path = tmp_path / "x"
    mdir.mkdir()
    content = textwrap.dedent(
        """
        [ModInfo]
        Name = X
        Version = 1.0

        [ScriptReplace]
        something = x
        """
    ).strip()
    mf: Path = mdir / "mod.mos"
    write_file(mf, content)
    ok, errors = validate_mod_config(str(mf))
    assert ok is False
    err_list = cast(List[Any], errors)
    assert any("disallowed section" in str(e) for e in err_list)
