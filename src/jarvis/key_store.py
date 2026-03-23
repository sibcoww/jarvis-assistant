import json
from pathlib import Path
from typing import Dict, Tuple

KEYS_PATH = Path.home() / ".jarvis" / "keys.json"
DEFAULT_KEYS = {
    "openai_api_key": "",
    "picovoice_access_key": "",
}


def _write_atomic(path: Path, data: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def ensure_keys_file() -> Tuple[Dict[str, str], bool]:
    """Load keys, creating a default file if missing.

    Returns:
        keys: dict with known keys
        created: True if file was created now
    """
    created = False
    KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not KEYS_PATH.exists():
        _write_atomic(KEYS_PATH, DEFAULT_KEYS)
        created = True

    try:
        data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("keys.json corrupted: expected object")
    except Exception:
        data = DEFAULT_KEYS.copy()
        _write_atomic(KEYS_PATH, data)
        created = True

    # Ensure all defaults exist
    for k, v in DEFAULT_KEYS.items():
        data.setdefault(k, v)

    return data, created


def save_keys(keys: Dict[str, str]) -> None:
    current, _ = ensure_keys_file()
    data = DEFAULT_KEYS.copy()
    data.update(current)

    for k, v in keys.items():
        if k not in data:
            continue
        if not isinstance(v, str):
            continue
        normalized = v.strip()
        if not normalized:
            # защита от затирания существующего ключа пустым значением
            continue
        data[k] = normalized

    _write_atomic(KEYS_PATH, data)
