import types

import pytest

from jarvis import executor as executor_module
from jarvis.executor import Executor


class DummyPluginManager:
    def __init__(self, *args, **kwargs):
        pass

    def handle_intent(self, _intent_type, _slots):
        return False


class DummyAIClient:
    def __init__(self, responses, errors=None):
        self.responses = list(responses)
        self.errors = list(errors or [None] * len(self.responses))
        self.calls = 0
        self.last_error = None

    def is_enabled(self):
        return True

    def get_response(self, query, history=None, **_kwargs):
        self.calls += 1
        idx = min(self.calls - 1, len(self.responses) - 1)
        self.last_error = self.errors[idx] if idx < len(self.errors) else None
        return self.responses[idx]


def make_executor(monkeypatch, tmp_path):
    monkeypatch.setattr(executor_module, "PluginManager", DummyPluginManager)
    monkeypatch.setattr(executor_module, "ensure_keys_file", lambda: ({"openai_api_key": ""}, False))

    executor = Executor(config={"ai": {"enabled": True}})
    executor._chat_history_path = tmp_path / "history.json"
    executor._chat_history = []
    executor._save_chat_history = lambda: None
    executor._log = lambda msg: None
    return executor


def test_ai_success_updates_history(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(["Привет"], [None])

    handled = executor.handle_unrecognized_command("что ты умеешь")

    assert handled is True
    assert executor._chat_history == [
        {"role": "user", "content": "что ты умеешь"},
        {"role": "assistant", "content": "Привет"},
    ]
    assert executor._ai_client.calls == 2


def test_ai_empty_first_retry_success(monkeypatch, tmp_path):
    executor = make_executor(
        monkeypatch,
        tmp_path,
    )
    executor._ai_client = DummyAIClient(
        responses=[None, "Готов"],
        errors=["OpenAI вернул пустой текст", None],
    )

    handled = executor.handle_unrecognized_command("скажи статус")

    assert handled is True
    assert executor._ai_client.calls == 2
    assert executor._chat_history[-1] == {"role": "assistant", "content": "Готов"}


def test_ai_rate_limited_no_history(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=[None],
        errors=["OpenAI временно ограничил запросы (429)"],
    )

    handled = executor.handle_unrecognized_command("ответь")

    assert handled is False
    assert executor._ai_client.calls == 2
    assert executor._chat_history == []


def test_user_memory_commands(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    messages = []
    executor._log = lambda msg: messages.append(msg)
    executor._chat_history = [{"role": "user", "content": "x"}] * 6
    executor.memory._upsert_memory("временный контекст", "temporary", 2, ttl_days=14)
    executor.memory._upsert_memory("Пользователя зовут Алексей", "profile", 4)

    assert executor.handle_unrecognized_command("очисти память") is True
    assert any("Очищен недавний контекст" in m for m in messages)
    assert executor._chat_history == []
    assert any(m.get("type") == "profile" for m in executor.memory.memories)

    messages.clear()
    assert executor.handle_unrecognized_command("удали всю информацию обо мне") is True
    assert any("Вся информация о пользователе удалена" in m for m in messages)
    assert executor.memory.memories == []


def test_memory_phrase_variant_is_handled_locally(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    messages = []
    executor._log = lambda msg: messages.append(msg)

    handled = executor.handle_unrecognized_command("что ты помнишь обо мне")
    assert handled is True
    assert any("Память:" in m for m in messages)


def test_forget_all_phrase_variant(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    messages = []
    executor._log = lambda msg: messages.append(msg)
    executor.memory._upsert_memory("Никнейм пользователя: сибкош", "profile", 4, layer="core")

    handled = executor.handle_unrecognized_command("забудь всю информацию обо мне")
    assert handled is True
    assert executor.memory.memories == []
    assert any("Вся информация о пользователе удалена" in m for m in messages)


def test_add_note_personal_info_routes_to_memory(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=[
            '{"save": true, "layer": "core", "type": "fact", "value": "мне двадцать один год", "importance": 3}',
            "Принял.",
        ],
        errors=[None, None],
    )
    # Should route to memory path instead of plain note append.
    executor.run({"type": "add_note", "slots": {"text": "запомни информацию обо мне мне двадцать один год"}})
    assert any("мне двадцать один год" in m.get("text", "") for m in executor.memory.memories)


def test_ai_memory_suggestion_is_applied(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    # first call -> memory extraction JSON, second call -> normal AI answer
    executor._ai_client = DummyAIClient(
        responses=[
            '{"save": true, "layer": "core", "type": "profile", "key": "education", "value": "учусь на IT", "importance": 4}',
            "Отлично, понял.",
        ],
        errors=[None, None],
    )

    handled = executor.handle_unrecognized_command("я учусь на IT")
    assert handled is True
    assert executor._ai_client.calls >= 2
    assert any("учусь на IT" in m.get("text", "") for m in executor.memory.memories)


def test_ai_interprets_set_volume_command(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    # first AI call is command interpretation, should stop there
    executor._ai_client = DummyAIClient(
        responses=['{"mode":"command","intent":"set_volume","slots":{"value":50}}'],
        errors=[None],
    )

    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    handled = executor.handle_unrecognized_command("уменьши громкость до 50")

    assert handled is True
    assert called["intent"]["type"] == "set_volume"
    assert called["intent"]["slots"]["value"] == 50


def test_ai_interprets_open_youtube_command(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=['{"mode":"command","intent":"browser_navigate","slots":{"site":"youtube.com"}}'],
        errors=[None],
    )
    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    handled = executor.handle_unrecognized_command("открой ютуб в браузере")

    assert handled is True
    assert called["intent"]["type"] == "browser_navigate"
    assert "youtube" in called["intent"]["slots"]["url"]


def test_ai_interprets_youtube_channel_search(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=[
            '{"mode":"command","intent":"browser_navigate","slots":{"url":"https://www.youtube.com/results?search_query=MrBeast"}}'
        ],
        errors=[None],
    )
    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    handled = executor.handle_unrecognized_command("открой канал MrBeast на YouTube")

    assert handled is True
    assert called["intent"]["type"] == "browser_navigate"
    assert called["intent"]["slots"]["url"] == "https://www.youtube.com/results?search_query=MrBeast"


def test_ai_interprets_google_search_url(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=[
            '{"mode":"command","intent":"browser_navigate","slots":{"url":"https://www.google.com/search?q=python+unittest"}}'
        ],
        errors=[None],
    )
    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    handled = executor.handle_unrecognized_command("найди в гугле python unittest")

    assert handled is True
    assert called["intent"]["type"] == "browser_navigate"
    assert called["intent"]["slots"]["url"] == "https://www.google.com/search?q=python+unittest"


def test_ai_site_query_fallback_to_google_search(monkeypatch, tmp_path):
    """Если модель вернула только site+query без url — безопасный общий поиск по строке."""
    executor = make_executor(monkeypatch, tmp_path)
    out = executor._validate_ai_command_payload(
        {
            "mode": "command",
            "intent": "browser_navigate",
            "slots": {"site": "youtube", "query": "MrBeast"},
        }
    )
    assert out is not None
    assert "google.com/search" in out["slots"]["url"]
    assert "MrBeast" in out["slots"]["url"]


def test_ai_site_query_same_words_resolves_home_url(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(responses=["{}"], errors=[None])
    executor._resolve_site_home_url_with_ai = lambda _site: "https://www.twitch.tv/"
    out = executor._validate_ai_command_payload(
        {
            "mode": "command",
            "intent": "browser_navigate",
            "slots": {"site": "Twitch", "query": "twitch"},
        }
    )
    assert out is not None
    assert out["type"] == "browser_navigate"
    assert out["slots"]["url"] == "https://www.twitch.tv/"
