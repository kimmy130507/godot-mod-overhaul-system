from importlib import import_module

import pytest

mod = import_module("gmos")


def test_ensure_within_rejects_traversal(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    # attempt to reference a sibling outside the work dir
    outside = tmp_path / "outside_dir" / "evil.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("not allowed")

    # relative traversal path
    traversal = str(root / "../outside_dir/evil.txt")
    with pytest.raises(RuntimeError):
        mod.ensure_within(str(root), traversal)

    # absolute path outside root
    with pytest.raises(RuntimeError):
        mod.ensure_within(str(root), str(outside))


def test_patch_file_replace_fails_when_target_outside(tmp_path):
    # Setup an origin tree and work root
    orig = tmp_path / "orig"
    work = tmp_path / "work"
    mod_dir = tmp_path / "mod"
    orig.mkdir()
    work.mkdir()
    mod_dir.mkdir()

    # create a source replacement file inside mod
    src = mod_dir / "patch.bin"
    src.write_bytes(b"\x00\x01NEW")

    # craft a target path that resolves outside the workroot using traversal
    target_res = "res://../../outside_dir/escape.bin"
    # call patch_file_replace.
    # Accept either:
    #  - a RuntimeError raised from ensure_within OR
    #  - a returned log list where one entry contains the path-escape message.
    try:
        log = mod.patch_file_replace(str(orig), str(work), target_res, str(src))
    except RuntimeError:
        # acceptable behavior
        return
    else:
        # function returned a log list. ensure it contains a path-escape entry.
        assert isinstance(log, list)
        joined = "\n".join(str(x) for x in log)
        # accept either the old "Path escape" phrasing or the new "Invalid resource path traversal"
        assert (
            "Path escape detected" in joined
            or "Path escape" in joined
            or "Invalid resource path traversal" in joined
        ), f"Expected path-escape error in log, got: {joined}"
