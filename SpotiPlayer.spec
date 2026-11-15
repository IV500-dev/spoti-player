# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for "Spoti Player.exe" — one windowed, single-file exe.

Run it with:   python -m PyInstaller --clean --noconfirm SpotiPlayer.spec
(or just double-click build_exe.bat)

libVLC is intentionally NOT bundled: the exe talks to the VLC the user already
has installed (or a portable "vlc\" folder placed next to the exe).
"""

# yt-dlp loads its extractors dynamically, so PyInstaller has to be told about
# every module - otherwise the full-song download would only fail in the exe.
try:
    from PyInstaller.utils.hooks import collect_submodules

    yt_dlp_hidden = collect_submodules("yt_dlp")
except Exception:
    yt_dlp_hidden = []

block_cipher = None

# the built-in anime wallpapers for the mini bar's radio mode
a_datas = [("assets/wallpaper/*", "assets/wallpaper")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=a_datas,
    hiddenimports=yt_dlp_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # keep the exe small: none of these are used by the player
        "tkinter",
        "matplotlib",
        "numpy",
        "PyQt5",
        "PyQt6",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "PIL.ImageQt",
        "unittest",
        "pydoc",
        "doctest",
        "argcomplete",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Spoti Player",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,           # no black console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets\\spoti.ico"],
    version="assets\\version_info.txt",
)
