import json
import subprocess
import logging
import os
import shutil
import time
import re
import threading
from urllib.parse import quote_plus, urlparse
from urllib.request import urlopen
from pathlib import Path
from ctypes import cast, POINTER
from datetime import datetime, timedelta
from typing import List, Dict
import webbrowser

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import psutil
except ImportError:
    psutil = None

from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL
from .plugin_api import PluginManager
from .key_store import ensure_keys_file
from .memory_store import MemoryStore
from .nlu import extract_number
from .unified_ai_turn import (
    REPLY_ONLY_AFTER_MISROUTING_PROMPT,
    looks_like_informational_without_explicit_action,
    parse_unified_model_output,
    unified_turn_system_prompt,
)

logger = logging.getLogger(__name__)

class Executor:
    def __init__(self, config=None, log_callback=None):
        self.config = config or self._load_default_config()
        self.log_callback = log_callback  # Callback для GUI
        self._ai_client = None
        self._chat_history: List[Dict[str, str]] = []
        self._chat_history_path = Path.home() / ".jarvis" / "chat_history.json"
        self._chat_history_limit_messages = 15
        self._chat_reset_timeout = 300  # сек
        self._last_ai_at: float | None = None
        self.memory = MemoryStore()
        self._reminders_lock = threading.Lock()
        self._timer_lock = threading.Lock()
        self._active_timer: dict | None = None
        self._memory_save_markers = (
            "запомни",
            "запомни что",
            "запомни обо мне",
            "сохрани",
            "сохрани это",
            "учти что",
            "имей в виду",
            "запиши себе что",
        )
        self._reset_phrases = {
            "очисти контекст",
            "забудь контекст",
            "забудь диалог",
            "сбрось контекст",
        }
        self._pending_clarification: dict | None = None
        self._pending_confirmation: dict | None = None
        self._action_history: List[Dict[str, object]] = []
        
        # Инициализируем систему плагинов
        self.plugin_manager = PluginManager()
        self._apply_context_config()
        self._init_ai_assistant()
        self._load_chat_history()

    def _record_action(self, intent_type: str, slots: dict | None) -> None:
        if intent_type in {"show_action_history", "repeat_last_command"}:
            return
        entry = {
            "type": str(intent_type or "").strip(),
            "slots": dict(slots or {}),
            "ts": datetime.now().isoformat(),
        }
        self._action_history.append(entry)
        if len(self._action_history) > 30:
            self._action_history = self._action_history[-30:]

    @staticmethod
    def _looks_like_reset_context_phrase(query: str) -> bool:
        """
        Fuzzy-детект команд очистки контекста для ASR-ошибок
        (например «очисти сессию» / «очисти со сью»).
        """
        q = (query or "").strip().lower()
        if not q.startswith(("очисти", "сбрось", "забудь")):
            return False
        flat = re.sub(r"[^a-zа-яё0-9]+", "", q)
        markers = (
            "контекст",
            "диалог",
            "истори",
            "чат",
            "сесси",
            "сесию",
            "сессию",
            "сосью",
        )
        return any(m in flat for m in markers)

    @staticmethod
    def _extract_quoted_or_tail(raw: str, prefix_pattern: str) -> str:
        m = re.search(prefix_pattern, raw, flags=re.IGNORECASE)
        if not m:
            return ""
        return (m.group(1) or "").strip(" \"'.,!?")

    def _detect_missing_args(self, query: str) -> tuple[str, str] | None:
        q = (query or "").strip().lower()
        if q in {"открой сайт", "открой в браузере", "открой страницу", "открой"}:
            return ("browser_target", "Какой сайт или страницу открыть?")
        if q in {"открой программу", "открой приложение", "запусти программу", "запусти приложение"}:
            return ("app_target", "Какую программу открыть?")
        if q in {"поставь громкость", "сделай громкость", "установи громкость", "громкость", "звук"}:
            return ("volume_value", "На какую громкость поставить? (0-100)")
        if q in {"найди в браузере", "найди", "поищи"}:
            return ("browser_query", "Что именно найти в браузере?")
        return None

    def _try_consume_pending_clarification(self, query: str) -> str | None:
        pending = self._pending_clarification
        if not pending:
            return None
        expires_at = float(pending.get("expires_at", 0))
        if time.time() > expires_at:
            self._pending_clarification = None
            return None

        q = (query or "").strip()
        qn = q.lower()
        if qn in {"отмена", "не надо", "забудь", "cancel"}:
            self._pending_clarification = None
            self._log("🤖 Ок, отменил уточнение.")
            return ""

        kind = pending.get("kind")
        if kind in {"volume_value", "volume_down_delta", "volume_up_delta"}:
            num = extract_number(q)
            if num is None:
                # Не сбрасываем pending: даём повторить значение.
                self._pending_clarification = pending
                self._log("🤖 Нужна цифра или число словами. Например: 20 или двадцать.")
                return ""
            self._pending_clarification = None
            if kind == "volume_value":
                num = max(0, min(100, num))
                self.run({"type": "set_volume", "slots": {"value": num}})
                self._log("✅ Готово.")
                return ""
            if kind == "volume_down_delta":
                num = max(1, min(100, num))
                self.run({"type": "volume_down", "slots": {"delta": num}})
                self._log("✅ Готово.")
                return ""
            if kind == "volume_up_delta":
                num = max(1, min(100, num))
                self.run({"type": "volume_up", "slots": {"delta": num}})
                self._log("✅ Готово.")
                return ""
        self._pending_clarification = None
        if kind == "browser_target":
            return f"открой {q} в браузере"
        if kind == "app_target":
            return f"открой {q}"
        if kind == "browser_query":
            return f"найди в браузере {q}"
        return q

    def pending_confirmation_from_text(self, source_text: str) -> tuple[bool, dict | None, str | None]:
        """Обработать ответ пользователя на подтверждение рискованного действия."""
        pending = self._pending_confirmation
        if not pending:
            return (False, None, None)
        expires_at = float(pending.get("expires_at", 0))
        if time.time() > expires_at:
            self._pending_confirmation = None
            return (False, None, None)
        t = (source_text or "").strip().lower()
        if t in {"подтверждаю", "подтверди", "да", "выполняй"}:
            intent = pending.get("intent")
            self._pending_confirmation = None
            return (True, intent if isinstance(intent, dict) else None, "confirm")
        if t in {"отмена", "нет", "не надо", "cancel"}:
            self._pending_confirmation = None
            return (True, None, "cancel")
        return (False, None, None)

    def should_require_confirmation(self, intent: dict) -> bool:
        t = str(intent.get("type") or "")
        if t in {"shutdown_pc", "restart_pc", "sleep_pc"}:
            return True
        if t not in {"delete_file", "move_file"}:
            return False
        slots = intent.get("slots") or {}
        path = str(slots.get("path") or slots.get("source") or "").strip().lower()
        # Для диплома: подтверждаем любые потенциально разрушающие файловые действия.
        return bool(path)

    def queue_confirmation(self, intent: dict, ttl_seconds: int = 20) -> str:
        self._pending_confirmation = {
            "intent": intent,
            "expires_at": time.time() + max(5, int(ttl_seconds)),
        }
        t = str(intent.get("type") or "")
        if t == "delete_file":
            slots = intent.get("slots") or {}
            p = str(slots.get("path") or "").strip()
            return f"Подтверди удаление файла: {p}. Скажи «подтверждаю» или «отмена»."
        if t == "move_file":
            slots = intent.get("slots") or {}
            s = str(slots.get("source") or "").strip()
            d = str(slots.get("destination") or "").strip()
            return f"Подтверди перемещение файла: {s} -> {d}. Скажи «подтверждаю» или «отмена»."
        if t == "shutdown_pc":
            return "Подтверди выключение компьютера. Скажи «подтверждаю» или «отмена»."
        if t == "restart_pc":
            return "Подтверди перезагрузку компьютера. Скажи «подтверждаю» или «отмена»."
        if t == "sleep_pc":
            return "Подтверди перевод компьютера в сон. Скажи «подтверждаю» или «отмена»."
        return "Подтверди действие: «подтверждаю» или «отмена»."

    def _dialog_context_recap_for_command_ai(self, max_messages: int = 6) -> str:
        """Короткий субтитр диалога, чтобы понимать «это», «оно», «то же самое» при интерпретации команды."""
        if not self._chat_history:
            return ""
        tail = self._chat_history[-max_messages:]
        lines: List[str] = []
        for m in tail:
            role = m.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = (m.get("content") or "").strip()
            if not content:
                continue
            label = "Пользователь" if role == "user" else "Ассистент"
            lines.append(f"{label}: {content[:450]}")
        if not lines:
            return ""
        return "Недавний диалог (учитывай местоимения «это», «оно», «его»):\n" + "\n".join(lines)

    def _interpret_command_with_ai(self, query: str) -> dict | None:
        if not self._ai_client or not self._ai_client.is_enabled():
            return None
        text = (query or "").strip()
        if not text:
            return None

        recap = self._dialog_context_recap_for_command_ai()
        if recap:
            text = f"{recap}\n\n---\nТекущая фраза пользователя: {text}"

        system_prompt = (
            "Ты интерпретатор команд ассистента. Ты НЕ ведёшь беседу и НЕ отвечаешь текстом пользователю — "
            "только один JSON-объект.\n"
            "Доступные команды:\n"
            "- set_volume(value 0..100)\n"
            "- volume_up(delta 1..100)\n"
            "- volume_down(delta 1..100)\n"
            "- browser_navigate — открыть сайт или страницу в браузере пользователя\n"
            "- browser_search(query) — общий поиск в браузере (Google), если нельзя дать точный URL\n"
            "Любая явная просьба открыть сайт, страницу, поиск, Кинопоиск, YouTube и т.п. — это mode=command, "
            "НЕ chat. Не отказывайся от «открытия» на уровне интерпретатора.\n"
            "Для browser_navigate ты сам выбираешь ресурс и формат:\n"
            "- По возможности верни готовый безопасный URL в slots.url (только http или https, полная ссылка).\n"
            "- Главная страница сайта: slots.url вида https://www.youtube.com/ или https://github.com/\n"
            "- Если запрос про статью/документацию/гайд — по возможности верни ПРЯМУЮ ссылку на материал, "
            "а не общий поиск.\n"
            "- Поиск внутри сайта (канал на YouTube, репозиторий на GitHub, карточка на Кинопоиске и т.д.): slots.url "
            "с корректным адресом поиска/выдачи на ЭТОМ сайте.\n"
            "- Кинопоиск: для поиска фильма/аниме используй slots.url вида "
            "https://www.kinopoisk.ru/index.php?kp_query=<название в URL-кодировке>, "
            "например запрос «Берсерк»: "
            "https://www.kinopoisk.ru/index.php?kp_query=%D0%91%D0%B5%D1%80%D1%81%D0%B5%D1%80%D0%BA\n"
            "- Если в текущей фразе «это/оно» — восстанови предмет по Недавнему диалогу (название, тема) и подставь в URL или query.\n"
            "- Если точный URL сформировать нельзя, можно вернуть slots.site и slots.query (коротко: что за площадка и что искать); "
            "исполнитель откроет безопасный поиск, не подставляя списки сайтов из кода.\n"
            "- Не придумывай опасные схемы (file:, javascript:, data: и т.п.).\n"
            "Пример: 'открой канал MrBeast на YouTube' -> "
            "{\"mode\":\"command\",\"intent\":\"browser_navigate\","
            "\"slots\":{\"url\":\"https://www.youtube.com/results?search_query=MrBeast\"}}\n"
            "Если фраза — команда (включая открытие сайтов), верни только JSON:\n"
            "{\"mode\":\"command\",\"intent\":\"...\",\"slots\":{...}}\n"
            "Только если это действительно нейтральный вопрос без действий (чистая беседа), верни:\n"
            "{\"mode\":\"chat\"}\n"
            "Никакого текста вне JSON."
        )
        raw = self._ai_client.get_response(
            text,
            history=None,
            system_prompt=system_prompt,
            max_tokens=220,
            temperature=0.0,
        )
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return self._validate_ai_command_payload(payload)

    def _try_unified_ai_turn(self, query: str, now_ts: float) -> bool:
        """Один AI-вызов: reply (текст) или action (валидированная команда)."""
        if not self._ai_client or not self._ai_client.is_enabled():
            return False
        text = (query or "").strip()
        if not text:
            return False

        info_lock = looks_like_informational_without_explicit_action(text)
        self._log(
            f"[DEBUG] Unified AI turn, model={getattr(self._ai_client, 'model', '?')}, "
            f"history_pairs~{len(self._chat_history) // 2}, informational_lock={info_lock}"
        )

        request_history = self._build_ai_request_history()

        recap = self._dialog_context_recap_for_command_ai()
        user_payload = text
        if recap:
            user_payload = f"{recap}\n\n---\nТекущая фраза пользователя: {text}"

        system_prompt = unified_turn_system_prompt(informational_lock=info_lock)
        ai_cfg = self._ai_settings()
        max_reply_tokens = int(ai_cfg.get("reply_max_tokens", 120))
        last_error_text = None
        response = None
        for attempt in range(2):
            response = self._ai_client.get_response(
                user_payload,
                history=request_history,
                system_prompt=system_prompt,
                max_tokens=max_reply_tokens,
                temperature=0.15,
            )
            last_error_text = getattr(self._ai_client, "last_error", None)

            if response:
                break

            empty_err = last_error_text and "пуст" in last_error_text.lower()
            rate_limited = last_error_text and "429" in last_error_text

            if attempt == 0 and empty_err:
                self._log("[DEBUG] Unified AI: пустой ответ, повтор")
                continue

            if rate_limited or (
                attempt == 0 and last_error_text and "ограничил" in last_error_text.lower()
            ):
                break

        if not response:
            return False

        raw_preview = (response or "").strip().replace("\r\n", "\n")
        if len(raw_preview) > 2000:
            raw_preview = raw_preview[:2000].rstrip() + "…"
        self._log(f"[DEBUG] Unified AI сырой ответ модели:\n{raw_preview}")

        parsed = parse_unified_model_output(response)
        if not parsed:
            parsed = {"mode": "reply", "message": response.strip()}

        if parsed.get("mode") == "action":
            intent_name = str(parsed.get("intent") or "")
            if info_lock and intent_name in ("browser_navigate", "browser_search"):
                self._log("[DEBUG] Unified AI: браузерное действие отклонено (информационный запрос)")
                response2 = self._ai_client.get_response(
                    text,
                    history=request_history,
                    system_prompt=REPLY_ONLY_AFTER_MISROUTING_PROMPT,
                    max_tokens=600,
                    temperature=0.25,
                )
                if response2 and response2.strip():
                    r2 = response2.strip().replace("\r\n", "\n")
                    if len(r2) > 2000:
                        r2 = r2[:2000].rstrip() + "…"
                    self._log(f"[DEBUG] Unified AI ответ после перепроверки (сырой):\n{r2}")
                    parsed = {"mode": "reply", "message": response2.strip()}
                else:
                    parsed = {
                        "mode": "reply",
                        "message": "Могу рассказать текстом или открыть сайт — скажи, что нужно.",
                    }

        try:
            dumped = json.dumps(parsed, ensure_ascii=False)
            if len(dumped) > 1500:
                dumped = dumped[:1500].rstrip() + "…"
            self._log(f"[DEBUG] Unified AI разобранный JSON (объект):\n{dumped}")
        except (TypeError, ValueError):
            self._log(f"[DEBUG] Unified AI разобранный объект (repr): {parsed!r}")

        if parsed.get("mode") == "action":
            legacy = {
                "mode": "command",
                "intent": parsed.get("intent"),
                "slots": parsed.get("slots") if isinstance(parsed.get("slots"), dict) else {},
            }
            ai_intent = self._validate_ai_command_payload(legacy)
            if not ai_intent:
                self._log("[DEBUG] Unified AI: структура действия не прошла валидацию — ответ текстом")
                fallback = parsed.get("message")
                if isinstance(fallback, str) and fallback.strip():
                    parsed = {"mode": "reply", "message": fallback.strip()}
                else:
                    response3 = self._ai_client.get_response(
                        text,
                        history=request_history,
                        system_prompt=REPLY_ONLY_AFTER_MISROUTING_PROMPT,
                        max_tokens=500,
                        temperature=0.25,
                    )
                    if response3 and response3.strip():
                        r3 = response3.strip().replace("\r\n", "\n")
                        if len(r3) > 2000:
                            r3 = r3[:2000].rstrip() + "…"
                        self._log(f"[DEBUG] Unified AI ответ после невалидного action (сырой):\n{r3}")
                        parsed = {"mode": "reply", "message": response3.strip()}
                    else:
                        return False
            else:
                self._log(f"[DEBUG] Unified AI command: {ai_intent['type']}")
                try:
                    self.run(ai_intent)
                    self._log("✅ Готово.")
                    self._append_chat_message("user", text)
                    ack = parsed.get("message") if isinstance(parsed.get("message"), str) else None
                    if not (ack and ack.strip()):
                        ack = self._brief_ack_for_command(ai_intent, text)
                    self._append_chat_message("assistant", ack.strip())
                    self._save_chat_history()
                    self._last_ai_at = now_ts
                    return True
                except Exception as error:
                    self._log(f"❌ Ошибка выполнения AI-команды: {error}")
                    logger.exception("Unified AI command failed")
                    return False

        if parsed.get("mode") == "reply":
            message = parsed.get("message")
            if not isinstance(message, str) or not message.strip():
                return False
            max_sentences = int(ai_cfg.get("reply_max_sentences", 1))
            max_chars = int(ai_cfg.get("reply_max_chars", 160))
            message = self._shorten_for_voice(message, max_sentences=max_sentences, max_chars=max_chars)
            self._append_chat_message("user", text)
            self._append_chat_message("assistant", message)
            self._save_chat_history()
            self._last_ai_at = now_ts
            self._log(f"🤖 AI: {message}")
            return True

        return False

    def _brief_ack_for_command(self, intent: dict, user_query: str) -> str:
        """Короткая реплика для истории чата (чтобы следующие фразы вроде «это» ссылались на контекст)."""
        t = intent.get("type")
        slots = intent.get("slots") or {}
        q = (user_query or "").strip()
        q_short = q[:200] if q else ""
        if t == "browser_navigate":
            url = (slots.get("url") or "").strip()
            if "google.com/search" in url or "google.ru/search" in url:
                return (
                    "Открыл поиск в Google. "
                    f"Запрос пользователя: {q_short}" if q_short else "Открыл поиск в Google."
                )
            if "kinopoisk" in url.lower():
                return "Открыл поиск на Кинопоиске по твоему запросу."
            return "Открыл страницу в браузере."
        if t == "browser_search":
            inner = (slots.get("query") or "").strip()
            return (
                f"Открыл поиск в браузере: {inner[:150]}" if inner else "Открыл поиск в браузере."
            )
        if t == "set_volume":
            return f"Громкость установлена на {slots.get('value')}%."
        if t == "volume_up":
            return f"Сделал громче на {slots.get('delta', 10)}."
        if t == "volume_down":
            return f"Сделал тише на {slots.get('delta', 10)}."
        if t == "add_todo":
            return "Добавил задачу."
        if t == "list_todos":
            return "Показал список задач."
        if t == "complete_todo":
            return "Отметил задачу выполненной."
        if t == "delete_todo":
            return "Удалил задачу."
        if t == "create_reminder":
            return "Создал напоминание."
        if t == "start_timer":
            return "Запустил таймер."
        if t == "timer_status":
            return "Показал статус таймера."
        if t == "cancel_timer":
            return "Отменил таймер."
        if t == "show_weather":
            return "Показал погоду."
        if t == "shutdown_pc":
            return "Выключаю компьютер."
        if t == "restart_pc":
            return "Перезагружаю компьютер."
        if t == "sleep_pc":
            return "Перевожу компьютер в режим сна."
        if t == "lock_pc":
            return "Блокирую экран."
        return "Выполнил команду."

    def _resolve_site_home_url_with_ai(self, site_name: str) -> str | None:
        """Попросить AI вернуть главную http(s)-ссылку сайта по названию."""
        if not self._ai_client or not self._ai_client.is_enabled():
            return None
        name = (site_name or "").strip()
        if not name:
            return None
        raw = self._ai_client.get_response(
            f"Название сайта: {name}",
            history=None,
            system_prompt=(
                "Ты URL-resolver. Верни строго JSON: "
                "{\"url\":\"https://...\"} "
                "Только главная страница сайта. Только http/https."
            ),
            max_tokens=70,
            temperature=0.0,
        )
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        url = str(payload.get("url") or "").strip()
        if not self._is_safe_http_url(url):
            return None
        return url

    def _resolve_site_query_url_with_ai(self, site_name: str, query: str) -> str | None:
        """Попросить AI вернуть прямую URL-ссылку страницы/канала на указанном сайте."""
        if not self._ai_client or not self._ai_client.is_enabled():
            return None
        site = (site_name or "").strip()
        q = (query or "").strip()
        if not site or not q:
            return None
        raw = self._ai_client.get_response(
            f"Сайт: {site}\nЗапрос пользователя: {q}",
            history=None,
            system_prompt=(
                "Ты URL-resolver. Нужно найти наиболее вероятную ПРЯМУЮ ссылку страницы на указанном сайте "
                "(статья, канал, профиль, карточка). Верни строго JSON: "
                "{\"url\":\"https://...\"}. Только http/https. Если уверен только в странице поиска на сайте, "
                "верни URL поиска на этом же сайте. Никакого текста вне JSON."
            ),
            max_tokens=100,
            temperature=0.0,
        )
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        url = str(payload.get("url") or "").strip()
        if not self._is_safe_http_url(url):
            return None
        return url

    def _resolve_article_url_with_ai(self, query: str, site_name: str = "") -> str | None:
        """Попробовать найти прямую ссылку на статью/документацию по теме."""
        if not self._ai_client or not self._ai_client.is_enabled():
            return None
        q = (query or "").strip()
        if not q:
            return None
        site = (site_name or "").strip()
        payload_text = f"Тема: {q}"
        if site:
            payload_text += f"\nПредпочтительный сайт: {site}"
        raw = self._ai_client.get_response(
            payload_text,
            history=None,
            system_prompt=(
                "Ты URL-resolver. Верни строго JSON: {\"url\":\"https://...\"}. "
                "Нужно дать прямую ссылку на качественную статью/документацию по теме. "
                "Если уверенного URL нет, верни {\"url\":\"\"}. Только http/https."
            ),
            max_tokens=120,
            temperature=0.0,
        )
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        url = str(payload.get("url") or "").strip()
        if not url:
            return None
        if not self._is_safe_http_url(url):
            return None
        return url

    @staticmethod
    def _google_search_url(query: str) -> str:
        """Универсальный поиск без привязки к конкретным доменам в коде."""
        return f"https://www.google.com/search?q={quote_plus(query.strip())}"

    @staticmethod
    def _youtube_search_url(query: str) -> str:
        return f"https://www.youtube.com/results?search_query={quote_plus(query.strip())}"

    @staticmethod
    def _is_safe_http_url(url: str) -> bool:
        u = (url or "").strip()
        if not u:
            return False
        parsed = urlparse(u)
        if parsed.scheme not in ("http", "https"):
            return False
        if not parsed.netloc:
            return False
        lowered = u.lower()
        if lowered.startswith("javascript:") or lowered.startswith("data:") or lowered.startswith("file:"):
            return False
        return True

    @classmethod
    def _normalize_browser_url_candidate(cls, raw: str) -> str | None:
        """Добавить https:// к веб-адресу без схемы; только http(s), без file:/javascript:."""
        s = (raw or "").strip()
        if not s:
            return None
        if s.startswith(("http://", "https://")):
            return s if cls._is_safe_http_url(s) else None
        if " " in s or "\n" in s:
            return None
        candidate = "https://" + s.lstrip("/")
        return candidate if cls._is_safe_http_url(candidate) else None

    @staticmethod
    def _normalized_label(value: str) -> str:
        return re.sub(r"[^a-z0-9а-яё]+", "", (value or "").strip().lower())

    @staticmethod
    def _is_article_first_query(query: str) -> bool:
        q = (query or "").lower()
        markers = (
            "стать",
            "документац",
            "документ",
            "гайд",
            "инструкция",
            "обзор",
            "tutorial",
            "manual",
            "доки",
            "материал",
        )
        return any(m in q for m in markers)

    @staticmethod
    def _is_video_or_blog_query(query: str) -> bool:
        q = (query or "").lower()
        markers = (
            "видео",
            "ролик",
            "трейлер",
            "ютуб",
            "youtube",
            "блог",
            "влог",
            "подкаст",
        )
        return any(m in q for m in markers)

    @staticmethod
    def _has_contextual_topic_placeholder(query: str) -> bool:
        q = (query or "").lower()
        markers = (
            "эту тему",
            "эту статью",
            "этого",
            "этой",
            "это",
            "на эту тему",
            "по этой теме",
        )
        return any(m in q for m in markers)

    def _extract_recent_topic_for_web(self) -> str:
        """Вытянуть последнюю содержательную тему из диалога для 'это/эта тема'."""
        if not self._chat_history:
            return ""
        ignore = (
            "открой",
            "найди",
            "запусти",
            "сохрани",
            "запомни",
            "что ты знаешь",
            "что ты помнишь",
            "покажи историю",
        )
        for item in reversed(self._chat_history):
            if item.get("role") != "user":
                continue
            text = str(item.get("content") or "").strip()
            low = text.lower()
            if not text:
                continue
            if any(x in low for x in ignore):
                continue
            if len(text) < 6:
                continue
            return text
        return ""

    def _expand_article_query_from_context(self, query: str) -> str:
        q = (query or "").strip()
        if not q:
            return ""
        if not self._has_contextual_topic_placeholder(q):
            return q
        topic = self._extract_recent_topic_for_web()
        if not topic:
            return q
        qn = q
        replacements = (
            "на эту тему",
            "по этой теме",
            "эту тему",
            "эту статью",
            "этой библиотеки",
            "этого",
            "этой",
            "это",
        )
        for frag in replacements:
            qn = re.sub(frag, "", qn, flags=re.IGNORECASE).strip()
        qn = re.sub(r"\s+", " ", qn).strip(" ,.:;!-")
        if not qn:
            return topic
        # Добавляем тему в хвост, чтобы AI-resolver видел конкретику.
        return f"{qn} {topic}".strip()

    def _validate_ai_command_payload(self, payload: dict | None) -> dict | None:
        if not isinstance(payload, dict):
            return None
        if str(payload.get("mode", "")).lower() != "command":
            return None

        intent = str(payload.get("intent", "")).strip()
        slots = payload.get("slots", {})
        if not isinstance(slots, dict):
            return None

        allowed_intents = {"set_volume", "volume_up", "volume_down", "browser_navigate", "browser_search"}
        if intent not in allowed_intents:
            return None

        if intent == "set_volume":
            try:
                value = int(slots.get("value"))
            except Exception:
                return None
            if not (0 <= value <= 100):
                return None
            return {"type": "set_volume", "slots": {"value": value}}

        if intent in {"volume_up", "volume_down"}:
            try:
                delta = int(slots.get("delta"))
            except Exception:
                return None
            if not (1 <= delta <= 100):
                return None
            return {"type": intent, "slots": {"delta": delta}}

        if intent == "browser_navigate":
            url_raw = str(slots.get("url") or "").strip()
            site = str(slots.get("site") or "").strip()
            query = str(slots.get("query") or "").strip()

            if url_raw:
                url = self._normalize_browser_url_candidate(url_raw) or (
                    url_raw if self._is_safe_http_url(url_raw) else None
                )
                if not url:
                    return None
                return {"type": "browser_navigate", "slots": {"url": url}}

            if site and query:
                if self._is_article_first_query(query):
                    article_url = self._resolve_article_url_with_ai(query, site)
                    if article_url:
                        return {"type": "browser_navigate", "slots": {"url": article_url}}
                # "открой сайт X" часто приходит как site+query, где query ~= site.
                # В этом случае сначала пытаемся открыть главную страницу сайта.
                if self._normalized_label(site) and self._normalized_label(site) == self._normalized_label(query):
                    resolved = self._resolve_site_home_url_with_ai(site)
                    if resolved:
                        return {"type": "browser_navigate", "slots": {"url": resolved}}
                # URL-first: сначала пробуем получить прямую страницу/канал на нужном сайте.
                resolved_query_url = self._resolve_site_query_url_with_ai(site, query)
                if resolved_query_url:
                    return {"type": "browser_navigate", "slots": {"url": resolved_query_url}}
                combined = f"{site} {query}".strip()
                url = self._google_search_url(combined)
                return {"type": "browser_navigate", "slots": {"url": url}}

            if site and not query:
                url = self._normalize_browser_url_candidate(site)
                if url:
                    return {"type": "browser_navigate", "slots": {"url": url}}
                return None

            return None

        if intent == "browser_search":
            query = str(slots.get("query") or "").strip()
            if not query or len(query) > 200:
                return None
            if self._is_article_first_query(query):
                expanded = self._expand_article_query_from_context(query)
                article_url = self._resolve_article_url_with_ai(expanded)
                if article_url:
                    return {"type": "browser_navigate", "slots": {"url": article_url}}
                return {"type": "browser_search", "slots": {"query": expanded}}
            if self._is_video_or_blog_query(query):
                return {"type": "browser_navigate", "slots": {"url": self._youtube_search_url(query)}}
            return {"type": "browser_search", "slots": {"query": query}}

        return None

    def _validate_ai_local_action_payload(self, payload: dict | None) -> dict | None:
        """Строгая валидация AI fallback только для локальных доменов."""
        if not isinstance(payload, dict):
            return None
        mode = str(payload.get("mode", "")).lower()
        if mode not in {"command", "action"}:
            return None
        intent = str(payload.get("intent", "")).strip()
        slots = payload.get("slots", {})
        if not isinstance(slots, dict):
            return None

        allowed = {
            "add_todo",
            "list_todos",
            "complete_todo",
            "delete_todo",
            "create_reminder",
            "start_timer",
            "timer_status",
            "cancel_timer",
            "shutdown_pc",
            "restart_pc",
            "sleep_pc",
            "lock_pc",
            "show_weather",
        }
        if intent not in allowed:
            return None

        if intent == "add_todo":
            text = self._normalize_spaces(str(slots.get("text") or ""))
            if not text or len(text) > 200:
                return None
            return {"type": intent, "slots": {"text": text}}

        if intent in {
            "list_todos",
            "timer_status",
            "cancel_timer",
            "shutdown_pc",
            "restart_pc",
            "sleep_pc",
            "lock_pc",
        }:
            return {"type": intent, "slots": {}}

        if intent == "show_weather":
            city = self._normalize_spaces(str(slots.get("city") or ""))
            if len(city) > 80:
                city = city[:80].rstrip()
            return {"type": intent, "slots": {"city": city}}

        if intent in {"complete_todo", "delete_todo"}:
            ref = self._normalize_spaces(str(slots.get("ref") or ""))
            if not ref or len(ref) > 120:
                return None
            return {"type": intent, "slots": {"ref": ref}}

        if intent == "create_reminder":
            reminder = self._normalize_spaces(str(slots.get("reminder") or ""))
            if not reminder or len(reminder) > 220:
                return None
            return {"type": intent, "slots": {"reminder": reminder}}

        if intent == "start_timer":
            try:
                amount = int(slots.get("amount"))
            except Exception:
                return None
            if not (1 <= amount <= 1440):
                return None
            unit = self._normalize_spaces(str(slots.get("unit") or "")).lower()
            if not unit:
                return None
            if unit.startswith("сек"):
                unit = "секунд"
            elif unit.startswith("час"):
                unit = "часов"
            else:
                unit = "минут"
            label = self._normalize_spaces(str(slots.get("label") or ""))
            if len(label) > 180:
                label = label[:180].rstrip()
            return {
                "type": intent,
                "slots": {"amount": amount, "unit": unit, "label": label},
            }
        return None

    @staticmethod
    def _looks_like_local_domain_query(query: str) -> bool:
        low = (query or "").lower()
        markers = (
            "задач",
            "todo",
            "дел",
            "список дел",
            "в дела",
            "отмет",
            "таймер",
            "напом",
            "напомин",
            "через",
            "сколько осталось",
            "выключи компьютер",
            "перезагрузи",
            "режим сна",
            "заблокируй экран",
            "блокир",
            "погод",
        )
        return any(m in low for m in markers)

    def _try_local_domain_ai_fallback(self, query: str, now_ts: float) -> bool:
        """AI fallback для естественных фраз локальных команд (todo/timer/reminders)."""
        if not self._ai_client or not self._ai_client.is_enabled():
            return False
        text = self._normalize_spaces(query)
        if not text:
            return False
        if not self._looks_like_local_domain_query(text):
            return False
        system_prompt = (
            "Ты интерпретатор локальных команд ассистента. Верни строго один JSON и ничего больше.\n"
            "Формат: {\"mode\":\"command\",\"intent\":\"...\",\"slots\":{...}}\n"
            "Разрешённые intent:\n"
            "- add_todo: slots {\"text\":\"...\"}\n"
            "- list_todos: slots {}\n"
            "- complete_todo: slots {\"ref\":\"...\"}\n"
            "- delete_todo: slots {\"ref\":\"...\"}\n"
            "- create_reminder: slots {\"reminder\":\"...\"}\n"
            "- start_timer: slots {\"amount\":<int>,\"unit\":\"секунд|минут|часов\",\"label\":\"...\"}\n"
            "- timer_status: slots {}\n"
            "- cancel_timer: slots {}\n"
            "- shutdown_pc: slots {}\n"
            "- restart_pc: slots {}\n"
            "- sleep_pc: slots {}\n"
            "- lock_pc: slots {}\n"
            "- show_weather: slots {\"city\":\"...\"} (city может быть пустым)\n"
            "Если фраза не про эти команды, верни {\"mode\":\"none\"}.\n"
            "Никакого текста вне JSON."
        )
        raw = self._ai_client.get_response(
            text,
            history=None,
            system_prompt=system_prompt,
            max_tokens=160,
            temperature=0.0,
        )
        if not raw:
            return False
        try:
            payload = json.loads(raw)
        except Exception:
            return False
        if str(payload.get("mode", "")).lower() == "none":
            return False
        intent = self._validate_ai_local_action_payload(payload)
        if not intent:
            return False
        try:
            self.run(intent)
            self._log("✅ Готово.")
            self._append_chat_message("user", text)
            self._append_chat_message("assistant", self._brief_ack_for_command(intent, text))
            self._save_chat_history()
            self._last_ai_at = now_ts
            self._log(f"[DEBUG] Local AI fallback command: {intent['type']}")
            return True
        except Exception as error:
            self._log(f"❌ Ошибка выполнения локальной AI-команды: {error}")
            logger.exception("Local AI fallback command failed")
            return False
    
    def _log(self, message: str):
        """Универсальное логирование - в logger и GUI callback"""
        logger.info(message)
        if self.log_callback:
            self.log_callback(message)

    def _ai_settings(self) -> dict:
        raw = self.config.get("ai", {})
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _shorten_for_voice(text: str, max_sentences: int = 1, max_chars: int = 160) -> str:
        s = re.sub(r"\s+", " ", (text or "")).strip()
        if not s:
            return ""
        parts = re.split(r"(?<=[.!?])\s+", s)
        picked = [p.strip() for p in parts if p.strip()][: max(1, int(max_sentences))]
        out = " ".join(picked).strip()
        if len(out) > max_chars:
            out = out[: max_chars - 1].rstrip(" ,;:-") + "…"
        return out

    def _apply_context_config(self):
        # Для памяти в дипломной версии держим только короткий контекст.
        self._chat_history_limit_messages = 15

    @staticmethod
    def _clip_text_for_context(text: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        s = (text or "").strip()
        if not s:
            return ""
        if len(s) <= max_chars:
            return s
        return s[: max_chars - 1] + "…"

    def _build_ai_request_history(self) -> List[Dict[str, str]]:
        """Короткая история + несколько последних пользовательских фактов."""
        prefix: List[Dict[str, str]] = []
        mem = self._clip_text_for_context(self.memory.build_context(top_k=6), 1000)
        if mem:
            prefix.append(
                {
                    "role": "system",
                    "content": "Факты о пользователе (учитывай аккуратно, без выдумки):\n" + mem,
                }
            )
        return prefix + list(self._chat_history)

    def _load_chat_history(self):
        try:
            if not self._chat_history_path.exists():
                self._chat_history = []
                return

            data = json.loads(self._chat_history_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                self._chat_history = []
                return

            normalized: List[Dict[str, str]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                if role not in {"user", "assistant", "system"}:
                    continue
                if not isinstance(content, str):
                    continue
                content = content.strip()
                if not content:
                    continue
                normalized.append({"role": role, "content": content})

            self._chat_history = normalized
            self._trim_chat_history()
            self._log(f"[DEBUG] Загружена история чата: {len(self._chat_history)} сообщений")
        except Exception as error:
            logger.warning(f"Не удалось загрузить чат-историю: {error}")
            self._chat_history = []

    def _save_chat_history(self):
        try:
            self._chat_history_path.parent.mkdir(parents=True, exist_ok=True)
            self._chat_history_path.write_text(
                json.dumps(self._chat_history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as error:
            logger.warning(f"Не удалось сохранить чат-историю: {error}")

    def _trim_chat_history(self):
        max_messages = self._chat_history_limit_messages
        if len(self._chat_history) <= max_messages:
            return
        removed = len(self._chat_history) - max_messages
        self._chat_history = self._chat_history[-max_messages:]
        self._log(f"[DEBUG] Обрезка чат-истории: -{removed} сообщений, осталось {len(self._chat_history)}")

    def _append_chat_message(self, role: str, content: str):
        if role not in {"user", "assistant", "system"}:
            return
        content = (content or "").strip()
        if not content:
            return
        self._chat_history.append({"role": role, "content": content})
        self._trim_chat_history()

    def reset_chat_history(self, reason: str = "manual"):
        if self._chat_history:
            self._log(f"[DEBUG] Сброс чат-истории: {reason}")
        self._chat_history = []
        self._last_ai_at = None
        self._save_chat_history()

    def _extract_memory_payload(self, text: str) -> str:
        s = (text or "").strip()
        low = s.lower()
        markers = sorted(self._memory_save_markers, key=len, reverse=True)
        for marker in markers:
            if low.startswith(marker):
                return s[len(marker):].strip(" ,.:;-")
        return s

    def _normalize_memory_fact_with_ai(self, source_text: str) -> str:
        raw = self._extract_memory_payload(source_text)
        if not raw:
            return ""
        if not (self._ai_client and self._ai_client.is_enabled()):
            return self.memory.normalize_fact(raw)
        prompt = (
            "Выдели из фразы пользователя один устойчивый факт о нём и верни только одну короткую строку "
            "без JSON, без кавычек, без слов «запомни/сохрани». Если факта нет, верни пусто.\n\n"
            f"Фраза: {raw}"
        )
        response = self._ai_client.get_response(
            prompt,
            history=[],
            system_prompt="Ты нормализуешь факты о пользователе для локальной памяти.",
            max_tokens=80,
            temperature=0.0,
        )
        if not response or not str(response).strip():
            return self.memory.normalize_fact(raw)
        first_line = str(response).strip().splitlines()[0]
        return self.memory.normalize_fact(first_line)

    def _save_memory_fact(self, source_text: str) -> str:
        fact = self._normalize_memory_fact_with_ai(source_text)
        if not fact:
            return ""
        return self.memory.add_fact(fact)

    def _is_memory_save_command(self, normalized_query: str) -> bool:
        q = (normalized_query or "").strip()
        return any(q.startswith(marker) for marker in self._memory_save_markers)

    @staticmethod
    def _is_memory_show_command(normalized_query: str) -> bool:
        q = (normalized_query or "").strip()
        if q in {"что ты помнишь", "что ты помнишь обо мне", "что ты знаешь обо мне", "напомни, что ты обо мне знаешь"}:
            return True
        return ("что ты помнишь" in q and "обо мне" in q)

    @staticmethod
    def _is_memory_forget_command(normalized_query: str) -> bool:
        q = (normalized_query or "").strip()
        return (
            q in {"забудь всё обо мне", "забудь обо мне", "забудь всё", "забудь все"}
            or q.startswith("забудь ")
            or q.startswith("удали это из памяти")
            or q.startswith("убери это из памяти")
        )

    def _reply_memory_summary(self, query: str, now_ts: float) -> bool:
        facts = [row.get("text", "") for row in self.memory.list_facts(12) if row.get("text")]
        if not facts:
            self._log("🤖 Пока ничего о тебе не запомнил.")
            return True
        facts_block = "\n".join(f"- {item}" for item in facts)
        if self._ai_client and self._ai_client.is_enabled():
            prompt = (
                "Сформируй одно короткое предложение на русском: выбери 2-3 главных факта из списка.\n"
                "Без списков и без новых фактов.\n\n"
                f"Факты:\n{facts_block}\n\n"
                f"Запрос: {query}"
            )
            response = self._ai_client.get_response(
                prompt,
                history=[],
                system_prompt="Ты кратко пересказываешь, что ассистент помнит о пользователе.",
                max_tokens=120,
                temperature=0.2,
            )
            if response and str(response).strip():
                message = self._shorten_for_voice(str(response).strip(), max_sentences=1, max_chars=180)
                self._append_chat_message("user", query)
                self._append_chat_message("assistant", message)
                self._save_chat_history()
                self._last_ai_at = now_ts
                self._log(f"🤖 AI: {message}")
                return True
        self._log("🤖 Помню: " + "; ".join(facts[-3:]))
        return True

    def _forget_memory_from_context(self, query: str) -> str:
        q = (query or "").strip().lower()
        if q in {"забудь всё обо мне", "забудь обо мне", "забудь всё", "забудь все"}:
            self.memory.clear_all()
            return "ALL"
        if q.startswith("забудь последнее"):
            return self.memory.remove_last()
        generic = (
            "забудь эту информацию обо мне",
            "удали это из памяти",
            "убери это из памяти",
            "забудь это",
        )
        if q in generic:
            hint = ""
            for item in reversed(self._chat_history):
                if item.get("role") == "assistant":
                    hint = str(item.get("content") or "")
                    if hint:
                        break
            if not hint:
                for item in reversed(self._chat_history):
                    if item.get("role") == "user":
                        hint = str(item.get("content") or "")
                        if hint:
                            break
            removed = self.memory.find_best_match_by_hint(hint)
            if removed:
                return removed
            return self.memory.remove_last()
        m = re.match(r"^(?:забудь|удали)\s+(?:что\s+)?(.+)$", q)
        if m:
            return self.memory.remove_by_substring(m.group(1).strip())
        return ""

    def load_config(self):
        """Перезагружает конфигурацию из файла"""
        self.config = self._load_default_config()
        self._apply_context_config()
        self._init_ai_assistant()

    def _init_ai_assistant(self):
        ai_config = self.config.get("ai", {}) if isinstance(self.config, dict) else {}
        enabled = ai_config.get("enabled", True)

        keys, keys_created = ensure_keys_file()
        openai_key_from_file = keys.get("openai_api_key", "").strip()

        if not enabled:
            self._ai_client = None
            logger.info("AI assistant disabled in config")
            return

        try:
            from .openai_client import OpenAIClient

            api_key = (
                openai_key_from_file
                or ai_config.get("api_key", "")
                or os.getenv("OPENAI_API_KEY", "")
            )

            if keys_created:
                logger.warning("keys.json создан. Добавь ключ OpenAI в настройках.")
            elif not api_key:
                logger.warning("OPENAI_API_KEY отсутствует в keys.json/config/env")

            self._ai_client = OpenAIClient(
                api_key=api_key,
                model=ai_config.get("model", "gpt-4o-mini"),
                timeout_seconds=int(ai_config.get("timeout_seconds", 20)),
                max_tokens=int(ai_config.get("max_tokens", 220)),
                system_prompt=ai_config.get(
                    "system_prompt",
                    "Ты голосовой ассистент Джарвис на ПК; ответы для озвучки — одно короткое предложение по-русски. "
                    "Исполнитель открывает браузер по командам; не отрицай доступ к сайтам.",
                ),
            )
            logger.info("AI assistant initialized (OpenAI)")
        except Exception as error:
            logger.warning(f"AI assistant init failed: {error}")
            self._ai_client = None

    def handle_unrecognized_command(self, source_text: str) -> bool:
        query = (source_text or "").strip()
        if not query:
            self._log("🗣 Оффлайн fallback: не понял команду")
            return False

        pending_query = self._try_consume_pending_clarification(query)
        if pending_query == "":
            return True
        if pending_query:
            query = pending_query

        normalized_query = query.lower().strip(" .,!?")
        if normalized_query in self._reset_phrases:
            self.reset_chat_history("голосовая команда")
            self._log("🤖 Контекст очищен")
            return True
        if self._looks_like_reset_context_phrase(normalized_query):
            self.reset_chat_history("голосовая команда (fuzzy)")
            self._log("🤖 Контекст очищен")
            return True

        if self._is_memory_save_command(normalized_query):
            saved = self._save_memory_fact(query)
            if saved:
                self._log("🤖 Запомнил.")
            else:
                self._log("🤖 Не нашёл устойчивый факт для сохранения.")
            return True

        if self._is_memory_show_command(normalized_query):
            now_ts = time.time()
            if self._last_ai_at and (now_ts - self._last_ai_at) > self._chat_reset_timeout:
                self.reset_chat_history("долгая пауза")
            return self._reply_memory_summary(query, now_ts)

        if self._is_memory_forget_command(normalized_query):
            forgotten = self._forget_memory_from_context(query)
            if forgotten == "ALL":
                self._log("🤖 Удалил всю информацию о тебе.")
            elif forgotten:
                self._log(f"🤖 Удалил из памяти: {forgotten}")
            else:
                self._log("🤖 Не нашёл подходящую запись в памяти.")
            return True

        if normalized_query in {"покажи историю", "покажи контекст", "что в истории"}:
            if not self._chat_history:
                self._log("🤖 История пуста.")
                return True
            tail = self._chat_history[-6:]
            lines: List[str] = []
            for item in tail:
                role = item.get("role")
                content = (item.get("content") or "").strip()
                if not content or role not in {"user", "assistant"}:
                    continue
                label = "Ты" if role == "user" else "Jarvis"
                lines.append(f"{label}: {content[:180]}")
            if not lines:
                self._log("🤖 История пуста.")
                return True
            self._log("🤖 Недавняя история:\n" + "\n".join(lines))
            return True

        missing = self._detect_missing_args(query)
        if missing:
            kind, question = missing
            self._pending_clarification = {
                "kind": kind,
                "expires_at": time.time() + 15,
            }
            self._log(f"🤖 {question}")
            return True

        now_ts = time.time()
        if self._last_ai_at and (now_ts - self._last_ai_at) > self._chat_reset_timeout:
            self.reset_chat_history("долгая пауза")

        local_like = self._looks_like_local_domain_query(query)
        if self._try_local_domain_ai_fallback(query, now_ts):
            return True
        if local_like:
            self._log(
                "⚠ Не смог разобрать локальную команду (задачи/таймер/напоминание/системное). "
                "Скажи короче: «добавь задачу ...», «таймер на 10 минут», «напомни через ...»"
            )
            return True

        if self._ai_client and self._ai_client.is_enabled():
            self._log(f"[DEBUG] Unknown command -> AI, len={len(query)}")
            if self._try_unified_ai_turn(query, now_ts):
                return True
            last_error_text = getattr(self._ai_client, "last_error", None)
            if last_error_text:
                self._log(f"⚠ AI недоступен: {last_error_text}")
            else:
                self._log("⚠ AI недоступен: нет текста и нет last_error")

        self._log("🗣 Оффлайн fallback: не понял команду")
        return False

    def _load_default_config(self) -> dict:
        config_path = Path(__file__).with_name("config.json")
        if not config_path.exists():
            logger.warning(f"config.json не найден: {config_path}")
            return {}
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            
            # Валидация структуры конфига
            self._validate_config(config_data, config_path)
            
            # Разрешаем переменные окружения в путях
            if "apps" in config_data:
                for key, path in config_data["apps"].items():
                    expanded_path = os.path.expandvars(path)
                    # Проверяем, существует ли приложение
                    in_path = shutil.which(expanded_path) is not None
                    if not Path(expanded_path).exists() and not in_path:
                        logger.warning(f"Приложение не найдено: {key} -> {expanded_path}")
                    config_data["apps"][key] = expanded_path
            
            logger.info(f"Config загружен: {len(config_data.get('apps', {}))} приложений, "
                       f"{len(config_data.get('scenarios', {}))} сценариев")
            return config_data
        except json.JSONDecodeError as error:
            logger.error(f"Ошибка парсинга config.json: {error}")
            return {}
        except Exception as error:
            logger.error(f"Не удалось загрузить config.json: {error}")
            return {}
    
    @staticmethod
    def _validate_config(config_data: dict, config_path: Path) -> None:
        """
        Валидирует структуру config.json.
        
        Args:
            config_data: Загруженные данные конфигурации
            config_path: Путь к файлу конфига
            
        Raises:
            ValueError: Если конфиг имеет неверную структуру
        """
        errors = []
        
        # Проверка apps
        if "apps" in config_data:
            if not isinstance(config_data["apps"], dict):
                errors.append("'apps' должен быть объектом")
            else:
                for name, path in config_data["apps"].items():
                    if not isinstance(name, str):
                        errors.append(f"Имя приложения должно быть строкой: {name}")
                    if not isinstance(path, str):
                        errors.append(f"Путь приложения должен быть строкой: {name}={path}")
        
        # Проверка synonyms
        if "synonyms" in config_data:
            if not isinstance(config_data["synonyms"], dict):
                errors.append("'synonyms' должен быть объектом")
            else:
                for syn, target in config_data["synonyms"].items():
                    if not isinstance(syn, str):
                        errors.append(f"Синоним должен быть строкой: {syn}")
                    if not isinstance(target, str):
                        errors.append(f"Цель синонима должна быть строкой: {syn}={target}")
        
        # Проверка scenarios
        if "scenarios" in config_data:
            if not isinstance(config_data["scenarios"], dict):
                errors.append("'scenarios' должен быть объектом")
            else:
                for name, actions in config_data["scenarios"].items():
                    if not isinstance(name, str):
                        errors.append(f"Имя сценария должно быть строкой: {name}")
                    if not isinstance(actions, list):
                        errors.append(f"Действия сценария должны быть списком: {name}")
        
        if errors:
            error_msg = "\n".join(errors)
            logger.warning(f"Ошибки валидации config.json:\n{error_msg}")

    def _resolve_target(self, target: str) -> str:
        normalized = target.strip().lower()
        synonyms = self.config.get("synonyms", {})
        return synonyms.get(normalized, normalized)

    def _resolve_site_target(self, target: str) -> str:
        """Только опциональные подсказки из config.json (sites); без встроенного словаря сайтов."""
        normalized = target.strip().lower()
        builtins = {
            "microsoft teams": "teams.microsoft.com",
            "teams": "teams.microsoft.com",
            "майкрософт тимс": "teams.microsoft.com",
            "тимс": "teams.microsoft.com",
            "whatsapp": "web.whatsapp.com",
            "ватсап": "web.whatsapp.com",
            "ватсапп": "web.whatsapp.com",
            "вотсап": "web.whatsapp.com",
            "вацап": "web.whatsapp.com",
        }
        if normalized in builtins:
            return builtins[normalized]

        site_aliases = dict(self.config.get("sites", {}))
        if not isinstance(site_aliases, dict):
            site_aliases = {}

        if normalized in site_aliases:
            return site_aliases[normalized]

        for alias, resolved in site_aliases.items():
            if alias in normalized:
                return resolved

        return normalized

    def _get_volume_endpoint(self):
        """Получить эндпоинт громкости с обработкой ошибок"""
        try:
            speakers = AudioUtilities.GetSpeakers()
            endpoint_volume = getattr(speakers, "EndpointVolume", None)
            if endpoint_volume is not None:
                return endpoint_volume.QueryInterface(IAudioEndpointVolume)

            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
        except Exception as e:
            logger.error(f"Ошибка получения эндпоинта громкости: {e}")
            raise

    def open_app(self, target: str):
        target = self._resolve_target(target)
        article_target = self._expand_article_query_from_context(target)
        if target == "browser":
            try:
                # На части Windows-систем `about:` может быть не зарегистрирован.
                # Открываем безопасный http(s) URL, чтобы гарантированно запустить браузер.
                webbrowser.open("https://www.google.com", new=1)
                self._log("🌐 Открываю браузер по умолчанию (как в настройках Windows).")
            except Exception as error:
                self._log(f"⚠ Не удалось открыть браузер по умолчанию: {error}")
                logger.exception("Default browser open failed")
            return

        apps = self.config.get("apps", {})
        if target in apps:
            cmd_path = apps[target]
            try:
                # Безопасный запуск без shell=True
                subprocess.Popen([cmd_path], shell=False)
                logger.info(f"Запускаю: {cmd_path}")
            except Exception as e:
                logger.error(f"Ошибка запуска приложения {cmd_path}: {e}")
        else:
            if self._is_article_first_query(article_target):
                article_url = self._resolve_article_url_with_ai(article_target)
                if article_url:
                    self.browser_navigate(article_url)
                    logger.info(f"Article-first direct URL: {article_url}")
                    return
            # Интеллектуальное разрешение сайтов/запросов — через AI; исполнитель только открывает URL.
            if self._ai_client and self._ai_client.is_enabled():
                phrase = (
                    article_target
                    if re.match(r"^(открой|запусти|включи)\s+", article_target.strip().lower())
                    else f"открой {article_target}"
                )
                ai_intent = self._interpret_command_with_ai(phrase)
                if ai_intent:
                    # Если модель решила сделать browser_search для "открой сайт X",
                    # даём ещё шанс открыть именно главную страницу сайта.
                    if (
                        ai_intent.get("type") == "browser_search"
                        and "сайт" in phrase.lower()
                    ):
                        resolved = self._resolve_site_home_url_with_ai(target)
                        if resolved:
                            ai_intent = {"type": "browser_navigate", "slots": {"url": resolved}}
                    # Для "статья/документация/гайд" сначала пытаемся открыть прямой материал.
                    if (
                        ai_intent.get("type") == "browser_search"
                        and self._is_article_first_query(phrase)
                    ):
                        article_url = self._resolve_article_url_with_ai(article_target)
                        if article_url:
                            ai_intent = {"type": "browser_navigate", "slots": {"url": article_url}}
                    try:
                        self.run(ai_intent)
                        logger.info("Открыто через AI-интерпретацию (open_app)")
                        return
                    except Exception as e:
                        logger.warning(f"AI-команда open_app не выполнена: {e}")

            resolved_site = self._resolve_site_target(article_target)
            if resolved_site != article_target.strip().lower():
                self.browser_navigate(resolved_site)
                logger.info(f"Открываю сайт из config (sites): {resolved_site}")
                return

            if "." in article_target and " " not in article_target:
                self.browser_navigate(article_target)
                logger.info(f"Открываю как URL/домен: {article_target}")
                return

            if self._is_video_or_blog_query(article_target):
                yt_url = self._youtube_search_url(article_target)
                self.browser_navigate(yt_url)
                logger.info(f"Video/blog fallback to YouTube: {yt_url}")
                return

            self.browser_search(article_target)
            logger.info(f"Поиск в браузере (нет локального соответствия): {article_target}")

    def close_app(self, target: str):
        resolved = self._resolve_target(target)
        apps = self.config.get("apps", {}) if isinstance(self.config, dict) else {}
        cmd_path = str(apps.get(resolved, "")).strip() if isinstance(apps, dict) else ""

        if psutil is None:
            self._log("⚠ Закрытие приложений недоступно: не установлен psutil.")
            return

        friendly_names = {
            "browser": "браузер",
            "telegram": "Telegram",
            "vscode": "VS Code",
            "notepad": "Блокнот",
            "whatsapp": "WhatsApp",
        }
        self._log(f"🛑 Закрываю: {friendly_names.get(resolved, resolved)}")

        candidates = set()
        if cmd_path:
            candidates.add(Path(cmd_path).name.lower())
        if resolved == "browser":
            # Частые браузеры для локального target=browser.
            candidates.update({"chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "firefox.exe"})
        raw = str(target or "").strip().lower()
        if raw:
            candidates.add(raw if raw.endswith(".exe") else f"{raw}.exe")

        terminated = 0
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                pid = int(proc.info.get("pid") or 0)
                if pid == os.getpid():
                    continue
                name = (proc.info.get("name") or "").lower()
                exe_name = Path(proc.info.get("exe") or "").name.lower()
                cmdline = proc.info.get("cmdline") or []
                cmd0 = Path(cmdline[0]).name.lower() if cmdline else ""
                if {name, exe_name, cmd0} & candidates:
                    proc.terminate()
                    terminated += 1
            except Exception:
                continue

        if terminated:
            self._log(f"✅ Готово. Закрыто процессов: {terminated}")
        else:
            self._log(f"⚠ Не нашёл запущенный процесс: {friendly_names.get(resolved, resolved)}")

    def set_volume(self, value: int):
        try:
            value = max(0, min(100, int(value)))
            volume = self._get_volume_endpoint()
            volume.SetMasterVolumeLevelScalar(value / 100.0, None)
            logger.info(f"Громкость установлена на {value}%")
        except Exception as e:
            logger.error(f"Ошибка установки громкости: {e}")

    def change_volume(self, delta: int):
        try:
            volume = self._get_volume_endpoint()
            current = int(round(volume.GetMasterVolumeLevelScalar() * 100))
            self.set_volume(current + delta)
        except Exception as e:
            logger.error(f"Ошибка изменения громкости: {e}")

    def create_folder(self, name: str):
        try:
            folder_name = name.strip().strip("\"'")
            if not folder_name:
                logger.warning("Имя папки пустое")
                return

            folder_path = Path.cwd() / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Папка готова: {folder_path}")
        except Exception as e:
            logger.error(f"Ошибка создания папки: {e}")

    def run_scenario(self, name: str):
        try:
            scenarios = self.config.get("scenarios", {})
            if name not in scenarios:
                logger.warning(f"Сценарий не найден: {name}")
                return

            for action in scenarios[name]:
                try:
                    if action.startswith("open:"):
                        self.open_app(action.split(":", 1)[1])
                except Exception as e:
                    logger.error(f"Ошибка выполнения действия {action}: {e}")
        except Exception as e:
            logger.error(f"Ошибка выполнения сценария: {e}")

    def run(self, intent: dict):
        try:
            t = intent["type"]
            slots = intent.get("slots", {})
            
            # Сначала проверяем плагины
            if self.plugin_manager.handle_intent(t, slots):
                return

            self._record_action(t, slots)

            if t == "set_volume":
                self.set_volume(slots.get("value", 50))
                return

            if t == "volume_up":
                self.change_volume(abs(int(slots.get("delta", 10))))
                return

            if t == "volume_down":
                self.change_volume(-abs(int(slots.get("delta", 10))))
                return

            if t == "open_app":
                self.open_app(slots.get("target", ""))
                return

            if t == "close_app":
                self.close_app(slots.get("target", ""))
                return

            if t == "run_scenario":
                self.run_scenario(slots.get("name", ""))
                return

            if t == "create_folder":
                self.create_folder(slots.get("name", ""))
                return
            
            # Browser commands
            if t == "browser_navigate":
                self.browser_navigate(slots.get("url", ""))
                return
            
            if t == "browser_search":
                self.browser_search(slots.get("query", ""))
                return
            
            # Media commands
            if t == "media_play":
                self.media_play()
                return
            
            if t == "media_pause":
                self.media_pause()
                return
            
            if t == "media_next":
                self.media_next()
                return
            
            if t == "media_previous":
                self.media_previous()
                return

            # Presentation commands
            if t == "presentation_next_slide":
                self.presentation_next_slide()
                return

            if t == "presentation_previous_slide":
                self.presentation_previous_slide()
                return

            if t == "presentation_start":
                self.presentation_start()
                return

            if t == "presentation_end":
                self.presentation_end()
                return

            # Window management
            if t == "window_minimize":
                self.window_minimize()
                return

            if t == "window_maximize":
                self.window_maximize()
                return

            if t == "window_close":
                self.window_close()
                return

            if t == "window_switch":
                self.window_switch()
                return

            if t == "window_snap_left":
                self.window_snap_left()
                return

            if t == "window_snap_right":
                self.window_snap_right()
                return

            if t == "window_snap_up":
                self.window_snap_up()
                return

            if t == "window_snap_down":
                self.window_snap_down()
                return

            if t == "window_split_two":
                self.window_split_two()
                return
            
            # Calendar/Time commands
            if t == "show_date":
                self.show_date()
                return
            
            if t == "show_time":
                self.show_time()
                return
            
            # Reminders & Notes
            if t == "create_reminder":
                self.create_reminder(slots.get("reminder", ""))
                return

            if t == "start_timer":
                self.start_timer(
                    slots.get("amount", 0),
                    slots.get("unit", "минут"),
                    slots.get("label", ""),
                )
                return

            if t == "timer_status":
                self.timer_status()
                return

            if t == "cancel_timer":
                self.cancel_timer()
                return

            if t == "shutdown_pc":
                self.shutdown_pc()
                return

            if t == "restart_pc":
                self.restart_pc()
                return

            if t == "sleep_pc":
                self.sleep_pc()
                return

            if t == "lock_pc":
                self.lock_pc()
                return

            if t == "show_weather":
                self.show_weather(slots.get("city", ""))
                return

            if t == "repeat_last_command":
                self.repeat_last_command()
                return

            if t == "show_action_history":
                self.show_action_history()
                return

            if t == "add_todo":
                self.add_todo(slots.get("text", ""))
                return

            if t == "list_todos":
                self.list_todos()
                return

            if t == "complete_todo":
                self.complete_todo(slots.get("ref", ""))
                return

            if t == "delete_todo":
                self.delete_todo(slots.get("ref", ""))
                return
            
            if t == "add_note":
                note_text = slots.get("text", "")
                saved = self._save_memory_fact(note_text)
                if saved:
                    self._log("🤖 Запомнил.")
                    return
                self.add_note(note_text)
                return
            
            if t == "read_notes":
                self.read_notes()
                return

            logger.warning(f"Не понял команду: {intent}")
        except Exception as e:
            logger.error(f"Ошибка выполнения команды: {e}")
    
    def copy_file(self, source: str, destination: str):
        """Копировать файл"""
        try:
            src = Path(source)
            dst = Path(destination)
            
            if not src.exists():
                logger.warning(f"Файл не найден: {source}")
                return
            
            if src.is_file():
                shutil.copy2(src, dst)
                logger.info(f"Файл скопирован: {source} → {destination}")
            else:
                logger.warning(f"Это не файл: {source}")
        except Exception as e:
            logger.error(f"Ошибка копирования файла: {e}")
    
    def move_file(self, source: str, destination: str):
        """Переместить файл"""
        try:
            src = Path(source)
            dst = Path(destination)
            
            if not src.exists():
                logger.warning(f"Файл не найден: {source}")
                return
            
            shutil.move(str(src), str(dst))
            logger.info(f"Файл перемещён: {source} → {destination}")
        except Exception as e:
            logger.error(f"Ошибка перемещения файла: {e}")
    
    def delete_file(self, path: str):
        """Удалить файл"""
        try:
            p = Path(path)
            
            if not p.exists():
                logger.warning(f"Файл не найден: {path}")
                return
            
            if p.is_file():
                p.unlink()
                logger.info(f"Файл удалён: {path}")
            else:
                logger.warning(f"Это не файл: {path}")
        except Exception as e:
            logger.error(f"Ошибка удаления файла: {e}")
    
    def create_file(self, path: str, content: str = ""):
        """Создать файл с содержимым"""
        try:
            p = Path(path)
            
            # Создаём необходимые папки
            p.parent.mkdir(parents=True, exist_ok=True)
            
            # Создаём файл
            p.write_text(content, encoding="utf-8")
            logger.info(f"Файл создан: {path}")
        except Exception as e:
            logger.error(f"Ошибка создания файла: {e}")
    
    # ============ Browser Commands ============
    def browser_navigate(self, url: str):
        """Открыть URL в браузере"""
        try:
            original = (url or "").strip()
            if original.startswith(("http://", "https://")) or "/" in original or "?" in original:
                url = original
            else:
                url = self._resolve_site_target(original)

            # host/path?query без схемы → https://...
            if (
                not url.startswith(("http://", "https://"))
                and ("/" in url or "?" in url)
                and " " not in url
            ):
                cand = "https://" + url.lstrip("/")
                if self._is_safe_http_url(cand):
                    url = cand

            # If phrase doesn't look like a domain/URL, treat as search query.
            if not self._looks_like_domain_or_url(url):
                self.browser_search(original)
                return

            # Добавляем https если нет протокола
            if not url.startswith(("http://", "https://")):
                url = "https://" + url

            if self._open_in_preferred_browser(url):
                logger.info(f"Открываю в предпочтительном браузере: {url}")
                return

            webbrowser.open(url)
            logger.info(f"Открываю в системном браузере: {url}")
        except Exception as e:
            logger.error(f"Ошибка навигации в браузере: {e}")

    @classmethod
    def _looks_like_domain_or_url(cls, value: str) -> bool:
        v = (value or "").strip()
        if not v:
            return False
        if v.startswith(("http://", "https://")):
            return cls._is_safe_http_url(v)
        if " " in v:
            return False
        # Basic domain-like shape: host.tld (без пути — дальше добавим https://)
        if "." in v and re.match(r"^[a-zA-Z0-9\-\.]+$", v):
            return True
        return False
    
    def browser_search(self, query: str):
        """Поиск в Google"""
        try:
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            if self._open_in_preferred_browser(search_url):
                logger.info(f"Ищу в Google через предпочтительный браузер: {query}")
                return
            webbrowser.open(search_url)
            logger.info(f"Ищу в Google через системный браузер: {query}")
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")

    def _open_in_preferred_browser(self, url: str) -> bool:
        apps = self.config.get("apps", {}) if isinstance(self.config, dict) else {}
        browser_cmd = (apps.get("browser") or "").strip() if isinstance(apps, dict) else ""
        if not browser_cmd:
            return False
        try:
            is_running = self._is_preferred_browser_running(browser_cmd)
            if is_running and self._supports_new_tab_flag(browser_cmd):
                subprocess.Popen([browser_cmd, "--new-tab", url], shell=False)
            else:
                subprocess.Popen([browser_cmd, url], shell=False)
            return True
        except Exception as error:
            logger.warning(f"Не удалось открыть предпочтительный браузер '{browser_cmd}': {error}")
            return False

    @staticmethod
    def _supports_new_tab_flag(browser_cmd: str) -> bool:
        name = Path((browser_cmd or "").strip()).name.lower()
        return any(x in name for x in ("chrome", "msedge", "edge", "brave", "opera", "vivaldi", "yandex"))

    @staticmethod
    def _is_preferred_browser_running(browser_cmd: str) -> bool:
        if psutil is None:
            return False
        exe_name = Path((browser_cmd or "").strip()).name.lower()
        if not exe_name:
            return False
        try:
            for proc in psutil.process_iter(["name", "exe", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    exe = Path(proc.info.get("exe") or "").name.lower()
                    cmdline = proc.info.get("cmdline") or []
                    cmd0 = Path(cmdline[0]).name.lower() if cmdline else ""
                except Exception:
                    continue
                if exe_name in {name, exe, cmd0}:
                    return True
        except Exception:
            return False
        return False
    
    # ============ Media Commands ============
    def media_play(self):
        """Включить музыку (Play)"""
        try:
            if pyautogui is None:
                logger.warning("pyautogui не установлен. Media команды недоступны.")
                return
            pyautogui.press('playpause')
            logger.info("Музыка включена")
        except Exception as e:
            logger.error(f"Ошибка воспроизведения: {e}")
    
    def media_pause(self):
        """Пауза"""
        try:
            if pyautogui is None:
                logger.warning("pyautogui не установлен.")
                return
            pyautogui.press('playpause')
            logger.info("Пауза")
        except Exception as e:
            logger.error(f"Ошибка паузы: {e}")
    
    def media_next(self):
        """Следующий трек"""
        try:
            if pyautogui is None:
                logger.warning("pyautogui не установлен.")
                return
            pyautogui.press('nexttrack')
            logger.info("Следующий трек")
        except Exception as e:
            logger.error(f"Ошибка переключения трека: {e}")
    
    def media_previous(self):
        """Предыдущий трек"""
        try:
            if pyautogui is None:
                logger.warning("pyautogui не установлен.")
                return
            pyautogui.press('prevtrack')
            logger.info("Предыдущий трек")
        except Exception as e:
            logger.error(f"Ошибка переключения на предыдущий трек: {e}")

    # ============ Presentation Commands ============
    def presentation_next_slide(self):
        try:
            if pyautogui is None:
                logger.warning("pyautogui не установлен.")
                return
            pyautogui.press("right")
            self._log("🎞 Следующий слайд.")
        except Exception as e:
            logger.error(f"Ошибка переключения на следующий слайд: {e}")

    def presentation_previous_slide(self):
        try:
            if pyautogui is None:
                logger.warning("pyautogui не установлен.")
                return
            pyautogui.press("left")
            self._log("🎞 Предыдущий слайд.")
        except Exception as e:
            logger.error(f"Ошибка переключения на предыдущий слайд: {e}")

    def presentation_start(self):
        try:
            if pyautogui is None:
                logger.warning("pyautogui не установлен.")
                return
            pyautogui.press("f5")
            self._log("🎞 Запустил презентацию.")
        except Exception as e:
            logger.error(f"Ошибка запуска презентации: {e}")

    def presentation_end(self):
        try:
            if pyautogui is None:
                logger.warning("pyautogui не установлен.")
                return
            pyautogui.press("esc")
            self._log("🎞 Завершил презентацию.")
        except Exception as e:
            logger.error(f"Ошибка завершения презентации: {e}")

    # ============ Window Management ============
    def window_minimize(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        pyautogui.hotkey("winleft", "down")
        self._log("🪟 Свернул текущее окно.")

    def window_maximize(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        pyautogui.hotkey("winleft", "up")
        self._log("🪟 Развернул текущее окно.")

    def window_close(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        pyautogui.hotkey("alt", "f4")
        self._log("🪟 Закрыл текущее окно.")

    def window_switch(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        pyautogui.hotkey("alt", "tab")
        self._log("🪟 Переключил окно.")

    def window_snap_left(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        pyautogui.hotkey("winleft", "left")
        self._log("🪟 Окно влево.")

    def window_snap_right(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        pyautogui.hotkey("winleft", "right")
        self._log("🪟 Окно вправо.")

    def window_snap_up(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        pyautogui.hotkey("winleft", "up")
        self._log("🪟 Окно вверх.")

    def window_snap_down(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        pyautogui.hotkey("winleft", "down")
        self._log("🪟 Окно вниз.")

    def window_split_two(self):
        if pyautogui is None:
            logger.warning("pyautogui не установлен.")
            return
        # Надёжный вариант для Windows Snap:
        # 1) Прижимаем текущее окно влево
        # 2) Даём Snap Assist показать список окон справа
        # Автопереключение Alt+Tab часто возвращает окно назад/ломает раскладку.
        pyautogui.hotkey("winleft", "left")
        time.sleep(0.15)
        self._log("🪟 Окно слева. Выбери второе окно в Snap Assist справа.")

    # ============ Action History ============
    def repeat_last_command(self):
        repeatable = {
            "set_volume",
            "volume_up",
            "volume_down",
            "open_app",
            "close_app",
            "create_folder",
            "browser_navigate",
            "browser_search",
            "media_play",
            "media_pause",
            "media_next",
            "media_previous",
            "show_date",
            "show_time",
            "create_reminder",
            "start_timer",
            "timer_status",
            "cancel_timer",
            "add_todo",
            "list_todos",
            "complete_todo",
            "delete_todo",
            "show_weather",
            "lock_pc",
            "window_minimize",
            "window_maximize",
            "window_close",
            "window_switch",
            "window_snap_left",
            "window_snap_right",
            "window_snap_up",
            "window_snap_down",
            "window_split_two",
            "presentation_next_slide",
            "presentation_previous_slide",
            "presentation_start",
            "presentation_end",
        }
        for row in reversed(self._action_history):
            t = str(row.get("type") or "")
            if t in repeatable:
                slots = row.get("slots")
                self._log(f"🔁 Повторяю: {t}")
                self.run({"type": t, "slots": slots if isinstance(slots, dict) else {}})
                return
        self._log("🔁 Нет команды для повтора.")

    def show_action_history(self):
        if not self._action_history:
            self._log("📜 История действий пуста.")
            return
        tail = self._action_history[-5:]
        self._log("📜 Последние действия:")
        for i, row in enumerate(tail, 1):
            self._log(f"  {i}. {row.get('type', 'unknown')}")
    
    # ============ Calendar/Time Commands ============
    def show_date(self):
        """Показать текущую дату"""
        try:
            now = datetime.now()
            months_ru = {
                1: "января", 2: "февраля", 3: "марта", 4: "апреля",
                5: "мая", 6: "июня", 7: "июля", 8: "августа",
                9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
            }
            date_str = f"{now.day} {months_ru[now.month]} {now.year} года"
            
            self._log(f"📅 Дата: {date_str}")
        except Exception as e:
            logger.error(f"Ошибка показа даты: {e}")
    
    def show_time(self):
        """Показать текущее время"""
        try:
            now = datetime.now()
            time_str = now.strftime("%H:%M")
            time_ru = f"{now.hour} часов {now.minute} минут"
            
            self._log(f"🕐 Время: {time_str}")
        except Exception as e:
            logger.error(f"Ошибка показа времени: {e}")
    
    # ============ Reminders & Notes ============
    @staticmethod
    def _normalize_spaces(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip())

    def _reminders_file(self) -> Path:
        reminders_file = Path.home() / ".jarvis" / "reminders.json"
        reminders_file.parent.mkdir(parents=True, exist_ok=True)
        return reminders_file

    def _load_reminders(self) -> list[dict]:
        path = self._reminders_file()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _save_reminders(self, reminders: list[dict]) -> None:
        path = self._reminders_file()
        path.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")

    def _parse_reminder_schedule(self, reminder: str) -> tuple[str, datetime | None]:
        """
        Поддержка минимального формата:
        - "через 10 минут <текст>" / "10 минут <текст>"
        - "через 2 часа <текст>" / "2 часа <текст>"
        - "в 14:30 <текст>"
        """
        raw = self._normalize_spaces(reminder)
        if not raw:
            return "", None

        # Относительное время: (через) N минут/часов
        m_rel = re.match(
            r"^(?:через\s+)?(.+?)\s+(минут[ауы]?|час(?:а|ов)?)\s+(.+)$",
            raw,
            flags=re.IGNORECASE,
        )
        if m_rel:
            amount_text = self._normalize_spaces(m_rel.group(1))
            unit = (m_rel.group(2) or "").lower()
            text = self._normalize_spaces(m_rel.group(3))
            amount = extract_number(amount_text)
            if amount is not None and amount > 0 and text:
                if unit.startswith("минут"):
                    return text, datetime.now() + timedelta(minutes=amount)
                return text, datetime.now() + timedelta(hours=amount)

        # Абсолютное время: в HH:MM
        m_abs = re.match(r"^в\s*(\d{1,2})[:.](\d{2})\s+(.+)$", raw, flags=re.IGNORECASE)
        if m_abs:
            hour = int(m_abs.group(1))
            minute = int(m_abs.group(2))
            text = self._normalize_spaces(m_abs.group(3))
            if 0 <= hour <= 23 and 0 <= minute <= 59 and text:
                now = datetime.now()
                due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if due <= now:
                    due = due + timedelta(days=1)
                return text, due

        return raw, None

    def pop_due_reminders(self) -> list[str]:
        """Вернуть сработавшие напоминания и пометить их выполненными."""
        with self._reminders_lock:
            reminders = self._load_reminders()
            if not reminders:
                return []
            now = datetime.now()
            fired: list[str] = []
            changed = False
            for row in reminders:
                if not isinstance(row, dict):
                    continue
                if bool(row.get("done")):
                    continue
                due_raw = str(row.get("due_at") or "").strip()
                if not due_raw:
                    continue
                try:
                    due_dt = datetime.fromisoformat(due_raw)
                except Exception:
                    continue
                if due_dt <= now:
                    text = self._normalize_spaces(str(row.get("text") or ""))
                    if text:
                        fired.append(text)
                    row["done"] = True
                    row["done_at"] = now.isoformat()
                    changed = True
            if changed:
                self._save_reminders(reminders)
            return fired

    def create_reminder(self, reminder: str):
        """Создать напоминание"""
        try:
            clean_text, due_at = self._parse_reminder_schedule(reminder)
            if not clean_text:
                self._log("⚠ Не удалось создать напоминание: пустой текст.")
                return

            with self._reminders_lock:
                reminders = self._load_reminders()
                payload = {
                    "text": clean_text,
                    "created": datetime.now().isoformat(),
                    "done": False,
                }
                if due_at is not None:
                    payload["due_at"] = due_at.isoformat()
                reminders.append(payload)
                self._save_reminders(reminders)

            if due_at is not None:
                self._log(f"⏰ Напоминание создано на {due_at.strftime('%H:%M')}: {clean_text}")
            else:
                self._log(
                    "⏰ Напоминание сохранено без времени. "
                    "Скажи: «напомни через 10 минут ...» или «напомни в 19:30 ...»"
                )
        except Exception as e:
            logger.error(f"Ошибка создания напоминания: {e}")

    # ============ Timer ============
    @staticmethod
    def _timer_total_seconds(amount: int, unit: str) -> int:
        value = max(1, int(amount))
        u = (unit or "").lower()
        if u.startswith("сек"):
            return value
        if u.startswith("час"):
            return value * 3600
        return value * 60

    def start_timer(self, amount: int, unit: str, label: str = ""):
        try:
            total = self._timer_total_seconds(int(amount), str(unit))
        except Exception:
            self._log("⚠ Не удалось запустить таймер: неверное время.")
            return
        now = time.time()
        with self._timer_lock:
            self._active_timer = {
                "created_at": datetime.now().isoformat(),
                "end_ts": now + total,
                "seconds": total,
                "label": self._normalize_spaces(label),
                "done": False,
            }
        if total >= 60:
            human = f"{total // 60} мин"
        else:
            human = f"{total} сек"
        if label:
            self._log(f"⏱ Таймер запущен на {human}: {self._normalize_spaces(label)}")
        else:
            self._log(f"⏱ Таймер запущен на {human}.")

    def timer_status(self):
        with self._timer_lock:
            timer = dict(self._active_timer) if isinstance(self._active_timer, dict) else None
        if not timer or bool(timer.get("done")):
            self._log("⏱ Активного таймера нет.")
            return
        left = int(round(float(timer.get("end_ts", 0)) - time.time()))
        if left <= 0:
            self._log("⏱ Таймер уже срабатывает.")
            return
        minutes, seconds = divmod(left, 60)
        if minutes > 0:
            self._log(f"⏱ До таймера осталось: {minutes} мин {seconds} сек.")
        else:
            self._log(f"⏱ До таймера осталось: {seconds} сек.")

    def cancel_timer(self):
        with self._timer_lock:
            existed = isinstance(self._active_timer, dict) and not bool(self._active_timer.get("done"))
            self._active_timer = None
        if existed:
            self._log("⏱ Таймер отменён.")
        else:
            self._log("⏱ Активного таймера нет.")

    def pop_due_timers(self) -> list[str]:
        with self._timer_lock:
            timer = self._active_timer
            if not isinstance(timer, dict):
                return []
            if bool(timer.get("done")):
                return []
            if float(timer.get("end_ts", 0)) > time.time():
                return []
            timer["done"] = True
            self._active_timer = None
            label = self._normalize_spaces(str(timer.get("label") or ""))
            return [label] if label else [""]

    # ============ System actions ============
    def shutdown_pc(self):
        try:
            self._log("🛑 Выключаю компьютер...")
            subprocess.Popen(["shutdown", "/s", "/t", "0"], shell=False)
        except Exception as e:
            logger.error(f"Ошибка выключения ПК: {e}")

    def restart_pc(self):
        try:
            self._log("🛑 Перезагружаю компьютер...")
            subprocess.Popen(["shutdown", "/r", "/t", "0"], shell=False)
        except Exception as e:
            logger.error(f"Ошибка перезагрузки ПК: {e}")

    def sleep_pc(self):
        try:
            self._log("🛑 Перевожу компьютер в сон...")
            subprocess.Popen(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                shell=False,
            )
        except Exception as e:
            logger.error(f"Ошибка перехода в сон: {e}")

    def lock_pc(self):
        try:
            self._log("🛑 Блокирую экран...")
            if os.name == "nt":
                import ctypes

                ctypes.windll.user32.LockWorkStation()
                return
            self._log("⚠ Блокировка экрана поддерживается только в Windows.")
        except Exception as e:
            logger.error(f"Ошибка блокировки экрана: {e}")

    def show_weather(self, city: str):
        """Показать текущую погоду через wttr.in (без API-ключа)."""
        target_city = self._normalize_spaces(city) or "Astana"
        try:
            url = f"https://wttr.in/{quote_plus(target_city)}?format=j1"
            with urlopen(url, timeout=6) as response:
                raw = response.read().decode("utf-8", errors="ignore")
            payload = json.loads(raw)
            current = (payload.get("current_condition") or [{}])[0]
            temp = str(current.get("temp_C", "")).strip()
            desc_items = current.get("weatherDesc") or []
            desc = ""
            if isinstance(desc_items, list) and desc_items:
                desc = str((desc_items[0] or {}).get("value", "")).strip()
            humidity = str(current.get("humidity", "")).strip()
            if not temp:
                self._log(f"⚠ Не удалось получить погоду для: {target_city}")
                return
            parts = [f"🌤 Погода в {target_city}: {temp}°C"]
            if desc:
                parts.append(desc.lower())
            if humidity:
                parts.append(f"влажность {humidity}%")
            self._log(", ".join(parts) + ".")
        except Exception as e:
            logger.error(f"Ошибка получения погоды: {e}")
            self._log(f"⚠ Не удалось получить погоду для: {target_city}")

    # ============ Todo ============
    def _todos_file(self) -> Path:
        todos_file = Path.home() / ".jarvis" / "todos.json"
        todos_file.parent.mkdir(parents=True, exist_ok=True)
        return todos_file

    def _load_todos(self) -> list[dict]:
        path = self._todos_file()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    def _save_todos(self, todos: list[dict]) -> None:
        self._todos_file().write_text(
            json.dumps(todos, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _find_todo_index(todos: list[dict], ref: str) -> int:
        token = (ref or "").strip()
        if not token:
            return -1
        num = extract_number(token)
        if num is not None and num > 0:
            visible = [idx for idx, row in enumerate(todos) if not bool(row.get("done"))]
            pos = num - 1
            if 0 <= pos < len(visible):
                return visible[pos]
        low = token.lower()
        for idx, row in enumerate(todos):
            text = str(row.get("text") or "").lower()
            if low and low in text and not bool(row.get("done")):
                return idx
        return -1

    def add_todo(self, text: str):
        task = self._normalize_spaces(text)
        if not task:
            self._log("⚠ Не удалось добавить задачу: пустой текст.")
            return
        todos = self._load_todos()
        todos.append(
            {
                "text": task,
                "created": datetime.now().isoformat(),
                "done": False,
            }
        )
        self._save_todos(todos)
        self._log(f"📌 Задача добавлена: {task}")

    def list_todos(self):
        todos = self._load_todos()
        active = [row for row in todos if isinstance(row, dict) and not bool(row.get("done"))]
        if not active:
            self._log("📌 Активных задач нет.")
            return
        self._log(f"📌 Активных задач: {len(active)}")
        for i, row in enumerate(active[:10], 1):
            self._log(f"  {i}. {str(row.get('text') or '').strip()}")

    def complete_todo(self, ref: str):
        todos = self._load_todos()
        idx = self._find_todo_index(todos, ref)
        if idx < 0:
            self._log("⚠ Задача не найдена.")
            return
        todos[idx]["done"] = True
        todos[idx]["done_at"] = datetime.now().isoformat()
        text = str(todos[idx].get("text") or "").strip()
        self._save_todos(todos)
        self._log(f"✅ Задача выполнена: {text}")

    def delete_todo(self, ref: str):
        todos = self._load_todos()
        idx = self._find_todo_index(todos, ref)
        if idx < 0:
            self._log("⚠ Задача не найдена.")
            return
        text = str(todos[idx].get("text") or "").strip()
        todos.pop(idx)
        self._save_todos(todos)
        self._log(f"🗑 Удалил задачу: {text}")
    
    def add_note(self, text: str):
        """Добавить заметку"""
        try:
            notes_file = Path.home() / ".jarvis" / "notes.json"
            notes_file.parent.mkdir(parents=True, exist_ok=True)
            
            notes = []
            if notes_file.exists():
                notes = json.loads(notes_file.read_text(encoding="utf-8"))
            
            notes.append({
                "text": text,
                "timestamp": datetime.now().isoformat(),
                "tags": []
            })
            
            notes_file.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log(f"📝 Заметка добавлена: {text}")
        except Exception as e:
            logger.error(f"Ошибка добавления заметки: {e}")
    
    def read_notes(self):
        """Прочитать все заметки"""
        try:
            notes_file = Path.home() / ".jarvis" / "notes.json"
            
            if not notes_file.exists():
                self._log("📝 Заметок не найдено")
                return
            
            notes = json.loads(notes_file.read_text(encoding="utf-8"))
            
            if not notes:
                self._log("📝 Заметок нет")
                return
            
            self._log(f"📝 Найдено заметок: {len(notes)}")
            
            # Показываем последние 5 заметок
            for i, note in enumerate(notes[-5:], 1):
                self._log(f"  {i}. {note['text']}")
        except Exception as e:
            logger.error(f"Ошибка чтения заметок: {e}")
