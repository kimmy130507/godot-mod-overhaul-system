from importlib import import_module

from tests.corpus.gdscript_samples import SAMPLES

mod = import_module("gmos")


def _safe_call_get_var_block(src: str, var_name: str):
    lines = src.splitlines()
    try:
        span = mod.get_var_block(lines, var_name)
        return span
    except Exception as e:
        raise AssertionError(f"get_var_block crashed for var {var_name}: {e}")


def _safe_call_get_function_block(src: str, func_name: str):
    lines = src.splitlines()
    try:
        span = mod.get_function_block(lines, func_name)
        return span
    except Exception as e:
        raise AssertionError(f"get_function_block crashed for func {func_name}: {e}")


def test_parser_corpus_basic():
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
