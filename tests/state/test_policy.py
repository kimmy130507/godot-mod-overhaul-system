# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GMOS Integration Test Suite: Conflict Resolution & Load Order
# Verifies that 'Last Mod Wins' and Policy overrides function correctly in a full patch run.

from typing import Any, Dict

import pytest

from gmos.core.patcher import generate_patch_plan, parse_mod_config, run_patcher
from gmos.state import policy

# Check for pyfakefs
try:
    import pyfakefs.fake_filesystem_unittest  # noqa: F401 # type: ignore[reportUnusedImport]

    _pyfakefs_available = True
except ImportError:
    _pyfakefs_available = False


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_last_mod_wins_file_replace(fs: Any) -> None:
    """
    Scenario: Mod A and Mod B both replace 'icon.png'.
    Expected: Mod B (loaded last) overwrites Mod A's version.
    """
    # 1. Setup Environment
    game_dir = "/game"
    mod_a_dir = "/mods/ModA"
    mod_b_dir = "/mods/ModB"

    fs.create_dir(game_dir)
    fs.create_file(f"{game_dir}/icon.png", contents="VANILLA")
    fs.create_file(f"{game_dir}/project.godot")

    # Mod A setup - Include [ModInfo]
    fs.create_file(f"{mod_a_dir}/icon_a.png", contents="MOD_A_ICON")
    fs.create_file(
        f"{mod_a_dir}/mod.mos",
        contents="[ModInfo]\nName=ModA\nVersion=1.0\n[FileReplace]\nres://icon.png = icon_a.png",
    )

    # Mod B setup - Include [ModInfo]
    fs.create_file(f"{mod_b_dir}/icon_b.png", contents="MOD_B_ICON")
    fs.create_file(
        f"{mod_b_dir}/mod.mos",
        contents="[ModInfo]\nName=ModB\nVersion=1.0\n[FileReplace]\nres://icon.png = icon_b.png",
    )

    # 2. Construct Plan (Mod A then Mod B)
    real_cfg_a = parse_mod_config(mod_a_dir)
    real_cfg_b = parse_mod_config(mod_b_dir)
    assert real_cfg_a is not None
    assert real_cfg_b is not None

    plan_a = generate_patch_plan(mod_a_dir, real_cfg_a)
    plan_b = generate_patch_plan(mod_b_dir, real_cfg_b)

    full_plan = plan_a + plan_b

    # 3. Execute Run
    log = run_patcher(game_dir, full_plan)

    # 4. Validate Outcome
    with open(f"{game_dir}/icon.png", "r") as f:
        content = f.read()

    assert content == "MOD_B_ICON", "Mod B should have overwritten Mod A"
    assert any("Applying FileReplace from ModA" in line for line in log)
    assert any("Applying FileReplace from ModB" in line for line in log)


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_policy_override_wins(fs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Scenario: Mod A and Mod B replace 'script.gd'. Policy says 'ModA' wins.
    Expected: Mod A's version persists, even if Mod B is loaded last.
    """
    game_dir = "/game"
    mod_a_dir = "/mods/ModA"
    mod_b_dir = "/mods/ModB"

    fs.create_file(f"{game_dir}/script.gd", contents="print('vanilla')")
    fs.create_file(f"{game_dir}/project.godot")
    # Mod A - Include [ModInfo]
    fs.create_file(f"{mod_a_dir}/patch.gd", contents="print('MOD_A')")
    fs.create_file(
        f"{mod_a_dir}/mod.mos",
        contents="[ModInfo]\nName=ModA\nVersion=1.0\n[FileReplace]\nres://script.gd = patch.gd",
    )

    # Mod B - Include [ModInfo]
    fs.create_file(f"{mod_b_dir}/patch.gd", contents="print('MOD_B')")
    fs.create_file(
        f"{mod_b_dir}/mod.mos",
        contents="[ModInfo]\nName=ModB\nVersion=1.0\n[FileReplace]\nres://script.gd = patch.gd",
    )

    # Mock Policy to favor ModA
    def mock_rules() -> Dict[str, str]:
        return {"script.gd": "ModA"}

    monkeypatch.setattr(policy, "load_file_rules", mock_rules)

    real_cfg_a = parse_mod_config(mod_a_dir)
    real_cfg_b = parse_mod_config(mod_b_dir)
    assert real_cfg_a and real_cfg_b

    plan_a = generate_patch_plan(mod_a_dir, real_cfg_a)
    plan_b = generate_patch_plan(mod_b_dir, real_cfg_b)

    full_plan = plan_a + plan_b

    log = run_patcher(game_dir, full_plan)

    with open(f"{game_dir}/script.gd", "r") as f:
        content = f.read()

    assert (
        content == "print('MOD_A')"
    ), "Policy winner ModA should overwrite/prevent ModB"
    assert not any(
        "Applying FileReplace from ModB" in line for line in log
    ), "ModB should have been filtered out by policy"


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_script_variable_merge(fs: Any) -> None:
    """
    Scenario: Mod A changes var 'speed', Mod B changes var 'gravity'.
    Expected: Both changes are applied to the target script (Merge).
    """
    game_dir = "/game"
    mod_a = "/mods/SpeedMod"
    mod_b = "/mods/GravityMod"

    # Vanilla Script
    vanilla_content = (
        "extends Node\n"
        "var speed = 10\n"
        "var gravity = 9.8\n"
        "func _ready():\n"
        "    pass\n"
    )
    fs.create_file(f"{game_dir}/player.gd", contents=vanilla_content)
    fs.create_file(f"{game_dir}/project.godot")
    # Mod A - Include [ModInfo]
    fs.create_file(f"{mod_a}/speed.gd", contents="var speed = 500\n")
    fs.create_file(
        f"{mod_a}/mod.mos",
        contents="[ModInfo]\nName=SpeedMod\nVersion=1.0\n[VariablePatch]\nres://player.gd::speed = speed.gd::speed; mode=replace",
    )

    # Mod B - Include [ModInfo]
    fs.create_file(f"{mod_b}/grav.gd", contents="var gravity = 0\n")
    fs.create_file(
        f"{mod_b}/mod.mos",
        contents="[ModInfo]\nName=GravityMod\nVersion=1.0\n[VariablePatch]\nres://player.gd::gravity = grav.gd::gravity; mode=replace",
    )

    cfg_a = parse_mod_config(mod_a)
    cfg_b = parse_mod_config(mod_b)
    assert cfg_a and cfg_b

    plan = generate_patch_plan(mod_a, cfg_a) + generate_patch_plan(mod_b, cfg_b)

    run_patcher(game_dir, plan)

    with open(f"{game_dir}/player.gd", "r") as f:
        final_code = f.read()

    assert "var speed = 500" in final_code
    assert "var gravity = 0" in final_code
    assert "extends Node" in final_code
