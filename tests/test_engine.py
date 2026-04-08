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

    def test_known_command_stays_offline(self):
        """Известные команды должны выполняться локально и не вызывать AI fallback."""
        with patch.object(self.engine.ex, "run") as mock_run, patch.object(
            self.engine.ex, "handle_unrecognized_command"
        ) as mock_ai:
            result = self.engine._execute_intent_if_valid("открой браузер")

        self.assertTrue(result)
        mock_run.assert_called_once()
        mock_ai.assert_not_called()

    def test_unknown_command_goes_to_ai_fallback(self):
        """Неизвестные команды должны уходить в AI fallback."""
        with patch.object(self.engine.ex, "run") as mock_run, patch.object(
            self.engine.ex, "handle_unrecognized_command", return_value=True
        ) as mock_ai:
            result = self.engine._execute_intent_if_valid("кто ты такой")

        self.assertTrue(result)
        mock_ai.assert_called_once_with("кто ты такой")
        mock_run.assert_not_called()

    def test_risky_intent_requires_confirmation(self):
        logs = []
        engine = JarvisEngine(asr=None, log=lambda m: logs.append(m))
        with patch.object(engine.nlu, "parse", return_value={"type": "delete_file", "slots": {"path": "x.txt"}}), patch.object(
            engine.ex, "should_require_confirmation", return_value=True
        ), patch.object(
            engine.ex, "queue_confirmation", return_value="Подтверди удаление."
        ), patch.object(
            engine.ex, "run"
        ) as mock_run:
            handled = engine._execute_intent_if_valid("удали файл x.txt")
        self.assertTrue(handled)
        mock_run.assert_not_called()
        self.assertTrue(any("pending_confirmation" in m for m in logs))

    def test_confirmation_phrase_executes_pending_intent(self):
        logs = []
        engine = JarvisEngine(asr=None, log=lambda m: logs.append(m))
        with patch.object(
            engine.ex,
            "pending_confirmation_from_text",
            return_value=(True, {"type": "delete_file", "slots": {"path": "x.txt"}}, "confirm"),
        ), patch.object(engine.ex, "run") as mock_run:
            handled = engine._execute_intent_if_valid("подтверждаю")
        self.assertTrue(handled)
        mock_run.assert_called_once()
        self.assertTrue(any("[PIPE]" in m for m in logs))

    def test_stop_idempotent_logs_once(self):
        logs: list[str] = []
        engine = JarvisEngine(asr=None, log=lambda m: logs.append(m))
        engine.is_running = True

        engine.stop("first")
        engine.stop("second")

        stop_logs = [m for m in logs if "Движок остановлен" in m]
        self.assertEqual(len(stop_logs), 1)
        self.assertEqual(engine._stop_reason, "first")
        self.assertTrue(engine._stop.is_set())

    def test_ai_failure_does_not_stop_engine(self):
        engine = JarvisEngine(asr=None, log=lambda m: None)
        engine.is_running = True
        with patch.object(engine.nlu, "parse", return_value={"type": "unknown", "confidence": 1.0}):
            with patch.object(engine.ex, "handle_unrecognized_command", side_effect=RuntimeError("boom")):
                handled = engine._execute_intent_if_valid("fail")

        self.assertFalse(handled)
        self.assertFalse(engine._stop.is_set())
        self.assertTrue(engine.is_running)

    def test_ai_empty_or_rate_limit_keeps_engine_alive(self):
        engine = JarvisEngine(asr=None, log=lambda m: None)
        engine.is_running = True
        with patch.object(engine.nlu, "parse", return_value={"type": "unknown", "confidence": 1.0}):
            with patch.object(engine.ex, "handle_unrecognized_command", return_value=False):
                handled = engine._execute_intent_if_valid("429 scenario")

        self.assertFalse(handled)
        self.assertFalse(engine._stop.is_set())
        self.assertTrue(engine.is_running)

    def test_continuous_timeout_does_not_stop_engine(self):
        logs: list[str] = []
        engine = JarvisEngine(asr=None, log=lambda m: logs.append(m))
        engine.continuous_mode = True
        engine.continuous_mode_until = time.time() - 1

        expired = engine._expire_continuous_if_needed(now=time.time())

        self.assertTrue(expired)
        self.assertFalse(engine.continuous_mode)
        self.assertFalse(engine._stop.is_set())
        self.assertTrue(any("continuous истёк" in msg for msg in logs))

    def test_stop_start_stop_sequence(self):
        engine = JarvisEngine(asr=None, log=lambda m: None)
        engine.is_running = True
        engine.stop("first")
        self.assertTrue(engine._stop.is_set())
        self.assertEqual(engine._stop_reason, "first")

        # Stub out bootstrap to avoid heavy init
        engine._bootstrap_and_run = lambda: None
        engine.start()

        self.assertFalse(engine._stop.is_set())
        self.assertIsNone(engine._stop_reason)
        self.assertFalse(engine.continuous_mode)
        self.assertFalse(engine.armed)

        engine.is_running = True
        engine.stop("second")
        self.assertTrue(engine._stop.is_set())
        self.assertEqual(engine._stop_reason, "second")


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
