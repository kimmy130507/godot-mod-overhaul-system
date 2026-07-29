# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from unittest.mock import MagicMock, patch

from gmos.net.p2p import P2PClient, P2PHost


def test_p2p_host_start_stop() -> None:
    """Verify the P2PHost starts the TCPServer and correctly cleans up its thread."""
    session = MagicMock()
    # Bind to port 0 to let the OS assign an available port (avoids CI port collisions)
    host = P2PHost(session, port=0)

    host.start()
    assert host.httpd is not None
    assert host.thread is not None
    assert host.thread.is_alive()

    host.stop()
    host.thread.join(timeout=2)
    assert not host.thread.is_alive()


@patch("gmos.net.p2p.requests.get")
@patch("gmos.net.p2p.LobbyManifest.from_json")
def test_p2p_client_connect(mock_from_json: MagicMock, mock_get: MagicMock) -> None:
    """Verify P2PClient correctly fetches the remote manifest and triggers sync."""
    mock_session = MagicMock()
    mock_session.mods = []
    client = P2PClient(mock_session)

    mock_response = MagicMock()
    mock_response.text = '{"dummy": "json"}'
    mock_get.return_value = mock_response

    mock_manifest = MagicMock()
    mock_manifest.host_name = "TestHost"
    mock_manifest.mods = []
    mock_from_json.return_value = mock_manifest

    with patch.object(client, "sync_mods") as mock_sync:
        client.connect("127.0.0.1", 27027)
        # Should parse JSON and hand off to sync_mods
        mock_sync.assert_called_once_with(mock_manifest, "http://127.0.0.1:27027")
