import json
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class CommandHistory:
    """
    Простая история команд с сохранением/загрузкой из JSON.
    """
    
    def __init__(self, max_size: int = 100, history_file: str = None):
        """
        Args:
            max_size: Максимальное количество сохраняемых команд
            history_file: Путь к файлу истории (если None, используется default)
        """
        self.max_size = max_size
        self.history: List[Dict[str, Any]] = []
        
        if history_file is None:
            history_file = str(Path.home() / ".jarvis" / "command_history.json")
        self.history_file = Path(history_file)
    
    def add(self, command: str, intent_type: str = None, status: str = "success") -> None:
        """
        Добавить команду в историю.
        
        Args:
            command: Текст команды
            intent_type: Тип интента
            status: Статус выполнения (success/error/unknown)
        """
        import datetime
        
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "command": command,
            "intent_type": intent_type,
            "status": status
        }
        
        self.history.append(entry)
        
        # Ограничиваем размер истории
        if len(self.history) > self.max_size:
            self.history.pop(0)
        
        logger.debug(f"История: добавлена команда '{command}'")
    
    def get_recent(self, count: int = 10) -> List[Dict[str, Any]]:
        """Получить последние N команд"""
        return self.history[-count:] if self.history else []
    
    def search(self, query: str) -> List[Dict[str, Any]]:
        """Поиск команд содержащих query"""
        return [
            entry for entry in self.history
            if query.lower() in entry["command"].lower()
        ]
    
    def save(self) -> bool:
        """Сохранить историю в файл"""
        try:
            # Создаём необходимые папки
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Сохраняем историю
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, ensure_ascii=False, indent=2)
            
            logger.info(f"История сохранена: {self.history_file} ({len(self.history)} команд)")
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения истории: {e}")
            return False
    
    def load(self) -> bool:
        """Загрузить историю из файла"""
        try:
            if not self.history_file.exists():
                logger.info(f"Файл истории не найден: {self.history_file}")
                return False
            
            with open(self.history_file, 'r', encoding='utf-8') as f:
                self.history = json.load(f)
            
            # Ограничиваем размер при загрузке
            if len(self.history) > self.max_size:
                self.history = self.history[-self.max_size:]
            
            logger.info(f"История загружена: {len(self.history)} команд")
            return True
        except Exception as e:
            logger.error(f"Ошибка загрузки истории: {e}")
            return False
    
    def clear(self) -> None:
        """Очистить историю"""
        self.history.clear()
        logger.info("История очищена")
    
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику по истории"""
        total = len(self.history)
        if total == 0:
            return {"total": 0}
        
        # Подсчитываем типы интентов
        intent_types = {}
        statuses = {}
        for entry in self.history:
            intent = entry.get("intent_type", "unknown")
            status = entry.get("status", "unknown")
            intent_types[intent] = intent_types.get(intent, 0) + 1
            statuses[status] = statuses.get(status, 0) + 1
        
        return {
            "total": total,
            "intent_types": intent_types,
            "statuses": statuses,
            "success_rate": round(100 * statuses.get("success", 0) / total, 1) if total > 0 else 0
        }
