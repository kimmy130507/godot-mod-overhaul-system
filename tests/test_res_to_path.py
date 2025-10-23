import os
from importlib import import_module

import pytest

mod = import_module("gmos")


def test_res_to_path_rejects_traversal():
    with pytest.raises(RuntimeError):
        mod._res_to_path("res://../outside/file.txt")
    with pytest.raises(RuntimeError):
        mod._res_to_path("res://../../escape.bin")


def test_res_to_path_normalizes_dots():
    # '.' should be removed
    out = mod._res_to_path("res://scenes/./main.tscn")
    assert out == os.path.join("scenes", "main.tscn")

    # inner '..' should collapse
    out2 = mod._res_to_path("res://scenes/sub/../main.tscn")
    assert out2 == os.path.join("scenes", "main.tscn")


def test_res_to_path_accepts_plain_relative_and_empty():
    # plain relative path (no res:// prefix)
    p = "scenes/main.tscn"
    assert mod._res_to_path(p) == os.path.join("scenes", "main.tscn")

    # res:// with no trailing path returns empty string
    assert mod._res_to_path("res://") == ""
    assert mod._res_to_path("") == ""
