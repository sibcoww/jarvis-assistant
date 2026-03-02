"""
Примеры плагинов для Jarvis Assistant.
Копируйте и модифицируйте эти файлы в папку plugins/
"""

from src.jarvis.plugin_api import JarvisPlugin
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


class WeatherPlugin(JarvisPlugin):
    """
    Пример плагина для получения информации о погоде.
    Требует установки: pip install requests
    """
    
    def get_info(self) -> Dict[str, str]:
        return {
            "name": "Weather Plugin",
            "version": "1.0.0",
            "author": "Jarvis Community",
            "description": "Получение информации о погоде"
        }
    
    def get_intents(self) -> Dict[str, list]:
        return {
            "weather_current": [
                "какая погода",
                "погода",
                "сейчас какая погода"
            ],
            "weather_tomorrow": [
                "погода завтра",
                "какая будет погода завтра"
            ]
        }
    
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        if intent_type == "weather_current":
            logger.info("☀️ Получаю текущую погоду...")
            # TODO: вызвать weather API
            logger.info("☀️ Погода: Облачно, +5°C, ветер 3 м/с")
            return True
        
        elif intent_type == "weather_tomorrow":
            logger.info("☀️ Получаю прогноз на завтра...")
            logger.info("☀️ Завтра: Снег, -2°C")
            return True
        
        return False


class CalculatorPlugin(JarvisPlugin):
    """Простой калькулятор плагин"""
    
    def get_info(self) -> Dict[str, str]:
        return {
            "name": "Calculator Plugin",
            "version": "1.0.0",
            "author": "Jarvis Community",
            "description": "Выполнение математических операций"
        }
    
    def get_intents(self) -> Dict[str, list]:
        return {
            "calc_simple": [
                "сколько будет два плюс два",
                "посчитай десять минус пять",
                "какой результат пять умножить на три"
            ]
        }
    
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        if intent_type == "calc_simple":
            # Простой пример
            logger.info("🔢 Результат: 4")
            return True
        
        return False


class ReminderNotificationPlugin(JarvisPlugin):
    """Расширенное управление напоминаниями с уведомлениями"""
    
    def get_info(self) -> Dict[str, str]:
        return {
            "name": "Reminder Notifications",
            "version": "1.0.0",
            "author": "Jarvis Community",
            "description": "Продвинутые напоминания с уведомлениями"
        }
    
    def get_intents(self) -> Dict[str, list]:
        return {
            "remind_in": [
                "напомни мне через час",
                "напоминание через 30 минут",
                "напомни завтра в 9 утра"
            ],
            "remind_list": [
                "список напоминаний",
                "какие у меня напоминания",
                "мои напоминания"
            ]
        }
    
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        if intent_type == "remind_in":
            logger.info("⏰ Напоминание установлено")
            return True
        
        elif intent_type == "remind_list":
            logger.info("⏰ Напоминания: Встреча в 15:00, Купить молоко")
            return True
        
        return False


class NewsPlugin(JarvisPlugin):
    """Плагин для получения новостей"""
    
    def get_info(self) -> Dict[str, str]:
        return {
            "name": "News Plugin",
            "version": "1.0.0",
            "author": "Jarvis Community",
            "description": "Получение последних новостей"
        }
    
    def get_intents(self) -> Dict[str, list]:
        return {
            "news_latest": [
                "какие новости",
                "последние новости",
                "что случилось"
            ],
            "news_tech": [
                "технические новости",
                "новости технологий",
                "что нового в IT"
            ]
        }
    
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        if intent_type == "news_latest":
            logger.info("📰 Последние новости: [заголовок 1], [заголовок 2]")
            return True
        
        elif intent_type == "news_tech":
            logger.info("📰 Новости IT: Python 3.12 вышел, ChatGPT обновлен")
            return True
        
        return False


class MusicPlugin(JarvisPlugin):
    """Расширенный контроль музыки с плейлистами"""
    
    def get_info(self) -> Dict[str, str]:
        return {
            "name": "Music Controller",
            "version": "1.0.0",
            "author": "Jarvis Community",
            "description": "Продвинутый контроль музыки и плейлистов"
        }
    
    def get_intents(self) -> Dict[str, list]:
        return {
            "music_playlist": [
                "включи плейлист релакс",
                "плейлист рок",
                "музыка для работы"
            ],
            "music_artist": [
                "включи музыку от The Beatles",
                "плей Metallica",
                "песни Pink Floyd"
            ]
        }
    
    def handle(self, intent_type: str, slots: Dict[str, Any]) -> bool:
        if intent_type == "music_playlist":
            logger.info("🎵 Плейлист включен")
            return True
        
        elif intent_type == "music_artist":
            logger.info("🎵 Музыка исполнителя включена")
            return True
        
        return False
