# Заглушка для wake-word. Позже подключим Porcupine/Vosk KWS.
class WakeWord:
    def __init__(self, keyword: str = "джарвис"):
        self.keyword = keyword

    def heard(self, text: str) -> bool:
        return self.keyword in text.lower()
