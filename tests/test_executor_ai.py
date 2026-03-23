import types

import pytest

from jarvis import executor as executor_module
from jarvis.executor import Executor


class DummyPluginManager:
    def __init__(self, *args, **kwargs):
        pass


class DummyAIClient:
    def __init__(self, responses, errors=None):
        self.responses = list(responses)
        self.errors = list(errors or [None] * len(self.responses))
        self.calls = 0
        self.last_error = None

    def is_enabled(self):
        return True

    def get_response(self, query, history=None):
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
    assert executor._ai_client.calls == 1


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
    assert executor._ai_client.calls == 1
    assert executor._chat_history == []
