import os
from importlib import import_module
from pathlib import Path

mod = import_module("gmos")


def test_suggest_work_root(tmp_path):
    exe = tmp_path / "game" / "bin" / "game.exe"
    # the function only computes suggestion; file need not exist
    suggested = mod.suggest_work_root(str(exe))
    assert suggested.endswith(os.path.join("game", "bin", "mods"))


def test_wizard_autopopulate_and_create(tmp_path):
    # simulate choosing an exe inside a folder and then writing config
    exe_dir = tmp_path / "GameInstall" / "bin"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "game"
    exe.write_text("binary")  # dummy file

    cfgpath = tmp_path / "cfg" / "config.json"
    suggested = mod.suggest_work_root(str(exe))
    # simulate user's OK: create suggested folder and write config
    work_root = Path(suggested)
    # ensure not exists initially
    if work_root.exists():
        # remove for test isolation
        if work_root.is_dir():
            for p in work_root.iterdir():
                p.unlink()
            work_root.rmdir()
    assert not work_root.exists()

    cfg = {"game_exe": str(exe), "work_root": str(work_root)}
    mod.write_config(cfg, str(cfgpath))

    # create the folder as the wizard would do on OK
    os.makedirs(work_root, exist_ok=True)

    # verify config content
    loaded = mod.load_config(str(cfgpath))
    assert loaded["game_exe"] == str(exe)
    assert loaded["work_root"] == str(work_root)
    assert work_root.exists() and work_root.is_dir()
