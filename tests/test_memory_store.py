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


def test_sensitive_skipped_in_cloud_context(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store._upsert_memory("Публичный факт про Python", "fact", 3, layer="core", sensitive=False)
    store._upsert_memory("Секретный номер карты 1234", "fact", 4, layer="core", sensitive=True)

    pub = store.build_context("python совет", top_k=4, for_cloud=True).lower()
    assert "python" in pub
    assert "1234" not in pub

    full = store.build_context("карта", top_k=4, for_cloud=False).lower()
    assert "1234" in full


def test_forget_last_and_substring(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store._upsert_memory("один", "fact", 2)
    store._upsert_memory("два про котиков", "fact", 2)
    assert store.forget_matching_substring("котик") == "два про котиков"
    assert len(store.memories) == 1
    assert store.forget_last_entry() == "один"
    assert store.memories == []


def test_clear_session_layer_keeps_core(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store._upsert_memory("ядро", "fact", 3, layer="core")
    store._upsert_memory("сессия", "temporary", 2, layer="session", ttl_days=7)
    store.clear_session_layer()
    assert len(store.memories) == 1
    assert store.memories[0].get("text") == "ядро"


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


def test_memory_sanitize_conversational_noise(tmp_path):
    store = MemoryStore()
    store.base_dir = tmp_path
    store.profile_path = tmp_path / "user_profile.json"
    store.memories_path = tmp_path / "memories.jsonl"
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = []

    store.learn_from_user_text("ещё йоу я работаю в академии шаг")
    assert any("пользователь работает в академии шаг" in m.get("text", "").lower() for m in store.memories)
    assert all("ещё йоу" not in m.get("text", "").lower() for m in store.memories)


def test_prune_low_signal_memories_on_init_and_upsert(tmp_path):
    base = tmp_path / "jarvis_home"
    base.mkdir(parents=True, exist_ok=True)
    memories_path = base / "memories.jsonl"
    memories_path.write_text(
        "\n".join(
            [
                '{"id":"mem-1","text":"временная мысль","type":"temporary","layer":"session","importance":1}',
                '{"id":"mem-2","text":"я работаю преподавателем","type":"fact","layer":"core","importance":3}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    store = MemoryStore()
    store.base_dir = base
    store.profile_path = base / "user_profile.json"
    store.memories_path = memories_path
    store.profile = {"name": "", "nickname": "", "preferences": [], "facts": [], "updated_at": None}
    store.memories = store._load_memories()
    store._prune_low_signal_memories(persist=False)

    assert all("временная мысль" not in m.get("text", "").lower() for m in store.memories)

    before = len(store.memories)
    store._upsert_memory("истинное сегодня", "temporary", 2, layer="session", ttl_days=14)
    assert len(store.memories) == before
