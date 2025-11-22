# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025 Kim
#
# This file is part of GMOS.
#
# GMOS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GMOS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GMOS.  If not, see <https://www.gnu.org/licenses/>.
"""
PCK Tools: Utilities for parsing and manipulating Godot PCK archives.
Implements logic to read the index and append patched files non-destructively.
"""

import os
import struct
from dataclasses import dataclass
from typing import BinaryIO, List, Optional

from gmos.utils import logger

# Godot PCK Magic: 'GDPC'
PCK_MAGIC = 0x43504447
# Default alignment for Godot files
PCK_PADDING = 16


@dataclass
class PCKFileEntry:
    path: str
    offset: int
    size: int
    md5: bytes
    flags: int = 0


@dataclass
class PCKHeader:
    magic: int
    format_version: int
    major: int
    minor: int
    patch: int
    flags: int = 0
    file_base: int = 0
    files: List[PCKFileEntry] = None  # type: ignore

    def __post_init__(self) -> None:
        self.files = []


class PCKError(Exception):
    pass


def _read_string(f: BinaryIO) -> str:
    """Read a Pascal-style (length-prefixed) string from the PCK."""
    len_bytes = f.read(4)
    if len(len_bytes) < 4:
        raise PCKError("Unexpected EOF reading string length")
    length = struct.unpack("<I", len_bytes)[0]

    # Sanity check on string length
    if length > 65536:
        raise PCKError(f"String length too large: {length}")

    string_data = f.read(length)
    if len(string_data) < length:
        raise PCKError("Unexpected EOF reading string data")

    # Godot stores strings as UTF-8 (usually). Skip trailing null if present.
    return string_data.decode("utf-8").rstrip("\x00")


def _write_string(f: BinaryIO, s: str) -> None:
    """Write a string in Godot PCK format (length + utf8 data)."""
    # Godot strings are padded to 4 bytes alignment in some versions,
    # but standard pack format mostly just uses straight length+data.
    data = s.encode("utf-8")
    f.write(struct.pack("<I", len(data)))
    f.write(data)


def read_pck_header(pck_path: str) -> PCKHeader:
    """
    Parse the header and file index of a Godot .pck file.
    """
    if not os.path.exists(pck_path):
        raise FileNotFoundError(f"PCK file not found: {pck_path}")

    with open(pck_path, "rb") as f:
        magic_bytes = f.read(4)
        if len(magic_bytes) < 4:
            raise PCKError("Empty file")

        magic = struct.unpack("<I", magic_bytes)[0]
        if magic != PCK_MAGIC:
            raise PCKError(f"Invalid PCK magic: {hex(magic)}")

        # Read version
        fmt_ver = struct.unpack("<I", f.read(4))[0]
        v_major = struct.unpack("<I", f.read(4))[0]
        v_minor = struct.unpack("<I", f.read(4))[0]
        v_patch = struct.unpack("<I", f.read(4))[0]

        flags = 0
        file_base = 0

        # Format Version 2 (Godot 4.x) adds flags and file_base offset
        if fmt_ver >= 2:
            flags = struct.unpack("<I", f.read(4))[0]
            file_base = struct.unpack("<Q", f.read(8))[0]

        # Reserved bytes (16 * 4)
        f.read(16 * 4)

        file_count = struct.unpack("<I", f.read(4))[0]
        logger.debug(
            "Reading PCK %s: %d files, format %d", pck_path, file_count, fmt_ver
        )

        entries: List[PCKFileEntry] = []

        for _ in range(file_count):
            path = _read_string(f)
            ofst = struct.unpack("<Q", f.read(8))[0]
            size = struct.unpack("<Q", f.read(8))[0]
            md5 = f.read(16)

            entry_flags = 0
            if fmt_ver >= 2:
                entry_flags = struct.unpack("<I", f.read(4))[0]

            entries.append(PCKFileEntry(path, ofst + file_base, size, md5, entry_flags))

        return PCKHeader(
            magic, fmt_ver, v_major, v_minor, v_patch, flags, file_base, entries
        )


def get_file_content(pck_path: str, res_path: str) -> Optional[bytes]:
    """
    Extract a specific file from the PCK by its resource path (e.g. 'res://icon.png').
    Returns bytes or None if not found.
    """
    header = read_pck_header(pck_path)
    target = next((e for e in header.files if e.path == res_path), None)
    if not target:
        return None

    with open(pck_path, "rb") as f:
        f.seek(target.offset)
        return f.read(target.size)


def append_file_to_pck(pck_path: str, file_data: bytes, res_path: str) -> None:
    """
    Append a new file to the PCK by rebuilding it safely.
    Implements 'Safe Rebuild' strategy to prevent header growth from corrupting data.
    """
    from gmos.io import replace_with_retries, safe_remove

    header = read_pck_header(pck_path)

    # Prepare new entry metadata
    import hashlib

    md5 = hashlib.md5(file_data, usedforsecurity=False).digest()
    size = len(file_data)

    # 1. Update internal file list (in memory only first)
    # We must preserve the original order but update/append the target file
    new_files_list: List[PCKFileEntry] = []
    replaced = False

    for entry in header.files:
        if entry.path == res_path:
            # Replace existing entry
            new_files_list.append(PCKFileEntry(res_path, 0, size, md5, 0))
            replaced = True
        else:
            # Keep existing entry (offset will be recalculated)
            new_files_list.append(entry)

    if not replaced:
        new_files_list.append(PCKFileEntry(res_path, 0, size, md5, 0))

    header.files = new_files_list

    # 2. Calculate new header size to determine start of data
    # We perform a dummy write to a null stream to measure the header exactly
    class NullIO:
        count: int

        def __init__(self) -> None:
            self.count = 0

        def write(self, b: bytes) -> int:
            n = len(b)
            self.count += n
            return n

    dummy = NullIO()
    # Write magic/versions
    dummy.write(b"\x00" * 16)  # Magic + versions
    if header.format_version >= 2:
        dummy.write(b"\x00" * 12)  # Flags + file_base
    dummy.write(b"\x00" * 64)  # Reserved
    dummy.write(b"\x00" * 4)  # File count

    # Write file entries
    for entry in header.files:
        # String path (len + utf8)
        s_data = entry.path.encode("utf-8")
        dummy.write(struct.pack("<I", len(s_data)))
        dummy.write(s_data)
        # Offset, size, md5
        dummy.write(b"\x00" * 32)
        if header.format_version >= 2:
            dummy.write(b"\x00" * 4)

    header_end = dummy.count

    # Align start of data
    header_padding = (PCK_PADDING - (header_end % PCK_PADDING)) % PCK_PADDING
    current_offset = header_end + header_padding

    # 3. Recalculate offsets for ALL files
    for entry in header.files:
        entry.offset = current_offset
        current_offset += entry.size
        # Each file data blob is also aligned? Godot source usually aligns the *start* of files.
        # We will align the next file start.
        file_pad = (PCK_PADDING - (current_offset % PCK_PADDING)) % PCK_PADDING
        current_offset += file_pad

    # 4. Rewrite entire PCK to temp file
    tmp_pck = pck_path + ".tmp_rebuild"

    try:
        with open(pck_path, "rb") as f_src, open(tmp_pck, "wb") as f_dst:

            # --- Write Header ---
            f_dst.write(struct.pack("<I", header.magic))
            f_dst.write(struct.pack("<I", header.format_version))
            f_dst.write(struct.pack("<I", header.major))
            f_dst.write(struct.pack("<I", header.minor))
            f_dst.write(struct.pack("<I", header.patch))

            if header.format_version >= 2:
                f_dst.write(struct.pack("<I", header.flags))
                f_dst.write(struct.pack("<Q", header.file_base))

            f_dst.write(b"\x00" * (16 * 4))  # Reserved
            f_dst.write(struct.pack("<I", len(header.files)))

            for entry in header.files:
                _write_string(f_dst, entry.path)
                f_dst.write(struct.pack("<Q", entry.offset))
                f_dst.write(struct.pack("<Q", entry.size))
                f_dst.write(entry.md5)
                if header.format_version >= 2:
                    f_dst.write(struct.pack("<I", entry.flags))

            # Write Header Padding
            f_dst.write(b"\x00" * header_padding)

            # --- Write Data Blobs ---
            # We need the original header to find where old files are
            original_header = read_pck_header(pck_path)

            for entry in header.files:
                # Is this the new file?
                if entry.path == res_path:
                    f_dst.write(file_data)
                else:
                    # Copy from source
                    # Find original entry
                    orig = next(
                        (e for e in original_header.files if e.path == entry.path), None
                    )
                    if orig:
                        f_src.seek(orig.offset)
                        # Efficient copy using shutil
                        # We can't use copyfileobj easily for a slice, so read chunks
                        left = orig.size
                        while left > 0:
                            chunk = f_src.read(min(left, 1024 * 1024))
                            if not chunk:
                                break
                            f_dst.write(chunk)
                            left -= len(chunk)
                    else:
                        # Should not happen unless file logic is flawed
                        logger.error(f"Lost file during rebuild: {entry.path}")

                # Write blob padding
                pad = (PCK_PADDING - (f_dst.tell() % PCK_PADDING)) % PCK_PADDING
                if pad > 0:
                    f_dst.write(b"\x00" * pad)

        # Atomic replace
        replace_with_retries(tmp_pck, pck_path)
        logger.info("Rebuilt PCK %s: appended '%s'", pck_path, res_path)

    except Exception as e:
        if os.path.exists(tmp_pck):
            safe_remove(tmp_pck)
        raise e


def get_main_pck_path(game_dir: str) -> Optional[str]:
    """
    Identifies the primary PCK file for the game.
    Strategy:
    1. Look for a .pck with the same name as the executable (if discernable).
    2. Look for the largest .pck file in the directory.
    """
    candidates: List[os.DirEntry[str]] = []
    try:
        with os.scandir(game_dir) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(".pck"):
                    candidates.append(entry)
    except OSError:
        return None

    if not candidates:
        return None

    # Sort by size descending (Main PCK is usually the biggest)
    candidates.sort(key=lambda e: e.stat().st_size, reverse=True)
    return candidates[0].path


def extract_pck(pck_path: str, output_dir: str) -> int:
    """
    Extracts all files from a PCK archive to the output directory.
    Returns the count of extracted files.
    """
    header = read_pck_header(pck_path)
    count = 0

    os.makedirs(output_dir, exist_ok=True)

    with open(pck_path, "rb") as f:
        for entry in header.files:
            # Clean path: strip res:// and avoid traversal
            rel_path = entry.path.replace("res://", "").lstrip("/")
            parts = [
                p
                for p in rel_path.replace("\\", "/").split("/")
                if p and p != ".." and p != "."
            ]
            if not parts:
                continue

            safe_rel = os.path.join(*parts)
            dest_path = os.path.join(output_dir, safe_rel)

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            f.seek(entry.offset)
            with open(dest_path, "wb") as out_f:
                out_f.write(f.read(entry.size))
            count += 1

    logger.info("Extracted %d files from %s to %s", count, pck_path, output_dir)
    return count
