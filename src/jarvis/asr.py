class MockASR:
    """Простейший ASR: читает строку из stdin вместо микрофона."""
    def listen_once(self):
        try:
            return input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

# Заготовка под реальный Vosk ASR (позже):
class VoskASR:
    def __init__(self, model_path: str = "models/vosk-ru"):
        raise NotImplementedError("Подключи Vosk и инициализируй модель. См. scripts/notes_vosk.txt")

    def listen_once(self):
        raise NotImplementedError
