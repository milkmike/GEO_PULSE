from contextlib import contextmanager
from types import SimpleNamespace
from datetime import datetime, timezone

import httpx
import pytest

from scripts import analyze, build_threads, collect
from src.pipeline.filter import is_relevant
from tests.test_collect import _article, _database, _seed


@pytest.mark.parametrize("suffix", [" [emb:123]", " [text:0123456789abcdef0123]"])
def test_thread_internal_identity_is_not_public_copy(monkeypatch, suffix):
    saved = []

    class Session:
        def execute(self, statement, params):
            saved.append(params)
            return SimpleNamespace(fetchone=lambda: None)

    monkeypatch.setattr(build_threads, "calculate_importance_v2", lambda _: {
        "importance": 4, "velocity": 0, "sentiment_shift": 0,
    })
    articles = [dict(article_id=i, sentiment=1, action_level=2,
                     published_at=datetime.now(timezone.utc)) for i in (1, 2)]
    key = "specific trade agreement" + suffix
    build_threads.upsert_thread(Session(), "BY", key, articles, ["specific trade agreement"],
                                generate_narrative=False)
    assert saved[0]["thread_key"] == key
    assert saved[0]["title"] == "specific trade agreement"


@pytest.mark.parametrize("result", [None, RuntimeError("provider unavailable")])
def test_failed_analysis_stays_unprocessed(monkeypatch, result):
    monkeypatch.setattr(analyze, "OPENROUTER_API_KEY", "test")

    def provider(**kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(analyze, "analyze_sentiment", provider)
    row = SimpleNamespace(id=1, title="Russia signs treaty", body="", source_name="Test", country_code="KZ")
    assert analyze._analyze_one(row) is None


def test_missing_key_does_not_create_completed_placeholder(monkeypatch):
    monkeypatch.setattr(analyze, "OPENROUTER_API_KEY", "")
    row = SimpleNamespace(id=1, title="Russia signs treaty", body="")
    assert analyze._analyze_one(row) is None


def test_queue_failure_is_not_counted_as_success(monkeypatch):
    monkeypatch.setattr(analyze, "_redis_available", True)
    monkeypatch.setattr(analyze, "dequeue", lambda *args, **kwargs: {"article_id": 42})
    monkeypatch.setattr(analyze, "_analyze_article_by_id", lambda _: False)
    dead = []
    monkeypatch.setattr(analyze, "enqueue", lambda *args: dead.append(args))
    monkeypatch.setattr(analyze, "get_redis", lambda: pytest.fail("failed work counted as success"))
    assert analyze.process_from_queue() == analyze.QUEUE_PROCESSED
    assert len(dead) == 1
    assert dead[0][0] == analyze.Q_DEAD_LETTER
    assert dead[0][1]["article_id"] == 42


@pytest.mark.parametrize("title", [
    "Russland greift Kyjiw an", "روسيا توقع اتفاقية جديدة", "俄罗斯与阿塞拜疆签署协议",
    "Rusia firma un acuerdo", "La Russie signe un accord", "Rusya yeni anlaşma imzaladı",
    "Rússia assina acordo", "ロシアが協定に署名", "러시아 정상 회담", "Росія підписала угоду",
    "Rossiya bilan muzokaralar boshlandi",
    "Atacuri aeriene ruseşti la Kiev", "Atacuri aeriene rusești la Kiev",
])
def test_multilingual_russia_is_not_dropped(title):
    assert is_relevant(title)


@pytest.mark.parametrize("title", ["Cisco releases new router", "Disco festival tonight", "Precision agriculture grows"])
def test_short_acronyms_do_not_match_inside_words(title):
    assert not is_relevant(title)


@pytest.mark.parametrize("title", [
    "Gazprom signs gas deal", "Rosatom builds nuclear plant", "Rosneft earnings",
    "Lukoil signs a contract", "Russian Railways opens route", "Санкционные ограничения",
    "Переговоры о санкциях", "Rosatom's new project",
])
def test_company_aliases_and_sanctions_forms_reach_analysis(title):
    assert is_relevant(title)


@pytest.mark.parametrize("title", ["NotRosatomCompany launches", "Gazprometer device", "Migrant birds arrive"])
def test_company_aliases_do_not_admit_unrelated_substrings(title):
    assert not is_relevant(title)


@pytest.mark.parametrize(("title", "declared", "expected"), [
    ("Über neue Gespräche in Berlin", "de", "de"),
    ("Über neue Gespräche in Berlin", "ru", "und"),
    ("روسيا توقع اتفاقية جديدة", "ar", "ar"),
    ("俄罗斯与阿塞拜疆签署协议", "zh", "zh"),
    ("El gobierno anuncia un acuerdo", "es", "es"),
    ("Президент подписал соглашение", "ru", "ru"),
    ("", None, "und"),
])
def test_language_does_not_invent_russian_english_or_turkmen(title, declared, expected):
    assert collect._detect_language(title, declared) == expected


@pytest.mark.parametrize("rollback", [False, True])
def test_enqueue_happens_only_after_outer_commit(monkeypatch, rollback):
    engine, sessions = _database()
    source = _seed(sessions)
    committed = False
    queued = []

    @contextmanager
    def transaction():
        nonlocal committed
        with sessions.begin() as session:
            yield session
            if rollback:
                raise RuntimeError("commit failed")
        committed = True

    def enqueue(*args):
        assert committed, "article can be dequeued before DB commit"
        queued.append(args)

    monkeypatch.setattr(collect, "get_session", transaction)
    monkeypatch.setattr(collect, "find_duplicate", lambda *args: None)
    monkeypatch.setattr(collect, "_enqueue_article", enqueue)
    articles = [_article("committed", "EL PAÍS", "https://elpais.com", "elpais.com")]
    if rollback:
        with pytest.raises(RuntimeError, match="commit failed"):
            collect._save_source(source, articles)
        assert queued == []
    else:
        assert collect._save_source(source, articles) == (1, 0, 0)
        assert len(queued) == 1
    engine.dispose()


def test_disconnected_embedding_groups_with_identical_keys_survive():
    class Session:
        def execute(self, *args):
            return SimpleNamespace(fetchall=lambda: [])

    articles = [
        {"article_id": aid, "country_code": "KZ", "has_embedding": True,
         "event_key": "x" * 240, "title": "Treaty signed", "action_level": 2}
        for aid in (9, 3)
    ]
    result = build_threads.cluster_pass1_embeddings(Session(), articles, use_llm_pair_judge=False)
    reverse = build_threads.cluster_pass1_embeddings(Session(), articles[::-1], use_llm_pair_judge=False)
    assert len(result) == 2
    assert sorted(a["article_id"] for group in result.values() for a in group) == [3, 9]
    assert result == reverse
    assert len({key.split(":", 1)[1][:200] for key in result}) == 2


def test_connected_article_with_higher_action_does_not_rename_thread():
    class Session:
        def execute(self, statement, params):
            ids = params["ids"]
            return SimpleNamespace(fetchall=lambda: [
                SimpleNamespace(id1=ids[0], id2=i, similarity=.95) for i in ids[1:]
            ])

    rows = [{"article_id": i, "country_code": "BY", "has_embedding": True,
             "event_key": label, "title": label, "action_level": action}
            for i, label, action in [(3, "trade agreement signed", 2),
                                      (9, "trade agreement signed", 2),
                                      (12, "major new agreement", 5)]]
    before = build_threads.cluster_pass1_embeddings(Session(), rows[:2], use_llm_pair_judge=False)
    after = build_threads.cluster_pass1_embeddings(Session(), rows, use_llm_pair_judge=False)
    assert list(before) == list(after)
    assert len(next(iter(after.values()))) == 3


def test_auth_failure_cools_down_without_repeated_paid_requests(monkeypatch):
    from src import llm

    monkeypatch.setattr(llm, "OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(llm, "OLLAMA_URL", "")
    monkeypatch.setattr(llm, "track_api_call", lambda **kwargs: None)
    monkeypatch.setattr(llm, "_openrouter_blocked_until", 0.0, raising=False)
    clock = [100.0]
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock[0])
    calls = []

    def failure(*args):
        calls.append(1)
        response = httpx.Response(401, request=httpx.Request("POST", "https://openrouter.ai"))
        response.raise_for_status()

    monkeypatch.setattr(llm, "_call_openrouter", failure)
    for _ in range(2):
        with pytest.raises(llm.LLMError):
            llm.chat("Test", models=["model"])
    assert len(calls) == 1
    clock[0] += 301
    with pytest.raises(llm.LLMError):
        llm.chat("Test", models=["model"])
    assert len(calls) == 2


def test_embedding_group_keeps_identity_when_other_component_leaves_window():
    session = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: []))
    rows = [{"article_id": aid, "country_code": "KZ", "has_embedding": True,
             "event_key": "same event label", "title": "Treaty", "action_level": 2} for aid in (3, 9)]
    first = build_threads.cluster_pass1_embeddings(session, rows, use_llm_pair_judge=False)
    second = build_threads.cluster_pass1_embeddings(session, rows[1:], use_llm_pair_judge=False)
    previous_key = next(key for key, group in first.items() if group[0]["article_id"] == 9)
    assert list(second) == [previous_key]


def test_trigram_fallback_does_not_overwrite_embedding_component():
    session = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: []))
    rows = [
        {"article_id": 3, "country_code": "KZ", "has_embedding": True,
         "event_key": "x" * 240, "title": "Treaty", "action_level": 2},
        {"article_id": 9, "country_code": "KZ", "has_embedding": False,
         "event_key": "x" * 200, "title": "Other treaty", "action_level": 2},
    ]
    result = build_threads.cluster_pass1_embeddings(session, rows, use_llm_pair_judge=False)
    assert sorted(a["article_id"] for group in result.values() for a in group) == [3, 9]
    assert len(result) == 2


def test_redis_socket_deadline_exceeds_empty_queue_poll(monkeypatch):
    from src import queue
    monkeypatch.setattr(queue, "_pool", None)
    monkeypatch.setattr(queue, "REDIS_URL", "redis://localhost:6379/0")
    client = queue.get_redis()
    assert client.connection_pool.connection_kwargs.get("socket_timeout", 5) > 5
