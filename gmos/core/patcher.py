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
import configparser
import contextlib
import datetime
import difflib
import filecmp
import json
import os
import re
import time
import zipfile
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, wait
from contextlib import ExitStack
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import (
    Any,
    Dict,
    Generator,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    TypedDict,
    Union,
    cast,
)

# Binary Diffing Support
try:
    import bsdiff4  # type: ignore[reportMissingTypeStubs, unused-ignore]

    _bsdiff_found = True
except ImportError:
    bsdiff4 = cast(Any, None)
    _bsdiff_found = False

from gmos.core import security
from gmos.core.parser import Lexer as GDScriptLexer
from gmos.core.parser import Token, TokenType
from gmos.io import (
    SymlinkManager,
    atomic_replace,
    atomic_write_bytes,
    atomic_write_copy,
    atomic_write_with_backup,
    get_io_executor,
    pack_pck,
    safe_atomic_copy_with_bak,
    safe_remove,
    safe_write_text,
)
from gmos.io.locking import pause_game_dir_watcher, resume_game_dir_watcher
from gmos.io.pck import PCKReader
from gmos.state import policy
from gmos.utils import ModConfig, get_mod_name_from_config, logger

NATIVE_BIN_EXTENSIONS = (".dll", ".so", ".dylib", ".gdextension")


class ConflictDelegate(Protocol):
    """Interface for resolving file conflicts (UI or Headless)."""

    def resolve(self, file_path: str, orig_text: str, new_text: str) -> Optional[str]:
        # This is an interface. Implementation belongs in UI layer.
        ...


_UNIFIED_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# VFS Cache for synthetic merges and patched files
GMOS_CACHE_DIR = "gmos_data/cache/merged"

# --- I/O helpers and caching ---
# Optimization: Avoid loading large files into memory during conflict checks.
_small_file_limit = int(os.environ.get("GMOS_SMALL_FILE_LIMIT", str(5 * 1024 * 1024)))
_HASH_CHUNK = 1024 * 1024  # 1 MiB


@lru_cache(maxsize=1024)
def _read_text_cached(path: str) -> str:
    """Read text file into memory (errors ignored). Cached for reuse during one patch run."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# --- Cache control helpers ---
def clear_read_cache() -> None:
    """Clear the internal LRU cache used for small-file text reads.

    Call this after any operation that mutates files on disk so subsequent
    conflict checks read fresh content.
    """
    try:
        _read_text_cached.cache_clear()
    except Exception:
        logger.debug("failed to clear read cache", exc_info=True)


def set_small_file_limit(bytes_count: int) -> None:
    """Change _small_file_limit at runtime and clear cache to avoid inconsistent behavior."""
    global _small_file_limit
    _small_file_limit = int(bytes_count)
    clear_read_cache()


# --- Write helpers that invalidate caches ---
def write_atomic_replace(target_path: str, text: str) -> None:
    """Atomic text replace then clear read cache."""
    try:
        pause_game_dir_watcher()
        atomic_replace(target_path, text)
    finally:
        resume_game_dir_watcher()
    clear_read_cache()


def write_atomic_write_copy(src: str, dst: str) -> None:
    """Atomic copy (src -> dst) then clear read cache."""
    atomic_write_copy(src, dst)
    clear_read_cache()


def write_atomic_write_with_backup(target_path: str, new_text: str) -> None:
    """Atomic write with single bak, then clear read cache."""
    try:
        pause_game_dir_watcher()
        atomic_write_with_backup(target_path, new_text)
    finally:
        resume_game_dir_watcher()
    clear_read_cache()


def write_safe_atomic_copy_with_bak(
    src: str, dst: str, *args: Any, **kwargs: Any
) -> None:
    """Safe wrapper that delegates to io.safe_atomic_copy_with_bak and clears cache."""
    try:
        safe_atomic_copy_with_bak(src, dst, *args, **kwargs)
    finally:
        clear_read_cache()


# --- Patch Context Manager ---
@contextlib.contextmanager
def patch_run_context() -> Generator[None, None, None]:
    """
    Context manager to wrap a full patch/apply run.

    Clears the small-file read cache on enter/exit to ensure subsequent operations see current files.

    Usage:
        with patch_run_context():
            ... perform patch/preview/apply operations ...
    """
    clear_read_cache()
    try:
        yield
    finally:
        clear_read_cache()


def write_safe_write_text(path: str, text: str) -> None:
    """Call through to io.safe_write_text (or earlier safe_write_text) then invalidate cache."""
    # safe_write_text is imported earlier from gmos.io; call it and clear cache.
    try:
        safe_write_text(path, text)
    finally:
        clear_read_cache()


# --- Type Definitions ---


class Hunk(TypedDict):
    """Defines the structure of a parsed unified diff hunk."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[str]
    old_lines: List[str]
    new_lines: List[str]


class RuntimeManifest(TypedDict):
    timestamp: str
    game_dir: str
    applied_ops_count: int
    applied_ops: List[Dict[str, str]]
    modified_files: List[str]


# --- Godot Path Helpers ---


def resolve_res_path(res_path: str) -> str:
    """
    Convert a Godot `res://` resource path to a safe filesystem-relative path.
    - Accepts strings beginning with 'res://' or plain relative paths.
    - Rejects any path that attempts to traverse above the resource root (leading '..').
    - Collapses '.' and '..' segments safely.

    Returns a platform-native relative path (no leading slash).
    """
    if not res_path:
        return ""

    # strip prefix if present
    if res_path.startswith("res://"):
        rel = res_path[len("res://") :]
    else:
        rel = res_path

    rel = rel.replace("\\", "/").lstrip("/")

    # collapse segments while rejecting escapes that would pop past root
    parts: List[str] = []
    for segment in rel.split("/"):
        if segment == "" or segment == ".":
            continue
        if segment == "..":
            if not parts:
                # trying to escape above the res:// root
                raise RuntimeError(f"Invalid resource path traversal: {res_path}")
            parts.pop()
            continue
        parts.append(segment)

    # return platform-native relative path
    return os.path.join(*parts) if parts else ""


def sanitize_script_content(content: str, filename: str) -> str:
    """
    Sanitizes GDScript content using AST-based rewriting.
    Prevents malicious calls (OS.execute) by redirecting them to the Sandbox.
    """
    if not (filename.endswith(".gd") or filename.endswith(".gdc")):
        return content
    return security.secure_rewrite_script(content)


def ensure_within(base: str, target: str) -> bool:
    """
    Ensure `target` is within `base` after resolving symlinks.
    Raises RuntimeError on violation.

    Uses Path.resolve(strict=False) so non-existent targets are still resolved
    in a canonical manner where possible. Treats the base itself as allowed.
    """
    if not base:
        raise RuntimeError("ensure_within: base path empty")

    # use pathlib resolve to canonicalize symlinks
    base_p = Path(base).resolve(strict=False)
    target_p = Path(target).resolve(strict=False)

    # exact match is allowed
    if target_p == base_p:
        return True

    try:
        if not target_p.is_relative_to(base_p):
            raise RuntimeError(f"Path escape detected. base={base_p} target={target_p}")
    except AttributeError:
        # Fallback for Python < 3.9
        try:
            target_p.relative_to(base_p)
        except ValueError:
            raise RuntimeError(
                f"Path escape detected. base={base_p} target={target_p}"
            ) from None

    return True


# --- Mod Dependency Logic ---


def _parse_dependencies_from_config(mod_config: ModConfig) -> Set[str]:
    """Return a set of dependency names declared by this mod config."""
    deps: Set[str] = set()
    sections = mod_config.get("Sections", {}) or {}
    for sec_k in sections.keys():
        if sec_k.lower() == "dependencies":
            section_content = sections[sec_k]
            if isinstance(section_content, list):
                for line in section_content:
                    try:
                        _, val = [p.strip() for p in line.split("=", 1)]
                    except Exception:
                        val = line.strip()
                    for part in (p.strip() for p in val.split(",") if p.strip()):
                        if part:
                            deps.add(part)
    return deps


def resolve_mod_dependencies(
    mod_configs: Sequence[ModConfig],
) -> Tuple[List[ModConfig], Dict[str, List[str]]]:
    """
    Topologically sort mods by declared dependencies.

    Returns (ordered_mod_configs, errors_dict).
    - ordered_mod_configs: list in load order (deps first). If cycles exist the
      returned list will be partial (nodes outside cycles).
    - errors_dict: mapping mod_name -> list[str] of error messages (missing deps or cycle)
    """
    # map name -> config
    name_to_cfg: Dict[str, ModConfig] = {}
    name_priority: Dict[str, int] = {}
    for i, cfg in enumerate(mod_configs):
        name = get_mod_name_from_config(cfg)
        name_to_cfg[name] = cfg
        name_priority[name] = i

    # build adjacency: dep -> set(dependents)
    adj: Dict[str, Set[str]] = {n: set() for n in name_to_cfg}
    # track missing deps
    errors: Dict[str, List[str]] = {}
    deps_of: Dict[str, Set[str]] = {}

    for name, cfg in name_to_cfg.items():
        deps = _parse_dependencies_from_config(cfg)
        deps_of[name] = deps
        for d in deps:
            if d not in name_to_cfg:
                errors.setdefault(name, []).append(f"missing dependency: {d}")
            else:
                adj[d].add(name)

    # Kahn's algorithm for topological sort
    # compute in-degree
    indeg: Dict[str, int] = dict.fromkeys(name_to_cfg, 0)
    for _src, targets in adj.items():
        for t in targets:
            indeg[t] += 1

    # Initialize queue with nodes having 0 dependencies, sorted by priority
    queue = [n for n, d in indeg.items() if d == 0]
    queue.sort(key=lambda n: name_priority.get(n, 9999))
    order: List[str] = []

    # Process until all nodes are ordered
    while len(order) < len(name_to_cfg):
        if not queue:
            # Cycle detected: Queue is empty but nodes remain.
            # Break cycle by picking the remaining node with highest priority (lowest index in user list).
            remaining = [n for n in name_to_cfg if n not in order]
            if not remaining:
                break  # Should not happen

            # Sort by user list order (0 is top/first)
            remaining.sort(key=lambda n: name_priority.get(n, 9999))
            forced_node = remaining[0]

            # Force load
            queue.append(forced_node)
            # Decrement to satisfy deps for neighbors in the cycle
            errors.setdefault(forced_node, []).append(
                "Dependency cycle detected. Forced load order based on priority."
            )
        n = queue.pop(0)
        order.append(n)
        # Sort neighbors by priority for stable resolution
        neighbors = sorted(adj.get(n, []), key=lambda x: name_priority.get(x, 9999))
        for m in neighbors:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)

    # produce ordered configs for nodes in order
    ordered_cfgs = [name_to_cfg[n] for n in order if n in name_to_cfg]
    return ordered_cfgs, errors


def apply_dependency_resolution(
    mod_configs: List[ModConfig],
) -> Tuple[List[ModConfig], Dict[str, List[str]]]:
    """
    Annotate mod_configs with dependency resolution results.

    Adds per-config keys:
      - '_deps_errors': list[str] of human messages (missing deps, cycles)
      - '_resolved_order_rank': int (0-based) if placed in topo order

    Returns (ordered_configs, errors_dict) where ordered_configs is the list
    sorted in load order (dependencies first). Mods involved in cycles or with missing
    deps may be missing a rank and will be placed after resolved mods.
    """
    ordered, errors = resolve_mod_dependencies(mod_configs)

    # clear previous annotations
    for cfg in mod_configs:
        cfg.pop("_deps_errors", None)
        cfg.pop("_resolved_order_rank", None)

    # assign ranks for ordered mods
    rank = 0
    for cfg in ordered:
        name = get_mod_name_from_config(cfg)
        cfg["_resolved_order_rank"] = rank
        rank += 1

    # attach errors from resolver to original configs
    for name, errs in errors.items():
        # find matching config by name and set _deps_errors
        for cfg in mod_configs:
            if get_mod_name_from_config(cfg) == name:
                cfg["_deps_errors"] = list(errs)
                break

    # place unresolved configs (cycles/missing deps) after resolved ones, preserve relative order
    def _sort_key(cfg: ModConfig) -> Tuple[int, Union[str, int]]:
        r: Optional[int] = cfg.get("_resolved_order_rank")
        if r is None:
            # place after resolved; keep stable order by mod folder name
            return (1, get_mod_name_from_config(cfg).lower())
        return (0, r)

    ordered_all = sorted(mod_configs, key=_sort_key)
    return ordered_all, errors


# --- Core Patcher Logic ---


def _leading_whitespace(line: str) -> str:
    """Returns the leading whitespace of a line."""
    return line[: len(line) - len(line.lstrip("\t "))]


class CSTParser:
    """
    Analyzes text streams to inject payload data at specific contextual indices.
    """

    def __init__(self, source: str):
        self.lexer = GDScriptLexer()
        self.tokens = list(self.lexer.tokenize(source))
        self.lines = source.splitlines(keepends=True)

    def _get_indent_level(self, line_idx: int) -> int:
        """Returns the length of the leading whitespace for a given line."""
        if line_idx >= len(self.lines):
            return 0
        line = self.lines[line_idx]
        return len(line) - len(line.lstrip())

    def find_function_body(self, func_name: str) -> Optional[Tuple[int, int]]:
        for i, t in enumerate(self.tokens):
            if t.type == TokenType.KEYWORD and t.value == "func":
                next_i = self._peek_next(i + 1)
                if next_i != -1 and self.tokens[next_i].value == func_name:
                    return self._resolve_func_scope(next_i)
        return None

    def find_variable_block(self, var_name: str) -> Optional[Tuple[int, int]]:
        """
        Finds the start and end lines of a variable declaration.
        Handles multi-line dicts/arrays by tracking bracket balance.
        """
        for i, t in enumerate(self.tokens):
            if t.type == TokenType.KEYWORD and t.value in ("var", "const"):
                next_i = self._peek_next(i + 1)
                if next_i != -1 and self.tokens[next_i].value == var_name:
                    start_line = t.line - 1
                    end_idx = i
                    brackets = 0
                    for j in range(i, len(self.tokens)):
                        tj = self.tokens[j]
                        if tj.type not in (TokenType.STRING, TokenType.COMMENT):
                            if tj.type == TokenType.LPAREN or tj.value in "([{":
                                brackets += 1
                            elif tj.type == TokenType.RPAREN or tj.value in ")]}":
                                brackets -= 1
                            if j > i and brackets <= 0:
                                if tj.type == TokenType.KEYWORD and tj.value in (
                                    "func",
                                    "var",
                                    "const",
                                    "pass",
                                ):
                                    end_idx = j - 1
                                    break
                                elif tj.type == TokenType.NEWLINE:
                                    end_idx = j
                                    break
                        end_idx = j
                    end_line = self.tokens[end_idx].line - 1
                    return (start_line, end_line)
        return None

    def _peek_next(self, start_idx: int) -> int:
        """Find next significant token."""
        for j in range(start_idx, len(self.tokens)):
            if self.tokens[j].type not in (
                TokenType.SKIP,
                TokenType.COMMENT,
                TokenType.NEWLINE,
            ):
                return j
        return -1

    def _resolve_func_scope(self, name_idx: int) -> Optional[Tuple[int, int]]:
        """
        Scans from function name to finding the body range.
        """
        i = name_idx + 1
        n = len(self.tokens)

        # Scan past signature to Colon
        colon_found = False
        while i < n:
            if self.tokens[i].value == ":":
                colon_found = True
                break
            i += 1

        if not colon_found:
            return None

        # Check if one-liner (code immediately follows colon on same line)
        colon_line = self.tokens[i].line - 1
        next_sig_i = self._peek_next(i + 1)

        if next_sig_i != -1 and self.tokens[next_sig_i].line - 1 == colon_line:
            return (colon_line, colon_line)

        # Standard block: Body starts on next line
        body_start = colon_line + 1
        if body_start >= len(self.lines):
            return (colon_line, colon_line)  # Empty file after func?

        func_def_indent = self._get_indent_level(
            colon_line
        )  # Approximation, usually safe

        end_line = body_start
        for j in range(body_start, len(self.lines)):
            line = self.lines[j]
            if not line.strip() or line.strip().startswith("#"):
                continue  # Skip empty/comments for bound checks

            curr_indent = len(line) - len(line.lstrip())
            if curr_indent <= func_def_indent:
                # Dedent detected -> End of block was previous line
                return (body_start, max(body_start, j - 1))
            end_line = j

        return (body_start, end_line)

    def _scan_to_statement_end(self, start_idx: int) -> int:
        """Scans until newline where bracket balance is zero."""
        balance = 0
        last_idx = start_idx
        for j in range(start_idx, len(self.tokens)):
            t = self.tokens[j]
            if t.value in "([{":
                balance += 1
            elif t.value in ")]}":
                balance -= 1

            if t.type == TokenType.NEWLINE and balance == 0:
                return last_idx  # Return index of last token on the statement line

            if t.type != TokenType.NEWLINE and t.type != TokenType.SKIP:
                last_idx = j

        return last_idx


def get_var_block(lines: List[str], var_name: str) -> Optional[Tuple[int, int]]:
    """
    Return (start, end) indices of a var or const block named var_name in lines.
    Uses CSTParser for robust multi-line detection.
    """
    sep = "" if (lines and lines[0].endswith("\n")) else "\n"
    source_text = sep.join(lines)
    parser = CSTParser(source_text)
    return parser.find_variable_block(var_name)


def get_function_block(lines: List[str], func_name: str) -> Optional[Tuple[int, int]]:
    """
    Return (start, end) indices of a function body block named func_name in lines.
    Uses CSTParser for robust block detection.
    """
    sep = "" if (lines and lines[0].endswith("\n")) else "\n"
    source_text = sep.join(lines)
    parser = CSTParser(source_text)
    return parser.find_function_body(func_name)


def parse_mod_config(mod_path: str) -> Optional[ModConfig]:
    """Parses a mod configuration file (INI-style) into a structured dictionary."""
    config_file = next(
        (f for f in ["mod.mos"] if os.path.exists(os.path.join(mod_path, f))),
        None,
    )
    if not config_file:
        return None

    config: ModConfig = {"Name": Path(mod_path).name, "Path": mod_path, "Sections": {}}
    current_section: Optional[str] = None

    try:
        with open(os.path.join(mod_path, config_file), "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            section_match = re.match(r"^\[(.+)\]$", line)
            if section_match:
                section_name = section_match.group(1).strip()
                config["Sections"][section_name] = []

                current_section = section_name
                if current_section == "ModInfo":
                    config["Sections"]["ModInfo"] = {}
                continue

            if current_section == "ModInfo":
                section_gen = config["Sections"]["ModInfo"]
                if isinstance(section_gen, dict) and "=" in line:
                    key, value = [p.strip() for p in line.split("=", 1)]
                    if key == "Name":
                        config["Name"] = value.strip('"')  # Special handling for Name
                    else:
                        section_gen[key] = value.strip('"')
            elif current_section:
                section_content = config["Sections"][current_section]
                if isinstance(section_content, list) and "=" in line:
                    section_content.append(line)

        return config

    except Exception as e:
        print(f"Error parsing mod config {os.path.join(mod_path, config_file)}: {e}")
        return None


def generate_patch_plan(
    mod_path: str, mod_config: ModConfig
) -> List[Tuple[str, str, Tuple[Any, ...]]]:
    """
    Produces a normalized list of (mod_name, operation, details) tuples.
    details:
      - FileReplace -> (target_res, source_path)
      - VariablePatch -> (target_res, target_var, source_path, source_var, mode)  # mode in ('replace','add','create')
      - FunctionPatch -> (target_res, target_func, source_path, source_func)
    DataAdd is emitted as VariablePatch with mode='create'.
    """
    plan: List[Tuple[str, str, Tuple[Any, ...]]] = []
    mod_name = mod_config.get("Name", Path(mod_path).name)
    sections = mod_config.get("Sections", {}) or {}

    # FileReplace
    file_replace_lines = sections.get("FileReplace", [])
    for line in file_replace_lines:
        target, source = [p.strip() for p in line.split("=", 1)]
        plan.append((mod_name, "FileReplace", (target, os.path.join(mod_path, source))))

    # VariablePatch (explicit mode required via '; mode=...') -> normalized to 5-tuple
    var_patch_lines = sections.get("VariablePatch", [])
    for line in var_patch_lines:
        target, source_spec = [p.strip() for p in line.split("=", 1)]
        t_res, t_var = [p.strip() for p in target.split("::", 1)]
        s_res, s_var, meta = _parse_source_with_meta(source_spec)
        s_path = os.path.join(mod_path, s_res)
        mode = meta.get("mode")
        if mode not in ("add", "replace", "create"):
            raise ValueError(
                f"VariablePatch in mod '{mod_name}' must include '; mode=add|replace|create' in line: {line}"
            )
        if mode == "replace" and s_var and s_var != t_var:
            raise ValueError(
                f"VariablePatch replace in mod '{mod_name}' attempts rename '{t_var}' -> '{s_var}'. Keep original name."
            )
        plan.append((mod_name, "VariablePatch", (t_res, t_var, s_path, s_var, mode)))

    # FunctionPatch: allow optional target function and inline metadata like '; mode=create'
    func_patch_lines = sections.get("FunctionPatch", [])
    for line in func_patch_lines:
        # split LHS = RHS
        target, source_spec = [p.strip() for p in line.split("=", 1)]
        # target may be "res://path/file.gd::func" or just "res://path/file.gd"
        t_func: Optional[str]
        if "::" in target:
            t_res, t_func_raw = [p.strip() for p in target.split("::", 1)]
            t_func = t_func_raw if t_func_raw else None
        else:
            t_res = target
            t_func = None
        # parse source (handles metadata like ; mode=create)
        s_res, s_func, meta = _parse_source_with_meta(source_spec)
        s_path = os.path.join(mod_path, s_res)
        mode = meta.get("mode")  # may be None or 'create'
        # store normalized 5-tuple for FunctionPatch: (t_res, t_func_or_None, s_path, s_func, mode)
        plan.append((mod_name, "FunctionPatch", (t_res, t_func, s_path, s_func, mode)))

    # BinaryPatch: Apply bsdiff/xdelta patch to a file
    # Format: res://path/to/file = path/to/patch.bin
    bin_patch_lines = sections.get("BinaryPatch", [])
    for line in bin_patch_lines:
        target, source = [p.strip() for p in line.split("=", 1)]
        plan.append((mod_name, "BinaryPatch", (target, os.path.join(mod_path, source))))

    # SmartPatch: Inject code into existing functions OR variables without replacement
    # Format: res://file.gd::target_name = path/to/code.gd ; at=start|end OR anchor="code snippet"
    smart_patch_lines = sections.get("SmartPatch", [])
    for line in smart_patch_lines:
        target, source_spec = [p.strip() for p in line.split("=", 1)]
        if "::" not in target:
            raise ValueError(
                f"SmartPatch target must include name (res::name): {target}"
            )
        t_res, t_name = [p.strip() for p in target.split("::", 1)]

        s_res, _, meta = _parse_source_with_meta(source_spec)
        s_path = os.path.join(mod_path, s_res)

        # Mode resolution: Anchor > At > Default(End)
        anchor = meta.get("anchor")
        inject_at = meta.get("at", "end" if not anchor else None)

        plan.append(
            (mod_name, "SmartPatch", (t_res, t_name, s_path, inject_at, anchor))
        )

    # DataAdd treat as request to create a new top-level variable/table
    data_add_content = sections.get("DataAdd", [])
    combined_data_lines: List[str] = []
    combined_data_lines.extend(data_add_content)

    for line in combined_data_lines:
        target, source = [p.strip() for p in line.split("=", 1)]
        t_res, t_var = [p.strip() for p in target.split("::", 1)]
        s_res, s_var = [p.strip() for p in source.split("::", 1)]
        s_path = os.path.join(mod_path, s_res)
        plan.append(
            (mod_name, "VariablePatch", (t_res, t_var, s_path, s_var, "create"))
        )

    return plan


def _validate_mod_info_section(
    sections: Dict[str, Any], mod_config: Dict[str, Any]
) -> List[str]:
    """Helper to validate the ModInfo section for Name and Version."""
    errors: List[str] = []
    mod_info = sections.get("modinfo")

    if mod_info is None:
        return ["Missing required section: [ModInfo]"]

    # Enforce Dict structure for ModInfo
    if not isinstance(mod_info, dict):
        return ["[ModInfo] section format invalid (must be key=value pairs)"]
    has_name = bool(mod_config.get("Name"))
    mi_dict = cast(Dict[str, Any], mod_info)

    if not has_name and mi_dict.get("Name"):
        has_name = True

    if not has_name:
        errors.append("[ModInfo] missing required field: Name")

    # Version Check
    if not mi_dict.get("Version"):
        errors.append("[ModInfo] missing required field: Version")

    return errors


def _validate_mod_config_dict(mod_config: Dict[str, Any]) -> List[str]:
    """
    Validate the provided mod_config dict.
    Returns a list of error strings (empty if no errors).
    """
    errors: List[str] = []
    try:
        mod_path = mod_config.get("Path", "")
        raw_sections_any: Any = mod_config.get("Sections", {}) or {}
        if not isinstance(raw_sections_any, dict):
            return ["'Sections' key is not a valid dictionary."]

        raw_sections: Dict[str, Any] = cast(Dict[str, Any], raw_sections_any)
        sections = {str(k).lower(): v for k, v in raw_sections.items()}

        # Enforce allowed section names. Any unexpected section is considered invalid.
        allowed = {
            "filereplace",
            "variablepatch",
            "functionpatch",
            "binarypatch",
            "smartpatch",
            "dataadd",
            "dependencies",
            "modinfo",
        }
        for sec in raw_sections.keys():
            if str(sec).lower() not in allowed:
                errors.append(f"disallowed section: [{sec}]")

        # STRICT VALIDATION: ModInfo Name and Version are mandatory.
        errors.extend(_validate_mod_info_section(sections, mod_config))

        def validate_resource_target(res_target: str) -> Optional[str]:
            try:
                resolve_res_path(res_target)
                return None
            except Exception as e:
                return f"Invalid resource target '{res_target}': {e}"

        # FileReplace
        fr_lines_any = sections.get("filereplace", [])
        if isinstance(fr_lines_any, list):
            fr_list: List[object] = cast(List[object], fr_lines_any)
            for line_any in fr_list:
                if not isinstance(line_any, str):
                    errors.append(
                        f"Invalid non-string entry in FileReplace: {line_any}"
                    )
                    continue
                line = line_any
                try:
                    target, source = [p.strip() for p in line.split("=", 1)]
                except Exception:
                    errors.append(f"Malformed FileReplace line: {line}")
                    continue
                err = validate_resource_target(target)
                if err:
                    errors.append(err)
                if os.path.isabs(source):
                    errors.append(
                        f"FileReplace source must be relative inside mod: {source}"
                    )
                else:
                    abs_src = os.path.join(mod_path, source)
                    try:
                        ensure_within(mod_path, abs_src)
                    except Exception as e:
                        errors.append(
                            f"FileReplace source path outside mod: {source} ({e})"
                        )
                        continue
                    if not os.path.exists(abs_src):
                        errors.append(f"FileReplace source not found: {abs_src}")

        # VariablePatch
        vp_lines_any = sections.get("variablepatch", [])
        if isinstance(vp_lines_any, list):
            vp_list: List[object] = cast(List[object], vp_lines_any)
            for line_any in vp_list:
                if not isinstance(line_any, str):
                    errors.append(
                        f"Invalid non-string entry in VariablePatch: {line_any}"
                    )
                    continue
                line = line_any
                try:
                    target, source_spec = [p.strip() for p in line.split("=", 1)]
                except Exception:
                    errors.append(f"Malformed VariablePatch line: {line}")
                    continue
                try:
                    s_res, s_var, meta = _parse_source_with_meta(source_spec)
                except Exception:
                    errors.append(f"Malformed VariablePatch source spec: {source_spec}")
                    continue
                if "mode" not in meta:
                    errors.append(
                        f"VariablePatch missing '; mode=add|replace|create' in line: {line}"
                    )
                s_path = os.path.join(mod_path, s_res)
                try:
                    ensure_within(mod_path, s_path)
                except Exception as e:
                    errors.append(f"VariablePatch source outside mod: {s_res} ({e})")
                    continue
                if not os.path.exists(s_path):
                    errors.append(f"VariablePatch source not found: {s_path}")
                try:
                    t_var_list = [p.strip() for p in target.split("::", 1)]
                    t_var = t_var_list[1] if len(t_var_list) > 1 else ""
                except Exception:
                    errors.append(f"Malformed VariablePatch target: {target}")
                    continue
                if meta.get("mode") == "replace" and s_var and s_var != t_var:
                    errors.append(
                        f"VariablePatch replace attempts rename '{t_var}' -> '{s_var}' in line: {line}"
                    )

        # FunctionPatch
        fp_lines_any = sections.get("functionpatch", [])
        if isinstance(fp_lines_any, list):
            fp_list: List[object] = cast(List[object], fp_lines_any)
            for line_any in fp_list:
                if not isinstance(line_any, str):
                    errors.append(
                        f"Invalid non-string entry in FunctionPatch: {line_any}"
                    )
                    continue
                line = line_any
                try:
                    _, source = [p.strip() for p in line.split("=", 1)]
                    s_res, _ = [p.strip() for p in source.split("::", 1)]
                except Exception:
                    errors.append(f"Malformed FunctionPatch line: {line}")
                    continue
                s_path = os.path.join(mod_path, s_res)
                try:
                    ensure_within(mod_path, s_path)
                except Exception as e:
                    errors.append(f"FunctionPatch source outside mod: {s_res} ({e})")
                    continue
                if not os.path.exists(s_path):
                    errors.append(f"FunctionPatch source not found: {s_path}")

        # DataAdd
        da_lines_any = sections.get("dataadd", [])
        combined_data_lines: List[str] = []

        if isinstance(da_lines_any, list):
            da_list: List[object] = cast(List[object], da_lines_any)
            for item in da_list:
                if isinstance(item, str):
                    combined_data_lines.append(item)

        for line in combined_data_lines:
            try:
                _, source = [p.strip() for p in line.split("=", 1)]
                s_res, _ = [p.strip() for p in source.split("::", 1)]
            except Exception:
                errors.append(f"Malformed Data line: {line}")
                continue
            s_path = os.path.join(mod_path, s_res)
            try:
                ensure_within(mod_path, s_path)
            except Exception as e:
                errors.append(f"Data source outside mod: {s_res} ({e})")
                continue
            if not os.path.exists(s_path):
                errors.append(f"Data source not found: {s_path}")

        # Attempt to generate plan to catch other semantic errors
        try:
            _ = generate_patch_plan(mod_path, cast(ModConfig, mod_config))
        except Exception as e:
            errors.append(f"generate_patch_plan error: {e}")

    except Exception as e:
        return [str(e)]

    return errors


def validate_mod_config(
    mod_config: Union[str, Dict[str, Any]],
) -> Tuple[bool, Optional[object]]:
    """
    Validate a mod config or a path to a .mos manifest.

    - If passed a string path to a .mos file, returns (ok: bool, errors: List[str]).
    """
    # If caller provided a path string, parse it into a mod_config-like dict
    if isinstance(mod_config, str):
        mos_path = mod_config
        if not mos_path or not os.path.exists(mos_path):
            return False, [f"manifest missing: {mos_path}"]

        class CasePreservingConfigParser(configparser.ConfigParser):
            def optionxform(self, optionstr: str) -> str:
                return optionstr

        cp = CasePreservingConfigParser(delimiters=("=",))
        try:
            cp.read(mos_path, encoding="utf-8")
        except Exception as e:
            return False, [f"failed to parse manifest: {e}"]

        sections: Dict[str, Any] = {}
        for sec in cp.sections():
            # Special handling for ModInfo: keep as Dict for validation
            if sec.lower() == "modinfo":
                sections["ModInfo"] = dict(cp.items(sec))
            else:
                # Legacy behavior for other sections (list of strings)
                lines = [f"{k} = {v}" for k, v in cp.items(sec)]
                sections[sec] = lines

        cfg: Dict[str, Any] = {
            "Path": os.path.dirname(os.path.abspath(mos_path)),
            "Sections": sections,
        }
        errs = _validate_mod_config_dict(cfg)
        return (len(errs) == 0, errs)

    errs = _validate_mod_config_dict(mod_config)
    if errs:
        # return first error for backward compatibility
        return False, errs[0]
    return True, None


def _parse_source_with_meta(spec: str) -> Tuple[str, str, Dict[str, str]]:
    parts = [p.strip() for p in spec.split(";") if p.strip()]
    main = parts[0] if parts else ""
    meta: Dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            meta[k.strip()] = v.strip()
    if "::" in main:
        res, name = [x.strip() for x in main.split("::", 1)]
    else:
        res, name = main.strip(), ""
    return res, name, meta


def analyze_mods_for_conflicts(
    mod_configs: List[ModConfig],
) -> Dict[str, List[Tuple[str, str, Tuple[Any, ...]]]]:
    """
    Build conflict map keyed by canonical target keys:
      - Variable::<res>::<var>
      - Function::<res>::<func>
      - FileReplace::<res>
    Rules (summary):
      - Variable: multiple 'replace' -> hard conflict
      - Variable: create + replace -> conflict
      - Variable: multiple 'add' -> flagged
      - Function: >1 full replace -> conflict
      - Function: replace + wrappers -> conflict
      - FileReplace: multi-touch -> conflict
    Returns dict {target_key: [(mod_name, operation, details), ...]} for targets that need user attention.
    """
    targets: Dict[str, List[Tuple[str, str, Tuple[Any, ...]]]] = {}
    all_instructions: List[Tuple[str, str, Tuple[Any, ...]]] = []

    for mod in mod_configs:
        try:
            mod_path = mod.get("Path")
            if not mod_path:
                logger.debug(
                    "Skipping mod with no 'Path' during conflict analysis: %s",
                    mod.get("Name", "Unnamed"),
                )
                continue
            all_instructions.extend(generate_patch_plan(mod_path, mod))
        except Exception as e:
            # skip malformed mod during conflict analysis; preserve trace for audits
            logger.debug(
                "Skipping malformed mod during conflict analysis: %s (%s)",
                mod.get("Name", mod.get("Path")),
                e,
            )
            continue

    for mod_name, op, details in all_instructions:
        key: Optional[str] = None
        t_res: str = "UNKNOWN_RESOURCE"
        try:
            if op == "FileReplace":
                t_res = cast(str, details[0])
                key = f"FileReplace::{t_res}"
            elif op == "VariablePatch":
                t_res, t_var = cast(str, details[0]), cast(str, details[1])
                key = f"Variable::{t_res}::{t_var}"
            elif op == "FunctionPatch":
                t_res, t_func = cast(str, details[0]), cast(str, details[1])
                key = f"Function::{t_res}::{t_func}"
            else:
                # conservative fallback
                try:
                    t_res = cast(str, details[0])
                    key = f"Other::{t_res}"
                except Exception as e:
                    logger.debug("Conflict-key generation failed for %s: %s", t_res, e)
                    continue

            if key:
                targets.setdefault(key, []).append((mod_name, op, details))
        except Exception as e:
            logger.debug("Conflict-key generation failed for %s: %s", t_res, e)
            continue

    conflicts: Dict[str, List[Tuple[str, str, Tuple[Any, ...]]]] = {}
    for key, instrs in targets.items():
        if len(instrs) <= 1:
            continue

        if key.startswith("Variable::"):
            replace_count = sum(
                1 for _, _, d in instrs if len(d) >= 5 and d[4] == "replace"
            )
            create_count = sum(
                1 for _, _, d in instrs if len(d) >= 5 and d[4] in ("create", "dataadd")
            )
            add_count = sum(1 for _, _, d in instrs if len(d) >= 5 and d[4] == "add")

            if replace_count > 1:
                conflicts[key] = instrs
                continue
            if create_count > 0 and replace_count > 0:
                conflicts[key] = instrs
                continue
            if add_count > 1 or (add_count >= 1 and replace_count >= 1):
                conflicts[key] = instrs
                continue

        elif key.startswith("Function::"):
            # count full replacements (source func not prefixed with prefix_/postfix_)
            repls = 0
            smart_injects = 0

            for _, op, d in instrs:
                if op == "SmartPatch":
                    smart_injects += 1
                    continue

                try:
                    sfunc = d[3]
                    if not str(sfunc).startswith(("prefix_", "postfix_")):
                        repls += 1
                except Exception:
                    repls += 1

            # SmartPatch is compatible with other SmartPatches, but NOT with a full replace
            if repls > 0 and (smart_injects > 0 or repls > 1):
                conflicts[key] = instrs

        else:
            # FileReplace and BinaryPatch are generally exclusive or conflict if multiple mods touch same file
            # BinaryPatch on top of FileReplace is technically valid (stacking), but risky.
            # Multiple BinaryPatches on the same file is highly risky without strict order.
            # We flag any multi-mod contention on the same file as a conflict.
            conflicts[key] = instrs

    return conflicts


def apply_hunks(
    original_text: str,
    hunks: Union[Sequence["Hunk"], str],
    selected_hunk_indices: Optional[List[int]] = None,
) -> str:
    """
    Apply selected hunks (by index) to original_text and return merged text.
    """
    orig_lines = original_text.splitlines(keepends=False)

    # If hunks is a string (new_text), compute hunks using SequenceMatcher opcodes.
    if isinstance(hunks, str):
        new_lines = hunks.splitlines(keepends=False)
        sm = SequenceMatcher(None, orig_lines, new_lines)
        ops = sm.get_opcodes()
        computed: List[Dict[str, Any]] = []
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal":
                continue
            computed.append(
                {
                    "old_start": i1 + 1,
                    "old_count": max(0, i2 - i1),
                    "new_lines": new_lines[j1:j2],
                    "orig_segment": orig_lines[i1:i2],
                }
            )
        hunks_list = cast(List[Hunk], computed)
    else:
        hunks_list = list(hunks)

    # If selected_hunk_indices omitted, accept all hunks by default (test-friendly)
    if selected_hunk_indices is None:
        selected_hunk_indices = list(range(len(hunks_list)))

    result: List[str] = []
    ptr = 0  # pointer in orig_lines

    for idx, h in enumerate(hunks_list):
        h_data = cast(Dict[str, Any], h)

        old_start = int(h_data.get("old_start", 1)) - 1
        old_count_val = h_data.get("old_count")
        if old_count_val is not None:
            old_count = int(old_count_val)
        else:
            # Use length of old_lines if available
            old_lines_val = h_data.get("old_lines")
            if isinstance(old_lines_val, list):
                old_list: List[object] = cast(List[object], old_lines_val)
                old_count = len(old_list)
            else:
                old_count = 0
        # copy unchanged lines up to hunk
        while ptr < old_start and ptr < len(orig_lines):
            result.append(orig_lines[ptr])
            ptr += 1

        if idx in selected_hunk_indices:
            # Accept new_lines
            new_lines_seg: List[str] = []

            val_new = h_data.get("new_lines")
            if isinstance(val_new, list):
                new_lines_seg = cast(List[str], val_new)
            else:
                val_seg = h_data.get("new_segment")
                if isinstance(val_seg, list):
                    new_lines_seg = cast(List[str], val_seg)

            for nl in new_lines_seg:
                result.append(nl.rstrip("\n"))
        else:
            # keep original old_lines
            for j in range(old_count):
                if (old_start + j) < len(orig_lines):
                    result.append(orig_lines[old_start + j])

        ptr = old_start + old_count

    # append remaining tail
    while ptr < len(orig_lines):
        result.append(orig_lines[ptr])
        ptr += 1

    # reassemble with trailing newline if original had one
    return "\n".join(result) + ("\n" if original_text.endswith("\n") else "")


def generate_unified_diff(
    orig_text: str,
    new_text: str,
    fromfile: str = "orig",
    tofile: str = "new",
    n: int = 3,
) -> str:
    """Return unified diff string between orig_text and new_text."""
    a = orig_text.splitlines(keepends=True)
    b = new_text.splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=fromfile, tofile=tofile, n=n))


def parse_unified_diff_hunks(diff_text: str) -> List[Hunk]:
    """
    Parse unified diff text into a list of hunks.
    Each hunk is a dict:
      {
        'old_start': int, 'old_count': int,
        'new_start': int, 'new_count': int,
        'lines': List[str],         # raw diff lines for the hunk (including leading ' ', '-', '+')
        'old_lines': List[str],     # original-context lines for the hunk (without prefixes)
        'new_lines': List[str],     # new-context lines for the hunk (without prefixes)
      }
    """
    lines = diff_text.splitlines()
    hunks: List[Hunk] = []
    i = 0
    while i < len(lines):
        m = _UNIFIED_HUNK_RE.match(lines[i])
        if not m:
            i += 1
            continue
        old_start = int(m.group(1))
        old_count = int(m.group(2) or "1")
        new_start = int(m.group(3))
        new_count = int(m.group(4) or "1")
        i += 1
        hunk_lines: List[str] = []
        old_lines: List[str] = []
        new_lines: List[str] = []
        while i < len(lines) and not _UNIFIED_HUNK_RE.match(lines[i]):
            ln = lines[i]
            if ln.startswith("+") or ln.startswith("-") or ln.startswith(" "):
                hunk_lines.append(ln)
                if ln.startswith("-") or ln.startswith(" "):
                    old_lines.append(ln[1:])
                if ln.startswith("+") or ln.startswith(" "):
                    new_lines.append(ln[1:])
            else:
                # context or file header lines might appear; include as-is
                hunk_lines.append(ln)
            i += 1
        hunks.append(
            {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "lines": hunk_lines,
                "old_lines": old_lines,
                "new_lines": new_lines,
            }
        )
    return hunks


# --- Artifact/Dry-Run Logic ---


def save_dryrun_artifact(
    sim_log: List[str],
    temp_work_root: str,
    game_dir: str,
    out_dir: str,
    combined_diff: Optional[str] = None,
) -> Optional[str]:
    """
    Persist sim_log, runtime_manifest.json, and optional combined_diff
    to logs/dryrun_TIMESTAMP/ and zip it.

    Returns path to created bundle or None.
    """
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dry_dir = os.path.join(out_dir, f"dryrun_{ts}")
    os.makedirs(dry_dir, exist_ok=True)

    # write sim_log
    try:
        with open(os.path.join(dry_dir, "sim_log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(sim_log))
    except Exception:
        logger.exception("Failed writing sim_log text")

    # copy manifest if present
    manifest_src = os.path.join(temp_work_root, "runtime_manifest.json")
    if os.path.exists(manifest_src):
        try:
            dest_manifest = os.path.join(dry_dir, "runtime_manifest.json")
            atomic_write_copy(manifest_src, dest_manifest)
        except Exception:
            logger.exception("Failed copying runtime_manifest.json")

    # save combined diff if provided
    if combined_diff:
        try:
            combined_path = os.path.join(dry_dir, "combined_diff.patch")
            atomic_write_bytes(combined_path, combined_diff.encode("utf-8"))
        except Exception:
            logger.exception("Failed writing combined_diff.patch")

    # write metadata
    meta = {
        "timestamp_utc": ts,
        "game_dir": game_dir,
        "temp_work_root": temp_work_root,
    }
    try:
        with open(os.path.join(dry_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception:
        logger.exception("Failed writing dryrun meta.json")

    # create zipped snapshot for easy attachment
    bundle_path: Optional[str] = None
    try:
        bundle_path = os.path.join(out_dir, f"dryrun_bundle_{ts}.zip")
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(dry_dir):
                for fn in files:
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, dry_dir)
                    zf.write(full, arc)
        logger.info("Dry-run artifact written: %s", bundle_path)
    except Exception:
        logger.exception("Failed creating dryrun bundle")

    return bundle_path


# --- Patch Operations ---


def lazy_copy_file(game_dir: str, res_path: str) -> Tuple[str, str]:
    """
    Ensures the file path exists in the game directory.
    In the Single-Folder model, this just verifies existence.
    """

    relative_path = resolve_res_path(res_path)
    game_path = os.path.join(game_dir, relative_path)
    ensure_within(game_dir, game_path)

    if not os.path.exists(game_path):
        # It might be a new file being created by a mod, which is fine.
        pass

    return game_path, f"Target: {relative_path}"


def patch_file_replace(
    game_dir: str,
    target_res: str,
    source_path: str,
    conflict_delegate: Optional[ConflictDelegate] = None,
) -> List[str]:
    """Replaces the target file entirely with the source file."""
    log: List[str] = []
    orig_text: Optional[str] = None
    new_text: Optional[str] = None
    try:
        relative_path = resolve_res_path(target_res)
        work_path = os.path.join(game_dir, relative_path)
        ensure_within(game_dir, work_path)

        if not os.path.exists(source_path):
            log.append(f"ERROR: Source file not found: {source_path}. Skipping.")
            return log

        if os.path.exists(work_path):
            try:
                # Check if files are identical before expensive read/hash
                # shallow=False forces content comparison if stat signatures match.
                if filecmp.cmp(work_path, source_path, shallow=False):
                    return log
                # Fast path: if both files are relatively small, read as text and diff.
                wsize = os.path.getsize(work_path)
                ssize = os.path.getsize(source_path)
                large = (wsize > _small_file_limit) or (ssize > _small_file_limit)

                if large:
                    # large files differ: treat as binary conflict and skip interactive resolution
                    log.append(
                        f"CONFLICT: large files differ (skipping textual merge): {work_path}"
                    )
                    return log

                # Small files: use cached text reads to avoid duplicate I/O during a run
                orig_text = _read_text_cached(work_path)
                new_text = _read_text_cached(source_path)
            except FileNotFoundError:
                # Something disappeared concurrently; skip
                log.append(
                    f"ERROR: Source/work file disappeared during conflict check: {work_path}"
                )
                return log
            except Exception:
                # Conservative fallback: compare as bytes (streaming) and treat mismatch as binary
                try:
                    with open(work_path, "rb") as wf, open(source_path, "rb") as sf:
                        # stream compare without loading whole file
                        same = True
                        while True:
                            wb = wf.read(_HASH_CHUNK)
                            sb = sf.read(_HASH_CHUNK)
                            if not wb and not sb:
                                break
                            if wb != sb:
                                same = False
                                break
                        if not same:
                            log.append(
                                f"CONFLICT: binary files differ: {work_path}; skipping interactive resolution in headless mode."
                            )
                            return log
                        return log  # identical bytes -> nothing to do
                except Exception:
                    log.append(
                        f"ERROR: failed comparing files for conflict resolution: {work_path}"
                    )
                    return log
        else:
            # work_path does not exist. This is not a conflict.
            # We will just copy. No need for diff.
            # Let's read texts if small enough for consistency, in case UI wants to show.
            try:
                ssize = os.path.getsize(source_path)
                if ssize > _small_file_limit:
                    # large file, just copy, no diff
                    pass  # Let it fall through to the copy at L1464
                else:
                    orig_text = ""  # Empty original
                    new_text = _read_text_cached(source_path)
            except FileNotFoundError:
                log.append(f"ERROR: Source file disappeared: {source_path}")
                return log
            except Exception as e:
                log.append(f"ERROR: Failed reading source file {source_path}: {e}")
                return log

    except Exception as e:
        log.append(f"ERROR: conflict resolution pre-check failed: {e}")
        return log

    # At this point, orig_text and new_text are either both None (large file, or error)
    # or both set (str).
    if orig_text is not None and new_text is not None:
        if orig_text == new_text:
            return log

        merged_text: Optional[str] = None
        # Delegate to the provided resolver (UI or Headless Strategy)
        if conflict_delegate:
            try:
                merged_text = conflict_delegate.resolve(work_path, orig_text, new_text)
            except Exception as e:
                log.append(f"ERROR: conflict delegate failed: {e}")
        else:
            # No delegate provided (Default Headless Behavior)
            # Current policy: Auto-accept replacement (Last Mod Wins)
            merged_text = new_text

        if merged_text is None:
            log.append(
                f"CONFLICT: user cancelled resolution for {work_path}; skipping."
            )
            return log

        # Write merged text to Cache
        cache_path = os.path.join(game_dir, GMOS_CACHE_DIR, relative_path)
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            atomic_write_with_backup(cache_path, merged_text)

            # We copy the cached result to the game dir to ensure the file is actually updated.
            atomic_write_copy(cache_path, work_path)

            log.append(f"SUCCESS: Applied merged changes to {work_path}")
            return log
        except Exception as e:
            log.append(f"ERROR: failed to apply merged result: {e}")
            try:
                safe_remove(cache_path)
            except Exception as e:
                logger.debug("cleanup failed for %s: %s", cache_path, e)
                pass
            return log

    # No conflict: file is handled by VFS
    try:
        atomic_write_copy(source_path, work_path)
        log.append(f"SUCCESS: Copied {source_path} -> {work_path}")
    except Exception as e:
        log.append(f"ERROR: failed to copy replacement file: {e}")

    return log


def patch_smart_inject(
    game_dir: str,
    target_res: str,
    target_name: str,
    source_path: str,
    inject_at: Optional[str],
    anchor: Optional[str] = None,
    vfs: Optional[Dict[str, bytes]] = None,
    pck_pool: Optional[List[PCKReader]] = None,
) -> List[str]:
    """
    Injects code into a function OR variable using Token Stream Analysis.
    Supports 'start', 'end', or 'anchor' (token sequence matching).
    """
    log: List[str] = []
    try:
        # 1. Read Target
        _, copy_log = lazy_copy_file(game_dir, target_res)
        if copy_log:
            log.append(copy_log)

        try:
            target_text = read_source_for_patching(game_dir, target_res, vfs, pck_pool)
        except Exception as e:
            log.append(f"ERROR: SmartPatch target read failed: {e}")
            return log

        # 2. Read Source
        if not os.path.exists(source_path):
            log.append(f"ERROR: SmartPatch source missing: {source_path}")
            return log

        with open(source_path, "r", encoding="utf-8") as f:
            injection_code = f.read()

        # 3. Determine Block Bounds (Func OR Var)
        target_lines = target_text.splitlines(keepends=True)
        block_range = get_function_block(target_lines, target_name)
        block_type = "func"

        if not block_range:
            # Try variable block
            block_range = get_var_block(target_lines, target_name)
            block_type = "var"

        if not block_range:
            log.append(
                f"ERROR: Could not find function or variable '{target_name}' in {target_res}."
            )
            return log

        start_line, end_line = block_range
        # start_line is first body line (for func) or definition line (for var)

        # 4. Determine Indentation (Scan first few lines of block)
        indent_str = "\t"  # Default
        for i in range(start_line, min(end_line + 2, len(target_lines))):
            line = target_lines[i]
            stripped = line.lstrip()
            if stripped and not stripped.startswith("#"):
                ws_len = len(line) - len(stripped)
                if ws_len > 0:
                    indent_str = line[:ws_len]
                    break

        # Prepare Injection Block
        injected_lines: List[str] = []
        for line in injection_code.splitlines():
            if line.strip():
                # For vars, if we are inside a dict, simple appending might break trailing commas.
                # The user is responsible for syntax in the injected snippet (e.g. adding commas),
                # but we handle the indentation.
                injected_lines.append(indent_str + line.strip() + "\n")
            else:
                injected_lines.append("\n")

        insert_idx = -1

        # 5. Logic: Anchor vs Start/End
        if anchor:
            # ANCHOR MODE: Find token sequence inside the block
            lexer = GDScriptLexer()
            block_text = "".join(target_lines[start_line : end_line + 1])
            block_tokens = lexer.tokenize(block_text)
            anchor_tokens = lexer.tokenize(anchor)

            def filter_sig(t: Token) -> bool:
                return t.type not in (
                    TokenType.SKIP,
                    TokenType.COMMENT,
                    TokenType.NEWLINE,
                )

            sig_block = [t for t in block_tokens if filter_sig(t)]
            sig_anchor = [t for t in anchor_tokens if filter_sig(t)]

            match_index = -1
            if sig_anchor:
                for i in range(len(sig_block) - len(sig_anchor) + 1):
                    if all(
                        sig_block[i + k].value == sig_anchor[k].value
                        for k in range(len(sig_anchor))
                    ):
                        match_index = i + len(sig_anchor) - 1
                        break

            if match_index != -1:
                insert_idx = start_line + sig_block[match_index].line
            else:
                log.append(f"ERROR: Anchor '{anchor}' not found in '{target_name}'.")
                return log

        elif inject_at == "start":
            insert_idx = start_line
            # For functions, start_line is body start. For vars, it might be definition line.
            if block_type == "var":
                insert_idx = start_line + 1  # Inject after 'var x = {' line
        else:
            # End mode
            if block_type == "var":
                # For variables (Dict/Array), end_line is usually the closing brace/bracket.
                # We generally want to insert BEFORE the closing brace so we stay inside the structure.
                insert_idx = end_line
            else:
                # For functions, end_line is the last significant line of code.
                # We want to append AFTER this line to be at the end of the function.
                insert_idx = end_line + 1

        # 6. Apply Injection
        new_lines = (
            target_lines[:insert_idx] + injected_lines + target_lines[insert_idx:]
        )

        result_text = "".join(new_lines)
        _write_target(game_dir, resolve_res_path(target_res), result_text, vfs)
        log.append(
            f"SUCCESS: SmartPatch injected code into '{target_name}' (Mode: {anchor and 'Anchor' or inject_at})."
        )
        return log

    except Exception as e:
        log.append(f"ERROR: SmartPatch crashed: {e}")
        return log


def patch_function(
    game_dir: str,
    target_res: str,
    target_func: Optional[str],
    source_path: str,
    source_func: str,
    mode: Optional[str] = None,
    mod_name: Optional[str] = None,
    vfs: Optional[Dict[str, bytes]] = None,
    conflict_delegate: Optional[ConflictDelegate] = None,
    pck_pool: Optional[List[PCKReader]] = None,
) -> List[str]:
    """Patches a function in the target file with code from the source file, supporting prefix/postfix wrapping and creation."""
    log: List[str] = []
    try:
        work_path, copy_log = lazy_copy_file(game_dir, target_res)
        log.append(copy_log)
        ensure_within(game_dir, work_path)
        try:
            target_text = read_source_for_patching(game_dir, target_res, vfs, pck_pool)
            target_lines = target_text.splitlines(keepends=True)
        except Exception as e:
            log.append(f"ERROR: Failed to read target file '{work_path}': {e}")
            return log

        with open(source_path, "r", encoding="utf-8") as f:
            source_lines = f.readlines()

        # Determine effective patch mode
        effective_mode = mode
        if source_func.startswith("prefix_"):
            effective_mode = "prefix"
        elif source_func.startswith("postfix_"):
            effective_mode = "postfix"
        elif not effective_mode:
            effective_mode = "replace"

        if effective_mode == "create":
            if not target_func:
                log.append(
                    "ERROR: 'create' mode requires a target function name. Skipping."
                )
                return log
            # For 'create', the target function must NOT exist.
            if get_function_block(target_lines, target_func):
                log.append(
                    f"ERROR: Target function '{target_func}' already exists. 'create' mode will not overwrite. Skipping."
                )
                return log

            # Find the source function signature and body
            source_sig_idx = -1
            for i, ln in enumerate(source_lines):
                if re.match(rf"^\s*func\s+{re.escape(source_func)}\s*\(.*?\):", ln):
                    source_sig_idx = i
                    break

            if source_sig_idx == -1:
                log.append(
                    f"ERROR: Source function '{source_func}' not found in '{source_path}'. Skipping create."
                )
                return log
            source_body_range = get_function_block(source_lines, source_func)
            end_idx = source_body_range[1] if source_body_range else source_sig_idx

            # Get the entire function block from the source
            new_func_block = source_lines[source_sig_idx : end_idx + 1]

            # Rename the function in the signature line to match the target
            if source_func != target_func:
                pat = re.compile(rf"(^\s*func\s+){re.escape(source_func)}(\s*\(.*?\):)")
                new_func_block[0] = pat.sub(
                    rf"\1{target_func}\2", new_func_block[0], count=1
                )

            # Append the new function to the end of the file
            new_lines_create = target_lines + ["\n"] + new_func_block

            _write_target(
                game_dir, resolve_res_path(target_res), "".join(new_lines_create), vfs
            )
            log.append(
                f"SUCCESS: Created new function '{target_func}' in '{target_res}'."
            )
            return log

        # Original logic for replace/prefix/postfix
        if not target_func:
            log.append(
                f"ERROR: {effective_mode.upper()} mode requires a target function name. Skipping."
            )
            return log

        target_sig_line_index = -1
        for i, ln in enumerate(target_lines):
            if re.match(rf"^\s*func\s+{re.escape(target_func)}\s*\(.*?\):", ln):
                target_sig_line_index = i
                break

        # This check is only an error for non-create modes
        if target_sig_line_index == -1:
            log.append(
                f"ERROR: Target function '{target_func}' not found in '{target_res}'. Skipping."
            )
            return log

        target_body_range = get_function_block(target_lines, target_func)
        source_body_range = get_function_block(source_lines, source_func)
        new_lines: List[str] = []

        if effective_mode == "replace":
            source_sig_idx = -1
            for i, ln in enumerate(source_lines):
                if re.match(rf"^\s*func\s+{re.escape(source_func)}\s*\(.*?\):", ln):
                    source_sig_idx = i
                    break

            if source_sig_idx == -1:
                log.append(
                    f"ERROR: Source function '{source_func}' not found for replace. Skipping."
                )
                return log

            start_idx = target_sig_line_index
            end_idx = target_body_range[1] if target_body_range else start_idx

            source_end_idx = (
                source_body_range[1] if source_body_range else source_sig_idx
            )
            patch_content = source_lines[source_sig_idx : source_end_idx + 1]

            # Rename if necessary
            if source_func != target_func:
                pat = re.compile(rf"(^\s*func\s+){re.escape(source_func)}(\s*\(.*?\):)")
                patch_content[0] = pat.sub(
                    rf"\1{target_func}\2", patch_content[0], count=1
                )

            new_lines = (
                target_lines[:start_idx] + patch_content + target_lines[end_idx + 1 :]
            )

        else:  # prefix or postfix
            if not target_body_range:
                log.append(
                    f"ERROR: Original function body for '{target_func}' is empty. Cannot wrap."
                )
                return log
            if not source_body_range:
                log.append(
                    f"ERROR: Patch function body for '{source_func}' is empty. Cannot wrap."
                )
                return log

            original_body_lines = target_lines[
                target_body_range[0] : target_body_range[1] + 1
            ]
            patch_body_lines = source_lines[
                source_body_range[0] : source_body_range[1] + 1
            ]

            # Use the robust logic to determine the body's actual indentation level
            original_body_lines_filtered = [
                line
                for line in original_body_lines
                if line.strip() and not line.lstrip().startswith("#")
            ]
            if original_body_lines_filtered:
                # Use the indentation of the first significant line of the original body
                body_indent = _leading_whitespace(original_body_lines_filtered[0])
            else:
                # Fallback: Find signature indentation and add a standard level (\t or 4 spaces)
                sig_indent = _leading_whitespace(target_lines[target_sig_line_index])
                if "\t" in sig_indent:
                    body_indent = sig_indent + "\t"
                else:
                    body_indent = sig_indent + "    "  # Note: Using regular spaces here
            body_has_markers = any(
                "#--- ORIGINAL FUNCTION BODY ---" in line
                for line in original_body_lines
            )
            display_name = f"[{mod_name}] " if mod_name else ""
            wrapper_body: list[str]
            if body_has_markers:
                if effective_mode == "postfix":
                    end_postfix_idx = -1
                    for idx, line in enumerate(original_body_lines):
                        if "#--- END POSTFIX PATCH ---" in line:
                            end_postfix_idx = idx
                            break
                    if end_postfix_idx != -1:
                        patch_entry = [
                            f"{body_indent}#--- GMOS POSTFIX: {display_name}{Path(source_path).name}::{source_func} ---\n"
                        ] + patch_body_lines
                        original_body_lines[end_postfix_idx:end_postfix_idx] = (
                            patch_entry
                        )
                        wrapper_body = original_body_lines
                    else:
                        wrapper_body = (
                            original_body_lines
                            + [
                                f"{body_indent}#--- GMOS POSTFIX: {display_name}{Path(source_path).name}::{source_func} ---\n"
                            ]
                            + patch_body_lines
                        )
                else:  # prefix
                    start_prefix_idx = -1
                    for idx, line in enumerate(original_body_lines):
                        if "#--- START PREFIX PATCH" in line:
                            start_prefix_idx = idx
                            break
                    if start_prefix_idx != -1:
                        patch_entry = [
                            f"{body_indent}#--- GMOS PREFIX: {display_name}{Path(source_path).name}::{source_func} ---\n"
                        ] + patch_body_lines
                        original_body_lines[
                            start_prefix_idx + 1 : start_prefix_idx + 1
                        ] = patch_entry
                        wrapper_body = original_body_lines
                    else:
                        wrapper_body = (
                            [
                                f"{body_indent}#--- GMOS PREFIX: {display_name}{Path(source_path).name}::{source_func} ---\n"
                            ]
                            + patch_body_lines
                            + original_body_lines
                        )
            else:
                wrapper_body = []
                if effective_mode == "prefix":
                    wrapper_body.append(
                        f"{body_indent}#--- START PREFIX PATCH: {display_name}{Path(source_path).name}::{source_func} ---\n"
                    )
                    wrapper_body.extend(patch_body_lines)
                    wrapper_body.append(
                        f"{body_indent}#--- ORIGINAL FUNCTION BODY ---\n"
                    )
                    wrapper_body.extend(original_body_lines)
                    wrapper_body.append(f"{body_indent}#--- END PREFIX PATCH ---\n")
                else:  # postfix
                    wrapper_body.append(
                        f"{body_indent}#--- ORIGINAL FUNCTION BODY ---\n"
                    )
                    wrapper_body.extend(original_body_lines)
                    wrapper_body.append(
                        f"{body_indent}#--- START POSTFIX PATCH: {display_name}{Path(source_path).name}::{source_func} ---\n"
                    )
                    wrapper_body.extend(patch_body_lines)
                    wrapper_body.append(f"{body_indent}#--- END POSTFIX PATCH ---\n")

            start_idx = target_sig_line_index + 1
            end_idx = target_body_range[1]
            new_lines = (
                target_lines[:start_idx] + wrapper_body + target_lines[end_idx + 1 :]
            )

        # Ensure file is always written for replace/prefix/postfix ---
        if new_lines:
            _write_target(
                game_dir, resolve_res_path(target_res), "".join(new_lines), vfs
            )
            log.append(
                f"SUCCESS: Function '{target_func}' patched with {effective_mode.upper()} in '{target_res}'."
            )

        return log

    except FileNotFoundError as e:
        log.append(f"ERROR: File not found during FunctionPatch: {e}")
        return log
    except OSError as e:
        log.append(f"ERROR: I/O error during FunctionPatch: {e}")
        return log
    except Exception as e:
        log.append(f"FATAL ERROR during FunctionPatch: {e}")
        return log


def read_source_for_patching(
    game_dir: str,
    target_res: str,
    vfs: Optional[Dict[str, bytes]] = None,
    pck_pool: Optional[List[PCKReader]] = None,
) -> str:
    """
    Reads source code for a target resource.
    Priority:
    1. In-memory VFS (if file was already modified).
    2. Loose file in game_dir
    3. Content extracted from any .pck file in game_dir (Vanilla) via optimized pool.
    """
    rel_path = resolve_res_path(target_res)

    # Try VFS
    if vfs is not None and rel_path in vfs:
        return vfs[rel_path].decode("utf-8", errors="ignore")
    # Try loose file
    work_path = os.path.join(game_dir, rel_path)
    if os.path.exists(work_path):
        return Path(work_path).read_text(encoding="utf-8", errors="ignore")

    # Try PCK files from pool
    if pck_pool:
        for reader in pck_pool:
            try:
                content_bytes = reader.read_file(target_res)
                if content_bytes:
                    # If using VFS (PCK mode), cache it there.
                    # If loose mode, write to disk so subsequent patches have a base.
                    if vfs is not None:
                        vfs[rel_path] = content_bytes
                    else:
                        ensure_within(game_dir, work_path)
                        os.makedirs(os.path.dirname(work_path), exist_ok=True)
                        atomic_write_bytes(work_path, content_bytes)
                    return content_bytes.decode("utf-8", errors="ignore")
            except Exception as e:
                logger.debug("Failed read from PCK pool: %s", e)

    raise FileNotFoundError(
        f"Target resource '{target_res}' not found in game dir or any .pck archive."
    )


def apply_policy_to_plan(
    plan: List[Tuple[str, str, Tuple[Any, ...]]], game_dir: str
) -> List[Tuple[str, str, Tuple[Any, ...]]]:
    """
    Filters the patch plan based on persistent file rules.
    If a rule exists for a target resource (e.g. 'res://icon.png': 'Mod A'),
    any conflicting operations from other mods for that target are dropped.
    """
    rules = policy.load_file_rules(game_dir=game_dir)
    if not rules:
        return plan

    filtered_plan: List[Tuple[str, str, Tuple[Any, ...]]] = []

    # Helper to extract target resource from instruction details
    def _get_target(op: str, det: Tuple[Any, ...]) -> Optional[str]:
        try:
            if op in ("FileReplace", "VariablePatch", "FunctionPatch"):
                return cast(str, det[0])
            return None
        except IndexError:
            return None

    # Pre-calculate active mods per target to verify if a rule is stale
    active_mods_per_target: Dict[str, Set[str]] = defaultdict(set)
    for mod_name, op, details in plan:
        target_res = _get_target(op, details)
        if target_res:
            active_mods_per_target[resolve_res_path(target_res)].add(mod_name)
    for mod_name, op, details in plan:
        target_res = _get_target(op, details)
        if not target_res:
            filtered_plan.append((mod_name, op, details))
            continue

        # Normalize path for lookup
        norm_target = resolve_res_path(target_res)
        winner = rules.get(norm_target)

        # If a winner is defined and this mod isn't it, check if we should drop it.
        # Policy strictly enforces: If a winner is set, they own the file for destructive ops.
        # We drop ops from non-winners ONLY if the winner is actively participating in this patch run.
        if winner and winner in active_mods_per_target[norm_target]:
            if winner != mod_name:
                logger.info(
                    "Policy enforcement: Dropping %s on %s from %s (Winner is %s)",
                    op,
                    target_res,
                    mod_name,
                    winner,
                )
                continue
        filtered_plan.append((mod_name, op, details))

    return filtered_plan


def _write_target(
    game_dir: str,
    rel_path: str,
    content: Union[str, bytes],
    vfs: Optional[Dict[str, bytes]] = None,
) -> None:
    """
    Helper to write data either to disk (cache path) or to VFS memory.
    """
    if isinstance(content, str):
        # Auto-Sanitization for GDScript
        if rel_path.endswith(".gd"):
            content = sanitize_script_content(content, rel_path)
        data_bytes = content.encode("utf-8")
    else:
        data_bytes = content

    if vfs is not None:
        vfs[rel_path] = data_bytes
    else:
        # Write to disk
        work_path = os.path.join(game_dir, rel_path)
        os.makedirs(os.path.dirname(work_path), exist_ok=True)
        if isinstance(content, str):
            atomic_write_with_backup(work_path, content)
        else:
            atomic_write_bytes(work_path, data_bytes)


def revert_to_vanilla(game_dir: str) -> List[str]:
    """
    Restores the game directory to its vanilla state using .bak files
    and the previous runtime_manifest.json.
    """
    log: List[str] = []
    manifest_path = os.path.join(game_dir, "runtime_manifest.json")
    modified_files: Set[str] = set()

    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest: Dict[str, Any] = json.load(f)

            pck_name = manifest.get("target_pck")
            if pck_name:
                pck_path = os.path.join(game_dir, pck_name)
                if os.path.exists(pck_path):
                    try:
                        os.remove(pck_path)
                        log.append(f"Removed override PCK: {pck_name}")
                    except Exception as e:
                        log.append(f"ERROR removing {pck_name}: {e}")

            for f_path in manifest.get("modified_files", []):
                modified_files.add(f_path)
        except Exception as e:
            log.append(f"ERROR reading manifest: {e}")
    else:
        log.append(
            "No previous runtime manifest found. Scanning for .bak files as fallback..."
        )

    # Always scan for orphaned .bak files to ensure a clean state
    for root, dirs, files in os.walk(game_dir):
        # Skip internal directories
        for ignore_dir in [".godot", ".import", "gmos_data", "mods", "profiles"]:
            if ignore_dir in dirs:
                dirs.remove(ignore_dir)

        for fn in files:
            if fn.endswith(".bak"):
                full_bak = os.path.join(root, fn)
                full_orig = full_bak[:-4]
                rel_orig = os.path.relpath(full_orig, game_dir).replace("\\", "/")
                modified_files.add(rel_orig)

    log.append(f"Reverting {len(modified_files)} files to vanilla...")

    for rel_path in modified_files:
        file_path = os.path.join(game_dir, rel_path)
        bak_path = file_path + ".bak"

        # If it's a symlink, unlink it first to make room for restoration
        if os.path.islink(file_path):
            try:
                os.unlink(file_path)
            except OSError as e:
                log.append(f"ERROR unlinking {rel_path}: {e}")

        if os.path.exists(bak_path):
            # Restore backup (COPY back to preserve backup for next time)
            try:
                atomic_write_copy(bak_path, file_path)
                log.append(f"Restored: {rel_path}")
            except Exception as e:
                log.append(f"ERROR restoring {rel_path}: {e}")
        elif os.path.exists(file_path) and not os.path.islink(file_path):
            # File created by mod (no backup available)
            try:
                os.remove(file_path)
                log.append(f"Removed mod-added file: {rel_path}")
            except Exception as e:
                log.append(f"ERROR removing mod-added file {rel_path}: {e}")

    return log


def patch_variable(
    game_dir: str,
    target_res: str,
    target_var: str,
    source_path: str,
    source_var: str,
    mode: str,
    vfs: Optional[Dict[str, bytes]] = None,
    pck_pool: Optional[List[PCKReader]] = None,
) -> List[str]:
    """Patches a variable in the target script."""
    log: List[str] = []
    try:
        work_path, copy_log = lazy_copy_file(game_dir, target_res)
        log.append(copy_log)
        ensure_within(game_dir, work_path)

        try:
            target_text = read_source_for_patching(game_dir, target_res, vfs, pck_pool)
            target_lines = target_text.splitlines(keepends=True)
        except Exception as e:
            log.append(f"ERROR: Failed to read target file '{work_path}': {e}")
            return log

        with open(source_path, "r", encoding="utf-8") as f:
            source_lines = f.readlines()

        src_range = get_var_block(source_lines, source_var)
        tgt_range = get_var_block(target_lines, target_var)

        if not src_range:
            log.append(
                f"ERROR: Source var '{source_var}' not found in '{source_path}'."
            )
            return log

        src_block = source_lines[src_range[0] : src_range[1] + 1]

        if mode == "replace":
            if not tgt_range:
                log.append(
                    f"ERROR: Target var '{target_var}' not found for replace in {target_res}. Skipping."
                )
                return log
            if src_block and source_var != target_var:
                pat = re.compile(
                    rf"(^\s*(var|const)\s+){re.escape(source_var)}(\s*[:=])"
                )
                src_block[0] = pat.sub(rf"\1{target_var}\3", src_block[0], count=1)

            new_lines = (
                target_lines[: tgt_range[0]]
                + src_block
                + target_lines[tgt_range[1] + 1 :]
            )
            _write_target(
                game_dir, resolve_res_path(target_res), "".join(new_lines), vfs
            )
            log.append(f"SUCCESS: Replaced var '{target_var}' in {target_res}.")
            return log

        if mode == "add":
            if not tgt_range:
                log.append(
                    f"ERROR: Target var '{target_var}' not present; 'add' requires existing var. Skipping."
                )
                return log
            inner = src_block[1:-1] if len(src_block) > 2 else []
            if not inner:
                log.append(f"NOTICE: No inner lines to append from '{source_var}'.")
                return log
            insert_at = tgt_range[1]
            new_lines = target_lines[:insert_at] + inner + target_lines[insert_at:]
            _write_target(
                game_dir, resolve_res_path(target_res), "".join(new_lines), vfs
            )
            log.append(
                f"SUCCESS: Appended {len(inner)} lines into '{target_var}' in {target_res}."
            )
            return log

        if mode == "create":
            pass

        return log
    except Exception as e:
        log.append(f"ERROR: Variable patch failed: {e}")
        return log


def apply_patches_to_file(
    game_dir: str,
    target_res: str,
    operations: List[Tuple[str, str, Any]],
    pck_pool: List[PCKReader],
    conflict_delegate: Optional[ConflictDelegate],
    is_packed: bool = False,
) -> List[str]:
    """
    Worker function: Applies a sequence of operations to a SINGLE file.
    Runs in a thread.
    Instead of writing directly to game_dir, this worker now:
    1. Builds the file in memory (vfs dict).
    2. If modified (Function/Variable/Smart patches), writes the result to GMOS_CACHE_DIR.
    3. If 1:1 replacement (FileReplace), records the source path for symlinking.
    """
    log: list[str] = []
    rel_path = resolve_res_path(target_res)
    dest_path = os.path.join(game_dir, rel_path)
    cache_path = os.path.join(game_dir, GMOS_CACHE_DIR, rel_path)

    # Track the 'Winning' physical source file.
    # If this remains set at the end, we symlink directly to it.
    # If vfs is populated, we write vfs to cache and symlink to cache.
    direct_symlink_source: Optional[str] = None
    # In-memory VFS for this file's thread context
    vfs: Dict[str, bytes] = {}

    try:
        ensure_within(game_dir, dest_path)

        for mod_name, op_type, details in operations:
            if op_type == "FileReplace":
                source_path = details[1]
                # Read source
                try:
                    src_content = Path(source_path).read_bytes()
                    # Sanitize
                    if dest_path.endswith(".gd"):
                        text = src_content.decode("utf-8", errors="ignore")
                        text = sanitize_script_content(text, dest_path)
                        src_content = text.encode("utf-8")

                    # CONFLICT CHECK: Does vfs already have content for this file?
                    # If yes, a previous mod (or vanilla) is being overwritten.
                    if rel_path in vfs and conflict_delegate:
                        # We have a collision. Try to resolve via text merge.
                        try:
                            existing_text = vfs[rel_path].decode(
                                "utf-8", errors="strict"
                            )
                            new_text = src_content.decode("utf-8", errors="strict")

                            # Only trigger UI if content actually differs
                            if existing_text != new_text:
                                merged = conflict_delegate.resolve(
                                    dest_path, existing_text, new_text
                                )
                                if merged is not None:
                                    src_content = merged.encode("utf-8")
                                    log.append(
                                        f"[{mod_name}] Resolved conflict in {target_res}"
                                    )
                                    # It's a synthetic merge now, cannot use direct link
                                    direct_symlink_source = None
                                else:
                                    log.append(
                                        f"[{mod_name}] Conflict skipped/cancelled for {target_res}"
                                    )
                        except UnicodeError:
                            # Binary file conflict - Last Mod Wins (Default)
                            direct_symlink_source = source_path
                    else:
                        # No conflict or first op
                        direct_symlink_source = source_path

                    vfs[rel_path] = src_content
                    log.append(str(f"[{mod_name}] Resolved conflict in {target_res}"))
                except Exception as e:
                    log.append(f"ERROR [{mod_name}]: FileReplace failed: {e}")

            elif op_type == "BinaryPatch":
                # Binary patch implies modification, cannot direct symlink
                direct_symlink_source = None
                if not _bsdiff_found:
                    log.append(
                        f"ERROR [{mod_name}]: BinaryPatch skipped. 'bsdiff4' module not installed."
                    )
                    continue

                patch_src = details[1]
                try:
                    # 1. Get Base Content (from VFS, Disk, or PCK)
                    base_bytes: Optional[bytes] = None
                    if rel_path in vfs:
                        base_bytes = vfs[rel_path]
                    elif os.path.exists(dest_path):
                        base_bytes = Path(dest_path).read_bytes()
                    else:
                        # Try finding in PCK pool
                        for reader in pck_pool:
                            base_bytes = reader.read_file(target_res)
                            if base_bytes:
                                break

                    if base_bytes is None:
                        log.append(
                            f"ERROR [{mod_name}]: BinaryPatch failed. Base file {target_res} not found."
                        )
                        continue

                    # 2. Apply Patch
                    patch_bytes = Path(patch_src).read_bytes()
                    new_bytes: bytes = cast(Any, bsdiff4).patch(base_bytes, patch_bytes)
                    vfs[rel_path] = new_bytes
                    log.append(
                        str(
                            f"[{mod_name}] Applied BinaryPatch ({len(patch_bytes)} bytes) to {target_res}"
                        )
                    )

                except Exception as e:
                    log.append(f"ERROR [{mod_name}]: BinaryPatch execution failed: {e}")

            elif op_type == "SmartPatch":
                direct_symlink_source = None
                # details: (t_res, t_name, s_path, inject_at, anchor)
                t_res = cast(str, details[0])
                t_name = cast(str, details[1])
                s_path = cast(str, details[2])
                inject_at = cast(Optional[str], details[3])
                anchor = cast(Optional[str], details[4]) if len(details) > 4 else None

                lines = patch_smart_inject(
                    game_dir,
                    t_res,
                    t_name,
                    s_path,
                    inject_at,
                    anchor,
                    vfs=vfs,
                    pck_pool=pck_pool,
                )
                log.extend(lines)

            elif op_type == "FunctionPatch":
                direct_symlink_source = None
                t_res = cast(str, details[0])
                t_func = cast(Optional[str], details[1])
                s_path = cast(str, details[2])
                s_func = cast(str, details[3])
                mode = cast(Optional[str], details[4])

                lines = patch_function(
                    game_dir,
                    t_res,
                    t_func,
                    s_path,
                    s_func,
                    mode=mode,
                    mod_name=mod_name,
                    vfs=vfs,
                    conflict_delegate=conflict_delegate,
                    pck_pool=pck_pool,
                )
                log.extend(lines)

            elif op_type == "VariablePatch":
                direct_symlink_source = None
                t_res = cast(str, details[0])
                t_var = cast(str, details[1])
                s_path = cast(str, details[2])
                s_var = cast(str, details[3])
                mode = cast(str, details[4])

                lines = patch_variable(
                    game_dir,
                    t_res,
                    t_var,
                    s_path,
                    s_var,
                    mode=mode,
                    vfs=vfs,
                    pck_pool=pck_pool,
                )
                log.extend(lines)

        # FINAL DEPLOYMENT STEP
        sym_mgr = SymlinkManager(game_dir)

        # Case A: Native Dynamic Libraries OR Loose File Mode MUST be physically deployed via symlinks
        if rel_path.lower().endswith(NATIVE_BIN_EXTENSIONS) or not is_packed:
            if rel_path in vfs:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                atomic_write_bytes(cache_path, vfs[rel_path])
                sym_mgr.deploy(rel_path, cache_path)
            elif direct_symlink_source:
                sym_mgr.deploy(rel_path, direct_symlink_source)

            if not is_packed:
                log.append(f"Deploying loose file symlink: {rel_path}")
            else:
                log.append(f"Deploying native library symlink: {rel_path}")
            return log

        # Case B: Standard Godot Resources (Packed Mode) -> Kept in VFS for PCK compilation
        if rel_path in vfs:
            # Write the result to the Cache
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            atomic_write_bytes(cache_path, vfs[rel_path])

    except Exception as e:
        log.append(f"CRITICAL ERROR patching {target_res}: {e}")
        logger.exception("Thread worker failed")

    return log


def run_patcher(
    game_dir: str,
    patch_plan: List[Tuple[str, str, Tuple[Any, ...]]],
    conflict_delegate: Optional[ConflictDelegate] = None,
    game_executable: str = "game.exe",
    is_packed: bool = False,
) -> List[str]:
    """
    Executes the patch plan using DAG-based Parallel Scheduling
    Independent files are processed concurrently.
    """
    log: List[str] = []
    start_time = time.time()

    # 1. Revert to Vanilla
    log.extend(revert_to_vanilla(game_dir))

    # 2. Group Operations (DAG Building)
    # We group by 'target_res' because operations on the same file MUST be sequential (Last Mod Wins),
    # but operations on different files can happen at the same time.
    ops_by_file: Dict[str, List[Tuple[str, str, Any]]] = defaultdict(list)

    for item in patch_plan:
        # Item structure: (mod_name, op, details)
        _, op, details = item
        target_res = ""

        # Extract target resource path based on op type
        if op in ("FileReplace", "BinaryPatch"):
            if details:
                target_res = details[0]
        elif op in ("FunctionPatch", "VariablePatch", "SmartPatch"):
            if details:
                target_res = details[0]

        if target_res:
            ops_by_file[target_res].append(item)

    log.append(f"Planned {len(patch_plan)} operations across {len(ops_by_file)} files.")

    # 3. Parallel Execution Phase
    pause_game_dir_watcher()  # Prevent UI from reacting to intermediate file churn

    # Ensure Cache Directory Exists
    os.makedirs(os.path.join(game_dir, GMOS_CACHE_DIR), exist_ok=True)
    try:
        with ExitStack() as stack:
            # Initialize PCK Pool (Thread-safe read-only access to vanilla files)
            pck_pool: List[PCKReader] = []
            try:
                with os.scandir(game_dir) as it:
                    for entry in it:
                        if entry.is_file() and entry.name.endswith(".pck"):
                            reader = stack.enter_context(PCKReader(entry.path))
                            pck_pool.append(reader)
            except Exception as e:
                log.append(f"Warning: Failed to initialize PCK pool: {e}")

            # Get the shared thread pool
            executor = get_io_executor()
            futures: Set[Future[Any]] = set()

            def collect_results(done_futures: Set[Future[Any]]) -> None:
                for f in done_futures:
                    try:
                        res_log = f.result()
                        log.extend(res_log)
                    except Exception as e:
                        log.append(f"CRITICAL WORKER ERROR: {e}")

            # Submit tasks
            for target_res, ops in ops_by_file.items():
                # Bounded Submission: Don't flood the queue if we have thousands of files
                if len(futures) >= (executor._max_workers * 2):
                    done, futures = wait(futures, return_when=FIRST_COMPLETED)
                    collect_results(done)

                # Dispatch the file worker
                fut = executor.submit(
                    apply_patches_to_file,
                    game_dir,
                    target_res,
                    ops,
                    pck_pool,
                    conflict_delegate,
                    is_packed,
                )
                futures.add(fut)

            # Wait for all remaining tasks
            if futures:
                done, _ = wait(futures)
                collect_results(done)
            target_pck_name = None
            if is_packed:
                # 3.5 Build GMOS Override PCK Archive
                target_pck_name = "gmos_override.pck"
                log.append(f"Building native override pack {target_pck_name}...")
                files_to_pack: Dict[str, Union[str, Path]] = {}

                for target_res, ops in ops_by_file.items():
                    rel_path = resolve_res_path(target_res)
                    # Skip native OS binaries from PCK
                    if rel_path.lower().endswith(NATIVE_BIN_EXTENSIONS):
                        continue

                    cache_path = os.path.join(game_dir, GMOS_CACHE_DIR, rel_path)
                    if os.path.exists(cache_path):
                        files_to_pack[target_res] = cache_path
                    else:
                        for _mod_name, op, details in reversed(ops):
                            if op == "FileReplace":
                                src = details[1]
                                if os.path.exists(src):
                                    files_to_pack[target_res] = src
                                    break

                if files_to_pack:
                    target_pck_path = os.path.join(game_dir, target_pck_name)
                    try:
                        pack_pck(target_pck_path, files_to_pack)
                        log.append(
                            f"Successfully packed {len(files_to_pack)} files into {target_pck_name}"
                        )
                    except Exception as e:
                        log.append(f"ERROR building {target_pck_name}: {e}")
        # 4. Persistence: Save Runtime Manifest (Required for Revert)
        # We gather all keys from ops_by_file as the list of modified resources
        modified_list = list(ops_by_file.keys())
        # Build applied_ops for debugging reference
        applied_ops: List[Dict[str, Any]] = []
        for item in patch_plan:
            mod_name, op, details = item
            record: Dict[str, Any] = {"mod": mod_name, "op": op}
            try:
                if op in ("FileReplace", "BinaryPatch"):
                    record["target"] = details[0]
                    record["source"] = details[1]
                elif op == "FunctionPatch":
                    record["target"] = (
                        f"{details[0]}::{details[1]}" if details[1] else details[0]
                    )
                    record["source"] = f"{details[2]}::{details[3]}"
                    record["mode"] = details[4] or ""
                elif op == "VariablePatch":
                    record["target"] = f"{details[0]}::{details[1]}"
                    record["source"] = f"{details[2]}::{details[3]}"
                    record["mode"] = details[4]
                elif op == "SmartPatch":
                    record["target"] = f"{details[0]}::{details[1]}"
                    record["source"] = details[2]
                    record["inject_at"] = details[3]
                    record["anchor"] = details[4] if len(details) > 4 else None
            except Exception as e:
                record["status"] = "error"
                record["notes"] = str(e)
            applied_ops.append(record)
        manifest: Dict[str, Any] = {
            "timestamp": datetime.datetime.now().isoformat(),
            "game_dir": game_dir,
            "target_pck": target_pck_name,
            "modified_files": modified_list,
            "applied_ops_count": len(patch_plan),
            "applied_ops": applied_ops,
        }
        try:
            manifest_path = os.path.join(game_dir, "runtime_manifest.json")
            atomic_replace(manifest_path, json.dumps(manifest, indent=2))
        except Exception as e:
            log.append(f"ERROR saving runtime manifest: {e}")
    finally:
        resume_game_dir_watcher()

    elapsed = time.time() - start_time
    log.append(f"Patching finished in {elapsed:.2f}s")
    return log
