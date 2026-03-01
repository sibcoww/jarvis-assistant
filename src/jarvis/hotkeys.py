import logging
from typing import Callable, Optional

try:
    from pynput import keyboard
    _PYNPUT_AVAILABLE = True
except ImportError:
    _PYNPUT_AVAILABLE = False

logger = logging.getLogger(__name__)


class HotkeyManager:
    """
    Управляет глобальными горячими клавишами (push-to-talk).
    Требует pynput для работы. Если pynput недоступен, функция отключена.
    """
    
    def __init__(self):
        self.listener: Optional[keyboard.Listener] = None
        self.on_press_callback: Optional[Callable] = None
        self.on_release_callback: Optional[Callable] = None
        self.is_active = False
        
    def register_push_to_talk(
        self,
        hotkey: keyboard.Key = keyboard.Key.f6,
        on_press: Optional[Callable] = None,
        on_release: Optional[Callable] = None
    ) -> bool:
        """
        Регистрирует глобальную горячую клавишу для push-to-talk.
        
        Args:
            hotkey: Клавиша (по умолчанию F6)
            on_press: Callback при нажатии клавиши
            on_release: Callback при отпускании клавиши
            
        Returns:
            True если успешно, False если pynput недоступен
        """
        if not _PYNPUT_AVAILABLE:
            logger.warning("pynput не установлен. Push-to-talk отключен.")
            return False
        
        self.on_press_callback = on_press
        self.on_release_callback = on_release
        self.target_key = hotkey
        
        try:
            def on_press_handler(key):
                try:
                    if key == self.target_key and self.on_press_callback:
                        self.on_press_callback()
                except Exception as e:
                    logger.error(f"Ошибка в on_press обработчике: {e}")
            
            def on_release_handler(key):
                try:
                    if key == self.target_key and self.on_release_callback:
                        self.on_release_callback()
                except Exception as e:
                    logger.error(f"Ошибка в on_release обработчике: {e}")
            
            self.listener = keyboard.Listener(
                on_press=on_press_handler,
                on_release=on_release_handler
            )
            self.listener.start()
            self.is_active = True
            logger.info(f"Push-to-talk зарегистрирована на клавишу {hotkey}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка регистрации push-to-talk: {e}")
            return False
    
    def unregister(self):
        """Отключает горячую клавишу"""
        if self.listener:
            self.listener.stop()
            self.is_active = False
            logger.info("Push-to-talk отключена")
