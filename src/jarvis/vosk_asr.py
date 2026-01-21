import queue
import json
import time
try:
    import sounddevice as sd
    from vosk import Model, KaldiRecognizer
except Exception as e:
    sd = None
    Model = None
    KaldiRecognizer = None

class VoskASR:
    """
    Простой Vosk ASR для коротких команд.
    - Инициализируй модель один раз.
    - listen_once() пишет звук ~5 секунд или до паузы.
    """
    def __init__(self, model_path: str = "models/vosk-ru", samplerate: int = 16000, record_seconds: float = 5.0):
        if Model is None or sd is None:
            raise RuntimeError("Установи пакеты 'vosk' и 'sounddevice'")
        self.model = Model(model_path)
        self.samplerate = samplerate
        self.record_seconds = record_seconds
        self.q = queue.Queue()

    def _callback(self, indata, frames, time_info, status):
        if status:
            # Можно логировать статус
            pass
        self.q.put(bytes(indata))

    def listen_once(self) -> str | None:
        """Записывает звук и возвращает распознанный текст (или None)."""
        rec = KaldiRecognizer(self.model, self.samplerate)
        rec.SetWords(False)
        with sd.RawInputStream(samplerate=self.samplerate, blocksize = 8000, dtype='int16',
                               channels=1, callback=self._callback):
            start = time.time()
            while time.time() - start < self.record_seconds:
                try:
                    data = self.q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if rec.AcceptWaveform(data):
                    res = rec.Result()
                    try:
                        j = json.loads(res)
                        text = j.get("text", "").strip()
                        return text if text else None
                    except Exception:
                        return None
            # Финальный результат после истечения времени
            res = rec.FinalResult()
            try:
                j = json.loads(res)
                text = j.get("text", "").strip()
                return text if text else None
            except Exception:
                return None
