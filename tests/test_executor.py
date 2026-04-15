import os
import json
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta
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

    def test_run_routes_open_known_programs(self):
        ex = _make_executor(
            {
                "apps": {
                    "browser": "C:/Program Files/Google/Chrome/Application/chrome.exe",
                    "telegram": "C:/Apps/Telegram/Telegram.exe",
                    "vscode": "C:/Apps/VSCode/Code.exe",
                    "notepad": "notepad.exe",
                },
                "synonyms": {},
            }
        )
        cases = [
            ("browser", "chrome.exe"),
            ("telegram", "telegram.exe"),
            ("vscode", "code.exe"),
            ("notepad", "notepad.exe"),
        ]
        for target, expected_exe in cases:
            with self.subTest(target=target):
                if target == "browser":
                    with patch("src.jarvis.executor.webbrowser.open") as mock_open:
                        ex.run({"type": "open_app", "slots": {"target": target}})
                        mock_open.assert_called_once()
                    continue
                with patch("src.jarvis.executor.subprocess.Popen") as mock_popen:
                    ex.run({"type": "open_app", "slots": {"target": target}})
                    mock_popen.assert_called_once()
                    launched_cmd = [str(x).lower() for x in mock_popen.call_args.args[0]]
                    self.assertTrue(any(expected_exe in token for token in launched_cmd))

    def test_run_routes_close_app(self):
        ex = _make_executor(
            {
                "apps": {"telegram": "Telegram.exe"},
                "synonyms": {"телеграм": "telegram"},
            }
        )
        with patch.object(ex, "close_app") as mock_close:
            ex.run({"type": "close_app", "slots": {"target": "телеграм"}})
            mock_close.assert_called_once_with("телеграм")

    def test_close_app_logs_when_psutil_missing(self):
        ex = _make_executor({})
        messages = []
        ex._log = lambda msg: messages.append(msg)
        with patch("src.jarvis.executor.psutil", None):
            ex.close_app("telegram")
        self.assertTrue(any("psutil" in m.lower() for m in messages))

    def test_system_actions_require_confirmation(self):
        ex = _make_executor({})
        self.assertTrue(ex.should_require_confirmation({"type": "shutdown_pc", "slots": {}}))
        self.assertTrue(ex.should_require_confirmation({"type": "restart_pc", "slots": {}}))
        self.assertTrue(ex.should_require_confirmation({"type": "sleep_pc", "slots": {}}))
        self.assertFalse(ex.should_require_confirmation({"type": "lock_pc", "slots": {}}))

    def test_run_routes_system_actions(self):
        ex = _make_executor({})
        with patch.object(ex, "shutdown_pc") as m_shutdown:
            ex.run({"type": "shutdown_pc", "slots": {}})
            m_shutdown.assert_called_once()
        with patch.object(ex, "restart_pc") as m_restart:
            ex.run({"type": "restart_pc", "slots": {}})
            m_restart.assert_called_once()
        with patch.object(ex, "sleep_pc") as m_sleep:
            ex.run({"type": "sleep_pc", "slots": {}})
            m_sleep.assert_called_once()
        with patch.object(ex, "lock_pc") as m_lock:
            ex.run({"type": "lock_pc", "slots": {}})
            m_lock.assert_called_once()
        with patch.object(ex, "show_weather") as m_weather:
            ex.run({"type": "show_weather", "slots": {"city": "astana"}})
            m_weather.assert_called_once_with("astana")

    def test_close_app_terminates_matching_known_programs(self):
        ex = _make_executor(
            {
                "apps": {
                    "browser": "C:/Program Files/Google/Chrome/Application/chrome.exe",
                    "telegram": "C:/Apps/Telegram/Telegram.exe",
                    "vscode": "C:/Apps/VSCode/Code.exe",
                    "notepad": "notepad.exe",
                },
                "synonyms": {},
            }
        )

        class FakeProc:
            def __init__(self, pid, name):
                self.info = {"pid": pid, "name": name, "exe": "", "cmdline": []}
                self.terminated = False

            def terminate(self):
                self.terminated = True

        for target, exe_name in [
            ("browser", "chrome.exe"),
            ("telegram", "telegram.exe"),
            ("vscode", "code.exe"),
            ("notepad", "notepad.exe"),
        ]:
            proc = FakeProc(99999, exe_name)
            with self.subTest(target=target), patch(
                "src.jarvis.executor.psutil.process_iter",
                return_value=[proc],
            ):
                ex.close_app(target)
                self.assertTrue(proc.terminated)

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

    def test_open_app_video_query_falls_back_to_youtube(self):
        ex = _make_executor({})
        ex._ai_client = None
        with patch.object(ex, "browser_navigate") as mock_nav:
            ex.run({"type": "open_app", "slots": {"target": "видео про fastapi"}})
            mock_nav.assert_called_once()
            called_url = mock_nav.call_args.args[0]
            self.assertIn("youtube.com/results", called_url)

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

    def test_presentation_controls_route_correctly(self):
        ex = _make_executor({})
        with patch("src.jarvis.executor.pyautogui") as mock_pyautogui:
            mock_pyautogui.press = unittest.mock.MagicMock()
            ex.presentation_next_slide()
            mock_pyautogui.press.assert_called_with("right")
            ex.presentation_previous_slide()
            mock_pyautogui.press.assert_called_with("left")
            ex.presentation_start()
            mock_pyautogui.press.assert_called_with("f5")
            ex.presentation_end()
            mock_pyautogui.press.assert_called_with("esc")

    def test_window_controls_route_correctly(self):
        ex = _make_executor({})
        with patch("src.jarvis.executor.pyautogui") as mock_pyautogui:
            mock_pyautogui.press = unittest.mock.MagicMock()
            mock_pyautogui.hotkey = unittest.mock.MagicMock()
            ex.window_snap_left()
            mock_pyautogui.hotkey.assert_called_with("winleft", "left")
            ex.window_snap_right()
            mock_pyautogui.hotkey.assert_called_with("winleft", "right")
            ex.window_split_two()
            self.assertTrue(mock_pyautogui.hotkey.call_count >= 2)

    def test_repeat_last_command_replays_previous_action(self):
        ex = _make_executor({})
        ex._action_history = [
            {"type": "show_action_history", "slots": {}, "ts": datetime.now().isoformat()},
            {"type": "window_snap_left", "slots": {}, "ts": datetime.now().isoformat()},
        ]
        with patch.object(ex, "window_snap_left") as mock_left:
            ex.repeat_last_command()
            mock_left.assert_called_once()


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

    def test_create_reminder_parses_relative_time(self):
        ex = _make_executor({})
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                ex.create_reminder("10 минут купить молоко")
                reminders_file = Path(temp_dir) / ".jarvis" / "reminders.json"
                data = json.loads(reminders_file.read_text(encoding="utf-8"))
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]["text"], "купить молоко")
                self.assertTrue(data[0].get("due_at"))

    def test_pop_due_reminders_marks_done(self):
        ex = _make_executor({})
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                reminders_file = Path(temp_dir) / ".jarvis" / "reminders.json"
                reminders_file.parent.mkdir(parents=True, exist_ok=True)
                payload = [
                    {
                        "text": "проверить отчёт",
                        "created": datetime.now().isoformat(),
                        "due_at": (datetime.now() - timedelta(minutes=1)).isoformat(),
                        "done": False,
                    }
                ]
                reminders_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                due = ex.pop_due_reminders()
                self.assertEqual(due, ["проверить отчёт"])
                updated = json.loads(reminders_file.read_text(encoding="utf-8"))
                self.assertTrue(updated[0]["done"])

    def test_add_todo_creates_file(self):
        ex = _make_executor({})
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                ex.add_todo("купить молоко")
                todos_file = Path(temp_dir) / ".jarvis" / "todos.json"
                self.assertTrue(todos_file.exists())
                data = json.loads(todos_file.read_text(encoding="utf-8"))
                self.assertEqual(data[0]["text"], "купить молоко")
                self.assertFalse(data[0]["done"])

    def test_complete_todo_marks_done_by_number(self):
        ex = _make_executor({})
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                todos_file = Path(temp_dir) / ".jarvis" / "todos.json"
                todos_file.parent.mkdir(parents=True, exist_ok=True)
                todos_file.write_text(
                    json.dumps(
                        [
                            {"text": "первая", "created": datetime.now().isoformat(), "done": False},
                            {"text": "вторая", "created": datetime.now().isoformat(), "done": False},
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                ex.complete_todo("2")
                data = json.loads(todos_file.read_text(encoding="utf-8"))
                self.assertFalse(data[0]["done"])
                self.assertTrue(data[1]["done"])

    def test_delete_todo_removes_by_substring(self):
        ex = _make_executor({})
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home") as mock_home:
                mock_home.return_value = Path(temp_dir)
                todos_file = Path(temp_dir) / ".jarvis" / "todos.json"
                todos_file.parent.mkdir(parents=True, exist_ok=True)
                todos_file.write_text(
                    json.dumps(
                        [
                            {"text": "купить молоко", "created": datetime.now().isoformat(), "done": False},
                            {"text": "позвонить маме", "created": datetime.now().isoformat(), "done": False},
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                ex.delete_todo("молоко")
                data = json.loads(todos_file.read_text(encoding="utf-8"))
                self.assertEqual(len(data), 1)
                self.assertEqual(data[0]["text"], "позвонить маме")

    def test_start_timer_sets_active_timer(self):
        ex = _make_executor({})
        ex.start_timer(1, "минут", "чай")
        self.assertIsNotNone(ex._active_timer)
        self.assertEqual(ex._active_timer["label"], "чай")

    def test_cancel_timer_clears_active_timer(self):
        ex = _make_executor({})
        ex.start_timer(10, "секунд", "")
        ex.cancel_timer()
        self.assertIsNone(ex._active_timer)

    def test_pop_due_timers_returns_fired_label(self):
        ex = _make_executor({})
        ex.start_timer(1, "секунд", "паста")
        # форсируем завершение
        ex._active_timer["end_ts"] = 0
        fired = ex.pop_due_timers()
        self.assertEqual(fired, ["паста"])
        self.assertIsNone(ex._active_timer)


if __name__ == "__main__":
    unittest.main()
