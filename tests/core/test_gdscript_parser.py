# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GDScript Parsing Test Suite
# Includes both Unit Tests (Deterministic) and Property-Based Fuzz Tests (Hypothesis)

from importlib import import_module
from typing import Optional, Sequence, Tuple

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tests.data.gdscript_samples import SAMPLES

# Import the module under test
mod = import_module("gmos.core.patcher")
# Access functions directly for fuzz tests
get_var_block = mod.get_var_block
get_function_block = mod.get_function_block


def _extract_block(
    lines: Sequence[str],
    src: str,
    span: Optional[Tuple[int, int]],
) -> Optional[str]:
    """Helper to extract block text from returned span."""
    if span is None:
        return None
    start, end = span
    if 0 <= start < len(lines) and 0 <= end <= len(lines) and start < end:
        return "\n".join(lines[start:end])
    if 0 <= start < len(lines) and 0 <= end < len(lines) and start <= end:
        return "\n".join(lines[start : end + 1])
    if 0 <= start < len(src) and 0 <= end <= len(src) and start < end:
        return src[start:end]
    return None


def _safe_call_get_var_block(src: str, var_name: str) -> Optional[Tuple[int, int]]:
    lines = src.splitlines()
    try:
        span: Optional[Tuple[int, int]] = mod.get_var_block(lines, var_name)
        return span
    except Exception as e:
        raise AssertionError(f"get_var_block crashed for var {var_name}: {e}") from e


def _safe_call_get_function_block(
    src: str, func_name: str
) -> Optional[Tuple[int, int]]:
    lines = src.splitlines()
    try:
        span: Optional[Tuple[int, int]] = mod.get_function_block(lines, func_name)
        return span
    except Exception as e:
        raise AssertionError(
            f"get_function_block crashed for func {func_name}: {e}"
        ) from e


# --- Unit Tests (Deterministic) ---


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
    # Handle single line vs multi line return logic
    b1: Optional[str]
    if s1 > e1:
        s1, e1 = e1, s1
    if s1 == e1 and 0 <= s1 < len(lines):
        b1 = lines[s1]
    else:
        b1 = _extract_block(lines, src, (s1, e1))

    assert b1 is not None and "foo" in b1

    assert span2 is not None
    s2, e2 = span2
    b2: Optional[str]
    if s2 == e2 and 0 <= s2 < len(lines):
        b2 = lines[s2]
    else:
        b2 = _extract_block(lines, src, (s2, e2))

    assert b2 is not None
    assert ("bar" in b2) or ("print" in b2)


def test_parser_data_basic() -> None:
    for src, expect in SAMPLES:
        for v in expect.get("var", []):
            span = _safe_call_get_var_block(src, v)
            assert span is not None, f"var {v} not found in sample: {src!r}"
        for c in expect.get("const", []):
            span = _safe_call_get_var_block(src, c)
            assert span is not None, f"const {c} not found in sample: {src!r}"
        for f in expect.get("func", []):
            span = _safe_call_get_function_block(src, f)
            assert span is not None, f"func {f} not found in sample: {src!r}"


# --- Fuzz Tests (Hypothesis) ---

# Generate simple identifiers (var names)
identifiers = st.from_regex(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", fullmatch=True)

# Generate random lines of code
code_lines = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs")), min_size=0, max_size=100
)


def balanced_braces() -> st.SearchStrategy[str]:
    return st.recursive(
        st.text(alphabet="abcdefg 12345"),
        lambda children: st.one_of(
            st.builds(lambda s: f"{{{s}}}", children),  # type: ignore
            st.builds(lambda s: f"[{s}]", children),  # type: ignore
            st.builds(lambda s: f"({s})", children),  # type: ignore
        ),
        max_leaves=5,
    )


@given(var_name=identifiers, val=st.text(alphabet=st.characters(blacklist_categories=("Cc", "Cs")), min_size=1).map(lambda s: s.replace("\n", " ")))  # type: ignore[misc]
def test_get_var_block_fuzz_crash_safe(var_name: str, val: str) -> None:
    """Property: get_var_block should never crash on random input lines."""
    lines = [
        f"# random comment {val}",
        f"var {var_name} = {val}",
        "func some_other_stuff():",
        "    pass",
    ]

    try:
        result = get_var_block(lines, var_name)
    except Exception as e:
        pytest.fail(f"get_var_block crashed on valid-ish input: {e}")

    if result:
        start, end = result
        assert 0 <= start < len(lines)
        assert start <= end < len(lines)
        assert f"var {var_name}" in lines[start] or f"const {var_name}" in lines[start]


@given(code=st.lists(code_lines, min_size=1, max_size=50), target=identifiers)  # type: ignore[misc]
def test_get_var_block_fuzz_random_noise(code: list[str], target: str) -> None:
    """Property: Feeding complete garbage lines should not crash the parser."""
    try:
        get_var_block(code, target)
    except Exception as e:
        pytest.fail(f"Parser crashed on noise: {e}")


@settings(max_examples=200)  # type: ignore[misc]
@given(func_name=identifiers, body=st.lists(st.text(alphabet=st.characters(blacklist_categories=("Cc", "Cs")), min_size=1).map(lambda s: s.replace("\n", " ")), min_size=1, max_size=10))  # type: ignore[misc]
def test_get_function_block_structure(func_name: str, body: list[str]) -> None:
    """Property: A correctly formatted function must be detected."""
    lines = [f"func {func_name}():"]
    lines.extend([f"    {line}" for line in body])
    lines.append("func next_function():")
    lines.append("    pass")

    result = get_function_block(lines, func_name)

    assert result is not None, f"Failed to find generated function {func_name}"
    start, end = result

    detected_body = lines[start : end + 1]
    # Filter logic: Parser strips trailing empty lines/comments.
    cutoff = len(body) - 1
    while cutoff >= 0:
        line = f"    {body[cutoff]}"
        if not line.strip() or line.strip().startswith("#"):
            cutoff -= 1
        else:
            break
    significant_lines = cutoff + 1
    assert len(detected_body) >= significant_lines


@given(nested=balanced_braces())  # type: ignore[misc]
def test_get_var_block_nested_braces(nested: str) -> None:
    """Property: Parser handles nested braces in variable assignment."""
    assume(nested.strip())
    lines = ["var complex_data = (", f"    {nested}", ")", "var next_one = 1"]

    result = get_var_block(lines, "complex_data")
    if result:
        start, end = result
        block_content = "".join(lines[start : end + 1])
        assert nested in block_content, "Parser cut off nested structure"
