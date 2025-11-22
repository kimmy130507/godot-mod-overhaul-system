# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# Dependency resolution, cycles, and sorting tests.

from pathlib import Path
from typing import Dict, List, Optional, Union

from gmos.core.patcher import resolve_mod_dependencies
from gmos.utils import (
    ModConfig,  # type: ignore [reportPrivateUsage]
    _get_mod_name_from_config,
)


def cfg_for(tmp_path: Path, name: str, deps: Optional[List[str]] = None) -> ModConfig:
    """Helper to create a dummy mod config for dependency testing."""
    d: Path = tmp_path / name
    d.mkdir()
    # Type sections to match the ModConfig TypedDict
    sections: Dict[str, Union[List[str], Dict[str, str]]] = {}
    if deps:
        sections["Dependencies"] = [f"requires = {', '.join(deps)}"]
    # This dict literal is compatible with the ModConfig TypedDict
    return {"Path": str(d), "Sections": sections}


def test_simple_dependency_order(tmp_path: Path) -> None:
    a = cfg_for(tmp_path, "A", deps=["B"])
    b = cfg_for(tmp_path, "B", deps=[])
    ordered, errors = resolve_mod_dependencies([a, b])
    # B must come before A
    names = [_get_mod_name_from_config(c) for c in ordered]
    assert names == ["B", "A"]
    assert errors == {}


def test_missing_dependency_reported(tmp_path: Path) -> None:
    a = cfg_for(tmp_path, "A", deps=["Missing"])
    _ordered, errors = resolve_mod_dependencies([a])
    assert "A" in errors
    assert any("missing dependency" in e for e in errors["A"])


def test_cycle_detected(tmp_path: Path) -> None:
    a = cfg_for(tmp_path, "A", deps=["B"])
    b = cfg_for(tmp_path, "B", deps=["A"])
    _ordered, errors = resolve_mod_dependencies([a, b])
    # New behavior: cycle is broken by heuristic, so list is full size
    assert len(_ordered) == 2

    # At least one mod should report the cycle warning
    has_cycle_warning = False
    all_errs: List[List[str]] = list(errors.values())
    for err_list in all_errs:
        if any("Dependency cycle detected" in e for e in err_list):
            has_cycle_warning = True
            break
    assert has_cycle_warning
