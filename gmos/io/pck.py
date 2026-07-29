# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025-2026 Kim
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
Includes optimized PCKReader for O(1) random access.
"""

import hashlib
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, TypedDict, Union

from gmos.utils import logger

# Godot PCK Magic: 'GDPC'
PCK_MAGIC = 0x43504447
# Alignment
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
    files: List[PCKFileEntry] = field(default_factory=lambda: [])


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

    # Godot strings are UTF-8. Skip trailing null.
    return string_data.decode("utf-8").rstrip("\x00")


class PCKReader:
    """
    Optimized reader that parses the PCK header once.
    """

    def __init__(self, path: str, parse_files: bool = True):
        self.path = path
        self.parse_files = parse_files
        self.file_obj: Optional[BinaryIO] = None
        self.header: Optional[PCKHeader] = None
        self.index: Dict[str, PCKFileEntry] = {}

    def __enter__(self) -> "PCKReader":
        self.file_obj = open(self.path, "rb")
        self._read_header()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.file_obj:
            self.file_obj.close()
            self.file_obj = None

    def _read_header(self) -> None:
        f = self.file_obj
        if not f:
            raise RuntimeError("File not open")
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        f.seek(0)
        magic_bytes = f.read(4)
        if len(magic_bytes) < 4:
            raise PCKError("Empty file")

        magic = struct.unpack("<I", magic_bytes)[0]
        pck_start = 0
        if magic != PCK_MAGIC:
            chunk_size = 8 * 1024 * 1024  # 8MB chunks
            magic_bytes_le = struct.pack("<I", PCK_MAGIC)

            bytes_to_read = file_size
            scan_limit = max(0, file_size - (4 * 1024 * 1024))
            found = False
            while bytes_to_read > scan_limit and not found:
                read_size = min(bytes_to_read - scan_limit, chunk_size)
                f.seek(bytes_to_read - read_size)
                chunk = f.read(read_size)

                idx = chunk.rfind(magic_bytes_le)
                while idx != -1:
                    if idx >= 8:
                        offset_bytes = chunk[idx - 8 : idx]
                        potential_pck_start = struct.unpack("<Q", offset_bytes)[0]
                        if potential_pck_start < file_size:
                            try:
                                f.seek(potential_pck_start)
                                if f.read(4) == magic_bytes_le:
                                    magic = PCK_MAGIC
                                    pck_start = potential_pck_start
                                    found = True
                                    break
                            except Exception:
                                pass
                    idx = chunk.rfind(magic_bytes_le, 0, idx)

                bytes_to_read -= max(1, read_size - 12)

            # Fallback: Forward scan for the PCK header if the footer was not found or shifted
            if not found:
                bytes_read = 0
                forward_scan_limit = min(file_size, 128 * 1024 * 1024)
                while bytes_read < forward_scan_limit and not found:
                    read_size = min(forward_scan_limit - bytes_read, chunk_size)
                    f.seek(bytes_read)
                    chunk = f.read(read_size)

                    idx = chunk.find(magic_bytes_le)
                    while idx != -1:
                        if idx + 12 <= len(chunk):
                            fmt_ver = struct.unpack("<I", chunk[idx + 4 : idx + 8])[0]
                            v_major = struct.unpack("<I", chunk[idx + 8 : idx + 12])[0]
                            if fmt_ver in (1, 2) and v_major in (3, 4):
                                magic = PCK_MAGIC
                                pck_start = bytes_read + idx
                                found = True
                                break
                        idx = chunk.find(magic_bytes_le, idx + 1)

                    bytes_read += max(1, read_size - 32)

        if magic != PCK_MAGIC:
            raise PCKError(f"Invalid PCK magic: {hex(magic)}")

        f.seek(pck_start + 4)
        fmt_ver = struct.unpack("<I", f.read(4))[0]
        v_major = struct.unpack("<I", f.read(4))[0]
        v_minor = struct.unpack("<I", f.read(4))[0]
        v_patch = struct.unpack("<I", f.read(4))[0]

        flags = 0
        file_base = 0

        if fmt_ver >= 2:
            flags = struct.unpack("<I", f.read(4))[0]
            file_base = struct.unpack("<Q", f.read(8))[0]

        # Reserved bytes (16 * 4)
        f.read(16 * 4)

        file_count = struct.unpack("<I", f.read(4))[0]

        # Populate Header object
        entries: List[PCKFileEntry] = []
        if self.parse_files:
            for _ in range(file_count):
                path = _read_string(f)
                ofst = struct.unpack("<Q", f.read(8))[0]
                size = struct.unpack("<Q", f.read(8))[0]
                md5 = f.read(16)

                entry_flags = 0
                if fmt_ver >= 2:
                    entry_flags = struct.unpack("<I", f.read(4))[0]

                entry = PCKFileEntry(
                    path, ofst + file_base + pck_start, size, md5, entry_flags
                )
                entries.append(entry)
                self.index[path] = entry

        self.header = PCKHeader(
            magic, fmt_ver, v_major, v_minor, v_patch, flags, file_base, entries
        )

    def read_file(self, res_path: str) -> Optional[bytes]:
        """Reads file content using cached index."""
        if not self.file_obj or not self.header:
            raise RuntimeError("PCKReader not initialized (use 'with' context)")

        entry = self.index.get(res_path)
        if not entry:
            if res_path.startswith("res://"):
                entry = self.index.get(res_path.replace("res://", "", 1))
            else:
                entry = self.index.get(f"res://{res_path}")

            if not entry:
                return None

        self.file_obj.seek(entry.offset)
        return self.file_obj.read(entry.size)


def read_pck_header(pck_path: str, parse_files: bool = False) -> PCKHeader:
    """Parse header from disk."""
    if not os.path.exists(pck_path):
        raise FileNotFoundError(f"PCK file not found: {pck_path}")

    with PCKReader(pck_path, parse_files=parse_files) as reader:
        if reader.header:
            return reader.header
        raise PCKError("Failed to read header")


def get_file_content(pck_path: str, res_path: str) -> Optional[bytes]:
    """Extract file content."""
    with PCKReader(pck_path) as reader:
        return reader.read_file(res_path)


def get_main_pck_path(game_dir: str) -> Optional[str]:
    """Identifies the primary PCK file or embedded executable for the game."""
    candidates: List[os.DirEntry[str]] = []
    exe_candidates: List[os.DirEntry[str]] = []
    try:
        with os.scandir(game_dir) as it:
            for entry in it:
                if entry.is_file():
                    if entry.name.endswith(".pck"):
                        candidates.append(entry)
                    elif entry.name.endswith(
                        (".exe", ".x86_64", ".x86", ".arm64", ".app")
                    ):
                        exe_candidates.append(entry)
                    elif "." not in entry.name and os.access(entry.path, os.X_OK):
                        exe_candidates.append(entry)
    except OSError:
        return None

    if candidates:
        candidates.sort(key=lambda e: e.stat().st_size, reverse=True)
        return candidates[0].path

    if exe_candidates:
        exe_candidates.sort(key=lambda e: e.stat().st_size, reverse=True)
        magic_bytes_le = struct.pack("<I", PCK_MAGIC)
        chunk_size = 8 * 1024 * 1024  # 8MB chunks

        for exe in exe_candidates:
            try:
                with open(exe.path, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    file_size = f.tell()

                    bytes_to_read = file_size
                    scan_limit = max(0, file_size - (4 * 1024 * 1024))
                    while bytes_to_read > scan_limit:
                        read_size = min(bytes_to_read - scan_limit, chunk_size)
                        f.seek(bytes_to_read - read_size)
                        chunk = f.read(read_size)

                        idx = chunk.rfind(magic_bytes_le)
                        while idx != -1:
                            if idx >= 8:
                                offset_bytes = chunk[idx - 8 : idx]
                                pck_start = struct.unpack("<Q", offset_bytes)[0]
                                if pck_start < file_size:
                                    try:
                                        f.seek(pck_start)
                                        if f.read(4) == magic_bytes_le:
                                            return exe.path
                                    except Exception:
                                        pass
                            idx = chunk.rfind(magic_bytes_le, 0, idx)

                        bytes_to_read -= max(1, read_size - 12)

                    # Fallback forward scan
                    bytes_read = 0
                    forward_scan_limit = min(file_size, 128 * 1024 * 1024)
                    while bytes_read < forward_scan_limit:
                        read_size = min(forward_scan_limit - bytes_read, chunk_size)
                        f.seek(bytes_read)
                        chunk = f.read(read_size)

                        idx = chunk.find(magic_bytes_le)
                        while idx != -1:
                            if idx + 12 <= len(chunk):
                                fmt_ver = struct.unpack("<I", chunk[idx + 4 : idx + 8])[
                                    0
                                ]
                                v_major = struct.unpack(
                                    "<I", chunk[idx + 8 : idx + 12]
                                )[0]
                                if fmt_ver in (1, 2) and v_major in (3, 4):
                                    return exe.path
                            idx = chunk.find(magic_bytes_le, idx + 1)

                        bytes_read += max(1, read_size - 32)
            except Exception:
                continue
    return None


class FileMetadata(TypedDict):
    path_bytes: bytes
    size: int
    md5: bytes
    data: bytes
    offset: int


def pack_pck(
    output_pck: Union[str, Path],
    files_to_pack: Dict[str, Union[str, Path]],
    format_version: int = 2,
) -> None:
    """Packs a dictionary of res:// paths to local file paths into a native PCK."""
    file_metadata: List[FileMetadata] = []
    for res_path, local_path in files_to_pack.items():
        with open(local_path, "rb") as f:
            data = f.read()

        md5_hash = hashlib.md5(data, usedforsecurity=False).digest()
        path_bytes = res_path.encode("utf-8")
        file_metadata.append(
            {
                "path_bytes": path_bytes,
                "size": len(data),
                "md5": md5_hash,
                "data": data,
                "offset": 0,
            }
        )

    class CountIO:
        count: int = 0

        def write(self, b: bytes) -> int:
            n = len(b)
            self.count += n
            return n

    dummy = CountIO()
    dummy.write(b"\x00" * 20)
    if format_version >= 2:
        dummy.write(b"\x00" * 12)
    dummy.write(b"\x00" * 64)
    dummy.write(b"\x00" * 4)

    for meta in file_metadata:
        dummy.write(struct.pack("<I", len(meta["path_bytes"])))
        dummy.write(meta["path_bytes"])
        dummy.write(b"\x00" * 32)
        if format_version >= 2:
            dummy.write(b"\x00" * 4)

    header_end = dummy.count
    header_padding = (PCK_PADDING - (header_end % PCK_PADDING)) % PCK_PADDING
    current_offset = header_end + header_padding

    for meta in file_metadata:
        meta["offset"] = current_offset
        current_offset += meta["size"]
        file_pad = (PCK_PADDING - (current_offset % PCK_PADDING)) % PCK_PADDING
        current_offset += file_pad

    with open(output_pck, "wb") as out_f:
        out_f.write(struct.pack("<I", PCK_MAGIC))
        out_f.write(struct.pack("<I", format_version))
        out_f.write(struct.pack("<III", 4, 3, 0))

        if format_version >= 2:
            out_f.write(struct.pack("<I", 0))
            out_f.write(struct.pack("<Q", 0))

        out_f.write(b"\x00" * 64)
        out_f.write(struct.pack("<I", len(file_metadata)))

        for meta in file_metadata:
            out_f.write(struct.pack("<I", len(meta["path_bytes"])))
            out_f.write(meta["path_bytes"])
            out_f.write(struct.pack("<QQ", meta["offset"], meta["size"]))
            out_f.write(meta["md5"])
            if format_version >= 2:
                out_f.write(struct.pack("<I", 0))

        out_f.write(b"\x00" * header_padding)

        for meta in file_metadata:
            out_f.write(meta["data"])
            pad = (PCK_PADDING - (out_f.tell() % PCK_PADDING)) % PCK_PADDING
            if pad > 0:
                out_f.write(b"\x00" * pad)

    logger.info("Packed %d files into %s", len(file_metadata), output_pck)
