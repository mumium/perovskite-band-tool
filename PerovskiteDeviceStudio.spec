# -*- mode: python ; coding: utf-8 -*-
import os


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets'), ('vendor/simsalabim', 'vendor/simsalabim'), ('device_structures', 'device_structures')],
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
    icon=['assets/logo.ico'],
    exclude_binaries=folder_build,
)

if folder_build:
    collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                         name='PerovskiteDeviceStudio-portable')
