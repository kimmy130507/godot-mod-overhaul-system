# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from unittest.mock import patch

from gmos.state.config import (
    GlobalConfig,
    InstanceMetadata,
    load_global_config,
    load_instance_config_dict,
    save_global_config,
    save_instance_config_dict,
)


def test_global_config_sqlite_persistence(tmp_path: Path) -> None:
    """Verify GlobalConfig accurately writes and fetches to the underlying SQLite schema."""
    db_path = tmp_path / "gmos_data"

    with patch("gmos.state.config.get_app_data_path", return_value=str(db_path)):
        cfg = GlobalConfig()
        cfg.nexus_api_key = "123456"
        cfg.instances["inst1"] = InstanceMetadata(
            id="inst1", name="Game1", path="/fake"
        )

        save_global_config(cfg)

        loaded = load_global_config()
        assert loaded.nexus_api_key == "123456"
        assert "inst1" in loaded.instances
        assert loaded.instances["inst1"].path == "/fake"


def test_instance_config_json_persistence(tmp_path: Path) -> None:
    """Verify instance settings bind correctly to local game directories."""
    cfg_path = tmp_path / "instance.json"

    data = {"game_executable": "custom.exe", "mod_website": "https://nexusmods.com"}

    save_instance_config_dict(data, str(cfg_path))

    loaded = load_instance_config_dict(str(cfg_path))
    assert loaded["game_executable"] == "custom.exe"
    assert loaded["mod_website"] == "https://nexusmods.com"
