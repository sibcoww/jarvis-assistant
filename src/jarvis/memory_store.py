from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


class MemoryStore:
    """Simple long-term memory: one list of user facts."""

    def __init__(self, base_dir: Path | str | None = None, max_facts: int = 50):
        self.base_dir = Path(base_dir) if base_dir is not None else Path.home() / ".jarvis"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / "user_memory.json"
        self.max_facts = max_facts
        self.facts: list[dict[str, str]] = []
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.exists():
                self.facts = []
                return
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw_facts = data.get("facts", []) if isinstance(data, dict) else []
            if not isinstance(raw_facts, list):
                self.facts = []
                return
            cleaned: list[dict[str, str]] = []
            for row in raw_facts:
                if not isinstance(row, dict):
                    continue
                text = self.normalize_fact(str(row.get("text", "")))
                if not text:
                    continue
                cleaned.append(
                    {
                        "text": text,
                        "created_at": str(row.get("created_at") or self._now_iso()),
                    }
                )
            self.facts = cleaned[-self.max_facts :]
        except Exception:
            self.facts = []

    def _save(self) -> None:
        payload = {"facts": self.facts[-self.max_facts :]}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def normalize_fact(text: str) -> str:
        s = re.sub(r"\s+", " ", (text or "").strip())
        s = s.strip(" ,.;:-")
        if not s:
            return ""
        # Keep memory concise for diploma demo.
        if len(s) > 180:
            s = s[:180].rstrip(" ,.;:-")
        return s

    def add_fact(self, text: str) -> str:
        fact = self.normalize_fact(text)
        if not fact:
            return ""
        lower_fact = fact.lower()
        for row in self.facts:
            if str(row.get("text", "")).lower() == lower_fact:
                return ""
        self.facts.append({"text": fact, "created_at": self._now_iso()})
        if len(self.facts) > self.max_facts:
            self.facts = self.facts[-self.max_facts :]
        self._save()
        return fact

    def clear_all(self) -> None:
        self.facts = []
        self._save()

    def remove_last(self) -> str:
        if not self.facts:
            return ""
        removed = str(self.facts.pop().get("text", ""))
        self._save()
        return removed

    def remove_by_substring(self, fragment: str) -> str:
        token = self.normalize_fact(fragment).lower()
        if not token:
            return ""
        for idx in range(len(self.facts) - 1, -1, -1):
            text = str(self.facts[idx].get("text", ""))
            if token in text.lower():
                removed = self.facts.pop(idx).get("text", "")
                self._save()
                return str(removed)
        return ""

    def find_best_match_by_hint(self, hint: str) -> str:
        hint_words = {w for w in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", (hint or "").lower()) if len(w) >= 3}
        if not hint_words:
            return ""
        best_idx = -1
        best_score = 0
        for idx, row in enumerate(self.facts):
            text = str(row.get("text", "")).lower()
            words = set(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text))
            score = len(hint_words & words)
            if score > best_score:
                best_score = score
                best_idx = idx
        if best_idx < 0 or best_score == 0:
            return ""
        removed = self.facts.pop(best_idx).get("text", "")
        self._save()
        return str(removed)

    def list_facts(self, limit: int = 50) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        return self.facts[-limit:]

    def build_context(self, top_k: int = 6) -> str:
        rows = self.list_facts(top_k)
        if not rows:
            return ""
        return "\n".join(f"- {row.get('text', '')}" for row in rows if row.get("text"))
