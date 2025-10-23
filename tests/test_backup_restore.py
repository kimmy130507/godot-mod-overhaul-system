from importlib import import_module
from pathlib import Path

mod = import_module("gmos")


def test_backup_and_restore(tmp_path):
    orig = tmp_path / "orig"
    work = tmp_path / "work"
    orig.mkdir()
    work.mkdir()
    target = work / "data.txt"
    target.write_text("original")
    # simulate replacement that should create .bak
    src = tmp_path / "new.txt"
    src.write_text("replacement")
    mod.atomic_copy_with_single_bak(str(src), str(target))
    bak = Path(str(target) + ".bak")
    # bak of original must exist and contain original content
    assert bak.exists()
    assert bak.read_text() == "original"
    # target should now contain replacement content
    assert target.read_text() == "replacement"
