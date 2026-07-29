# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import uuid
from typing import Any
from unittest.mock import patch

from gmos.state.config import (
    GlobalConfig,
    InstanceMetadata,
    load_global_config,
    save_global_config,
)


def test_global_config_lifecycle(tmp_path: Any) -> None:
    """Verifies Global Registry persistence using real tmp fs (safer for sqlite/db)."""

    # Mock get_app_data_path to return our temp path
    with patch("gmos.state.config.get_app_data_path", return_value=str(tmp_path)):
        game_a = str(tmp_path / "games" / "Brotato")
        os.makedirs(game_a, exist_ok=True)

        cfg = load_global_config()
        assert len(cfg.instances) == 0

        uid_a = str(uuid.uuid4())
        meta_a = InstanceMetadata(id=uid_a, name="Brotato", path=game_a)
        cfg.instances[uid_a] = meta_a
        cfg.default_instance_id = uid_a

        save_global_config(cfg)

        cfg_new = load_global_config()
        assert len(cfg_new.instances) == 1
        assert cfg_new.instances[uid_a].name == "Brotato"


def test_self_healing_startup(tmp_path: Any) -> None:
    """Resilience Test: Verify App repairs stale sandbox configs on startup."""

    with patch("gmos.state.config.get_app_data_path", return_value=str(tmp_path)):
        game_dir = str(tmp_path / "games" / "CorruptedGame")
        os.makedirs(game_dir, exist_ok=True)

        # Game folder exists, but 'gmos_sandbox.gd' is MISSING.
        assert not os.path.exists(os.path.join(game_dir, "gmos_sandbox.gd"))

        meta = InstanceMetadata(id="bad_id", name="Corrupted", path=game_dir)
        cfg = GlobalConfig(instances={"bad_id": meta})

    # Patch SandboxInjector inside gmos.ui.app (where App imports it)
    with patch("gmos.ui.app.SandboxInjector") as MockInjectorCls:
        mock_injector_instance = MockInjectorCls.return_value
        # Simulate that the sandbox IS injected in config
        mock_injector_instance.is_injected.return_value = True

        # Prevent App from launching GUI
        with patch("gmos.ui.app.App.__init__", return_value=None):
            from gmos.ui.app import App

            app = App()
            app.global_cfg = cfg

            # Invoke the logic under test
            app._heal_instances()  # type: ignore

        # The App logic should have called injector.remove() because gmos_sandbox.gd is missing
        mock_injector_instance.remove.assert_called_once()
