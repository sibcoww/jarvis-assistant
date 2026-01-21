import argparse
from rich import print
from .nlu import SimpleNLU
from .executor import Executor
from .asr import MockASR

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

    print("[green]Скажи «Джарвис», чтобы активировать ассистента[/green]")

    while True:
        try:
            text = asr.listen_once()
        except KeyboardInterrupt:
            print("\n[yellow]Выход по Ctrl+C[/yellow]")
            break

        if not text:
            continue

        t = text.strip().lower()

        # выход (даже без wake-word)
        if t in ("exit", "quit", "выход"):
            print("[yellow]Завершение работы[/yellow]")
            break

        print(f"[cyan]Распознано:[/] {text}")

        # 1) если ещё не активирован — ждём wake-word
        if not armed:
            if has_wake_word(t):
                armed = True
                print("[green]✅ Активирован. Скажи команду...[/green]")
            # иначе игнорируем всё
            continue

        # 2) если активирован — обрабатываем следующую фразу как команду
        intent = nlu.parse(text)
        print(f"[magenta]Интент:[/] {intent}")
        ex.run(intent)

        # 3) после выполнения снова ждём wake-word
        armed = False
        print("[green]Скажи «Джарвис», чтобы активировать ассистента[/green]")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Запуск без микрофона (ввод с клавиатуры)")
    parser.add_argument("--asr", type=str, default="mock", help="mock|vosk")
    parser.add_argument("--model", type=str, default="models/vosk-model-ru-0.42",
                        help="Путь к модели Vosk")
    args = parser.parse_args()

    if args.asr == "vosk" and not args.mock:
        try:
            from .vosk_asr import VoskASR
            print(f"[green]Jarvis — режим Vosk[/green]  (модель: {args.model})")
            asr = VoskASR(model_path=args.model)
        except Exception as e:
            print(f"[red]Ошибка инициализации VoskASR:[/] {e}")
            print("[yellow]Падаем обратно в mock-режим[/yellow]")
            asr = MockASR()
    else:
        print("[green]Jarvis — mock режим[/green]")
        asr = MockASR()

    run_loop(asr)

if __name__ == "__main__":
    main()
