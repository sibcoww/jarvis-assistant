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
    executor._ai_client = DummyAIClient(
        ['{"mode":"reply","message":"Привет"}'],
        [None],
    )

    handled = executor.handle_unrecognized_command("что ты умеешь")

    assert handled is True
    assert executor._chat_history == [
        {"role": "user", "content": "что ты умеешь"},
        {"role": "assistant", "content": "Привет"},
    ]
    assert executor._ai_client.calls == 1


def test_ai_empty_first_retry_success(monkeypatch, tmp_path):
    executor = make_executor(
        monkeypatch,
        tmp_path,
    )
    executor._ai_client = DummyAIClient(
        responses=[None, '{"mode":"reply","message":"Готов"}'],
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
    # Единый turn: при 429 не делаем второй проход (в отличие от старой пары interpret+chat).
    assert executor._ai_client.calls == 1
    assert executor._chat_history == []


def test_fuzzy_reset_session_phrase_is_handled_locally(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=['{"mode":"action","intent":"browser_search","slots":{"query":"со сью"}}'],
        errors=[None],
    )
    messages = []
    executor._log = lambda msg: messages.append(msg)
    executor._chat_history = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]

    handled = executor.handle_unrecognized_command("очисти со сью")
    assert handled is True
    assert executor._chat_history == []
    assert executor._ai_client.calls == 0
    assert any("Контекст очищен" in m for m in messages)


def test_show_history_phrase_variant(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    messages = []
    executor._log = lambda msg: messages.append(msg)
    executor._chat_history = [
        {"role": "user", "content": "первый вопрос"},
        {"role": "assistant", "content": "первый ответ"},
        {"role": "user", "content": "второй вопрос"},
        {"role": "assistant", "content": "второй ответ"},
    ]

    handled = executor.handle_unrecognized_command("покажи историю")
    assert handled is True
    assert any("Недавняя история" in m for m in messages)
    assert any("первый вопрос" in m for m in messages)


def test_ai_interprets_set_volume_command(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=['{"mode":"action","intent":"set_volume","slots":{"value":50}}'],
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


def test_pending_args_light_for_open_site(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=['{"mode":"action","intent":"browser_navigate","slots":{"url":"https://wikipedia.org/"}}'],
        errors=[None],
    )
    logs = []
    executor._log = lambda msg: logs.append(msg)
    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    assert executor.handle_unrecognized_command("открой сайт") is True
    assert executor._ai_client.calls == 0
    assert any("Какой сайт" in m for m in logs)

    assert executor.handle_unrecognized_command("wikipedia.org") is True
    assert executor._ai_client.calls == 1
    assert called["intent"]["type"] == "browser_navigate"


def test_pending_args_light_for_open_program(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    logs = []
    executor._log = lambda msg: logs.append(msg)
    executor._ai_client = DummyAIClient(responses=['{"mode":"reply","message":"ok"}'], errors=[None])

    assert executor.handle_unrecognized_command("открой программу") is True
    assert any("Какую программу открыть" in m for m in logs)


def test_pending_args_light_for_set_volume(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    logs = []
    executor._log = lambda msg: logs.append(msg)
    executor._ai_client = DummyAIClient(
        responses=['{"mode":"action","intent":"set_volume","slots":{"value":35}}'],
        errors=[None],
    )
    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    assert executor.handle_unrecognized_command("поставь громкость") is True
    assert any("На какую громкость" in m for m in logs)

    assert executor.handle_unrecognized_command("35") is True
    assert called["intent"]["type"] == "set_volume"
    assert called["intent"]["slots"]["value"] == 35

    assert executor.handle_unrecognized_command("поставь громкость") is True
    assert executor.handle_unrecognized_command("девяносто") is True
    assert called["intent"]["type"] == "set_volume"
    assert called["intent"]["slots"]["value"] == 90


def test_volume_up_down_phrases_are_not_pending_clarification(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    assert executor._detect_missing_args("сделай тише") is None
    assert executor._detect_missing_args("убавь громкость") is None
    assert executor._detect_missing_args("сделай громче") is None
    assert executor._detect_missing_args("добавь громкость") is None


def test_ai_interprets_open_youtube_command(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=['{"mode":"action","intent":"browser_navigate","slots":{"site":"youtube.com"}}'],
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
            '{"mode":"action","intent":"browser_navigate","slots":{"url":"https://www.youtube.com/results?search_query=MrBeast"}}'
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
            '{"mode":"action","intent":"browser_navigate","slots":{"url":"https://www.google.com/search?q=python+unittest"}}'
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


def test_informational_film_question_does_not_run_browser(monkeypatch, tmp_path):
    """Ложное браузерное действие по информационному запросу переводится в текстовый ответ."""
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=[
            '{"mode":"action","intent":"browser_navigate","slots":{"url":"https://www.kinopoisk.ru/index.php?kp_query=1"}}',
            "Кратко: «1+1» (Intouchables) — французская драма о дружбе.",
        ],
        errors=[None, None],
    )
    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    handled = executor.handle_unrecognized_command("Расскажи кратко про фильм 1+1")

    assert handled is True
    assert "intent" not in called
    assert "Intouchables" in executor._chat_history[-1]["content"]
    assert executor._ai_client.calls == 2


def test_ai_site_query_fallback_to_google_search(monkeypatch, tmp_path):
    """Если модель вернула только site+query без url — безопасный общий поиск по строке."""
    executor = make_executor(monkeypatch, tmp_path)
    executor._resolve_site_query_url_with_ai = lambda _site, _query: None
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


def test_ai_site_query_resolves_direct_page_url(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._resolve_site_query_url_with_ai = (
        lambda _site, _query: "https://ru.wikipedia.org/wiki/Python"
    )
    out = executor._validate_ai_command_payload(
        {
            "mode": "command",
            "intent": "browser_navigate",
            "slots": {"site": "wikipedia.org", "query": "Python"},
        }
    )
    assert out is not None
    assert out["type"] == "browser_navigate"
    assert out["slots"]["url"] == "https://ru.wikipedia.org/wiki/Python"


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




def test_interpret_command_includes_dialog_recap(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    captured: dict = {}
    dummy = DummyAIClient(responses=['{"mode":"chat"}'], errors=[None])
    _orig = dummy.get_response

    def wrap_get(q, history=None, **_kwargs):
        captured["prompt"] = q
        return _orig(q, history=history, **_kwargs)

    dummy.get_response = wrap_get
    executor._ai_client = dummy
    executor._chat_history = [
        {"role": "user", "content": "\u043d\u0430\u0439\u0434\u0438 \u0430\u043d\u0438\u043c\u0435 \u0411\u0435\u0440\u0441\u0435\u0440\u043a"},
        {"role": "assistant", "content": "\u041e\u0442\u043a\u0440\u044b\u043b \u043f\u043e\u0438\u0441\u043a \u0432 Google."},
    ]
    executor._interpret_command_with_ai(
        "\u043e\u0442\u043a\u0440\u043e\u0439 \u044d\u0442\u043e \u043d\u0430 \u041a\u0438\u043d\u043e\u043f\u043e\u0438\u0441\u043a\u0435"
    )
    prompt = captured.get("prompt", "")
    assert "\u0411\u0435\u0440\u0441\u0435\u0440\u043a" in prompt
    assert "\u041a\u0438\u043d\u043e\u043f\u043e\u0438\u0441\u043a" in prompt


def test_build_ai_request_history_without_memory_prefix(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._chat_history = [
        {"role": "user", "content": "uuuuuu"},
        {"role": "assistant", "content": "aaaaaa"},
        {"role": "user", "content": "last-q"},
        {"role": "assistant", "content": "last-a"},
    ]
    hist = executor._build_ai_request_history()
    assert any(m.get("role") == "user" and m.get("content") == "last-q" for m in hist)
    mem_blocks = [m for m in hist if m["role"] == "system" and "Память о пользователе" in m["content"]]
    assert mem_blocks == []
