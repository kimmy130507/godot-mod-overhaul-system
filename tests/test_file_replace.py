from importlib import import_module


def test_file_replace_binary_and_text(tmp_path):
    mod = import_module("gmos")
    orig = tmp_path / "orig"
    work = tmp_path / "work"
    mod_dir = tmp_path / "mod"
    orig.mkdir()
    work.mkdir()
    mod_dir.mkdir()
    # create original binary and script
    img = orig / "res://assets/logo.png".replace("res://", "")
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x00\x01OLD")
    script = orig / "res://scripts/a.gd".replace("res://", "")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("print('old')")

    # mod provides replacements
    patch_img = mod_dir / "patches/logo_fixed.png"
    patch_img.parent.mkdir(parents=True, exist_ok=True)
    patch_img.write_bytes(b"\x00\x01NEW")
    patch_script = mod_dir / "patches/a.gd"
    patch_script.parent.mkdir(parents=True, exist_ok=True)
    patch_script.write_text("print('new')")

    # call patch_file_replace (imported name may vary)
    # adapt call depending on your function name
    log = mod.patch_file_replace(
        str(orig), str(work), "res://assets/logo.png", str(patch_img)
    )
    assert any("SUCCESS" in line for line in log)

    log2 = mod.patch_file_replace(
        str(orig), str(work), "res://scripts/a.gd", str(patch_script)
    )
    assert any("SUCCESS" in line for line in log2)

    # verify work tree files exist
    assert (work / "assets/logo.png").exists()
    assert (work / "scripts/a.gd").exists()
