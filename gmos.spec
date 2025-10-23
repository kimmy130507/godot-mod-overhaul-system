# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

block_cipher = None

# Build the single-file application from the repository root module gmos.py
a = Analysis(
    ['gmos.py'],         
    pathex=['.'],        # ensure repo root is in search path
    binaries=[],
    datas=collect_data_files('.', include_py_files=False),
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name='gmos',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='gmos',
)
