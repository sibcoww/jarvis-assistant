import json
from pathlib import Path

from src.jarvis import key_store


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_save_preserves_other_keys(tmp_path, monkeypatch):
    keys_path = tmp_path / "keys.json"
    monkeypatch.setattr(key_store, "KEYS_PATH", keys_path)

    key_store.save_keys({"openai_api_key": "sk-123"})
    key_store.save_keys({"picovoice_access_key": "pv-456"})

    data, _ = key_store.ensure_keys_file()
    assert data["openai_api_key"] == "sk-123"
    assert data["picovoice_access_key"] == "pv-456"


def test_empty_save_does_not_overwrite(tmp_path, monkeypatch):
    keys_path = tmp_path / "keys.json"
    monkeypatch.setattr(key_store, "KEYS_PATH", keys_path)

    key_store.save_keys({"openai_api_key": "sk-abc"})
    key_store.save_keys({"openai_api_key": "  "})

    data = _load(keys_path)
    assert data["openai_api_key"] == "sk-abc"


def test_partial_update_is_merged(tmp_path, monkeypatch):
    keys_path = tmp_path / "keys.json"
    monkeypatch.setattr(key_store, "KEYS_PATH", keys_path)

    key_store.save_keys({"openai_api_key": "sk-old"})
    key_store.save_keys({"picovoice_access_key": "pv-keep"})

    key_store.save_keys({"openai_api_key": "sk-new"})

    data = _load(keys_path)
    assert data["openai_api_key"] == "sk-new"
    assert data["picovoice_access_key"] == "pv-keep"


def test_unknown_keys_are_ignored(tmp_path, monkeypatch):
    keys_path = tmp_path / "keys.json"
    monkeypatch.setattr(key_store, "KEYS_PATH", keys_path)

    key_store.save_keys({"openai_api_key": "oa-456"})
    key_store.save_keys({"openrouter_api_key": "or-123"})

    data = _load(keys_path)
    assert data["openai_api_key"] == "oa-456"
    assert "openrouter_api_key" not in data
