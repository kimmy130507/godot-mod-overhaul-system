# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# Property-Based Testing Suite
# Generates random valid and invalid GDScript fragments to stress-test the regex parser.

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gmos.core.patcher import get_function_block, get_var_block

# Generate simple identifiers (var names)
identifiers = st.from_regex(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", fullmatch=True)

# Generate random lines of code, including comments and whitespace
code_lines = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs")), min_size=0, max_size=100
)


# Generate balanced braces to simulate nested structures (dictionaries, arrays)
def balanced_braces() -> st.SearchStrategy[str]:
    return st.recursive(
        st.text(alphabet="abcdefg 12345"),
        lambda children: st.one_of(
            st.builds(lambda s: f"{{{s}}}", children),  # type: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
            st.builds(lambda s: f"[{s}]", children),  # type: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
            st.builds(lambda s: f"({s})", children),  # type: ignore[reportUnknownArgumentType, reportUnknownLambdaType]
        ),
        max_leaves=5,
    )


# --- Tests ---


@given(var_name=identifiers, val=st.text(min_size=1))  # type: ignore[misc]
def test_get_var_block_fuzz_crash_safe(var_name: str, val: str) -> None:
    """
    Property: get_var_block should never crash (raise unhandled exception)
    on random input lines, and if it returns a block, indices must be valid.
    """
    # Construct a synthetic file content that *might* contain the var
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
        assert 0 <= start < len(lines), "Start index out of bounds"
        assert start <= end < len(lines), "End index out of bounds or less than start"
        # The block must actually contain the declaration
        assert f"var {var_name}" in lines[start] or f"const {var_name}" in lines[start]


@given(code=st.lists(code_lines, min_size=1, max_size=50), target=identifiers)  # type: ignore[misc]
def test_get_var_block_fuzz_random_noise(code: list[str], target: str) -> None:
    """
    Property: Feeding complete garbage lines should not crash the parser.
    """
    try:
        get_var_block(code, target)
    except Exception as e:
        pytest.fail(f"Parser crashed on noise: {e}")


@settings(max_examples=200)  # type: ignore[misc]
@given(
    func_name=identifiers, body=st.lists(st.text(min_size=1), min_size=1, max_size=10)
)  # type: ignore[misc]
def test_get_function_block_structure(func_name: str, body: list[str]) -> None:
    """
    Property: A correctly formatted function must be detected.
    """
    # Construct valid function
    lines = [f"func {func_name}():"]
    # Indent body
    lines.extend([f"    {line}" for line in body])
    lines.append("func next_function():")
    lines.append("    pass")

    result = get_function_block(lines, func_name)

    assert result is not None, f"Failed to find generated function {func_name}"
    start, end = result

    # The block should strictly contain the body lines
    detected_body = lines[start : end + 1]
    # Filter logic: The parser strips trailing empty lines and comments from the block.
    # We must do the same to our expected body to verify the length matches.
    cutoff = len(body) - 1
    while cutoff >= 0:
        line = f"    {body[cutoff]}"
        if not line.strip() or line.strip().startswith("#"):
            cutoff -= 1
        else:
            break
    significant_lines = cutoff + 1

    # The detected body must contain at least the significant lines
    assert len(detected_body) >= significant_lines


@given(nested=balanced_braces())  # type: ignore[misc]
def test_get_var_block_nested_braces(nested: str) -> None:
    """
    Property: The parser should handle nested braces/brackets in variable assignment
    without ending the block prematurely.
    """
    lines = ["var complex_data = ", f"    {nested}", "var next_one = 1"]

    result = get_var_block(lines, "complex_data")
    if result:
        start, end = result
        # The block should encompass the nested lines if the heuristics work
        # Ideally, it captures lines[0] and lines[1]
        block_content = "".join(lines[start : end + 1])
        assert nested in block_content, "Parser cut off nested structure"
