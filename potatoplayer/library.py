"""Music library: folder scanning, natural name sorting and tag reading."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

AUDIO_EXTS = {
    ".mp3", ".mp2", ".m4a", ".m4b", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wav", ".wma", ".aiff", ".aif", ".ape", ".wv", ".mpc", ".mka", ".mp4",
}

_NUM_RE = re.compile(r"(\d+)")


def natural_key(text: str):
    """Sort key so that 'track2' < 'track10' (human / natural order).

    Numbered files sort before lettered ones (like Windows Explorer), and every
    part is a same-shaped tuple so mixed types can never raise a TypeError.
    """
    key = []
    for part in _NUM_RE.split((text or "").casefold()):
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part), ""))
        else:
            key.append((1, 0, part))
    return key


@dataclass
class Track:
    path: str
    name: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    meta_loaded: bool = False
    bitrate: int = 0
    sample_rate: int = 0
    codec: str = ""
    folder: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        # drag & drop hands us "C:/x/y" style paths: normalise once, everywhere
        if self.path:
            self.path = os.path.normpath(self.path)
        if not self.name:
            self.name = os.path.basename(self.path)
        if not self.title:
            self.title = os.path.splitext(self.name)[0]
        if not self.folder:
            self.folder = os.path.dirname(self.path)
        if not self.codec:
            self.codec = os.path.splitext(self.name)[1].lstrip(".").upper()

    @property
    def display_title(self) -> str:
        return self.title or os.path.splitext(self.name)[0]

    @property
    def display_artist(self) -> str:
        return self.artist or "Unknown artist"

    def subtitle(self) -> str:
        bits = [b for b in (self.artist, self.album) if b]
        return " • ".join(bits) if bits else "—"

    def tech_line(self) -> str:
        """'MP3 • 320 kbps • 44.1 kHz' style summary."""
        bits = []
        if self.codec:
            bits.append(self.codec)
        if self.bitrate:
            bits.append(f"{self.bitrate // 1000} kbps")
        if self.sample_rate:
            bits.append(f"{self.sample_rate / 1000:g} kHz")
        return "  •  ".join(bits)


# --------------------------------------------------------------------------- #
#  Scanning
# --------------------------------------------------------------------------- #
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information"}


def _visible_dirs(names) -> list[str]:
    return sorted((d for d in names if not d.startswith(".") and d not in SKIP_DIRS), key=natural_key)


def _walk_audio(folder: str):
    """Yield audio files below *folder*, recursively, in a stable order."""
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = _visible_dirs(dirnames)
        for name in sorted(filenames, key=natural_key):
            if os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                yield os.path.join(dirpath, name)


def count_audio(folder: str) -> int:
    """How many audio files live below *folder* (cheap filename-only walk)."""
    total = 0
    for _dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = _visible_dirs(dirnames)
        total += sum(1 for name in filenames
                     if os.path.splitext(name)[1].lower() in AUDIO_EXTS)
    return total


def list_subfolders(folder: str) -> list[tuple[str, str, int]]:
    """Immediate sub-folders of *folder* as ``[(name, path, song_count)]``."""
    root = Path(folder)
    if not root.is_dir():
        return []
    try:
        names = _visible_dirs([entry.name for entry in os.scandir(root) if entry.is_dir()])
    except OSError:
        return []
    result = []
    for name in names:
        path = os.path.join(folder, name)
        result.append((name, path, count_audio(path)))
    return result


def has_subfolders(folder: str) -> bool:
    return bool(list_subfolders(folder))


def iter_audio_files(folder: str, recursive: bool = True, only_dirs=None, root_files: bool = True):
    """Yield audio file paths below *folder* (skipping hidden/`.` folders).

    *only_dirs* limits the walk to the given sub-folders: with ``root_files`` the
    files sitting directly in *folder* are included as well (multi-folder mode),
    without it the result is exactly those sub-folders - which is what the
    "show only this sub-folder" choice in the UI uses.
    """
    root = Path(folder)
    if not root.is_dir():
        return

    if only_dirs is not None:
        if root_files:
            for entry in sorted(os.listdir(root), key=natural_key):
                full = os.path.join(root, entry)
                if os.path.isfile(full) and os.path.splitext(entry)[1].lower() in AUDIO_EXTS:
                    yield full
        for sub in sorted(only_dirs, key=natural_key):
            if os.path.isdir(sub):
                yield from _walk_audio(sub)
        return

    if recursive:
        yield from _walk_audio(str(root))
    else:
        for entry in sorted(os.listdir(root), key=natural_key):
            full = os.path.join(root, entry)
            if os.path.isfile(full) and os.path.splitext(entry)[1].lower() in AUDIO_EXTS:
                yield full


def scan_folder(folder: str, recursive: bool = True, only_dirs=None,
                root_files: bool = True) -> list[Track]:
    """Return every audio file in *folder* sorted by file name (natural order)."""
    tracks = [Track(path=p) for p in iter_audio_files(folder, recursive, only_dirs, root_files)]
    return sort_tracks(tracks, "File name (A→Z)")


# --------------------------------------------------------------------------- #
#  Sorting
# --------------------------------------------------------------------------- #
SORT_MODES = [
    "File name (A→Z)",
    "File name (Z→A)",
    "Title (A→Z)",
    "Artist (A→Z)",
    "Album (A→Z)",
    "Duration (short→long)",
    "Duration (long→short)",
    "Newest first",
    "Folder / File name",
    "Manual (custom)",
]


def sort_tracks(tracks: list[Track], mode: str) -> list[Track]:
    if mode == "Manual (custom)":
        return list(tracks)
    if mode == "File name (Z→A)":
        return sorted(tracks, key=lambda t: natural_key(t.name), reverse=True)
    if mode == "Title (A→Z)":
        return sorted(tracks, key=lambda t: natural_key(t.display_title))
    if mode == "Artist (A→Z)":
        return sorted(tracks, key=lambda t: (natural_key(t.artist), natural_key(t.album), natural_key(t.name)))
    if mode == "Album (A→Z)":
        return sorted(tracks, key=lambda t: (natural_key(t.album), natural_key(t.name)))
    if mode == "Duration (short→long)":
        return sorted(tracks, key=lambda t: (t.duration or 1e9, natural_key(t.name)))
    if mode == "Duration (long→short)":
        return sorted(tracks, key=lambda t: (-(t.duration or 0), natural_key(t.name)))
    if mode == "Newest first":
        return sorted(tracks, key=lambda t: -_mtime(t.path))
    if mode == "Folder / File name":
        return sorted(tracks, key=lambda t: (natural_key(os.path.relpath(t.folder, os.path.dirname(t.folder))), natural_key(t.name)))
    return sorted(tracks, key=lambda t: natural_key(t.name))


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# --------------------------------------------------------------------------- #
#  Tags
# --------------------------------------------------------------------------- #
def read_metadata(track: Track) -> Track:
    """Fill title/artist/album/duration from the file tags (best effort)."""
    track.meta_loaded = True
    try:
        from mutagen import File as MutagenFile  # imported lazily: optional dep
    except Exception:
        return track

    audio = None
    try:
        audio = MutagenFile(track.path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        return track

    tags = getattr(audio, "tags", None) or {}

    def first(*keys):
        for key in keys:
            try:
                value = tags.get(key)
            except Exception:
                value = None
            if not value:
                continue
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            text = str(value).strip()
            if text:
                return text
        return ""

    title = first("title")
    artist = first("artist", "albumartist", "performer", "composer")
    album = first("album")

    if title:
        track.title = title
    if artist:
        track.artist = artist
    if album:
        track.album = album

    try:
        info = getattr(audio, "info", None)
        length = getattr(info, "length", 0.0)
        track.duration = float(length or 0.0)
        track.bitrate = int(getattr(info, "bitrate", 0) or 0)
        track.sample_rate = int(getattr(info, "sample_rate", 0) or 0)
    except Exception:
        track.duration = 0.0

    mime = getattr(audio, "mime", None)
    if isinstance(mime, (list, tuple)) and mime:
        raw = str(mime[0]).split("/")[-1].upper()
        pretty = {"MPEG": "MP3", "MP4": "M4A", "X-WAV": "WAV", "OGG": "OGG",
                  "FLAC": "FLAC", "OPUS": "OPUS"}.get(raw)
        if pretty:
            track.codec = pretty
    return track


def format_time(seconds: float) -> str:
    """0:00 style clock (or '-:--' when unknown)."""
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return "-:--"
    if seconds <= 0 or seconds != seconds:
        return "-:--"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
