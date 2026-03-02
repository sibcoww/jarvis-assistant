# 🔌 Система плагинов Jarvis Assistant

Jarvis теперь поддерживает расширяемую систему плагинов! Пользователи могут создавать свои собственные команды без изменения основного кода.

## 📚 Быстрый старт

### 1. Создайте свой плагин

```python
from src.jarvis.plugin_api import JarvisPlugin
from typing import Dict, Any

class MyPlugin(JarvisPlugin):
    """Мой первый плагин"""
    
    def get_info(self) -> Dict[str, str]:
        return {
            "name": "My Plugin",
            "version": "1.0.0",
            "author": "Your Name",
            "description": "Описание моего плагина"
        }
    
    def get_intents(self) -> Dict[str, list]:
        """Определяем интенты и шаблоны команд"""
        return {
            "my_command": [
                "мая команда",
                "запусти мою команду",
                "my command"
            ],
            "another_intent": [
                "другая команда"
            ]
        }
    
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        """Обрабатываем интент"""
        if intent_type == "my_command":
            print("Выполняю мою команду!")
            return True
        
        elif intent_type == "another_intent":
            value = slots.get("value", "default")
            print(f"Другая команда с параметром: {value}")
            return True
        
        return False
    
    def initialize(self):
        """Вызывается при загрузке плагина"""
        print("Плагин инициализирован!")
    
    def shutdown(self):
        """Вызывается при выгрузке плагина"""
        print("Плагин выключен!")
```

### 2. Загрузите плагин в Executor

```python
from src.jarvis.executor import Executor
from my_plugin import MyPlugin

# Создаём executor
ex = Executor(enable_tts=False)

# Загружаем плагин
plugin = MyPlugin()
ex.plugin_manager.load_plugin(plugin)

# Теперь можно использовать команды плагина
ex.run({
    "type": "my_command",
    "slots": {}
})
```

### 3. Используйте в GUI

```python
from src.jarvis.engine import JarvisEngine
from my_plugin import MyPlugin

engine = JarvisEngine()
plugin = MyPlugin()
engine.ex.plugin_manager.load_plugin(plugin)

# Плагин будет работать через обычный движок
```

## 🏗️ Архитектура плагинов

```
JarvisPlugin (базовый класс)
    ├─ get_info()       → Информация о плагине
    ├─ get_intents()    → Карта интентов
    ├─ handle()         → Обработка команд
    ├─ initialize()     → Инициализация (опционально)
    └─ shutdown()       → Завершение (опционально)

PluginManager
    ├─ load_plugin()      → Загрузить плагин
    ├─ unload_plugin()    → Выгрузить плагин
    ├─ handle_intent()    → Обработать интент
    └─ list_plugins()     → Список загруженных плагинов
```

## 📦 Встроенные примеры плагинов

### WeatherPlugin
Получение информации о погоде.

```
Интенты:
  • weather_current: "какая погода"
  • weather_tomorrow: "погода завтра"
```

### CalculatorPlugin
Математические вычисления.

```
Интенты:
  • calc_simple: "сколько будет два плюс два"
```

### ReminderNotificationPlugin
Расширенные напоминания с уведомлениями.

```
Интенты:
  • remind_in: "напомни мне через час"
  • remind_list: "список напоминаний"
```

### NewsPlugin
Получение новостей.

```
Интенты:
  • news_latest: "какие новости"
  • news_tech: "технические новости"
```

### MusicPlugin
Управление музыкой и плейлистами.

```
Интенты:
  • music_playlist: "включи плейлист релакс"
  • music_artist: "музыка от The Beatles"
```

## 🔧 Расширенный пример

### Плагин для контроля умного дома

```python
from src.jarvis.plugin_api import JarvisPlugin
import requests

class SmartHomePlugin(JarvisPlugin):
    """Контроль устройств умного дома"""
    
    def __init__(self, api_url="http://localhost:8000"):
        self.api_url = api_url
    
    def get_info(self) -> Dict[str, str]:
        return {
            "name": "Smart Home",
            "version": "1.0.0",
            "author": "Home Automation",
            "description": "Управление устройствами умного дома"
        }
    
    def get_intents(self) -> Dict[str, list]:
        return {
            "home_light": [
                "включи свет",
                "свет на",
                "свет",
                "выключи свет"
            ],
            "home_temp": [
                "установи температуру 22",
                "сделай теплее",
                "сделай холоднее"
            ],
            "home_security": [
                "включи сигнализацию",
                "выключи сигнализацию",
                "проверь двери"
            ]
        }
    
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        try:
            if intent_type == "home_light":
                self._control_light(slots)
                return True
            
            elif intent_type == "home_temp":
                self._control_temperature(slots)
                return True
            
            elif intent_type == "home_security":
                self._control_security(slots)
                return True
        
        except Exception as e:
            print(f"Ошибка управления умным домом: {e}")
        
        return False
    
    def _control_light(self, slots):
        # Отправляем команду на API
        response = requests.post(f"{self.api_url}/light/toggle")
        print(f"💡 Свет переключен")
    
    def _control_temperature(self, slots):
        temp = slots.get("temp", 22)
        requests.post(f"{self.api_url}/thermostat/set", json={"temp": temp})
        print(f"🌡️ Температура установлена на {temp}°C")
    
    def _control_security(self, slots):
        requests.post(f"{self.api_url}/security/check")
        print(f"🔒 Проверка безопасности выполнена")
```

## 📊 API плагинов

### Методы JarvisPlugin

| Метод | Возвращает | Описание |
|-------|-----------|---------|
| `get_info()` | `Dict` | Информация о плагине |
| `get_intents()` | `Dict` | Карта интентов |
| `handle(intent_type, slots)` | `bool` | Обработка команды |
| `initialize()` | - | Инициализация (опционально) |
| `shutdown()` | - | Завершение (опционально) |

### Методы PluginManager

| Метод | Описание |
|-------|---------|
| `load_plugin(plugin)` | Загрузить плагин |
| `unload_plugin(name)` | Выгрузить плагин |
| `handle_intent(type, slots)` | Обработать интент |
| `get_available_intents()` | Список интентов |
| `list_plugins()` | Все плагины |
| `get_plugin_info(name)` | Информация о плагине |

## 🎯 Лучшие практики

1. **Имена интентов** — используйте `plugin_intent` формат
2. **Обработка ошибок** — всегда используйте try-except
3. **Логирование** — используйте `logger` для логирования
4. **Асинхронность** — используйте threading для длительных операций
5. **Документация** — описывайте интенты и параметры

## 📝 Полный пример использования

```python
import logging
from src.jarvis.executor import Executor
from src.jarvis.example_plugins import WeatherPlugin, NewsPlugin

logging.basicConfig(level=logging.INFO)

# Создаём executor
ex = Executor(enable_tts=False)

# Загружаем плагины
weather = WeatherPlugin()
news = NewsPlugin()

ex.plugin_manager.load_plugin(weather)
ex.plugin_manager.load_plugin(news)

# Используем команды из плагинов
ex.run({"type": "weather_current", "slots": {}})
ex.run({"type": "news_latest", "slots": {}})

# Смотрим доступные интенты
print(f"Доступные интенты: {ex.plugin_manager.get_available_intents()}")

# Смотрим загруженные плагины
print(f"Плагины: {list(ex.plugin_manager.list_plugins().keys())}")
```

## 🚀 Тестирование плагинов

Запустите встроенный тест:

```bash
python test_plugins.py
```

## 📚 Дополнительные ресурсы

- `src/jarvis/plugin_api.py` — API плагинов
- `src/jarvis/example_plugins.py` — примеры плагинов
- `test_plugins.py` — тесты системы
