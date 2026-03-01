import queue
import json
import time

import sounddevice as sd
from vosk import Model, KaldiRecognizer

_TIMING_DEBUG = False  # Set True to see detailed timing logs during loading
_MODEL_CACHE = {}  # Global cache for loaded models


class VoskASR:
    """
    Быстрый Vosk ASR:
    - модель и аудио-поток инициализируются один раз
    - listen_once() только читает из очереди
    """

    def __init__(
        self,
        model_path: str,
        device: int | None = None,
        samplerate: int | None = None,
        phrase_timeout: float = 6.0,   # максимум ждать фразу
        silence_timeout: float = 1.2,  # сколько тишины считать концом фразы
        on_progress=None,  # callback(step: int, total: int) for UI feedback
    ):
        start_total = time.time()
        
        if on_progress:
            on_progress(0, 2)  # теперь 2 шага: модель + recognizer (поток отложен)
        
        # Попытка получить модель из кеша
        t0 = time.time()
        if model_path in _MODEL_CACHE:
            self.model = _MODEL_CACHE[model_path]
            if _TIMING_DEBUG:
                print(f"[TIMING] Model loaded from cache: {time.time() - t0:.3f}s")
        else:
            self.model = Model(model_path)
            _MODEL_CACHE[model_path] = self.model
            if _TIMING_DEBUG:
                print(f"[TIMING] Model init (fresh): {time.time() - t0:.2f}s")
        
        self.q: "queue.Queue[bytes]" = queue.Queue()

        self.device = device

        # если samplerate не задан — берём дефолтный у устройства (это часто ускоряет старт)
        if samplerate is None:
            dev = sd.query_devices(device, "input")
            samplerate = int(dev["default_samplerate"])
        self.samplerate = int(samplerate)

        self.phrase_timeout = phrase_timeout
        self.silence_timeout = silence_timeout

        if on_progress:
            on_progress(1, 2)
        
        t0 = time.time()
        # recognizer создаём один раз
        self.rec = KaldiRecognizer(self.model, self.samplerate)
        self.rec.SetWords(False)
        if _TIMING_DEBUG:
            print(f"[TIMING] KaldiRecognizer init: {time.time() - t0:.2f}s")

        if on_progress:
            on_progress(2, 2)
        
        # Поток создаём отложенно при первом вызове listen_once()
        self.stream = None
        
        if _TIMING_DEBUG:
            print(f"[TIMING] VoskASR total init: {time.time() - start_total:.2f}s")

    def _callback(self, indata, frames, time_info, status):
        # не спамим логами, но статус можно отладить при необходимости
        self.q.put(bytes(indata))

    def _ensure_stream(self):
        """Отложенная инициализация аудио-потока при первом использовании"""
        if self.stream is not None:
            return
        
        t0 = time.time()
        self.stream = sd.RawInputStream(
            samplerate=self.samplerate,
            blocksize=8000,
            device=self.device,
            dtype="int16",
            channels=1,
            callback=self._callback,
        )
        self.stream.start()
        if _TIMING_DEBUG:
            print(f"[TIMING] Stream init (lazy): {time.time() - t0:.2f}s")

    def _drain_queue(self):
        # очистить очередь перед новой фразой (иначе могут тянуться старые куски)
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def listen_once(self) -> str | None:
        """
        Возвращает распознанную фразу (или None).
        Завершает фразу по:
        - AcceptWaveform (Vosk решил, что фраза закончилась)
        - или по таймауту phrase_timeout
        """
        self._ensure_stream()  # создаём поток при первом вызове
        self._drain_queue()
        start = time.time()

        while time.time() - start < self.phrase_timeout:
            try:
                data = self.q.get(timeout=0.5)
            except queue.Empty:
                continue

            if self.rec.AcceptWaveform(data):
                return self._extract_text(self.rec.Result())

        # если таймаут — пробуем финальный результат
        return self._extract_text(self.rec.FinalResult())

    @staticmethod
    def _extract_text(result_json: str) -> str | None:
        try:
            j = json.loads(result_json)
            text = j.get("text", "").strip()
            return text if text else None
        except Exception:
            return None

    def close(self):
        try:
            if self.stream:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
