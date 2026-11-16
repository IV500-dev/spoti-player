"""The songs the listener downloaded from the online search.

Searching the iTunes catalogue only hands out a 30-second preview of every
song; the *whole* track is fetched by :mod:`fulltrack`.  This module keeps
those files on disk (inside the player's own folder) together with a small JSON
index, so the "Downloads" playlist can list them with their real title, artist,
album, cover and length even when the network is gone.

The files stay ordinary audio files - copy them out, play them elsewhere.  The
folder used to be called "Saved previews"; older libraries are moved over once
when the player starts, so nothing that was already saved is lost.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import config, online
from .library import Track

INDEX_NAME = "saved_previews.json"
FOLDER_NAME = "Downloads"
OLD_FOLDER_NAME = "Saved previews"


# --------------------------------------------------------------------------- #
#  location
# --------------------------------------------------------------------------- #
def folder() -> Path:
    """The folder the downloaded songs live in (inside the player's own data)."""
    target = config.config_dir() / FOLDER_NAME
    old = config.config_dir() / OLD_FOLDER_NAME
    if old.is_dir():
        _migrate(old, target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _migrate(old: Path, target: Path) -> None:
    """Move the old "Saved previews" folder over once and fix the index."""
    try:
        target.mkdir(parents=True, exist_ok=True)
        for item in old.iterdir():
            if item.is_file():
                destination = target / item.name
                if not destination.exists():
                    os.replace(item, destination)
        old.rmdir()
    except OSError:
        return
    items = _load()
    moved = False
    for item in items:
        file = item.get("file") or ""
        try:
            relative = Path(file).relative_to(old)
        except (ValueError, OSError):
            continue
        item["file"] = str(target / relative)
        moved = True
    if moved:
        _store(items)


def index_path() -> Path:
    return config.config_dir() / INDEX_NAME


# --------------------------------------------------------------------------- #
#  index
# --------------------------------------------------------------------------- #
def _load() -> list[dict]:
    try:
        with open(index_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        data = data.get("items") or []
    items = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        if not item.get("file"):
            continue
        items.append(item)
    return items


def _store(items: list[dict]) -> None:
    target = index_path()
    try:
        temp = target.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(items, handle, indent=2, ensure_ascii=False)
        os.replace(temp, target)
    except OSError:
        pass


def entries() -> list[dict]:
    """Every downloaded song that still exists on disk, newest first."""
    items = [item for item in _load() if os.path.isfile(item["file"])]
    items.sort(key=lambda item: item.get("saved_at") or 0.0, reverse=True)
    return items


def ids() -> set[int]:
    return {int(item["id"]) for item in entries() if item.get("id") is not None}


def find(track_id: int) -> dict | None:
    for item in entries():
        try:
            if int(item.get("id")) == int(track_id):
                return item
        except (TypeError, ValueError):
            continue
    return None


def add(track: online.OnlineTrack, path: str | os.PathLike,
        duration: float = 0.0) -> dict:
    """Record a downloaded song in the index (replacing an older copy)."""
    file = os.path.normpath(str(path))
    items = [item for item in _load() if os.path.normcase(item["file"]) != os.path.normcase(file)]
    entry = {
        "id": int(track.id),
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "duration": float(duration or 0.0),
        "page_url": track.page_url,
        "artwork_url": track.artwork_url,
        "file": file,
        "saved_at": time.time(),
    }
    items.append(entry)
    _store(items)
    return entry


def remove(path: str | os.PathLike, delete_file: bool = True) -> bool:
    """Forget a downloaded song (and delete its file unless told otherwise)."""
    file = os.path.normpath(str(path))
    items = _load()
    keep = [item for item in items if os.path.normcase(item["file"]) != os.path.normcase(file)]
    changed = len(keep) != len(items)
    if changed:
        _store(keep)
    if delete_file:
        try:
            os.remove(file)
        except OSError:
            pass
    return changed


def total_bytes() -> int:
    total = 0
    for item in entries():
        try:
            total += os.path.getsize(item["file"])
        except OSError:
            pass
    return total


# --------------------------------------------------------------------------- #
#  tracks for the playlist
# --------------------------------------------------------------------------- #
def tracks() -> list[Track]:
    """The downloaded songs as normal playlist tracks (metadata from the index)."""
    result = []
    for item in entries():
        try:
            track = Track(
                path=item["file"],
                title=item.get("title") or "",
                artist=item.get("artist") or "",
                album=item.get("album") or "",
                duration=float(item.get("duration") or 0.0),
                meta_loaded=True,          # the index is the source of truth
            )
        except OSError:
            continue
        result.append(track)
    return result


# --------------------------------------------------------------------------- #
#  best-effort tags, so the files look right outside the player too
# --------------------------------------------------------------------------- #
def write_tags(path: str | os.PathLike, track: online.OnlineTrack, image=None) -> None:
    """Write title/artist/album/cover into the downloaded song (silently optional)."""
    file = str(path)
    suffix = Path(file).suffix.lower()
    if suffix in (".mp3",):
        _write_mp3(file, track, image)
    else:
        _write_mp4(file, track, image)


def _cover_bytes(image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _write_mp4(file: str, track: online.OnlineTrack, image) -> None:
    try:
        from mutagen.mp4 import MP4, MP4Cover
    except Exception:
        return
    try:
        audio = MP4(file)
    except Exception:
        return
    try:
        audio["\xa9nam"] = [track.title]
        if track.artist:
            audio["\xa9ART"] = [track.artist]
        if track.album:
            audio["\xa9alb"] = [track.album]
        if image is not None:
            audio["covr"] = [MP4Cover(_cover_bytes(image),
                                      imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
    except Exception:
        pass


def _write_mp3(file: str, track: online.OnlineTrack, image) -> None:
    try:
        from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1
    except Exception:
        return
    try:
        try:
            tags = ID3(file)
        except ID3NoHeaderError:
            tags = ID3()
        tags.setall("TIT2", [TIT2(encoding=3, text=track.title)])
        if track.artist:
            tags.setall("TPE1", [TPE1(encoding=3, text=track.artist)])
        if track.album:
            tags.setall("TALB", [TALB(encoding=3, text=track.album)])
        if image is not None:
            tags.setall("APIC", [APIC(encoding=3, mime="image/jpeg", type=3,
                                      desc="Cover", data=_cover_bytes(image))])
        tags.save(file)
    except Exception:
        pass
