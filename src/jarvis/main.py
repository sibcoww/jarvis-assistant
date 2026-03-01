import argparse
import logging
from .logger import setup_logging
from .nlu import SimpleNLU
from .executor import Executor

logger = logging.getLogger(__name__)

WAKE_WORDS = {
    "джарвис", "жарвис", "джервис",
    "джанверт", "джанвис", "джаврис"  # частые ошибки vosk
}
def has_wake_word(text: str) -> bool:
    text = text.lower()
    words = text.split()
    return any(w in WAKE_WORDS for w in words)

def run_loop(asr):
    nlu = SimpleNLU()
    ex = Executor()

    armed = False  # ждём ли команду после wake-word

    logger.info("🔵 Скажи «Джарвис», чтобы активировать ассистента")

    while True:
        try:
            text = asr.listen_once()
        except KeyboardInterrupt:
            logger.info("🟡 Выход по Ctrl+C")
            break

        if not text:
            continue

        t = text.strip().lower()

        # выход (даже без wake-word)
        if t in ("exit", "quit", "выход"):
            logger.info("🟡 Завершение работы")
            break

        logger.info(f"🎤 Распознано: {text}")

        # 1) если ещё не активирован — ждём wake-word
        if not armed:
            if has_wake_word(t):
                armed = True
                logger.info("✅ Активирован. Скажи команду...")
            # иначе игнорируем всё
            continue

        # 2) если активирован — обрабатываем следующую фразу как команду
        intent = nlu.parse(text)
        logger.info(f"🧠 Интент: {intent}")
        ex.run(intent)

        # 3) после выполнения снова ждём wake-word
        armed = False
        logger.info("🔵 Скажи «Джарвис», чтобы активировать ассистента")


def main():
    setup_logging()
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Запуск без микрофона (ввод с клавиатуры)")
    parser.add_argument("--asr", type=str, default="mock", help="mock|vosk")
    parser.add_argument("--model", type=str, default="models/vosk-model-ru-0.42",
                        help="Путь к модели Vosk")
    args = parser.parse_args()

    if args.asr == "vosk" and not args.mock:
        try:
            from .vosk_asr import VoskASR
            logger.info(f"🔵 Jarvis — режим Vosk (модель: {args.model})")
            asr = VoskASR(model_path=args.model)
        except Exception as e:
            logger.error(f"🔴 Ошибка инициализации VoskASR: {e}")
            logger.warning("🟡 Падаем обратно в mock-режим")
            from .asr import MockASR
            asr = MockASR()
    else:
        logger.info("🔵 Jarvis — mock режим")
        from .asr import MockASR
        asr = MockASR()

    run_loop(asr)

if __name__ == "__main__":
    main()
