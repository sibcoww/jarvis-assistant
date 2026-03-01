import logging
import sys

def setup_logging(level=logging.INFO):
    """
    Настраивает централизованное логирование для всего приложения Jarvis.
    
    Args:
        level: Уровень логирования (logging.DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Формат логов
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    
    # Базовая конфигурация
    logging.basicConfig(
        level=level,
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            # Опционально: можно добавить FileHandler для записи в файл
            # logging.FileHandler("jarvis.log", encoding="utf-8")
        ]
    )
    
    # Отключаем лишний шум от сторонних библиотек
    logging.getLogger("vosk").setLevel(logging.WARNING)
    logging.getLogger("sounddevice").setLevel(logging.WARNING)
    
    return logging.getLogger("jarvis")
