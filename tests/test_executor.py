import os
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch, MagicMock
from pathlib import Path


def _make_executor(config=None):
    try:
        from src.jarvis.executor import Executor
    except Exception as error:
        raise unittest.SkipTest(f"Executor недоступен: {error}")
    return Executor(config=config)


class TestExecutor(unittest.TestCase):
    def test_resolve_target_uses_synonyms(self):
        ex = _make_executor(
            {
                "apps": {"telegram": "Telegram.exe"},
                "synonyms": {"телеграм": "telegram"},
            }
        )

        self.assertEqual(ex._resolve_target("телеграм"), "telegram")
        self.assertEqual(ex._resolve_target("telegram"), "telegram")

    def test_run_routes_volume_intents(self):
        ex = _make_executor({})

        with patch.object(ex, "change_volume") as mock_change:
            ex.run({"type": "volume_up", "slots": {"delta": 15}})
            mock_change.assert_called_once_with(15)

        with patch.object(ex, "change_volume") as mock_change:
            ex.run({"type": "volume_down", "slots": {"delta": 7}})
            mock_change.assert_called_once_with(-7)

    def test_run_routes_open_app_with_synonym(self):
        ex = _make_executor(
            {
                "apps": {"telegram": "Telegram.exe"},
                "synonyms": {"телеграм": "telegram"},
            }
        )

        with patch("src.jarvis.executor.subprocess.Popen") as mock_popen:
            ex.run({"type": "open_app", "slots": {"target": "телеграм"}})
            mock_popen.assert_called_once()
            launched_cmd = mock_popen.call_args.args[0]
            self.assertIn("Telegram.exe", launched_cmd)

    def test_create_folder_creates_directory(self):
        ex = _make_executor({})

        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                ex.run({"type": "create_folder", "slots": {"name": "Проект"}})
                self.assertTrue(os.path.isdir(os.path.join(temp_dir, "Проект")))
            finally:
                os.chdir(previous_cwd)

    def test_get_volume_endpoint_uses_endpoint_volume_query_interface(self):
        ex = _make_executor({})

        class FakeEndpointVolume:
            def __init__(self):
                self.query_arg = None

            def QueryInterface(self, interface):
                self.query_arg = interface
                return "endpoint"

        class FakeSpeakers:
            def __init__(self, endpoint_volume):
                self.EndpointVolume = endpoint_volume

        fake_endpoint_volume = FakeEndpointVolume()
        fake_speakers = FakeSpeakers(fake_endpoint_volume)

        with patch("src.jarvis.executor.AudioUtilities.GetSpeakers", return_value=fake_speakers):
            endpoint = ex._get_volume_endpoint()

        self.assertEqual(endpoint, "endpoint")
        self.assertIsNotNone(fake_endpoint_volume.query_arg)


class TestExecutorBrowserCommands(unittest.TestCase):
    """Тесты для команд браузера"""
    
    def test_browser_navigate(self):
        ex = _make_executor({})
        
        with patch("src.jarvis.executor.subprocess.Popen", side_effect=RuntimeError("no browser")), patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_open:
            ex.browser_navigate("google.com")
            mock_open.assert_called_once()
            called_url = mock_open.call_args.args[0]
            self.assertIn("google.com", called_url)
            self.assertTrue(called_url.startswith("https://"))

    def test_browser_navigate_prefers_configured_browser(self):
        ex = _make_executor({"apps": {"browser": "C:/Program Files/Google/Chrome/Application/chrome.exe"}})

        with patch("src.jarvis.executor.subprocess.Popen") as mock_popen, patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_web_open:
            ex.browser_navigate("google.com")
            mock_popen.assert_called_once()
            args = mock_popen.call_args.args[0]
            self.assertIn("chrome.exe", args[0].lower())
            self.assertIn("https://google.com", args)
            mock_web_open.assert_not_called()

    def test_open_preferred_browser_uses_new_tab_when_running(self):
        ex = _make_executor({"apps": {"browser": "C:/Program Files/Google/Chrome/Application/chrome.exe"}})
        with patch.object(ex, "_is_preferred_browser_running", return_value=True), patch(
            "src.jarvis.executor.subprocess.Popen"
        ) as mock_popen:
            ok = ex._open_in_preferred_browser("https://google.com")
            self.assertTrue(ok)
            args = mock_popen.call_args.args[0]
            self.assertEqual(args[1], "--new-tab")
            self.assertEqual(args[2], "https://google.com")

    def test_open_preferred_browser_launches_when_not_running(self):
        ex = _make_executor({"apps": {"browser": "C:/Program Files/Google/Chrome/Application/chrome.exe"}})
        with patch.object(ex, "_is_preferred_browser_running", return_value=False), patch(
            "src.jarvis.executor.subprocess.Popen"
        ) as mock_popen:
            ok = ex._open_in_preferred_browser("https://google.com")
            self.assertTrue(ok)
            args = mock_popen.call_args.args[0]
            self.assertEqual(args[1], "https://google.com")
    
    def test_browser_search(self):
        ex = _make_executor({})
        
        with patch("src.jarvis.executor.subprocess.Popen", side_effect=RuntimeError("no browser")), patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_open:
            ex.browser_search("python")
            mock_open.assert_called_once()
            called_url = mock_open.call_args.args[0]
            self.assertIn("search", called_url)
            self.assertIn("python", called_url)

    def test_browser_navigate_resolves_site_alias(self):
        ex = _make_executor({"sites": {"ютуб": "www.youtube.com"}})

        with patch("src.jarvis.executor.subprocess.Popen", side_effect=RuntimeError("no browser")), patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_open:
            ex.browser_navigate("включи ютуб")
            mock_open.assert_called_once()
            called_url = mock_open.call_args.args[0]
            self.assertIn("youtube.com", called_url)

    def test_open_app_builtin_site_alias_teams(self):
        ex = _make_executor({})
        ex._ai_client = None  # проверяем локальный fallback без AI

        with patch("src.jarvis.executor.subprocess.Popen", side_effect=RuntimeError("no browser")), patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_open:
            ex.run({"type": "open_app", "slots": {"target": "microsoft teams"}})
            mock_open.assert_called_once()
            called_url = mock_open.call_args.args[0]
            self.assertIn("teams.microsoft.com", called_url)

    def test_browser_navigate_vk_phrase_falls_back_to_search_without_config(self):
        ex = _make_executor({})
        with patch.object(ex, "browser_search") as mock_search:
            ex.browser_navigate("вконтакте")
            mock_search.assert_called_once_with("вконтакте")

    def test_browser_navigate_non_domain_phrase_falls_back_to_search(self):
        ex = _make_executor({})
        with patch.object(ex, "browser_search") as mock_search:
            ex.browser_navigate("эпл мьюзик")
            mock_search.assert_called_once_with("эпл мьюзик")

    def test_open_app_falls_back_to_site_open(self):
        ex = _make_executor({"sites": {"ютуб": "www.youtube.com"}})

        with patch("src.jarvis.executor.subprocess.Popen", side_effect=RuntimeError("no browser")), patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_open:
            ex.run({"type": "open_app", "slots": {"target": "ютуб"}})
            mock_open.assert_called_once()
            called_url = mock_open.call_args.args[0]
            self.assertIn("youtube.com", called_url)

    def test_open_app_parses_youtube_channel_phrase(self):
        ex = _make_executor({})
        ex._ai_client = MagicMock()
        ex._ai_client.is_enabled.return_value = True

        with patch.object(
            ex,
            "_interpret_command_with_ai",
            return_value={
                "type": "browser_navigate",
                "slots": {"url": "https://www.youtube.com/results?search_query=mr+beast"},
            },
        ), patch("src.jarvis.executor.subprocess.Popen", side_effect=RuntimeError("no browser")), patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_open:
            ex.run({"type": "open_app", "slots": {"target": "mr beast youtube"}})
            mock_open.assert_called_once()
            called_url = mock_open.call_args.args[0]
            self.assertIn("youtube.com/results?search_query=", called_url)
            self.assertIn("mr+beast", called_url)

    def test_open_app_channel_phrase_defaults_to_youtube(self):
        ex = _make_executor({})
        ex._ai_client = MagicMock()
        ex._ai_client.is_enabled.return_value = True

        with patch.object(
            ex,
            "_interpret_command_with_ai",
            return_value={
                "type": "browser_navigate",
                "slots": {"url": "https://www.youtube.com/results?search_query=%D0%B4%D0%B8%D0%BC%D1%8B+%D0%BC%D0%B0%D1%81%D0%BB%D0%B5%D0%BD%D0%BD%D0%B8%D0%BA%D0%BE%D0%B2%D0%B0"},
            },
        ), patch("src.jarvis.executor.subprocess.Popen", side_effect=RuntimeError("no browser")), patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_open:
            ex.run({"type": "open_app", "slots": {"target": "канал димы масленникова"}})
            mock_open.assert_called_once()
            called_url = mock_open.call_args.args[0]
            self.assertIn("youtube.com/results?search_query=", called_url)

    def test_open_app_site_phrase_converts_search_to_main_url(self):
        ex = _make_executor({})
        ex._ai_client = MagicMock()
        ex._ai_client.is_enabled.return_value = True
        ex._resolve_site_home_url_with_ai = MagicMock(return_value="https://vk.com/")

        with patch.object(
            ex,
            "_interpret_command_with_ai",
            return_value={"type": "browser_search", "slots": {"query": "ВКонтакте"}},
        ), patch("src.jarvis.executor.subprocess.Popen", side_effect=RuntimeError("no browser")), patch(
            "src.jarvis.executor.webbrowser.open"
        ) as mock_open:
            ex.run({"type": "open_app", "slots": {"target": "сайт ВКонтакте"}})
            mock_open.assert_called_once()
            called_url = mock_open.call_args.args[0]
            self.assertEqual(called_url, "https://vk.com/")

    def test_google_search_url_encodes_cyrillic_query(self):
        ex = _make_executor({})
        url = ex._google_search_url("димы масленникова")
        self.assertIn("google.com/search", url)
        self.assertNotIn(" ", url)


class TestExecutorMediaCommands(unittest.TestCase):
    """Тесты для медиа-команд"""
    
    def test_media_play_routes_correctly(self):
        ex = _make_executor({})
        
        with patch("src.jarvis.executor.pyautogui") as mock_pyautogui:
            mock_pyautogui.press = unittest.mock.MagicMock()
            ex.media_play()
            # Проверяем, что метод вызывается когда pyautogui доступен
            if mock_pyautogui is not None:
                mock_pyautogui.press.assert_called_with('playpause')
    
    def test_media_next_routes_correctly(self):
        ex = _make_executor({})
        
        with patch("src.jarvis.executor.pyautogui") as mock_pyautogui:
            mock_pyautogui.press = unittest.mock.MagicMock()
            ex.media_next()
            if mock_pyautogui is not None:
                mock_pyautogui.press.assert_called_with('nexttrack')


class TestExecutorNotesCommands(unittest.TestCase):
    """Тесты для команд заметок"""
    
    def test_add_note_creates_file(self):
        ex = _make_executor({})
        
        with tempfile.TemporaryDirectory() as temp_dir:
            # Патчим Path.home() для тестирования
            with patch("pathlib.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                ex.add_note("тестовая заметка")
                
                notes_file = Path(temp_dir) / ".jarvis" / "notes.json"
                self.assertTrue(notes_file.exists())
    
    def test_create_reminder_creates_file(self):
        ex = _make_executor({})
        
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                ex.create_reminder("купить молоко")
                
                reminders_file = Path(temp_dir) / ".jarvis" / "reminders.json"
                self.assertTrue(reminders_file.exists())


if __name__ == "__main__":
    unittest.main()
