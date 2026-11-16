"""Online song search through Apple's public iTunes Search API.

The API is free and needs no account.  It exposes the *30-second preview* clip
of every song in the iTunes / Apple Music catalogue (``previewUrl``), together
with the artwork and the Apple Music page of the full track.  The player streams
that preview; downloading the *whole* song is done by :mod:`fulltrack`, which
finds the track in the public SoundCloud/YouTube catalogue.

Everything here is plain ``urllib`` + ``PIL`` so it works with the packages the
player already ships and never blocks the Qt thread (the window calls these
from a worker thread).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SEARCH_URL = "https://itunes.apple.com/search"
#: preview clips are exactly this long (the UI labels them as previews)
PREVIEW_SECONDS = 30.0
TIMEOUT = 8.0
USER_AGENT = "SpotiPlayer/3.0 (desktop music player; preview search)"


class SearchError(Exception):
    """The online catalogue could not be reached or answered badly."""


@dataclass(frozen=True)
class OnlineTrack:
    """One song found in the online catalogue (played as a preview)."""

    id: int
    title: str
    artist: str
    album: str
    duration: float          # the full song's length (for the subtitle)
    preview_url: str
    page_url: str
    artwork_url: str

    # the same little interface the local Track exposes, so the now-playing
    # panels, Discord presence and mini bar can render it unchanged
    @property
    def name(self) -> str:
        return self.title

    @property
    def display_title(self) -> str:
        return self.title

    @property
    def display_artist(self) -> str:
        return self.artist

    def subtitle(self) -> str:
        return " • ".join(part for part in (self.artist, self.album) if part)

    def tech_line(self) -> str:
        return "Apple Music preview • 30 seconds"


def search(query: str, limit: int = 25) -> list[OnlineTrack]:
    """Search the catalogue; raises :class:`SearchError` on a network problem."""
    query = (query or "").strip()
    if not query:
        return []
    params = {
        "term": query,
        "media": "music",
        "entity": "song",
        "limit": str(max(1, min(50, int(limit)))),
    }
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
    except Exception as exc:                     # URLError, timeout, HTTPError...
        raise SearchError(str(exc)) from exc
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise SearchError("the catalogue answered with invalid data") from exc

    tracks: list[OnlineTrack] = []
    for item in data.get("results") or []:
        preview = item.get("previewUrl") or ""
        if not preview:
            continue                         # nothing to play for this result
        try:
            track_id = int(item.get("trackId") or 0)
        except (TypeError, ValueError):
            track_id = 0
        tracks.append(OnlineTrack(
            id=track_id or abs(hash(item.get("trackName", ""))) % (1 << 31),
            title=(item.get("trackName") or "Unknown song").strip(),
            artist=(item.get("artistName") or "").strip(),
            album=(item.get("collectionName") or "").strip(),
            duration=(item.get("trackTimeMillis") or 0) / 1000.0,
            preview_url=preview,
            page_url=(item.get("trackViewUrl") or "").strip(),
            artwork_url=(item.get("artworkUrl100") or "").strip(),
        ))
    return tracks


def safe_name(text: str, limit: int = 60) -> str:
    """Turn a title/artist into something Windows accepts as a file name."""
    bad = '<>:"/\\|?*\x00'
    cleaned = "".join(" " if char in bad else char for char in (text or ""))
    cleaned = " ".join(cleaned.split()).strip(" .")
    if len(cleaned) > limit:
        cut = cleaned[:limit]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        cleaned = cut.strip(" .-")
    return cleaned or "Unknown"


def preview_suffix(url: str) -> str:
    """The real file extension of a preview clip (the API serves m4a)."""
    suffix = Path(urllib.parse.urlparse(url or "").path).suffix.lower()
    return suffix if suffix in (".m4a", ".mp3", ".aac", ".mp4") else ".m4a"


def download_preview(track: OnlineTrack, folder: Path | str, progress=None) -> Path:
    """Save the official 30-second preview clip into *folder*.

    Only the preview the catalogue hands out is stored - it is the same clip
    the player streams.  The download goes to a ``.part`` file first, so an
    interrupted transfer never leaves a half-written song in the library.
    ``progress`` is called as ``progress(done_bytes, total_bytes)``.
    """
    if not track.preview_url:
        raise SearchError("this result has no preview to save")
    target_dir = Path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = f"{safe_name(track.artist, 40)} - {safe_name(track.title, 60)} [{track.id}]"
    target = target_dir / (name + preview_suffix(track.preview_url))
    request = urllib.request.Request(track.preview_url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "audio/*,*/*",
    })
    temp = target.with_suffix(target.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response, \
                open(temp, "wb") as handle:
            total = 0
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
                if progress is not None:
                    try:
                        progress(done, total)
                    except Exception:
                        pass
        if not temp.is_file() or temp.stat().st_size <= 1024:
            raise SearchError("the preview came back empty")
        os.replace(temp, target)
    except SearchError:
        _discard(temp)
        raise
    except Exception as exc:                     # URLError, timeout, disk...
        _discard(temp)
        raise SearchError(str(exc)) from exc
    return target


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def fetch_artwork(track: OnlineTrack, cache_dir: Path | str, size: int = 640):
    """Download (once) and return the cover as a PIL image, or raise.

    The image is stored in the player's cache folder, so replaying a preview -
    or searching the same song again - never downloads the cover twice.
    """
    from PIL import Image, ImageOps

    url = (track.artwork_url or "").replace("100x100", f"{int(size)}x{int(size)}")
    if not url:
        raise SearchError("this result has no artwork")
    folder = Path(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    cached = folder / f"online_{key}.jpg"
    if cached.is_file():
        try:
            with Image.open(cached) as image:
                return image.convert("RGB")
        except Exception:
            pass                              # broken cache file: fetch again
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read()
    except Exception as exc:
        raise SearchError(str(exc)) from exc
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise SearchError("the cover could not be decoded") from exc
    image = ImageOps.fit(image, (int(size), int(size)))
    try:
        image.save(cached, "JPEG", quality=90)
    except OSError:
        pass
    return image
