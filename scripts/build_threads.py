"""Narrative Threads v2 — LLM-powered dedup, structured narratives, smart scoring."""
import argparse
import json
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text

from src.config import COUNTRY_NAMES, OPENROUTER_API_KEY
from src.db import get_session, wait_for_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("threads-v2")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4"

# Clustering config
TRGM_THRESHOLD = 0.25  # Low threshold to catch near-duplicates (предлоги, падежи)
MIN_ARTICLES_FOR_THREAD = 2
MIN_IMPORTANCE = 3.0
NARRATIVE_MIN_IMPORTANCE = 5.0

# Blacklist garbage event_keys
BLACKLIST = [
    'прогноз погоды', 'архив сайта', 'поиск на сайте', 'курс валют',
    'новости дня', 'лента новостей', 'главные новости', 'обзор прессы',
    'новости мира', 'важные новости', 'последние новости',
]


def get_headers() -> dict | None:
    if not OPENROUTER_API_KEY:
        return None
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://geopulse.app",
        "X-Title": "GeoPulse Threads v2",
    }


def llm_call(prompt: str, max_tokens: int = 500) -> str | None:
    """Make LLM API call. Returns response text or None."""
    headers = get_headers()
    if not headers:
        return None
    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers=headers,
            json={"model": MODEL, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return None


def llm_json(prompt: str, max_tokens: int = 800) -> dict | None:
    """LLM call expecting JSON response."""
    raw = llm_call(prompt, max_tokens)
    if not raw:
        return None
    # Extract JSON from response (handle ```json blocks)
    text_clean = raw
    if "```json" in text_clean:
        text_clean = text_clean.split("```json")[1].split("```")[0]
    elif "```" in text_clean:
        text_clean = text_clean.split("```")[1].split("```")[0]
    try:
        return json.loads(text_clean.strip())
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM JSON: {raw[:200]}")
        return None


# ── Step 1: Fetch articles ──────────────────────────────

def fetch_articles(session, days: int = 30) -> list[dict]:
    """Fetch all relevant articles with event_keys."""
    rows = session.execute(text("""
        SELECT
            an.id AS analysis_id,
            an.article_id,
            COALESCE(an.event_key, an.raw_response->>'event_key') AS event_key,
            an.sentiment,
            an.action_level,
            an.event_type,
            ar.title,
            ar.url,
            ar.published_at,
            s.country_code,
            s.name AS source_name,
            s.tier
        FROM analysis an
        JOIN articles ar ON an.article_id = ar.id
        JOIN sources s ON ar.source_id = s.id
        WHERE an.is_relevant = true
          AND ar.published_at > NOW() - INTERVAL :days
          AND (
            (an.event_key IS NOT NULL AND LENGTH(TRIM(an.event_key)) > 5)
            OR (an.raw_response->>'event_key' IS NOT NULL
                AND LENGTH(TRIM(an.raw_response->>'event_key')) > 5)
          )
        ORDER BY ar.published_at ASC
    """), {"days": f"{days} days"}).fetchall()

    articles = []
    for r in rows:
        ek = (r.event_key or "").strip().lower()
        if not ek or any(bl in ek for bl in BLACKLIST):
            continue
        articles.append({
            "analysis_id": r.analysis_id,
            "article_id": r.article_id,
            "event_key": ek,
            "sentiment": float(r.sentiment) if r.sentiment is not None else None,
            "action_level": r.action_level or 1,
            "event_type": r.event_type,
            "title": r.title,
            "url": r.url,
            "published_at": r.published_at,
            "country_code": r.country_code.strip(),
            "source_name": r.source_name,
            "tier": r.tier or "mainstream",
        })
    return articles


# ── Step 1.5: Normalize event keys ──────────────────────

import re as _re

# Russian stopwords/prepositions that cause false splits
_STOPWORDS = {"в", "на", "и", "с", "о", "об", "из", "к", "по", "за", "для", "от", "до", "при", "про", "между"}

# Transliteration normalization (common variants)
_TRANSLIT_MAP = {
    "вэнс": "вэнс", "венс": "вэнс", "vance": "вэнс",
    "байден": "байден", "biden": "байден",
    "трамп": "трамп", "trump": "трамп",
}


def normalize_event_key(key: str) -> str:
    """Normalize event key: remove stopwords, unify transliterations."""
    words = key.lower().strip().split()
    normalized = []
    for w in words:
        w = _TRANSLIT_MAP.get(w, w)
        if w not in _STOPWORDS and len(w) > 1:
            normalized.append(w)
    return " ".join(normalized)


# ── Step 2: Two-pass clustering ─────────────────────────

def cluster_pass1_trgm(session, articles: list[dict]) -> dict[str, list[dict]]:
    """Pass 1: Group by country, then cluster with pg_trgm."""
    by_country: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        by_country[a["country_code"]].append(a)

    # result: {cluster_id -> [articles]}
    clusters: dict[str, list[dict]] = {}

    for cc, cc_articles in by_country.items():
        unique_keys = list({a["event_key"] for a in cc_articles})
        if not unique_keys:
            continue

        # Pre-merge: exact match after normalization
        norm_map: dict[str, str] = {}  # normalized -> first original key
        for k in unique_keys:
            nk = normalize_event_key(k)
            if nk not in norm_map:
                norm_map[nk] = k

        # Build union-find via pg_trgm similarity
        parent: dict[str, str] = {k: k for k in unique_keys}

        # Union keys with same normalized form
        for nk, first_key in norm_map.items():
            for k in unique_keys:
                if normalize_event_key(k) == nk:
                    # Union with the first key for this normalized form
                    pass  # Will be handled by union below

        # Pre-union: same normalized key → same cluster
        for k in unique_keys:
            nk = normalize_event_key(k)
            canonical = norm_map[nk]
            if canonical != k:
                parent[k] = canonical

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Check all pairs via DB (batch)
        for i, key_a in enumerate(unique_keys):
            if len(unique_keys) <= 1:
                break
            # Find all keys similar to key_a
            others = [k for k in unique_keys[i+1:] if find(k) != find(key_a) or True]
            if not others:
                continue

            try:
                pg_array = "ARRAY[" + ",".join(f"'{k.replace(chr(39), chr(39)+chr(39))}'" for k in others) + "]::text[]"
                sim_rows = session.execute(text(f"""
                    SELECT unnest AS candidate, similarity(unnest, :key) AS sim
                    FROM unnest({pg_array})
                    WHERE similarity(unnest, :key) > :threshold
                """), {"key": key_a, "threshold": TRGM_THRESHOLD}).fetchall()

                for row in sim_rows:
                    union(key_a, row.candidate)
            except Exception as e:
                logger.warning(f"trgm similarity failed for {key_a[:50]}: {e}")

        # Group articles by cluster
        groups: dict[str, list[str]] = defaultdict(list)
        for k in unique_keys:
            groups[find(k)].append(k)

        for canonical, keys in groups.items():
            cluster_id = f"{cc}:{canonical}"
            cluster_articles = [a for a in cc_articles if a["event_key"] in set(keys)]
            if cluster_articles:
                clusters[cluster_id] = cluster_articles

    return clusters


def cluster_pass2_llm(clusters: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Pass 2: LLM-based merge of similar clusters within same country."""
    # Group clusters by country
    by_country: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for cid, articles in clusters.items():
        cc = cid.split(":")[0]
        by_country[cc].append((cid, articles))

    merged: dict[str, list[dict]] = {}
    merge_count = 0

    for cc, cc_clusters in by_country.items():
        if len(cc_clusters) <= 1:
            for cid, arts in cc_clusters:
                merged[cid] = arts
            continue

        # Extract cluster summaries for LLM
        summaries = []
        for cid, arts in cc_clusters:
            keys = list({a["event_key"] for a in arts})
            summaries.append({
                "id": cid,
                "keys": keys[:5],
                "title": arts[0]["title"][:100] if arts else "",
                "count": len(arts),
            })

        # Only send to LLM if there are enough clusters to potentially merge
        if len(summaries) < 2:
            for cid, arts in cc_clusters:
                merged[cid] = arts
            continue

        # Batch: send up to 30 clusters at a time
        summaries_text = "\n".join(
            f"[{s['id']}] keys: {', '.join(s['keys'][:5])} | {s['count']} articles | \"{s['title'][:120]}\""
            for s in summaries[:40]
        )

        prompt = f"""Ты аналитик медиа. Ниже список кластеров новостей из страны {COUNTRY_NAMES.get(cc, cc)}.
Найди кластеры которые описывают ОДИН И ТОТ ЖЕ сюжет и должны быть объединены.

ВАЖНО: мержи если:
- Один и тот же инцидент/событие, даже если заголовки сформулированы по-разному
- Разные аспекты одного события (напр. "запрет въезда Х в Россию" и "Россия запретила въезд Х")
- Одно и то же лицо/действие, но разные написания (Вэнс/Венс, Сабуров/Saburov)
- Один сюжет с фокусом на разных странах (напр. "визит Вэнса в Армению" и "визит Вэнса в Армению и Азербайджан")

НЕ мержи если это реально разные события, даже если про одну тему.

Кластеры:
{summaries_text}

Верни JSON массив групп для объединения. Каждая группа — массив id кластеров.
Если кластер уникален, НЕ включай его. Только группы для мержа.
Формат: {{"merge": [["id1", "id2"], ["id3", "id4", "id5"]]}}
Если нечего мержить: {{"merge": []}}"""

        result = llm_json(prompt, max_tokens=500)

        # Apply merges
        merged_ids: set[str] = set()
        if result and "merge" in result:
            for group in result["merge"]:
                if len(group) < 2:
                    continue
                # Merge all into first
                primary_id = group[0]
                combined_articles = []
                for gid in group:
                    for cid, arts in cc_clusters:
                        if cid == gid:
                            combined_articles.extend(arts)
                            merged_ids.add(cid)
                            break
                if combined_articles:
                    merged[primary_id] = combined_articles
                    merge_count += len(group) - 1
                    logger.info(f"  Merged {len(group)} clusters → {primary_id}")

        # Add unmerged clusters
        for cid, arts in cc_clusters:
            if cid not in merged_ids:
                merged[cid] = arts

    if merge_count:
        logger.info(f"LLM merged {merge_count} duplicate clusters")
    return merged


# ── Step 3: Scoring ─────────────────────────────────────

def calculate_importance_v2(articles: list[dict]) -> dict:
    """Calculate enhanced importance metrics."""
    if not articles:
        return {"importance": 0, "velocity": 0, "sentiment_shift": 0}

    n = len(articles)
    dates = sorted([a["published_at"] for a in articles if a["published_at"]])
    sentiments = [a["sentiment"] for a in articles if a["sentiment"] is not None]
    tiers = {a["tier"] for a in articles}
    sources = {a["source_name"] for a in articles}
    max_action = max((a["action_level"] or 1) for a in articles)

    # Velocity: articles per day (higher = faster growing)
    if len(dates) >= 2:
        span_hours = max((dates[-1] - dates[0]).total_seconds() / 3600, 1)
        velocity = n / (span_hours / 24)
    else:
        velocity = 0

    # Sentiment shift: difference between first and last quarter
    sentiment_shift = 0.0
    if len(sentiments) >= 4:
        q = len(sentiments) // 4
        first_q = sum(sentiments[:q]) / q
        last_q = sum(sentiments[-q:]) / q
        sentiment_shift = last_q - first_q

    # Freshness: decay based on last article age
    now = datetime.now(timezone.utc)
    if dates:
        hours_since_last = (now - dates[-1]).total_seconds() / 3600
        freshness = max(0.1, 1.0 - (hours_since_last / (7 * 24)))  # decay over 7 days
    else:
        freshness = 0.1

    # Source diversity: log scale
    source_diversity = math.log(len(sources) + 1) / math.log(10)

    # Tier coverage (max 5 tiers)
    tier_bonus = len(tiers) * 0.5

    # Action escalation: was there an increase in action_level?
    action_levels = [a["action_level"] or 1 for a in articles]
    if len(action_levels) >= 2:
        mid = len(action_levels) // 2
        first_avg = sum(action_levels[:mid]) / mid
        second_avg = sum(action_levels[mid:]) / (len(action_levels) - mid)
        escalation = max(1.0, second_avg / max(first_avg, 0.5))
    else:
        escalation = 1.0

    # Final importance
    volume = math.log(n + 1) * 3
    importance = round(
        volume * max_action * source_diversity * (1 + tier_bonus) * freshness * escalation,
        2
    )

    return {
        "importance": importance,
        "velocity": round(velocity, 2),
        "sentiment_shift": round(sentiment_shift, 3),
    }


def determine_arc_phase(articles: list[dict]) -> tuple[str, str]:
    """Returns (arc_phase, status)."""
    if not articles:
        return "emerging", "developing"

    now = datetime.now(timezone.utc)
    dates = [a["published_at"] for a in articles if a["published_at"]]
    if not dates:
        return "emerging", "developing"

    first = min(dates)
    last = max(dates)
    age_hours = (now - first).total_seconds() / 3600
    silence_hours = (now - last).total_seconds() / 3600

    if silence_hours > 72:
        return "resolved", "resolved"
    if silence_hours > 48:
        return "resolved", "dormant"
    if silence_hours > 24:
        return "cooling", "developing"
    if age_hours < 12:
        return "emerging", "developing"

    # Compare halves
    mid = first + (last - first) / 2
    first_half = sum(1 for a in articles if a["published_at"] and a["published_at"] <= mid)
    second_half = sum(1 for a in articles if a["published_at"] and a["published_at"] > mid)

    if second_half > first_half * 1.3:
        return "escalating", "developing"
    elif second_half < first_half * 0.5:
        return "cooling", "developing"
    else:
        return "peak", "developing"


# ── Step 4: Structured narrative ────────────────────────

def generate_structured_narrative(
    cc: str, thread_key: str, articles: list[dict], metrics: dict
) -> dict | None:
    """Generate structured narrative via LLM."""
    country = COUNTRY_NAMES.get(cc, cc)

    sorted_arts = sorted(articles, key=lambda a: a.get("published_at") or datetime.min.replace(tzinfo=timezone.utc))
    articles_text = "\n".join(
        f"- [{str(a.get('published_at', '?'))[:16]}] {a.get('title', '?')} "
        f"(sent: {a.get('sentiment', '?')}, action: {a.get('action_level', '?')}, "
        f"src: {a.get('source_name', '?')}, tier: {a.get('tier', '?')})"
        for a in sorted_arts[:20]
    )

    tiers = list({a["tier"] for a in articles})
    sources = list({a["source_name"] for a in articles})

    prompt = f"""Ты геополитический аналитик. Проанализируй сюжет и верни структурированный JSON.

Страна: {country}
Ключ: {thread_key}
Статей: {len(articles)}, источников: {len(sources)}, tier'ов: {len(tiers)}
Velocity: {metrics['velocity']:.1f} статей/день
Sentiment shift: {metrics['sentiment_shift']:+.3f}

Статьи (хронологически):
{articles_text}

Верни JSON:
{{
  "title": "краткий заголовок сюжета (до 100 символов)",
  "summary": "суть в 1 предложении",
  "dynamics": "как развивался сюжет, ключевые повороты (2-3 предложения)",
  "impact": "почему это важно для отношений с Россией (1 предложение)",
  "forecast": "куда движется, что ожидать (1 предложение)",
  "key_actors": ["список ключевых участников"],
  "tags": ["3-5 тематических тегов"]
}}"""

    return llm_json(prompt, max_tokens=800)


# ── Step 5: Upsert ──────────────────────────────────────

def upsert_thread(session, cc: str, canonical_key: str, articles: list[dict], all_keys: list[str]) -> int | None:
    """Upsert a single thread. Returns thread_id or None."""
    n = len(articles)
    if n < MIN_ARTICLES_FOR_THREAD:
        return None

    metrics = calculate_importance_v2(articles)
    if metrics["importance"] < MIN_IMPORTANCE:
        return None

    arc_phase, status = determine_arc_phase(articles)
    sentiments = [a["sentiment"] for a in articles if a["sentiment"] is not None]
    avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0
    max_action = max((a["action_level"] or 1) for a in articles)
    dates = [a["published_at"] for a in articles if a["published_at"]]
    first_seen = min(dates) if dates else None
    last_seen = max(dates) if dates else None

    # Generate narrative for important threads
    summary_json = None
    title = canonical_key[:200]
    narrative = None

    if metrics["importance"] >= NARRATIVE_MIN_IMPORTANCE:
        structured = generate_structured_narrative(cc, canonical_key, articles, metrics)
        if structured:
            summary_json = structured
            title = structured.get("title", title)[:500]
            narrative = structured.get("summary", "")
            if structured.get("dynamics"):
                narrative += " " + structured["dynamics"]
    
    if not narrative:
        # Fallback: use best article title
        best = max(articles, key=lambda a: (a.get("action_level") or 1))
        title = best.get("title", canonical_key)[:500]

    result = session.execute(text("""
        INSERT INTO threads (
            country_code, thread_key, title, narrative, status, arc_phase,
            first_seen, last_seen, article_count, avg_sentiment,
            max_action_level, importance_score, velocity, sentiment_shift,
            merged_keys, summary_json, generated_at
        ) VALUES (
            :cc, :thread_key, :title, :narrative, :status, :arc_phase,
            :first_seen, :last_seen, :article_count, :avg_sentiment,
            :max_action, :importance, :velocity, :sentiment_shift,
            :merged_keys, :summary_json, NOW()
        )
        ON CONFLICT (country_code, thread_key)
        DO UPDATE SET
            title = EXCLUDED.title,
            narrative = COALESCE(EXCLUDED.narrative, threads.narrative),
            status = EXCLUDED.status,
            arc_phase = EXCLUDED.arc_phase,
            first_seen = LEAST(threads.first_seen, EXCLUDED.first_seen),
            last_seen = GREATEST(threads.last_seen, EXCLUDED.last_seen),
            article_count = EXCLUDED.article_count,
            avg_sentiment = EXCLUDED.avg_sentiment,
            max_action_level = EXCLUDED.max_action_level,
            importance_score = EXCLUDED.importance_score,
            velocity = EXCLUDED.velocity,
            sentiment_shift = EXCLUDED.sentiment_shift,
            merged_keys = EXCLUDED.merged_keys,
            summary_json = COALESCE(EXCLUDED.summary_json, threads.summary_json),
            generated_at = NOW()
        RETURNING id
    """), {
        "cc": cc,
        "thread_key": canonical_key[:200],
        "title": title,
        "narrative": narrative,
        "status": status,
        "arc_phase": arc_phase,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "article_count": n,
        "avg_sentiment": round(avg_sentiment, 2),
        "max_action": max_action,
        "importance": metrics["importance"],
        "velocity": metrics["velocity"],
        "sentiment_shift": metrics["sentiment_shift"],
        "merged_keys": all_keys,
        "summary_json": json.dumps(summary_json, ensure_ascii=False) if summary_json else None,
    })

    thread_id = result.fetchone()[0]

    # Update thread_articles
    article_ids = list({a["article_id"] for a in articles})
    session.execute(text("DELETE FROM thread_articles WHERE thread_id = :tid"), {"tid": thread_id})
    for aid in article_ids:
        session.execute(text("""
            INSERT INTO thread_articles (thread_id, article_id)
            VALUES (:tid, :aid) ON CONFLICT DO NOTHING
        """), {"tid": thread_id, "aid": aid})

    return thread_id


# ── Step 6: Find cross-thread relations ─────────────────

def link_related_threads(session):
    """Find and link related threads across countries."""
    session.execute(text("""
        UPDATE threads t1
        SET related_threads = (
            SELECT array_agg(DISTINCT t2.id)
            FROM threads t2
            WHERE t2.id != t1.id
              AND t2.country_code != t1.country_code
              AND similarity(t1.thread_key, t2.thread_key) > 0.3
              AND t2.importance_score >= 3
        )
        WHERE t1.importance_score >= 5
    """))


# ── Step 7: Cleanup old threads ─────────────────────────

def cleanup_duplicate_threads(session):
    """Remove duplicate threads that have very similar keys in the same country."""
    # Find pairs with high trgm similarity within same country
    try:
        dupes = session.execute(text("""
            SELECT t1.id AS keep_id, t2.id AS remove_id,
                   t1.thread_key AS keep_key, t2.thread_key AS remove_key,
                   t1.article_count AS keep_count, t2.article_count AS remove_count,
                   similarity(t1.thread_key, t2.thread_key) AS sim
            FROM threads t1
            JOIN threads t2 ON t1.country_code = t2.country_code
                           AND t1.id < t2.id
            WHERE similarity(t1.thread_key, t2.thread_key) > 0.5
              AND t1.status != 'resolved'
              AND t2.status != 'resolved'
        """)).fetchall()

        removed = 0
        for d in dupes:
            # Keep the one with more articles or higher importance
            if d.keep_count >= d.remove_count:
                remove_id, keep_id = d.remove_id, d.keep_id
            else:
                remove_id, keep_id = d.keep_id, d.remove_id

            # Move articles from removed thread to kept thread
            session.execute(text("""
                INSERT INTO thread_articles (thread_id, article_id)
                SELECT :keep_id, article_id FROM thread_articles
                WHERE thread_id = :remove_id
                ON CONFLICT DO NOTHING
            """), {"keep_id": keep_id, "remove_id": remove_id})

            # Update article count on kept thread
            new_count = session.execute(text(
                "SELECT COUNT(*) FROM thread_articles WHERE thread_id = :tid"
            ), {"tid": keep_id}).scalar()
            session.execute(text(
                "UPDATE threads SET article_count = :cnt WHERE id = :tid"
            ), {"cnt": new_count, "tid": keep_id})

            # Delete duplicate
            session.execute(text("DELETE FROM thread_articles WHERE thread_id = :tid"), {"tid": remove_id})
            session.execute(text("DELETE FROM threads WHERE id = :tid"), {"tid": remove_id})
            removed += 1
            logger.info(f"  Deduped: removed thread {remove_id} (merged into {keep_id}, sim={d.sim:.2f})")

        if removed:
            logger.info(f"Cleaned up {removed} duplicate threads")
    except Exception as e:
        logger.warning(f"Duplicate cleanup failed: {e}")


def cleanup_old_threads(session):
    """Mark old developing threads as dormant/resolved."""
    # First: remove duplicates
    cleanup_duplicate_threads(session)

    # Dormant: no articles for 72h
    session.execute(text("""
        UPDATE threads SET status = 'dormant'
        WHERE status = 'developing' AND last_seen < NOW() - INTERVAL '72 hours'
    """))
    # Resolved: no articles for 7 days
    session.execute(text("""
        UPDATE threads SET status = 'resolved', arc_phase = 'resolved'
        WHERE status IN ('developing', 'dormant') AND last_seen < NOW() - INTERVAL '7 days'
    """))
    # Delete very old low-importance resolved threads
    session.execute(text("""
        DELETE FROM threads
        WHERE status = 'resolved' AND importance_score < 5
          AND last_seen < NOW() - INTERVAL '30 days'
    """))


# ── Main ────────────────────────────────────────────────

def build_threads():
    """Main thread building pipeline."""
    logger.info("═══ Threads v2 build started ═══")

    with get_session() as session:
        # 1. Fetch
        articles = fetch_articles(session)
        logger.info(f"Fetched {len(articles)} articles with event_keys")
        if not articles:
            logger.info("No articles, skipping")
            return

        # 2. Cluster pass 1: pg_trgm
        clusters = cluster_pass1_trgm(session, articles)
        logger.info(f"Pass 1 (trgm): {len(clusters)} clusters")

        # 3. Cluster pass 2: LLM dedup
        clusters = cluster_pass2_llm(clusters)
        logger.info(f"Pass 2 (LLM): {len(clusters)} clusters after dedup")

        # 4. Upsert threads
        count = 0
        for cluster_id, cluster_articles in clusters.items():
            cc = cluster_id.split(":")[0]
            canonical_key = cluster_id.split(":", 1)[1] if ":" in cluster_id else cluster_id
            all_keys = list({a["event_key"] for a in cluster_articles})

            tid = upsert_thread(session, cc, canonical_key, cluster_articles, all_keys)
            if tid:
                count += 1

        logger.info(f"Upserted {count} threads")

        # 5. Link related threads
        try:
            link_related_threads(session)
            logger.info("Related threads linked")
        except Exception as e:
            logger.warning(f"Failed to link related threads: {e}")

        # 6. Cleanup
        cleanup_old_threads(session)
        logger.info("Cleanup done")

    logger.info("═══ Threads v2 build complete ═══")


def main():
    parser = argparse.ArgumentParser(description="GeoPulse — Narrative Threads v2")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=3600)
    args = parser.parse_args()

    wait_for_db()

    if args.loop:
        logger.info(f"Starting threads v2 (interval: {args.interval}s)")
        build_threads()
        while True:
            time.sleep(args.interval)
            try:
                build_threads()
            except Exception as e:
                logger.error(f"Thread build error: {e}", exc_info=True)
    else:
        build_threads()


if __name__ == "__main__":
    main()
