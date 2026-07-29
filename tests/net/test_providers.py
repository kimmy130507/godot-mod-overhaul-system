# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

from gmos.net.providers import NexusProvider


@pytest.fixture
def mock_requests() -> Generator[MagicMock, None, None]:
    """Mocks requests.get and returns the mock object."""
    with patch("requests.get") as mock_get:
        yield mock_get


def test_nexus_search_integration(mock_requests: MagicMock) -> None:
    """Verifies Nexus API search results map to ModDTO correctly."""
    provider = NexusProvider(api_key="dummy_key")

    # Configure mock response
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {
                "name": "Nexus Godot Mod",
                "mod_id": 12345,
                "summary": "Cool mod",
                "author": "NexusUser",
                "version": "1.5",
                "mod_downloads": 100,
                "endorsement_count": 10,
            }
        ]
    }
    mock_requests.return_value = mock_resp

    results = provider.search("Godot")

    assert len(results) == 1
    assert results[0].name == "Nexus Godot Mod"
    assert results[0].download_url == "nexus://12345"
    assert results[0].downloads == 100


def test_nexus_dependency_scraping_logic(mock_requests: MagicMock) -> None:
    """Verify regex logic extracts IDs from HTML-like strings."""
    provider = NexusProvider(api_key="fake", game_domain="godotengine")

    # Fake HTML content mimicking a Nexus page
    fake_html = """
    <div class="requirements">
        <a href="https://www.nexusmods.com/godotengine/mods/42">Core Lib</a>
        <a href="https://www.nexusmods.com/godotengine/mods/101">Another Mod</a>
        <a href="https://www.nexusmods.com/godotengine/mods/999">Self</a>
    </div>
    """

    # Configure mock to return the HTML
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = fake_html
    mock_requests.return_value = mock_resp

    # We use a dummy ID '999' as "self" to test self-exclusion logic
    deps = provider.resolve_dependencies("999")

    assert "42" in deps
    assert "101" in deps
    assert "999" not in deps
    assert len(deps) == 2


def test_nexus_get_download_url(mock_requests: MagicMock) -> None:
    """Verify Nexus file selection prioritizes 'MAIN' files and returns the URI."""
    provider = NexusProvider(api_key="dummy", game_domain="godotengine")

    # 1. Mock the /files.json endpoint response
    mock_files_resp = MagicMock()
    mock_files_resp.json.return_value = {
        "files": [
            {
                "category_id": 1,
                "file_id": "999",
                "uploaded_timestamp": 12345,
                "file_name": "GodotMod_v1.zip",
            }
        ]
    }

    # 2. Mock the /download_link.json endpoint response
    mock_link_resp = MagicMock()
    mock_link_resp.json.return_value = [
        {"URI": "https://download.nexusmods.com/test_file.zip"}
    ]

    # Apply sequential responses
    mock_requests.side_effect = [mock_files_resp, mock_link_resp]

    url = provider.get_download_url("123")

    assert url == "https://download.nexusmods.com/test_file.zip"
