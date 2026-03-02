# Фаза 1-2: ML NLU и Wake-Word

## Обзор

В этой фазе реализованы два важных компонента для улучшения качества распознавания команд:

### 1. ML-based NLU (Phase 1)

**Что было:**
- Regex-based NLU (простые шаблоны в nlu.py)
- Жесткие правила для распознавания команд
- Ограниченная гибкость и точность

**Что реализовано:**
- Гибридный ML-подход с использованием spaCy embeddings
- Косинусное сходство между фразами для классификации интентов
- Автоматическое извлечение slots (параметров команд)
- Fallback на SimpleNLU если ML NLU не инициализирован

**Компоненты:**
- `src/jarvis/ml_nlu.py` - MLNLU класс с полной реализацией
- Использует `ru_core_news_sm` модель spaCy для русского языка
- 40+ примеров обучающих фраз для разных категорий команд
- Confidence scores для каждого распознанного интента

**Интеграция:**
```python
# engine.py автоматически выбирает ML NLU с fallback
engine = JarvisEngine(use_ml_nlu=True)  # Попытается использовать ML
# Если ML не инициализируется - автоматически вернется к SimpleNLU
```

**Преимущества:**
- Распознает похожие фразы как один интент
- Работает с разными формулировками команд
- Автоматическое извлечение параметров
- Confidence scores показывают уверенность распознавания

### 2. Porcupine Wake-Word (Phase 2)

**Что было:**
- Простая проверка "джарвис in text" в коде ASR
- Блокирующее распознавание во всех потоках
- Невозможно использовать в фоне

**Что реализовано:**
- Интеграция Porcupine SDK для точного обнаружения "джарвис"
- Фоновое прослушивание в отдельном потоке (threading)
- Callback система для срабатывания при обнаружении
- Automatic fallback на SimpleWakeWord если Porcupine недоступен

**Компоненты:**
- `src/jarvis/wakeword.py` - полная переписанная версия:
  - `PorcupineWakeWord` - основной класс с Porcupine SDK
  - `SimpleWakeWord` - fallback вариант
  - `get_wakeword_detector()` - factory функция
  - Background listen loop в отдельном потоке

**Фоновое слушание:**
```python
detector = PorcupineWakeWord(
    access_key="YOUR_PORCUPINE_ACCESS_KEY",
    sensitivity=0.5,
    on_detected=callback_function
)

# Запустить в фоне (не блокирует)
detector.start_listening()

# ... делать другие вещи ...

# Остановить когда нужно
detector.stop_listening()
```

**Преимущества:**
- Точное обнаружение "джарвис" в шуме
- Не блокирует основной поток
- Callback система для интеграции
- Context manager поддержка

**Настройка Porcupine:**
1. Получить free access key: https://console.picovoice.ai/
2. Использовать в коде или через конфиг
3. Поддерживает любые языки, включая русский

## Тесты

### ML NLU Tests (`tests/test_ml_nlu.py`)
- ✓ Browser commands (navigate, search)
- ✓ Media commands (play, pause, next, prev)
- ✓ Time/Date commands
- ✓ Slot extraction
- ✓ Confidence scoring
- ✓ Engine integration with fallback
- ✓ Similar phrase variations
- ✓ Command variations

**Запуск:**
```bash
python tests/test_ml_nlu.py
```

### Wake-Word Tests (`tests/test_wakeword.py`)
- ✓ Simple detector (keyword matching)
- ✓ Case-insensitive detection
- ✓ Porcupine detector instantiation
- ✓ Sensitivity settings
- ✓ Callback triggering
- ✓ Context manager support
- ✓ Factory function
- ✓ Non-blocking detection

**Запуск:**
```bash
python tests/test_wakeword.py
```

## Обновленные зависимости

```
spacy==3.8.2              # ML NLU with embeddings
ru_core_news_sm           # Russian language model (downloaded separately)
pvporcupine==3.0.3        # Porcupine wake-word SDK
```

## Интеграция в Engine

```python
# engine.py автоматически использует ML NLU
class JarvisEngine:
    def __init__(self, use_ml_nlu=True):
        # Попытается загрузить ML NLU
        if use_ml_nlu:
            try:
                self.nlu = MLNLU()
                self.nlu_type = "ML"
            except:
                self.nlu = SimpleNLU()
                self.nlu_type = "Simple"
```

## Будущие улучшения

1. **Fine-tuning на custom данных** - Обучение модели на реальных командах пользователей
2. **Кэширование embeddings** - Ускорение распознавания через кэширование векторов
3. **Multi-intent support** - Распознавание нескольких интентов в одной команде
4. **Entity extraction** - Более сложное извлечение параметров
5. **Porcupine custom wake-words** - Использование пользовательских wake words

## Статус

- ✅ ML NLU реализована и протестирована
- ✅ Porcupine wake-word интегрирована
- ✅ Fallback механизмы работают
- ✅ Все тесты проходят
- ✅ Документация завершена
- ✅ Требования обновлены

## Следующие шаги

1. Web Interface (Phase 3) - Flask REST API + React dashboard
2. Развертывание с PyInstaller
3. Оптимизация производительности
4. Community plugin contributions
