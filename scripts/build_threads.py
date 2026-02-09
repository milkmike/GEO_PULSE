"""Narrative Threads builder — clusters event_keys into threads with LLM narratives."""
import argparse
import json
import logging
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
logger = logging.getLogger("threads")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4"
SIMILARITY_THRESHOLD = 0.6
IMPORTANCE_THRESHOLD = 3.0
NARRATIVE_IMPORTANCE_THRESHOLD = 5.0


def get_headers() -> dict | None:
    if not OPENROUTER_API_KEY:
        return None
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cis-thermometer.app",
        "X-Title": "CIS Thermometer - Threads",
    }


def fetch_event_keys(session) -> list[dict]:
    """Fetch all articles with event_key from analysis table."""
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
          AND (an.event_key IS NOT NULL AND LENGTH(an.event_key) > 3
               OR an.raw_response->>'event_key' IS NOT NULL
                  AND LENGTH(an.raw_response->>'event_key') > 3)
          AND ar.published_at > NOW() - INTERVAL '30 days'
        ORDER BY ar.published_at ASC
    """)).fetchall()
    return [dict(r._mapping) for r in rows]


def cluster_event_keys(session, articles: list[dict]) -> dict[str, dict]:
    """Cluster similar event_keys per country using pg_trgm similarity.

    Returns: {(country_code, canonical_key): {articles: [...], ...}}
    """
    # Group by country first
    by_country: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        if a["event_key"]:
            by_country[a["country_code"]].append(a)

    clusters: dict[tuple, dict] = {}

    for country_code, country_articles in by_country.items():
        # Get unique event_keys for this country
        unique_keys = list({a["event_key"] for a in country_articles})

        if not unique_keys:
            continue

        # Build similarity groups using pg_trgm
        # For each pair of event_keys, check similarity
        key_to_cluster: dict[str, str] = {}
        cluster_canonical: dict[str, str] = {}  # cluster_id -> canonical key

        for i, key in enumerate(unique_keys):
            if key in key_to_cluster:
                continue

            # This key starts a new cluster
            key_to_cluster[key] = key
            cluster_canonical[key] = key

            # Find similar keys using DB
            if len(unique_keys) > 1:
                # Build PostgreSQL array literal for text[]
                _escaped = [k.replace("'", "''") for k in unique_keys]
                _pg_array = "ARRAY[" + ",".join(f"'{k}'" for k in _escaped) + "]::text[]"
                similar_rows = session.execute(text(f"""
                    SELECT unnest AS candidate,
                           similarity(unnest, :key) AS sim
                    FROM unnest({_pg_array}) AS unnest
                    WHERE similarity(unnest, :key) > :threshold
                      AND unnest != :key
                    ORDER BY sim DESC
                """), {
                    "key": key,
                    "threshold": SIMILARITY_THRESHOLD,
                }).fetchall()

                for row in similar_rows:
                    candidate = row.candidate
                    if candidate not in key_to_cluster:
                        key_to_cluster[candidate] = key

        # Now group articles by cluster
        for article in country_articles:
            ek = article["event_key"]
            canonical = key_to_cluster.get(ek, ek)
            cluster_key = (country_code, canonical)

            if cluster_key not in clusters:
                clusters[cluster_key] = {
                    "country_code": country_code,
                    "thread_key": canonical,
                    "articles": [],
                    "event_keys": set(),
                }

            clusters[cluster_key]["articles"].append(article)
            clusters[cluster_key]["event_keys"].add(ek)

    return clusters


def determine_arc_phase(articles: list[dict]) -> str:
    """Determine narrative arc phase based on article timeline."""
    if not articles:
        return "emerging"

    now = datetime.now(timezone.utc)
    dates = [a["published_at"] for a in articles if a["published_at"]]
    if not dates:
        return "emerging"

    first = min(dates)
    last = max(dates)
    age_hours = (now - first).total_seconds() / 3600
    silence_hours = (now - last).total_seconds() / 3600

    # resolved: no new articles for > 48h
    if silence_hours > 48:
        return "resolved"

    # dormant: no new articles for > 24h but < 48h
    if silence_hours > 24:
        return "cooling"

    # emerging: less than 24h old
    if age_hours < 24:
        return "emerging"

    # Check if escalating or at peak
    # Split timeline into halves and compare volume
    mid = first + (last - first) / 2
    first_half = [a for a in articles if a["published_at"] and a["published_at"] <= mid]
    second_half = [a for a in articles if a["published_at"] and a["published_at"] > mid]

    if len(second_half) > len(first_half):
        return "escalating"
    elif len(second_half) < len(first_half) * 0.7:
        return "cooling"
    else:
        return "peak"


def calculate_importance(articles: list[dict]) -> float:
    """Calculate importance score = article_count * max_action_level * tier_diversity."""
    if not articles:
        return 0.0

    article_count = len(articles)
    max_action = max((a.get("action_level") or 1) for a in articles)
    unique_tiers = len({a.get("tier", "mainstream") for a in articles})

    return round(article_count * max_action * unique_tiers, 2)


def generate_narrative(
    country_code: str,
    thread_key: str,
    articles: list[dict],
    avg_sentiment: float,
) -> str | None:
    """Generate narrative summary via LLM."""
    headers = get_headers()
    if headers is None:
        logger.warning("OPENROUTER_API_KEY not set, skipping narrative generation")
        return None

    country = COUNTRY_NAMES.get(country_code, country_code)

    # Build articles list for prompt
    articles_sorted = sorted(articles, key=lambda a: a.get("published_at") or datetime.min.replace(tzinfo=timezone.utc))
    articles_text = "\n".join(
        f"- [{str(a.get('published_at', '?'))[:16]}] {a.get('title', '?')} "
        f"(sentiment: {a.get('sentiment', '?')}, source: {a.get('source_name', '?')})"
        for a in articles_sorted[:15]  # Limit to 15 articles
    )

    prompt = f"""Ты аналитик. Опиши развитие этого сюжета в 2-3 предложения.
Страна: {country}
Ключ события: {thread_key}
Статьи (хронологически):
{articles_text}
Средний sentiment: {avg_sentiment:+.2f}
Формат: краткий нарратив, как развивался сюжет. Начни с сути, потом динамика."""

    try:
        # Ensure prompt is clean UTF-8 (replace any problematic chars)
        prompt_clean = prompt.encode("utf-8", errors="replace").decode("utf-8")
        response = httpx.post(
            OPENROUTER_URL,
            headers=headers,
            json={
                "model": MODEL,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt_clean}],
            },
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
        narrative = data["choices"][0]["message"]["content"].strip()
        return narrative
    except Exception as e:
        logger.error(f"Failed to generate narrative for {thread_key}: {e}", exc_info=True)
        return None


def generate_title(thread_key: str, articles: list[dict]) -> str:
    """Generate a human-readable title from the thread key and top article."""
    # Use the most common/representative article title as base
    if articles:
        # Pick article with highest action_level
        best = max(articles, key=lambda a: (a.get("action_level") or 1))
        title = best.get("title", thread_key)
        if len(title) > 200:
            title = title[:197] + "..."
        return title
    return thread_key[:200]


def upsert_threads(session, clusters: dict) -> int:
    """UPSERT thread clusters into the database. Returns count of upserted threads."""
    count = 0

    for (country_code, canonical_key), cluster in clusters.items():
        articles = cluster["articles"]
        if not articles:
            continue

        article_count = len(articles)
        sentiments = [a["sentiment"] for a in articles if a["sentiment"] is not None]
        avg_sentiment = round(sum(sentiments) / len(sentiments), 2) if sentiments else 0.0
        max_action = max((a.get("action_level") or 1) for a in articles)
        importance = calculate_importance(articles)

        if importance < IMPORTANCE_THRESHOLD:
            continue

        dates = [a["published_at"] for a in articles if a["published_at"]]
        first_seen = min(dates) if dates else None
        last_seen = max(dates) if dates else None

        arc_phase = determine_arc_phase(articles)

        # Determine status from arc_phase
        if arc_phase == "resolved":
            status = "resolved"
        elif arc_phase == "cooling" and last_seen and (datetime.now(timezone.utc) - last_seen).total_seconds() > 48 * 3600:
            status = "dormant"
        else:
            status = "developing"

        title = generate_title(canonical_key, articles)

        # Generate narrative for important threads
        narrative = None
        if importance >= NARRATIVE_IMPORTANCE_THRESHOLD:
            narrative = generate_narrative(country_code, canonical_key, articles, avg_sentiment)

        # UPSERT
        result = session.execute(text("""
            INSERT INTO threads (
                country_code, thread_key, title, narrative, status, arc_phase,
                first_seen, last_seen, article_count, avg_sentiment,
                max_action_level, importance_score, generated_at
            ) VALUES (
                :country_code, :thread_key, :title, :narrative, :status, :arc_phase,
                :first_seen, :last_seen, :article_count, :avg_sentiment,
                :max_action_level, :importance_score, NOW()
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
                generated_at = NOW()
            RETURNING id
        """), {
            "country_code": country_code,
            "thread_key": canonical_key,
            "title": title,
            "narrative": narrative,
            "status": status,
            "arc_phase": arc_phase,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "article_count": article_count,
            "avg_sentiment": avg_sentiment,
            "max_action_level": max_action,
            "importance_score": importance,
        })

        thread_id = result.fetchone()[0]

        # Update thread_articles
        article_ids = [a["article_id"] for a in articles]
        if article_ids:
            # Delete old links and re-insert
            session.execute(text("DELETE FROM thread_articles WHERE thread_id = :tid"), {"tid": thread_id})
            for aid in article_ids:
                session.execute(text("""
                    INSERT INTO thread_articles (thread_id, article_id)
                    VALUES (:tid, :aid)
                    ON CONFLICT DO NOTHING
                """), {"tid": thread_id, "aid": aid})

        count += 1

    return count


def build_threads():
    """Main thread building logic."""
    logger.info("Building narrative threads...")

    with get_session() as session:
        # Fetch articles with event_keys
        articles = fetch_event_keys(session)
        logger.info(f"Fetched {len(articles)} articles with event_keys")

        if not articles:
            logger.info("No articles with event_keys found, skipping")
            return

        # Cluster event_keys
        clusters = cluster_event_keys(session, articles)
        logger.info(f"Found {len(clusters)} event_key clusters")

        # UPSERT threads
        count = upsert_threads(session, clusters)
        logger.info(f"Upserted {count} threads")

        # Mark old developing threads as dormant
        session.execute(text("""
            UPDATE threads
            SET status = 'dormant'
            WHERE status = 'developing'
              AND last_seen < NOW() - INTERVAL '72 hours'
        """))

    logger.info("Thread building complete")


def main():
    parser = argparse.ArgumentParser(description="GEO PULSE — Narrative Threads Builder")
    parser.add_argument("--loop", action="store_true", help="Run in loop mode")
    parser.add_argument("--interval", type=int, default=3600, help="Interval between runs (seconds)")
    args = parser.parse_args()

    wait_for_db()

    if args.loop:
        logger.info(f"Starting thread builder (interval: {args.interval}s)")
        build_threads()
        while True:
            time.sleep(args.interval)
            try:
                build_threads()
            except Exception as e:
                logger.error(f"Thread building error: {e}", exc_info=True)
    else:
        build_threads()


if __name__ == "__main__":
    main()
