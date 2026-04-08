import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List


class MemoryStore:
    def __init__(self):
        self.base_dir = Path.home() / ".jarvis"
        self.profile_path = self.base_dir / "user_profile.json"
        self.memories_path = self.base_dir / "memories.jsonl"
        self.profile = self._load_profile()
        self.memories = self._load_memories()
        self._prune_low_signal_memories(persist=True)

    def _load_profile(self) -> Dict:
        try:
            if not self.profile_path.exists():
                return {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
            data = json.loads(self.profile_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("nickname", "")
                return data
        except Exception:
            pass
        return {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}

    def _load_memories(self) -> List[Dict]:
        entries: List[Dict] = []
        try:
            if not self.memories_path.exists():
                return entries
            for raw in self.memories_path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                item = json.loads(raw)
                if isinstance(item, dict) and item.get("text"):
                    entries.append(item)
        except Exception:
            return []
        return entries[-500:]

    def _save_profile(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.profile_path.write_text(
            json.dumps(self.profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _save_memories(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(item, ensure_ascii=False) for item in self.memories[-500:]]
        self.memories_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    @staticmethod
    def _keywords(text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Zа-яА-Я0-9]{3,}", (text or "").lower())
        stop = {"это", "как", "что", "для", "про", "или", "меня", "тебя", "есть", "могу"}
        return {t for t in tokens if t not in stop}

    @staticmethod
    def _normalize_text(text: str) -> str:
        clean = (text or "").lower().strip()
        clean = re.sub(r"[^\w\sа-яА-Яa-zA-Z0-9]", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean

    @staticmethod
    def _jaccard_similarity(text_a: str, text_b: str) -> float:
        a_tokens = set(MemoryStore._normalize_text(text_a).split())
        b_tokens = set(MemoryStore._normalize_text(text_b).split())
        if not a_tokens or not b_tokens:
            return 0.0
        return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def sanitize_user_memory_text(text: str) -> str:
        """Убираем разговорный шум/обвязку перед сохранением в память."""
        s = (text or "").strip()
        if not s:
            return ""
        # Частые вводные/паразиты в начале фразы.
        s = re.sub(r"^(?:еще|ещё|йоу|ну|кстати|короче|типа|блин|слушай)\b[\s,.:;-]*", "", s, flags=re.IGNORECASE)
        # Обвязка "запомни информацию обо мне ...".
        s = re.sub(
            r"^(?:запомни|запиши)\s+(?:информацию|инфу)\s+обо?\s+мне\b[\s,.:;-]*",
            "",
            s,
            flags=re.IGNORECASE,
        )
        s = re.sub(r"\s+", " ", s).strip(" ,.;:-")
        return s

    @staticmethod
    def canonicalize_personal_fact_text(text: str) -> str:
        s = (text or "").strip()
        if not s:
            return ""
        lowered = s.lower()
        m = re.match(r"^меня\s+зовут\s+(.+)$", lowered, flags=re.IGNORECASE)
        if m:
            name = m.group(1).strip(" .,!?:;\"'")
            if name:
                return f"Пользователя зовут {name}"
        m = re.match(r"^(?:я\s+)?работаю\s+(.+)$", lowered, flags=re.IGNORECASE)
        if m:
            tail = m.group(1).strip(" .,!?:;\"'")
            if tail:
                return f"Пользователь работает {tail}"
        m = re.match(r"^(?:я\s+)?учусь\s+(.+)$", lowered, flags=re.IGNORECASE)
        if m:
            tail = m.group(1).strip(" .,!?:;\"'")
            if tail:
                return f"Пользователь учится {tail}"
        m = re.match(r"^(?:я\s+)?живу\s+(.+)$", lowered, flags=re.IGNORECASE)
        if m:
            tail = m.group(1).strip(" .,!?:;\"'")
            if tail:
                return f"Пользователь живет {tail}"
        return s

    def _replace_name_memory(self, name: str):
        clean_name = (name or "").strip()
        if not clean_name:
            return
        prefix = "пользователя зовут "
        self.memories = [
            item
            for item in self.memories
            if not str(item.get("text", "")).strip().lower().startswith(prefix)
        ]

    @staticmethod
    def _is_low_signal_memory_text(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return True
        # Шумные/тестовые/неинформативные фразы, которые не нужно хранить.
        noisy_phrases = {
            "временная мысль",
            "истинное сегодня",
            "истинно сегодня",
        }
        if t in noisy_phrases:
            return True
        # Слишком короткие обрывки обычно появляются из ASR-шума.
        if len(t) < 4:
            return True
        return False

    def _prune_low_signal_memories(self, persist: bool = False):
        before = len(self.memories)
        self.memories = [
            item for item in self.memories if not self._is_low_signal_memory_text(str(item.get("text") or ""))
        ]
        if persist and len(self.memories) != before:
            self._save_memories()

    @staticmethod
    def _dedup_texts(items: List[str], limit: int) -> List[str]:
        out: List[str] = []
        for text in items:
            t = (text or "").strip()
            if not t:
                continue
            # Схлопываем близкие повторы в отображении.
            if any(
                t.lower() == x.lower() or MemoryStore._jaccard_similarity(t, x) >= 0.6
                for x in out
            ):
                continue
            out.append(t)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _is_expired(item: Dict) -> bool:
        ttl_days = item.get("ttl_days")
        if not isinstance(ttl_days, int) or ttl_days <= 0:
            return False
        created_raw = item.get("created_at")
        if not created_raw:
            return False
        try:
            created = datetime.fromisoformat(created_raw)
        except Exception:
            return False
        age_days = (datetime.now() - created).days
        return age_days > ttl_days

    def _prune_expired(self):
        self.memories = [item for item in self.memories if not self._is_expired(item)]

    def _upsert_memory(
        self,
        text: str,
        mem_type: str = "fact",
        importance: int = 2,
        layer: str | None = None,
        ttl_days: int | None = None,
        sensitive: bool = False,
    ):
        clean = self.canonicalize_personal_fact_text(self.sanitize_user_memory_text(text))
        if not clean:
            return
        if self._is_low_signal_memory_text(clean):
            return

        self._prune_expired()

        # Strict dedup and near-duplicate merge on recent window.
        for item in self.memories[-80:]:
            existing = item.get("text", "")
            if existing.lower() == clean.lower() or self._jaccard_similarity(existing, clean) >= 0.60:
                item["last_seen_at"] = self._now_iso()
                item["importance"] = max(int(item.get("importance", 1)), max(1, min(5, importance)))
                if isinstance(ttl_days, int) and ttl_days > 0:
                    item["ttl_days"] = ttl_days
                if sensitive:
                    item["sensitive"] = True
                return

        resolved_layer = layer or ("core" if mem_type in {"profile", "preference", "fact"} else "session")
        now = self._now_iso()
        payload = {
            "id": f"mem-{len(self.memories) + 1}",
            "text": clean,
            "type": mem_type,
            "layer": resolved_layer,
            "importance": max(1, min(5, importance)),
            "created_at": now,
            "last_seen_at": now,
            "sensitive": bool(sensitive),
        }
        if isinstance(ttl_days, int) and ttl_days > 0:
            payload["ttl_days"] = ttl_days
        self.memories.append(
            payload
        )

    def save_ai_suggestion(self, suggestion: Dict) -> bool:
        if not isinstance(suggestion, dict):
            return False
        if suggestion.get("save") is False:
            return False

        layer = str(suggestion.get("layer", "")).strip().lower()
        mem_type = str(suggestion.get("type", "")).strip().lower()
        value = self.canonicalize_personal_fact_text(
            self.sanitize_user_memory_text(str(suggestion.get("value", "")))
        )
        key = str(suggestion.get("key", "")).strip().lower()
        importance_raw = suggestion.get("importance", 3)

        if layer not in {"core", "session"}:
            return False
        if mem_type not in {"profile", "preference", "fact", "project"}:
            return False
        if not value:
            return False
        try:
            importance = int(importance_raw)
        except Exception:
            importance = 3

        sensitive = bool(suggestion.get("sensitive"))
        mapped_type = "temporary" if mem_type == "project" and layer == "session" else mem_type
        ttl_days = 14 if layer == "session" else None
        if layer == "core" and mem_type == "profile" and key == "name":
            self._replace_name_memory(value)
        self._upsert_memory(
            value, mapped_type, importance, layer=layer, ttl_days=ttl_days, sensitive=sensitive
        )

        if layer == "core":
            if mem_type == "profile" and key in {"name", "nickname", "education", "occupation", "age"}:
                if key in {"name", "nickname"}:
                    self.profile[key] = value
                else:
                    facts = self.profile.setdefault("facts", [])
                    if value not in facts:
                        facts.append(value)
                        self.profile["facts"] = facts[-60:]
            elif mem_type == "preference":
                prefs = self.profile.setdefault("preferences", [])
                if value not in prefs:
                    prefs.append(value)
                    self.profile["preferences"] = prefs[-40:]
            elif mem_type in {"fact", "project"}:
                facts = self.profile.setdefault("facts", [])
                if value not in facts:
                    facts.append(value)
                    self.profile["facts"] = facts[-60:]

        self._save_profile()
        self._save_memories()
        return True

    @staticmethod
    def _user_marked_sensitive(lowered: str) -> bool:
        markers = (
            "секрет",
            "конфиденциаль",
            "только между нами",
            "не говори никому",
            "никому не говори",
            "не отправляй",
            "не отправляй это",
            "это приват",
        )
        return any(m in lowered for m in markers)

    def learn_from_user_text(self, user_text: str):
        text = self.sanitize_user_memory_text(user_text)
        if not text:
            return
        lowered = text.lower()
        mark_sensitive = self._user_marked_sensitive(lowered)

        name_match = re.search(
            r"\b(?:меня\s+зовут|моё\s+имя|мое\s+имя)\s+([А-Яа-яA-Za-z][А-Яа-яA-Za-z\- ]{1,40})\b",
            text,
            re.IGNORECASE,
        )
        if name_match:
            extracted_name = name_match.group(1).strip()
            if extracted_name:
                self.profile["name"] = extracted_name
                self._replace_name_memory(extracted_name)
                self._upsert_memory(
                    f"Пользователя зовут {extracted_name}",
                    "profile",
                    4,
                    layer="core",
                    sensitive=mark_sensitive,
                )

        nickname_match = re.search(
            r"\b(?:мой\s+никнейм|мой\s+ник|никнейм)\s*(?:это|—|-|:)?\s*([А-Яа-яA-Za-z0-9_\- ]{2,40})\b",
            text,
            re.IGNORECASE,
        )
        if nickname_match:
            nickname = nickname_match.group(1).strip().lower()
            if "без пробела" in lowered:
                nickname = nickname.replace(" ", "")
            if nickname:
                self.profile["nickname"] = nickname
                self._upsert_memory(
                    f"Никнейм пользователя: {nickname}",
                    "profile",
                    4,
                    layer="core",
                    sensitive=mark_sensitive,
                )

        # Spelled nickname: "с и б к о ш а" -> "сибкоша"
        letters = re.findall(r"\b[А-Яа-яA-Za-z]\b", text)
        if len(letters) >= 4:
            nickname = "".join(letters).lower()
            self.profile["nickname"] = nickname
            self._upsert_memory(
                f"Никнейм пользователя: {nickname}",
                "profile",
                4,
                layer="core",
                sensitive=mark_sensitive,
            )

        # Correction: "без а в конце" trims trailing letter from current nickname.
        if "без" in lowered and "в конце" in lowered and self.profile.get("nickname"):
            letter_match = re.search(r"\bбез\s+([А-Яа-яA-Za-z])\b", lowered)
            if letter_match:
                trailing = letter_match.group(1).lower()
                current = str(self.profile.get("nickname", ""))
                if current.lower().endswith(trailing):
                    updated = current[:-1].strip()
                    if updated:
                        self.profile["nickname"] = updated
                        self._upsert_memory(
                            f"Никнейм пользователя: {updated}",
                            "profile",
                            4,
                            layer="core",
                            sensitive=mark_sensitive,
                        )

        if "без пробела" in lowered and self.profile.get("nickname"):
            current = str(self.profile.get("nickname", ""))
            normalized = current.replace(" ", "")
            if normalized and normalized != current:
                self.profile["nickname"] = normalized
                self._upsert_memory(
                    f"Никнейм пользователя: {normalized}",
                    "profile",
                    4,
                    layer="core",
                    sensitive=mark_sensitive,
                )

        if any(x in lowered for x in ["мне нравится", "я люблю", "предпочитаю"]):
            self._upsert_memory(text, "preference", 3, layer="core", sensitive=mark_sensitive)
            prefs = self.profile.setdefault("preferences", [])
            if text not in prefs:
                prefs.append(text)
                self.profile["preferences"] = prefs[-40:]

        if any(x in lowered for x in ["я работаю", "я учусь", "я живу", "мой город", "я изучаю"]):
            self._upsert_memory(text, "fact", 3, layer="core", sensitive=mark_sensitive)
            facts = self.profile.setdefault("facts", [])
            if text not in facts:
                facts.append(text)
                self.profile["facts"] = facts[-60:]

        # Age-like facts: "мне 21 год", "мне двадцать один год"
        if "мне" in lowered and "год" in lowered:
            self._upsert_memory(text, "fact", 3, layer="core", sensitive=mark_sensitive)
            facts = self.profile.setdefault("facts", [])
            if text not in facts:
                facts.append(text)
                self.profile["facts"] = facts[-60:]

        if any(x in lowered for x in ["сегодня", "завтра", "на этой неделе", "временн", "пока что"]):
            self._upsert_memory(
                text, "temporary", 2, layer="session", ttl_days=14, sensitive=mark_sensitive
            )

        self._save_profile()
        self._save_memories()

    def clear_all(self):
        self.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
        self.memories = []
        self._save_profile()
        self._save_memories()

    def clear_recent_context(self, last_n: int = 10):
        """Clear only recent/ephemeral context, preserving core user profile memory."""
        if last_n <= 0:
            return

        protected_types = {"profile", "fact", "preference"}
        removable_indexes = [
            idx
            for idx, item in enumerate(self.memories)
            if item.get("layer") == "session" or item.get("type") not in protected_types
        ]
        # Remove only the most recent removable entries.
        indexes_to_remove = set(removable_indexes[-last_n:])
        if indexes_to_remove:
            self.memories = [item for idx, item in enumerate(self.memories) if idx not in indexes_to_remove]
            self._save_memories()

    def describe_profile(self) -> str:
        self._prune_expired()
        parts: List[str] = []
        name = (self.profile.get("name") or "").strip()
        nickname = (self.profile.get("nickname") or "").strip()
        if name:
            parts.append(f"Имя: {name}")
        if nickname:
            parts.append(f"Никнейм: {nickname}")

        prefs = self._dedup_texts(list(reversed(self.profile.get("preferences", []))), limit=3)
        if prefs:
            parts.append("Предпочтения: " + "; ".join(prefs))

        facts = self._dedup_texts(list(reversed(self.profile.get("facts", []))), limit=4)
        if facts:
            parts.append("Факты: " + "; ".join(facts))

        if not parts and not self.memories:
            return "Пока ничего важного не запомнил."

        # Человеческий хвост последних записей без повторов.
        tail_raw: List[str] = []
        for m in reversed(self.memories[-20:]):
            text = str(m.get("text", "")).strip()
            if not text:
                continue
            if m.get("sensitive"):
                text = f"🔒 {text}"
            tail_raw.append(text)
        tail = self._dedup_texts(tail_raw, limit=4)
        if tail:
            parts.append("Последние записи: " + "; ".join(tail))
        return "\n".join(parts)

    def build_context(self, query: str, top_k: int = 4, for_cloud: bool = True) -> str:
        self._prune_expired()
        qk = self._keywords(query)
        scored = []
        for item in self.memories[-200:]:
            if for_cloud and item.get("sensitive"):
                continue
            tk = self._keywords(item.get("text", ""))
            overlap = len(qk & tk) if qk else 0
            recency_bonus = 0
            try:
                seen = datetime.fromisoformat(item.get("last_seen_at", ""))
                age_days = max(0, (datetime.now() - seen).days)
                recency_bonus = max(0, 5 - min(age_days, 5))
            except Exception:
                recency_bonus = 0
            score = overlap * 3 + int(item.get("importance", 1)) + recency_bonus
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [item for score, item in scored if score > 0][:top_k]
        if not selected:
            tail = self.memories[-min(top_k, len(self.memories)) :]
            if for_cloud:
                tail = [m for m in tail if not m.get("sensitive")]
            selected = tail
        if not selected:
            return ""
        core = [item for item in selected if item.get("layer") != "session"]
        session = [item for item in selected if item.get("layer") == "session"]
        lines: List[str] = []
        if core:
            lines.append("Core memory:")
            lines.extend([f"- {item.get('text', '')}" for item in core])
        if session:
            lines.append("Session memory:")
            lines.extend([f"- {item.get('text', '')}" for item in session])
        return "\n".join(lines)

    def forget_last_entry(self) -> str | None:
        """Удалить последнюю добавленную запись в memories.jsonl (по порядку в списке)."""
        self._prune_expired()
        if not self.memories:
            return None
        removed = self.memories.pop()
        self._save_memories()
        return str(removed.get("text") or "").strip() or None

    def forget_matching_substring(self, needle: str) -> str | None:
        """Удалить последнюю запись, в тексте которой встречается подстрока (без учёта регистра)."""
        self._prune_expired()
        n = (needle or "").strip().lower()
        if not n:
            return None
        for idx in range(len(self.memories) - 1, -1, -1):
            text = str(self.memories[idx].get("text", "")).lower()
            if n in text:
                removed = self.memories.pop(idx)
                self._save_memories()
                return str(removed.get("text") or "").strip() or None
        return None

    def remove_by_id(self, mem_id: str) -> bool:
        if not mem_id:
            return False
        for i, item in enumerate(self.memories):
            if item.get("id") == mem_id:
                self.memories.pop(i)
                self._save_memories()
                return True
        return False

    def clear_session_layer(self):
        """Удалить все записи слоя session из memories (core профиль в user_profile не трогаем)."""
        self._prune_expired()
        before = len(self.memories)
        self.memories = [m for m in self.memories if m.get("layer") != "session"]
        if len(self.memories) != before:
            self._save_memories()

    def remove_fact_by_substring(self, needle: str) -> str | None:
        """Удалить последнюю core-запись (fact/profile/preference) по подстроке."""
        self._prune_expired()
        n = (needle or "").strip().lower()
        if not n:
            return None
        for idx in range(len(self.memories) - 1, -1, -1):
            item = self.memories[idx]
            if item.get("layer") == "session":
                continue
            if item.get("type") not in {"fact", "profile", "preference"}:
                continue
            text = str(item.get("text", "")).lower()
            if n in text:
                removed = self.memories.pop(idx)
                self._save_memories()
                return str(removed.get("text") or "").strip() or None
        return None

    def list_memories_for_ui(self, limit: int = 50) -> List[Dict]:
        """Свежие записи первыми; для GUI."""
        self._prune_expired()
        slice_ = self.memories[-limit:] if limit > 0 else []
        out: List[Dict] = []
        for m in reversed(slice_):
            out.append(
                {
                    "id": m.get("id"),
                    "text": m.get("text"),
                    "layer": m.get("layer"),
                    "type": m.get("type"),
                    "sensitive": bool(m.get("sensitive")),
                }
            )
        return out
