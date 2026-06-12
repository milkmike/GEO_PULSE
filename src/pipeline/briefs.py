"""AI briefs — world brief «Россия и мир» + per-country dossier briefs.

worldmonitor pattern: inputs are hashed before the LLM call; if the input
state hasn't changed since the last brief, no tokens are spent. Output is
Russian markdown, stored in the briefs table (history) and served via API.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from src.countries import COUNTRIES, country_name_ru
from src.db import get_session
from src.llm import LLMError, chat
from src.pipeline.topics import TOPICS

logger = logging.getLogger(__name__)

WORLD_BRIEF_PROMPT = """Ты — дежурный аналитик платформы «Массаракш», отслеживающей отношения всех стран мира с Россией.

Ниже — машинная сводка за последние 24 часа: крупнейшие сдвиги индекса отношений, активные сигналы разведки медиаполя и главные заголовки.

ДАННЫЕ:
{data}

Составь брифинг «Россия и мир» на русском языке в markdown. Структура:
## Главное
3-5 пунктов: самые значимые изменения в отношениях стран с Россией за сутки.
## Эскалация
Страны, чьи отношения с РФ ухудшаются (с числами индекса).
## Деэскалация / сближение
Страны с потеплением (с числами).
## Сигналы
Самое важное из сигналов: конвергенция источников, замалчивания, информационные штормы.
## Следить
2-3 сюжета, которые могут развиться в ближайшие дни.

Заголовки в данных пронумерованы полем "n". Каждое фактическое утверждение,
основанное на заголовке, помечай сноской [n] (например: «...растёт напряжённость [3]»).
Используй ТОЛЬКО номера из данных, не выдумывай. Утверждения из индексов и
сигналов сносок не требуют.
Пиши сжато, фактологично, без воды. Если данных мало — так и скажи, не выдумывай."""

COUNTRY_BRIEF_PROMPT = """Ты — аналитик платформы «Массаракш». Составь досье-брифинг об отношениях страны {country} с Россией на основе машинной сводки.

ДАННЫЕ:
{data}

Структура (markdown, на русском):
## {country} ↔ Россия: текущее состояние
Индекс, уровень, динамика за 24ч/7д, что это значит.
## Структурный фон
Блоки, санкции, базовые факторы.
## Медиаполе
Что и как пишут СМИ страны о России (тон, темы, объём).
## Ключевые события
Последние значимые события из данных.
## Прогноз внимания
Что отслеживать дальше.

Заголовки в данных пронумерованы полем "n". Каждое фактическое утверждение,
основанное на заголовке, помечай сноской [n] (например: «...растёт напряжённость [3]»).
Используй ТОЛЬКО номера из данных, не выдумывай. Утверждения из индексов и
сигналов сносок не требуют.
Пиши сжато и фактологично. Не выдумывай события, которых нет в данных."""


TOPIC_BRIEF_PROMPT = """Ты — аналитик платформы «Массаракш». Составь тематический брифинг по теме «{topic_label}» в отношениях стран мира с Россией на основе машинной сводки.

ДАННЫЕ:
{data}

Структура (markdown, на русском):
## Главное
3-4 пункта по теме за период.
## Страны в фокусе
Где тема звучит сильнее всего (с цифрами).
## Тональность
Как тема окрашена в разных странах.
## Следить
1-2 сюжета, которые могут развиться.

Заголовки в данных пронумерованы полем "n". Каждое фактическое утверждение,
основанное на заголовке, помечай сноской [n] (например: «...растёт напряжённость [3]»).
Используй ТОЛЬКО номера из данных, не выдумывай. Утверждения из статистики
сносок не требуют.
Пиши сжато, фактологично. Не выдумывай события, которых нет в данных."""


def _hash_inputs(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def _last_brief(session, scope: str):
    return session.execute(
        text("""
            SELECT id, content, model, source_hash, created_at, meta FROM briefs
            WHERE scope = :scope ORDER BY created_at DESC LIMIT 1
        """),
        {"scope": scope},
    ).fetchone()


def gather_world_inputs(session) -> dict:
    """Top index movers + active signals + hot headlines for the world brief."""
    movers = session.execute(
        text("""
            SELECT DISTINCT ON (country_code) country_code, score, level, delta_24h
            FROM ru_index
            WHERE delta_24h IS NOT NULL
            ORDER BY country_code, time DESC
        """)
    ).fetchall()
    movers = sorted(movers, key=lambda r: abs(float(r.delta_24h or 0)), reverse=True)[:12]

    signals = session.execute(
        text("""
            SELECT signal_type, country_code, severity, title, description
            FROM signals
            WHERE created_at > NOW() - INTERVAL '24 hours'
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     confidence DESC
            LIMIT 15
        """)
    ).fetchall()

    headlines = session.execute(
        text("""
            SELECT s.country_code, ar.title, ar.url, s.name AS source_name,
                   a.sentiment, a.action_level
            FROM analysis a
            JOIN articles ar ON a.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE ar.published_at > NOW() - INTERVAL '24 hours'
              AND a.is_relevant = TRUE AND a.action_level >= 3
            ORDER BY a.action_level DESC, ar.reprint_count DESC
            LIMIT 15
        """)
    ).fetchall()

    gdelt_top = session.execute(
        text("""
            SELECT country_code, tone_avg, volume, article_samples
            FROM gdelt_daily
            WHERE day >= CURRENT_DATE - 1 AND article_samples IS NOT NULL
            ORDER BY volume DESC NULLS LAST
            LIMIT 8
        """)
    ).fetchall()

    citations = []

    def _cite(title, url, source, country):
        n = len(citations) + 1
        citations.append({"n": n, "title": title, "url": url,
                          "source": source, "country": country})
        return n

    tier1_headlines = []
    for r in headlines:
        entry = {"country": country_name_ru(r.country_code), "title": r.title,
                 "sentiment": float(r.sentiment or 0),
                 "action_level": int(r.action_level or 1)}
        if r.url:
            entry["n"] = _cite(r.title, r.url, r.source_name, r.country_code)
        tier1_headlines.append(entry)

    world_headlines = []
    for r in gdelt_top:
        for s in (r.article_samples or [])[:2]:
            entry = {"country": country_name_ru(r.country_code),
                     "title": s.get("title", ""),
                     "tone": float(r.tone_avg) if r.tone_avg is not None else None}
            if s.get("url"):
                entry["n"] = _cite(s.get("title", ""), s["url"], "GDELT", r.country_code)
            world_headlines.append(entry)

    return {
        "index_movers": [
            {"country": country_name_ru(r.country_code), "score": float(r.score),
             "level": r.level, "delta_24h": float(r.delta_24h)}
            for r in movers
        ],
        "signals": [
            {"type": r.signal_type, "country": country_name_ru(r.country_code or ""),
             "severity": r.severity, "title": r.title}
            for r in signals
        ],
        "tier1_headlines": tier1_headlines,
        "world_headlines": world_headlines[:12],
        "citations": citations,
    }


def gather_country_inputs(session, code: str) -> dict:
    """Dossier inputs for one country."""
    country = COUNTRIES.get(code, {})

    idx = session.execute(
        text("""
            SELECT score, structural, media, level, delta_24h, delta_7d, details
            FROM ru_index WHERE country_code = :cc
            ORDER BY time DESC LIMIT 1
        """),
        {"cc": code},
    ).fetchone()

    signals = session.execute(
        text("""
            SELECT signal_type, severity, title, description FROM signals
            WHERE country_code = :cc AND created_at > NOW() - INTERVAL '7 days'
            ORDER BY created_at DESC LIMIT 10
        """),
        {"cc": code},
    ).fetchall()

    headlines = session.execute(
        text("""
            SELECT ar.title, ar.url, s.name AS source_name,
                   a.sentiment, a.action_level, ar.published_at::date AS day
            FROM analysis a
            JOIN articles ar ON a.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE s.country_code = :cc AND a.is_relevant = TRUE
              AND ar.published_at > NOW() - INTERVAL '7 days'
            ORDER BY a.action_level DESC, ar.published_at DESC
            LIMIT 12
        """),
        {"cc": code},
    ).fetchall()

    gdelt = session.execute(
        text("""
            SELECT day, tone_avg, volume, article_samples FROM gdelt_daily
            WHERE country_code = :cc ORDER BY day DESC LIMIT 7
        """),
        {"cc": code},
    ).fetchall()

    citations = []

    def _cite(title, url, source, country):
        n = len(citations) + 1
        citations.append({"n": n, "title": title, "url": url,
                          "source": source, "country": country})
        return n

    own_media_headlines = []
    for r in headlines:
        entry = {"title": r.title, "sentiment": float(r.sentiment or 0),
                 "action_level": int(r.action_level or 1), "day": str(r.day)}
        if r.url:
            entry["n"] = _cite(r.title, r.url, r.source_name, code)
        own_media_headlines.append(entry)

    gdelt_headlines = []
    for r in gdelt:
        if r.article_samples:
            for s in r.article_samples[:8]:
                entry = {"title": s.get("title", "")}
                if s.get("url"):
                    entry["n"] = _cite(s.get("title", ""), s["url"], "GDELT", code)
                gdelt_headlines.append(entry)
            break

    return {
        "country": country.get("name_ru", code),
        "region": country.get("region"),
        "memberships": country.get("memberships", []),
        "sanctions_on_russia": country.get("sanctions_on_russia"),
        "index": {
            "score": float(idx.score) if idx else None,
            "structural": float(idx.structural) if idx and idx.structural is not None else None,
            "media": float(idx.media) if idx and idx.media is not None else None,
            "level": idx.level if idx else None,
            "delta_24h": float(idx.delta_24h) if idx and idx.delta_24h is not None else None,
            "delta_7d": float(idx.delta_7d) if idx and idx.delta_7d is not None else None,
        } if idx else None,
        "signals": [{"type": r.signal_type, "severity": r.severity, "title": r.title}
                    for r in signals],
        "own_media_headlines": own_media_headlines,
        "gdelt_tone_7d": [
            {"day": str(r.day), "tone": float(r.tone_avg) if r.tone_avg is not None else None,
             "volume": float(r.volume) if r.volume is not None else None}
            for r in gdelt
        ],
        "gdelt_headlines": gdelt_headlines,
        "citations": citations,
    }


def _save_brief(session, scope: str, content: str, model: str, source_hash: str, meta: dict):
    session.execute(
        text("""
            INSERT INTO briefs (scope, content, model, source_hash, meta, created_at)
            VALUES (:scope, :content, :model, :hash, CAST(:meta AS jsonb), NOW())
        """),
        {"scope": scope, "content": content, "model": model,
         "hash": source_hash, "meta": json.dumps(meta, ensure_ascii=False)},
    )


def generate_world_brief(force: bool = False) -> dict | None:
    """Generate the world brief if inputs changed since the last one."""
    with get_session() as session:
        inputs = gather_world_inputs(session)
        # Pop citations registry before hashing so it doesn't affect hash
        # (url availability may change without headline content changing).
        citations = inputs.pop("citations", [])

        if not any(inputs.values()):
            logger.info("World brief: no inputs yet, skipping")
            return None

        source_hash = _hash_inputs(inputs)
        last = _last_brief(session, "world")
        if last and last.source_hash == source_hash and not force:
            logger.info("World brief: inputs unchanged, skipping LLM call")
            return None

        prompt = WORLD_BRIEF_PROMPT.format(
            data=json.dumps(inputs, ensure_ascii=False, indent=1)[:14000]
        )

    try:
        content, model = chat(prompt, max_tokens=1500, temperature=0.3, script="briefs.py")
    except LLMError as e:
        logger.error(f"World brief LLM failed: {e}")
        return None

    from src.pipeline.citations import apply_citations
    content, used = apply_citations(content, {c["n"] for c in citations})

    with get_session() as session:
        _save_brief(session, "world", content, model, source_hash,
                    {"movers": len(inputs["index_movers"]),
                     "signals": len(inputs["signals"]),
                     "citations": [{**c, "used": c["n"] in used} for c in citations]})
    logger.info(f"World brief generated ({model}, {len(content)} chars)")
    return {"content": content, "model": model}


def generate_country_brief(code: str, max_age_hours: float = 6.0,
                           force: bool = False) -> dict | None:
    """Generate (or reuse) a country dossier brief."""
    code = code.upper()
    now = datetime.now(timezone.utc)

    with get_session() as session:
        last = _last_brief(session, code)
        if last and not force:
            age_h = (now - last.created_at).total_seconds() / 3600
            if age_h < max_age_hours:
                return {"content": last.content, "model": last.model,
                        "created_at": last.created_at.isoformat(), "cached": True,
                        "citations": (last.meta or {}).get("citations", [])}

        inputs = gather_country_inputs(session, code)
        if not inputs.get("index") and not inputs.get("own_media_headlines") \
                and not inputs.get("gdelt_tone_7d"):
            return None

        # Pop citations registry before hashing so url availability doesn't
        # trigger spurious cache misses when article content is unchanged.
        citations = inputs.pop("citations", [])

        source_hash = _hash_inputs(inputs)
        if last and last.source_hash == source_hash and not force:
            return {"content": last.content, "model": last.model,
                    "created_at": last.created_at.isoformat(), "cached": True,
                    "citations": (last.meta or {}).get("citations", [])}

        prompt = COUNTRY_BRIEF_PROMPT.format(
            country=country_name_ru(code),
            data=json.dumps(inputs, ensure_ascii=False, indent=1)[:12000],
        )

    try:
        content, model = chat(prompt, max_tokens=1200, temperature=0.3, script="briefs.py")
    except LLMError as e:
        logger.error(f"Country brief LLM failed for {code}: {e}")
        return None

    from src.pipeline.citations import apply_citations
    content, used = apply_citations(content, {c["n"] for c in citations})

    citations_meta = [{**c, "used": c["n"] in used} for c in citations]

    with get_session() as session:
        _save_brief(session, code, content, model, source_hash,
                    {"citations": citations_meta})
    logger.info(f"Country brief generated for {code} ({model})")
    return {"content": content, "model": model,
            "created_at": now.isoformat(), "cached": False,
            "citations": citations_meta}


def gather_topic_inputs(session, topic: str) -> dict:
    """Headlines and country stats for a topic-lens brief."""
    headlines = session.execute(
        text("""
            SELECT s.country_code, ar.title, ar.url, s.name AS source_name,
                   a.sentiment, a.action_level
            FROM analysis a
            JOIN articles ar ON a.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE a.is_relevant = TRUE AND ar.is_duplicate = FALSE
              AND :topic = ANY(a.topics)
              AND ar.published_at > NOW() - INTERVAL '7 days'
            ORDER BY a.action_level DESC NULLS LAST,
                     ar.reprint_count DESC NULLS LAST,
                     ar.published_at DESC
            LIMIT 20
        """),
        {"topic": topic},
    ).fetchall()

    country_stats = session.execute(
        text("""
            SELECT s.country_code, COUNT(*) AS articles, AVG(a.sentiment) AS avg_sentiment
            FROM analysis a
            JOIN articles ar ON a.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE a.is_relevant = TRUE AND ar.is_duplicate = FALSE
              AND :topic = ANY(a.topics)
              AND ar.published_at > NOW() - INTERVAL '14 days'
            GROUP BY s.country_code
            ORDER BY articles DESC
            LIMIT 12
        """),
        {"topic": topic},
    ).fetchall()

    citations = []

    def _cite(title, url, source, country):
        n = len(citations) + 1
        citations.append({"n": n, "title": title, "url": url,
                          "source": source, "country": country})
        return n

    numbered_headlines = []
    for r in headlines:
        entry = {"country": country_name_ru(r.country_code), "title": r.title,
                 "sentiment": float(r.sentiment or 0),
                 "action_level": int(r.action_level or 1)}
        if r.url:
            entry["n"] = _cite(r.title, r.url, r.source_name, r.country_code)
        numbered_headlines.append(entry)

    stats = []
    for r in country_stats:
        stats.append({
            "country": country_name_ru(r.country_code),
            "articles": int(r.articles),
            "avg_sentiment": round(float(r.avg_sentiment), 2) if r.avg_sentiment is not None else None,
        })

    return {
        "topic": topic,
        "label": TOPICS.get(topic, topic),
        "headlines": numbered_headlines,
        "country_stats": stats,
        "citations": citations,
    }


def generate_topic_brief(topic: str, max_age_hours: float = 6.0,
                         force: bool = False) -> dict | None:
    """Generate (or reuse) a topic-lens brief."""
    scope = f"topic:{topic}"
    now = datetime.now(timezone.utc)

    with get_session() as session:
        last = _last_brief(session, scope)
        if last and not force:
            age_h = (now - last.created_at).total_seconds() / 3600
            if age_h < max_age_hours:
                return {"content": last.content, "model": last.model,
                        "created_at": last.created_at.isoformat(), "cached": True,
                        "citations": (last.meta or {}).get("citations", [])}

        inputs = gather_topic_inputs(session, topic)
        if not inputs.get("headlines") and not inputs.get("country_stats"):
            return None

        # Pop citations before hashing so url availability doesn't cause
        # spurious cache misses when headline content is unchanged.
        citations = inputs.pop("citations", [])

        source_hash = _hash_inputs(inputs)
        if last and last.source_hash == source_hash and not force:
            return {"content": last.content, "model": last.model,
                    "created_at": last.created_at.isoformat(), "cached": True,
                    "citations": (last.meta or {}).get("citations", [])}

        prompt = TOPIC_BRIEF_PROMPT.format(
            topic_label=TOPICS.get(topic, topic),
            data=json.dumps(inputs, ensure_ascii=False, indent=1)[:12000],
        )

    try:
        content, model = chat(prompt, max_tokens=1000, temperature=0.3, script="briefs.py")
    except LLMError as e:
        logger.error(f"Topic brief LLM failed for {topic}: {e}")
        return None

    from src.pipeline.citations import apply_citations
    content, used = apply_citations(content, {c["n"] for c in citations})

    citations_meta = [{**c, "used": c["n"] in used} for c in citations]

    with get_session() as session:
        _save_brief(session, scope, content, model, source_hash,
                    {"citations": citations_meta})
    logger.info(f"Topic brief generated for {topic} ({model})")
    return {"content": content, "model": model,
            "created_at": now.isoformat(), "cached": False,
            "citations": citations_meta}
