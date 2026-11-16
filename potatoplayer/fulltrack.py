"""Download the *whole* song behind an online search result.

The iTunes catalogue only hands out the 30-second preview clip, so the full
track is searched and fetched from the public Audius catalogue first (a plain
JSON/Audio API that needs no account), and then - when yt-dlp is installed -
from SoundCloud and YouTube.  Everything here is plain Python and runs in the
window's worker thread; the UI only ever sees progress numbers.

Nothing is converted (the player ships without ffmpeg): the best audio stream is
saved exactly as it comes (mp3 from Audius, m4a from SoundCloud, m4a/webm from
YouTube) and libVLC plays all of them.  Search hits whose length is nowhere near
the real song, that are only a 30-second preview, or that are a slowed/sped-up/
remixed re-upload are skipped, so the file that lands in "Downloads" really is
the song the listener picked.
"""

from __future__ import annotations

import glob
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .online import OnlineTrack, safe_name

AUDIUS_API = "https://api.audius.co"
AUDIUS_APP = "SpotiPlayer"
#: how many search hits to look at, per source
SEARCH_RESULTS = 8
#: a real song is never this short (previews are exactly 30 s)
MIN_SECONDS = 45.0
TIMEOUT = 20.0
USER_AGENT = "SpotiPlayer/3.0 (desktop music player; full-song download)"
#: uploads that carry a different recording under the same name
UNWANTED = (
    "slowed", "sped up", "speed up", "nightcore", "reverb", "8d audio",
    "bass boost", "instrumental", "karaoke", "cover", "remix", "mashup",
    "live at", "reaction", "tutorial",
)
#: backends, in the order they are tried ("audius" needs no yt-dlp)
BACKENDS = ("audius", "scsearch", "ytsearch")

_AUDIUS_HOST = ""


class FullTrackError(Exception):
    """The full song could not be found or downloaded."""


# --------------------------------------------------------------------------- #
#  finding the right upload
# --------------------------------------------------------------------------- #
def _words(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", (text or "").lower(), re.UNICODE))


def pick_candidates(track: OnlineTrack, entries) -> list[dict]:
    """The search hits that really are *track*, best first.

    Pure and offline-testable: previews, wrong lengths and alternate versions
    are dropped before anything is downloaded.
    """
    wanted = _words(f"{track.title} {track.artist}")
    expected = float(getattr(track, "duration", 0) or 0)
    tolerance = max(20.0, expected * 0.2) if expected > MIN_SECONDS else 0.0
    wanted_text = (f"{track.title} {track.artist}").lower()

    ranked: list[tuple[float, dict]] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("webpage_url"):
            continue
        try:
            duration = float(entry.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration < MIN_SECONDS:
            continue                     # a preview clip, not the song
        if tolerance and abs(duration - expected) > tolerance:
            continue                     # a different edit / a DJ set
        title = entry.get("title") or ""
        overlap = len(wanted & _words(title))
        if not overlap:
            continue                     # an unrelated result
        lowered = title.lower()
        if any(word in lowered and word not in wanted_text for word in UNWANTED):
            continue                     # a slowed/remixed/cover re-upload
        score = overlap / float(len(wanted) or 1)
        if tolerance:
            score += max(0.0, 1.0 - abs(duration - expected) / tolerance)
        ranked.append((score, entry))

    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _score, entry in ranked]


# --------------------------------------------------------------------------- #
#  downloading
# --------------------------------------------------------------------------- #
def download(track: OnlineTrack, folder: Path | str, progress=None) -> tuple[Path, float]:
    """Download the whole song into *folder* and return ``(path, seconds)``.

    ``progress`` is called as ``progress(done_bytes, total_bytes)`` from the
    worker thread.  Raises :class:`FullTrackError` when no upload of the song
    could be downloaded.
    """
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    query = " ".join(part for part in (track.artist, track.title) if part).strip()
    query = query or track.title
    base = f"{safe_name(track.artist, 40)} - {safe_name(track.title, 60)} [{track.id}]"

    errors: list[str] = []
    yt_dlp = None
    for backend in BACKENDS:
        if backend == "audius":
            try:
                entries = _audius_search(query)
            except Exception as exc:                 # blocked, offline, bad JSON...
                errors.append(_short(exc))
                continue
        else:
            if yt_dlp is None:
                try:
                    yt_dlp = _import_ytdlp()
                except FullTrackError as exc:
                    errors.append(str(exc))
                    break
            try:
                entries = _search(yt_dlp, backend, query)
            except Exception as exc:
                errors.append(_short(exc))
                continue
        for entry in pick_candidates(track, entries)[:3]:
            try:
                if backend == "audius":
                    return _audius_fetch(entry, target_dir, base, progress)
                return _fetch(yt_dlp, entry, target_dir, base, progress)
            except Exception as exc:                 # DRM, dead upload, timeout...
                errors.append(_short(exc))
                _cleanup(target_dir, base)

    if errors:
        message = errors[0]
        if yt_dlp is None:
            message += " — run: pip install yt-dlp for SoundCloud/YouTube"
        raise FullTrackError(message)
    raise FullTrackError("no full-length upload of this song was found")


# --------------------------------------------------------------------------- #
#  backend 1: Audius (no account, no key, no extra package)
# --------------------------------------------------------------------------- #
def _audius_host() -> str:
    global _AUDIUS_HOST
    if _AUDIUS_HOST:
        return _AUDIUS_HOST
    payload = _read_json(AUDIUS_API)
    hosts = payload.get("data") or []
    if not hosts:
        raise FullTrackError("the Audius catalogue is not reachable")
    _AUDIUS_HOST = str(hosts[0]).rstrip("/")
    return _AUDIUS_HOST


def _read_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8", "replace"))


def _audius_search(query: str) -> list[dict]:
    """Search Audius and shape the hits like the yt-dlp search entries."""
    url = (f"{_audius_host()}/v1/tracks/search"
           f"?query={urllib.parse.quote(query)}&app_name={AUDIUS_APP}")
    payload = _read_json(url)
    entries = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        permalink = item.get("permalink") or ""
        entries.append({
            "id": item.get("id"),
            "title": item.get("title") or "",
            "duration": item.get("duration"),
            "webpage_url": ("https://audius.co" + permalink) if permalink
                           else "https://audius.co",
        })
    return entries


def _audius_fetch(entry: dict, target_dir: Path, base: str,
                  progress=None) -> tuple[Path, float]:
    track_id = entry.get("id")
    if not track_id:
        raise FullTrackError("this Audius result has no id")
    url = f"{_audius_host()}/v1/tracks/{track_id}/stream?app_name={AUDIUS_APP}"
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "audio/*,*/*",
    })
    target = target_dir / (base + ".mp3")
    temp = target.with_suffix(".mp3.part")
    last = [-1.0]
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response, \
                open(temp, "wb") as handle:
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                total = 0
            done = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                fraction = (done / total) if total else 0.0
                if progress is not None and (fraction - last[0] >= 0.04 or fraction >= 1.0):
                    last[0] = fraction
                    try:
                        progress(done, total)
                    except Exception:
                        pass
        if not temp.is_file() or temp.stat().st_size <= 50_000:
            raise FullTrackError("the download came back empty")
        os.replace(temp, target)
    except FullTrackError:
        _discard(temp)
        raise
    except Exception as exc:
        _discard(temp)
        raise FullTrackError(_short(exc)) from exc
    duration = float(entry.get("duration") or 0) or _length(target)
    if duration and duration < MIN_SECONDS:
        _discard(target)
        raise FullTrackError("only a short clip was available")
    return target, duration


# --------------------------------------------------------------------------- #
#  backend 2/3: SoundCloud and YouTube (through yt-dlp)
# --------------------------------------------------------------------------- #
def _import_ytdlp():
    try:
        import yt_dlp
    except Exception as exc:
        raise FullTrackError(
            "yt-dlp is not installed — run: pip install yt-dlp") from exc
    return yt_dlp


def _search(yt_dlp, source: str, query: str) -> list[dict]:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"{source}{SEARCH_RESULTS}:{query}", download=False)
    return list((info or {}).get("entries") or [])


def _fetch(yt_dlp, entry: dict, target_dir: Path, base: str,
           progress=None) -> tuple[Path, float]:
    last = [-1.0]

    def hook(status: dict) -> None:
        if progress is None:
            return
        if status.get("status") == "finished":
            try:
                progress(1, 1)
            except Exception:
                pass
            return
        if status.get("status") != "downloading":
            return
        try:
            done = int(status.get("downloaded_bytes") or 0)
            total = int(status.get("total_bytes")
                        or status.get("total_bytes_estimate") or 0)
        except (TypeError, ValueError):
            return
        fraction = (done / total) if total else 0.0
        if fraction - last[0] >= 0.04 or fraction >= 1.0:
            last[0] = fraction
            try:
                progress(done, total)
            except Exception:
                pass

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(target_dir / (base + ".%(ext)s")),
        "noprogress": True,
        "overwrites": True,
        "retries": 3,
        "socket_timeout": 20,
        "progress_hooks": [hook],
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(entry["webpage_url"], download=True)

    path = _downloaded_path(info, target_dir, base)
    if path is None or not path.is_file() or path.stat().st_size <= 50_000:
        raise FullTrackError("the download came back empty")
    try:
        duration = float(info.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    duration = duration or _length(path)
    if duration and duration < MIN_SECONDS:
        raise FullTrackError("only a short clip was available")
    return path, duration


def _downloaded_path(info: dict, target_dir: Path, base: str) -> Path | None:
    """Where yt-dlp actually wrote the file (it picks the stream's extension)."""
    for request in info.get("requested_downloads") or []:
        candidate = request.get("filepath") or request.get("_filename")
        if candidate:
            return Path(candidate)
    for key in ("filepath", "_filename"):
        candidate = info.get(key)
        if candidate:
            return Path(candidate)
    matches = sorted(glob.glob(str(target_dir / (base + ".*"))))
    return Path(matches[0]) if matches else None


# --------------------------------------------------------------------------- #
#  small helpers
# --------------------------------------------------------------------------- #
def _length(path: Path) -> float:
    try:
        import mutagen

        audio = mutagen.File(str(path))
        return float(getattr(audio.info, "length", 0.0) or 0.0)
    except Exception:
        return 0.0


def _cleanup(target_dir: Path, base: str) -> None:
    """Remove a half-written file a failed attempt may have left behind."""
    for name in glob.glob(str(target_dir / (base + ".*"))):
        try:
            Path(name).unlink()
        except OSError:
            pass


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _short(exc: Exception) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).strip()
    text = text.replace("ERROR: ", "").strip()
    first = text.splitlines()[0] if text else ""
    return (first or "download failed")[:160]
