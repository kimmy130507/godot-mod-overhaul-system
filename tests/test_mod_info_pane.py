import pytest

try:
    import tkinter as tk
except Exception:
    tk = None

from importlib import import_module

mod = import_module("gmos")


def _can_create_tk_root():
    if tk is None:
        return False
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _can_create_tk_root(), reason="tkinter not available or no display"
)
def test_mod_info_pane_update_for_config(tmp_path):
    root = tk.Tk()
    root.withdraw()
    pane = mod.ModInfoPane(root)

    cfg = {
        "Path": str(tmp_path / "my_mod"),
        "Sections": {
            "Metadata": [
                "Name = Test Mod",
                "Version = 1.2.3",
                "Author = Unit Tester",
                "Description = A test mod for the info pane",
            ],
            "Dependencies": ["requires = base_mod, dep2"],
        },
        "_deps_errors": ["missing dependency: dep2"],
    }

    # ensure no exception
    pane.update_for_config(cfg)

    # check widgets populated
    assert pane._widgets["name"].cget("text") == "Test Mod"
    assert pane._widgets["version"].cget("text") == "1.2.3"
    assert pane._widgets["author"].cget("text") == "Unit Tester"
    desc = pane._widgets["desc"].get("1.0", "end").strip()
    assert "A test mod for the info pane" in desc
    deps_text = pane._widgets["deps"].cget("text")
    assert "base_mod" in deps_text and "dep2" in deps_text
    errs = pane._widgets["errors"].cget("text")
    assert "missing dependency" in errs

    # clear pane
    pane.update_for_config(None)
    assert pane._widgets["name"].cget("text") == ""

    # cleanup
    try:
        pane.destroy()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass
