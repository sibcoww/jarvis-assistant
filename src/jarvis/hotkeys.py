import logging
from typing import Callable, Optional, Set

try:
    from pynput import keyboard, mouse
    _PYNPUT_AVAILABLE = True
except ImportError:
    _PYNPUT_AVAILABLE = False

logger = logging.getLogger(__name__)


class HotkeyManager:
    """
    Управляет глобальными горячими клавишами и комбинациями (push-to-talk).
    Поддерживает клавиатуру и мышь, включая комбинации.
    Требует pynput для работы.
    """
    
    def __init__(self):
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.mouse_listener: Optional[mouse.Listener] = None
        self.on_press_callback: Optional[Callable] = None
        self.on_release_callback: Optional[Callable] = None
        self.is_active = False
        
        # Для отслеживания комбинаций
        self.pressed_keys: Set = set()
        self.target_keys: Set = set()
        self.combo_active = False
        
    def register_push_to_talk(
        self,
        hotkey,  # Может быть одна клавиша или tuple из клавиш для комбинации
        on_press: Optional[Callable] = None,
        on_release: Optional[Callable] = None
    ) -> bool:
        """
        Регистрирует глобальную горячую клавишу/комбинацию для push-to-talk.
        
        Args:
            hotkey: Клавиша, кнопка мыши или tuple из них для комбинации
            on_press: Callback при активации
            on_release: Callback при деактивации
            
        Returns:
            True если успешно, False если pynput недоступен
        """
        if not _PYNPUT_AVAILABLE:
            logger.warning("pynput не установлен. Push-to-talk отключен.")
            return False
        
        self.on_press_callback = on_press
        self.on_release_callback = on_release
        
        # Нормализуем hotkey к set
        if isinstance(hotkey, (tuple, list)):
            self.target_keys = set(hotkey)
        else:
            self.target_keys = {hotkey}
        
        try:
            # Обработчики клавиатуры
            def on_key_press(key):
                try:
                    self.pressed_keys.add(key)
                    self._check_combo_activation()
                except Exception as e:
                    logger.error(f"Ошибка в on_key_press: {e}")
            
            def on_key_release(key):
                try:
                    self.pressed_keys.discard(key)
                    if self.combo_active:
                        # Если отпустили любую клавишу из комбинации
                        if key in self.target_keys:
                            self._deactivate_combo()
                except Exception as e:
                    logger.error(f"Ошибка в on_key_release: {e}")
            
            # Обработчики мыши
            def on_mouse_click(x, y, button, pressed):
                try:
                    if pressed:
                        self.pressed_keys.add(button)
                        self._check_combo_activation()
                    else:
                        self.pressed_keys.discard(button)
                        if self.combo_active and button in self.target_keys:
                            self._deactivate_combo()
                except Exception as e:
                    logger.error(f"Ошибка в on_mouse_click: {e}")
            
            # Запускаем слушатели
            self.keyboard_listener = keyboard.Listener(
                on_press=on_key_press,
                on_release=on_key_release
            )
            self.mouse_listener = mouse.Listener(
                on_click=on_mouse_click
            )
            
            self.keyboard_listener.start()
            self.mouse_listener.start()
            self.is_active = True
            
            logger.info(f"Push-to-talk зарегистрирован на: {self.target_keys}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка регистрации push-to-talk: {e}")
            return False
    
    def _check_combo_activation(self):
        """Проверяет, активирована ли комбинация клавиш"""
        if not self.combo_active and self.target_keys.issubset(self.pressed_keys):
            self.combo_active = True
            if self.on_press_callback:
                self.on_press_callback()
    
    def _deactivate_combo(self):
        """Деактивирует комбинацию"""
        if self.combo_active:
            self.combo_active = False
            if self.on_release_callback:
                self.on_release_callback()
    
    def unregister(self):
        """Отключает горячую клавишу"""
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        if self.mouse_listener:
            self.mouse_listener.stop()
        self.is_active = False
        self.pressed_keys.clear()
        self.combo_active = False
        logger.info("Push-to-talk отключен")
