import os
import stat
import sys
import time
from importlib import import_module

mod = import_module("gmos")


def test_file_replace_and_launch_headless(tmp_path):
    """
    Cross-platform headless E2E:
    - create an 'orig' tree and 'work' tree
    - apply FileReplace
    - create a small Python launcher that writes a file and exit
    - launch it with _safe_spawn using the current Python interpreter
    """
    orig = tmp_path / "orig"
    work = tmp_path / "work"
    orig.mkdir()
    work.mkdir()

    # create asset in orig and copy to work
    assets_dir = orig / "assets"
    assets_dir.mkdir()
    orig_logo = assets_dir / "logo.png"
    orig_logo.write_bytes(b"ORIGINAL")
    (work / "assets").mkdir()
    (work / "assets" / "logo.png").write_bytes(orig_logo.read_bytes())

    # mod: replacement file inside mod dir
    mod_dir = tmp_path / "mod"
    mod_dir.mkdir()
    patch = mod_dir / "logo_new.png"
    patch.write_bytes(b"REPLACED")

    # call patch_file_replace
    target_res = "res://assets/logo.png"
    log = mod.patch_file_replace(str(orig), str(work), target_res, str(patch))
    assert isinstance(log, list)
    assert any("SUCCESS" in str(x) for x in log)

    # verify replacement
    replaced = (work / "assets" / "logo.png").read_bytes()
    assert replaced == b"REPLACED"

    # create portable launcher: a small Python script that writes a marker file
    bin_dir = work / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "launcher.py"
    out_file = work / "launcher_ran.txt"

    launcher.write_text(
        f"""import pathlib
path=pathlib.Path({out_file!r})
path.write_text('launched', encoding='utf-8')
"""
    )

    # On Unix make it executable (not required on Windows)
    try:
        st = os.stat(launcher)
        os.chmod(launcher, st.st_mode | stat.S_IEXEC)
    except Exception:
        pass

    # launch using the current Python interpreter for portability
    cmd = [sys.executable, str(launcher)]
    p = mod._safe_spawn(cmd, cwd=str(work))
    p.wait(timeout=10)
    # give a short moment for filesystem to settle
    time.sleep(0.05)

    assert out_file.exists()
    assert out_file.read_text(encoding="utf-8") == "launched"
