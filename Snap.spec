# -*- mode: python ; coding: utf-8 -*-
import glob as _glob
import os as _os
import shutil as _shutil

from PyInstaller.utils.hooks import collect_data_files

datas = [('ui.html', '.'), ('ui.css', '.'), ('ui.js', '.'), ('icon.png', '.')]
datas += collect_data_files('yt_dlp_ejs')


def _find_tool(name, winget_glob, hint):
    # PATH first, then WinGet's package dir (its PATH edit only reaches future shells)
    p = _shutil.which(name)
    if p:
        return p
    hits = _glob.glob(_os.path.expandvars(winget_glob), recursive=True)
    if not hits:
        raise SystemExit(f'{name} not found — install it ({hint}) before building')
    return hits[0]


_ffmpeg = _find_tool('ffmpeg', r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\ffmpeg.exe',
                     'winget install Gyan.FFmpeg')
_deno = _find_tool('deno', r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\DenoLand.Deno*\**\deno.exe',
                   'winget install DenoLand.Deno')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[(_ffmpeg, '.'), (_os.path.join(_os.path.dirname(_ffmpeg), 'ffprobe.exe'), '.'), (_deno, '.')],
    datas=datas,
    hiddenimports=['pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Snap',
    icon='assets/snap.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Snap',
)
