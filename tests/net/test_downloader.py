# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
from pathlib import Path
from typing import Generator, List, Tuple
from unittest.mock import MagicMock, patch

import pytest

from gmos.net.downloader import DownloadError, DownloadManager


@pytest.fixture
def mock_session() -> Generator[MagicMock, None, None]:
    """Mocks requests.Session and returns the session instance."""
    with patch("requests.Session") as MockSessionCls:
        mock_instance = MockSessionCls.return_value
        yield mock_instance


def test_threaded_download_success(tmp_path: Path, mock_session: MagicMock) -> None:
    """
    Verifies that the download worker correctly writes files and reports progress
    using the new Session-based architecture.
    """
    dest_file = tmp_path / "mod.zip"

    # Mock the Response object
    mock_response = MagicMock()
    mock_response.headers = {"content-length": "10"}
    # iter_content must yield bytes
    mock_response.iter_content.return_value = [b"12345", b"67890"]
    # Support context manager (with response as r:)
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = None
    mock_session.get.return_value = mock_response
    progress_calls: List[Tuple[int, int]] = []

    def on_progress(current: int, total: int) -> None:
        progress_calls.append((current, total))

    manager = DownloadManager()
    future = manager.download_file(
        "http://fake.url/mod.zip", str(dest_file), on_progress
    )
    result_path = future.result()

    # Assertions
    assert result_path == str(dest_file)
    assert dest_file.read_bytes() == b"1234567890"

    mock_session.get.assert_called_once()
    assert len(progress_calls) >= 1
    assert progress_calls[-1] == (10, 10)


def test_threaded_download_failure(tmp_path: Path, mock_session: MagicMock) -> None:
    """
    Verifies error propagation from the worker thread to the main thread.
    """
    dest_file = tmp_path / "fail.zip"

    # Simulate connection error
    mock_session.get.side_effect = Exception("Connection Lost")

    manager = DownloadManager()
    future = manager.download_file("http://bad.url", str(dest_file))

    with pytest.raises(DownloadError) as excinfo:
        future.result()

    assert "Connection Lost" in str(excinfo.value)


def test_threaded_download_cancellation(
    tmp_path: Path, mock_session: MagicMock
) -> None:
    """
    Verifies that the download manager correctly raises a DownloadError
    when the cancel_event is set during file chunk processing.
    """
    dest_file = tmp_path / "cancel.zip"

    mock_response = MagicMock()
    mock_response.headers = {"content-length": "100"}
    mock_response.iter_content.return_value = [b"chunk1", b"chunk2", b"chunk3"]
    mock_response.__enter__.return_value = mock_response
    mock_response.__exit__.return_value = None
    mock_session.get.return_value = mock_response

    cancel_ev = threading.Event()
    manager = DownloadManager()
    cancel_ev.set()  # Trip the event immediately before it iterates

    future = manager.download_file(
        "http://fake.url/cancel.zip", str(dest_file), cancel_event=cancel_ev
    )

    with pytest.raises(DownloadError, match="Cancelled by user"):
        future.result()
