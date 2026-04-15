"""
Поиск типичных .exe на Windows для секции apps в config.json.
Не перезаписывает уже рабочие пути пользователя; добавляет новые программы.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def _norm_path(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/")


def _first_glob_exe(parent: Path, pattern: str) -> Path | None:
    """Первый подходящий файл (для Discord/Slack: app-*/App.exe)."""
    if not parent.is_dir():
        return None
    matches = sorted(parent.glob(pattern), key=lambda x: str(x))
    for p in reversed(matches):
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _config_path_works(val: str) -> bool:
    """True, если путь к .exe существует или это команда из PATH (notepad, cmd, …)."""
    v = (val or "").strip()
    if not v:
        return False
    low = v.lower().split()[0] if v else ""
    if shutil.which(low):
        return True
    try:
        return Path(v).expanduser().is_file()
    except OSError:
        return False


# Синонимы только для ключей, которые реально добавили/обновили (не трогаем чужие).
_ALIASES_FOR_APP: dict[str, tuple[str, ...]] = {
    "firefox": ("firefox", "файрфокс", "огнелис", "мозилла"),
    "discord": ("discord", "дискорд"),
    "spotify": ("spotify", "спотифай"),
    "zoom": ("zoom", "зум"),
    "steam": ("steam", "стим"),
    "obs": ("obs", "обс"),
    "slack": ("slack", "слак"),
    "vlc": ("vlc", "влц"),
    "brave": ("brave", "брейв"),
    "teams": ("teams", "тимс", "microsoft teams", "майкрософт тимс"),
    "whatsapp": ("whatsapp", "whatsapp desktop", "ватсап", "ватсапп", "вотсап", "вацап"),
    "notion": ("notion", "ноушн"),
    "epic": ("epic", "epic games", "епик"),
}


def scan_common_apps() -> dict[str, str]:
    """
    Ищет известные установки в стандартных местах.
    Ключи — имена для секции apps (и для синонимов).
    """
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")

    pairs: list[tuple[str, list[Path]]] = [
        (
            "browser",
            [
                Path(pf) / "Google/Chrome/Application/chrome.exe",
                Path(pf86) / "Google/Chrome/Application/chrome.exe",
                Path(pf) / "Microsoft/Edge/Application/msedge.exe",
                Path(pf86) / "Microsoft/Edge/Application/msedge.exe",
            ],
        ),
        (
            "brave",
            [
                Path(pf) / "BraveSoftware/Brave-Browser/Application/brave.exe",
                Path(local) / "BraveSoftware/Brave-Browser/Application/brave.exe",
            ],
        ),
        ("firefox", [Path(pf) / "Mozilla Firefox/firefox.exe", Path(pf86) / "Mozilla Firefox/firefox.exe"]),
        (
            "telegram",
            [
                Path(roaming) / "Telegram Desktop/Telegram.exe",
                Path(local) / "Telegram Desktop/Telegram.exe",
            ],
        ),
        (
            "vscode",
            [
                Path(local) / "Programs/Microsoft VS Code/Code.exe",
                Path(pf) / "Microsoft VS Code/Code.exe",
                Path(pf86) / "Microsoft VS Code/Code.exe",
            ],
        ),
        ("discord", []),  # через glob ниже
        ("slack", []),
        ("spotify", [Path(roaming) / "Spotify/Spotify.exe", Path(pf) / "Spotify/Spotify.exe"]),
        ("zoom", [Path(roaming) / "Zoom/bin/Zoom.exe"]),
        ("steam", [Path(pf86) / "Steam/steam.exe", Path(pf) / "Steam/steam.exe"]),
        ("obs", [Path(pf) / "obs-studio/bin/64bit/obs64.exe"]),
        ("vlc", [Path(pf) / "VideoLAN/VLC/vlc.exe", Path(pf86) / "VideoLAN/VLC/vlc.exe"]),
        ("teams", [Path(local) / "Microsoft/Teams/current/Teams.exe"]),
        ("whatsapp", [Path(local) / "WhatsApp/WhatsApp.exe"]),
        ("notion", [Path(local) / "Programs/Notion/Notion.exe", Path(pf) / "Notion/Notion.exe"]),
        (
            "epic",
            [Path(pf86) / "Epic Games/Launcher/Portal/Binaries/Win32/EpicGamesLauncher.exe"],
        ),
    ]

    out: dict[str, str] = {}

    disc = _first_glob_exe(Path(local) / "Discord", "app-*/Discord.exe")
    if disc:
        out["discord"] = _norm_path(disc)

    sl = _first_glob_exe(Path(local) / "Slack", "app-*/Slack.exe")
    if sl:
        out["slack"] = _norm_path(sl)

    for key, paths in pairs:
        if key in out:
            continue
        for path in paths:
            try:
                if path and path.is_file():
                    out[key] = _norm_path(path)
                    break
            except OSError:
                continue

    return out


def _merge_synonyms(data: dict[str, Any], app_keys_updated: set[str]) -> list[str]:
    """Добавляет русские/английские алиасы для новых приложений (не перезаписывает существующие)."""
    syn = data.get("synonyms")
    if not isinstance(syn, dict):
        syn = {}
        data["synonyms"] = syn

    added: list[str] = []
    for app_key in app_keys_updated:
        for alias in _ALIASES_FOR_APP.get(app_key, ()):
            a = alias.strip().lower()
            if not a or a in syn:
                continue
            syn[a] = app_key
            added.append(a)
    return added


def merge_scanned_apps_into_config(config_path: str | Path) -> dict[str, Any]:
    """
    Дополняет apps: новые ключи с диска, битые пути — замена найденным.
    Рабочие пути пользователя (другой диск/портативная сборка) не трогаем.
    """
    path = Path(config_path)
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(raw)

    apps = data.get("apps")
    if not isinstance(apps, dict):
        apps = {}
        data["apps"] = apps

    scanned = scan_common_apps()
    updated: list[str] = []
    skipped_same: list[str] = []
    skipped_kept_user: list[str] = []

    keys_touched_for_synonyms: set[str] = set()

    for key, new_val in scanned.items():
        old = apps.get(key)
        if old == new_val:
            skipped_same.append(key)
            continue
        if old is not None and str(old).strip() and _config_path_works(str(old)):
            skipped_kept_user.append(key)
            continue
        apps[key] = new_val
        updated.append(key)
        keys_touched_for_synonyms.add(key)

    if "notepad" not in apps:
        apps["notepad"] = "notepad"
        updated.append("notepad")
        keys_touched_for_synonyms.add("notepad")

    synonyms_added: list[str] = []
    if keys_touched_for_synonyms:
        synonyms_added = _merge_synonyms(data, keys_touched_for_synonyms)

    if not updated and not synonyms_added:
        return {
            "updated": [],
            "skipped_unchanged": skipped_same,
            "skipped_kept_user": skipped_kept_user,
            "discovered": list(scanned.keys()),
        }

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "updated": updated,
        "skipped_unchanged": skipped_same,
        "skipped_kept_user": skipped_kept_user,
        "discovered": list(scanned.keys()),
        "synonyms_added": synonyms_added,
    }
