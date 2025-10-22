def test_mod_loader_importable():
    import importlib
    spec = importlib.util.find_spec("mod_loader")
    assert spec is not None, "mod_loader.py not found on import path"
    mod = importlib.import_module("mod_loader")
    assert mod is not None
