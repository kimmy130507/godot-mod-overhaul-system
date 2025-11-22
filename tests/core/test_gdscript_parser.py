# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# For GDScript parsing logic tests.
from importlib import import_module
from typing import Optional, Sequence, Tuple

from tests.data.gdscript_samples import SAMPLES

mod = import_module("gmos.core.patcher")


def _extract_block(
    lines: Sequence[str],
    src: str,
    span: Optional[Tuple[int, int]],
) -> Optional[str]:
    """
    Helper to extract block text from returned span.
    Handles both line-index and char-index spans.
    """
    if span is None:
        return None
    start, end = span
    # attempt line-index interpretation (exclusive)
    if 0 <= start < len(lines) and 0 <= end <= len(lines) and start < end:
        return "\n".join(lines[start:end])
    # attempt line-index interpretation (inclusive)
    if 0 <= start < len(lines) and 0 <= end < len(lines) and start <= end:
        return "\n".join(lines[start : end + 1])
    # attempt character-index interpretation
    if 0 <= start < len(src) and 0 <= end <= len(src) and start < end:
        return src[start:end]
    return None


def _safe_call_get_var_block(src: str, var_name: str) -> Optional[Tuple[int, int]]:
    """Helper to safely call get_var_block for data testing."""
    lines = src.splitlines()
    try:
        span: Optional[Tuple[int, int]] = mod.get_var_block(lines, var_name)
        return span
    except Exception as e:
        raise AssertionError(f"get_var_block crashed for var {var_name}: {e}") from e


def _safe_call_get_function_block(
    src: str, func_name: str
) -> Optional[Tuple[int, int]]:
    """Helper to safely call get_function_block for data testing."""
    lines = src.splitlines()
    try:
        span: Optional[Tuple[int, int]] = mod.get_function_block(lines, func_name)
        return span
    except Exception as e:
        raise AssertionError(
            f"get_function_block crashed for func {func_name}: {e}"
        ) from e


# --- Test Parser Blocks ---


def test_get_var_block_simple() -> None:
    src: str = "var x = 1\nvar y = 'a'\n"
    lines = src.splitlines()
    span: Optional[Tuple[int, int]] = mod.get_var_block(lines, "x")
    assert span is not None
    block: Optional[str] = _extract_block(lines, src, span)
    assert block is not None and "var x" in block


def test_get_function_block_edgecases() -> None:
    src: str = "func foo(): return 1\n\nfunc bar():\n    print('ok')\n"
    lines = src.splitlines()
    span1: Optional[Tuple[int, int]] = mod.get_function_block(lines, "foo")
    span2: Optional[Tuple[int, int]] = mod.get_function_block(lines, "bar")

    assert span1 is not None
    s1, e1 = span1
    b1: Optional[str]
    if s1 > e1:
        s1, e1 = e1, s1
    if s1 == e1 and 0 <= s1 < len(lines):
        b1 = lines[s1]
    else:
        b1 = _extract_block(lines, src, (s1, e1))

    assert b1 is not None and "foo" in b1
    # multi-line functions should return a valid block
    assert span2 is not None
    s2, e2 = span2
    b2: Optional[str]
    if s2 == e2 and 0 <= s2 < len(lines):
        b2 = lines[s2]
    else:
        b2 = _extract_block(lines, src, (s2, e2))

    assert b2 is not None
    assert ("bar" in b2) or ("print" in b2)


# --- Test Parser data ---


def test_parser_data_basic() -> None:
    for src, expect in SAMPLES:
        # test variables
        for v in expect.get("var", []):
            span = _safe_call_get_var_block(src, v)
            assert (
                span is not None
            ), f"var {v} not found or span None in sample: {src!r}"
        for c in expect.get("const", []):
            span = _safe_call_get_var_block(src, c)
            assert (
                span is not None
            ), f"const {c} not found or span None in sample: {src!r}"
        # test functions
        for f in expect.get("func", []):
            span = _safe_call_get_function_block(src, f)
            assert (
                span is not None
            ), f"func {f} not found or span None in sample: {src!r}"
