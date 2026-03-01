import unittest
from src.jarvis.nlu import SimpleNLU, extract_number, NUMBERS


class TestExtractNumber(unittest.TestCase):
    """Тесты функции extract_number"""
    
    def test_extract_digit_number(self):
        """Извлечение числа из текста с цифрами"""
        self.assertEqual(extract_number("громкость 50"), 50)
        self.assertEqual(extract_number("тише на 20"), 20)
        self.assertEqual(extract_number("увеличь на 100"), 100)
    
    def test_extract_word_number(self):
        """Извлечение числа из текста со словами"""
        self.assertEqual(extract_number("громкость двадцать"), 20)
        self.assertEqual(extract_number("сделай тише на пять"), 5)
        self.assertEqual(extract_number("шум девяносто"), 90)
    
    def test_extract_zero(self):
        """Извлечение нуля"""
        self.assertEqual(extract_number("ноль"), 0)
    
    def test_no_number_found(self):
        """Случай когда число не найдено"""
        self.assertIsNone(extract_number("привет мир"))
        self.assertIsNone(extract_number(""))
        self.assertIsNone(extract_number("без чисел"))
    
    def test_extract_teen_numbers(self):
        """Извлечение чисел 11-19"""
        self.assertEqual(extract_number("тринадцать"), 13)
        self.assertEqual(extract_number("семнадцать"), 17)
        self.assertEqual(extract_number("девятнадцать"), 19)


class TestSimpleNLU(unittest.TestCase):
    """Тесты для SimpleNLU"""
    
    def setUp(self):
        self.nlu = SimpleNLU()
    
    def test_open_app_browser(self):
        """Распознавание команды открыть браузер"""
        intent = self.nlu.parse("открой браузер")
        self.assertEqual(intent["type"], "open_app")
        self.assertEqual(intent["slots"]["target"], "browser")
        
        # Синонимы
        intent = self.nlu.parse("запусти хром")
        self.assertEqual(intent["type"], "open_app")
        self.assertEqual(intent["slots"]["target"], "browser")
    
    def test_open_app_telegram(self):
        """Распознавание команды открыть телеграм"""
        intent = self.nlu.parse("открой телеграм")
        self.assertEqual(intent["type"], "open_app")
        self.assertEqual(intent["slots"]["target"], "telegram")
        
        intent = self.nlu.parse("запусти телеграмм")
        self.assertEqual(intent["type"], "open_app")
        self.assertEqual(intent["slots"]["target"], "telegram")
    
    def test_open_app_vscode(self):
        """Распознавание команды открыть VS Code"""
        intent = self.nlu.parse("открой vscode")
        self.assertEqual(intent["type"], "open_app")
        self.assertEqual(intent["slots"]["target"], "vscode")
        
        intent = self.nlu.parse("запусти вс код")
        self.assertEqual(intent["type"], "open_app")
        self.assertEqual(intent["slots"]["target"], "vscode")
    
    def test_open_app_notepad(self):
        """Распознавание команды открыть блокнот"""
        intent = self.nlu.parse("открой блокнот")
        self.assertEqual(intent["type"], "open_app")
        self.assertEqual(intent["slots"]["target"], "notepad")
    
    def test_volume_down(self):
        """Распознавание команды уменьшить громкость"""
        intent = self.nlu.parse("сделай тише")
        self.assertEqual(intent["type"], "volume_down")
        self.assertIn("delta", intent["slots"])
        
        intent = self.nlu.parse("убавь громкость на 30")
        self.assertEqual(intent["type"], "volume_down")
        self.assertEqual(intent["slots"]["delta"], 30)
    
    def test_volume_up(self):
        """Распознавание команды увеличить громкость"""
        intent = self.nlu.parse("сделай громче")
        self.assertEqual(intent["type"], "volume_up")
        self.assertIn("delta", intent["slots"])
        
        intent = self.nlu.parse("добавь громкость на 25")
        self.assertEqual(intent["type"], "volume_up")
        self.assertEqual(intent["slots"]["delta"], 25)
    
    def test_set_volume_direct(self):
        """Распознавание команды установить громкость на конкретное значение"""
        intent = self.nlu.parse("громкость 50")
        self.assertEqual(intent["type"], "set_volume")
        self.assertEqual(intent["slots"]["value"], 50)
        
        intent = self.nlu.parse("звук семьдесят")
        self.assertEqual(intent["type"], "set_volume")
        self.assertEqual(intent["slots"]["value"], 70)
    
    def test_set_volume_clamped(self):
        """Проверка clamping громкости (0-100)"""
        intent = self.nlu.parse("громкость 150")
        self.assertEqual(intent["type"], "set_volume")
        self.assertEqual(intent["slots"]["value"], 100)
        
        intent = self.nlu.parse("звук -50")
        self.assertEqual(intent["type"], "set_volume")
        self.assertGreaterEqual(intent["slots"]["value"], 0)
    
    def test_run_scenario(self):
        """Распознавание команды запустить сценарий"""
        intent = self.nlu.parse("рабочий режим")
        self.assertEqual(intent["type"], "run_scenario")
        self.assertEqual(intent["slots"]["name"], "рабочий режим")
    
    def test_case_insensitivity(self):
        """Проверка регистронезависимости"""
        intent1 = self.nlu.parse("открой браузер")
        intent2 = self.nlu.parse("ОТКРОЙ БРАУЗЕР")
        intent3 = self.nlu.parse("Открой Браузер")
        
        self.assertEqual(intent1["type"], intent2["type"])
        self.assertEqual(intent1["type"], intent3["type"])
    
    def test_create_folder_intent(self):
        """Распознавание команды создать папку"""
        intent = self.nlu.parse("создай папку MyFolder")
        # Может быть не обработана, проверяем что не вызывает ошибку
        self.assertIsNotNone(intent)
    
    def test_unknown_command(self):
        """Обработка неизвестной команды"""
        intent = self.nlu.parse("абракадабра алокасим")
        # Должна вернуться какая-то структура
        self.assertIsNotNone(intent)
        self.assertIn("type", intent)


class TestNLUEdgeCases(unittest.TestCase):
    """Тесты граничных случаев для NLU"""
    
    def setUp(self):
        self.nlu = SimpleNLU()
    
    def test_empty_input(self):
        """Обработка пустого ввода"""
        intent = self.nlu.parse("")
        self.assertIsNotNone(intent)
    
    def test_whitespace_only(self):
        """Обработка только пробелов"""
        intent = self.nlu.parse("   ")
        self.assertIsNotNone(intent)
    
    def test_special_characters(self):
        """Обработка специальных символов"""
        intent = self.nlu.parse("открой браузер!!! ???")
        self.assertIsNotNone(intent)
    
    def test_mixed_languages(self):
        """Обработка смешивания языков"""
        intent = self.nlu.parse("открой chrome browser")
        self.assertIsNotNone(intent)


if __name__ == "__main__":
    unittest.main()
