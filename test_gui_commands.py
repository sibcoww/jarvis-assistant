#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки команд через GUI интерфейс.
Эмулирует работу JarvisEngine с логированием в "GUI".
"""

import sys
from src.jarvis.engine import JarvisEngine
from src.jarvis.nlu import SimpleNLU

def main():
    print("=" * 60)
    print("ТЕСТ GUI КОМАНД JARVIS ASSISTANT")
    print("=" * 60)
    
    # Эмулируем GUI log callback
    gui_logs = []
    def gui_log(msg):
        gui_logs.append(msg)
        print(f"[GUI] {msg}")
    
    # Создаём движок с callback
    engine = JarvisEngine(asr=None, log=gui_log)
    nlu = SimpleNLU()
    
    test_commands = [
        ("какая дата", "Показать дату"),
        ("какое время", "Показать время"),
        ("запомни купить хлеб", "Добавить заметку"),
        ("напоминание позвонить врачу", "Создать напоминание"),
        ("вспомни", "Прочитать заметки"),
        ("включи музыку", "Медиа: Play"),
        ("гугл python", "Поиск в Google"),
        ("перейди на github.com", "Открыть сайт"),
    ]
    
    for i, (command, description) in enumerate(test_commands, 1):
        print(f"\n--- Тест {i}: {description} ---")
        print(f"Команда: '{command}'")
        
        intent = nlu.parse(command)
        print(f"Intent: {intent['type']}")
        
        engine.ex.run(intent)
        print()
    
    print("=" * 60)
    print(f"ИТОГО ЛОГОВ В GUI: {len(gui_logs)}")
    print("=" * 60)
    
    if gui_logs:
        print("\nВсе сообщения в GUI:")
        for i, log in enumerate(gui_logs, 1):
            print(f"{i}. {log}")
    
    print("\n✅ Все команды выполнены успешно!")

if __name__ == "__main__":
    main()
