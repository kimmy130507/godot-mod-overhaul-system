import textwrap
from importlib import import_module

mod = import_module("gmos")


def write_file(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding="utf-8")


def test_manifest_valid_file_replace(tmp_path):
    mdir = tmp_path / "my_mod"
    mdir.mkdir()
    # create a replacement file inside mod
    (mdir / "patches").mkdir()
    (mdir / "patches" / "file.tscn").write_text("dummy")
    content = textwrap.dedent(
        """
        [FileReplace]
        res://scenes/main.tscn = patches/file.tscn

        [Dependencies]
        requires = base_mod
        """
    ).strip()
    mf = mdir / "mod.mos"
    write_file(mf, content)
    ok, errors = mod.validate_mod_config(str(mf))
    assert ok is True
    assert errors == []


def test_manifest_invalid_traversal(tmp_path):
    mdir = tmp_path / "badmod"
    mdir.mkdir()
    (mdir / "patches").mkdir()
    content = textwrap.dedent(
        """
        [FileReplace]
        res://scenes/main.tscn = ../outside/evil.tscn
        """
    ).strip()
    mf = mdir / "mod.mos"
    write_file(mf, content)
    ok, errors = mod.validate_mod_config(str(mf))
    assert ok is False
    assert any(
        "outside mod" in e or "replacement path outside mod" in e for e in errors
    )


def test_manifest_disallowed_section(tmp_path):
    mdir = tmp_path / "x"
    mdir.mkdir()
    content = textwrap.dedent(
        """
        [ScriptReplace]
        something = x
        """
    ).strip()
    mf = mdir / "mod.mos"
    write_file(mf, content)
    ok, errors = mod.validate_mod_config(str(mf))
    assert ok is False
    assert any("disallowed section" in e for e in errors)
