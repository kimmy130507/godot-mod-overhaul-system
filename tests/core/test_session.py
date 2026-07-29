# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import zipfile
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from gmos.core.session import GmosSession, SecurityScanError
from gmos.utils import ModConfig


@pytest.fixture
def mock_session(tmp_path: Path) -> GmosSession:
    # Setup fake game/mods directories
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    return GmosSession(str(game_dir), str(mods_dir))


@pytest.fixture
def mock_mod_discovery() -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """Mocks the mod parsing and validation subsystem."""
    with (
        patch("gmos.core.session.parse_mod_config") as mock_parse,
        patch("gmos.core.session.validate_mod_config") as mock_validate,
    ):
        mock_validate.return_value = (True, None)
        yield mock_parse, mock_validate


def test_refresh_mods_discovery(
    mock_session: GmosSession, mock_mod_discovery: tuple[MagicMock, MagicMock]
) -> None:
    """Verify session finds and parses mods from disk."""
    mock_parse, _ = mock_mod_discovery
    # Create a dummy mod using pathlib correctly
    mod_path = Path(mock_session.mods_dir) / "TestMod"
    mod_config = ModConfig(Name="TestMod", Path=str(mod_path))
    mod_path.mkdir()

    mock_parse.return_value = mod_config

    # Consume the generator
    list(mock_session.refresh_mods())

    assert len(mock_session.mods) == 1
    assert mock_session.mods[0].name == "TestMod"
    assert mock_session.mods[0].is_valid


@pytest.fixture
def mock_patcher_ops() -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """Mocks the core patcher operations."""
    with (
        patch("gmos.core.patcher.generate_patch_plan") as mock_plan,
        patch("gmos.core.patcher.run_patcher") as mock_run,
    ):
        yield mock_plan, mock_run


def test_apply_changes_flow(
    mock_session: GmosSession, mock_patcher_ops: tuple[MagicMock, MagicMock]
) -> None:
    """Verify the session calculates a plan and calls the patcher."""
    mock_plan, mock_run = mock_patcher_ops
    # Add a fake enabled mod
    mock_mod = MagicMock()
    mock_mod.is_enabled = True
    mock_mod.config = {"Name": "MyMod"}
    mock_session.mods = [mock_mod]

    # Configure mocks
    mock_plan.return_value = [("MyMod", "FileReplace", ("a", "b"))]
    mock_run.return_value = ["Success"]

    # Run application
    logs = list(mock_session.apply_changes())

    # Assert run_patcher was called with the plan
    mock_run.assert_called_once()
    args, _ = mock_run.call_args
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


def test_install_zip_slip_prevention(mock_session: GmosSession, tmp_path: Path) -> None:
    """
    Security Test: Verify that archives with path traversal ('..') are rejected.
    Addresses Roadmap Item: Archive Extraction Security (Zip Slip).
    """
    malicious_zip = tmp_path / "evil.zip"

    # Create a zip file containing an entry that traverses up directories
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        # We use ZipInfo to bypass some default sanitization in write methods
        # attempting to place a file in the parent directory of the extraction root.
        evil_file = zipfile.ZipInfo("../evil_payload.exe")
        zf.writestr(evil_file, "malicious binary content")

        # Add a valid file too, to ensure it doesn't fail on empty zip
        zf.writestr("valid_mod.txt", "safe content")

    # The session should detect the traversal and raise SecurityScanError
    with pytest.raises(SecurityScanError) as exc_info:
        mock_session.install_mod_from_archive(str(malicious_zip))

    # Verify the error message mentions the specific attack vector
    # Note: The code logs "Zip Slip" to the console but raises "Security Violation" in the exception
    assert "Security Violation" in str(exc_info.value)
    assert "outside the extraction directory" in str(exc_info.value)
