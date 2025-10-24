from importlib import import_module

mod = import_module("gmos")


def cfg_for(tmp_path, name, deps=None):
    d = tmp_path / name
    d.mkdir()
    sections = {}
    if deps:
        sections["Dependencies"] = [f"requires = {', '.join(deps)}"]
    return {"Path": str(d), "Sections": sections}


def test_simple_dependency_order(tmp_path):
    a = cfg_for(tmp_path, "A", deps=["B"])
    b = cfg_for(tmp_path, "B", deps=[])
    ordered, errors = mod.resolve_mod_dependencies([a, b])
    # B must come before A
    names = [mod._get_mod_name_from_config(c) for c in ordered]
    assert names == ["B", "A"]
    assert errors == {}


def test_missing_dependency_reported(tmp_path):
    a = cfg_for(tmp_path, "A", deps=["Missing"])
    ordered, errors = mod.resolve_mod_dependencies([a])
    assert "A" in errors
    assert any("missing dependency" in e for e in errors["A"])


def test_cycle_detected(tmp_path):
    a = cfg_for(tmp_path, "A", deps=["B"])
    b = cfg_for(tmp_path, "B", deps=["A"])
    ordered, errors = mod.resolve_mod_dependencies([a, b])
    # no valid full order; both A and B should be in errors with cycle message
    assert "A" in errors and "B" in errors
    assert any("dependency cycle" in msg for msg in errors["A"])
