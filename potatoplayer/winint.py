"""Windows integration — default music player, run-at-startup, shell helpers.

Everything is written under **HKEY_CURRENT_USER** only, so no administrator
rights are ever needed, and every function is a safe no-op on other platforms.

* ``register()``   — associate audio extensions with Spoti Player so a
                     double-click in Explorer opens the file in this app.
                     Windows keeps its own per-user ``UserChoice`` hash, so
                     the previous default is remembered and restored by
                     ``unregister()``, and the Settings app is opened once so
                     the user can confirm the choice where Windows demands it.
* ``set_startup_enabled()`` — add/remove a ``Run`` entry so Spoti Player
                     starts with Windows (launched with ``--startup``, which
                     opens it minimised in the tray).
"""

from __future__ import annotations

import os
import subprocess
import sys

APP_NAME = "Spoti Player"
PROGID = "SpotiPlayer.audio"
CLASSES = r"Software\Classes"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
BACKUP_KEY = r"Software\SpotiPlayer\AssocBackup"

#: the formats double-clicking should hand to Spoti Player
AUDIO_EXTS = [".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus",
              ".wma", ".m4b", ".aiff", ".mp2", ".mpc"]


# --------------------------------------------------------------------------- #
#  basics
# --------------------------------------------------------------------------- #
def _winreg():
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except Exception:                                     # pragma: no cover
        return None
    return winreg


def is_frozen() -> bool:
    """True inside the built .exe (PyInstaller sets ``sys.frozen``)."""
    return bool(getattr(sys, "frozen", False))


def main_py() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "main.py"))


def launch_command(file_arg: str = "%1") -> str:
    """The shell command that opens Spoti Player with *file_arg*.

    The file argument is quoted (song paths contain spaces); flags such as
    ``--startup`` are passed through untouched.
    """
    arg = file_arg if str(file_arg).startswith("-") else f'"{file_arg}"'
    if is_frozen():
        return f'"{sys.executable}" {arg}'
    return f'"{sys.executable}" "{main_py()}" {arg}'


def startup_command() -> str:
    """What goes into the Run key: start with Windows, minimised in the tray."""
    return launch_command("--startup")


def app_key_name() -> str | None:
    r"""``Applications\<name>`` only makes sense for the built .exe."""
    if is_frozen():
        return os.path.basename(sys.executable)
    return None


def normalize_exts(exts: list[str] | None) -> list[str]:
    exts = AUDIO_EXTS if exts is None else exts
    out = []
    for ext in exts:
        ext = str(ext).strip().lower()
        if not ext:
            continue
        out.append(ext if ext.startswith(".") else "." + ext)
    return out


# --------------------------------------------------------------------------- #
#  registry helpers
# --------------------------------------------------------------------------- #
def _read_value(path: str, name: str = "", root=None):
    wr = _winreg()
    if wr is None:
        return None
    root = root or wr.HKEY_CURRENT_USER
    try:
        with wr.OpenKey(root, path) as key:
            value, _kind = wr.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def _write_value(path: str, name: str, value, kind=None, root=None) -> bool:
    wr = _winreg()
    if wr is None:
        return False
    root = root or wr.HKEY_CURRENT_USER
    kind = kind or (wr.REG_SZ if isinstance(value, str) else wr.REG_BINARY)
    try:
        with wr.CreateKeyEx(root, path) as key:
            wr.SetValueEx(key, name, 0, kind, value)
        return True
    except OSError:
        return False


def _delete_value(path: str, name: str = "", root=None) -> None:
    wr = _winreg()
    if wr is None:
        return
    root = root or wr.HKEY_CURRENT_USER
    try:
        with wr.OpenKey(root, path, 0, wr.KEY_SET_VALUE) as key:
            wr.DeleteValue(key, name)
    except OSError:
        pass


def _delete_tree(path: str, root=None) -> None:
    """Recursive ``rmdir`` for a registry key (DeleteKey needs it on Win)."""
    wr = _winreg()
    if wr is None:
        return
    root = root or wr.HKEY_CURRENT_USER
    try:
        with wr.OpenKey(root, path, 0, wr.KEY_READ | wr.KEY_WRITE) as key:
            children = []
            index = 0
            while True:
                try:
                    children.append(wr.EnumKey(key, index))
                    index += 1
                except OSError:
                    break
    except OSError:
        return
    for child in children:
        _delete_tree(f"{path}\\{child}", root)
    try:
        wr.DeleteKey(root, path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
#  default music player
# --------------------------------------------------------------------------- #
def is_registered(exts: list[str] | None = None) -> bool:
    """True when our ProgID + command are in the registry."""
    wr = _winreg()
    if wr is None:
        return False
    return _read_value(f"{CLASSES}\\{PROGID}\\shell\\open\\command") is not None


def registered_command() -> str | None:
    return _read_value(f"{CLASSES}\\{PROGID}\\shell\\open\\command")


def register(exts: list[str] | None = None, command: str | None = None) -> bool:
    """Hand the audio extensions to Spoti Player (HKCU, no admin).

    The previous per-extension default is stored so ``unregister()`` can put
    the old player back exactly as it was.
    """
    wr = _winreg()
    if wr is None:
        return False
    exts = normalize_exts(exts)
    command = command or launch_command("%1")
    try:
        _write_value(f"{CLASSES}\\{PROGID}", "", "Spoti Player audio file")
        if not _write_value(f"{CLASSES}\\{PROGID}\\shell\\open\\command", "", command):
            return False

        name = app_key_name()
        if name:
            _write_value(f"{CLASSES}\\Applications\\{name}\\shell\\open\\command", "", command)
            _write_value(f"{CLASSES}\\Applications\\{name}", "SupportedTypes",
                         " ".join(exts))

        for ext in exts:
            current = _read_value(f"{CLASSES}\\{ext}")
            if current != PROGID:                       # remember the old player
                _delete_value(f"{BACKUP_KEY}\\{ext}")
                _delete_value(f"{BACKUP_KEY}\\{ext}.empty")
                if current is None:
                    _write_value(f"{BACKUP_KEY}\\{ext}.empty", "", "1")
                else:
                    _write_value(f"{BACKUP_KEY}\\{ext}", "", str(current))
                _write_value(f"{CLASSES}\\{ext}", "", PROGID)
            _write_value(f"{CLASSES}\\{ext}\\OpenWithProgids", PROGID, b"", wr.REG_NONE)
        return True
    except OSError:
        return False


def unregister(exts: list[str] | None = None) -> bool:
    """Undo ``register()`` and give the previous player its default back."""
    wr = _winreg()
    if wr is None:
        return False
    exts = normalize_exts(exts)
    try:
        for ext in exts:
            _delete_value(f"{CLASSES}\\{ext}\\OpenWithProgids", PROGID)
            current = _read_value(f"{CLASSES}\\{ext}")
            if current == PROGID:                       # give the old player back
                if _read_value(f"{BACKUP_KEY}\\{ext}.empty"):
                    _delete_value(f"{CLASSES}\\{ext}")
                else:
                    backup = _read_value(f"{BACKUP_KEY}\\{ext}")
                    if backup:
                        _write_value(f"{CLASSES}\\{ext}", "", str(backup))
            _delete_tree(f"{BACKUP_KEY}\\{ext}")
            _delete_value(f"{BACKUP_KEY}\\{ext}.empty")
        name = app_key_name()
        if name:
            _delete_tree(f"{CLASSES}\\Applications\\{name}")
        _delete_tree(f"{CLASSES}\\{PROGID}")
        _delete_tree(f"{BACKUP_KEY}")
        return True
    except OSError:
        return False


def open_windows_default_apps() -> bool:
    """Open Windows' «Default apps» page so the user can confirm the choice.

    Windows protects the per-user *UserChoice* hash, so the final click is
    always the user's — this just jumps straight to the right page.
    """
    if sys.platform != "win32":
        return False
    for target in ("ms-settings:defaultapps",):
        try:
            os.startfile(target)                          # noqa: S606
            return True
        except Exception:
            pass
    try:
        subprocess.Popen(["control.exe", "/name", "Microsoft.DefaultPrograms"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  run at startup
# --------------------------------------------------------------------------- #
def startup_enabled(value_name: str = APP_NAME, key: str = RUN_KEY) -> bool:
    return _read_value(key, value_name) is not None


def set_startup_enabled(enabled: bool, value_name: str = APP_NAME,
                        key: str = RUN_KEY) -> bool:
    """Add/remove the Run entry.  Returns False when Windows refuses
    (group policy / a locked-down account) so the UI can say so."""
    if enabled:
        return _write_value(key, value_name, startup_command())
    _delete_value(key, value_name)
    return True
