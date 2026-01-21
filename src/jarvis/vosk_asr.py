import queue
import json
import time

import sounddevice as sd
from vosk import Model, KaldiRecognizer


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
    ):
        self.model = Model(model_path)
        self.q: "queue.Queue[bytes]" = queue.Queue()

        self.device = device

        # если samplerate не задан — берём дефолтный у устройства (это часто ускоряет старт)
        if samplerate is None:
            dev = sd.query_devices(device, "input")
            samplerate = int(dev["default_samplerate"])
        self.samplerate = int(samplerate)

        self.phrase_timeout = phrase_timeout
        self.silence_timeout = silence_timeout

        # recognizer создаём один раз
        self.rec = KaldiRecognizer(self.model, self.samplerate)
        self.rec.SetWords(False)

        # поток создаём один раз
        self.stream = sd.RawInputStream(
            samplerate=self.samplerate,
            blocksize=8000,
            device=self.device,
            dtype="int16",
            channels=1,
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, indata, frames, time_info, status):
        # не спамим логами, но статус можно отладить при необходимости
        self.q.put(bytes(indata))

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
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
