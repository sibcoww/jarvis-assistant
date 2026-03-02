"""
Plugin API для Jarvis Assistant.
Позволяет пользователям добавлять пользовательские команды.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class JarvisPlugin(ABC):
    """
    Базовый класс для всех плагинов Jarvis.
    
    Пример использования:
    
    class MyPlugin(JarvisPlugin):
        def get_info(self):
            return {
                "name": "My Plugin",
                "version": "1.0.0",
                "author": "Your Name"
            }
        
        def get_intents(self):
            return {
                "my_intent": ["my command pattern"]
            }
        
        def handle(self, intent_type: str, slots: Dict) -> bool:
            if intent_type == "my_intent":
                print(f"Handling my_intent with slots: {slots}")
                return True
            return False
    """
    
    @abstractmethod
    def get_info(self) -> Dict[str, str]:
        """
        Возвращает информацию о плагине.
        
        Returns:
            Dict с полями: name, version, author, description
        """
        pass
    
    @abstractmethod
    def get_intents(self) -> Dict[str, list]:
        """
        Возвращает словарь интентов и их шаблонов.
        
        Returns:
            {
                "intent_name": ["pattern1", "pattern2"],
                "another_intent": ["pattern3"]
            }
        """
        pass
    
    @abstractmethod
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        """
        Обрабатывает интент.
        
        Args:
            intent_type: Тип интента
            slots: Параметры интента
            
        Returns:
            True если интент был обработан, False иначе
        """
        pass
    
    def initialize(self):
        """Опциональный метод инициализации плагина"""
        pass
    
    def shutdown(self):
        """Опциональный метод при отключении плагина"""
        pass


class PluginManager:
    """Менеджер для загрузки и управления плагинами"""
    
    def __init__(self):
        self.plugins: Dict[str, JarvisPlugin] = {}
        self.intents: Dict[str, tuple] = {}  # intent_type -> (plugin, patterns)
    
    def load_plugin(self, plugin: JarvisPlugin) -> bool:
        """
        Загрузить плагин.
        
        Args:
            plugin: Экземпляр плагина (наследник JarvisPlugin)
            
        Returns:
            True если успешно, False если ошибка
        """
        try:
            info = plugin.get_info()
            name = info.get("name", "Unknown")
            
            # Инициализируем плагин
            plugin.initialize()
            
            # Добавляем его в список
            self.plugins[name] = plugin
            
            # Регистрируем интенты
            intents = plugin.get_intents()
            for intent_type, patterns in intents.items():
                self.intents[intent_type] = (plugin, patterns)
            
            logger.info(f"✓ Плагин загружен: {name} v{info.get('version', '?')}")
            return True
        except Exception as e:
            logger.error(f"✗ Ошибка загрузки плагина: {e}")
            return False
    
    def unload_plugin(self, plugin_name: str) -> bool:
        """Выгрузить плагин"""
        try:
            if plugin_name not in self.plugins:
                logger.warning(f"Плагин не найден: {plugin_name}")
                return False
            
            plugin = self.plugins[plugin_name]
            plugin.shutdown()
            
            # Удаляем интенты плагина
            self.intents = {
                intent: (p, patterns) 
                for intent, (p, patterns) in self.intents.items() 
                if p != plugin
            }
            
            del self.plugins[plugin_name]
            logger.info(f"✓ Плагин выгружен: {plugin_name}")
            return True
        except Exception as e:
            logger.error(f"✗ Ошибка выгрузки плагина: {e}")
            return False
    
    def handle_intent(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        """
        Попытаться обработать интент через плагины.
        
        Args:
            intent_type: Тип интента
            slots: Параметры интента
            
        Returns:
            True если плагин обработал интент
        """
        if intent_type not in self.intents:
            return False
        
        plugin, patterns = self.intents[intent_type]
        try:
            return plugin.handle(intent_type, slots)
        except Exception as e:
            logger.error(f"Ошибка обработки интента {intent_type}: {e}")
            return False
    
    def get_available_intents(self) -> list:
        """Получить список всех доступных интентов от плагинов"""
        return list(self.intents.keys())
    
    def get_plugin_info(self, plugin_name: str) -> Optional[Dict]:
        """Получить информацию о плагине"""
        if plugin_name not in self.plugins:
            return None
        return self.plugins[plugin_name].get_info()
    
    def list_plugins(self) -> Dict[str, Dict]:
        """Получить список всех загруженных плагинов"""
        return {
            name: plugin.get_info()
            for name, plugin in self.plugins.items()
        }
