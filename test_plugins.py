#!/usr/bin/env python3
"""
Test system for Jarvis Assistant plugins
"""

import logging
import sys
import os

# Fix Unicode encoding on Windows
if os.name == 'nt':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

from src.jarvis.plugin_api import JarvisPlugin, PluginManager
from src.jarvis.executor import Executor
from typing import Dict, Any


# Создадим тестовый плагин
class TestPlugin(JarvisPlugin):
    """Простой тестовый плагин"""
    
    def get_info(self) -> Dict[str, str]:
        return {
            "name": "Test Plugin",
            "version": "1.0.0",
            "author": "Test Author",
            "description": "Плагин для тестирования"
        }
    
    def get_intents(self) -> Dict[str, list]:
        return {
            "test_hello": ["привет", "хей", "hello"],
            "test_calc": ["посчитай"]
        }
    
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        if intent_type == "test_hello":
            print(f"✓ [Plugin] Обработан интент: {intent_type}")
            return True
        elif intent_type == "test_calc":
            print(f"✓ [Plugin] Результат: 2 + 2 = 4")
            return True
        return False


def test_plugin_manager():
    """Тест PluginManager"""
    print("\n=== Тест 1: PluginManager ===")
    
    pm = PluginManager()
    plugin = TestPlugin()
    
    # Загрузим плагин
    assert pm.load_plugin(plugin), "Ошибка загрузки плагина"
    print(f"Загруженные плагины: {list(pm.plugins.keys())}")
    print(f"Доступные интенты: {pm.get_available_intents()}")
    
    # Обработаем интент
    result = pm.handle_intent("test_hello", {})
    assert result, "Плагин не обработал интент"
    print("✓ Плагин обработал интент test_hello")
    
    # Попытаемся обработать несуществующий интент
    result = pm.handle_intent("unknown_intent", {})
    assert not result, "Плагин обработал несуществующий интент"
    print("✓ Плагин правильно отказал для неизвестного интента")
    
    # Выгрузим плагин
    assert pm.unload_plugin("Test Plugin"), "Ошибка выгрузки плагина"
    assert len(pm.plugins) == 0, "Плагин не был выгружен"
    print("✓ Плагин успешно выгружен")


def test_executor_with_plugins():
    """Тест интеграции плагинов с Executor"""
    print("\n=== Тест 2: Executor с плагинами ===")
    
    ex = Executor()
    plugin = TestPlugin()
    
    # Загрузим плагин в executor
    assert ex.plugin_manager.load_plugin(plugin), "Ошибка загрузки плагина"
    
    # Запустим встроенную команду (не плагина)
    print("\n• Команда встроенная (громкость):")
    ex.run({"type": "set_volume", "slots": {"value": 50}})
    
    # Запустим команду из плагина
    print("\n• Команда из плагина (test_hello):")
    ex.run({"type": "test_hello", "slots": {}})
    
    print("\n• Неизвестная команда:")
    ex.run({"type": "unknown", "slots": {}})


def test_example_plugins():
    """Тест примеров плагинов"""
    print("\n=== Тест 3: Примеры плагинов ===")
    
    from src.jarvis.example_plugins import (
        WeatherPlugin, CalculatorPlugin, ReminderNotificationPlugin,
        NewsPlugin, MusicPlugin
    )
    
    plugins = [
        WeatherPlugin(),
        CalculatorPlugin(),
        ReminderNotificationPlugin(),
        NewsPlugin(),
        MusicPlugin()
    ]
    
    pm = PluginManager()
    
    for plugin in plugins:
        info = plugin.get_info()
        print(f"\n• Загружаю: {info['name']} v{info['version']}")
        pm.load_plugin(plugin)
        
        # Показываем доступные интенты
        intents = plugin.get_intents()
        print(f"  Интенты: {list(intents.keys())}")
    
    print(f"\n✓ Загружено плагинов: {len(pm.plugins)}")
    print(f"✓ Всего интентов: {len(pm.get_available_intents())}")
    
    # Протестируем каждый плагин
    print("\n• Тестирование интентов:")
    test_cases = [
        ("weather_current", {}),
        ("calc_simple", {}),
        ("remind_in", {}),
        ("news_latest", {}),
        ("music_playlist", {})
    ]
    
    for intent_type, slots in test_cases:
        result = pm.handle_intent(intent_type, slots)
        status = "[OK]" if result else "[FAIL]"
        print(f"  {status} {intent_type}")


if __name__ == "__main__":
    try:
        test_plugin_manager()
        test_executor_with_plugins()
        test_example_plugins()
        
        print("\n" + "="*50)
        print("[OK] ALL TESTS PASSED!")
        print("="*50)
    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        exit(1)
