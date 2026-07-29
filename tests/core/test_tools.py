# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GMOS Tooling Manager Test Suite
# Verifies: External tool acquisition, GitHub API integration, and fallback logic.

import json
import os
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from gmos.core import tools
from gmos.core.tools import ToolManager

# Sample JSON response mimicking GitHub's Release API
GITHUB_RELEASE_JSON = {
    "assets": [
        {
            "name": "GDRE_Tools-v2.2.0-windows.zip",
            "browser_download_url": "https://example.com/download/win.zip",
        },
        {
            "name": "GDRE_Tools-v2.2.0-linux.zip",
            "browser_download_url": "https://example.com/download/linux.zip",
        },
        {
            "name": "GDRE_Tools-v2.2.0-macos.zip",
            "browser_download_url": "https://example.com/download/mac.zip",
        },
        {
            "name": "source_code.zip",
            "browser_download_url": "https://example.com/source.zip",
        },
    ]
}


@pytest.fixture
def mock_app_data(tmp_path: str) -> Generator[str, None, None]:
    """Mock get_app_data_path to use a temporary directory."""
    with patch("gmos.core.tools.get_app_data_path", return_value=str(tmp_path)):
        yield str(tmp_path)


@pytest.fixture
def mock_github_response() -> Generator[MagicMock, None, None]:
    """Provides a basic mock response for urllib.request.urlopen."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(GITHUB_RELEASE_JSON).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    yield mock_resp


def test_get_latest_gdre_url_windows(mock_github_response: MagicMock) -> None:
    """Verify Windows asset resolution from GitHub JSON."""
    manager = ToolManager()
    with patch("urllib.request.urlopen", return_value=mock_github_response):
        url = manager._get_latest_gdre_url("Windows")  # type: ignore [reportPrivateUsage]
        assert url == "https://example.com/download/win.zip"


def test_get_latest_gdre_url_linux(mock_github_response: MagicMock) -> None:
    """Verify Linux asset resolution from GitHub JSON."""
    manager = ToolManager()
    with patch("urllib.request.urlopen", return_value=mock_github_response):
        url = manager._get_latest_gdre_url("Linux")  # type: ignore [reportPrivateUsage]
        assert url == "https://example.com/download/linux.zip"


def test_get_latest_gdre_url_not_found(mock_github_response: MagicMock) -> None:
    """Verify error handling when no matching asset is found."""
    manager = ToolManager()

    # Override response to return valid JSON but with no matching assets
    empty_json = {"assets": [{"name": "useless_file.txt", "url": "..."}]}
    mock_github_response.read.return_value = json.dumps(empty_json).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=mock_github_response):
        with pytest.raises(RuntimeError, match="No asset found"):
            manager._get_latest_gdre_url("Windows")  # type: ignore [reportPrivateUsage]


def test_get_latest_gdre_url_api_error(mock_github_response: MagicMock) -> None:
    """Verify error propagation if API returns non-200."""
    manager = ToolManager()
    mock_github_response.status = 404

    with patch("urllib.request.urlopen", return_value=mock_github_response):
        with pytest.raises(RuntimeError, match="GitHub API status: 404"):
            manager._get_latest_gdre_url("Windows")  # type: ignore [reportPrivateUsage]


# --- Installation Flow Tests ---


@pytest.fixture
def mock_extraction_env() -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """Setup common mocks for urlretrieve, ZipFile, and os.remove."""
    with (
        patch("urllib.request.urlretrieve") as mock_retrieve,
        patch("zipfile.ZipFile") as mock_zip,
        patch("os.remove"),
    ):  # Prevent actual deletion

        # Mock extraction side-effect
        def side_effect_extract(path: str) -> None:
            os.makedirs(path, exist_ok=True)
            bin_path = os.path.join(path, "GDRE Tools.exe")
            with open(bin_path, "w") as f:
                f.write("mock binary content")

        mock_zip.return_value.__enter__.return_value.extractall.side_effect = (
            side_effect_extract
        )
        yield mock_retrieve, mock_zip


def test_install_tool_success_dynamic(
    mock_app_data: str, mock_extraction_env: tuple[MagicMock, MagicMock]
) -> None:
    """Test that install_tool uses the dynamic URL when available."""
    manager = ToolManager()
    mock_retrieve, _ = mock_extraction_env
    dynamic_url = "https://dynamic.url/tool.zip"

    with (
        patch.object(
            manager, "_get_latest_gdre_url", return_value=dynamic_url
        ) as mock_get_url,
        patch.object(manager, "_get_system_key", return_value="Windows"),
    ):

        path = manager.install_tool("gdre_tools")

        assert "GDRE Tools.exe" in path
        mock_get_url.assert_called_once()
        assert mock_retrieve.call_args[0][0] == dynamic_url


def test_install_tool_fallback_on_api_failure(
    mock_app_data: str, mock_extraction_env: tuple[MagicMock, MagicMock]
) -> None:
    """Test that install_tool falls back to the manifest URL if the dynamic fetch fails."""
    manager = ToolManager()
    mock_retrieve, _ = mock_extraction_env

    with (
        patch.object(
            manager, "_get_latest_gdre_url", side_effect=RuntimeError("API Down")
        ),
        patch.object(manager, "_get_system_key", return_value="Windows"),
    ):

        path = manager.install_tool("gdre_tools")

        assert "GDRE Tools.exe" in path

        # Verify fallback URL usage
        expected_fallback = tools.TOOLS_MANIFEST["gdre_tools"]["urls"]["Windows"]
        actual_url = mock_retrieve.call_args[0][0]
        assert actual_url == expected_fallback


def test_install_unknown_tool_raises_error() -> None:
    """Verify ValueError for unknown tool IDs."""
    manager = ToolManager()
    with pytest.raises(ValueError, match="Unknown tool"):
        manager.install_tool("non_existent_tool")
