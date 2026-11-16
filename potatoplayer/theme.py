"""Spoti Player — colour system, fonts and the Qt stylesheet.

The whole interface follows Spotify's look: a near-black frame, the layered
greys Spotify uses for its panels (#121212 / #181818 / #242424) and Spotify's
brand accents — the green (#1ED760) that owns the transport, library, volume
and equalizer, the lime (#CDF564) of the output section, the magenta
(#AF2896) and blue (#509BF5) of the logo gradient, and the blurple of
Discord. Folders are drawn as Spotify playlists, so their tiles use a small
set of brand gradients.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QLinearGradient

# --------------------------------------------------------------------------- #
#  Base surfaces — Spotify's grey stack
# --------------------------------------------------------------------------- #
BG0 = "#000000"          # app background (Spotify's black frame)
BG1 = "#121212"          # panel (Spotify's base surface)
BG2 = "#181818"          # cards / raised surfaces
BG3 = "#242424"          # inputs, chips, pressed surfaces
SUNK = "#0A0A0A"         # sunken (playlist, entries)
STROKE = "#2A2A2A"       # hairline border
STROKE_HI = "#4D4D4D"
MENU = "#282828"         # Spotify's context-menu surface
MENU_HOVER = "#3E3E3E"
PRESSED = "#000000"

TEXT = "#FFFFFF"
TEXT_DIM = "#B3B3B3"     # Spotify's "subdued" text
TEXT_MUTED = "#7C7C7C"

# --------------------------------------------------------------------------- #
#  Spotify brand accents
# --------------------------------------------------------------------------- #
GREEN = "#1ED760"        # Spotify's primary accent
GREEN_DARK = "#1DB954"   # pressed / secondary green
GREEN_LIGHT = "#3BE477"
GREEN_DEEP = "#0F6B33"
LIME = "#CDF564"         # Spotify's secondary (lime)
MAGENTA = "#AF2896"      # Spotify logo gradient (purple end)
BLUE = "#509BF5"         # Spotify logo gradient (blue end)
ORANGE = "#FFA42B"
CORAL = "#FF4D6D"

#: functional accents — Spotify is a monochrome-green interface, so every
#: area is a shade of the brand green rather than a hue of its own: green for
#: the library and the player, a brighter green for the volume drive, a soft
#: green for the output device and lime for the equalizer. Discord keeps its
#: own blurple (that is Discord's brand), warnings keep red/coral.
SOFT_GREEN = "#7CE88F"
VIOLET = GREEN           # library / playlist
CYAN = GREEN_DARK        # player / transport / seek
EMERALD = GREEN_LIGHT    # volume drive
AMBER = SOFT_GREEN       # output device
PINK = LIME              # equalizer
DISCORD = "#5865F2"      # discord rich presence
ROSE = "#F15E6C"         # errors / stop
SKY = BLUE
PURPLE = "#7B4DFF"

#: semantic aliases used by the UI
LIBRARY = VIOLET
PLAYER = CYAN
VOLUME = EMERALD
DEVICE = AMBER
EQUALIZER = PINK
DANGER = ROSE

SECTIONS = {
    "library": VIOLET,
    "player": CYAN,
    "volume": EMERALD,
    "device": AMBER,
    "eq": PINK,
    "discord": DISCORD,
    "danger": ROSE,
}

#: gradients used for playlist tiles (folders rendered as Spotify playlists),
#: built from Spotify's own brand hues (green / lime / blue / purple / magenta)
PLAYLIST_GRADIENTS = [
    ("#1ED760", "#0F6B33"),   # Spotify green (used for "All songs")
    ("#1ED760", "#509BF5"),
    ("#CDF564", "#1DB954"),
    ("#509BF5", "#7B4DFF"),
    ("#AF2896", "#509BF5"),
    ("#3BE477", "#0F6B33"),
    ("#1DB954", "#509BF5"),
    ("#7CE88F", "#1DB954"),
]

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def color(value: str | QColor, alpha: float | None = None) -> QColor:
    qcolor = QColor(value) if not isinstance(value, QColor) else QColor(value)
    if alpha is not None:
        qcolor.setAlphaF(max(0.0, min(1.0, alpha)))
    return qcolor


def mix(first: str | QColor, second: str | QColor, t: float) -> QColor:
    """Blend two colours (t=0 -> first, t=1 -> second)."""
    t = max(0.0, min(1.0, t))
    a, b = color(first), color(second)
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def lighten(value: str | QColor, t: float = 0.2) -> QColor:
    return mix(value, "#FFFFFF", t)


def darken(value: str | QColor, t: float = 0.25) -> QColor:
    return mix(value, "#000000", t)


def font(size: int = 10, weight: int = 400, family: str = FONT_FAMILY, italic: bool = False) -> QFont:
    f = QFont(family, size)
    f.setWeight(QFont.Weight(weight))
    f.setItalic(italic)
    return f


def mono(size: int = 9, weight: int = 400) -> QFont:
    f = QFont(MONO_FAMILY, size)
    f.setWeight(QFont.Weight(weight))
    return f


def vertical_gradient(top: str | QColor, bottom: str | QColor, height: float = 100.0) -> QLinearGradient:
    grad = QLinearGradient(0, 0, 0, height)
    grad.setColorAt(0.0, color(top))
    grad.setColorAt(1.0, color(bottom))
    return grad


def hexstr(value: str | QColor) -> str:
    return color(value).name()


def playlist_gradient(seed: str) -> tuple[str, str]:
    """Deterministic two-colour gradient for a folder shown as a playlist.

    Spotify gives every playlist a cover; folders do not have one, so they get
    a stable brand gradient derived from their name.
    """
    if not seed:
        return PLAYLIST_GRADIENTS[0]
    digest = 0
    for char in seed:
        digest = (digest * 31 + ord(char)) & 0xFFFFFFFF
    return PLAYLIST_GRADIENTS[digest % len(PLAYLIST_GRADIENTS)]


def dominant_color(image, fallback: str = GREEN) -> str:
    """Pick a vivid, representative colour out of a PIL image (album art).

    Used to tint the "now playing" area per song, the way Spotify lets each
    album's artwork colour the interface around it.
    """
    try:
        small = image.convert("RGB").resize((72, 72))
    except Exception:
        return fallback
    quantized = small.quantize(colors=12, method=2)          # 2 = FASTOCTREE
    palette = quantized.getpalette() or []
    counts = sorted(quantized.getcolors() or [], reverse=True)

    best, best_score = None, -1.0
    common, common_count = None, -1
    for count, index in counts[:12]:
        r, g, b = palette[index * 3:index * 3 + 3]
        qcolor = QColor(r, g, b)
        hue, sat, val = qcolor.getHsvF()[:3]                  # PySide returns (h, s, v, a)
        if val < 0.12:                                        # ignore near-black
            continue
        if count > common_count:
            common, common_count = qcolor, count
        if sat < 0.18 or val < 0.16:                          # greys
            continue
        score = (count / max(1, counts[0][0])) * (0.35 + sat) * (0.45 + val)
        if score > best_score:
            best, best_score = qcolor, score
    if best is None:
        # greyscale cover: keep its tone but push a little colour into it
        best = common
    if best is None:
        return fallback
    hue, sat, val = best.getHsvF()[:3]
    sat = min(1.0, max(0.45, sat))
    val = min(1.0, max(0.62, val))
    return QColor.fromHsvF(hue, sat, val).name()


# --------------------------------------------------------------------------- #
#  global stylesheet (Spotify: flat, 4-8 px radii, grey hairlines, green focus)
# --------------------------------------------------------------------------- #
def stylesheet() -> str:
    return f"""
    QWidget {{
        color: {TEXT};
        font-family: "{FONT_FAMILY}";
        font-size: 10pt;
    }}
    QMainWindow, QDialog {{ background: {BG0}; }}
    /* labels must never paint their own slab over the panels */
    QLabel {{ background: transparent; }}

    /* ---------- panels (Spotify: seamless #121212 surfaces, no borders) ---------- */
    Panel {{
        background: {BG1};
        border: none;
        border-radius: 8px;
    }}
    .QFrame#Card, QFrame#Card {{
        background: {BG2};
        border: none;
        border-radius: 8px;
    }}

    /* ---------- labels ---------- */
    QLabel#SectionTitle {{
        color: {TEXT};
        font-size: 11pt;
        font-weight: 700;
    }}
    QLabel#NowTitle {{ color: {TEXT}; font-size: 17pt; font-weight: 700; }}
    QLabel#NowArtist {{ color: {TEXT_DIM}; font-size: 11pt; }}
    QLabel#NowAlbum {{ color: {TEXT_MUTED}; font-size: 9pt; }}
    QLabel#NowTech {{ color: {TEXT_MUTED}; font-family: "{MONO_FAMILY}"; font-size: 8pt; }}
    /* the bottom play bar */
    QLabel#BarTitle {{ color: {TEXT}; font-size: 10pt; font-weight: 700; }}
    QLabel#BarArtist {{ color: {TEXT_DIM}; font-size: 8.5pt; }}
    QLabel#Time {{ color: {TEXT_DIM}; font-family: "{MONO_FAMILY}"; font-size: 9pt; }}
    QLabel#Status {{ color: {TEXT_DIM}; font-size: 8pt; }}
    QLabel#Hint {{ color: {TEXT_MUTED}; font-size: 8pt; }}
    QLabel#Count {{ color: {TEXT_MUTED}; font-size: 8pt; }}
    QLabel#AppName {{ color: {TEXT}; font-size: 19pt; font-weight: 800; }}
    /* the playlist hero (Spotify's big playlist header) */
    QLabel#HeroEyebrow {{ color: {TEXT}; font-size: 10pt; font-weight: 700; }}
    QLabel#HeroTitle {{ color: {TEXT}; font-size: 30pt; font-weight: 800; }}
    QLabel#HeroMeta {{ color: {TEXT_DIM}; font-size: 10pt; font-weight: 600; }}

    /* ---------- inputs ---------- */
    QLineEdit {{
        background: {BG3};
        border: 1px solid transparent;
        border-radius: 4px;
        padding: 8px 12px;
        color: {TEXT};
        selection-background-color: {GREEN};
        selection-color: #000000;
    }}
    QLineEdit:focus {{ border: 1px solid {TEXT}; }}
    QLineEdit#Placeholder {{ color: {TEXT_MUTED}; }}

    /* the search pill in the header (Spotify: dark grey, fully rounded) */
    QLineEdit#SearchPill {{
        background: {BG3};
        border: 1px solid transparent;
        border-radius: 500px;
        padding: 6px 14px 6px 36px;
        color: {TEXT};
        font-size: 10pt;
        selection-background-color: {GREEN};
        selection-color: #000000;
    }}
    QLineEdit#SearchPill:hover {{ background: {STROKE}; }}
    QLineEdit#SearchPill:focus {{
        background: {BG3};
        border: 1px solid {TEXT};
    }}

    QComboBox {{
        background: {BG3};
        border: 1px solid transparent;
        border-radius: 4px;
        padding: 4px 10px;
        color: {TEXT};
        min-height: 22px;
    }}
    QComboBox:hover {{ background: {STROKE}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox::down-arrow {{
        image: none;
        width: 0; height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid {TEXT_DIM};
        margin-right: 8px;
    }}
    QComboBox::down-arrow:hover {{ border-top-color: {GREEN}; }}
    QCheckBox {{
        background: transparent;
        spacing: 8px;
        color: {TEXT};
    }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1px solid {STROKE_HI};
        border-radius: 4px;
        background: {BG3};
    }}
    QCheckBox::indicator:hover {{ border: 1px solid {TEXT}; }}
    QCheckBox::indicator:checked {{
        background: {GREEN};
        border: 1px solid {GREEN};
    }}
    QCheckBox:disabled {{ color: {TEXT_MUTED}; }}
    QComboBox QAbstractItemView {{
        background: {STROKE};
        border: none;
        border-radius: 4px;
        selection-background-color: {STROKE_HI};
        selection-color: {TEXT};
        outline: none;
        padding: 4px;
    }}

    /* ---------- playlist ---------- */
    QListWidget#Playlist {{
        background: transparent;
        border: none;
        border-radius: 0;
        outline: none;
        padding: 0;
    }}
    QListWidget#Playlist::item {{ border: none; }}
    QListWidget#Online {{
        background: transparent;
        border: none;
        outline: none;
        padding: 0;
    }}
    QListWidget#Online::item {{ border: none; }}

    /* ---------- playlists (folders) list ---------- */
    QListWidget#Playlists {{
        background: transparent;
        border: none;
        border-radius: 4px;
        outline: none;
        padding: 0;
        font-size: 9pt;
    }}
    QListWidget#Playlists::item {{
        padding: 2px 4px;
        border-radius: 6px;
        color: {TEXT_DIM};
    }}
    QListWidget#Playlists::item:hover {{ background: {BG2}; color: {TEXT}; }}
    QListWidget#Playlists::item:selected {{ background: {STROKE}; color: {TEXT}; }}

    /* ---------- scrollbars (Spotify: thin grey, white on hover) ---------- */
    QScrollBar:vertical {{
        background: transparent; width: 9px; margin: 4px 2px 4px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {STROKE_HI}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {TEXT_DIM}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: transparent; height: 0; width: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent; height: 9px; margin: 0 4px 2px 4px;
    }}
    QScrollBar::handle:horizontal {{ background: {STROKE_HI}; border-radius: 5px; min-width: 30px; }}
    QScrollBar::handle:horizontal:hover {{ background: {TEXT_DIM}; }}

    /* ---------- menus ---------- */
    QMenu {{
        background: {MENU};
        border: none;
        border-radius: 4px;
        padding: 4px;
        color: {TEXT};
    }}
    QMenu::item {{ padding: 8px 18px 8px 12px; border-radius: 3px; }}
    QMenu::item:selected {{ background: {MENU_HOVER}; color: {TEXT}; }}
    QMenu::separator {{ height: 1px; background: {MENU_HOVER}; margin: 5px 8px; }}

    /* ---------- tooltips ---------- */
    QToolTip {{
        background: {MENU};
        color: {TEXT};
        border: none;
        border-radius: 4px;
        padding: 6px 10px;
    }}

    /* ---------- dialogs ---------- */
    QDialog {{ background: {BG0}; }}
    QMessageBox {{ background: {BG1}; }}
    QMessageBox QLabel {{ color: {TEXT}; }}
    """
