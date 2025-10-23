def test_gmos_importable():
    import importlib

    spec = importlib.util.find_spec("gmos")
    assert spec is not None, "gmos.py not found on import path"
    mod = importlib.import_module("gmos")
    assert mod is not None
