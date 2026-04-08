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
    executor._session_summary_path = tmp_path / "session_summary.txt"
    executor._session_summary = ""
    executor._chat_history = []
    executor._save_chat_history = lambda: None
    executor._save_session_summary = lambda: None
    executor._log = lambda msg: None
    # Иначе MemoryStore читает реальный ~/.jarvis и тесты недетерминированы.
    executor.memory.base_dir = tmp_path
    executor.memory.profile_path = tmp_path / "user_profile.json"
    executor.memory.memories_path = tmp_path / "memories.jsonl"
    executor.memory.profile = {
        "name": "",
        "nickname": "",
        "preferences": [],
        "facts": [],
        "updated_at": None,
    }
    executor.memory.memories = []
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


def test_memory_management_commands(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    messages = []
    executor._log = lambda msg: messages.append(msg)
    executor.memory._upsert_memory("Пользователь учится на IT", "fact", 3, layer="core")
    executor.memory._upsert_memory("временный контекст", "temporary", 2, layer="session", ttl_days=7)

    assert executor.handle_unrecognized_command("покажи факты обо мне") is True
    assert any("Факты:" in m for m in messages)

    messages.clear()
    assert executor.handle_unrecognized_command("удали факт IT") is True
    assert all("учится на IT" not in m.get("text", "") for m in executor.memory.memories)
    assert any("Удалил факт" in m for m in messages)

    messages.clear()
    assert executor.handle_unrecognized_command("очисти временную память") is True
    assert all(m.get("layer") != "session" for m in executor.memory.memories)
    assert any("Временная память очищена" in m for m in messages)


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
            '{"mode":"reply","message":"Принял."}',
            '{"save": true, "layer": "core", "type": "fact", "value": "мне двадцать один год", "importance": 3}',
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
            '{"mode":"reply","message":"Отлично, понял."}',
            '{"save": true, "layer": "core", "type": "profile", "key": "education", "value": "учусь на IT", "importance": 4}',
        ],
        errors=[None, None],
    )

    handled = executor.handle_unrecognized_command("я учусь на IT")
    assert handled is True
    assert executor._ai_client.calls >= 2
    assert any("учится" in m.get("text", "").lower() for m in executor.memory.memories)


def test_ai_enabled_also_uses_local_memory_rules(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor._ai_client = DummyAIClient(
        responses=[
            '{"mode":"reply","message":"Ок."}',
            '{"save": false}',
        ],
        errors=[None, None],
    )
    called = {"local": 0}
    original = executor.memory.learn_from_user_text

    def wrap_local(text):
        called["local"] += 1
        return original(text)

    executor.memory.learn_from_user_text = wrap_local
    handled = executor.handle_unrecognized_command("я работаю преподавателем")
    assert handled is True
    assert called["local"] >= 1


def test_ai_disabled_uses_local_memory_rules(monkeypatch, tmp_path):
    monkeypatch.setattr(executor_module, "PluginManager", DummyPluginManager)
    monkeypatch.setattr(executor_module, "ensure_keys_file", lambda: ({"openai_api_key": ""}, False))
    executor = Executor(config={"ai": {"enabled": False}})
    executor._chat_history_path = tmp_path / "history.json"
    executor._session_summary_path = tmp_path / "session_summary.txt"
    executor._save_chat_history = lambda: None
    executor._save_session_summary = lambda: None
    executor._log = lambda _msg: None
    executor.memory.base_dir = tmp_path
    executor.memory.profile_path = tmp_path / "user_profile.json"
    executor.memory.memories_path = tmp_path / "memories.jsonl"
    executor.memory.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    executor.memory.memories = []

    handled = executor.handle_unrecognized_command("я учусь на IT")
    assert handled is False
    assert any("учится" in m.get("text", "").lower() for m in executor.memory.memories)


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


def test_pending_args_light_for_volume_down_delta(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    logs = []
    executor._log = lambda msg: logs.append(msg)
    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    assert executor.handle_unrecognized_command("сделай тише") is True
    assert any("На сколько сделать тише" in m for m in logs)
    assert executor.handle_unrecognized_command("12") is True
    assert called["intent"]["type"] == "volume_down"
    assert called["intent"]["slots"]["delta"] == 12


def test_pending_args_light_for_volume_up_delta(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    logs = []
    executor._log = lambda msg: logs.append(msg)
    called = {}

    def fake_run(intent):
        called["intent"] = intent

    executor.run = fake_run
    assert executor.handle_unrecognized_command("сделай громче") is True
    assert any("На сколько сделать громче" in m for m in logs)
    assert executor.handle_unrecognized_command("8") is True
    assert called["intent"]["type"] == "volume_up"
    assert called["intent"]["slots"]["delta"] == 8


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


def test_session_summary_roll_keeps_recent(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor.config["ai"]["context_summarize_after_pairs"] = 2
    executor.config["ai"]["context_recent_pairs"] = 1
    executor._ai_client = DummyAIClient(responses=[], errors=[])

    captured = {}

    def fake_chunk(chunk, prior):
        captured["len_chunk"] = len(chunk)
        captured["prior"] = prior
        return "- пункт один\n- пункт два"

    executor._summarize_dialog_chunk = fake_chunk
    executor._chat_history = [
        {"role": "user", "content": "a1"},
        {"role": "assistant", "content": "b1"},
        {"role": "user", "content": "a2"},
        {"role": "assistant", "content": "b2"},
        {"role": "user", "content": "a3"},
        {"role": "assistant", "content": "b3"},
    ]
    executor._maybe_roll_session_summary()
    assert len(executor._chat_history) == 2
    assert executor._chat_history[-2]["content"] == "a3"
    assert "пункт" in executor._session_summary
    assert captured["len_chunk"] == 4


def test_session_summary_roll_preserves_history_on_summary_failure(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor.config["ai"]["context_summarize_after_pairs"] = 2
    executor.config["ai"]["context_recent_pairs"] = 1
    executor._ai_client = DummyAIClient(responses=[], errors=[])
    executor._summarize_dialog_chunk = lambda _chunk, _prior: None
    original = [
        {"role": "user", "content": "a1"},
        {"role": "assistant", "content": "b1"},
        {"role": "user", "content": "a2"},
        {"role": "assistant", "content": "b2"},
        {"role": "user", "content": "a3"},
        {"role": "assistant", "content": "b3"},
    ]
    executor._chat_history = list(original)

    executor._maybe_roll_session_summary()

    assert executor._chat_history == original
    assert executor._session_summary == ""


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


def test_forget_last_phrase_via_executor(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    messages = []
    executor._log = lambda msg: messages.append(msg)
    executor.memory._upsert_memory("временный контекст", "temporary", 2, layer="session")
    executor.memory._upsert_memory("ещё одна", "fact", 2, layer="core")

    assert executor.handle_unrecognized_command("забудь это") is True
    assert executor.memory.memories[-1].get("text") == "временный контекст"
    assert any("Удалил последнюю" in m for m in messages)


def test_forget_about_substring_via_executor(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    messages = []
    executor._log = lambda msg: messages.append(msg)
    executor.memory._upsert_memory("prefers black tea", "preference", 3, layer="core")
    # Команда на кириллице + фрагмент темы латиницей (устойчиво к кодировке файла).
    forget_cmd = "\u0437\u0430\u0431\u0443\u0434\u044c \u043f\u0440\u043e tea"

    assert executor.handle_unrecognized_command(forget_cmd) is True
    assert executor.memory.memories == []
    assert any("Удалил запись" in m for m in messages)


def test_build_ai_request_history_clips_memory_and_history(monkeypatch, tmp_path):
    executor = make_executor(monkeypatch, tmp_path)
    executor.config["ai"]["context_max_chars_history"] = 18
    executor.config["ai"]["context_max_chars_memory"] = 35
    executor.config["ai"]["context_max_chars_session_summary"] = 12
    executor._session_summary = "длинное" * 20
    executor._chat_history = [
        {"role": "user", "content": "uuuuuu"},
        {"role": "assistant", "content": "aaaaaa"},
        {"role": "user", "content": "last-q"},
        {"role": "assistant", "content": "last-a"},
    ]
    mem = "fact-" * 30
    hist = executor._build_ai_request_history(mem)
    assert any(m.get("role") == "user" and m.get("content") == "last-q" for m in hist)
    mem_blocks = [m for m in hist if m["role"] == "system" and "Память о пользователе" in m["content"]]
    assert mem_blocks and len(mem_blocks[0]["content"]) <= 80
