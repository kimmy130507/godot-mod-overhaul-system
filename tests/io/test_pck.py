# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import struct
from pathlib import Path
from typing import Dict, Union

from gmos.io.cache import detect_godot_version
from gmos.io.pck import (
    PCK_MAGIC,
    PCKReader,
    get_file_content,
    get_main_pck_path,
    pack_pck,
)


def test_pack_and_read_pck(tmp_path: Path) -> None:
    """Verify GMOS can build and unpack native Godot PCK archives via raw byte-streams."""
    # 1. Create dummy files
    src1 = tmp_path / "script1.gd"
    src1.write_bytes(b"extends Node\n")

    src2 = tmp_path / "icon.png"
    src2.write_bytes(b"\x89PNG\r\n\x1a\n")

    files_to_pack = {"res://scripts/script1.gd": str(src1), "res://icon.png": str(src2)}

    out_pck = tmp_path / "test.pck"

    # 2. Pack them into native V2 format
    pack_pck(out_pck, files_to_pack, format_version=2)  # type: ignore[arg-type]
    assert out_pck.exists()

    # 3. Read them back using the optimized PCKReader
    with PCKReader(str(out_pck)) as reader:
        assert reader.header is not None
        assert reader.header.magic == 0x43504447  # 'GDPC' signature
        assert len(reader.header.files) == 2

        data1 = reader.read_file("res://scripts/script1.gd")
        assert data1 == b"extends Node\n"

        data2 = reader.read_file("res://icon.png")
        assert data2 == b"\x89PNG\r\n\x1a\n"

        # Check cache-miss
        assert reader.read_file("res://missing.gd") is None

    # 4. Test single-file convenience extractor
    data = get_file_content(str(out_pck), "res://icon.png")
    assert data == b"\x89PNG\r\n\x1a\n"


def _create_embedded_exe(
    exe_path: Path, pck_files: Dict[str, Union[str, Path]]
) -> None:
    """Helper to build a fake executable with an embedded PCK archive."""
    # Fake binary prefix
    fake_exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 1024
    pck_start_offset = len(fake_exe_bytes)

    # Write initial EXE stub
    exe_path.write_bytes(fake_exe_bytes)

    # Pack temporary PCK
    temp_pck = exe_path.parent / "temp.pck"
    pack_pck(temp_pck, pck_files)
    pck_bytes = temp_pck.read_bytes()
    temp_pck.unlink()

    # Append PCK bytes + 12-byte Godot embedded footer
    # Footer format: [8-byte uint64 PCK start offset][4-byte uint32 PCK magic]
    footer = struct.pack("<Q", pck_start_offset) + struct.pack("<I", PCK_MAGIC)

    with open(exe_path, "ab") as f:
        f.write(pck_bytes)
        f.write(footer)


def test_get_main_pck_path_standalone_pck(tmp_path: Path) -> None:
    """Verify standalone .pck files take priority."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    pck_file = game_dir / "game.pck"
    pck_file.touch()

    assert get_main_pck_path(str(game_dir)) == str(pck_file)


def test_get_main_pck_path_embedded_exe(tmp_path: Path) -> None:
    """Verify get_main_pck_path finds embedded PCK inside an executable when no .pck exists."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    dummy_script = tmp_path / "main.gd"
    dummy_script.write_text("extends Node", encoding="utf-8")

    exe_file = game_dir / "game.exe"
    _create_embedded_exe(exe_file, {"res://main.gd": str(dummy_script)})

    resolved_path = get_main_pck_path(str(game_dir))
    assert resolved_path == str(exe_file)


def test_pck_reader_embedded_exe(tmp_path: Path) -> None:
    """Verify PCKReader reads files directly from an embedded executable."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    script_file = tmp_path / "player.gd"
    script_content = "extends CharacterBody2D\nfunc _ready(): pass"

    # Write raw bytes to lock in the exact encoding and line endings
    script_file.write_bytes(script_content.encode("utf-8"))

    exe_file = game_dir / "game.exe"
    _create_embedded_exe(exe_file, {"res://player.gd": str(script_file)})

    with PCKReader(str(exe_file)) as reader:
        assert reader.header is not None
        content = reader.read_file("res://player.gd")
        assert content is not None
        assert content.decode("utf-8") == script_content


def test_detect_godot_version_from_embedded_exe(tmp_path: Path) -> None:
    """Verify detect_godot_version extracts Godot major version from embedded EXE."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    proj_file = tmp_path / "project.godot"
    proj_file.write_text("config_version=5", encoding="utf-8")

    exe_file = game_dir / "Brotato.exe"
    _create_embedded_exe(exe_file, {"res://project.godot": str(proj_file)})

    version = detect_godot_version(str(game_dir))
    assert version == 4
