# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.building.build_main import Analysis, PYZ, EXE, BUNDLE
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = []
binaries = []
hiddenimports = []

# Collect all required packages that rely on external data/binaries
for pkg in ['ttkbootstrap', 'tkinterdnd2']:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

# Define the new asset root
ASSET_ROOT = os.path.join('gmos', 'assets')

# Recursively include all assets
if os.path.exists(ASSET_ROOT):
    datas.append((ASSET_ROOT, 'gmos/assets'))

hiddenimports.append('gmos.ui')

# Platform-specific icon
icon_file = None
if sys.platform == "darwin":
    icon_file = os.path.join(ASSET_ROOT, 'gmos.icns')
elif sys.platform.startswith("win"):
    icon_file = os.path.join(ASSET_ROOT, 'gmos.ico')

a = Analysis(
    ['gmos/__main__.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,   
    a.zipfiles,
    a.datas,
    [],
    exclude_binaries=False,
    name='GMOS',
    debug=False,        
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # GUI mode
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='GMOS.app',
        icon=icon_file,
        bundle_identifier='io.github.kimmy130507.gmos',
        info_plist={
            'CFBundleName': 'GMOS',
            'CFBundleDisplayName': 'Godot Mod Overhaul System',
            'CFBundleShortVersionString': '2.0.0',
            'CFBundleVersion': '2.0.0',
            'NSHighResolutionCapable': 'True'
        },
    )