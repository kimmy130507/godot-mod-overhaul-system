from importlib import import_module
from pathlib import Path

mod = import_module("gmos")


def test_write_and_load_config(tmp_path):
    cfgfile = tmp_path / "cfg" / "config.json"
    cfg = {"game_exe": "/usr/bin/game", "work_root": str(tmp_path / "work")}
    mod.write_config(cfg, str(cfgfile))
    loaded = mod.load_config(str(cfgfile))
    assert loaded == cfg


def test_ensure_config_headless(tmp_path):
    cfgfile = tmp_path / "cfg" / "config.json"
    defaults = {"game_exe": "x", "work_root": str(tmp_path / "w")}
    out = mod.ensure_config(config_path=str(cfgfile), headless_defaults=defaults)
    assert out == defaults
    assert Path(cfgfile).exists()
