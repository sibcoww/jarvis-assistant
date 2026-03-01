import logging
import threading
from typing import Optional

try:
    import pyttsx3
    _PYTTSX3_AVAILABLE = True
except ImportError:
    _PYTTSX3_AVAILABLE = False

logger = logging.getLogger(__name__)


class TextToSpeech:
    """
    Синтез речи (text-to-speech) для озвучивания ответов.
    Использует pyttsx3 для offline синтеза. Если не установлен, функция отключена.
    """
    
    def __init__(self, voice_rate: int = 150, voice_volume: float = 1.0):
        """
        Args:
            voice_rate: Скорость речи (слов в минуту)
            voice_volume: Громкость (0.0 - 1.0)
        """
        self.voice_rate = voice_rate
        self.voice_volume = voice_volume
        self.engine: Optional[pyttsx3.Engine] = None
        self.is_speaking = False
        self._lock = threading.Lock()
        
        if _PYTTSX3_AVAILABLE:
            try:
                self._init_engine()
            except Exception as e:
                logger.error(f"Ошибка инициализации TTS: {e}")
    
    def _init_engine(self):
        """Инициализация pyttsx3 engine"""
        self.engine = pyttsx3.init()
        self.engine.setProperty('rate', self.voice_rate)
        self.engine.setProperty('volume', self.voice_volume)
        
        # Выбираем русский голос если доступен
        try:
            voices = self.engine.getProperty('voices')
            for voice in voices:
                if 'russian' in voice.languages or 'ru' in voice.name.lower():
                    self.engine.setProperty('voice', voice.id)
                    logger.info(f"Использую голос: {voice.name}")
                    return
        except Exception as e:
            logger.warning(f"Не удалось выбрать русский голос: {e}")
    
    def speak(self, text: str, async_mode: bool = True) -> bool:
        """
        Озвучить текст.
        
        Args:
            text: Текст для озвучивания
            async_mode: Если True, выполняется в отдельном потоке
            
        Returns:
            True если успешно, False если TTS недоступен
        """
        if not _PYTTSX3_AVAILABLE or self.engine is None:
            logger.warning("pyttsx3 не установлен. TTS отключен.")
            return False
        
        if not text or not text.strip():
            return False
        
        try:
            if async_mode:
                thread = threading.Thread(target=self._speak_sync, args=(text,), daemon=True)
                thread.start()
            else:
                self._speak_sync(text)
            return True
        except Exception as e:
            logger.error(f"Ошибка при озвучивании: {e}")
            return False
    
    def _speak_sync(self, text: str):
        """Синхронное озвучивание"""
        with self._lock:
            try:
                self.is_speaking = True
                logger.debug(f"TTS: {text}")
                self.engine.say(text)
                # Используем startLoop вместо runAndWait для избежания конфликтов
                try:
                    self.engine.runAndWait()
                except RuntimeError:
                    # Если loop уже запущен, просто ждём
                    import time
                    time.sleep(len(text) * 0.05)  # Примерная оценка времени
            finally:
                self.is_speaking = False
    
    def stop(self):
        """Остановить синтез речи"""
        if self.engine:
            try:
                self.engine.stop()
                self.is_speaking = False
            except Exception as e:
                logger.error(f"Ошибка остановки TTS: {e}")
    
    def set_rate(self, rate: int):
        """Установить скорость речи"""
        if self.engine:
            try:
                self.voice_rate = rate
                self.engine.setProperty('rate', rate)
            except Exception as e:
                logger.error(f"Ошибка установки скорости: {e}")
    
    def set_volume(self, volume: float):
        """Установить громкость (0.0 - 1.0)"""
        if self.engine:
            try:
                volume = max(0.0, min(1.0, volume))
                self.voice_volume = volume
                self.engine.setProperty('volume', volume)
            except Exception as e:
                logger.error(f"Ошибка установки громкости: {e}")
