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
            logger.warning(f"config.json не найден: {config_path}")
            return {}
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
            
            # Валидация структуры конфига
            self._validate_config(config_data, config_path)
            
            # Разрешаем переменные окружения в путях
            if "apps" in config_data:
                for key, path in config_data["apps"].items():
                    expanded_path = os.path.expandvars(path)
                    # Проверяем, существует ли приложение
                    if not Path(expanded_path).exists():
                        logger.warning(f"Приложение не найдено: {key} -> {expanded_path}")
                    config_data["apps"][key] = expanded_path
            
            logger.info(f"Config загружен: {len(config_data.get('apps', {}))} приложений, "
                       f"{len(config_data.get('scenarios', {}))} сценариев")
            return config_data
        except json.JSONDecodeError as error:
            logger.error(f"Ошибка парсинга config.json: {error}")
            return {}
        except Exception as error:
            logger.error(f"Не удалось загрузить config.json: {error}")
            return {}
    
    @staticmethod
    def _validate_config(config_data: dict, config_path: Path) -> None:
        """
        Валидирует структуру config.json.
        
        Args:
            config_data: Загруженные данные конфигурации
            config_path: Путь к файлу конфига
            
        Raises:
            ValueError: Если конфиг имеет неверную структуру
        """
        errors = []
        
        # Проверка apps
        if "apps" in config_data:
            if not isinstance(config_data["apps"], dict):
                errors.append("'apps' должен быть объектом")
            else:
                for name, path in config_data["apps"].items():
                    if not isinstance(name, str):
                        errors.append(f"Имя приложения должно быть строкой: {name}")
                    if not isinstance(path, str):
                        errors.append(f"Путь приложения должен быть строкой: {name}={path}")
        
        # Проверка synonyms
        if "synonyms" in config_data:
            if not isinstance(config_data["synonyms"], dict):
                errors.append("'synonyms' должен быть объектом")
            else:
                for syn, target in config_data["synonyms"].items():
                    if not isinstance(syn, str):
                        errors.append(f"Синоним должен быть строкой: {syn}")
                    if not isinstance(target, str):
                        errors.append(f"Цель синонима должна быть строкой: {syn}={target}")
        
        # Проверка scenarios
        if "scenarios" in config_data:
            if not isinstance(config_data["scenarios"], dict):
                errors.append("'scenarios' должен быть объектом")
            else:
                for name, actions in config_data["scenarios"].items():
                    if not isinstance(name, str):
                        errors.append(f"Имя сценария должно быть строкой: {name}")
                    if not isinstance(actions, list):
                        errors.append(f"Действия сценария должны быть списком: {name}")
        
        if errors:
            error_msg = "\n".join(errors)
            logger.warning(f"Ошибки валидации config.json:\n{error_msg}")

    def _resolve_target(self, target: str) -> str:
        normalized = target.strip().lower()
        synonyms = self.config.get("synonyms", {})
        return synonyms.get(normalized, normalized)

    def _get_volume_endpoint(self):
        """Получить эндпоинт громкости с обработкой ошибок"""
        try:
            speakers = AudioUtilities.GetSpeakers()
            endpoint_volume = getattr(speakers, "EndpointVolume", None)
            if endpoint_volume is not None:
                return endpoint_volume.QueryInterface(IAudioEndpointVolume)

            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(interface, POINTER(IAudioEndpointVolume))
        except Exception as e:
            logger.error(f"Ошибка получения эндпоинта громкости: {e}")
            raise

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
        try:
            value = max(0, min(100, int(value)))
            volume = self._get_volume_endpoint()
            volume.SetMasterVolumeLevelScalar(value / 100.0, None)
            logger.info(f"Громкость установлена на {value}%")
        except Exception as e:
            logger.error(f"Ошибка установки громкости: {e}")

    def change_volume(self, delta: int):
        try:
            volume = self._get_volume_endpoint()
            current = int(round(volume.GetMasterVolumeLevelScalar() * 100))
            self.set_volume(current + delta)
        except Exception as e:
            logger.error(f"Ошибка изменения громкости: {e}")

    def create_folder(self, name: str):
        try:
            folder_name = name.strip().strip("\"'")
            if not folder_name:
                logger.warning("Имя папки пустое")
                return

            folder_path = Path.cwd() / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Папка готова: {folder_path}")
        except Exception as e:
            logger.error(f"Ошибка создания папки: {e}")

    def run_scenario(self, name: str):
        try:
            scenarios = self.config.get("scenarios", {})
            if name not in scenarios:
                logger.warning(f"Сценарий не найден: {name}")
                return

            for action in scenarios[name]:
                try:
                    if action.startswith("open:"):
                        self.open_app(action.split(":", 1)[1])
                except Exception as e:
                    logger.error(f"Ошибка выполнения действия {action}: {e}")
        except Exception as e:
            logger.error(f"Ошибка выполнения сценария: {e}")

    def run(self, intent: dict):
        try:
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
        except Exception as e:
            logger.error(f"Ошибка выполнения команды: {e}")
