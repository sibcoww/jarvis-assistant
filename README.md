# Jarvis PC Assistant — Starter

Полнофункциональный голосовой ассистент для Windows с поддержкой офлайн-распознавания речи, синтеза речи и управления приложениями.

## 📦 Стек

**Core:**
- Python 3.10+
- Vosk 0.3.45 — офлайн распознавание речи (русский язык)
- sounddevice 0.5.2 — работа с микрофоном
- PySide6 6.10.1 — Qt-based GUI

**Optional (P2):**
- pyttsx3 — синтез речи (text-to-speech)
- pynput — глобальные горячие клавиши (push-to-talk)

**Development:**
- pytest — тестирование
- pycaw — управление громкостью Windows

## 🚀 Быстрый старт

### 1. Подготовка

```bash
# Клонируем репозиторий
git clone https://github.com/sibcoww/jarvis-assistant.git
cd jarvis-assistant

# Создаём виртуальное окружение
python -m venv .venv
.venv\Scripts\activate

# Устанавливаем зависимости
pip install -r requirements.txt

# (Опционально) Скачиваем русскую модель Vosk
cd models
# Загрузите vosk-model-ru-0.42 отсюда: https://alphacephei.com/vosk/models
cd ..
```

### 2. Конфигурация

```bash
# Копируем пример конфигурации
cp src/jarvis/config.json.example src/jarvis/config.json

# Редактируем пути к приложениям
notepad src/jarvis/config.json
```

Подробнее в [CONFIG.md](CONFIG.md)

### 3. Запуск

**GUI режим (рекомендуется):**
```bash
python -m gui.app
```

**CLI режим:**
```bash
# Mock режим (без микрофона, ввод с клавиатуры)
python -m src.jarvis.main --mock

# Vosk режим (с распознаванием речи)
python -m src.jarvis.main --asr vosk
```

## 🎤 Использование

### Основные команды

**Приложения:**
- **"открой браузер"** — запуск приложения
- **"запусти vscode"** — запуск VS Code

**Громкость:**
- **"сделай громче на 20"** — увеличить громкость
- **"сделай тише на 10"** — уменьшить громкость
- **"громкость 50"** — установить конкретное значение

**Браузер:**
- **"перейди на google.com"** — открыть сайт
- **"открой сайт wikipedia.org"** — открыть страницу
- **"гугл python"** — поиск в Google
- **"поиск как готовить торт"** — поиск

**Медиа:**
- **"включи музыку"** — play
- **"пауза"** — pause/stop
- **"далее"** — следующий трек
- **"назад"** — предыдущий трек

**Календарь & Время:**
- **"какая дата"** — показать текущую дату
- **"какое время"** — показать текущее время
- **"который час"** — показать время

**Заметки & Напоминания:**
- **"запомни позвонить маме"** — добавить заметку
- **"напоминание купить молоко"** — создать напоминание
- **"вспомни"** — прочитать все заметки
- **"прочитай заметки"** — показать последние заметки

**Файлы & Папки:**
- **"создай папку MyFolder"** — создать папку
- **"рабочий режим"** — запустить сценарий

### Синонимы

Можно настроить синонимы в `config.json`:
```json
"synonyms": {
  "хром": "browser",
  "браузер": "browser",
  "вс код": "vscode"
}
```

## 🧪 Тестирование

```bash
# Запустить все тесты
python -m unittest discover -s tests -p "test_*.py" -v

# Или с pytest
pytest -v

# Проверка покрытия (>70%)
pytest --cov=src --cov-report=term-missing
```

**Статус:** 35 тестов ✓

## 📚 Архитектура

```
jarvis/
├── asr.py              # Распознавание речи (Vosk)
├── nlu.py              # Понимание намеренийОбработка интентов)
├── executor.py         # Выполнение команд
├── engine.py           # Основной движок (орхестрация)
├── hotkeys.py          # Глобальные горячие клавиши
├── wakeword.py         # Обнаружение wake-word
├── logger.py           # Централизованное логирование
├── tts.py              # Синтез речи (опционально)
├── history.py          # История команд (P2)
├── updater.py          # Автообновление (P2)
└── config.json         # Конфигурация приложений
```

## 🔧 Функциональность

### P0 (Критически важное)
- ✅ Безопасное выполнение команд (shell=False)
- ✅ Централизованное логирование
- ✅ Валидация config.json
- ✅ Обработка исключений во всех критических местах
- ✅ Дублирующие виджеты удалены

### P1 (Важное)
- ✅ HotkeyManager для push-to-talk (F6)
- ✅ 35 unit-тестов (engine + NLU)
- ✅ Config с поддержкой переменных окружения
- ✅ Полная обработка ошибок
- ✅ Tabbed GUI с настройками

### P2 (Опциональное)
- ✅ TextToSpeech (pyttsx3) для озвучивания
- ✅ Операции с файлами (copy, move, delete, create)
- ✅ История команд с поиском и статистикой
- ✅ AutoUpdater для проверки обновлений на GitHub

## ⚙️ Конфигурация

Основной файл: `src/jarvis/config.json`

Пример:
```json
{
  "apps": {
    "browser": "${PROGRAMFILES}/Google/Chrome/Application/chrome.exe",
    "vscode": "E:/Tools/VS Code/Code.exe",
    "notepad": "notepad"
  },
  "synonyms": {
    "браузер": "browser",
    "хром": "browser"
  },
  "scenarios": {
    "рабочий режим": ["open:vscode", "open:browser"]
  }
}
```

Поддерживаемые переменные окружения:
- `${PROGRAMFILES}` — C:\Program Files
- `${APPDATA}` — %AppData% 
- `${LOCALAPPDATA}` — %LocalAppData%
- `${USERPROFILE}` — %UserProfile%

## 📊 Статистика

- **Всего Python файлов:** 15
- **Строк кода:** ~3000
- **Тесты:** 52 (100% pass rate)
- **Документация:** 4 файла (README, CONFIG, COMPLETION_SUMMARY, etc)
- **Коммиты:** 5 (P0, P1, P2, P3 Phase 1)

## 🤝 Разработка

### Добавление новой команды

1. Добавьте паттерн в `nlu.py`:
```python
if "мой_паттерн" in t:
    return {"type": "my_intent", "slots": {...}}
```

2. Реализуйте обработчик в `executor.py`:
```python
def my_handler(self, param):
    # ваш код
    logger.info("Done")
```

3. Добавьте тест в `tests/test_nlu.py` или `tests/test_executor.py`

### Логирование

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Информационное сообщение")
logger.warning("Предупреждение")
logger.error("Ошибка")
logger.debug("Отладочная информация")
```

## 📝 Примечания

- Приложение использует офлайн-распознавание (Vosk) — не требует интернета
- Все голосовые команды выполняются локально
- История команд сохраняется в `~/.jarvis/command_history.json`
- Логи выводятся в консоль и можно перенаправить в файл

## 📄 Лицензия

MIT License. Ты — автор.

## 🔗 Ссылки

- [GitHub репозиторий](https://github.com/sibcoww/jarvis-assistant)
- [Vosk моделиОфлайн ASR](https://alphacephei.com/vosk/models)
- [PySide6 документация](https://doc.qt.io/qtforpython/)
- [pyttsx3 документация](https://pyttsx3.readthedocs.io/)
