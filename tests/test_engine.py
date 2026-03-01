import unittest
from unittest.mock import Mock, patch, MagicMock
import threading
import time

from src.jarvis.engine import JarvisEngine


class TestJarvisEngine(unittest.TestCase):
    """Тесты для JarvisEngine"""
    
    def setUp(self):
        """Подготовка перед каждым тестом"""
        self.engine = JarvisEngine(asr=None, log=lambda msg: None)
    
    def tearDown(self):
        """Очистка после каждого теста"""
        if self.engine.is_running:
            self.engine.stop()
            time.sleep(0.1)
    
    def test_engine_initialization(self):
        """Проверка инициализации движка"""
        self.assertIsNone(self.engine.asr)
        self.assertFalse(self.engine.is_loading)
        self.assertFalse(self.engine.is_ready)
        self.assertFalse(self.engine.is_running)
        self.assertFalse(self.engine.armed)
    
    def test_set_device(self):
        """Проверка переключения микрофона"""
        self.engine.set_device(1)
        self.assertEqual(self.engine.device, 1)
        
        # Сброс ASR при изменении устройства
        self.assertIsNone(self.engine.asr)
        self.assertFalse(self.engine.is_ready)
    
    def test_set_device_during_running_fails(self):
        """Переключение микрофона во время работы должно быть запрещено"""
        self.engine.is_running = True
        log_messages = []
        self.engine.log = lambda msg: log_messages.append(msg)
        
        self.engine.set_device(1)
        
        # Проверяем, что было выведено предупреждение
        self.assertTrue(any("нельзя менять" in msg.lower() for msg in log_messages))
    
    def test_stop_when_not_running(self):
        """Остановка неработающего движка должна быть безопасна"""
        self.engine.is_running = False
        self.engine.stop()  # Не должно вызвать ошибку
    
    def test_wake_word_detection(self):
        """Проверка обнаружения wake-word"""
        # Положительные случаи
        self.assertTrue(self.engine._has_wake_word("джарвис"))
        self.assertTrue(self.engine._has_wake_word("ДЖАРВИС"))
        self.assertTrue(self.engine._has_wake_word("hello джарвис world"))
        self.assertTrue(self.engine._has_wake_word("жарвис"))
        
        # Отрицательные случаи
        self.assertFalse(self.engine._has_wake_word("hello world"))
        self.assertFalse(self.engine._has_wake_word(""))
    
    def test_nlu_integration(self):
        """Проверка интеграции с NLU"""
        # Проверяем, что NLU был инициализирован
        self.assertIsNotNone(self.engine.nlu)
        
        # Проверяем базовое распознавание интента
        intent = self.engine.nlu.parse("открой браузер")
        self.assertEqual(intent["type"], "open_app")
        self.assertIn("target", intent["slots"])
    
    @patch('src.jarvis.vosk_asr.VoskASR')
    def test_ensure_asr_called_once(self, mock_vosk):
        """Проверка, что ASR инициализируется только один раз"""
        mock_asr = MagicMock()
        mock_vosk.return_value = mock_asr
        
        # Первый вызов
        self.engine._ensure_asr()
        self.assertEqual(mock_vosk.call_count, 1)
        
        # Второй вызов - не должен пересоздавать
        self.engine._ensure_asr()
        self.assertEqual(mock_vosk.call_count, 1)
    
    def test_thread_safety_of_stop(self):
        """Проверка потокобезопасности остановки"""
        # Установим флаги как если бы движок работал
        self.engine.is_running = True
        self.engine.is_loading = False
        
        # Проверим, что флаг _stop работает
        self.assertFalse(self.engine._stop.is_set())
        self.engine.stop()
        self.assertTrue(self.engine._stop.is_set())
        
        # Очистим флаг для следующих тестов
        self.engine._stop.clear()


class TestJarvisEngineIntegration(unittest.TestCase):
    """Интеграционные тесты для JarvisEngine"""
    
    def test_full_recognition_flow_with_mock_asr(self):
        """Полный цикл распознавания с mock ASR"""
        log_messages = []
        engine = JarvisEngine(asr=None, log=lambda msg: log_messages.append(msg))
        
        # Mock ASR для тестирования
        mock_asr = MagicMock()
        mock_asr.listen_once.side_effect = [
            "джарвис",  # wake-word
            "открой браузер",  # command
        ]
        engine.asr = mock_asr
        engine.is_ready = True
        
        # Проверяем wake-word обнаружение
        text1 = mock_asr.listen_once()
        self.assertTrue(engine._has_wake_word(text1))
        
        # Проверяем распознавание команды
        text2 = mock_asr.listen_once()
        intent = engine.nlu.parse(text2)
        self.assertEqual(intent["type"], "open_app")


if __name__ == "__main__":
    unittest.main()
