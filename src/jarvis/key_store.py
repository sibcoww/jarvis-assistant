import json
from pathlib import Path
from typing import Dict, Tuple

KEYS_PATH = Path.home() / ".jarvis" / "keys.json"
DEFAULT_KEYS = {
    "openrouter_api_key": "",
    "picovoice_access_key": "",
}


def ensure_keys_file() -> Tuple[Dict[str, str], bool]:
    """Load keys, creating a default file if missing.

    Returns:
        keys: dict with known keys
        created: True if file was created now
    """
    created = False
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not KEYS_PATH.exists():
        KEYS_PATH.write_text(json.dumps(DEFAULT_KEYS, ensure_ascii=False, indent=2), encoding="utf-8")
        created = True

    try:
        data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("keys.json corrupted: expected object")
    except Exception:
        data = DEFAULT_KEYS.copy()
        KEYS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        created = True

    # Ensure all defaults exist
    for k, v in DEFAULT_KEYS.items():
        data.setdefault(k, v)

    return data, created


def save_keys(keys: Dict[str, str]) -> None:
    data = DEFAULT_KEYS.copy()
    for k, v in keys.items():
        if k in data:
            data[k] = v
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEYS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
