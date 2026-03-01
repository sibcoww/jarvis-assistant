import json
import subprocess
import logging
import os
from pathlib import Path
from ctypes import cast, POINTER

from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self, config=None):
        self.config = config or self._load_default_config()

    def _load_default_config(self) -> dict:
        config_path = Path(__file__).with_name("config.json")
        if not config_path.exists():
            return {}
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            # Разрешаем переменные окружения в путях
            if "apps" in config_data:
                for key, path in config_data["apps"].items():
                    config_data["apps"][key] = os.path.expandvars(path)
            return config_data
        except Exception as error:
            logger.error(f"Не удалось загрузить config.json: {error}")
            return {}

    def _resolve_target(self, target: str) -> str:
        normalized = target.strip().lower()
        synonyms = self.config.get("synonyms", {})
        return synonyms.get(normalized, normalized)

    def _get_volume_endpoint(self):
        speakers = AudioUtilities.GetSpeakers()
        endpoint_volume = getattr(speakers, "EndpointVolume", None)
        if endpoint_volume is not None:
            return endpoint_volume.QueryInterface(IAudioEndpointVolume)

        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def open_app(self, target: str):
        target = self._resolve_target(target)
        apps = self.config.get("apps", {})
        if target in apps:
            cmd_path = apps[target]
            try:
                # Безопасный запуск без shell=True
                subprocess.Popen([cmd_path], shell=False)
                logger.info(f"Запускаю: {cmd_path}")
            except Exception as e:
                logger.error(f"Ошибка запуска приложения {cmd_path}: {e}")
        else:
            logger.warning(f"Неизвестное приложение: {target}")

    def set_volume(self, value: int):
        value = max(0, min(100, int(value)))
        volume = self._get_volume_endpoint()
        volume.SetMasterVolumeLevelScalar(value / 100.0, None)
        logger.info(f"Громкость установлена на {value}%")

    def change_volume(self, delta: int):
        volume = self._get_volume_endpoint()
        current = int(round(volume.GetMasterVolumeLevelScalar() * 100))
        self.set_volume(current + delta)

    def create_folder(self, name: str):
        folder_name = name.strip().strip("\"'")
        if not folder_name:
            logger.warning("Имя папки пустое")
            return

        folder_path = Path.cwd() / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Папка готова: {folder_path}")

    def run_scenario(self, name: str):
        scenarios = self.config.get("scenarios", {})
        if name not in scenarios:
            logger.warning(f"Сценарий не найден: {name}")
            return

        for action in scenarios[name]:
            if action.startswith("open:"):
                self.open_app(action.split(":", 1)[1])

    def run(self, intent: dict):
        t = intent["type"]
        slots = intent.get("slots", {})

        if t == "set_volume":
            self.set_volume(slots.get("value", 50))
            return

        if t == "volume_up":
            self.change_volume(abs(int(slots.get("delta", 10))))
            return

        if t == "volume_down":
            self.change_volume(-abs(int(slots.get("delta", 10))))
            return

        if t == "open_app":
            self.open_app(slots.get("target", ""))
            return

        if t == "run_scenario":
            self.run_scenario(slots.get("name", ""))
            return

        if t == "create_folder":
            self.create_folder(slots.get("name", ""))
            return

        logger.warning(f"Не понял команду: {intent}")
