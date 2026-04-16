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


def collapse_repeated_stt_words(text: str) -> str:
    """Схлопывает подряд идущие одинаковые слова из STT."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return ""
    words = s.split(" ")
    out: list[str] = []
    prev_norm = ""
    for word in words:
        norm = re.sub(r"[^\wа-яё]", "", word.lower(), flags=re.IGNORECASE)
        if norm and norm == prev_norm:
            continue
        out.append(word)
        prev_norm = norm
    return " ".join(out)

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
        t_clean = re.sub(r"\s+", " ", t).strip()

        def _extract_target_before_browser_phrase(raw: str) -> str:
            m = re.search(r"(?:открой|запусти|включи)\s+(.+?)\s+в\s+браузер[е]?", raw)
            if not m:
                return ""
            candidate = (m.group(1) or "").strip(" .,!?")
            if candidate in {"ватсап", "ватсапп", "whatsapp", "вотсап", "вацап"}:
                return "whatsapp"
            if candidate in {"телеграм", "телеграмм", "телеграмму", "telegram"}:
                return "telegram"
            return candidate

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

        # action history / repeat
        if any(
            p in t
            for p in (
                "повтори команду",
                "повтори последнее",
                "повтори последнюю команду",
                "повтори действие",
            )
        ):
            return {"type": "repeat_last_command", "slots": {}}
        if any(
            p in t
            for p in (
                "что ты сделал",
                "история действий",
                "покажи действия",
                "последние действия",
            )
        ):
            return {"type": "show_action_history", "slots": {}}

        if any(
            p in t
            for p in (
                "какие программы ты знаешь",
                "покажи список доступных программ",
                "покажи доступные программы",
                "список доступных программ",
                "какие приложения ты знаешь",
                "что ты умеешь открывать",
            )
        ):
            return {"type": "list_known_apps", "slots": {}}

        # window management
        if any(p in t for p in ("сверни окно", "минимизируй окно", "сверни текущее окно")):
            return {"type": "window_minimize", "slots": {}}
        if any(p in t for p in ("разверни окно", "максимизируй окно", "на весь экран окно")):
            return {"type": "window_maximize", "slots": {}}
        if any(p in t for p in ("закрой окно", "закрой текущее окно")):
            return {"type": "window_close", "slots": {}}
        if any(p in t for p in ("переключи окно", "следующее окно", "переключи на другое окно")):
            return {"type": "window_switch", "slots": {}}
        if any(
            p in t
            for p in (
                "окно влево",
                "поставь окно влево",
                "прижми окно влево",
            )
        ):
            return {"type": "window_snap_left", "slots": {}}
        if any(
            p in t
            for p in (
                "окно вправо",
                "поставь окно вправо",
                "прижми окно вправо",
            )
        ):
            return {"type": "window_snap_right", "slots": {}}
        if any(p in t for p in ("окно вверх", "окно наверх")):
            return {"type": "window_snap_up", "slots": {}}
        if any(p in t for p in ("окно вниз", "окно внизу")):
            return {"type": "window_snap_down", "slots": {}}
        if any(
            p in t
            for p in (
                "раздели экран пополам",
                "два окна рядом",
                "поставь окна слева и справа",
                "размести два окна рядом",
            )
        ):
            return {"type": "window_split_two", "slots": {}}

        # presentation commands
        if (
            ("слайд" in t and any(w in t for w in ("следующ", "вперед", "далее")))
            or any(p in t for p in ("следующий слайд", "слайд вперед", "слайд далее"))
        ):
            return {"type": "presentation_next_slide", "slots": {}}
        if (
            ("слайд" in t and any(w in t for w in ("предыдущ", "назад")))
            or any(p in t for p in ("предыдущий слайд", "слайд назад"))
        ):
            return {"type": "presentation_previous_slide", "slots": {}}
        if any(
            p in t
            for p in (
                "запусти презентацию",
                "начни презентацию",
                "начать презентацию",
                "начни показ слайдов",
                "запусти показ слайдов",
            )
        ):
            return {"type": "presentation_start", "slots": {}}
        if any(
            p in t
            for p in (
                "останови презентацию",
                "заверши презентацию",
                "выход из презентации",
                "закрой презентацию",
            )
        ):
            return {"type": "presentation_end", "slots": {}}
        
        # media commands (проверяем ДО open_app чтобы не было конфликта с "включи")
        if any(w in t for w in ("включи музыку", "включи музик", "запусти музыку")):
            return {"type": "media_play", "slots": {}}
        
        if any(w in t for w in ("пауза", "стоп", "остановись")):
            return {"type": "media_pause", "slots": {}}
        
        if any(w in t for w in ("далее", "следующая", "следующий")):
            return {"type": "media_next", "slots": {}}
        
        if any(w in t for w in ("назад", "предыдущая", "предыдущий")):
            return {"type": "media_previous", "slots": {}}

        # volume up/down X (включая разговорные формулировки "на десять меньше/больше")
        if t_clean in {"поставь громкость", "сделай громкость", "установи громкость", "громкость", "звук"}:
            return {"type": "unknown", "slots": {"text": text}}

        if "громк" in t or "звук" in t:
            if any(p in t for p in ("меньше", "понизь", "пониже", "убав")):
                delta = extract_number(text)
                if delta is None:
                    delta = 10
                delta = max(1, min(100, int(delta)))
                return {"type": "volume_down", "slots": {"delta": delta}}
            if any(p in t for p in ("больше", "повысь", "повыше", "добав", "громче")):
                delta = extract_number(text)
                if delta is None:
                    delta = 10
                delta = max(1, min(100, int(delta)))
                return {"type": "volume_up", "slots": {"delta": delta}}

        # open app
        if any(w in t for w in ("закрой", "закрыть", "выключи", "останови")):
            if any(w in t for w in ("браузер", "хром", "chrome")):
                return {"type": "close_app", "slots": {"target": "browser"}}
            if any(w in t for w in ("телеграм", "телеграмм", "телеграмму", "telegram")):
                return {"type": "close_app", "slots": {"target": "telegram"}}
            if any(w in t for w in ("vscode", "vs code", "вс код", "визуал студио код", "визу студию код")):
                return {"type": "close_app", "slots": {"target": "vscode"}}
            if any(w in t for w in ("блокнот", "notepad", "блокноту")):
                return {"type": "close_app", "slots": {"target": "notepad"}}
            if any(w in t for w in ("ватсап", "ватсапп", "whatsapp", "вотсап", "вацап")):
                return {"type": "close_app", "slots": {"target": "whatsapp"}}

        # open app
        if any(w in t for w in ("открой", "открыть", "запусти", "запустить", "включи")):
            # Слишком общая команда -> пусть уйдет в AI/уточнение.
            if t_clean in {
                "открой сайт",
                "открой программу",
                "открой приложение",
                "запусти программу",
                "запусти приложение",
            }:
                return {"type": "unknown", "slots": {"text": text}}

            # "открой X в браузере" -> X, а не сам browser.
            if "браузер" in t:
                extracted = _extract_target_before_browser_phrase(t)
                if extracted and extracted not in {"браузер", "хром", "chrome"}:
                    return {"type": "open_app", "slots": {"target": extracted}}

            if any(w in t for w in ("браузер", "хром", "chrome")):
                return {"type": "open_app", "slots": {"target": "browser"}}
            if any(w in t for w in ("телеграм", "телеграмм", "телеграмму", "telegram")):
                return {"type": "open_app", "slots": {"target": "telegram"}}
            if any(w in t for w in ("ватсап", "ватсапп", "whatsapp", "вотсап", "вацап")):
                return {"type": "open_app", "slots": {"target": "whatsapp"}}
            if any(w in t for w in ("vscode", "vs code", "вс код", "визуал студио код", "визу студию код")):
                return {"type": "open_app", "slots": {"target": "vscode"}}
            if any(w in t for w in ("блокнот", "notepad", "блокноту")):
                return {"type": "open_app", "slots": {"target": "notepad"}}
            # Если указано открой/запусти, но приложение не распознано, ищем дальше

        # volume up/down X
        if "сделай тише" in t or "убавь громкость" in t:
            delta = extract_number(text)
            delta = int(delta) if delta is not None else 10
            return {"type": "volume_down", "slots": {"delta": delta}}
        if "сделай громче" in t or "добавь громкость" in t:
            delta = extract_number(text)
            delta = int(delta) if delta is not None else 10
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
        if t.startswith(("открой", "открыть", "запусти", "запустить")):
            target = t
            for verb in ("открой", "открыть", "запусти", "запустить"):
                if target.startswith(verb):
                    target = target.replace(verb, "", 1).strip()
                    break
            # "открой программу зона" -> "зона"
            target = re.sub(
                r"^(?:программу|программа|приложение|приложуху|сайт)\s+",
                "",
                target,
            ).strip()
            generic_targets = {"сайт", "программу", "программа", "приложение", "приложуху"}
            if target in generic_targets:
                return {"type": "unknown", "slots": {"text": text}}
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

        # weather commands
        m = re.search(r"(?:погода|какая погода|что с погодой)(?:\s+в\s+(.+))?", t)
        if m:
            city = (m.group(1) or "").strip()
            return {"type": "show_weather", "slots": {"city": city}}

        # system actions
        m = re.search(r"выключи\s+.+\b(комп|компьютер|пк)\b", t)
        if m:
            return {"type": "shutdown_pc", "slots": {}}
        if any(p in t for p in ("выключи компьютер", "выключи пк", "заверши работу", "выруби компьютер")):
            return {"type": "shutdown_pc", "slots": {}}
        m = re.search(r"(?:перезагрузи|перезапусти|рестарт)\s+.+\b(комп|компьютер|пк)\b", t)
        if m:
            return {"type": "restart_pc", "slots": {}}
        if any(p in t for p in ("перезагрузи компьютер", "перезагрузи пк", "перезапусти компьютер", "рестарт компьютер")):
            return {"type": "restart_pc", "slots": {}}
        if any(p in t for p in ("режим сна", "усыпи компьютер", "в сон")):
            return {"type": "sleep_pc", "slots": {}}
        if any(p in t for p in ("заблокируй экран", "блокируй экран", "заблокируй компьютер", "блокировка экрана")):
            return {"type": "lock_pc", "slots": {}}
        
        if "напоминание" in t or "напомни" in t:
            m = re.search(r"(?:напоминание|напомни)(?:\s+через|\s+на)?\s+(.+)", t)
            if m:
                reminder = m.group(1).strip()
                return {"type": "create_reminder", "slots": {"reminder": reminder}}

        # timer commands
        m = re.search(r"(?:таймер|засеки(?:\s+таймер)?)\s+(?:на\s+)?(.+)", t)
        if m:
            payload = m.group(1).strip()
            mt = re.match(r"(.+?)\s+(секунд[ауы]?|минут[ауы]?|час(?:а|ов)?)\s*(.*)$", payload)
            if mt:
                amount_text = mt.group(1).strip()
                unit = mt.group(2).strip()
                label = mt.group(3).strip()
                amount = extract_number(amount_text)
                if amount is not None and amount > 0:
                    return {
                        "type": "start_timer",
                        "slots": {"amount": amount, "unit": unit, "label": label},
                    }

        if any(p in t for p in ("сколько осталось", "таймер статус", "статус таймера", "сколько до таймера")):
            return {"type": "timer_status", "slots": {}}

        if any(p in t for p in ("отмени таймер", "отмена таймера", "удали таймер", "сбрось таймер")):
            return {"type": "cancel_timer", "slots": {}}

        # todo commands
        m = re.search(r"(?:добавь|создай|новая)\s+(?:в\s+)?(?:задач[ауи]?|todo)\s+(.+)", t)
        if m:
            task_text = m.group(1).strip()
            if task_text:
                return {"type": "add_todo", "slots": {"text": task_text}}

        # Естественная форма: "запиши мне в дела купить хлеб"
        m = re.search(r"(?:запиши|добавь)\s+(?:мне\s+)?(?:в\s+)?дела\s+(.+)", t)
        if m:
            task_text = m.group(1).strip()
            if task_text:
                return {"type": "add_todo", "slots": {"text": task_text}}

        if any(
            p in t
            for p in (
                "покажи задачи",
                "покажи задач",
                "список задач",
                "мои задачи",
                "покажи todo",
            )
        ):
            return {"type": "list_todos", "slots": {}}

        m = re.search(
            r"(?:выполнил[а]?|выполнен[а]?|заверши|закрой|закрыла|отметь)\s+"
            r"(?:задач[ауеи]?|задат[ьи]|todo)\s+(.+)",
            t,
        )
        if m:
            ref = m.group(1).strip()
            if ref:
                return {"type": "complete_todo", "slots": {"ref": ref}}

        # Естественная/шумная форма: "отметьте как первая задача", "отметь когда первую задач"
        m = re.search(
            r"(?:отметь|отметьте|отмети)\s+(?:как|когда)?\s*(.+?)\s+(?:задач[ауеи]?|задат[ьи]|todo)\b",
            t,
        )
        if m:
            ref = m.group(1).strip()
            if ref:
                return {"type": "complete_todo", "slots": {"ref": ref}}

        m = re.search(
            r"(?:удали|удалить|убери)\s+(?:из\s+)?(?:задач[ауеи]?|задат[ьи]|todo)\s+(.+)",
            t,
        )
        if m:
            ref = m.group(1).strip()
            if ref:
                return {"type": "delete_todo", "slots": {"ref": ref}}
        
        # notes commands
        if "запомни" in t or "запишись" in t:
            m = re.search(r"(?:запомни|запишись)(?:\s+что)?\s+(.+)", t)
            if m:
                note = m.group(1).strip()
                return {"type": "add_note", "slots": {"text": note}}
        
        if "вспомни" in t or "напомни записал" in t or "прочитай заметк" in t:
            return {"type": "read_notes", "slots": {}}

        return {"type": "unknown", "slots": {"text": text}}
    
    

