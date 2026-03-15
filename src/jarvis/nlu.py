import re
import logging

logger = logging.getLogger(__name__)

NUMBERS = {
    "ноль": 0,
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
    "сто": 100
}

def extract_number(text: str):
    # 1) если есть цифры — берём их
    m = re.search(r"\d{1,3}", text)
    if m:
        return int(m.group())

    # 2) иначе пробуем собрать число из слов
    words = text.lower().split()
    total = 0
    found = False

    for w in words:
        if w in NUMBERS:
            total += NUMBERS[w]
            found = True

    return total if found else None

class SimpleNLU:
    """Очень простой NLU: сопоставляет фразы с интентами.
       Возвращает словарь вида {"type": "...", "slots": {...}}.
    """
    def load_config(self):
        """Совместимость с интерфейсом движка; настроек нет."""
        return None

    def parse(self, text: str) -> dict:
        t = text.lower()

        # browser commands (проверяем ДО open_app чтобы не было конфликта)
        if "перейди на" in t or "открой сайт" in t:
            m = re.search(r"(?:перейди на|открой сайт)\s+(.+)", t)
            if m:
                site = m.group(1).strip()
                return {"type": "browser_navigate", "slots": {"url": site}}
        
        if "гугл" in t or "поиск" in t:
            m = re.search(r"(?:гугл|поиск)\s+(.+)", t)
            if m:
                query = m.group(1).strip()
                return {"type": "browser_search", "slots": {"query": query}}
        
        # media commands (проверяем ДО open_app чтобы не было конфликта с "включи")
        if any(w in t for w in ("включи музыку", "включи музик", "запусти музыку")):
            return {"type": "media_play", "slots": {}}
        
        if any(w in t for w in ("пауза", "стоп", "остановись")):
            return {"type": "media_pause", "slots": {}}
        
        if any(w in t for w in ("далее", "следующая", "следующий")):
            return {"type": "media_next", "slots": {}}
        
        if any(w in t for w in ("назад", "предыдущая", "предыдущий")):
            return {"type": "media_previous", "slots": {}}

        # open app
        if any(w in t for w in ("открой", "запусти", "включи")):
            if any(w in t for w in ("браузер", "хром", "chrome")):
                return {"type": "open_app", "slots": {"target": "browser"}}
            if any(w in t for w in ("телеграм", "телеграмм", "телеграмму", "telegram")):
                return {"type": "open_app", "slots": {"target": "telegram"}}
            if any(w in t for w in ("vscode", "vs code", "вс код", "визуал студио код", "визу студию код")):
                return {"type": "open_app", "slots": {"target": "vscode"}}
            if any(w in t for w in ("блокнот", "notepad", "блокноту")):
                return {"type": "open_app", "slots": {"target": "notepad"}}
            # Если указано открой/запусти, но приложение не распознано, ищем дальше

        # volume up/down X
        if "сделай тише" in t or "убавь громкость" in t:
            m = re.search(r"(\d+)", t)
            delta = int(m.group(1)) if m else 10
            return {"type": "volume_down", "slots": {"delta": delta}}
        if "сделай громче" in t or "добавь громкость" in t:
            m = re.search(r"(\d+)", t)
            delta = int(m.group(1)) if m else 10
            return {"type": "volume_up", "slots": {"delta": delta}}

        # scenario
        if "рабочий режим" in t:
            return {"type": "run_scenario", "slots": {"name": "рабочий режим"}}

        if "громк" in t or "звук" in t:
            value = extract_number(text)
            if value is not None:
                value = max(0, min(100, value))
                return {
                    "type": "set_volume",
                    "slots": {"value": value}
                }

        # open app (generic pattern)
        if t.startswith(("открой", "запусти")):
            target = t.replace("открой", "").replace("запусти", "").strip()
            if target:
                return {
                    "type": "open_app",
                    "slots": {"target": target}
                }

        # scenario
        if t in ("рабочий режим", "рабочий"):
            return {
                "type": "run_scenario",
                "slots": {"name": t}
            }
        
        # create folder
        m = re.search(r"(создай|сделай)\s+папк[ауы]\s+(.+)", t)
        if m:
            return {"type": "create_folder", "slots": {"name": m.group(2).strip()}}
        
        # calendar/time commands
        if any(w in t for w in ("какая дата", "сегодня дата", "текущая дата")):
            return {"type": "show_date", "slots": {}}
        
        if any(w in t for w in ("какое время", "текущее время", "который час")):
            return {"type": "show_time", "slots": {}}
        
        if "напоминание" in t or "напомни" in t:
            m = re.search(r"(?:напоминание|напомни)(?:\s+через|\s+на)?\s+(.+)", t)
            if m:
                reminder = m.group(1).strip()
                return {"type": "create_reminder", "slots": {"reminder": reminder}}
        
        # notes commands
        if "запомни" in t or "запишись" in t:
            m = re.search(r"(?:запомни|запишись)(?:\s+что)?\s+(.+)", t)
            if m:
                note = m.group(1).strip()
                return {"type": "add_note", "slots": {"text": note}}
        
        if "вспомни" in t or "напомни записал" in t or "прочитай заметк" in t:
            return {"type": "read_notes", "slots": {}}

        return {"type": "unknown", "slots": {"text": text}}
    
    

