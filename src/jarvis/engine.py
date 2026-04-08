import threading
import logging
import time
import json
import os
import inspect
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .nlu import SimpleNLU
from .executor import Executor
from .key_store import ensure_keys_file

logger = logging.getLogger(__name__)

class JarvisEngine:
    def __init__(self, asr=None, log=None, continuous_mode_timeout: float = 10.0):
        self.asr = asr
        self.log = log or (lambda msg: None)
        self.continuous_mode_timeout = continuous_mode_timeout  # Время ожидания след. команды без wake-word (сек)
        self.min_intent_confidence = 0.65
        self._app_started_wall = datetime.now()
        self._app_started_monotonic = time.perf_counter()
        self._asr_loading_started_monotonic = None
        self._startup_timing_log_path = Path.home() / ".jarvis" / "startup_timing.log"
        self.audio_config = self._load_audio_config()

        self._record_startup_timing("app_start")
        self.log(f"⏱ Запуск приложения: {self._app_started_wall.strftime('%H:%M:%S')}")
        
        self.nlu = SimpleNLU()
        self.nlu_type = "Simple"
        
        self.ex = Executor(log_callback=self.log)
        self._stop = threading.Event()
        self._stop_reason: str | None = None
        self._thread: Optional[threading.Thread] = None

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
                percent = int((step / total) * 100)
                self.log(f"📊 Загрузка: {percent}% ({step}/{total})")
            
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
            self.log("✅ Конфигурация перезагружена")
        except Exception as e:
            self.log(f"❌ Ошибка при перезагрузке конфигурации: {e}")
            raise


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
        defaults = {"phrase_timeout": 6.0, "silence_timeout": 1.2, "wake_engine": "vosk_text"}
        if not config_path.exists():
            return defaults
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            audio = data.get("audio", {}) if isinstance(data, dict) else {}
            result = defaults.copy()
            result.update({
                "phrase_timeout": audio.get("phrase_timeout", defaults["phrase_timeout"]),
                "silence_timeout": audio.get("silence_timeout", defaults["silence_timeout"]),
                "wake_engine": audio.get("wake_engine", defaults["wake_engine"]),
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
        listen_started_at = time.perf_counter()
        text = self.asr.listen_once()
        listen_elapsed = time.perf_counter() - listen_started_at
        if text:
            self.log(f"⏱ {stage_label}: фраза за {listen_elapsed:.2f}с")
        return text

    def _execute_intent_if_valid(self, source_text: str):
        raw_text = (source_text or "").strip()

        # Шаг подтверждения рискованных действий.
        try:
            handled, confirmed_intent, confirm_state = self.ex.pending_confirmation_from_text(raw_text)
        except Exception:
            handled, confirmed_intent, confirm_state = (False, None, None)
        if handled:
            if confirm_state == "cancel":
                self.log("🛑 Действие отменено.")
                self.log(f"[PIPE] text='{raw_text}' -> intent='confirm' -> result='cancelled'")
                return True
            if confirmed_intent:
                try:
                    self.ex.run(confirmed_intent)
                    self.log("✅ Подтвержденное действие выполнено.")
                    self.log(
                        f"[PIPE] text='{raw_text}' -> intent='{confirmed_intent.get('type')}' -> result='ok' reason='confirmed'"
                    )
                    return True
                except Exception as error:
                    self.log(f"❌ Ошибка выполнения подтвержденного действия: {error}")
                    logger.exception("Confirmed executor run failed")
                    self.log(
                        f"[PIPE] text='{raw_text}' -> intent='{confirmed_intent.get('type')}' -> result='error'"
                    )
                    return False

        try:
            intent = self.nlu.parse(source_text)
        except Exception as error:
            self.log(f"❌ Ошибка распознавания интента: {error}")
            logger.exception("Intent parse failed")
            return False

        if intent.get("type") == "unknown":
            try:
                handled = self.ex.handle_unrecognized_command(source_text)
                self.log(
                    f"[PIPE] text='{raw_text}' -> intent='unknown' -> result='{'ok' if handled else 'fallback'}'"
                )
                return handled
            except Exception as error:
                self.log(f"❌ Ошибка AI fallback: {error}")
                logger.exception("AI fallback failed")
                self.log(f"[PIPE] text='{raw_text}' -> intent='unknown' -> result='error'")
                return False

        confidence = intent.get("confidence")
        if confidence is not None and confidence < self.min_intent_confidence:
            self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
            return False

        self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
        if hasattr(self.ex, "should_require_confirmation") and self.ex.should_require_confirmation(intent):
            prompt = self.ex.queue_confirmation(intent)
            self.log(f"⚠ {prompt}")
            self.log(
                f"[PIPE] text='{raw_text}' -> intent='{intent.get('type')}' -> result='pending_confirmation'"
            )
            return True
        try:
            self.ex.run(intent)
        except Exception as error:
            self.log(f"❌ Ошибка выполнения команды: {error}")
            logger.exception("Executor run failed")
            self.log(f"[PIPE] text='{raw_text}' -> intent='{intent.get('type')}' -> result='error'")
            return False
        self.log("✅ Готово.")
        self.log(f"[PIPE] text='{raw_text}' -> intent='{intent.get('type')}' -> result='ok'")
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
                
                executed = self._execute_intent_if_valid(text or "") if text else False
                if executed:
                    self.continuous_mode = True
                    self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                    self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")
                
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
            self.log("✅ Активирован. Скажи команду…")

            text = self._listen_once_timed("Команда после wake-word")
            if self._stop.is_set():
                break
            if text:
                self.log(f"🎙 Распознано: {text}")
                # Убираем wake-word из команды если он попал в распознавание
                text_clean = self.nlu._strip_wake_word(text.lower()) if hasattr(self.nlu, '_strip_wake_word') else text
                if text_clean != text:
                    self.log(f"🧹 Очищено: {text_clean}")
                text = text_clean

            executed = self._execute_intent_if_valid(text or "") if text else False
            if executed:
                self.continuous_mode = True
                self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")

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
                    self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                    self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")

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
                        self.ex.run(intent)
                    except Exception as error:
                        self.log(f"❌ Ошибка выполнения команды: {error}")
                        logger.exception("Executor run failed (wakeword)")
                        continue
                    self.log("✅ Готово.")
                    
                    # Enter continuous mode - wait for next command without wake word
                    self.continuous_mode = True
                    self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                    self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")
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
                        self.continuous_mode = True
                        self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                        self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")
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
                        self.ex.run(intent)
                    except Exception as error:
                        self.log(f"❌ Ошибка выполнения команды: {error}")
                        logger.exception("Executor run failed (continuous)")
                        continue
                    self.log("✅ Готово.")
                    
                    # Reset continuous mode timer
                    self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                    self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")
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
                    self.ex.run(intent)
                except Exception as error:
                    self.log(f"❌ Ошибка выполнения команды: {error}")
                    logger.exception("Executor run failed (armed)")
                    continue
                self.log("✅ Готово.")

                # Enter continuous mode after command execution
                self.continuous_mode = True
                self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                self.armed = False
                self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")
                continue

            # Not armed and no wake word - just log and continue
            self.log("🟢 Скажи «Джарвис» для активации.")
