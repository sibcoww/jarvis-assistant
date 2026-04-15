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

        intent = self.nlu.parse("открыть телеграмм")
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

    def test_close_app_commands(self):
        intent = self.nlu.parse("закрой телеграм")
        self.assertEqual(intent["type"], "close_app")
        self.assertEqual(intent["slots"]["target"], "telegram")

        intent = self.nlu.parse("выключи браузер")
        self.assertEqual(intent["type"], "close_app")
        self.assertEqual(intent["slots"]["target"], "browser")

        intent = self.nlu.parse("закрыть телеграмм")
        self.assertEqual(intent["type"], "close_app")
        self.assertEqual(intent["slots"]["target"], "telegram")

    def test_open_app_target_in_browser_phrase(self):
        intent = self.nlu.parse("открой ватсап в браузере")
        self.assertEqual(intent["type"], "open_app")
        self.assertEqual(intent["slots"]["target"], "whatsapp")

    def test_generic_open_commands_are_unknown_for_clarification(self):
        intent = self.nlu.parse("открой сайт")
        self.assertEqual(intent["type"], "unknown")
        intent = self.nlu.parse("открой программу")
        self.assertEqual(intent["type"], "unknown")
        intent = self.nlu.parse("поставь громкость")
        self.assertEqual(intent["type"], "unknown")
    
    def test_volume_down(self):
        """Распознавание команды уменьшить громкость"""
        intent = self.nlu.parse("сделай тише")
        self.assertEqual(intent["type"], "volume_down")
        self.assertEqual(intent["slots"]["delta"], 10)
        
        intent = self.nlu.parse("убавь громкость на 30")
        self.assertEqual(intent["type"], "volume_down")
        self.assertEqual(intent["slots"]["delta"], 30)

        intent = self.nlu.parse("надо сделать громкость на десять меньше")
        self.assertEqual(intent["type"], "volume_down")
        self.assertEqual(intent["slots"]["delta"], 10)
    
    def test_volume_up(self):
        """Распознавание команды увеличить громкость"""
        intent = self.nlu.parse("сделай громче")
        self.assertEqual(intent["type"], "volume_up")
        self.assertEqual(intent["slots"]["delta"], 10)
        
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


class TestNLUBrowserCommands(unittest.TestCase):
    """Тесты для команд браузера"""
    
    def setUp(self):
        self.nlu = SimpleNLU()
    
    def test_browser_navigate(self):
        """Распознавание команды перехода на сайт"""
        intent = self.nlu.parse("перейди на google.com")
        self.assertEqual(intent["type"], "browser_navigate")
        self.assertIn("google.com", intent["slots"]["url"])
        
        intent = self.nlu.parse("открой сайт wikipedia.org")
        self.assertEqual(intent["type"], "browser_navigate")
        self.assertIn("wikipedia.org", intent["slots"]["url"])
    
    def test_browser_search(self):
        """Распознавание команды поиска"""
        intent = self.nlu.parse("гугл python")
        self.assertEqual(intent["type"], "browser_search")
        self.assertEqual(intent["slots"]["query"], "python")
        
        intent = self.nlu.parse("поиск как готовить торт")
        self.assertEqual(intent["type"], "browser_search")
        self.assertIn("торт", intent["slots"]["query"])


class TestNLUMediaCommands(unittest.TestCase):
    """Тесты для медиа-команд"""
    
    def setUp(self):
        self.nlu = SimpleNLU()
    
    def test_media_play(self):
        """Распознавание команды включения музыки"""
        intent = self.nlu.parse("включи музыку")
        self.assertEqual(intent["type"], "media_play")
        
        intent = self.nlu.parse("запусти музыку")
        self.assertEqual(intent["type"], "media_play")
    
    def test_media_pause(self):
        """Распознавание команды паузы"""
        intent = self.nlu.parse("пауза")
        self.assertEqual(intent["type"], "media_pause")
        
        intent = self.nlu.parse("стоп")
        self.assertEqual(intent["type"], "media_pause")
        
        intent = self.nlu.parse("остановись")
        self.assertEqual(intent["type"], "media_pause")
    
    def test_media_next(self):
        """Распознавание команды следующего трека"""
        intent = self.nlu.parse("далее")
        self.assertEqual(intent["type"], "media_next")
        
        intent = self.nlu.parse("следующая")
        self.assertEqual(intent["type"], "media_next")
    
    def test_media_previous(self):
        """Распознавание команды предыдущего трека"""
        intent = self.nlu.parse("назад")
        self.assertEqual(intent["type"], "media_previous")
        
        intent = self.nlu.parse("предыдущая")
        self.assertEqual(intent["type"], "media_previous")


class TestNLUCalendarCommands(unittest.TestCase):
    """Тесты для команд календаря и времени"""
    
    def setUp(self):
        self.nlu = SimpleNLU()
    
    def test_show_date(self):
        """Распознавание команды показа даты"""
        intent = self.nlu.parse("какая дата")
        self.assertEqual(intent["type"], "show_date")
        
        intent = self.nlu.parse("сегодня дата")
        self.assertEqual(intent["type"], "show_date")
        
        intent = self.nlu.parse("текущая дата")
        self.assertEqual(intent["type"], "show_date")
    
    def test_show_time(self):
        """Распознавание команды показа времени"""
        intent = self.nlu.parse("какое время")
        self.assertEqual(intent["type"], "show_time")
        
        intent = self.nlu.parse("текущее время")
        self.assertEqual(intent["type"], "show_time")
        
        intent = self.nlu.parse("который час")
        self.assertEqual(intent["type"], "show_time")
    
    def test_create_reminder(self):
        """Распознавание команды создания напоминания"""
        intent = self.nlu.parse("напоминание купить молоко")
        self.assertEqual(intent["type"], "create_reminder")
        self.assertIn("молоко", intent["slots"]["reminder"])


class TestNLUNotesCommands(unittest.TestCase):
    """Тесты для команд заметок"""
    
    def setUp(self):
        self.nlu = SimpleNLU()
    
    def test_add_note(self):
        """Распознавание команды добавления заметки"""
        intent = self.nlu.parse("запомни позвонить маме")
        self.assertEqual(intent["type"], "add_note")
        self.assertIn("маме", intent["slots"]["text"])
        
        intent = self.nlu.parse("запишись что нужно купить хлеб")
        self.assertEqual(intent["type"], "add_note")
    
    def test_read_notes(self):
        """Распознавание команды чтения заметок"""
        intent = self.nlu.parse("вспомни")
        self.assertEqual(intent["type"], "read_notes")
        
        intent = self.nlu.parse("прочитай заметки")
        self.assertEqual(intent["type"], "read_notes")


class TestNLUTodoCommands(unittest.TestCase):
    def setUp(self):
        self.nlu = SimpleNLU()

    def test_add_todo(self):
        intent = self.nlu.parse("добавь задачу купить молоко")
        self.assertEqual(intent["type"], "add_todo")
        self.assertIn("молоко", intent["slots"]["text"])
        intent = self.nlu.parse("запиши мне в дела купить хлеб на вечер")
        self.assertEqual(intent["type"], "add_todo")
        self.assertIn("купить хлеб", intent["slots"]["text"])

    def test_list_todos(self):
        intent = self.nlu.parse("покажи задачи")
        self.assertEqual(intent["type"], "list_todos")
        intent = self.nlu.parse("покажи задач")
        self.assertEqual(intent["type"], "list_todos")

    def test_complete_todo(self):
        intent = self.nlu.parse("выполнил задачу 2")
        self.assertEqual(intent["type"], "complete_todo")
        self.assertEqual(intent["slots"]["ref"], "2")
        intent = self.nlu.parse("выполнила задачу один")
        self.assertEqual(intent["type"], "complete_todo")
        self.assertEqual(intent["slots"]["ref"], "один")
        intent = self.nlu.parse("выполнил задать один")
        self.assertEqual(intent["type"], "complete_todo")
        self.assertEqual(intent["slots"]["ref"], "один")
        intent = self.nlu.parse("отметьте как первая задача")
        self.assertEqual(intent["type"], "complete_todo")
        self.assertIn("первая", intent["slots"]["ref"])
        intent = self.nlu.parse("отметь когда первую задач")
        self.assertEqual(intent["type"], "complete_todo")
        self.assertIn("первую", intent["slots"]["ref"])

    def test_delete_todo(self):
        intent = self.nlu.parse("удали задачу купить молоко")
        self.assertEqual(intent["type"], "delete_todo")
        self.assertIn("молоко", intent["slots"]["ref"])
        intent = self.nlu.parse("удали задать один")
        self.assertEqual(intent["type"], "delete_todo")
        self.assertEqual(intent["slots"]["ref"], "один")


class TestNLUTimerCommands(unittest.TestCase):
    def setUp(self):
        self.nlu = SimpleNLU()

    def test_start_timer_minutes(self):
        intent = self.nlu.parse("таймер на 5 минут чай")
        self.assertEqual(intent["type"], "start_timer")
        self.assertEqual(intent["slots"]["amount"], 5)
        self.assertIn("минут", intent["slots"]["unit"])
        self.assertIn("чай", intent["slots"]["label"])

    def test_start_timer_seconds(self):
        intent = self.nlu.parse("таймер 10 секунд")
        self.assertEqual(intent["type"], "start_timer")
        self.assertEqual(intent["slots"]["amount"], 10)

    def test_timer_status(self):
        intent = self.nlu.parse("сколько осталось")
        self.assertEqual(intent["type"], "timer_status")

    def test_timer_cancel(self):
        intent = self.nlu.parse("отмени таймер")
        self.assertEqual(intent["type"], "cancel_timer")


class TestNLUPresentationCommands(unittest.TestCase):
    def setUp(self):
        self.nlu = SimpleNLU()

    def test_next_slide(self):
        intent = self.nlu.parse("следующий слайд")
        self.assertEqual(intent["type"], "presentation_next_slide")
        intent = self.nlu.parse("слайд вперед")
        self.assertEqual(intent["type"], "presentation_next_slide")

    def test_previous_slide(self):
        intent = self.nlu.parse("предыдущий слайд")
        self.assertEqual(intent["type"], "presentation_previous_slide")
        intent = self.nlu.parse("слайд назад")
        self.assertEqual(intent["type"], "presentation_previous_slide")

    def test_presentation_start_end(self):
        intent = self.nlu.parse("запусти презентацию")
        self.assertEqual(intent["type"], "presentation_start")
        intent = self.nlu.parse("останови презентацию")
        self.assertEqual(intent["type"], "presentation_end")


class TestNLUWindowAndHistoryCommands(unittest.TestCase):
    def setUp(self):
        self.nlu = SimpleNLU()

    def test_window_snap_commands(self):
        self.assertEqual(self.nlu.parse("окно влево")["type"], "window_snap_left")
        self.assertEqual(self.nlu.parse("окно вправо")["type"], "window_snap_right")
        self.assertEqual(self.nlu.parse("окно вверх")["type"], "window_snap_up")
        self.assertEqual(self.nlu.parse("окно вниз")["type"], "window_snap_down")

    def test_window_split_two(self):
        self.assertEqual(self.nlu.parse("раздели экран пополам")["type"], "window_split_two")

    def test_repeat_and_history(self):
        self.assertEqual(self.nlu.parse("повтори команду")["type"], "repeat_last_command")
        self.assertEqual(self.nlu.parse("что ты сделал")["type"], "show_action_history")


class TestNLUSystemCommands(unittest.TestCase):
    def setUp(self):
        self.nlu = SimpleNLU()

    def test_shutdown_pc(self):
        intent = self.nlu.parse("выключи компьютер")
        self.assertEqual(intent["type"], "shutdown_pc")
        intent = self.nlu.parse("выключи нахуй комп")
        self.assertEqual(intent["type"], "shutdown_pc")

    def test_restart_pc(self):
        intent = self.nlu.parse("перезагрузи компьютер")
        self.assertEqual(intent["type"], "restart_pc")
        intent = self.nlu.parse("перезапусти срочно комп")
        self.assertEqual(intent["type"], "restart_pc")

    def test_sleep_pc(self):
        intent = self.nlu.parse("режим сна")
        self.assertEqual(intent["type"], "sleep_pc")

    def test_lock_pc(self):
        intent = self.nlu.parse("заблокируй экран")
        self.assertEqual(intent["type"], "lock_pc")

    def test_show_weather(self):
        intent = self.nlu.parse("какая погода в астане")
        self.assertEqual(intent["type"], "show_weather")
        self.assertIn("астане", intent["slots"]["city"])
        intent = self.nlu.parse("погода")
        self.assertEqual(intent["type"], "show_weather")


if __name__ == "__main__":
    unittest.main()
