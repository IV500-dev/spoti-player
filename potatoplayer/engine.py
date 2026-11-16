"""Audio engine built on libVLC (python-vlc).

Gives the UI everything it needs:
  * playback (single-track, driven by the playlist in the app)
  * output volume "drive" (0 - 125 %), mute
  * switchable audio output device (WASAPI / mmdevice)
  * a real 10-band graphic equalizer with pre-amp and presets
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque

EQ_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
EQ_MIN, EQ_MAX = -12.0, 12.0
VOLUME_MAX = 125

# name -> (pre-amp dB, 10 band gains dB)  [31 Hz .. 16 kHz]
PRESETS: dict[str, tuple[float, tuple[float, ...]]] = {
    "Flat":          (0.0, (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
    "Custom":        (0.0, (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
    "Acoustic":      (2.0, (4, 3, 2, 1, 2, 2, 3, 3, 2, 1)),
    "Bass Boost":    (4.0, (8, 7, 5, 3, 1, 0, 0, 0, 0, 0)),
    "Bass & Treble": (3.0, (6, 5, 3, 1, 0, 0, 1, 3, 5, 6)),
    "Classical":     (2.0, (4, 3, 2, 0, -1, -1, 0, 2, 3, 4)),
    "Dance":         (2.0, (5, 4, 1, 0, 0, -3, -4, -4, 0, 0)),
    "Deep":          (-2.0, (5, 3, 2, 1, 0, -1, -2, -3, -4, -5)),
    "Electronic":    (2.0, (4, 3, 0, -2, -1, 1, 0, 1, 4, 5)),
    "Hip-Hop":       (3.0, (6, 5, 2, 2, -1, -1, 1, -1, 2, 3)),
    "Jazz":          (2.0, (4, 3, 1, 2, -1, -1, 0, 1, 3, 4)),
    "Live":          (-1.0, (-2, -1, 0, 2, 3, 3, 3, 2, 1, 0)),
    "Loudness":      (3.0, (7, 5, 0, -2, -1, 0, 2, 5, 6, 6)),
    "Lounge":        (-1.0, (-3, -1, 0, 1, 2, 1, 0, -1, -2, -3)),
    "Party":         (3.0, (5, 4, 1, 0, 0, -2, -3, -3, 0, 1)),
    "Pop":           (1.0, (-1, 2, 4, 4, 1, -1, -1, 1, 2, 2)),
    "Reggae":        (2.0, (0, 0, 0, -2, 0, 3, 4, 3, 0, 0)),
    "Rock":          (2.0, (5, 4, -1, -2, -1, 2, 4, 5, 5, 5)),
    "Soft":          (-1.0, (2, 1, 0, -1, 0, 1, 2, 2, 1, 0)),
    "Speech":        (0.0, (-4, -3, -1, 1, 3, 4, 4, 3, 1, -1)),
    "Techno":        (3.0, (5, 4, 1, 0, -1, -2, -2, 0, 3, 5)),
    "Treble Boost":  (3.0, (0, 0, 0, 0, 0, 0, 2, 4, 6, 7)),
    "Vocal":         (2.0, (-3, -2, -1, 1, 3, 4, 4, 2, 0, -1)),
}

PRESET_NAMES = list(PRESETS.keys())


class EngineError(RuntimeError):
    """Raised when libVLC cannot be located or started."""


# --------------------------------------------------------------------------- #
#  libVLC discovery
# --------------------------------------------------------------------------- #
def candidate_vlc_dirs():
    seen = set()

    def add(path):
        if not path:
            return None
        path = os.path.abspath(path)
        if path in seen or not os.path.isfile(os.path.join(path, "libvlc.dll")):
            return None
        seen.add(path)
        return path

    for env_var in ("POTATO_PLAYER_VLC_DIR", "VLC_HOME", "VLC_DIR"):
        hit = add(os.environ.get(env_var, ""))
        if hit:
            yield hit

    # portable builds: libvlc next to the .exe (or in a "vlc"/"libvlc" sub-folder)
    bases = []
    if getattr(sys, "frozen", False):
        bases.append(os.path.dirname(os.path.abspath(sys.executable)))
    bases.append(os.path.dirname(os.path.abspath(__file__)))
    for base in bases:
        for folder in ("", "vlc", "libvlc", "VLC", "VideoLAN\\VLC"):
            hit = add(os.path.join(base, folder) if folder else base)
            if hit:
                yield hit

    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), r"C:\Program Files", r"C:\Program Files (x86)"):
        hit = add(os.path.join(base or "", "VideoLAN", "VLC"))
        if hit:
            yield hit

    try:  # registry install dir
        import winreg
        for root, key in (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VideoLAN\VLC"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\VideoLAN\VLC"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\VideoLAN\VLC"),
        ):
            try:
                with winreg.OpenKey(root, key) as handle:
                    install_dir = winreg.QueryValueEx(handle, "InstallDir")[0]
            except OSError:
                continue
            hit = add(install_dir)
            if hit:
                yield hit
    except ImportError:
        pass

    for entry in os.environ.get("PATH", "").split(os.pathsep):
        hit = add(entry)
        if hit:
            yield hit


def load_vlc(vlc_dir: str | None = None):
    """Import the bundled/installed python-vlc module with libvlc.dll reachable."""
    dirs = list(candidate_vlc_dirs())
    chosen = None
    if vlc_dir:
        explicit = os.path.abspath(vlc_dir)
        if os.path.isfile(os.path.join(explicit, "libvlc.dll")):
            chosen = explicit
    if chosen is None and dirs:
        chosen = dirs[0]

    if chosen:
        try:
            os.add_dll_directory(chosen)
        except (AttributeError, OSError):
            pass
        os.environ["PATH"] = chosen + os.pathsep + os.environ.get("PATH", "")

    try:
        import vlc  # noqa: WPS433  (import after DLL directories are set up)
    except ImportError as exc:  # pragma: no cover
        raise EngineError(
            "python-vlc is not installed.  Run:  pip install python-vlc\n"
            f"({exc})"
        ) from exc

    if chosen is None:
        raise EngineError(
            "libvlc.dll was not found.  Install VLC media player (https://www.videolan.org)\n"
            "or point the player at an existing folder with the --vlc-dir option."
        )
    return vlc, chosen


# --------------------------------------------------------------------------- #
#  Engine
# --------------------------------------------------------------------------- #
class VlcEngine:
    #: convenience aliases so callers can use ``VlcEngine.EQ_FREQS``
    EQ_FREQS = EQ_FREQS
    PRESET_NAMES = PRESET_NAMES
    VOLUME_MAX = VOLUME_MAX

    def __init__(self, vlc_dir: str | None = None, volume: int = 70):
        self.vlc, self.vlc_dir = load_vlc(vlc_dir)
        args = [
            "--quiet",
            "--no-video",
            "--no-video-title-show",
            "--no-snapshot-preview",
            "--no-stats",
            "--intf", "dummy",
        ]
        try:
            self.instance = self.vlc.Instance(*args)
        except Exception as exc:  # pragma: no cover
            raise EngineError(f"libVLC failed to start: {exc}") from exc
        if self.instance is None:
            raise EngineError("libVLC failed to start (Instance() returned NULL).")

        self.player: "object" = self.instance.media_player_new()
        try:
            self.player.audio_output_set(b"mmdevice")   # WASAPI -> per-device output
        except Exception:
            pass

        self._media = None
        self._path = None
        self._started_at = 0.0
        self._pending_seek_ms = None
        self._pending_pause = False
        self._volume = max(0, min(VOLUME_MAX, int(volume)))
        self._muted = False
        self._eq_enabled = True
        self._eq_preamp = 0.0
        self._eq_bands = [0.0] * 10
        self._eq = self.vlc.AudioEqualizer()
        self._device_id = ""
        self._device_name = "Default"
        self._device_dirty = False
        self._volume_dirty = True
        self._eq_dirty = True
        self._devices_cache: tuple[float, list[tuple[str, str]]] | None = None
        self._events: deque = deque()
        self._lock = threading.Lock()
        self._attached = []
        self._register_events()
        self._push_eq()

    # ---------------------------------------------------------------- events #
    def _register_events(self) -> None:
        manager = self.player.event_manager()

        def make(kind):
            def handler(_event):  # runs on a libVLC thread: keep it tiny
                with self._lock:
                    self._events.append((kind, None))
            return handler

        for name, kind in (
            ("MediaPlayerEndReached", "ended"),
            ("MediaPlayerEncounteredError", "error"),
        ):
            etype = getattr(self.vlc.EventType, name, None)
            if etype is None:
                continue
            try:
                manager.event_attach(etype, make(kind))
                self._attached.append(etype)
            except Exception:
                pass

    def poll(self) -> list[tuple[str, object]]:
        """Apply deferred settings and return engine events since the last call."""
        self._sync_settings()
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def _sync_settings(self) -> None:
        try:
            playing = self.player.is_playing()
        except Exception:
            return
        if not playing:
            return
        # volume
        try:
            if self._volume_dirty or abs(int(self.player.audio_get_volume()) - self._volume) > 0:
                self.player.audio_set_volume(self._volume)
                self.player.audio_set_mute(self._muted)
                self._volume_dirty = False
        except Exception:
            pass
        if self._device_dirty:
            self._apply_device()
        if self._eq_dirty:
            self._push_eq()
        # restore position (and pause) after an output-device switch restarted
        # the stream: seek once the new stream knows its length
        if self._pending_seek_ms is not None:
            if self.length_ms() > 0:
                self.seek_ms(self._pending_seek_ms)
                self._pending_seek_ms = None
        elif self._pending_pause:
            self.set_pause(True)
            self._pending_pause = False

    # ------------------------------------------------------------- transport #
    def play_path(self, path: str) -> bool:
        try:
            media = self.instance.media_new(path)
        except Exception:
            return False
        if media is None:
            return False
        try:
            media.add_option("no-video")
        except Exception:
            pass
        self._media = media                       # keep a reference alive
        try:
            self.player.set_media(media)
            self._path = path
            self._pending_seek_ms = None
            self._pending_pause = False
            self._volume_dirty = True
            self._device_dirty = True
            self._eq_dirty = True
            started = self.player.play() == 0
        except Exception:
            return False
        if started:
            self._started_at = time.monotonic()
        return started

    def recently_started(self, seconds: float = 0.5) -> bool:
        """True right after a new media was started.

        libVLC can report ``EndReached`` for the *previous* media a moment after
        the player was handed a new one; the window uses this to ignore those
        stale events instead of treating the new song as already finished.
        """
        return (time.monotonic() - self._started_at) < seconds

    def stop(self) -> None:
        try:
            self.player.stop()
        except Exception:
            pass

    def pause(self) -> None:
        try:
            self.player.pause()
        except Exception:
            pass

    def set_pause(self, paused: bool) -> None:
        try:
            if paused:
                self.player.set_pause(1)
            else:
                self.player.set_pause(0)
        except Exception:
            pass

    def is_playing(self) -> bool:
        try:
            return bool(self.player.is_playing())
        except Exception:
            return False

    def state(self) -> str:
        try:
            return str(self.player.get_state())
        except Exception:
            return "State.NothingSpecial"

    def time_ms(self) -> int:
        try:
            return max(0, int(self.player.get_time()))
        except Exception:
            return 0

    def length_ms(self) -> int:
        try:
            return max(0, int(self.player.get_length()))
        except Exception:
            return 0

    def seek_ms(self, ms: int) -> None:
        try:
            self.player.set_time(int(max(0, ms)))
        except Exception:
            pass

    def seek_fraction(self, fraction: float) -> None:
        try:
            self.player.set_position(max(0.0, min(1.0, float(fraction))))
        except Exception:
            pass

    def position(self) -> float:
        try:
            return max(0.0, min(1.0, float(self.player.get_position())))
        except Exception:
            return 0.0

    # ---------------------------------------------------------------- volume #
    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, value: int) -> None:
        self._volume = max(0, min(VOLUME_MAX, int(round(value))))
        if self._volume > 0 and self._muted:
            self._muted = False
        self._volume_dirty = True
        try:
            self.player.audio_set_volume(self._volume)
            self.player.audio_set_mute(self._muted)
        except Exception:
            pass

    @property
    def muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool | None = None) -> None:
        self._muted = (not self._muted) if muted is None else bool(muted)
        self._volume_dirty = True
        try:
            self.player.audio_set_mute(self._muted)
        except Exception:
            pass

    # -------------------------------------------------------- output devices #
    def output_devices(self, max_age: float = 2.0) -> list[tuple[str, str]]:
        """[(display name, device id)] for the WASAPI output devices."""
        import time as _time
        if self._devices_cache and _time.monotonic() - self._devices_cache[0] < max_age:
            return list(self._devices_cache[1])

        devices: list[tuple[str, str]] = []
        listing = None
        try:
            self.player.audio_output_set(b"mmdevice")
            listing = self.player.audio_output_device_enum()
            node = listing
            guard = 0
            while node and guard < 128:
                guard += 1
                content = node.contents
                desc = content.description.decode("utf-8", "replace") if content.description else ""
                dev_id = content.device.decode("utf-8", "replace") if content.device else ""
                if desc or dev_id:
                    devices.append((desc or dev_id, dev_id))
                node = content.next
        except Exception:
            devices = []
        finally:
            if listing is not None:
                try:
                    self.vlc.libvlc_audio_output_device_list_release(listing)
                except Exception:
                    pass

        if not devices:
            devices = [("Default", "")]
        self._devices_cache = (_time.monotonic(), devices)
        return list(devices)

    def invalidate_devices(self) -> None:
        self._devices_cache = None

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def device_id(self) -> str:
        return self._device_id

    def set_output_device(self, device_id: str | None, name: str | None = None) -> None:
        new_id = device_id or ""
        new_name = name or "Default"
        if new_id == self._device_id and new_name == self._device_name:
            return
        self._device_id = new_id
        self._device_name = new_name
        self._device_dirty = True
        self._apply_device()
        self._restart_for_device()

    def _restart_for_device(self) -> None:
        """mmdevice (WASAPI) only reads the selected device when its audio
        output is created, so a switch mid-song needs a quick stop/play of
        the same media.  Position and pause state are restored via poll()."""
        if self._media is None or self._path is None:
            return
        state = self.state()
        # Ended/Stopped: nothing worth restarting — the device applies on
        # the next track anyway.
        if state.endswith(("NothingSpecial", "Ended", "Stopped", "EncounteredError")):
            return
        position = self.time_ms()
        was_paused = state.endswith("Paused")
        try:
            self.player.stop()
        except Exception:
            pass
        self._apply_device()                     # sticks now: the aout is gone
        self._volume_dirty = True
        self._eq_dirty = True
        try:
            self.player.set_media(self._media)
            if self.player.play() != 0:
                return
        except Exception:
            return
        if position > 750:                       # tiny offsets: just restart
            self._pending_seek_ms = position
        if was_paused:
            self._pending_pause = True

    def _apply_device(self) -> None:
        try:
            self.player.audio_output_set(b"mmdevice")
            self.player.audio_output_device_set(None, self._device_id or None)
            self._device_dirty = False
        except Exception:
            self._device_dirty = False

    def refresh_output_device(self) -> None:
        """Re-assert the selected device (after a new track starts)."""
        self._device_dirty = True

    # ------------------------------------------------------------- equalizer #
    @property
    def eq_enabled(self) -> bool:
        return self._eq_enabled

    @property
    def eq_bands(self) -> list[float]:
        return list(self._eq_bands)

    @property
    def eq_preamp(self) -> float:
        return self._eq_preamp

    def set_eq_enabled(self, enabled: bool) -> None:
        self._eq_enabled = bool(enabled)
        self._eq_dirty = True
        self._push_eq()

    def set_eq_preamp(self, db: float) -> None:
        self._eq_preamp = max(-20.0, min(20.0, float(db)))
        self._eq_dirty = True
        self._push_eq()

    def set_eq_band(self, index: int, db: float) -> None:
        if 0 <= index < len(self._eq_bands):
            self._eq_bands[index] = max(EQ_MIN, min(EQ_MAX, float(db)))
            self._eq_dirty = True
            self._push_eq()

    def eq_bands_snapshot(self) -> list[float]:
        return list(self._eq_bands)

    def apply_preset(self, name: str) -> None:
        if name not in PRESETS:
            return
        preamp, bands = PRESETS[name]
        self._eq_preamp = float(preamp)
        self._eq_bands = [float(v) for v in bands]
        self._eq_dirty = True
        self._push_eq()

    def preset_for_current_bands(self) -> str:
        for name, (_pre, bands) in PRESETS.items():
            if name == "Custom":
                continue
            if all(abs(b - self._eq_bands[i]) < 0.05 for i, b in enumerate(bands)):
                return name
        return "Custom"

    def _push_eq(self) -> None:
        try:
            if not self._eq_enabled:
                self.player.set_equalizer(None)
                self._eq_dirty = False
                return
            self._eq.set_preamp(self._eq_preamp)
            for idx, gain in enumerate(self._eq_bands):
                self._eq.set_amp_at_index(float(gain), idx)
            self.player.set_equalizer(self._eq)
            self._eq_dirty = False
        except Exception:
            self._eq_dirty = False

    # ---------------------------------------------------------------- misc #
    def version(self) -> str:
        try:
            return self.vlc.libvlc_get_version().decode("utf-8", "replace")
        except Exception:
            return "unknown"

    def shutdown(self) -> None:
        try:
            self.player.stop()
        except Exception:
            pass
        try:
            self.player.release()
        except Exception:
            pass
        try:
            self.instance.release()
        except Exception:
            pass
