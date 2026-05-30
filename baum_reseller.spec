# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Collect PySide6 plugin data
datas = [
    ('assets', 'assets'),
]
datas += collect_data_files('PySide6', includes=['*.dll', 'plugins/**/*'])
datas += collect_data_files('matplotlib')
datas += collect_data_files('reportlab')

# Hidden imports required for runtime
hiddenimports = [
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
    'PySide6.QtCharts',
    'matplotlib.backends.backend_qtagg',
    'matplotlib.backends.backend_agg',
    'PIL._imaging',
    'imagehash',
    'keyring.backends.Windows',
    'keyring.backends.fail',
    'pkg_resources.py2_compat',
    'plyer.platforms.win.notification',
    'google.auth.transport.requests',
    'google_auth_oauthlib.flow',
    'googleapiclient.discovery',
    'googleapiclient.http',
    'packaging.version',
    'requests',
    'sqlite3',
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'test', 'unittest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BaumReseller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # add assets/icon.ico here when you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BaumReseller',
)
