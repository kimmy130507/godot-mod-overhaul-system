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
import configparser
import contextlib
import datetime
import difflib
import filecmp
import hashlib
import json
import os
import re
import threading
import time
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
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

# --- Clean, Module-Level Imports ---
from gmos.io import (
    atomic_replace,
    atomic_write_bytes,
    atomic_write_copy,
    atomic_write_with_backup,
)
from gmos.io import pck as pck_tools
from gmos.io import safe_atomic_copy_with_bak, safe_remove, safe_write_text
from gmos.io.locking import pause_workroot_watcher, resume_workroot_watcher
from gmos.state import policy
from gmos.utils import _get_mod_name_from_config  # type: ignore [reportPrivateUsage]
from gmos.utils import ModConfig, logger


class ConflictDelegate(Protocol):
    """Interface for resolving file conflicts (UI or Headless)."""

    def resolve(self, file_path: str, orig_text: str, new_text: str) -> Optional[str]:
        # This is an interface. Implementation belongs in UI layer.
        ...


# --- Constants ---
_UNIFIED_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


# --- I/O helpers / caching for patch runs ---
# Avoid loading large files into memory unnecessarily during conflict checks.
# - _small_file_limit: files <= this are safe to read into memory for textual diffs.
# - For larger files we compute chunked sha256 and treat differing large files as binary
#   (skip interactive textual merge) which avoids high memory usage and long blocking reads.
# _small_file_limit can be tuned via environment for CI/headless vs desktop usage.
_small_file_limit = int(os.environ.get("GMOS_SMALL_FILE_LIMIT", str(5 * 1024 * 1024)))
_HASH_CHUNK = 1024 * 1024  # 1 MiB


def _sha256_file(path: str) -> str:
    """Compute SHA256 hex digest of file using a streaming read."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


@lru_cache(maxsize=1024)
def _read_text_cached(path: str) -> str:
    """Read text file into memory (errors ignored). Cached for reuse during one patch run."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


# --- Cache control helpers (public) ---
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
        pause_workroot_watcher()
        time.sleep(0.02)
        atomic_replace(target_path, text)
    finally:
        resume_workroot_watcher()
    clear_read_cache()


def write_atomic_write_copy(src: str, dst: str) -> None:
    """Atomic copy (src -> dst) then clear read cache."""
    atomic_write_copy(src, dst)
    clear_read_cache()


def write_atomic_write_with_backup(target_path: str, new_text: str) -> None:
    """Atomic write with single bak, then clear read cache."""
    try:
        pause_workroot_watcher()
        # tiny cooperative delay so other threads/processes can release handles
        time.sleep(0.02)
        atomic_write_with_backup(target_path, new_text)
    finally:
        resume_workroot_watcher()
    clear_read_cache()


def write_safe_atomic_copy_with_bak(
    src: str, dst: str, *args: Any, **kwargs: Any
) -> None:
    """Safe wrapper that delegates to io.safe_atomic_copy_with_bak and clears cache."""
    try:
        safe_atomic_copy_with_bak(src, dst, *args, **kwargs)
    finally:
        clear_read_cache()


# --- Patch-run context manager and compatibility aliases ---
# Provide a context manager that callers can use to ensure caches are cleared
# before and after a patch/apply operation. Also expose wrapper aliases so
# existing callsites can keep using the old function names without source changes.
@contextlib.contextmanager
def patch_run_context() -> Iterator[None]:
    """
    Context manager to wrap a full patch/apply run.

    - Clears the small-file read cache on enter (so checks read fresh content).
    - Clears the cache again on exit to ensure subsequent operations see current files.
    Usage:
        with patch_run_context():
            ... perform patch/preview/apply operations ...
    """
    clear_read_cache()
    try:
        yield
    finally:
        clear_read_cache()


# Provide a thin wrapper around safe_write_text to ensure cache invalidation
def write_safe_write_text(path: str, text: str) -> None:
    """Call through to io.safe_write_text (or earlier safe_write_text) then invalidate cache."""
    # safe_write_text is imported earlier from gmos.io; call it and clear cache.
    try:
        safe_write_text(path, text)
    finally:
        clear_read_cache()


# ensure pause/resume of the workroot watcher is always paired.
@contextmanager
def _pause_workroot_watcher_ctx() -> Iterator[None]:
    """Context manager that pauses the workroot watcher and always resumes it."""
    try:
        pause_workroot_watcher()
    except Exception:
        # If pausing fails, just warn and proceed; resume will be attempted later.
        logger.debug("pause_workroot_watcher() failed (continuing)", exc_info=True)
    try:
        yield
    finally:
        try:
            resume_workroot_watcher()
        except Exception:
            logger.debug("resume_workroot_watcher() failed", exc_info=True)


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
    applied_ops: List[Dict[str, str]]
    modified_files: List[str]


# --- Godot Path Helpers ---


def _res_to_path(res_path: str) -> str:
    """Convert a Godot `res://` resource path to a safe filesystem-relative path.

    Security rules:
    - Accepts strings beginning with 'res://' or plain relative paths.
    - Rejects any path that attempts to traverse above the resource root (leading '..').
    - Collapses '.' and '..' segments safely. Raises RuntimeError on invalid traversal.

    Returns a platform-native relative path (no leading slash).
    """
    if not res_path:
        return ""

    # strip prefix if present
    if res_path.startswith("res://"):
        rel = res_path[len("res://") :]
    else:
        rel = res_path

    # unify separators and drop leading slashes
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


def sanitize_script_content(content: str) -> str:
    """
    Active Security: Rewrites GDScript content to intercept dangerous calls.
    Redirects OS.* calls to the GMOS_Sandbox singleton.
    """
    # 1. Intercept OS.execute (ACE vector)
    # Matches "OS.execute(" with flexible whitespace
    # Replacement assumes GMOS_Sandbox autoload is present (injected by gmos.core.injection)
    content = re.sub(r"\bOS\.execute\s*\(", "GMOS_Sandbox.secure_execute(", content)

    # 2. Intercept OS.shell_open (Phishing/Malware download vector)
    content = re.sub(
        r"\bOS\.shell_open\s*\(", "GMOS_Sandbox.secure_shell_open(", content
    )
    return content


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

    base_str = str(base_p)
    target_str = str(target_p)

    # ensure trailing separator on base for prefix check
    if not base_str.endswith(os.sep):
        base_str = base_str + os.sep

    if not target_str.startswith(base_str):
        raise RuntimeError(f"Path escape detected. base={base_p} target={target_p}")

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

    Returns (ordered_mod_configs, errors_dict)
    - ordered_mod_configs: list in load order (deps first). If cycles exist the
      returned list will be partial (nodes outside cycles).
    - errors_dict: mapping mod_name -> list[str] of error messages (missing deps or cycle)
    """
    # map name -> config
    name_to_cfg: Dict[str, ModConfig] = {}
    # Preserve input order for stable cycle breaking
    name_priority: Dict[str, int] = {}
    for i, cfg in enumerate(mod_configs):
        name = _get_mod_name_from_config(cfg)
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
            # Heuristic: Break cycle by picking the remaining node with highest priority
            # (lowest index in user list) and forcing it to load.
            remaining = [n for n in name_to_cfg if n not in order]
            if not remaining:
                break  # Should not happen

            # Sort by user list order (0 is top/first)
            remaining.sort(key=lambda n: name_priority.get(n, 9999))
            forced_node = remaining[0]

            # Force load and log warning
            queue.append(forced_node)
            # Fake decrement to 'satisfy' deps for neighbors in the cycle
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
        name = _get_mod_name_from_config(cfg)
        cfg["_resolved_order_rank"] = rank
        rank += 1

    # attach errors from resolver to original configs
    for name, errs in errors.items():
        # find matching config by name and set _deps_errors
        for cfg in mod_configs:
            if _get_mod_name_from_config(cfg) == name:
                cfg["_deps_errors"] = list(errs)
                break

    # place unresolved configs (cycles/missing deps) after resolved ones, preserve relative order
    def _sort_key(cfg: ModConfig) -> Tuple[int, Union[str, int]]:
        r: Optional[int] = cfg.get("_resolved_order_rank")
        if r is None:
            # place after resolved; keep stable order by mod folder name
            return (1, _get_mod_name_from_config(cfg).lower())
        return (0, r)

    ordered_all = sorted(mod_configs, key=_sort_key)
    return ordered_all, errors


# --- Core Patcher Logic ---


def _leading_whitespace(line: str) -> str:
    """Returns the leading whitespace of a line."""
    return line[: len(line) - len(line.lstrip("\t "))]


def _is_line_comment(line: str) -> bool:
    """Checks if a line is entirely a comment (after leading whitespace)."""
    return line.lstrip().startswith("#")


def get_var_block(lines: List[str], var_name: str) -> Optional[Tuple[int, int]]:
    """
    Return (start, end) indices of a var or const block named var_name in lines.
    """
    # Updated regex to match 'var' or 'const'
    pat = re.compile(rf"^\s*(var|const)\s+{re.escape(var_name)}\s*=\s*")
    start = -1
    for i, ln in enumerate(lines):
        if pat.match(ln):
            start = i
            break
    if start == -1:
        return None

    # Balanced counts for common grouping tokens.
    balance = {"{": 0, "[": 0, "(": 0}
    opening = {"{": "}", "[": "]", "(": ")"}
    closing = {v: k for k, v in opening.items()}

    # Track string state. Can be one-char quote (' or ") or triple quotes (''' or """)
    in_string: Optional[str] = None
    escaped = False

    # Indentation of the var declaration line (helps detect end by dedent)
    var_indent = len(_leading_whitespace(lines[start]))

    # Whether the RHS has started (value may start on following lines)
    rhs_started = "=" in lines[start]
    i = start
    while i < len(lines):
        line = lines[i]

        # If assignment doesn't start on declaration line, wait for a non-empty, non-comment line.
        if not rhs_started and not line.strip().startswith("#") and line.strip():
            rhs_started = True

        if not rhs_started:
            i += 1
            continue

        # iterate characters with index to detect triple-quote sequences
        j = 0
        L = len(line)
        while j < L:
            ch = line[j]

            # If inside a string literal
            if in_string:
                # triple-quoted string termination
                if len(in_string) == 3:
                    if line[j : j + 3] == in_string:
                        in_string = None
                        j += 3
                        continue
                    else:
                        j += 1
                        continue
                else:
                    # single-quoted string: handle escapes
                    if escaped:
                        escaped = False
                        j += 1
                        continue
                    if ch == "\\":
                        escaped = True
                        j += 1
                        continue
                    if ch == in_string:
                        in_string = None
                        j += 1
                        continue
                    j += 1
                    continue

            # Not in a string: detect string starts (triple or single)
            if line[j : j + 3] in ('"""', "'''"):
                in_string = line[j : j + 3]
                j += 3
                continue
            if ch in ('"', "'"):
                in_string = ch
                j += 1
                continue

            # Track bracket/brace/paren balance
            if ch in balance:
                balance[ch] += 1
            elif ch in closing:
                balance[closing[ch]] -= 1
            j += 1

        # After scanning the line, if not in a string and balances are zero, candidate end
        if not in_string and all(v == 0 for v in balance.values()):
            # Look ahead for next significant line (skip blanks/comments)
            next_line = ""
            k = i + 1
            while k < len(lines):
                if lines[k].strip() == "" or lines[k].lstrip().startswith("#"):
                    k += 1
                    continue
                next_line = lines[k]
                break

            # If no next line, treat this as end.
            if not next_line:
                return start, i

            next_indent = len(_leading_whitespace(next_line))
            # If next significant line dedents to var level or less, end block here.
            if next_indent <= var_indent:
                # avoid ending if current line ends with comma (likely still in list/dict entries)
                if not line.rstrip().endswith(","):
                    return start, i

        i += 1

    # Exhausted file; return last scanned line as conservative fallback.
    return start, i - 1


def get_function_block(lines: List[str], func_name: str) -> Optional[Tuple[int, int]]:
    """
    Return (start, end) indices of a function body block named func_name in lines.
    Excludes the 'func ...' line and the final 'return' or 'pass' lines if they are not indented.
    """
    # allow optional return type between ')' and ':' (e.g. "-> void")
    pat = re.compile(
        rf"^\s*func\s+{re.escape(func_name)}\s*\(.*?\)\s*(?:->\s*[^:]+)?\s*:"
    )
    start = -1
    for i, ln in enumerate(lines):
        if pat.match(ln):
            start = i
            break
    if start == -1:
        return None

    # Function body starts one line after the signature
    body_start = start + 1
    # If the signature contains an inline body after the colon (e.g. `func foo(): return 1`)
    # treat the signature line itself as the function body.
    header_line = lines[start]
    if ":" in header_line:
        after = header_line.split(":", 1)[1]
        if after.strip() != "":
            # return the signature line as the (single-line) body
            return start, start

    # Find end of function block by checking indentation
    # Function body ends when indentation reverts to the level of the 'func' keyword
    func_indent = len(_leading_whitespace(lines[start]))
    end = len(lines) - 1

    for i in range(body_start, len(lines)):
        line = lines[i]
        stripped = line.lstrip()

        # Skip blank lines and full-line comments
        if not stripped or stripped.startswith("#"):
            continue

        # If indentation is less than or equal to the function signature's indentation,
        # and it's not a line immediately after the signature (where the first body line might be),
        # then the function has ended.
        line_indent = len(_leading_whitespace(line))
        if line_indent <= func_indent:
            end = i - 1
            break

        end = i

    # Clean up the end index: if the last line of the block is a comment or blank, back up
    while end >= body_start and (
        _is_line_comment(lines[end]) or not lines[end].strip()
    ):
        end -= 1

    # Return line numbers of function body (excluding signature line)
    return body_start, end


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
                # 1. Capture the section name in a local variable which Pylance knows is a 'str'.
                section_name = section_match.group(1).strip()
                config["Sections"][section_name] = []

                # 2. Update current_section for use later in the function.
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
    DataPatch and DataAdd are emitted as VariablePatch with mode='create'.
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
        # Disallow rename attempts on replace
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

    # DataAdd / DataPatch: treat as request to create a new top-level variable/table
    data_patch_content = sections.get("DataPatch", [])
    data_add_content = sections.get("DataAdd", [])
    combined_data_lines: List[str] = []
    combined_data_lines.extend(data_patch_content)
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


def _validate_mod_config_dict(mod_config: Dict[str, Any]) -> List[str]:
    """
    Validate the provided mod_config dict.
    Returns a list of error strings (empty if no errors).
    This is a helper used by validate_mod_config which dispatches based on
    whether the caller passed a .mos path (string) or a parsed dict.
    """
    # Helpers are now local
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
            "datapatch",
            "dataadd",
            "dependencies",
            "metadata",
        }
        for sec in raw_sections.keys():
            if str(sec).lower() not in allowed:
                errors.append(f"disallowed section: [{sec}]")

        def validate_resource_target(res_target: str) -> Optional[str]:
            try:
                _res_to_path(res_target)
                return None
            except Exception as e:
                return f"Invalid resource target '{res_target}': {e}"

        # FileReplace
        # Use Any (or implicit) to allow Mypy to narrow via isinstance,
        # but assign to typed variable to satisfy Pylance's unknown-type check.
        fr_lines_any = sections.get("filereplace", [])
        if isinstance(fr_lines_any, list):
            # Explicitly type the list variable to satisfy Pylance strict mode
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

        # DataAdd / DataPatch
        dp_lines_any = sections.get("datapatch", [])
        da_lines_any = sections.get("dataadd", [])
        combined_data_lines: List[str] = []

        if isinstance(dp_lines_any, list):
            dp_list: List[object] = cast(List[object], dp_lines_any)
            for item in dp_list:
                if isinstance(item, str):
                    combined_data_lines.append(item)
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
            # This function is now local
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
    - If passed a parsed mod_config dict, preserves the legacy return shape:
        (True, None) or (False, "error message").
    """
    # If caller provided a path string, parse it into a mod_config-like dict
    if isinstance(mod_config, str):
        mos_path = mod_config
        if not mos_path or not os.path.exists(mos_path):
            return False, [f"manifest missing: {mos_path}"]
        cp = configparser.ConfigParser(delimiters=("=",))

        def _preserve_case(optionstr: str) -> str:
            return optionstr

        cp.optionxform = _preserve_case  # type: ignore[method-assign]
        try:
            cp.read(mos_path, encoding="utf-8")
        except Exception as e:
            return False, [f"failed to parse manifest: {e}"]

        sections: Dict[str, List[str]] = {}
        for sec in cp.sections():
            # create lines in the form "key = value" mirroring prior tests/layout
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
    (Removed local imports for generate_patch_plan and logger, using module-level)
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
            for _, _, d in instrs:
                try:
                    sfunc = d[3]
                    if not str(sfunc).startswith(("prefix_", "postfix_")):
                        repls += 1
                except Exception:
                    repls += 1
            if repls > 1:
                conflicts[key] = instrs
            elif repls == 1 and len(instrs) > 1:
                conflicts[key] = instrs

        else:
            # file replaces and other multi-touch edits are conflicts
            conflicts[key] = instrs

    return conflicts


def apply_hunks(
    original_text: str,
    hunks: Union[Sequence["Hunk"], str],
    selected_hunk_indices: Optional[List[int]] = None,
) -> str:
    """
    Apply selected hunks (by index) to original_text and return merged text.

    Backwards-compatible behavior:
    - If `hunks` is a Sequence[Hunk] (preferred), use it.
    - If `hunks` is a string, treat it as the new text and compute hunks by diffing.
    - If callers still pass a legacy List[Dict[str, Any]] shaped like a Hunk, it will
      be acceptable at runtime because the structure is compatible; the static typing
      here prefers Sequence[Hunk] which is covariant and plays nicely with call sites.
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
                    "old_start": i1 + 1,  # 1-based to match legacy format
                    "old_count": max(0, i2 - i1),
                    "new_lines": new_lines[j1:j2],
                    "orig_segment": orig_lines[i1:i2],
                }
            )
        hunks_list = cast(List[Hunk], computed)
    else:
        # assume caller provided proper hunk dicts in legacy format
        hunks_list = list(hunks)

    # If selected_hunk_indices omitted, accept all hunks by default (test-friendly)
    if selected_hunk_indices is None:
        selected_hunk_indices = list(range(len(hunks_list)))

    result: List[str] = []
    ptr = 0  # pointer in orig_lines

    for idx, h in enumerate(hunks_list):
        # Cast to generic dict to avoid strict TypedDict warnings on legacy checks
        h_data = cast(Dict[str, Any], h)

        # Support both legacy keys and our computed keys
        old_start = int(h_data.get("old_start", 1)) - 1
        old_count_val = h_data.get("old_count")
        if old_count_val is not None:
            old_count = int(old_count_val)
        else:
            # Fallback: use length of old_lines if available
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
            # accept new_lines (legacy key 'new_lines' or computed new_lines)
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

    relative_path = _res_to_path(res_path)
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
        relative_path = _res_to_path(target_res)
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
                    # For large files, compare hashes (streaming). If equal -> nothing to do.
                    try:
                        if _sha256_file(work_path) == _sha256_file(source_path):
                            return log
                        # large files differ: treat as binary conflict and skip interactive resolution
                        log.append(
                            f"CONFLICT: large files differ (skipping textual merge): {work_path}"
                        )
                        return log
                    except Exception:
                        logger.debug(
                            "hash-compare failed for large files %s / %s",
                            work_path,
                            source_path,
                            exc_info=True,
                        )
                        # fall through to conservative fallback below

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

        # --- Textual Diff Resolution ---
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

        # write merged text to temporary file and atomically replace
        tmp_merge = work_path + ".gmos_merged"
        try:
            # Apply merged result
            _write_target(game_dir, relative_path, merged_text)
            log.append(f"SUCCESS: Applied merged changes to {work_path}")
            return log
        except Exception as e:
            log.append(f"ERROR: failed to apply merged result: {e}")
            try:
                safe_remove(tmp_merge)
            except Exception as e:
                logger.debug("cleanup failed for %s: %s", tmp_merge, e)
                pass
            return log

    # No conflict or resolved above: perform atomic replacement
    try:
        # Just copy/write
        with open(source_path, "rb") as f:
            src_bytes = f.read()
        _write_target(game_dir, relative_path, src_bytes)
        log.append(f"SUCCESS: Copied {source_path} -> {work_path}")
    except Exception as e:
        log.append(f"ERROR: failed to copy replacement file: {e}")

    return log


def patch_function(
    game_dir: str,
    target_res: str,
    target_func: Optional[str],
    source_path: str,
    source_func: str,
    mode: Optional[str] = None,
    vfs: Optional[Dict[str, bytes]] = None,
    conflict_delegate: Optional[ConflictDelegate] = None,
) -> List[str]:
    """Patches a function in the target file with code from the source file, supporting prefix/postfix wrapping and creation."""
    log: List[str] = []
    try:
        work_path, copy_log = lazy_copy_file(game_dir, target_res)
        log.append(copy_log)
        ensure_within(game_dir, work_path)
        try:
            target_text = read_source_for_patching(game_dir, target_res, vfs)
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
                game_dir, _res_to_path(target_res), "".join(new_lines_create), vfs
            )
            log.append(
                f"SUCCESS: Created new function '{target_func}' in '{target_res}'."
            )
            return log

        # --- Original logic for replace/prefix/postfix, with fixes ---
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

            body_indent = (
                _leading_whitespace(target_lines[target_sig_line_index]) + "    "
            )
            wrapper_body: List[str] = []
            wrapper_body.append(
                f"{body_indent}#--- START {effective_mode.upper()} PATCH: {Path(source_path).name}::{source_func} ---\n"
            )

            if effective_mode == "prefix":
                wrapper_body.extend(patch_body_lines)
                wrapper_body.append(f"{body_indent}#--- ORIGINAL FUNCTION BODY ---\n")
                wrapper_body.extend(original_body_lines)
            else:  # postfix
                wrapper_body.append(f"{body_indent}#--- ORIGINAL FUNCTION BODY ---\n")
                wrapper_body.extend(original_body_lines)
                wrapper_body.append(f"{body_indent}#--- POSTFIX PATCH CODE ---\n")
                wrapper_body.extend(patch_body_lines)

            wrapper_body.append(
                f"{body_indent}#--- END {effective_mode.upper()} PATCH ---\n"
            )

            start_idx = target_sig_line_index + 1
            end_idx = target_body_range[1]
            new_lines = (
                target_lines[:start_idx] + wrapper_body + target_lines[end_idx + 1 :]
            )

        # Ensure file is always written for replace/prefix/postfix ---
        if new_lines:
            _write_target(game_dir, _res_to_path(target_res), "".join(new_lines), vfs)
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


def patch_variable(
    game_dir: str,
    target_res: str,
    target_var: str,
    source_path: str,
    source_var: str,
    mode: str = "replace",
    vfs: Optional[Dict[str, bytes]] = None,
) -> List[str]:
    log: List[str] = []
    try:
        work_path, copy_log = lazy_copy_file(game_dir, target_res)
        log.append(copy_log)
        ensure_within(game_dir, work_path)
        try:
            target_text = read_source_for_patching(game_dir, target_res, vfs)
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
            # Ensure source signature uses target name to avoid renames.
            if src_block and source_var != target_var:
                # Correctly substitute the source variable name with the target one.
                # Handles both 'var' and 'const'
                pat = re.compile(
                    rf"(^\s*(var|const)\s+){re.escape(source_var)}(\s*[:=])"
                )
                src_block[0] = pat.sub(rf"\1{target_var}\3", src_block[0], count=1)

            new_lines = (
                target_lines[: tgt_range[0]]
                + src_block
                + target_lines[tgt_range[1] + 1 :]
            )
            _write_target(game_dir, _res_to_path(target_res), "".join(new_lines), vfs)
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
            _write_target(game_dir, _res_to_path(target_res), "".join(new_lines), vfs)
            log.append(
                f"SUCCESS: Appended {len(inner)} lines into '{target_var}' in {target_res}."
            )
            return log

        if mode == "create":
            if tgt_range:
                log.append(
                    f"ERROR: Target var '{target_var}' already exists; DataAdd/create will not overwrite. Skipping."
                )
                return log

            # Rename the variable in the source block if names differ.
            if source_var != target_var:
                pat = re.compile(
                    rf"(^\s*(var|const)\s+){re.escape(source_var)}(\s*[:=])"
                )
                src_block[0] = pat.sub(rf"\1{target_var}\3", src_block[0], count=1)

            new_lines = target_lines + ["\n"] + src_block
            _write_target(game_dir, _res_to_path(target_res), "".join(new_lines), vfs)
            log.append(f"SUCCESS: Created new var '{target_var}' in {target_res}.")
            return log

        log.append(f"ERROR: Unknown variable patch mode: {mode}")
        return log

    except FileNotFoundError as e:
        log.append(f"ERROR: File not found during Variable patch: {e}")
        return log
    except OSError as e:
        log.append(f"ERROR: I/O error during Variable patch: {e}")
        return log
    except Exception as e:
        log.append(f"FATAL ERROR during Variable patch ({mode}): {e}")
        return log


def read_source_for_patching(
    game_dir: str, target_res: str, vfs: Optional[Dict[str, bytes]] = None
) -> str:
    """
    Reads source code for a target resource.
    Priority:
    1. In-memory VFS (if force_pck is active and file was already modified).
    2. Loose file in game_dir (already patched or manually placed).
    3. Content extracted from any .pck file in game_dir (Vanilla).
    """
    rel_path = _res_to_path(target_res)

    # 0. Try VFS
    if vfs is not None and rel_path in vfs:
        return vfs[rel_path].decode("utf-8", errors="ignore")
    # 1. Try loose file
    work_path = os.path.join(game_dir, rel_path)
    if os.path.exists(work_path):
        return Path(work_path).read_text(encoding="utf-8", errors="ignore")

    # 2. Try PCK files
    # Scan for .pck files in the game root (e.g. 'Brotato.pck', 'data.pck')
    try:
        with os.scandir(game_dir) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(".pck"):
                    try:
                        content_bytes = pck_tools.get_file_content(
                            entry.path, target_res
                        )
                        if content_bytes:
                            logger.info(
                                "Read vanilla resource '%s' from PCK: %s",
                                target_res,
                                entry.name,
                            )
                            # If using VFS (PCK mode), cache it there.
                            # If loose mode, write to disk so subsequent patches have a base.
                            if vfs is not None:
                                vfs[rel_path] = content_bytes
                            else:
                                ensure_within(game_dir, work_path)
                                atomic_write_bytes(work_path, content_bytes)
                            return content_bytes.decode("utf-8", errors="ignore")
                    except Exception as e:
                        logger.debug("Failed to read from PCK %s: %s", entry.name, e)
    except Exception as e:
        logger.warning("Error scanning for PCK files: %s", e)

    raise FileNotFoundError(
        f"Target resource '{target_res}' not found in game dir or any .pck archive."
    )


def apply_policy_to_plan(
    plan: List[Tuple[str, str, Tuple[Any, ...]]],
) -> List[Tuple[str, str, Tuple[Any, ...]]]:
    """
    Filters the patch plan based on persistent file rules.
    If a rule exists for a target resource (e.g. 'res://icon.png': 'Mod A'),
    any conflicting operations from other mods for that target are dropped.
    """
    rules = policy.load_file_rules()
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

    for mod_name, op, details in plan:
        target_res = _get_target(op, details)
        if not target_res:
            filtered_plan.append((mod_name, op, details))
            continue

        # Normalize path for lookup
        norm_target = _res_to_path(target_res)
        winner = rules.get(norm_target)

        # If a winner is defined and this mod isn't it, check if we should drop it.
        # Policy strictly enforces: If a winner is set, they own the file for destructive ops.
        # We currently drop ALL ops from non-winners for that file to ensure stability.
        if winner and winner != mod_name:
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
    """Helper to write data either to disk (atomic) or to VFS memory."""
    if isinstance(content, str):
        # Auto-Sanitization for GDScript
        if rel_path.endswith(".gd"):
            content = sanitize_script_content(content)
        data_bytes = content.encode("utf-8")
    else:
        data_bytes = content

    if vfs is not None:
        vfs[rel_path] = data_bytes
    else:
        work_path = os.path.join(game_dir, rel_path)
        ensure_within(game_dir, work_path)
        if isinstance(content, str):
            atomic_write_with_backup(work_path, content)
        else:
            atomic_write_bytes(work_path, data_bytes)


def revert_to_vanilla(game_dir: str, pck_path: Optional[str] = None) -> List[str]:
    """
    Restores the game directory to its vanilla state using .bak files
    and the previous runtime_manifest.json.
    """
    log: List[str] = []
    manifest_path = os.path.join(game_dir, "runtime_manifest.json")
    if not os.path.exists(manifest_path):
        return ["No previous runtime manifest found. Assuming clean state."]

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest: Dict[str, Any] = json.load(f)
        # PCK Revert Logic
        if pck_path:
            bak_pck = pck_path + ".bak"
            if os.path.exists(bak_pck):
                atomic_write_copy(bak_pck, pck_path)
                log.append(f"Restored Main PCK from {os.path.basename(bak_pck)}")
            else:
                log.append("Notice: No PCK backup found to revert.")
        modified_files = cast(List[str], manifest.get("modified_files", []))
        log.append(f"Reverting {len(modified_files)} files to vanilla...")

        for rel_path in modified_files:
            file_path = os.path.join(game_dir, rel_path)
            bak_path = file_path + ".bak"

            if os.path.exists(bak_path):
                # Restore backup (COPY back to preserve backup for next time)
                try:
                    atomic_write_copy(bak_path, file_path)
                    log.append(f"Restored: {rel_path}")
                except Exception as e:
                    log.append(f"ERROR restoring {rel_path}: {e}")
            elif os.path.exists(file_path):
                # No backup exists. If it was created by GMOS, delete it.
                # (Ideally we'd track 'created' status, but for now we assume non-bak modified files are new)
                # But safer to leave it alone if we aren't sure?
                # Actually, if it's in 'modified_files' but has no bak, it was likely a 'create' op.
                # Let's check the op list? Too complex.
                # Current policy: If no backup, we can't restore.
                log.append(f"WARNING: No backup for {rel_path}, skipping.")

    except Exception as e:
        log.append(f"ERROR reading manifest: {e}")

    return log


def run_patcher(
    game_dir: str,
    patch_plan: List[Tuple[str, str, Tuple[Any, ...]]],
    force_pck: bool = False,
    progress_cb: Optional[Callable[[float, str], None]] = None,
    conflict_delegate: Optional[ConflictDelegate] = None,
) -> List[str]:
    """
    Execute the normalized patch_plan.

    If force_pck is True, patches are appended to the main PCK file instead of written to disk.

    This is an idempotent process. It first cleans the target files from the
    working directory to ensure a fresh patch application every time.

    The whole run is serialized with _patch_run_lock so multiple simulate/diff
    or patch runs cannot run concurrently inside this process and contend on the
    same temporary sim_work tree.
    """
    log: List[str] = []
    applied_ops: List[Dict[str, Any]] = []
    applied_files: Set[str] = set()
    # VFS for PCK patching (rel_path -> bytes)
    vfs: Optional[Dict[str, bytes]] = {} if force_pck else None
    main_pck_path: Optional[str] = None

    if force_pck:
        main_pck_path = pck_tools.get_main_pck_path(game_dir)
        if not main_pck_path:
            log.append("ERROR: Force PCK enabled but no .pck file found in game dir.")
            return log
        log.append(f"PCK Mode: Targeting {os.path.basename(main_pck_path)}")

        # Create PCK Backup if not exists
        pck_bak = main_pck_path + ".bak"
        if not os.path.exists(pck_bak):
            atomic_write_copy(main_pck_path, pck_bak)
            log.append("Created backup of main PCK.")
    # Throttled progress helper
    total_steps = 1  # Base step
    current_step = 0
    last_progress_time = 0.0

    def _emit_progress(msg: str, force: bool = False) -> None:
        nonlocal last_progress_time
        now = time.time()
        # Throttle updates to ~10fps to prevent UI stutter (Section 3.3)
        if progress_cb and (force or (now - last_progress_time > 0.1)):
            pct = min(0.99, current_step / max(1, total_steps))
            progress_cb(pct, msg)
            last_progress_time = now

    _patch_run_lock = threading.Lock()
    # serialize whole run to avoid simulate/diff races on shared sim_work tree
    try:
        with _patch_run_lock:
            if not os.path.isdir(game_dir):
                log.append(f"ERROR: Game directory not found: {game_dir}")
                return log

            # 1. Revert to Vanilla (Clean Slate)
            _emit_progress("Reverting to vanilla...", force=True)
            log.append("--- Reverting to Vanilla State ---")
            log.extend(
                revert_to_vanilla(game_dir, main_pck_path if force_pck else None)
            )

            # 2. Identify all unique files that will be patched.
            _emit_progress("Analyzing patch plan...")
            files_to_patch: Set[str] = set()
            # Apply Persistent Conflict Policy
            effective_plan = apply_policy_to_plan(patch_plan)
            for _, _, details in effective_plan:
                try:
                    target_res = cast(str, details[0])
                    files_to_patch.add(_res_to_path(target_res))
                except (IndexError, TypeError):
                    continue  # Skip malformed instructions

            applied_files.clear()
            applied_ops.clear()

            # Split variable ops and others (preserve plan order for others)
            var_ops: List[Tuple[str, str, Tuple[Any, ...]]] = []
            other_ops: List[Tuple[str, str, Tuple[Any, ...]]] = []
            for instr in effective_plan:
                if instr[1] == "VariablePatch":
                    var_ops.append(instr)
                else:
                    other_ops.append(instr)
            # Update total steps estimate
            total_steps = len(other_ops) + len(var_ops) + 5  # +5 for overhead tasks
            # Execute non-variable operations first
            for mod_name, op, details in other_ops:
                current_step += 1
                log.append(f"--- Applying {op} from {mod_name} ---")
                try:
                    if op == "FileReplace":
                        target_res, source_path = cast(str, details[0]), cast(
                            str, details[1]
                        )
                        _emit_progress(f"Patching {target_res}...")
                        # patch_file_replace needs to use _write_target internally or be adapted.
                        # For minimal refactor, we'll handle VFS write here for FileReplace
                        if vfs is not None:
                            with open(source_path, "rb") as f:
                                vfs[_res_to_path(target_res)] = f.read()
                            log.append(f"Buffered {target_res} for PCK.")
                        else:
                            log.extend(
                                patch_file_replace(
                                    game_dir,
                                    target_res,
                                    source_path,
                                    conflict_delegate=conflict_delegate,
                                )
                            )
                        applied_files.add(_res_to_path(target_res))
                        applied_ops.append(
                            {
                                "mod": mod_name,
                                "op": op,
                                "target": target_res,
                                "source": source_path,
                            }
                        )
                    elif op == "FunctionPatch":
                        try:
                            t_res = cast(str, details[0])
                            t_func = cast(Optional[str], details[1])
                            s_path = cast(str, details[2])
                            s_func = cast(str, details[3])
                            mode = cast(Optional[str], details[4])
                        except ValueError:
                            # Backwards compatibility: older format missing mode
                            t_res, t_func, s_path, s_func = (
                                cast(str, details[0]),
                                cast(Optional[str], details[1]),
                                cast(str, details[2]),
                                cast(str, details[3]),
                            )
                            mode = None  # Fallback for older format if needed

                        try:
                            lines = patch_function(
                                game_dir,
                                t_res,
                                t_func,
                                s_path,
                                s_func,
                                mode=mode,
                                vfs=vfs,
                            )
                            log.extend(lines)
                            applied_files.add(_res_to_path(t_res))
                            applied_ops.append(
                                {
                                    "mod": mod_name,
                                    "op": op,
                                    "target": f"{t_res}::{t_func}",
                                    "source": f"{s_path}::{s_func}",
                                    "mode": mode or "",
                                }
                            )
                        except Exception as e:
                            # record the error but continue with other patches
                            log.append(
                                f"FATAL ERROR while processing FunctionPatch for {mod_name}: {e}"
                            )
                            applied_ops.append(
                                {
                                    "mod": mod_name,
                                    "op": op,
                                    "status": "error",
                                    "notes": str(e),
                                }
                            )

                    else:
                        log.append(
                            f"WARNING: Unknown non-variable operation '{op}' from {mod_name}. Skipped."
                        )
                        applied_ops.append(
                            {"mod": mod_name, "op": op, "status": "skipped"}
                        )
                except Exception as e:
                    # Unexpected exception at top-level op processing: record and keep going.
                    log.append(f"FATAL ERROR while processing {op} for {mod_name}: {e}")
                    applied_ops.append(
                        {"mod": mod_name, "op": op, "status": "error", "notes": str(e)}
                    )

            # Group variable ops by (target_res, target_var)
            by_target: Dict[Tuple[str, str], List[Tuple[str, str, str, str]]] = (
                defaultdict(list)
            )
            for mod_name, _op, details in var_ops:
                try:
                    t_res = cast(str, details[0])
                    t_var = cast(str, details[1])
                    s_path = cast(str, details[2])
                    s_var = cast(str, details[3])
                    mode = cast(str, details[4])
                except (ValueError, IndexError):
                    log.append(
                        f"ERROR: Malformed VariablePatch details from {mod_name}: {details}"
                    )
                    continue
                by_target[(t_res, t_var)].append((mod_name, s_path, s_var, mode))

            # Apply per-target: replace -> create -> add
            # Adjust total steps to match the loop over individual ops we just did implicitly?
            # Actually we looped ops to build by_target, now we apply them.
            for (t_res, t_var), ops in by_target.items():
                _emit_progress(f"Patching variables in {t_res}...")
                log.append(f"=== Variable target {t_res}::{t_var} ===")
                # REPLACE (keep order)
                for mod_name, s_path, s_var, mode in ops:
                    current_step += 1
                    if mode == "replace":
                        log.append(f"--- Applying Variable REPLACE from {mod_name} ---")
                        lines = patch_variable(
                            game_dir,
                            t_res,
                            t_var,
                            s_path,
                            s_var,
                            mode="replace",
                            vfs=vfs,
                        )
                        log.extend(lines)
                        applied_ops.append(
                            {
                                "mod": mod_name,
                                "op": "VariablePatch",
                                "mode": "replace",
                                "target": f"{t_res}::{t_var}",
                                "source": f"{s_path}::{s_var}",
                            }
                        )
                        applied_files.add(_res_to_path(t_res))
                # CREATE
                for mod_name, s_path, s_var, mode in ops:
                    current_step += 1
                    if mode in ("create", "dataadd"):
                        log.append(f"--- Applying Variable CREATE from {mod_name} ---")
                        lines = patch_variable(
                            game_dir,
                            t_res,
                            t_var,
                            s_path,
                            s_var,
                            mode="create",
                        )
                        log.extend(lines)
                        applied_ops.append(
                            {
                                "mod": mod_name,
                                "op": "VariablePatch",
                                "mode": "create",
                                "target": f"{t_res}::{t_var}",
                                "source": f"{s_path}::{s_var}",
                            }
                        )
                        applied_files.add(_res_to_path(t_res))
                # ADD
                for mod_name, s_path, s_var, mode in ops:
                    current_step += 1
                    if mode == "add":
                        log.append(f"--- Applying Variable ADD from {mod_name} ---")
                        lines = patch_variable(
                            game_dir,
                            t_res,
                            t_var,
                            s_path,
                            s_var,
                            mode="add",
                        )
                        log.extend(lines)
                        applied_ops.append(
                            {
                                "mod": mod_name,
                                "op": "VariablePatch",
                                "mode": "add",
                                "target": f"{t_res}::{t_var}",
                                "source": f"{s_path}::{s_var}",
                            }
                        )
                        applied_files.add(_res_to_path(t_res))
            # 3. Finalize PCK Write
            if force_pck and vfs and main_pck_path:
                _emit_progress("Writing PCK archive...", force=True)
                log.append(f"--- Appending {len(vfs)} files to PCK ---")
                try:
                    # Restore from clean backup first to ensure we don't append duplicates on re-run
                    pck_bak = main_pck_path + ".bak"
                    if os.path.exists(pck_bak):
                        atomic_write_copy(pck_bak, main_pck_path)

                    for r_path, data in vfs.items():
                        # Godot expects "res://..." paths in the PCK index
                        # We stored them as rel_paths (no res://) in VFS key
                        res_str = f"res://{r_path.replace(os.sep, '/')}"
                        pck_tools.append_file_to_pck(main_pck_path, data, res_str)

                    log.append("PCK update complete.")
                except Exception as e:
                    log.append(f"FATAL PCK WRITE ERROR: {e}")
                    raise e
            # write human-readable patch.log (best-effort)
            try:
                log_path = os.path.join(game_dir, "patch.log")
                ensure_within(game_dir, log_path)  # Safety check
                log_content = time.strftime("%Y-%m-%d %H:%M:%S") + " - Patch run\n"
                log_content += "\n".join(log) + "\n"
                log_content += "--- end run ---\n"

                # Write patch.log atomically while pausing the workroot watcher to avoid races
                try:
                    with _pause_workroot_watcher_ctx():
                        # small cooperative delay so other threads can release transient handles (Windows)
                        time.sleep(0.02)
                        atomic_replace(log_path, log_content)
                except Exception as e:
                    logger.debug("ignored exception when writing patch.log: %s", e)
            except Exception as e:
                logger.debug("ignored exception preparing patch.log: %s", e)

            # runtime manifest (structured)
            try:
                manifest_path = os.path.join(game_dir, "runtime_manifest.json")
                ensure_within(game_dir, manifest_path)  # Safety check
                manifest: RuntimeManifest = {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "game_dir": game_dir,
                    "applied_ops": cast(List[Dict[str, str]], applied_ops),
                    "modified_files": sorted(applied_files),
                }
                # Persist runtime_manifest atomically while pausing the workroot watcher
                try:
                    with _pause_workroot_watcher_ctx():
                        time.sleep(0.02)
                        atomic_replace(manifest_path, json.dumps(manifest, indent=2))
                        log.append(f"INFO: runtime_manifest written: {manifest_path}")
                        for rel in sorted(applied_files):
                            log.append(f"MODIFIED: {rel}")
                except Exception as e:
                    log.append(f"WARNING: Failed to write runtime_manifest.json: {e}")
            except Exception as e:
                log.append(f"WARNING: Failed to assemble runtime manifest: {e}")
            # Final callback
            if progress_cb:
                progress_cb(1.0, "Patching complete.")

            return log
    except Exception as exc:
        # Top-level unexpected error: ensure we surface something useful to the caller
        logger.exception("run_patcher: unexpected fatal error, aborting patch run")
        log.append(f"FATAL: run_patcher aborted with exception: {exc}")
        applied_ops.append({"op": "run_patcher", "status": "fatal", "notes": str(exc)})
        # attempt a best-effort manifest/log write before returning
        try:
            manifest_path = os.path.join(game_dir, "runtime_manifest.partial.json")
            with _pause_workroot_watcher_ctx():
                time.sleep(0.02)
                atomic_replace(
                    manifest_path,
                    json.dumps(
                        {
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "error": str(exc),
                            "applied_ops": applied_ops,
                            "modified_files": sorted(applied_files),
                        },
                        indent=2,
                    ),
                )
                log.append(f"INFO: partial runtime_manifest written: {manifest_path}")
        except Exception:
            logger.debug("failed to write partial manifest", exc_info=True)
        return log
