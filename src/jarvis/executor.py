import subprocess
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL


class Executor:
    def __init__(self, config=None):
        self.config = config or {}

    def open_app(self, target: str):
        apps = self.config.get("apps", {})
        if target in apps:
            subprocess.Popen(apps[target], shell=True)
            print(f"Запускаю: {apps[target]}")
        else:
            print(f"Неизвестное приложение: {target}")

    def set_volume(self, value: int):
        device = AudioUtilities.GetSpeakers()
        volume = device.EndpointVolume.QueryInterface(IAudioEndpointVolume)
        volume.SetMasterVolumeLevelScalar(value / 100.0, None)
        print(f"Громкость установлена на {value}%")

    def run_scenario(self, name: str):
        scenarios = self.config.get("scenarios", {})
        if name not in scenarios:
            print(f"Сценарий не найден: {name}")
            return

        for action in scenarios[name]:
            if action.startswith("open:"):
                self.open_app(action.split(":", 1)[1])

    def run(self, intent: dict):
        t = intent["type"]

        if t == "set_volume":
            self.set_volume(intent["slots"]["value"])
            return

        if t == "open_app":
            self.open_app(intent["slots"]["target"])
            return

        if t == "run_scenario":
            self.run_scenario(intent["slots"]["name"])
            return

        print("Не понял команду:", intent)
