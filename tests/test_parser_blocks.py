from importlib import import_module

mod = import_module("gmos")


def _extract_block(lines, src, span):
    """Extract block text from returned span.

    Accepts:
    - line-index spans: (start_line, end_line) where end may be exclusive or inclusive.
    - char-index spans: (start_char, end_char) referring to indices in `src`.
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
    if (
        isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < len(src)
        and 0 <= end <= len(src)
        and start < end
    ):
        return src[start:end]
    return None


def test_get_var_block_simple():
    src = "var x = 1\nvar y = 'a'\n"
    lines = src.splitlines()
    span = mod.get_var_block(lines, "x")
    assert span is not None
    block = _extract_block(lines, src, span)
    assert block is not None and "var x" in block


def test_get_function_block_edgecases():
    src = "func foo(): return 1\n\nfunc bar():\n    print('ok')\n"
    lines = src.splitlines()
    span1 = mod.get_function_block(lines, "foo")
    span2 = mod.get_function_block(lines, "bar")
    # normalize reversed or zero-length spans
    s1, e1 = span1
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
    if s2 == e2 and 0 <= s2 < len(lines):
        b2 = lines[s2]
    else:
        b2 = _extract_block(lines, src, (s2, e2))
    assert b2 is not None
    assert ("bar" in b2) or ("print" in b2)
