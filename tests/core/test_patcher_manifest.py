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
    content = textwrap.dedent(
        """
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


def test_manifest_invalid_traversal(tmp_path: Path) -> None:
    mdir: Path = tmp_path / "badmod"
    mdir.mkdir()
    (mdir / "patches").mkdir()
    content = textwrap.dedent(
        """
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
