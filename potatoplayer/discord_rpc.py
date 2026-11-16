"""Discord Rich Presence for Spoti Player.

Shows what is playing (song title, artist/album and a live progress timer) as a
"Listening to ..." activity in the user's Discord profile.

Discord only accepts Rich Presence for a **registered Discord application**, so
the user supplies that application's *Application ID* once (it is free and takes
a minute - the UI has a setup dialog and the README explains it).  Everything is
optional and fails soft: if Discord is closed, the library is missing or the ID
is wrong, the player keeps working and the status line explains what happened.

The pypresence client is blocking, so it lives on its own worker thread that
throttles updates (Discord allows ~5 per 20 seconds) and reconnects by itself.
"""

from __future__ import annotations

import os
import threading
import time

try:  # pypresence is optional: the player works fine without it
    from pypresence import Presence as _Presence
    from pypresence import exceptions as _exc
    from pypresence.types import ActivityType as _ActivityType
    from pypresence.utils import get_ipc_path as _get_ipc_path

    LIBRARY_AVAILABLE = True
except Exception:  # pragma: no cover - only when pypresence is absent
    _Presence = None
    _exc = None
    _ActivityType = None
    _get_ipc_path = None
    LIBRARY_AVAILABLE = False

# Discord rate-limits activity updates to about 5 per 20 s, so never send faster
# than this, and refresh slowly while a song keeps playing (the timer is
# rendered by Discord itself thanks to the start/end timestamps).
MIN_UPDATE_GAP = 4.5
REFRESH_WHILE_PLAYING = 30.0
REFRESH_WHILE_PAUSED = 90.0

MAX_FIELD_BYTES = 128        # Discord's hard limit for details/state

STATE_OFF = "off"
STATE_NO_LIBRARY = "no-library"
STATE_NO_ID = "no-id"
STATE_NO_DISCORD = "no-discord"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_INVALID_ID = "invalid-id"
STATE_ERROR = "error"

STATUS_TEXT = {
    STATE_OFF: "Off",
    STATE_NO_LIBRARY: "pypresence is not installed",
    STATE_NO_ID: "Needs a Discord Application ID",
    STATE_NO_DISCORD: "Discord is not running",
    STATE_CONNECTING: "Connecting…",
    STATE_CONNECTED: "Connected",
    STATE_INVALID_ID: "Invalid Application ID",
    STATE_ERROR: "Connection error",
}

STATUS_COLOR_KEY = {
    STATE_CONNECTED: "accent",
    STATE_CONNECTING: "text_dim",
    STATE_NO_DISCORD: "warn",
    STATE_NO_ID: "warn",
    STATE_INVALID_ID: "danger",
    STATE_ERROR: "danger",
    STATE_OFF: "text_muted",
    STATE_NO_LIBRARY: "danger",
}


# --------------------------------------------------------------------------- #
#  helpers (pure functions -> easy to test)
# --------------------------------------------------------------------------- #
def clip(text: str, limit: int = MAX_FIELD_BYTES) -> str:
    """Trim *text* to Discord's byte limit without breaking UTF-8 characters."""
    text = (text or "").strip()
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    cut = raw[:limit]
    while cut:
        try:
            return cut.decode("utf-8")
        except UnicodeDecodeError:
            cut = cut[:-1]
    return ""


def discord_running() -> bool:
    """True when a Discord client IPC pipe is reachable right now."""
    if not LIBRARY_AVAILABLE:
        return False
    try:
        return bool(_get_ipc_path())
    except Exception:
        return False


def build_activity(title: str, artist: str = "", album: str = "",
                   position_ms: int = 0, duration_ms: int = 0, paused: bool = False,
                   large_image: str = "", large_text: str = "") -> dict:
    """Build the keyword arguments for pypresence's ``update()``.

    Discord needs ``details`` and ``state`` to be at least two characters long,
    so short or missing values fall back to something sensible.
    """
    details = clip(title) or "Spoti Player"
    if len(details) < 2:
        details = f"{details} " if details else "Spoti Player"

    second = " • ".join(bit for bit in (artist, album) if bit)
    state = clip(second) or "Unknown artist"
    if len(state) < 2:
        state = f"{state} "

    payload: dict = {
        "details": details,
        "state": state,
        "activity_type": _ActivityType.LISTENING if _ActivityType is not None else 2,
    }

    if large_image:
        payload["large_image"] = large_image
        payload["large_text"] = clip(large_text or album or "Spoti Player")

    if not paused:
        position_ms = max(0, int(position_ms or 0))
        duration_ms = max(0, int(duration_ms or 0))
        now = int(time.time())
        start = now - position_ms // 1000
        payload["start"] = start
        if duration_ms > 0:
            end = start + max(1, duration_ms // 1000)
            if end > now:
                payload["end"] = end
    return payload


# --------------------------------------------------------------------------- #
#  worker
# --------------------------------------------------------------------------- #
class DiscordPresence:
    """Threaded Rich Presence client. All public methods are thread-safe."""

    def __init__(self, client_id: str = "", enabled: bool = False,
                 large_image: str = "spoti", large_text: str = "Spoti Player"):
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._client_id = (client_id or "").strip()
        self._enabled = bool(enabled)
        self._large_image = (large_image or "").strip()
        self._large_text = large_text or "Spoti Player"

        self._activity: dict | None = None
        self._revision = 0
        self._status = STATE_OFF
        self._status_detail = ""
        self._sent_revision = -1
        self._last_send = 0.0
        self._performance = False
        self._connected_client_id = ""

    def set_performance(self, performance: bool) -> None:
        """In performance mode the presence is refreshed far less often."""
        with self._lock:
            self._performance = bool(performance)
        self._wake.set()

    # ------------------------------------------------------------ public API #
    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def client_id(self) -> str:
        with self._lock:
            return self._client_id

    @property
    def large_image(self) -> str:
        with self._lock:
            return self._large_image

    def status(self) -> str:
        with self._lock:
            return self._status

    def activity(self) -> dict | None:
        """The activity that is (or will be) published - handy for tests."""
        with self._lock:
            return dict(self._activity) if self._activity else None

    def status_text(self) -> str:
        with self._lock:
            base = STATUS_TEXT.get(self._status, self._status)
            if self._status_detail:
                return f"{base} — {self._status_detail}"
            return base

    def status_color_key(self) -> str:
        with self._lock:
            return STATUS_COLOR_KEY.get(self._status, "text_muted")

    def start(self) -> None:
        """Start (or restart) the worker thread."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="discord-rpc", daemon=True)
            self._thread.start()
        self._wake.set()

    def stop(self, clear: bool = True) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            self._thread = None
            self._activity = None
            self._status = STATE_OFF
            self._status_detail = ""

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            if not self._enabled:
                self._status = STATE_OFF
                self._status_detail = ""
                self._activity = None
                self._revision += 1
        self._wake.set()
        if enabled:
            self.start()

    def set_client_id(self, client_id: str) -> None:
        with self._lock:
            self._client_id = (client_id or "").strip()
            self._status = STATE_OFF
            self._status_detail = ""
            self._connected_client_id = ""
            self._revision += 1
        self._wake.set()
        self.start()

    def set_large_image(self, key: str) -> None:
        with self._lock:
            self._large_image = (key or "").strip()
            self._revision += 1
        self._wake.set()

    def update_track(self, track=None, position_ms: int = 0, duration_ms: int = 0,
                     paused: bool = False) -> None:
        """Publish the current song (``track=None`` clears the activity)."""
        with self._lock:
            if track is None:
                self._activity = None
            else:
                self._activity = build_activity(
                    title=getattr(track, "display_title", "") or getattr(track, "name", ""),
                    artist=getattr(track, "artist", ""),
                    album=getattr(track, "album", ""),
                    position_ms=position_ms,
                    duration_ms=duration_ms or int((getattr(track, "duration", 0) or 0) * 1000),
                    paused=paused,
                    large_image=self._large_image,
                    large_text=self._large_text,
                )
            self._revision += 1
        self._wake.set()

    # -------------------------------------------------------------- internals #
    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                enabled = self._enabled
                client_id = self._client_id

            if not enabled:
                self._set_status(STATE_OFF)
                self._idle(1.0)
                continue
            if not LIBRARY_AVAILABLE:
                self._set_status(STATE_NO_LIBRARY)
                self._idle(10.0)
                continue
            if not client_id:
                self._set_status(STATE_NO_ID)
                self._idle(2.0)
                continue
            if not discord_running():
                self._set_status(STATE_NO_DISCORD)
                self._idle(6.0)
                continue

            self._set_status(STATE_CONNECTING)
            client = None
            try:
                client = _Presence(client_id)
                client.connect()
                with self._lock:
                    self._connected_client_id = client_id
            except Exception as exc:                       # noqa: BLE001
                self._handle_connect_error(exc)
                try:
                    if client is not None:
                        client.close()
                except Exception:
                    pass
                # a wrong application id will not fix itself: back off longer
                self._idle(30.0 if self.status() == STATE_INVALID_ID else 8.0)
                continue

            with self._lock:
                self._sent_revision = -1
                self._last_send = 0.0
            self._set_status(STATE_CONNECTED)

            try:
                self._pump(client)
            except Exception as exc:                        # noqa: BLE001
                self._set_status(STATE_ERROR, type(exc).__name__)
            finally:
                try:
                    client.close()
                except Exception:
                    pass

            if not self._stop.is_set():
                self._idle(3.0)

    def _idle(self, seconds: float) -> None:
        """Sleep, but return early when the settings or the song changed."""
        self._wake.wait(seconds)
        self._wake.clear()

    def _handle_connect_error(self, exc: Exception) -> None:
        self._set_status(*self._status_for_error(exc))

    @staticmethod
    def _status_for_error(exc: Exception) -> tuple[str, str]:
        """Map a pypresence exception onto (status, detail)."""
        name = type(exc).__name__
        if _exc is not None:
            if isinstance(exc, getattr(_exc, "InvalidID", ())):
                return STATE_INVALID_ID, ""
            if isinstance(exc, getattr(_exc, "DiscordNotFound", ())):
                return STATE_NO_DISCORD, ""
            if isinstance(exc, getattr(_exc, "InvalidPipe", ())):
                return STATE_NO_DISCORD, ""
            if isinstance(exc, getattr(_exc, "ConnectionTimeout", ())):
                return STATE_ERROR, "timeout"
        return STATE_ERROR, name

    def _pump(self, client) -> None:
        """Send updates for as long as the Discord pipe stays healthy."""
        while not self._stop.is_set():
            with self._lock:
                enabled = self._enabled
                activity = dict(self._activity) if self._activity else None
                revision = self._revision
                sent_revision = self._sent_revision
                last_send = self._last_send
                client_id = self._client_id
            if not enabled or not client_id:
                return

            # a changed application id means we must reconnect to use it
            if client_id != self._connected_client_id:
                return

            now = time.monotonic()
            changed = revision != sent_revision
            paused = "start" not in (activity or {})
            if paused:
                refresh = REFRESH_WHILE_PAUSED
            elif self._performance:
                refresh = 180.0
            else:
                refresh = REFRESH_WHILE_PLAYING
            due = (changed or now - last_send >= refresh) and now - last_send >= MIN_UPDATE_GAP

            if due:
                try:
                    if activity is None:
                        client.clear()
                    else:
                        client.update(**activity)
                except Exception as exc:                    # noqa: BLE001
                    self._set_status(*self._status_for_error(exc))
                    raise                                   # outer loop reconnects
                with self._lock:
                    self._sent_revision = revision
                    self._last_send = time.monotonic()

            self._wake.wait(1.0)
            self._wake.clear()

    def _set_status(self, status: str, detail: str = "") -> None:
        with self._lock:
            self._status = status
            self._status_detail = detail


def describe_setup(client_id: str = "") -> str:
    """Human readable setup hint (used by the UI dialog and the README)."""
    if not LIBRARY_AVAILABLE:
        return ("Discord support needs the 'pypresence' package:\n\n"
                "    pip install pypresence")
    if not client_id:
        return ("Discord Rich Presence uses a Discord *application* to label your\n"
                "status. Create one (free, one minute):\n\n"
                "  1. Open https://discord.com/developers/applications\n"
                "  2. New Application -> name it \"Spoti Player\"\n"
                "  3. Copy the Application ID from the General Information page\n"
                "  4. Paste it below and press Save\n\n"
                "Optional: in that application open Rich Presence -> Art Assets and\n"
                "upload an image named 'spoti' to get a cover in your status.")
    return (f"Application ID: {client_id}\n"
            "\n"
            "Spoti Player is preconfigured with a default Application ID, so Discord\n"
            "Rich Presence works out of the box. Use the field below only if you want\n"
            "to use your own application instead.")
