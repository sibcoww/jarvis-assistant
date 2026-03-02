import threading
import logging
import time
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


    def _ensure_asr(self):
        if self.asr is not None:
            self.is_ready = True
            return

        with self._asr_lock:
            if self.asr is not None:
                self.is_ready = True
                return

            self.is_loading = True
            self.log("⏳ Загрузка модели распознавания речи...")
            
            def on_progress(step, total):
                percent = int((step / total) * 100)
                self.log(f"📊 Загрузка: {percent}% ({step}/{total})")
            
            from .vosk_asr import VoskASR
            self.asr = VoskASR("models/vosk-model-ru-0.42", device=self.device, on_progress=on_progress)
            self.is_loading = False
            self.is_ready = True
            self.log("✅ Модель загружена, микрофон готов")

    def set_device(self, device_index: int | None):
        if self.is_running or self.is_loading:
            self.log("⚠ Нельзя менять микрофон во время работы/загрузки.")
            return
        self.device = device_index
        self.asr = None
        self.is_ready = False
        self.log(f"🎤 Выбран микрофон: {device_index}. Нажми Старт (модель загрузится заново).")

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

            self.is_running = True
            self.log("🟢 Движок запущен. Скажи «Джарвис».")
            self._run()

        except Exception as e:
            self.log(f"❌ Ошибка запуска движка: {e}")
        finally:
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



    def _has_wake_word(self, text: str) -> bool:
        """Check if text contains wake word."""
        wake_words = {"джарвис", "жарвис", "джервис", "джанверт", "джанвис", "джаврис"}
        words = text.lower().split()
        return any(w in wake_words for w in words)

    def _run(self):
        while not self._stop.is_set():
            text = self.asr.listen_once()
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
