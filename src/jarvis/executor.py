import json
import subprocess
import logging
import os
import shutil
import time
import re
from urllib.parse import quote_plus, urlparse
from pathlib import Path
from ctypes import cast, POINTER
from datetime import datetime
from typing import List, Dict
import webbrowser

try:
    import pyautogui
except ImportError:
    pyautogui = None

from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL
from .plugin_api import PluginManager
from .key_store import ensure_keys_file
from .memory_store import MemoryStore

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self, config=None, log_callback=None):
        self.config = config or self._load_default_config()
        self.log_callback = log_callback  # Callback для GUI
        self._ai_client = None
        self._chat_history: List[Dict[str, str]] = []
        self._chat_history_path = Path.home() / ".jarvis" / "chat_history.json"
        self._chat_history_limit_pairs = 5
        self._chat_reset_timeout = 300  # сек
        self._last_ai_at: float | None = None
        self.memory = MemoryStore()
        self._reset_phrases = {
            "очисти контекст",
            "забудь контекст",
            "забудь диалог",
            "сбрось контекст",
        }
        
        # Инициализируем систему плагинов
        self.plugin_manager = PluginManager()
        self._init_ai_assistant()
        self._load_chat_history()

    @staticmethod
    def _should_run_ai_memory_extract(query: str) -> bool:
        text = (query or "").strip().lower()
        if len(text) < 8:
            return False
        command_like = ("открой ", "запусти ", "сделай ", "включи ", "выключи ", "громкость", "пауза")
        if any(text.startswith(prefix) for prefix in command_like):
            return False
        triggers = (
            "меня зовут",
            "мой ник",
            "мне ",
            "я учусь",
            "я работаю",
            "предпочитаю",
            "мне нравится",
            "сейчас",
            "пишу диплом",
            "проект",
        )
        return any(t in text for t in triggers)

    def _extract_memory_with_ai(self, query: str):
        if not self._ai_client or not self._ai_client.is_enabled():
            return
        if not self._should_run_ai_memory_extract(query):
            return

        extract_prompt = (
            "Определи, нужно ли сохранить это сообщение в память ассистента.\n"
            "Верни строго JSON без пояснений со схемой:\n"
            "{\"save\":bool,\"layer\":\"core|session\",\"type\":\"profile|preference|fact|project\","
            "\"key\":\"optional\",\"value\":\"...\",\"importance\":1..5}\n"
            "Если сохранять не нужно, верни {\"save\":false}.\n"
            f"Сообщение пользователя: {query}"
        )
        raw = self._ai_client.get_response(
            extract_prompt,
            history=None,
            system_prompt=(
                "Ты memory-classifier. Отвечай только валидным JSON без markdown и без комментариев."
            ),
            max_tokens=180,
            temperature=0.0,
        )
        if not raw:
            return

        try:
            suggestion = json.loads(raw)
        except Exception:
            return
        self.memory.save_ai_suggestion(suggestion)

    def _interpret_command_with_ai(self, query: str) -> dict | None:
        if not self._ai_client or not self._ai_client.is_enabled():
            return None
        text = (query or "").strip()
        if not text:
            return None

        system_prompt = (
            "Ты интерпретатор команд ассистента. Доступные команды:\n"
            "- set_volume(value 0..100)\n"
            "- volume_up(delta 1..100)\n"
            "- volume_down(delta 1..100)\n"
            "- browser_navigate — открыть сайт или страницу\n"
            "- browser_search(query) — общий поиск в браузере (Google), если нельзя дать точный URL\n"
            "Для browser_navigate ты сам выбираешь ресурс и формат:\n"
            "- По возможности верни готовый безопасный URL в slots.url (только http или https, полная ссылка).\n"
            "- Главная страница сайта: slots.url вида https://www.youtube.com/ или https://github.com/\n"
            "- Поиск внутри сайта (канал на YouTube, репозиторий на GitHub и т.д.): slots.url с корректным "
            "адресом страницы поиска или результатов на этом сайте (например youtube.com/results?search_query=...).\n"
            "- Если точный URL сформировать нельзя, можно вернуть slots.site и slots.query (коротко: что за площадка и что искать); "
            "исполнитель откроет безопасный поиск, не подставляя списки сайтов из кода.\n"
            "- Не придумывай опасные схемы (file:, javascript:, data: и т.п.).\n"
            "Пример: 'открой канал MrBeast на YouTube' -> "
            "{\"mode\":\"command\",\"intent\":\"browser_navigate\","
            "\"slots\":{\"url\":\"https://www.youtube.com/results?search_query=MrBeast\"}}\n"
            "Если фраза — команда, верни только JSON:\n"
            "{\"mode\":\"command\",\"intent\":\"...\",\"slots\":{...}}\n"
            "Если это не команда, верни:\n"
            "{\"mode\":\"chat\"}\n"
            "Никакого текста вне JSON."
        )
        raw = self._ai_client.get_response(
            text,
            history=None,
            system_prompt=system_prompt,
            max_tokens=140,
            temperature=0.0,
        )
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return self._validate_ai_command_payload(payload)

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

    @staticmethod
    def _google_search_url(query: str) -> str:
        """Универсальный поиск без привязки к конкретным доменам в коде."""
        return f"https://www.google.com/search?q={quote_plus(query.strip())}"

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
                # "открой сайт X" часто приходит как site+query, где query ~= site.
                # В этом случае сначала пытаемся открыть главную страницу сайта.
                if self._normalized_label(site) and self._normalized_label(site) == self._normalized_label(query):
                    resolved = self._resolve_site_home_url_with_ai(site)
                    if resolved:
                        return {"type": "browser_navigate", "slots": {"url": resolved}}
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
            return {"type": "browser_search", "slots": {"query": query}}

        return None
    
    def _log(self, message: str):
        """Универсальное логирование - в logger и GUI callback"""
        logger.info(message)
        if self.log_callback:
            self.log_callback(message)

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
        max_messages = self._chat_history_limit_pairs * 2
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

    def reset_user_memory(self):
        self.memory.clear_all()

    def clear_recent_memory_context(self):
        self.reset_chat_history("manual")
        self.memory.clear_recent_context(last_n=10)

    def load_config(self):
        """Перезагружает конфигурацию из файла"""
        self.config = self._load_default_config()
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
                    "Ты голосовой ассистент Джарвис. Отвечай кратко и по делу на русском языке.",
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

        self._log(f"[DEBUG] Unknown command -> AI, len={len(query)}")

        normalized_query = query.lower().strip(" .,!?")
        if normalized_query in self._reset_phrases:
            self.reset_chat_history("голосовая команда")
            self._log("🤖 Контекст очищен")
            return True

        if normalized_query in {"очисти память", "сбрось память"}:
            self.clear_recent_memory_context()
            self._log("🤖 Очищен недавний контекст (история и последние записи)")
            return True

        if normalized_query in {"удали всю информацию обо мне", "забудь всё", "забудь все", "забудь всю информацию обо мне"}:
            self.reset_user_memory()
            self.reset_chat_history("manual")
            self._log("🤖 Вся информация о пользователе удалена")
            return True

        if (
            normalized_query in {"что ты помнишь", "что ты обо мне помнишь", "что ты знаешь обо мне"}
            or ("что ты помнишь" in normalized_query and "обо мне" in normalized_query)
        ):
            self._log(f"🤖 Память: {self.memory.describe_profile()}")
            return True

        now_ts = time.time()
        if self._last_ai_at and (now_ts - self._last_ai_at) > self._chat_reset_timeout:
            self.reset_chat_history("долгая пауза")

        if self._ai_client and self._ai_client.is_enabled():
            ai_intent = self._interpret_command_with_ai(query)
            if ai_intent:
                self._log(f"[DEBUG] AI command intent: {ai_intent['type']}")
                try:
                    self.run(ai_intent)
                    self._log("✅ Готово.")
                    return True
                except Exception as error:
                    self._log(f"❌ Ошибка выполнения AI-команды: {error}")
                    logger.exception("AI interpreted command failed")

            self._log(
                f"[DEBUG] AI client present, model={getattr(self._ai_client, 'model', '?')}, history={len(self._chat_history)}"
            )

            last_error_text = None
            response = None
            self._extract_memory_with_ai(query)
            self.memory.learn_from_user_text(query)
            request_history = list(self._chat_history)
            memory_context = self.memory.build_context(query, top_k=4)
            if memory_context:
                request_history.append(
                    {
                        "role": "system",
                        "content": "Память о пользователе (учитывай при ответе):\n" + memory_context,
                    }
                )
            for attempt in range(2):
                response = self._ai_client.get_response(query, history=request_history)
                last_error_text = getattr(self._ai_client, "last_error", None)

                if response:
                    break

                empty_err = last_error_text and "пуст" in last_error_text.lower()
                rate_limited = last_error_text and "429" in last_error_text

                if attempt == 0 and empty_err:
                    self._log("[DEBUG] AI вернул пустой ответ, пробую ещё раз")
                    continue

                if rate_limited or (attempt == 0 and last_error_text and "ограничил" in last_error_text.lower()):
                    # Не крутим бесконечно на rate-limit
                    break

            if response:
                self._append_chat_message("user", query)
                self._append_chat_message("assistant", response)
                self._save_chat_history()
                self._last_ai_at = now_ts
                self._log(f"🤖 AI: {response}")
                return True

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
            # Интеллектуальное разрешение сайтов/запросов — через AI; исполнитель только открывает URL.
            if self._ai_client and self._ai_client.is_enabled():
                phrase = (
                    target
                    if re.match(r"^(открой|запусти|включи)\s+", target.strip().lower())
                    else f"открой {target}"
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
                    try:
                        self.run(ai_intent)
                        logger.info("Открыто через AI-интерпретацию (open_app)")
                        return
                    except Exception as e:
                        logger.warning(f"AI-команда open_app не выполнена: {e}")

            resolved_site = self._resolve_site_target(target)
            if resolved_site != target.strip().lower():
                self.browser_navigate(resolved_site)
                logger.info(f"Открываю сайт из config (sites): {resolved_site}")
                return

            if "." in target and " " not in target:
                self.browser_navigate(target)
                logger.info(f"Открываю как URL/домен: {target}")
                return

            self.browser_search(target)
            logger.info(f"Поиск в браузере (нет локального соответствия): {target}")

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
            
            if t == "add_note":
                note_text = slots.get("text", "")
                lowered_note = (note_text or "").lower()
                if "информац" in lowered_note and "обо мне" in lowered_note:
                    # Route "remember about me" phrases into personal memory flow.
                    if self.handle_unrecognized_command(note_text):
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
            subprocess.Popen([browser_cmd, url], shell=False)
            return True
        except Exception as error:
            logger.warning(f"Не удалось открыть предпочтительный браузер '{browser_cmd}': {error}")
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
    def create_reminder(self, reminder: str):
        """Создать напоминание"""
        try:
            reminders_file = Path.home() / ".jarvis" / "reminders.json"
            reminders_file.parent.mkdir(parents=True, exist_ok=True)
            
            reminders = []
            if reminders_file.exists():
                reminders = json.loads(reminders_file.read_text(encoding="utf-8"))
            
            reminders.append({
                "text": reminder,
                "created": datetime.now().isoformat(),
                "done": False
            })
            
            reminders_file.write_text(json.dumps(reminders, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log(f"⏰ Напоминание создано: {reminder}")
        except Exception as e:
            logger.error(f"Ошибка создания напоминания: {e}")
    
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
