import os
import tempfile
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
