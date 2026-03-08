import threading
import logging
import time
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .nlu import SimpleNLU
from .ml_nlu import MLNLU
from .executor import Executor
from PySide6.QtCore import QTimer

logger = logging.getLogger(__name__)

class JarvisEngine:
    def __init__(self, asr=None, log=None, use_ml_nlu: bool = True, continuous_mode_timeout: float = 10.0):
        self.asr = asr
        self.log = log or (lambda msg: None)
        self.continuous_mode_timeout = continuous_mode_timeout  # Время ожидания след. команды без wake-word (сек)
        self.min_intent_confidence = 0.55
        self._app_started_wall = datetime.now()
        self._app_started_monotonic = time.perf_counter()
        self._asr_loading_started_monotonic = None
        self._startup_timing_log_path = Path.home() / ".jarvis" / "startup_timing.log"

        self._record_startup_timing("app_start")
        self.log(f"⏱ Запуск приложения: {self._app_started_wall.strftime('%H:%M:%S')}")
        
        # Initialize NLU: try ML first, fallback to SimpleNLU
        if use_ml_nlu:
            try:
                self.log("🤖 Loading ML-based NLU...")
                self.nlu = MLNLU()
                self.nlu_type = "ML"
                self.log("✅ ML NLU loaded")
            except Exception as e:
                logger.warning(f"ML NLU initialization failed: {e}. Falling back to SimpleNLU.")
                self.nlu = SimpleNLU()
                self.nlu_type = "Simple"
        else:
            self.nlu = SimpleNLU()
            self.nlu_type = "Simple"
        
        self.ex = Executor(enable_tts=False, log_callback=self.log)  # Передаём callback в Executor
        self._stop = threading.Event()
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
        self._porcupine_access_key = os.getenv("PICOVOICE_ACCESS_KEY")

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
            
            def on_progress(step, total):
                percent = int((step / total) * 100)
                self.log(f"📊 Загрузка: {percent}% ({step}/{total})")
            
            from .vosk_asr import VoskASR
            self.asr = VoskASR("models/vosk-model-ru-0.42", device=self.device, on_progress=on_progress)
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

    def start(self):
        if self.is_running:
            return
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
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


    def stop(self):
        if not self.is_running and not self.is_loading:
            return
        self._stop.set()
        self.armed = False
        self.continuous_mode = False
        self.log("🔴 Движок остановлен.")

        
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
        intent = self.nlu.parse(source_text)
        if intent.get("type") == "unknown":
            self.log("❓ Не понял команду. Повтори.")
            return False

        if intent.get("confidence", 0.0) < self.min_intent_confidence:
            self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
            return False

        self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
        self.ex.run(intent)
        self.log("✅ Готово.")
        return True

    def _run_porcupine(self):
        while not self._stop.is_set():
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

            executed = self._execute_intent_if_valid(text or "") if text else False
            if executed:
                self.continuous_mode = True
                self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")

            while self.continuous_mode and not self._stop.is_set():
                if time.time() > self.continuous_mode_until:
                    self.continuous_mode = False
                    self.log("⏰ Режим continuous истёк. Скажи «Джарвис» для активации.")
                    break

                next_text = self._listen_once_timed("Команда в continuous")
                if self._stop.is_set():
                    break
                if not next_text:
                    continue

                self.log(f"🎙 Распознано: {next_text}")
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
                self.stop()
                break

            # Check if continuous mode timed out
            if self.continuous_mode and time.time() > self.continuous_mode_until:
                self.continuous_mode = False
                self.log("⏰ Режим continuous истёк. Скажи «Джарвис» для активации.")

            # Check if text contains both wake word and command
            if self._has_wake_word(t):
                # Wake word detected - parse with ML NLU which handles wake word stripping
                if hasattr(self.nlu, 'parse_with_wake_word'):
                    intent = self.nlu.parse_with_wake_word(t)
                else:
                    # Fallback: parse after manual wake word removal
                    intent = self.nlu.parse(t)
                
                if intent.get("type") != "unknown":
                    if intent.get("confidence", 0.0) < self.min_intent_confidence:
                        self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
                        continue

                    # Got valid intent in same sentence as wake word
                    self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
                    self.ex.run(intent)
                    self.log("✅ Готово.")
                    
                    # Enter continuous mode - wait for next command without wake word
                    self.continuous_mode = True
                    self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                    self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")
                    continue
                else:
                    # Wake word found but no command after it - arm and wait for command
                    self.armed = True
                    self._pending_command_since = time.perf_counter()
                    self.log("✅ Активирован. Скажи команду…")
                    continue
            
            # No wake word detected
            
            # Check if we're in continuous mode
            if self.continuous_mode:
                intent = self.nlu.parse(text)
                if intent.get("type") != "unknown":
                    if intent.get("confidence", 0.0) < self.min_intent_confidence:
                        self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
                        continue

                    self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
                    self.ex.run(intent)
                    self.log("✅ Готово.")
                    
                    # Reset continuous mode timer
                    self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                    self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")
                    continue
                else:
                    # No valid intent in continuous mode - just log and continue listening
                    self.log("❓ Не понял команду. Повтори.")
                    continue
            
            # Check if armed (regular two-step activation)
            if self.armed:
                intent = self.nlu.parse(text)
                if intent.get("type") == "unknown":
                    self.log("❓ Не понял команду. Повтори.")
                    continue

                if intent.get("confidence", 0.0) < self.min_intent_confidence:
                    self.log(f"⚠ Низкая уверенность ({intent.get('confidence', 0):.2f}). Повтори команду.")
                    continue

                self.log(f"🧠 Интент: {intent['type']} (confidence: {intent.get('confidence', 0):.2f})")
                self.ex.run(intent)
                self.log("✅ Готово.")

                # Enter continuous mode after command execution
                self.continuous_mode = True
                self.continuous_mode_until = time.time() + self.continuous_mode_timeout
                self.armed = False
                self.log(f"⏱ Слушаю следующую команду... ({self.continuous_mode_timeout:.0f}с)")
                continue

            # Not armed and no wake word - just log and continue
            self.log("🟢 Скажи «Джарвис» для активации.")
