# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gmos.core.session import GmosSession
from gmos.utils import ModConfig


@pytest.fixture
def mock_session(tmp_path: Path) -> GmosSession:
    # Setup fake game/mods directories
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    return GmosSession(str(game_dir), str(mods_dir))


def test_refresh_mods_discovery(mock_session: GmosSession) -> None:
    """Verify session finds and parses mods from disk."""
    # Create a dummy mod using pathlib correctly
    mod_path = Path(mock_session.mods_dir) / "TestMod"
    mod_config = ModConfig(Name="TestMod", Path=str(mod_path))
    mod_path.mkdir()

    # Mock the parser to return our config
    with patch("gmos.core.session.parse_mod_config", return_value=mod_config):
        with patch("gmos.core.session.validate_mod_config", return_value=(True, None)):
            # Consume the generator
            list(mock_session.refresh_mods())

    assert len(mock_session.mods) == 1
    assert mock_session.mods[0].name == "TestMod"
    assert mock_session.mods[0].is_valid


def test_apply_changes_flow(mock_session: GmosSession) -> None:
    """Verify the session calculates a plan and calls the patcher."""
    # Add a fake enabled mod
    mock_mod = MagicMock()
    mock_mod.is_enabled = True
    mock_mod.config = {"Name": "MyMod"}
    mock_session.mods = [mock_mod]

    # Mock the patcher internals
    with patch(
        "gmos.core.patcher.generate_patch_plan",
        return_value=[("MyMod", "FileReplace", ("a", "b"))],
    ):
        with patch(
            "gmos.core.patcher.run_patcher", return_value=["Success"]
        ) as mock_run:

            # Run application
            logs = list(mock_session.apply_changes())

            # Assert run_patcher was called with the plan
            mock_run.assert_called_once()
            args, _ = mock_run.call_args
            # args[1] is the plan list
            assert len(args[1]) == 1
            assert "Success" in logs


def test_sandbox_toggle(mock_session: GmosSession) -> None:
    """Verify session delegates sandbox toggling correctly."""
    with patch("gmos.core.session.SandboxInjector") as MockInjector:
        instance = MockInjector.return_value

        # Test Enable (Not Injected -> Inject)
        instance.is_injected.return_value = False
        assert mock_session.toggle_sandbox() is True
        instance.inject.assert_called_once()

        # Reset mock for next assertion
        instance.reset_mock()

        # Test Disable (Injected -> Remove)
        instance.is_injected.return_value = True
        assert mock_session.toggle_sandbox() is False
        instance.remove.assert_called_once()
