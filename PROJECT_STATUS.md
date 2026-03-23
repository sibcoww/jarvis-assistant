# Project Status (March 2026)

## What’s implemented
- Offline ASR (Vosk) with wake-word (Porcupine or Vosk text), push-to-talk, and continuous mode.
- Simple rule-based NLU with AI fallback to OpenRouter text completions; unknown commands route to AI, known commands stay offline.
- GUI (PySide6) with tray menu, Start/Stop, mic selection, wake-engine toggle, key inputs for OpenRouter and Porcupine, config reload/open, autostart toggle, and chat-context reset.
- Engine lifecycle hardening: idempotent `stop()`, caller-aware debug logs, continuous timeout drops mode without stopping engine, AI/NLU/executor exceptions are caught and don’t halt the loop.
- Key store hardening: atomic writes to `~/.jarvis/keys.json`, merge-with-existing, skip empty overwrites; GUI refuses to save empty keys.
- OpenRouter client hardening: structured content parsing, network/timeout handling, 402→free fallback, 429 handling, empty-response retry, `last_error` hygiene.

## Recent tests (add/updated)
- `tests/test_engine.py`: stop idempotency/log-once, AI failure/empty/429 safety (engine keeps running), continuous timeout safety, stop→start→stop reset.
- `tests/test_key_store.py`: partial updates merge keys, empty save doesn’t erase stored key, atomic writes.
- `tests/test_ai_client.py`: empty choices/text, 429 handling, 402 paid→free fallback.

## Known gaps / to-verify manually
- Run `pytest tests/test_engine.py tests/test_key_store.py tests/test_ai_client.py` in the configured venv.
- Quick GUI pass: Start/Stop, tray stop/quit, saving OpenRouter/Porcupine keys (empty saves are skipped), AI test button behaviour when key missing.
- README lower sections still describe legacy ML NLU/coverage; use the status block above as the source of truth.

## Key files touched recently
- `src/jarvis/engine.py`: lifecycle guards, caller debug, continuous timeout helper, try/except around NLU/AI/executor.
- `src/jarvis/key_store.py`: atomic writes, merge, empty-skip logic.
- `gui/app.py`: skip saving empty keys; same stop call sites (GUI stop button, tray stop/quit, voice exit).
- Tests: `tests/test_engine.py`, `tests/test_key_store.py`, `tests/test_ai_client.py`.

## Next ideas (if continuing)
1) Refresh README to align with SimpleNLU + OpenRouter fallback (remove legacy ML NLU claims).
2) Add integration tests covering GUI-driven key saves (could be functional/Qt or unit-level abstraction).
3) Optionally prune deprecated ML NLU references and unused docs if no longer planned.
