from jarvis.memory_store import MemoryStore
from datetime import datetime, timedelta


def test_memory_learns_name_and_preference(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store.learn_from_user_text("Меня зовут Алексей")
    store.learn_from_user_text("Мне нравится короткие ответы")

    assert store.profile["name"] == "Алексей"
    assert any("нравится" in p.lower() for p in store.profile["preferences"])
    assert len(store.memories) >= 2


def test_memory_context_retrieval(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store.learn_from_user_text("Я изучаю английский")
    store.learn_from_user_text("Мне нравится Python")

    context = store.build_context("дай совет по английскому", top_k=2).lower()
    assert "англий" in context


def test_memory_learns_age_fact(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store.learn_from_user_text("мне двадцать один год")
    assert any("мне двадцать один год" in f.lower() for f in store.profile["facts"])


def test_memory_dedup_near_duplicates(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store._upsert_memory("Мне нравится Python и backend", "preference", 3)
    store._upsert_memory("мне нравится python backend", "preference", 2)
    assert len(store.memories) == 1


def test_memory_ttl_prunes_expired(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    old = (datetime.now() - timedelta(days=20)).isoformat(timespec="seconds")
    store.memories.append(
        {
            "id": "mem-old",
            "text": "Временный факт",
            "type": "temporary",
            "importance": 2,
            "created_at": old,
            "last_seen_at": old,
            "ttl_days": 14,
        }
    )
    store.memories.append(
        {
            "id": "mem-keep",
            "text": "Постоянный факт",
            "type": "fact",
            "importance": 3,
            "created_at": old,
            "last_seen_at": old,
        }
    )

    ctx = store.build_context("факт", top_k=5).lower()
    assert "временный факт" not in ctx
    assert "постоянный факт" in ctx


def test_memory_nickname_capture_and_correction(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store.learn_from_user_text("мой никнейм это сик кош")
    assert store.profile["nickname"] == "сик кош"

    store.learn_from_user_text("с и б к о ш а")
    assert store.profile["nickname"] == "сибкоша"

    store.learn_from_user_text("без а в конце")
    assert store.profile["nickname"] == "сибкош"


def test_memory_name_with_spaces_and_no_false_name_from_study_phrase(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store.learn_from_user_text("меня зовут сиб кош")
    assert store.profile["name"] == "сиб кош"

    store.learn_from_user_text("я учусь на IT")
    assert store.profile["name"] == "сиб кош"
