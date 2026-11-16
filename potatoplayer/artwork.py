"""Album-art extraction (embedded + folder images), caching and placeholders."""

from __future__ import annotations

import base64
import hashlib
import io
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import theme

COVER_NAMES = (
    "cover", "folder", "front", "album", "albumart", "albumartsmall",
    "artwork", "thumb", "poster", "cd",
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")


def _mix_hex(first: str, second: str, t: float) -> str:
    """PIL-friendly linear colour blend between two #rrggbb strings."""
    t = max(0.0, min(1.0, t))
    a = tuple(int(first[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(second[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


# --------------------------------------------------------------------------- #
#  Embedded / sidecar art
# --------------------------------------------------------------------------- #
def _embedded_art(path: str) -> bytes | None:
    try:
        from mutagen import File as MutagenFile
    except Exception:
        return None
    try:
        audio = MutagenFile(path)
    except Exception:
        return None
    if audio is None:
        return None

    tags = getattr(audio, "tags", None)
    if tags is not None:
        # ID3 (mp3, wav with id3, aiff ...)
        getall = getattr(tags, "getall", None)
        if callable(getall):
            try:
                frames = getall("APIC")
                if frames:
                    return bytes(frames[0].data)
            except Exception:
                pass
        # MP4 / M4A cover art
        try:
            covr = tags.get("covr") if hasattr(tags, "get") else None
            if covr:
                return bytes(covr[0])
        except Exception:
            pass
        # Ogg / Opus (base64 FLAC picture block)
        try:
            block = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
            if block:
                from mutagen.flac import Picture
                raw = block[0] if isinstance(block, (list, tuple)) else block
                pic = Picture(base64.b64decode(raw))
                if pic.data:
                    return bytes(pic.data)
        except Exception:
            pass

    # FLAC
    pictures = getattr(audio, "pictures", None)
    if pictures:
        try:
            return bytes(pictures[0].data)
        except Exception:
            pass

    # WMA / ASF  (WM/Picture)
    try:
        if hasattr(tags, "get"):
            wm = tags.get("WM/Picture")
            if wm:
                return _parse_wm_picture(bytes(wm[0].value))
    except Exception:
        pass

    return None


def _parse_wm_picture(data: bytes) -> bytes | None:
    """Parse the ASF WM/Picture blob and return the image bytes."""
    try:
        if len(data) < 6:
            return None
        pos = 5  # 1 byte picture type + 4 bytes size
        # MIME type: UTF-16LE, NUL terminated
        end = pos
        while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
            end += 2
        pos = end + 2
        end = pos
        while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
            end += 2
        pos = end + 2
        return data[pos:] or None
    except Exception:
        return None


def _sidecar_art(path: str) -> bytes | None:
    stem, _ = os.path.splitext(path)
    for ext in IMAGE_EXTS:
        candidate = stem + ext
        if os.path.isfile(candidate):
            try:
                return Path(candidate).read_bytes()
            except OSError:
                pass
    return None


def _folder_art(path: str) -> bytes | None:
    folder = os.path.dirname(path)
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    lowered = {e.lower(): e for e in entries}
    for base in COVER_NAMES:
        for ext in IMAGE_EXTS:
            hit = lowered.get(base + ext)
            if hit:
                try:
                    return Path(folder, hit).read_bytes()
                except OSError:
                    pass
    return None


# --------------------------------------------------------------------------- #
#  Loader with on-disk cache
# --------------------------------------------------------------------------- #
class ArtworkLoader:
    """Finds cover art for a track, caches raw bytes on disk and crops/scales."""

    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, Image.Image | None] = {}

    # -- raw -------------------------------------------------------------- #
    def _cache_key(self, track) -> str:
        try:
            stat = os.stat(track.path)
            stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
        except OSError:
            stamp = "0"
        return hashlib.sha1(f"{track.path}|{stamp}".encode("utf-8", "replace")).hexdigest()

    def raw_bytes(self, track) -> bytes | None:
        key = self._cache_key(track)
        cache_file = self.cache_dir / f"{key}.art"
        miss_file = self.cache_dir / f"{key}.none"
        if cache_file.is_file():
            try:
                return cache_file.read_bytes()
            except OSError:
                return None
        if miss_file.is_file():
            return None

        data = _embedded_art(track.path) or _sidecar_art(track.path) or _folder_art(track.path)
        try:
            if data:
                cache_file.write_bytes(data)
            else:
                miss_file.write_bytes(b"")
        except OSError:
            pass
        return data

    # -- PIL images ------------------------------------------------------- #
    def image(self, track, size: int | tuple[int, int]) -> Image.Image:
        """Cover art for *track*, falling back to a generated placeholder."""
        if isinstance(size, int):
            size = (size, size)
        key = f"{self._cache_key(track)}:{size[0]}x{size[1]}"
        if key in self._mem:
            cached = self._mem[key]
            if cached is not None:
                return cached

        img = None
        data = self.raw_bytes(track)
        if data:
            try:
                src = Image.open(io.BytesIO(data))
                src = ImageOps.exif_transpose(src)
                img = ImageOps.fit(src.convert("RGB"), size, method=Image.LANCZOS, centering=(0.5, 0.4))
            except Exception:
                img = None
        if img is None:
            img = self.placeholder(getattr(track, "display_title", "") or "?", size)
        if len(self._mem) > 60:
            self._mem.clear()
        self._mem[key] = img
        return img

    # -- placeholder ------------------------------------------------------ #
    @staticmethod
    def placeholder(seed: str, size=(320, 320)) -> Image.Image:
        """Generated 'no cover' art: the Spotify playlist look — a brand gradient
        tile with a white note and the song's initial."""
        w, h = size
        top, bottom = theme.playlist_gradient(seed or "")
        img = Image.new("RGB", (w, h), bottom)
        draw = ImageDraw.Draw(img)
        for y in range(h):                      # top-left → bottom-right-ish gradient
            t = y / max(1, h - 1)
            draw.line([(0, y), (w, y)], fill=_mix_hex(top, bottom, t * 0.95))

        # soft corner glow, like Spotify's generated playlist covers
        glow = Image.new("RGB", (w, h), bottom)
        gdraw = ImageDraw.Draw(glow)
        r = int(min(w, h) * 0.5)
        gdraw.ellipse([w - r, -r // 2, w + r, r + r // 2], fill=top)
        gdraw.ellipse([-r // 2, h - r, r, h + r // 2], fill=bottom)
        glow = glow.filter(ImageFilter.GaussianBlur(max(6, int(min(w, h) * 0.10))))
        img = Image.blend(img, glow, 0.35)
        draw = ImageDraw.Draw(img)

        # music note drawn from primitives (no font required)
        s = min(w, h) / 320.0
        cx, cy = w * 0.40, h * 0.60
        head_r = 26 * s
        draw.ellipse([cx - head_r, cy - head_r * 0.78, cx + head_r, cy + head_r * 0.78],
                     fill=theme.TEXT)
        stem_w = 8 * s
        draw.rectangle([cx + head_r - stem_w, cy - 105 * s, cx + head_r, cy], fill=theme.TEXT)
        draw.polygon(
            [(cx + head_r - stem_w, cy - 105 * s), (cx + head_r + 46 * s, cy - 76 * s),
             (cx + head_r + 46 * s, cy - 46 * s), (cx + head_r - stem_w, cy - 75 * s)],
            fill=theme.TEXT,
        )
        if seed:
            initial = seed.strip()[:1].upper()
            try:
                from PIL import ImageFont
                fnt = ImageFont.truetype("segoeuib.ttf", int(min(w, h) * 0.16))
                draw.text((w * 0.78, h * 0.22), initial, font=fnt,
                          fill=theme.color("#FFFFFF", 0.85).name(), anchor="mm")
            except Exception:
                pass
        return img


# --------------------------------------------------------------------------- #
#  Helpers used by the UI
# --------------------------------------------------------------------------- #
def render_app_icon(size: int = 96) -> Image.Image:
    """Draw the Spoti Player app icon: Spotify green disc, black sound bars."""
    scale = size / 96.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    inset = int(3 * scale)
    disc = [inset, inset, size - 1 - inset, size - 1 - inset]
    draw.ellipse(disc, fill=theme.GREEN)

    # three bars, middle one longest — the little "now playing" mark
    bar_h = 8 * scale
    gap = 4.5 * scale
    for i, frac in enumerate((0.52, 0.78, 0.62)):
        length = size * frac * 0.62
        x0 = size / 2 - length / 2
        x1 = size / 2 + length / 2
        y = size / 2 + (i - 1) * (bar_h + gap)
        draw.rounded_rectangle([x0, y - bar_h / 2, x1, y + bar_h / 2], radius=bar_h / 2,
                               fill="#000000")
    return img


def rounded(img: Image.Image, radius: int) -> Image.Image:
    """Return *img* (RGB) with rounded corners and a transparent outside."""
    img = img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def with_border(img: Image.Image, radius: int, color: str = theme.STROKE_HI, width: int = 2) -> Image.Image:
    """Draw a rounded border around a rounded image (returns RGBA)."""
    base = img.convert("RGBA")
    w, h = base.size
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        [width // 2, width // 2, w - 1 - width // 2, h - 1 - width // 2],
        radius=radius, outline=color, width=width,
    )
    return Image.alpha_composite(base, layer)


def glow(img: Image.Image, radius: int, color: str = theme.CYAN, strength: float = 0.45) -> Image.Image:
    """Add a soft outer glow behind a rounded image (returns a bigger RGBA)."""
    w, h = img.size
    pad = max(10, int(min(w, h) * 0.06))
    canvas = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    halo = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).rounded_rectangle(
        [pad - 2, pad - 2, pad + w + 1, pad + h + 1], radius=radius + 4, fill=color,
    )
    halo = halo.filter(ImageFilter.GaussianBlur(pad * 0.75))
    halo.putalpha(halo.getchannel("A").point(lambda v: int(v * strength)))
    canvas = Image.alpha_composite(canvas, halo)
    canvas.paste(img, (pad, pad), img)
    return canvas
