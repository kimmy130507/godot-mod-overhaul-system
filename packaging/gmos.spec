from PyInstaller.utils.hooks import collect_data_files
a = Analysis(
    ['gmos.py'],
    pathex=[],
    binaries=[],
    datas=collect_data_files('.', include_py_files=False),
    hiddenimports=[],
    hookspath=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    name='gmos',
    console=False,
)
