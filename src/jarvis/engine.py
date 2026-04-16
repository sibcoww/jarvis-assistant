import threading
import logging
import time
import json
import os
import inspect
import re
import subprocess
import random
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .nlu import SimpleNLU, collapse_repeated_stt_words
from .executor import Executor
from .key_store import ensure_keys_file

logger = logging.getLogger(__name__)

try:
    import pyttsx3
except Exception:  # noqa: B902
    pyttsx3 = None

# Пресеты озвучки (pyttsx3: rate ~100–200+, volume 0–1)
TTS_PRESETS = {
    "quiet": {"tts_rate": 158, "tts_volume": 0.55},
    "normal": {"tts_rate": 182, "tts_volume": 0.95},
    "clear": {"tts_rate": 202, "tts_volume": 1.0},
}


class JarvisEngine:
    def __init__(self, asr=None, log=None, continuous_mode_timeout: float = 10.0):
        self.asr = asr
        self._log_sink = log or (lambda msg: None)
        self.log = self._emit_log
        self.continuous_mode_timeout = continuous_mode_timeout  # Время ожидания след. команды без wake-word (сек)
        self.min_intent_confidence = 0.65
        self._app_started_wall = datetime.now()
        self._app_started_monotonic = time.perf_counter()
        self._asr_loading_started_monotonic = None
        self._startup_timing_log_path = Path.home() / ".jarvis" / "startup_timing.log"
        self.audio_config = self._load_audio_config()
        self._assistant_speaking = threading.Event()
        self._speech_lock = threading.Lock()
        self._listen_resume_at = 0.0
        self._tts_engine = None
        self._tts_unavailable_logged = False
        self._tts_enabled = bool(self.audio_config.get("tts_enabled", True))
        self._on_asr_ready_callback: Optional[Callable[[], None]] = None
        # Озвучка для локальных команд (как для диплома)
        # По ТЗ: при любой успешно выполненной локальной команде — одна фраза из списка.
        self._sir_done_variants = (
            "Есть, сэр.",
            "Выполняю, сэр.",
            "Да, сэр.",
            "Сделал, сэр.",
            "Как вы и просили, сэр.",
        )

        self._record_startup_timing("app_start")
        self.log(f"⏱ Запуск приложения: {self._app_started_wall.strftime('%H:%M:%S')}")
        
        self.nlu = SimpleNLU()
        self.nlu_type = "Simple"
        
        self.ex = Executor(log_callback=self.log)
        self._stop = threading.Event()
        self._stop_reason: str | None = None
        self._thread: Optional[threading.Thread] = None
        self._reminder_thread: Optional[threading.Thread] = None

        self.armed = False  # ждём ли команду после wake-word
        self.continuous_mode = False  # ждём ли команду в continuous режиме
        self.continuous_mode_until = 0.0  # timestamp когда выключить continuous режим
        self._asr_lock = threading.Lock()
        self.is_loading = False
        self.is_ready = False
        self.is_running = False
        self.device = None  # индекс микрофона sounddevice

        self.wakeword_engine = "vosk_text"
        self._wake_detector = None
        self._wake_event = threading.Event()
        self._wake_detected_at: float | None = None
        self._pending_command_since: float | None = None
        keys, _ = ensure_keys_file()
        self._porcupine_access_key = (keys.get("picovoice_access_key", "") or os.getenv("PICOVOICE_ACCESS_KEY"))
        
        # Push-to-talk
        self._hotkey_manager = None
        self._ptt_active = False
        self._ptt_pressed = False  # Флаг для предотвращения повторных срабатываний
        self._init_tts()

    @staticmethod
    def _strip_emoji(text: str) -> str:
        s = str(text or "")
        # Убираем emoji/symbols, чтобы ничего не "ломалось" в озвучке и UI.
        out = []
        for ch in s:
            cat = unicodedata.category(ch)
            if cat in {"So", "Cs"}:
                continue
            out.append(ch)
        return "".join(out)

    def _speak_local_done(self):
        self._speak_async(random.choice(self._sir_done_variants))

    def _emit_log(self, msg: str):
        clean = self._strip_emoji(msg)
        self._log_sink(clean)
        # Не озвучиваем логи, если движок не запущен (важно для тестов/инициализации UI).
        if not getattr(self, "is_running", False) and not getattr(self, "is_loading", False):
            return
        # Озвучка (минимально и стабильно):
        # - AI: ... -> озвучиваем текст AI
        # - короткие системные ответы ("Готово.") -> озвучиваем
        # - вопросы (заканчиваются на "?") -> озвучиваем, чтобы пользователь слышал уточнение
        s = clean.strip()
        low = s.lower()
        if low.startswith("ai:"):
            self._speak_async(s.split(":", 1)[1].strip() if ":" in s else s)
            return
        if s.endswith("?") or low in {"готово.", "готово"}:
            self._speak_async(s)
            return

    def set_asr_ready_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Вызывается после успешной загрузки Vosk (без озвучки — для трея / UI)."""
        self._on_asr_ready_callback = callback

    def speak_if_logged_phrase(self, msg: str) -> None:
        """Для строк из GUI: говорим только если это AI-текст."""
        if not self._tts_enabled:
            return
        clean = self._strip_emoji(msg)
        if clean.strip().lower().startswith("ai:"):
            self._speak_async(clean.split(":", 1)[1].strip() if ":" in clean else clean)

    @staticmethod
    def _clean_for_tts(text: str, max_len: int = 380) -> str:
        s = (text or "").strip()
        if not s:
            return ""
        s = re.sub(r"https?://\S+", "ссылка", s, flags=re.IGNORECASE)
        s = re.sub(r"www\.\S+", "ссылка", s, flags=re.IGNORECASE)
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        s = re.sub(r"`+", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) > max_len:
            s = s[: max_len - 1].rstrip() + "…"
        return s

    @classmethod
    def _extract_tts_text(cls, msg: str) -> str | None:
        raw = (msg or "").strip()
        # VS15/VS16 ломают startswith("✅ ") / ("🤖 AI:") в логах Qt/Windows
        text = raw.replace("\uFE0F", "").replace("\uFE0E", "")
        if not text:
            return None
        low = text.lower()
        if "[debug]" in low or "[pipe]" in low:
            return None
        if "модель загружена" in low and "микрофон готов" in low:
            return None
        if text.startswith("⏱") and "слушаю следующую команду" in low:
            return "Слушаю."
        # Технические замеры/прогресс — не озвучиваем.
        if low.startswith(("📊", "📥", "🔊 wake-word")):
            return None
        if text.startswith("⏱"):
            # Оставляем озвучку для пользовательских сообщений про таймер,
            # но убираем метрики и внутренние тайминги.
            if "таймер" not in low:
                return None
            if re.match(r"^⏱\s*(от |время |запуск|команда )", low):
                return None
        if "загрузка:" in low and "%" in text:
            return None

        if text.startswith("✅ ") and "активирован" in low and "команду" in low:
            return "Слушаю."
        if text.startswith("🎙") and "слушаю команду" in low:
            return "Слушаю."

        # Иногда в логе может быть "AI:" без эмодзи.
        if low.startswith("ai:"):
            return cls._clean_for_tts(text.split(":", 1)[1].strip() if ":" in text else text)

        if text.startswith("🤖 AI:"):
            return cls._clean_for_tts(text.replace("🤖 AI:", "", 1).strip())
        if text.startswith("🤖 Нужна цифра") or text.startswith("🤖 Ок, отменил"):
            return cls._clean_for_tts(text.replace("🤖", "", 1).strip())

        speak_prefixes = (
            "⚠ ",
            "✅ ",
            "❌ ",
            "💡 ",
            "🔴 ",
            "🟡 ",
            "🗣 ",
            "🤖 ",
            "🌐 ",
            "🛑 ",
            "🔉 ",
            "🔊 ",
            "⏰ ",
            "⏱ ",
            "📌 ",
            "🎞 ",
            "🪟 ",
            "📜 ",
            "📅 ",
            "🕐 ",
            "🎧 ",
        )
        for prefix in speak_prefixes:
            if text.startswith(prefix):
                return cls._clean_for_tts(text[len(prefix) :].strip())
        return None

    def _init_tts(self):
        if not self._tts_enabled or pyttsx3 is None:
            return
        try:
            self._tts_engine = pyttsx3.init()
            self._tts_engine.setProperty("rate", int(self.audio_config.get("tts_rate", 182)))
            self._tts_engine.setProperty("volume", float(self.audio_config.get("tts_volume", 0.95)))
        except Exception as error:
            self._tts_engine = None
            if not self._tts_unavailable_logged:
                self._log_sink(f"⚠ Озвучка недоступна: {error}")
                self._tts_unavailable_logged = True

    def _speak_async(self, text: str):
        if not self._tts_enabled or not text.strip():
            return
        if self._tts_engine is None:
            self._init_tts()
            # Важно: на Windows можем говорить через SAPI даже без pyttsx3.
            if self._tts_engine is None and os.name != "nt":
                return
        threading.Thread(target=self._speak_blocking, args=(text,), daemon=True).start()

    def _speak_blocking(self, text: str):
        clean = self._clean_for_tts(re.sub(r"\s+", " ", text).strip())
        if not clean:
            return
        with self._speech_lock:
            com_inited = False
            try:
                self._assistant_speaking.set()
                if os.name == "nt":
                    try:
                        import pythoncom

                        pythoncom.CoInitialize()
                        com_inited = True
                    except Exception:
                        pass
                # На Windows SAPI работает стабильнее pyttsx3 (особенно при многократных вызовах).
                if os.name == "nt":
                    try:
                        self._speak_with_windows_sapi(clean)
                    except Exception:
                        # fallback на pyttsx3 если SAPI внезапно не сработал
                        if self._tts_engine is not None:
                            self._tts_engine.say(clean)
                            self._tts_engine.runAndWait()
                else:
                    if self._tts_engine is not None:
                        self._tts_engine.say(clean)
                        self._tts_engine.runAndWait()
            except Exception as error:
                self._log_sink(f"⚠ Озвучка не сработала, текст в логе. Причина: {error}")
                logger.warning("TTS failed: %s", error)
            finally:
                if com_inited:
                    try:
                        import pythoncom

                        pythoncom.CoUninitialize()
                    except Exception:
                        pass
                delay = float(self.audio_config.get("post_tts_mic_delay", 0.45))
                self._listen_resume_at = time.time() + max(0.0, delay)
                self._assistant_speaking.clear()

    def _speak_with_windows_sapi(self, text: str):
        escaped = text.replace("'", "''")
        # SAPI Rate: -10..10, Volume: 0..100
        try:
            tts_rate = int(self.audio_config.get("tts_rate", 182))
        except Exception:
            tts_rate = 182
        try:
            tts_volume = float(self.audio_config.get("tts_volume", 0.95))
        except Exception:
            tts_volume = 0.95

        # Маппинг "pyttsx3-like rate" -> SAPI rate
        sapi_rate = int(round((tts_rate - 182) / 10.0))
        sapi_rate = max(-10, min(10, sapi_rate))
        sapi_volume = int(round(max(0.0, min(1.0, tts_volume)) * 100))

        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$speak.Rate = {sapi_rate}; "
            f"$speak.Volume = {sapi_volume}; "
            f"$speak.Speak('{escaped}');"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
        )

    def _wait_until_not_speaking(self) -> bool:
        while not self._stop.is_set():
            if not self._assistant_speaking.is_set() and time.time() >= self._listen_resume_at:
                return True
            time.sleep(0.05)
        return False

    def enable_push_to_talk(self, hotkey_str: str = "f6") -> bool:
        """Включает режим push-to-talk с указанной клавишей/комбинацией
        
        Args:
            hotkey_str: Название клавиши, кнопки мыши или комбинация через "+"
                       Примеры: "f6", "ctrl+f6", "mouse_x1", "alt+space"
        """
        try:
            from .hotkeys import HotkeyManager
            from pynput import keyboard, mouse
            
            if self._hotkey_manager is None:
                self._hotkey_manager = HotkeyManager()
            
            # Парсим hotkey string
            parts = [p.strip() for p in hotkey_str.split("+")]
            
            # Маппинг строк на объекты pynput
            key_map = {
                "f1": keyboard.Key.f1, "f2": keyboard.Key.f2, "f3": keyboard.Key.f3,
                "f4": keyboard.Key.f4, "f5": keyboard.Key.f5, "f6": keyboard.Key.f6,
                "f7": keyboard.Key.f7, "f8": keyboard.Key.f8, "f9": keyboard.Key.f9,
                "f10": keyboard.Key.f10, "f11": keyboard.Key.f11, "f12": keyboard.Key.f12,
                "space": keyboard.Key.space, "ctrl": keyboard.Key.ctrl,
                "alt": keyboard.Key.alt, "shift": keyboard.Key.shift,
                "caps_lock": keyboard.Key.caps_lock, "tab": keyboard.Key.tab,
                "enter": keyboard.Key.enter, "esc": keyboard.Key.esc,
                "backspace": keyboard.Key.backspace, "delete": keyboard.Key.delete,
                "insert": keyboard.Key.insert, "home": keyboard.Key.home,
                "end": keyboard.Key.end, "page_up": keyboard.Key.page_up,
                "page_down": keyboard.Key.page_down,
                "mouse_left": mouse.Button.left,
                "mouse_right": mouse.Button.right,
                "mouse_middle": mouse.Button.middle,
                "mouse_x1": mouse.Button.x1,
                "mouse_x2": mouse.Button.x2
            }
            
            # Конвертируем в pynput объекты
            hotkey_objects = []
            for part in parts:
                key_obj = key_map.get(part.lower())
                if key_obj:
                    hotkey_objects.append(key_obj)
            
            if not hotkey_objects:
                self.log(f"⚠ Неизвестная комбинация: {hotkey_str}")
                return False
            
            # Если одна клавиша - передаём как объект, если несколько - как tuple
            hotkey = hotkey_objects[0] if len(hotkey_objects) == 1 else tuple(hotkey_objects)
            
            def on_press():
                if not self.is_running or self._ptt_pressed:
                    return
                self._ptt_pressed = True
                self._ptt_active = True
                self.armed = True
                self._pending_command_since = time.perf_counter()
            
            def on_release():
                if not self.is_running or not self._ptt_pressed:
                    return
                self._ptt_pressed = False
                # Флаги сбросятся в _run_porcupine после распознавания
                self.armed = False
                self._pending_command_since = None
            
            success = self._hotkey_manager.register_push_to_talk(
                hotkey=hotkey,
                on_press=on_press,
                on_release=on_release
            )
            return success
        except ImportError:
            self.log("⚠ pynput не установлен. Push-to-talk недоступен.")
            return False
        except Exception as e:
            self.log(f"❌ Ошибка активации push-to-talk: {e}")
            return False
    
    def disable_push_to_talk(self):
        """Отключает режим push-to-talk"""
        if self._hotkey_manager:
            self._hotkey_manager.unregister()
            self._ptt_active = False
            self._ptt_pressed = False


    def _record_startup_timing(self, event: str, **fields):
        payload = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **fields,
        }
        try:
            self._startup_timing_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._startup_timing_log_path.open("a", encoding="utf-8") as timing_log:
                timing_log.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as error:
            logger.warning(f"Не удалось записать startup timing log: {error}")


    def _ensure_asr(self):
        if self.asr is not None:
            self.is_ready = True
            return

        with self._asr_lock:
            if self.asr is not None:
                self.is_ready = True
                return

            self.is_loading = True
            self._asr_loading_started_monotonic = time.perf_counter()
            self._record_startup_timing("asr_load_start")
            self.log("⏳ Загрузка модели распознавания речи...")
            
            # Проверка наличия модели
            model_path = Path("models/vosk-model-ru-0.42")
            if not model_path.exists():
                self.is_loading = False
                self.log("❌ ОШИБКА: Модель не найдена!")
                self.log(f"📂 Ожидаемый путь: {model_path.resolve()}")
                self.log("📥 Скачай модель с https://alphacephei.com/vosk/models")
                self.log("   и распакуй в папку models/")
                return
            
            def on_progress(step, total):
                # Намеренно не логируем проценты: это вводит в заблуждение (часто всего 2 шага).
                _ = step, total
            
            try:
                from .vosk_asr import VoskASR
                self.asr = VoskASR(
                    str(model_path),
                    device=self.device,
                    phrase_timeout=float(self.audio_config.get("phrase_timeout", 6.0)),
                    silence_timeout=float(self.audio_config.get("silence_timeout", 1.2)),
                    on_progress=on_progress,
                )
            except Exception as e:
                self.is_loading = False
                self.log(f"❌ Ошибка загрузки модели: {e}")
                self.log("💡 Проверь, что модель распакована правильно")
                return
            
            self.is_loading = False
            self.is_ready = True
            self.log("✅ Модель загружена, микрофон готов")
            cb = self._on_asr_ready_callback
            if cb:
                try:
                    cb()
                except Exception:
                    logger.exception("on_asr_ready callback failed")

            asr_load_seconds = None
            if self._asr_loading_started_monotonic is not None:
                asr_load_seconds = time.perf_counter() - self._asr_loading_started_monotonic

            since_app_start_seconds = time.perf_counter() - self._app_started_monotonic

            if asr_load_seconds is not None:
                self.log(
                    f"⏱ Время загрузки модели: {asr_load_seconds:.2f}с "
                    f"(с запуска приложения: {since_app_start_seconds:.2f}с)"
                )
                self._record_startup_timing(
                    "asr_ready",
                    asr_load_seconds=round(asr_load_seconds, 3),
                    since_app_start_seconds=round(since_app_start_seconds, 3),
                )
            else:
                self._record_startup_timing(
                    "asr_ready",
                    since_app_start_seconds=round(since_app_start_seconds, 3),
                )

    def set_device(self, device_index: int | None):
        if self.is_running or self.is_loading:
            self.log("⚠ Нельзя менять микрофон во время работы/загрузки.")
            return
        self.device = device_index
        self.asr = None
        self.is_ready = False
        self.log(f"🎤 Выбран микрофон: {device_index}. Нажми Старт (модель загрузится заново).")

    def reload_config(self):
        """Перезагружает конфигурацию из config.json"""
        try:
            self.log("🔄 Перезагрузка конфигурации...")
            self.nlu.load_config()
            self.ex.load_config()
            self.audio_config = self._load_audio_config()
            self._tts_enabled = bool(self.audio_config.get("tts_enabled", True))
            self._tts_engine = None
            self._init_tts()
            self.log("✅ Конфигурация перезагружена")
        except Exception as e:
            self.log(f"❌ Ошибка при перезагрузке конфигурации: {e}")
            raise

    def test_tts_utterance(self):
        """Короткая фраза для кнопки «Тест озвучки» в GUI."""
        self._speak_async("Тест озвучки. Голос работает.")

    def _on_wakeword_detected(self, keyword: str):
        self._wake_detected_at = time.perf_counter()
        self._wake_event.set()
        self.log(f"🔊 Wake-word: {keyword}")

    def set_wakeword_engine(self, engine_name: str) -> bool:
        if self.is_running or self.is_loading:
            self.log("⚠ Нельзя менять движок активации во время работы/загрузки.")
            return False

        normalized = (engine_name or "").strip().lower()
        if normalized in {"vosk", "vosk_text", "text"}:
            self.wakeword_engine = "vosk_text"
            self._wake_detector = None
            self.log("⚙ Движок активации: Vosk (текстовый)")
            return True

        if normalized not in {"porcupine", "picovoice"}:
            self.log("⚠ Неизвестный движок активации. Оставлен текущий.")
            return False

        self._refresh_porcupine_key()
        if not self._porcupine_access_key:
            self.log("⚠ PICOVOICE_ACCESS_KEY не задан. Porcupine недоступен.")
            return False

        try:
            from .wakeword import get_wakeword_detector

            detector = get_wakeword_detector(
                use_porcupine=True,
                access_key=self._porcupine_access_key,
                sensitivity=0.6,
                on_detected=self._on_wakeword_detected,
            )

            if getattr(detector, "detector", None) is None:
                self.log("⚠ Porcupine не инициализирован. Проверь ключ/окружение.")
                return False

            self._wake_detector = detector
            self.wakeword_engine = "porcupine"
            self.log("⚙ Движок активации: Porcupine (Picovoice)")
            return True
        except Exception as error:
            self.log(f"❌ Ошибка инициализации Porcupine: {error}")
            return False

    def _refresh_porcupine_key(self):
        try:
            keys, _ = ensure_keys_file()
            self._porcupine_access_key = (
                keys.get("picovoice_access_key", "") or os.getenv("PICOVOICE_ACCESS_KEY", "")
            )
        except Exception:
            self._porcupine_access_key = os.getenv("PICOVOICE_ACCESS_KEY", "")

    @staticmethod
    def _load_audio_config() -> dict:
        config_path = Path(__file__).with_name("config.json")
        defaults = {
            "phrase_timeout": 6.0,
            "silence_timeout": 1.2,
            "wake_engine": "vosk_text",
            "tts_enabled": True,
            "tts_preset": "normal",
            "tts_rate": TTS_PRESETS["normal"]["tts_rate"],
            "tts_volume": TTS_PRESETS["normal"]["tts_volume"],
            "post_tts_mic_delay": 0.45,
        }
        if not config_path.exists():
            return defaults
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            audio = data.get("audio", {}) if isinstance(data, dict) else {}
            result = defaults.copy()
            preset = str(audio.get("tts_preset", "normal")).strip().lower()
            if preset not in TTS_PRESETS:
                preset = "normal"
            pr = TTS_PRESETS[preset]
            result.update({
                "phrase_timeout": audio.get("phrase_timeout", defaults["phrase_timeout"]),
                "silence_timeout": audio.get("silence_timeout", defaults["silence_timeout"]),
                "wake_engine": audio.get("wake_engine", defaults["wake_engine"]),
                "tts_enabled": audio.get("tts_enabled", defaults["tts_enabled"]),
                "tts_preset": preset,
                "tts_rate": int(audio.get("tts_rate", pr["tts_rate"])),
                "tts_volume": float(audio.get("tts_volume", pr["tts_volume"])),
                "post_tts_mic_delay": float(audio.get("post_tts_mic_delay", defaults["post_tts_mic_delay"])),
            })
            return result
        except Exception:
            return defaults

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        # Allow restart after stop(): stale is_running may still be True
        # for a short period until previous loop fully unwinds.
        if self.is_running and not self._stop.is_set():
            return

        self._stop.clear()
        self._stop_reason = None
        self.armed = False
        self.continuous_mode = False
        self._pending_command_since = None
        self._wake_event.clear()
        self._thread = threading.Thread(target=self._bootstrap_and_run, daemon=True)
        self._thread.start()
        self._reminder_thread = threading.Thread(target=self._run_reminder_loop, daemon=True)
        self._reminder_thread.start()


    def _bootstrap_and_run(self):
        try:
            self._ensure_asr()

            if self.wakeword_engine == "porcupine" and self._wake_detector:
                self._wake_event.clear()
                self._wake_detector.start_listening()
                self.log("🎧 Porcupine слушает wake-word в фоне")

            self.is_running = True
            self.log("🟢 Движок запущен. Скажи «Джарвис».")
            self._run()

        except Exception as e:
            self.log(f"❌ Ошибка запуска движка: {e}")
        finally:
            if self._wake_detector:
                self._wake_detector.stop_listening()
            self.is_running = False

    def _run_reminder_loop(self):
        """Фоновая проверка таймеров напоминаний."""
        while not self._stop.is_set():
            try:
                due_items = self.ex.pop_due_reminders()
                for text in due_items:
                    message = f"⏰ Напоминание: {text}"
                    self.log(message)
                    self._speak_async(f"Напоминание. {text}")
                due_timers = self.ex.pop_due_timers() if hasattr(self.ex, "pop_due_timers") else []
                for label in due_timers:
                    if label:
                        message = f"⏱ Таймер: время вышло. {label}"
                        voice = f"Таймер завершен. {label}"
                    else:
                        message = "⏱ Таймер: время вышло."
                        voice = "Таймер завершен."
                    self.log(message)
                    self._speak_async(voice)
            except Exception as error:
                logger.debug("Reminder poll failed: %s", error)
            time.sleep(1.0)


    def _caller_label(self) -> str:
        try:
            frames = inspect.stack()
            for frame_info in frames[2:6]:  # пропускаем текущий и прямого вызователя
                module = inspect.getmodule(frame_info.frame)
                mod_name = module.__name__ if module else "?"
                return f"{mod_name}.{frame_info.function}"
        except Exception:
            return "unknown"
        return "unknown"

    def stop(self, reason: str | None = None):
        """Идемпотентная остановка движка с защитой от повторных вызовов."""
        caller = self._caller_label()
        if self._stop.is_set():
            logger.debug("stop ignored: already stopping (reason=%s) caller=%s", self._stop_reason, caller)
            return
        if not self.is_running and not self.is_loading:
            logger.debug("stop ignored: engine not running/loading caller=%s", caller)
            return

        stop_reason = reason or self._stop_reason or "unspecified"
        first_stop = self._stop_reason is None
        self._stop_reason = self._stop_reason or stop_reason

        self._stop.set()
        self.armed = False
        self.continuous_mode = False

        if first_stop:
            logger.debug("stop accepted by %s reason=%s", caller, stop_reason)
        else:
            logger.debug(
                "stop accepted (already had reason=%s) new_call=%s", self._stop_reason, caller
            )
        if reason:
            self.log(f"🔴 Движок остановлен ({reason}).")
        else:
            self.log("🔴 Движок остановлен.")

    def reset_chat_history(self, reason: str = "manual") -> bool:
        try:
            if hasattr(self.ex, "reset_chat_history"):
                self.ex.reset_chat_history(reason)
            self.log("🧹 Контекст очищен")
            return True
        except Exception as error:
            self.log(f"❌ Не удалось очистить контекст: {error}")
        return False

        
    def preload(self):
        try:
            self._ensure_asr()
        except Exception as e:
            self.is_loading = False
            self.log(f"❌ Ошибка загрузки модели: {e}")


    def _listen_once_timed(self, stage_label: str) -> str | None:
        if not self._wait_until_not_speaking():
            return None
        listen_started_at = time.perf_counter()
        text = self.asr.listen_once()
        listen_elapsed = time.perf_counter() - listen_started_at
        if text:
            self.log(f"⏱ {stage_label}: фраза за {listen_elapsed:.2f}с")
        return text

    def _execute_intent_if_valid(self, source_text: str):
        raw_text = collapse_repeated_stt_words((source_text or "").strip())
        def _pipe(intent_name: str, result: str):
            self.log(f"[PIPE] q='{raw_text}' intent='{intent_name}' action='{intent_name}' result='{result}'")

        # Шаг подтверждения рискованных действий.
        try:
            handled, confirmed_intent, confirm_state = self.ex.pending_confirmation_from_text(raw_text)
        except Exception:
            handled, confirmed_intent, confirm_state = (False, None, None)
        if handled:
            if confirm_state == "cancel":
                self.log("🛑 Действие отменено.")
                _pipe("confirm", "cancelled")
                return True
            if confirmed_intent:
                try:
                    self.ex.run(confirmed_intent)
                    self._speak_local_done()
                    self.log("Подтвержденное действие выполнено.")
                    _pipe(str(confirmed_intent.get("type") or "unknown"), "ok_confirmed")
                    return True
                except Exception as error:
                    self.log(f"❌ Ошибка выполнения подтвержденного действия: {error}")
                    logger.exception("Confirmed executor run failed")
                    _pipe(str(confirmed_intent.get("type") or "unknown"), "error")
                    return False

        try:
            intent = self.nlu.parse(raw_text)
        except Exception as error:
            self.log(f"❌ Ошибка распознавания интента: {error}")
            logger.exception("Intent parse failed")
            return False

        if intent.get("type") == "unknown":
            try:
                handled = self.ex.handle_unrecognized_command(raw_text)
                _pipe("unknown", "ok" if handled else "fallback")
                return handled
            except Exception as error:
                self.log(f"❌ Ошибка AI fallback: {error}")
                logger.exception("AI fallback failed")
                _pipe("unknown", "error")
                return False

        confidence = intent.get("confidence")
        if confidence is not None and confidence < self.min_intent_confidence:
            self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
            return False

        self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
        if hasattr(self.ex, "should_require_confirmation") and self.ex.should_require_confirmation(intent):
            prompt = self.ex.queue_confirmation(intent)
            self.log(f"⚠ {prompt}")
            _pipe(str(intent.get("type") or "unknown"), "pending_confirmation")
            return True
        try:
            self.ex.run(intent)
        except Exception as error:
            self.log(f"❌ Ошибка выполнения команды: {error}")
            logger.exception("Executor run failed")
            _pipe(str(intent.get("type") or "unknown"), "error")
            return False
        self._speak_local_done()
        self.log("Готово.")
        _pipe(str(intent.get("type") or "unknown"), "ok")
        return True

    def _expire_continuous_if_needed(self, now: float | None = None) -> bool:
        """Проверяет таймер continuous режима и выключает его без стопа движка."""
        if not self.continuous_mode:
            return False
        now_ts = now if now is not None else time.time()
        if now_ts > self.continuous_mode_until:
            self.continuous_mode = False
            self.log("⏰ Режим continuous истёк. Скажи «Джарвис» для активации.")
            return True
        return False

    def _enter_continuous_mode_after_speech(self):
        """
        Важно: таймер continuous (10с) должен стартовать ПОСЛЕ озвучки,
        иначе пока ассистент говорит — время "сгорает", хотя микрофон заблокирован.
        """
        if not self._wait_until_not_speaking():
            return
        self.continuous_mode = True
        self.continuous_mode_until = time.time() + self.continuous_mode_timeout
        self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")

    def _run_porcupine(self):
        while not self._stop.is_set():
            # Проверка PTT активности
            if self._ptt_active and self.armed:
                # PTT нажат, даём небольшую задержку перед началом записи
                time.sleep(0.1)
                self.log("🎙 PTT: слушаю команду...")
                
                # Записываем пока клавиша зажата или пока не истечёт timeout
                text = self._listen_once_timed("PTT команда")
                
                if self._stop.is_set():
                    break
                
                # Сбрасываем флаги
                self.armed = False
                self._ptt_active = False
                self._ptt_pressed = False
                
                if text:
                    self.log(f"🎙 Распознано: {text}")
                    text_clean = self.nlu._strip_wake_word(text.lower()) if hasattr(self.nlu, '_strip_wake_word') else text
                    if text_clean != text:
                        self.log(f"🧹 Очищено: {text_clean}")
                    text = text_clean
                else:
                    pass

                executed = self._execute_intent_if_valid(text or "") if text else False
                if executed:
                    self._enter_continuous_mode_after_speech()
                
                self.armed = False
                continue
            
            # Обычный режим wake-word
            if not self._wake_event.wait(timeout=0.1):
                continue

            self._wake_event.clear()
            wake_detected_at = self._wake_detected_at or time.perf_counter()

            if self._wake_detector:
                self._wake_detector.stop_listening()

            command_listen_started = time.perf_counter()
            self.log(
                f"⏱ От детекта wake-word до старта прослушивания: "
                f"{(command_listen_started - wake_detected_at) * 1000:.0f}мс"
            )
            # В режиме Porcupine поднимаем armed, чтобы GUI показал "жду команду" (зелёный).
            self.armed = True
            self.log("✅ Активирован. Скажи команду…")

            text = self._listen_once_timed("Команда после wake-word")
            self.armed = False
            if self._stop.is_set():
                break
            if text:
                self.log(f"🎙 Распознано: {text}")
                # Убираем wake-word из команды если он попал в распознавание
                text_clean = self.nlu._strip_wake_word(text.lower()) if hasattr(self.nlu, '_strip_wake_word') else text
                if text_clean != text:
                    self.log(f"🧹 Очищено: {text_clean}")
                text = text_clean
            else:
                pass

            executed = self._execute_intent_if_valid(text or "") if text else False
            if executed:
                self._enter_continuous_mode_after_speech()

            while self.continuous_mode and not self._stop.is_set():
                if self._expire_continuous_if_needed(now=time.time()):
                    break

                next_text = self._listen_once_timed("Команда в continuous")
                if self._stop.is_set():
                    break
                if not next_text:
                    continue

                self.log(f"🎙 Распознано: {next_text}")
                # Убираем wake-word если попал в continuous режиме
                next_text_clean = self.nlu._strip_wake_word(next_text.lower()) if hasattr(self.nlu, '_strip_wake_word') else next_text
                if next_text_clean != next_text:
                    self.log(f"🧹 Очищено: {next_text_clean}")
                next_text = next_text_clean
                
                if self._execute_intent_if_valid(next_text):
                    # таймер должен начинаться после озвучки результата команды
                    self._enter_continuous_mode_after_speech()

            if self._wake_detector and not self._stop.is_set():
                self._wake_detector.start_listening()



    def _has_wake_word(self, text: str) -> bool:
        """Check if text contains wake word."""
        wake_words = {"джарвис", "жарвис", "джервис", "джанверт", "джанвис", "джаврис"}
        words = text.lower().split()
        return any(w in wake_words for w in words)

    def _run(self):
        if self.wakeword_engine == "porcupine" and self._wake_detector:
            self._run_porcupine()
            return

        while not self._stop.is_set():
            if self.armed and self._pending_command_since is not None:
                delay_ms = (time.perf_counter() - self._pending_command_since) * 1000
                self.log(f"⏱ От активации до старта прослушивания команды: {delay_ms:.0f}мс")
                self._pending_command_since = None

            stage = "Команда в continuous" if self.continuous_mode else ("Команда после wake-word" if self.armed else "Ожидание wake-word")
            text = self._listen_once_timed(stage)
            if self._stop.is_set():
                break
            if not text:
                if stage != "Ожидание wake-word":
                    pass
                continue

            t = text.strip().lower()
            self.log(f"🎙 Распознано: {text}")

            if t in ("exit", "quit", "выход"):
                self.log("🟡 Команда выхода.")
                self.stop("exit command")
                break

            # Check if continuous mode timed out
            self._expire_continuous_if_needed(now=time.time())

            # Check if text contains both wake word and command
            if self._has_wake_word(t):
                # Wake word detected - parse command
                try:
                    if hasattr(self.nlu, 'parse_with_wake_word'):
                        intent = self.nlu.parse_with_wake_word(t)
                    else:
                        # Fallback: parse after manual wake word removal
                        intent = self.nlu.parse(t)
                except Exception as error:
                    self.log(f"❌ Ошибка распознавания интента: {error}")
                    logger.exception("Intent parse failed (wakeword)")
                    continue
                
                if intent.get("type") != "unknown":
                    confidence = intent.get("confidence")
                    if confidence is not None and confidence < self.min_intent_confidence:
                        self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
                        continue

                    # Got valid intent in same sentence as wake word
                    self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
                    try:
                        self._speak_known_command_ack(intent.get("type", ""))
                        self.ex.run(intent)
                    except Exception as error:
                        self.log(f"❌ Ошибка выполнения команды: {error}")
                        logger.exception("Executor run failed (wakeword)")
                        continue
                    self.log("✅ Готово.")
                    
                    # Enter continuous mode - wait for next command without wake word
                    self._enter_continuous_mode_after_speech()
                    continue
                else:
                    # Wake word found but no known command after it - пробуем AI сразу
                    self.log("[DEBUG] Wake+unknown -> AI")
                    try:
                        handled = self.ex.handle_unrecognized_command(t)
                    except Exception as error:
                        self.log(f"❌ Ошибка AI fallback: {error}")
                        logger.exception("AI fallback failed (wake+unknown)")
                        handled = False
                    if handled:
                        self._enter_continuous_mode_after_speech()
                    else:
                        self.armed = True
                        self._pending_command_since = time.perf_counter()
                        self.log("✅ Активирован. Скажи команду…")
                    continue
            
            # No wake word detected
            
            # Check if we're in continuous mode
            if self.continuous_mode:
                try:
                    intent = self.nlu.parse(text)
                except Exception as error:
                    self.log(f"❌ Ошибка распознавания интента: {error}")
                    logger.exception("Intent parse failed (continuous)")
                    continue
                if intent.get("type") != "unknown":
                    confidence = intent.get("confidence")
                    if confidence is not None and confidence < self.min_intent_confidence:
                        self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
                        continue

                    self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
                    try:
                        self._speak_known_command_ack(intent.get("type", ""))
                        self.ex.run(intent)
                    except Exception as error:
                        self.log(f"❌ Ошибка выполнения команды: {error}")
                        logger.exception("Executor run failed (continuous)")
                        continue
                    self.log("✅ Готово.")
                    
                    # Reset continuous mode timer
                    self._enter_continuous_mode_after_speech()
                    continue
                else:
                    self.log("[DEBUG] continuous unknown -> AI")
                    try:
                        self.ex.handle_unrecognized_command(text)
                    except Exception as error:
                        self.log(f"❌ Ошибка AI fallback: {error}")
                        logger.exception("AI fallback failed (continuous)")
                    continue
            
            # Check if armed (regular two-step activation)
            if self.armed:
                try:
                    intent = self.nlu.parse(text)
                except Exception as error:
                    self.log(f"❌ Ошибка распознавания интента: {error}")
                    logger.exception("Intent parse failed (armed)")
                    continue
                if intent.get("type") == "unknown":
                    self.log("[DEBUG] armed unknown -> AI")
                    try:
                        self.ex.handle_unrecognized_command(text)
                    except Exception as error:
                        self.log(f"❌ Ошибка AI fallback: {error}")
                        logger.exception("AI fallback failed (armed)")
                    continue

                confidence = intent.get("confidence")
                if confidence is not None and confidence < self.min_intent_confidence:
                    self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
                    continue

                self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
                try:
                    self._speak_known_command_ack(intent.get("type", ""))
                    self.ex.run(intent)
                except Exception as error:
                    self.log(f"❌ Ошибка выполнения команды: {error}")
                    logger.exception("Executor run failed (armed)")
                    continue
                self.log("✅ Готово.")

                # Enter continuous mode after command execution
                self.armed = False
                self._enter_continuous_mode_after_speech()
                continue

            # Not armed and no wake word - just log and continue
            self.log("🟢 Скажи «Джарвис» для активации.")
