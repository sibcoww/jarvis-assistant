import threading
from typing import Callable, Optional

from .nlu import SimpleNLU
from .executor import Executor

class JarvisEngine:
    def __init__(self, asr, log: Optional[Callable[[str], None]] = None):
        self.asr = asr
        self.nlu = SimpleNLU()
        self.ex = Executor()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.log = log or (lambda msg: None)

        self.armed = False  # ждём ли команду после wake-word

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.log("🟢 Движок запущен. Скажи «Джарвис».")

    def stop(self):
        self._stop.set()
        self.log("🔴 Движок остановлен.")

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
