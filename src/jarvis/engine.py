import threading
from typing import Callable, Optional

from .nlu import SimpleNLU
from .executor import Executor
from PySide6.QtCore import QTimer

class JarvisEngine:
    def __init__(self, asr=None, log= None):
        self.asr = asr
        self.nlu = SimpleNLU()
        self.ex = Executor()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.log = log or (lambda msg: None)

        self.armed = False  # ждём ли команду после wake-word
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
            from .vosk_asr import VoskASR
            self.asr = VoskASR("models/vosk-model-ru-0.42", device=self.device)
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
        self.log("🔴 Движок остановлен.")

        
    def preload(self):
        try:
            self._ensure_asr()
        except Exception as e:
            self.is_loading = False
            self.log(f"❌ Ошибка загрузки модели: {e}")



    def _has_wake_word(self, text: str) -> bool:
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

            # ждём wake-word
            if not self.armed:
                if self._has_wake_word(t):
                    self.armed = True
                    self.log("✅ Активирован. Скажи команду…")
                continue

            # это уже команда
            intent = self.nlu.parse(text)
            self.log(f"🧠 Интент: {intent}")
            self.ex.run(intent)
            self.log("✅ Готово.")

            self.armed = False
            self.log("🟢 Скажи «Джарвис» для активации.")
