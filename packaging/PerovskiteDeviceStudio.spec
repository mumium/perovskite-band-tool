# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent


a = Analysis(
    [str(PROJECT_ROOT / 'run.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[(str(PROJECT_ROOT / 'assets'), 'assets'),
           (str(PROJECT_ROOT / 'vendor' / 'simsalabim'), 'vendor/simsalabim'),
           (str(PROJECT_ROOT / 'device_structures'), 'device_structures')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['IPython', 'notebook'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

folder_build = os.environ.get('PDS_BUILD_LAYOUT') == 'folder'

exe = EXE(
    pyz,
    a.scripts,
    *([] if folder_build else [a.binaries, a.datas]),
    [],
    name='PerovskiteDeviceStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(PROJECT_ROOT / 'assets' / 'logo.ico')],
    exclude_binaries=folder_build,
)

if folder_build:
    collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                         name='PerovskiteDeviceStudio-portable')
