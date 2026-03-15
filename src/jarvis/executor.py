import json
import subprocess
import logging
import os
import shutil
import time
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

logger = logging.getLogger(__name__)

DEFAULT_SITE_ALIASES = {
    "youtube": "www.youtube.com",
    "you tube": "www.youtube.com",
    "ютуб": "www.youtube.com",
    "ютюб": "www.youtube.com",
    "ютьюб": "www.youtube.com",
    "яндекс": "yandex.ru",
    "гугл": "google.com",
    "google": "google.com",
    "github": "github.com",
    "гитхаб": "github.com",
}


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

    def load_config(self):
        """Перезагружает конфигурацию из файла"""
        self.config = self._load_default_config()
        self._init_ai_assistant()

    def _init_ai_assistant(self):
        ai_config = self.config.get("ai", {}) if isinstance(self.config, dict) else {}
        enabled = ai_config.get("enabled", True)

        keys, keys_created = ensure_keys_file()
        key_from_file = keys.get("openrouter_api_key", "").strip()

        if not enabled:
            self._ai_client = None
            logger.info("AI assistant disabled in config")
            return

        try:
            from .ai_client import OpenRouterClient

            api_key = (
                key_from_file
                or ai_config.get("api_key", "")
                or os.getenv("OPENROUTER_API_KEY", "")
            )

            if keys_created:
                logger.warning("keys.json создан. Добавь ключ OpenRouter в настройках.")
            elif not api_key:
                logger.warning("OPENROUTER_API_KEY отсутствует в keys.json/config/env")

            self._ai_client = OpenRouterClient(
                api_key=api_key,
                model=ai_config.get("model", "openrouter/free"),
                timeout_seconds=int(ai_config.get("timeout_seconds", 20)),
                max_tokens=int(ai_config.get("max_tokens", 220)),
                system_prompt=ai_config.get(
                    "system_prompt",
                    "Ты голосовой ассистент Джарвис. Отвечай кратко и по делу на русском языке.",
                ),
            )
            logger.info("AI assistant initialized (OpenRouter)")
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

        now_ts = time.time()
        if self._last_ai_at and (now_ts - self._last_ai_at) > self._chat_reset_timeout:
            self.reset_chat_history("долгая пауза")

        if self._ai_client and self._ai_client.is_enabled():
            self._log(f"[DEBUG] AI client present, model={getattr(self._ai_client, 'model', '?')}, history={len(self._chat_history)}")
            response = self._ai_client.get_response(query, history=self._chat_history)
            if response:
                self._append_chat_message("user", query)
                self._append_chat_message("assistant", response)
                self._save_chat_history()
                self._last_ai_at = now_ts
                self._log(f"🤖 AI: {response}")
                return True
            if getattr(self._ai_client, "last_error", None):
                self._log(f"⚠ AI недоступен: {self._ai_client.last_error}")
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
        normalized = target.strip().lower()
        site_aliases = DEFAULT_SITE_ALIASES.copy()
        site_aliases.update(self.config.get("sites", {}))

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
            logger.warning(f"Неизвестное приложение: {target}")

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
                self.add_note(slots.get("text", ""))
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
            url = self._resolve_site_target(url)

            # Добавляем https если нет протокола
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            
            webbrowser.open(url)
            logger.info(f"Открываю в браузере: {url}")
        except Exception as e:
            logger.error(f"Ошибка навигации в браузере: {e}")
    
    def browser_search(self, query: str):
        """Поиск в Google"""
        try:
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            webbrowser.open(search_url)
            logger.info(f"Ищу в Google: {query}")
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
    
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
