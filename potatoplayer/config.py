"""Persistent settings for Spoti Player (stored as JSON in %APPDATA%)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

APP_NAME = "SpotiPlayer"
#: older install names whose settings are adopted on first run
LEGACY_APP_NAME = "PotatoPlayer"
LEGACY_APP_NAMES = ("PotatoPlayer", "potatoplayer", "GreenPlayer")

DEFAULTS: dict = {
    "folder": "",
    "last_folder": "",
    "subfolders_for": "",
    "subfolder_choice": "All songs",
    "volume": 70,
    "muted": False,
    "device_id": "",
    "device_name": "Default",
    "vlc_dir": "",
    "sort_mode": "File name (A→Z)",
    "shuffle": False,
    "repeat": "all",              # off | all | one
    "eq_enabled": True,
    "eq_preset": "Flat",
    "eq_preamp": 0.0,
    "eq_bands": [0.0] * 10,
    "discord_enabled": True,
    "discord_client_id": "1528123590871093369",
    "discord_large_image": "spoti",
    "minimize_to_tray": True,
    "mini_bar": False,
    "radio_station": "lofi",
    "performance_mode": False,
    "geometry": "",
}


def _base_dir() -> Path:
    return Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home()))


def config_dir() -> Path:
    d = _base_dir() / APP_NAME
    if not d.exists():
        # adopt settings from an earlier install name, if there is one
        for legacy in LEGACY_APP_NAMES:
            old = _base_dir() / legacy
            if old.is_dir():
                try:
                    shutil.copytree(old, d)
                except OSError:
                    d.mkdir(parents=True, exist_ok=True)
                break
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    d = config_dir() / "artcache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in DEFAULTS:
                    cfg[k] = v
            bands = cfg.get("eq_bands")
            if not isinstance(bands, list) or len(bands) != 10:
                cfg["eq_bands"] = [0.0] * 10
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    try:
        tmp = config_path().with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, config_path())
    except OSError:
        pass
