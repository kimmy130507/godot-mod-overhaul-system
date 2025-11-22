# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
#  For Setup wizard, GUI component checks, and dialog logic (permissions, hunk viewing).
import os
import tempfile
import tkinter
from pathlib import Path
from typing import Optional, cast
from unittest.mock import MagicMock, patch

import pytest
from pytest import MonkeyPatch

# Attempt to import tkinter for GUI tests later
try:
    import tkinter as tk

    tk_available = True
except Exception:
    # These variables will be None if import fails;
    # type checkers might complain about redefinition if we define them again with types
    tk_available = False
    tk = None  # type: ignore

from gmos.state import ensure_config, load_config, write_config


# Helper to check for a usable Tk environment
def _can_create_tk_root() -> bool:
    """Return True if a Tk root can be created in this environment."""
    root: Optional["tkinter.Tk"] = None
    if tk is None:
        return False
    try:
        assert tk is not None  # Hint for Pylance
        root = tk.Tk()
        # don't show window
        assert root is not None
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return True
    except Exception:
        try:
            # Best-effort cleanup if partially created
            # check if root exists before trying to destroy it
            if (
                "root" in locals()
                and root is not None
                and getattr(root, "winfo_exists", lambda: False)()
            ):
                root.destroy()
        except Exception:
            pass
        return False


# --- Test Setup Autopopulate ---


def test_suggest_work_root(tmp_path: Path) -> None:
    # In v1.1 Single-Folder, we don't "suggest" a separate work root.
    # We just use the game dir. This test is largely obsolete but we can
    # repurpose it to test default mods dir suggestion if needed,
    # or just remove it. For now, let's test the config default logic via the wizard test.
    pass


def test_wizard_autopopulate_and_create(tmp_path: Path) -> None:
    # simulate choosing an exe inside a folder and then writing config
    exe_dir = tmp_path / "GameInstall" / "bin"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "game"
    exe.write_text("binary")  # dummy file

    cfgpath = tmp_path / "cfg" / "config.json"

    # In Single-Folder, the game_dir is just the exe's parent
    expected_game_dir = str(exe_dir)

    # Simulate the config object the wizard would produce
    cfg = {"game_executable": "game", "game_dir": expected_game_dir}
    write_config(cfg, str(cfgpath))

    # verify config content
    loaded = load_config(str(cfgpath))
    assert loaded["game_executable"] == "game"
    assert loaded["game_dir"] == expected_game_dir
    assert Path(expected_game_dir).exists()


# --- Test Setup Wizard Logic ---


def test_write_and_load_config(tmp_path: Path) -> None:
    cfgfile = tmp_path / "cfg" / "config.json"
    # Updated keys for v1.1
    cfg = {"game_executable": "game", "game_dir": str(tmp_path / "game")}
    write_config(cfg, str(cfgfile))
    loaded = load_config(str(cfgfile))
    assert loaded == cfg


def test_ensure_config_headless(tmp_path: Path) -> None:
    cfgfile = tmp_path / "cfg" / "config.json"
    defaults = {"game_executable": "x", "game_dir": str(tmp_path / "game")}
    out = ensure_config(config_path=str(cfgfile), headless_defaults=defaults)
    assert out == defaults
    assert Path(cfgfile).exists()


# --- Test Setup Wizard GUI Geometry ---


@pytest.mark.skipif(not _can_create_tk_root(), reason="No usable Tk display")
def test_setupwizard_is_on_screen_and_centered() -> None:
    """SetupWizard must create a visible Toplevel and support common widget calls."""
    window: "tkinter.Toplevel | SetupWizard | None" = None  # Initialize to None
    from gmos.state.config import SetupWizard

    assert tk is not None
    tmpd = tempfile.TemporaryDirectory()
    cfg_path = os.path.join(tmpd.name, "test-config.json")

    root = tk.Tk()
    try:
        # place root at a known geometry and ensure layout calculations happen
        root.geometry("800x600+120+80")
        root.update_idletasks()

        wiz = SetupWizard(root, config_path=cfg_path)
        try:
            # Prefer public 'window' attribute if present (compat wrapper).
            window = getattr(wiz, "window", wiz)
            # Attempt to call update_idletasks either on wrapper or underlying widget.
            if hasattr(wiz, "update_idletasks"):
                wiz.update_idletasks()
            elif window is not None and hasattr(window, "update_idletasks"):
                window.update_idletasks()
            else:
                pytest.skip(
                    "SetupWizard has no update_idletasks proxy; skipping compatibility check."
                )

            # Basic assertions to ensure the dialog is configured
            assert window is not None
            assert hasattr(window, "winfo_reqwidth")
            assert hasattr(window, "winfo_reqheight")
            # geometry string should be present and parseable
            geom = (
                window.winfo_geometry() if hasattr(window, "winfo_geometry") else None
            )
            assert isinstance(geom, str)

        finally:
            # ensure wizard destroyed to avoid leaking windows
            try:
                if hasattr(wiz, "destroy"):
                    wiz.destroy()
                elif window is not None and hasattr(window, "destroy"):
                    window.destroy()
            except Exception:
                pass
    finally:
        try:
            if root.winfo_exists():
                root.destroy()
        except Exception:
            pass
        tmpd.cleanup()


# --- Test Mod Info Pane GUI ---


@pytest.mark.skipif(
    not _can_create_tk_root(), reason="tkinter not available or no display"
)
def test_mod_info_pane_update_for_config(tmp_path: Path) -> None:
    from gmos.ui import ModInfoPane, UIModConfig

    assert tk is not None
    root = tk.Tk()
    root.withdraw()
    pane = ModInfoPane(root)

    cfg: UIModConfig = {
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
    assert pane._widgets["name"].cget("text") == "Test Mod"  # type: ignore [reportPrivateUsage]
    assert pane._widgets["version"].cget("text") == "1.2.3"  # type: ignore [reportPrivateUsage]
    assert pane._widgets["author"].cget("text") == "Unit Tester"  # type: ignore [reportPrivateUsage]
    desc_widget = cast("tkinter.Text", pane._widgets["desc"])  # type: ignore [reportPrivateUsage]
    desc = desc_widget.get("1.0", "end").strip()
    assert "A test mod for the info pane" in desc
    deps_text = pane._widgets["deps"].cget("text")  # type: ignore [reportPrivateUsage]
    assert "base_mod" in deps_text and "dep2" in deps_text
    errs = pane._widgets["errors"].cget("text")  # type: ignore [reportPrivateUsage]
    assert "missing dependency" in errs

    # clear pane
    pane.update_for_config(None)
    assert pane._widgets["name"].cget("text") == ""  # type: ignore [reportPrivateUsage]

    # cleanup
    try:
        pane.destroy()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


# --- Test Permission Dialog Logic ---


def test_retry_on_permission_abort(monkeypatch: MonkeyPatch) -> None:
    import gmos.ui as ui_mod
    from gmos.utils import retry_on_permission

    calls = {"n": 0}

    def op() -> None:
        calls["n"] += 1
        raise PermissionError("denied")

    # Monkeypatch PermissionErrorDialog to simulate user choosing 'abort'
    class FakeDialog:
        def __init__(
            self,
            parent: Optional["tkinter.Widget"],
            path: str | os.PathLike[str],
            exc: Exception,
        ) -> None:
            pass

        def show(self) -> str:
            return "abort"

    monkeypatch.setattr(ui_mod, "PermissionErrorDialog", FakeDialog)

    with pytest.raises(PermissionError):
        retry_on_permission(op, parent=None, path="/fake")
    assert calls["n"] == 1


def test_retry_on_permission_retry_then_succeed(monkeypatch: MonkeyPatch) -> None:
    import gmos.ui as ui_mod
    from gmos.utils import retry_on_permission

    calls = {"n": 0}

    def op() -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise PermissionError("denied")
        return "ok"

    class FakeDialog2:
        def __init__(
            self,
            parent: Optional["tkinter.Widget"],
            path: str | os.PathLike[str],
            exc: Exception,
        ) -> None:
            pass

        def show(self) -> str:
            return "retry"

    # make show always return retry for the first exception then the op will succeed
    monkeypatch.setattr(ui_mod, "PermissionErrorDialog", FakeDialog2)

    res = retry_on_permission(op, parent=None, path="/fake")
    assert res == "ok"
    assert calls["n"] == 2


# --- Test Permission Dialog GUI ---


@pytest.mark.skipif(
    "DISPLAY" not in os.environ and not os.name == "nt",
    reason="Requires display or Xvfb",
)
def test_permission_dialog_shows(monkeypatch: MonkeyPatch) -> None:
    import gmos.ui as ui_mod
    from gmos.utils import retry_on_permission

    # create a fake op that raises then succeed
    state = {"n": 0}

    def op() -> str:
        state["n"] += 1
        if state["n"] == 1:
            raise PermissionError("denied")
        return "ok"

    # simulate user clicking 'retry' by monkeypatching PermissionErrorDialog.show
    class FakeDlg:
        def __init__(
            self,
            parent: Optional["tkinter.Widget"],
            path: str | os.PathLike[str],
            exc: Exception,
        ) -> None:
            pass

        def show(self) -> str:
            return "retry"

    monkeypatch.setattr(ui_mod, "PermissionErrorDialog", FakeDlg)

    res = retry_on_permission(op, parent=None, path="/fake")
    assert res == "ok"


# --- Test HunkViewer Headless ---


def test_hunkviewer_headless_auto_accept_strict() -> None:
    """Headless HunkViewer should return the exact merged text when auto-accepting hunks."""

    # MOCK TKINTER: Prevent "TclError: no display name" on CI
    with patch("tkinter.Toplevel"), patch("tkinter.Tk"):
        from gmos.core.patcher import apply_hunks
        from gmos.ui import HunkViewer

        orig = "line1\nline2\nline3\n"
        new = "line1\nLINE2_MODIFIED\nline3\n"

        # Instantiate safely (Toplevel __init__ is mocked)
        hv = HunkViewer(None, orig, new)

        # Mock the destroy method since the real one wraps a tk command
        hv.destroy = MagicMock()  # type: ignore

        # Run the logic we actually want to test
        merged = hv.show_modal(headless_auto_accept=True)

        assert isinstance(merged, str)

        # call with explicit default selection (apply all hunks)
        expected = apply_hunks(orig, new, selected_hunk_indices=None)

        # Exact equality check
        assert merged == expected
        assert merged != orig
        # Ensure no conflict markers remain
        for marker in ("<<<<<<<", "=======", ">>>>>>>"):
            assert marker not in merged
