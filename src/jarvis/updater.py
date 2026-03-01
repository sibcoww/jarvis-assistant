import logging
import subprocess
from typing import Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class AutoUpdater:
    """
    Автоматическое обновление приложения из GitHub.
    Проверяет наличие новых версий и предлагает обновление.
    """
    
    def __init__(self, repo_owner: str = "sibcoww", repo_name: str = "jarvis-assistant", 
                 current_version: str = "1.0.0"):
        """
        Args:
            repo_owner: Владелец репозитория на GitHub
            repo_name: Имя репозитория
            current_version: Текущая версия приложения
        """
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.current_version = current_version
        self.repo_url = f"https://github.com/{repo_owner}/{repo_name}"
    
    def get_latest_version(self) -> Optional[str]:
        """
        Получить номер последней версии из GitHub releases API.
        
        Returns:
            Версия или None если не удалось получить
        """
        try:
            import json
            import urllib.request
            
            url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
            
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                version = data.get('tag_name', '').lstrip('v')
                
                if version:
                    logger.info(f"Доступна новая версия: {version}")
                    return version
        except Exception as e:
            logger.debug(f"Ошибка проверки версии: {e}")
            return None
    
    def compare_versions(self, v1: str, v2: str) -> int:
        """
        Сравнить две версии (semver).
        
        Args:
            v1: Версия 1
            v2: Версия 2
            
        Returns:
            -1 если v1 < v2, 0 если равны, 1 если v1 > v2
        """
        try:
            v1_parts = tuple(map(int, v1.split('.')))
            v2_parts = tuple(map(int, v2.split('.')))
            
            if v1_parts < v2_parts:
                return -1
            elif v1_parts > v2_parts:
                return 1
            else:
                return 0
        except Exception as e:
            logger.error(f"Ошибка сравнения версий: {e}")
            return 0
    
    def is_update_available(self) -> bool:
        """Проверить, доступно ли обновление"""
        latest = self.get_latest_version()
        if latest:
            return self.compare_versions(self.current_version, latest) < 0
        return False
    
    def pull_latest(self) -> bool:
        """
        Загрузить последнюю версию из GitHub (git pull).
        
        Returns:
            True если успешно, False если ошибка
        """
        try:
            # Проверяем наличие git
            result = subprocess.run(['git', '--version'], capture_output=True, timeout=5)
            if result.returncode != 0:
                logger.warning("git не установлен")
                return False
            
            # git pull
            result = subprocess.run(['git', 'pull', 'origin', 'main'], 
                                  capture_output=True, timeout=30, text=True)
            if result.returncode == 0:
                logger.info("Приложение обновлено из GitHub")
                return True
            else:
                logger.error(f"Ошибка git pull: {result.stderr}")
                return False
        except Exception as e:
            logger.error(f"Ошибка обновления: {e}")
            return False
    
    def download_zip(self, destination: Path) -> bool:
        """
        Загрузить ZIP архив последней версии.
        
        Args:
            destination: Путь для сохранения архива
            
        Returns:
            True если успешно, False если ошибка
        """
        try:
            import urllib.request
            
            url = f"{self.repo_url}/archive/refs/heads/main.zip"
            logger.info(f"Загрузка {url}...")
            
            urllib.request.urlretrieve(url, str(destination))
            logger.info(f"Архив загружен: {destination}")
            return True
        except Exception as e:
            logger.error(f"Ошибка загрузки архива: {e}")
            return False
    
    def get_release_notes(self) -> Optional[str]:
        """
        Получить примечания к выпуску последней версии.
        
        Returns:
            Примечания или None если не удалось получить
        """
        try:
            import json
            import urllib.request
            
            url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/releases/latest"
            
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data.get('body', 'Нет описания')
        except Exception as e:
            logger.debug(f"Ошибка получения примечаний: {e}")
            return None
    
    def check_and_notify(self) -> Tuple[bool, Optional[str]]:
        """
        Проверить обновление и возвратить информацию.
        
        Returns:
            Кортеж (доступно_обновление, версия)
        """
        latest = self.get_latest_version()
        if latest and self.compare_versions(self.current_version, latest) < 0:
            logger.info(f"Доступно обновление: {self.current_version} → {latest}")
            return True, latest
        return False, None
