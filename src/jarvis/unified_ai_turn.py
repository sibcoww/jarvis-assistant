"""Один turn AI для нераспознанной фразы: ответ пользователю (reply) или действие (action)."""

from __future__ import annotations

import json
import re
from typing import Any

# Маркеры информационного запроса (до явной команды открытия/браузера)
_INFO_MARKERS_RU = (
    "расскажи",
    "расскажите",
    "объясни",
    "объясните",
    "что такое",
    "кто такой",
    "кто такая",
    "кто такие",
    "кратко про",
    "кратко о ",
    "опиши",
    "опишите",
    "в чём смысл",
    "в чем смысл",
    "почему",
    "как работает",
    "чем известен",
    "чем знаменит",
)

# Явные сигналы действия в браузере / системе (не считать чистым «вопросом»)
_ACTION_MARKERS_RU = (
    "открой",
    "откройте",
    "запусти",
    "запустите",
    "включи ",
    "выключи ",
    "найди в google",
    "найди в гугл",
    "найди на сайте",
    "покажи в браузере",
    "покажите в браузере",
    "открой страницу",
    "открой в браузере",
    "открой сайт",
    "открой на кинопоиске",
    "открой кинопоиск",
)


def looks_like_informational_without_explicit_action(text: str) -> bool:
    """True, если фраза похожа на вопрос/объяснение без явной команды открыть что-то."""
    t = (text or "").strip().lower()
    if not t:
        return False
    has_info = any(m in t for m in _INFO_MARKERS_RU)
    has_action = any(m in t for m in _ACTION_MARKERS_RU)
    return has_info and not has_action


def strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def parse_unified_model_output(raw: str) -> dict[str, Any] | None:
    """
    Разобрать JSON от модели. Поддерживаем:
    - {\"mode\":\"reply\",\"message\":\"...\"}
    - {\"mode\":\"action\",\"message\":\"...\",\"intent\":...,\"slots\":{...}}
    - legacy: {\"mode\":\"command\",...} и {\"mode\":\"chat\"}
    """
    if not raw or not str(raw).strip():
        return None
    try:
        payload = json.loads(strip_json_fence(str(raw)))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    mode = str(payload.get("mode") or "").strip().lower()
    if mode == "chat":
        mode = "reply"
    if mode == "command":
        mode = "action"

    if mode == "reply":
        msg = payload.get("message")
        if msg is None:
            msg = payload.get("say")
        if not isinstance(msg, str):
            return None
        msg = msg.strip()
        if not msg:
            return None
        return {"mode": "reply", "message": msg}

    if mode == "action":
        intent = str(payload.get("intent") or "").strip()
        slots = payload.get("slots")
        if not intent:
            return None
        if not isinstance(slots, dict):
            slots = {}
        message = payload.get("message")
        if message is not None and not isinstance(message, str):
            message = None
        if isinstance(message, str):
            message = message.strip() or None
        return {"mode": "action", "intent": intent, "slots": slots, "message": message}

    return None


def unified_turn_system_prompt(*, informational_lock: bool) -> str:
    lock = ""
    if informational_lock:
        lock = (
            "\n\nКРИТИЧНО для ЭТОЙ реплики: это информационный вопрос без явной команды "
            "«открой / покажи в браузере / найди на сайте». Верни ТОЛЬКО mode=reply с полным текстовым ответом. "
            "Запрещено mode=action с browser_navigate или browser_search.\n"
        )
    return (
        "Ты маршрутизатор голосового ассистента на ПК. На каждый запрос отвечай РОВНО одним JSON-объектом, "
        "без markdown, без текста до или после JSON.\n\n"
        "Два режима:\n"
        "1) mode=reply — обычный ответ пользователю. Обязательное поле message (строка) — полный ответ по-русски.\n"
        "2) mode=action — выполнить действие на компьютере. Поля: intent (строка), slots (объект), "
        "опционально message — короткий статус пользователю.\n\n"
        "Используй mode=reply, если пользователь просит информацию, объяснение, пересказ, мнение, факт: "
        "«расскажи», «объясни», «что такое», «кто такой», «кратко про», «опиши», «почему» и т.п. — "
        "даже если речь о фильме, сайте, сервисе. Не открывай браузер вместо ответа.\n\n"
        "Используй mode=action ТОЛЬКО если есть явная команда что-то открыть, запустить, показать в браузере, "
        "поиск в браузере по распоряжению пользователя: «открой», «запусти», «покажи в браузере», "
        "«найди в гугле», «найди на сайте», «открой страницу», «открой на кинопоиске» и т.п.\n\n"
        "Если сомневаешься — всегда mode=reply (лучше текст, чем лишнее действие).\n\n"
        "Для mode=reply поле message — ТОЛЬКО для голосового ответа: РОВНО одно короткое предложение, "
        "не длиннее ~100–120 символов, без списков, без markdown, без «во-первых», без цитат. "
        "Если тема большая — дай одну мысль и предложи задать уточняющий вопрос.\n\n"
        "Допустимые intent для mode=action:\n"
        "- set_volume — slots: {\"value\": 0..100}\n"
        "- volume_up / volume_down — slots: {\"delta\": 1..100}\n"
        "- browser_navigate — открыть URL: slots {\"url\":\"https://...\"} или slots "
        "{\"site\":\"...\",\"query\":\"...\"} если нужен поиск/главная\n"
        "- browser_search — общий поиск: slots {\"query\":\"...\"}\n\n"
        "Только http/https в url. Не используй file:, javascript:, data:.\n"
        "Пример reply: {\"mode\":\"reply\",\"message\":\"Кратко: …\"}\n"
        "Пример action: {\"mode\":\"action\",\"message\":\"Открываю YouTube.\","
        "\"intent\":\"browser_navigate\",\"slots\":{\"url\":\"https://www.youtube.com/\"}}\n"
        f"{lock}"
    )


REPLY_ONLY_AFTER_MISROUTING_PROMPT = (
    "Пользователь задал информационный вопрос. Ответь по-русски ОДНИМ коротким предложением "
    "(не длиннее ~120 символов), обычным текстом. Без JSON, без списков, без перечня команд, "
    "не открывай сайты — только суть ответа для озвучки вслух."
)
