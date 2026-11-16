"""Single running copy of Spoti Player.

Double-clicking a song in Explorer must not start a second player: the new
process notices the running one through a ``QSharedMemory`` lock, hands its
paths over through a small mailbox file in the temp folder and exits.  The
running instance polls the mailbox about once a second and opens what it
finds (folder → load, file → load its folder and play it).
"""

from __future__ import annotations

import json
import os
import tempfile

LOCK_KEY = "SpotiPlayer-single-instance"
MAILBOX_NAME = "spoti_player_open.json"

_holder: list = []          # keeps the shared memory alive for this process


def mailbox_path() -> str:
    return os.path.join(tempfile.gettempdir(), MAILBOX_NAME)


def acquire() -> bool:
    """True when no other instance is running (we own the lock now)."""
    try:
        from PySide6.QtCore import QSharedMemory
    except Exception:                                     # pragma: no cover
        return True
    memory = QSharedMemory(LOCK_KEY)
    if memory.attach():                 # somebody else is alive
        memory.detach()
        return False
    if memory.create(1):
        _holder.append(memory)          # keep the segment for our lifetime
        return True
    # lost the race against another process that is starting right now
    if memory.attach():
        memory.detach()
        return False
    return True                         # shared memory unsupported → run anyway


def send(paths: list[str], show: bool = False) -> bool:
    """Leave the requested paths for the running instance.

    ``show=True`` means “no paths, just bring the window up” (a second launch
    of the app itself while it is already running).
    """
    try:
        with open(mailbox_path(), "w", encoding="utf-8") as handle:
            json.dump({"paths": [str(p) for p in paths], "show": bool(show)}, handle)
        return True
    except OSError:
        return False


def poll() -> dict:
    """Consume the mailbox: ``{"paths": [...], "show": bool}`` (empty if none)."""
    path = mailbox_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"paths": [], "show": False}
    try:
        os.remove(path)
    except OSError:
        pass
    if not isinstance(data, dict):
        return {"paths": [], "show": False}
    paths = data.get("paths") or []
    return {"paths": [str(p) for p in paths if p], "show": bool(data.get("show"))}
