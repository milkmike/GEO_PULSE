"""Generate weekly narrative digests for each country."""
import argparse
import json
import logging
import os
import time

import httpx
from sqlalchemy import text

from src.config import COUNTRY_NAMES
from src.db import get_session
from src.api_tracker import track_api_call, track_duration

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "anthropic/claude-sonnet-4"

DIGEST_PROMPT = """Ты аналитик-международник. Напиши краткую сводку (4-6 предложений, по-русски) об отношениях {country} с Россией за последнюю неделю.

Данные:
- Текущая температура отношений: {temperature}° (шкала -100..+100, где +100 = союзники, -100 = враги)
- Тренд: {trend}
- Ключевые события за неделю (отсортированы по влиянию):

{events_text}

Требования:
1. Начни с общей характеристики: что происходит между {country} и Россией
2. Упомяни 2-3 ключевых события и их значение
3. Укажи тренд — отношения улучшаются, ухудшаются или стабильны
4. Если есть паттерн (маятник, спираль, дрейф) — назови его
5. Пиши живым языком, как колумнист, не как робот
6. НЕ используй числовые значения sentiment/action_level — переводи в слова
7. Вставляй ссылки на ключевые события в формате [краткое описание](url)

Ответь ТОЛЬКО текстом сводки с markdown-ссылками, без заголовков и маркеров."""


def generate_digest(country_code: str) -> dict | None:
    """Generate narrative digest for one country."""
    country_name = COUNTRY_NAMES.get(country_code, country_code)

    with get_session() as session:
        # Get current temperature
        temp_row = session.execute(text("""
            SELECT temperature, trend FROM temperature
            WHERE country_code = :cc
            ORDER BY time DESC LIMIT 1
        """), {"cc": country_code}).fetchone()

        if not temp_row:
            logger.warning(f"No temperature data for {country_code}")
            return None

        temperature = float(temp_row.temperature)
        trend = temp_row.trend

        # Get key events from last 7 days
        events = session.execute(text("""
            SELECT a.title, a.url, an.sentiment, an.action_level, an.event_type,
                   s.name as source, s.tier, a.published_at
            FROM analysis an
            JOIN articles a ON an.article_id = a.id
            JOIN sources s ON a.source_id = s.id
            WHERE s.country_code = :cc
              AND an.is_relevant = true
              AND an.sentiment IS NOT NULL
              AND a.published_at > NOW() - INTERVAL '7 days'
            ORDER BY (an.action_level * ABS(an.sentiment)) DESC
            LIMIT 10
        """), {"cc": country_code}).fetchall()

        if not events:
            logger.warning(f"No events for {country_code}")
            return None

        # Format events for prompt
        events_lines = []
        event_data = []
        for e in events:
            sent_word = {-3: "крайне негативно", -2: "негативно", -1: "скептически",
                         0: "нейтрально", 1: "позитивно", 2: "очень позитивно",
                         3: "восторженно"}.get(int(e.sentiment), "нейтрально")
            action_word = {1: "заявление", 2: "переговоры/визит", 3: "соглашение",
                           4: "санкции/ограничение", 5: "разрыв", 6: "военное действие"
                           }.get(e.action_level, "заявление")

            events_lines.append(
                f"- [{action_word}] {e.title} (тон: {sent_word}, источник: {e.source}, ссылка: {e.url})"
            )
            event_data.append({
                "title": e.title, "url": e.url, "sentiment": float(e.sentiment),
                "action_level": e.action_level, "source": e.source,
            })

        events_text = "\n".join(events_lines)

    # Call LLM
    prompt = DIGEST_PROMPT.format(
        country=country_name, temperature=f"{temperature:+.1f}",
        trend=trend, events_text=events_text,
    )

    try:
        with track_duration() as timer:
            resp = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
        data = resp.json()
        digest_text = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        track_api_call(
            service="openrouter", endpoint="/chat/completions",
            model=MODEL, script="generate_digests.py",
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            status="ok", duration_ms=timer.ms,
        )
    except Exception as e:
        logger.error(f"LLM error for {country_code}: {e}")
        track_api_call(
            service="openrouter", endpoint="/chat/completions",
            model=MODEL, script="generate_digests.py",
            status="error", error=str(e)[:500],
        )
        return None

    # Save to DB
    with get_session() as session:
        session.execute(text("""
            INSERT INTO digests (country_code, digest_text, period_start, period_end,
                               key_events, temperature_end, model_used)
            VALUES (:cc, :text, NOW() - INTERVAL '7 days', NOW(),
                    :events, :temp, :model)
        """), {
            "cc": country_code, "text": digest_text,
            "events": json.dumps(event_data, ensure_ascii=False),
            "temp": temperature, "model": MODEL,
        })
        session.commit()

    logger.info(f"{country_name}: digest generated ({len(digest_text)} chars)")
    return {"country": country_code, "digest": digest_text, "temperature": temperature}


def run_once(country: str | None = None):
    """Generate digests once for all or one country."""
    countries = [country] if country else list(COUNTRY_NAMES.keys())

    for cc in countries:
        result = generate_digest(cc)
        if result:
            logger.info(f"{COUNTRY_NAMES.get(cc, cc)} ({result['temperature']:+.1f}°): {len(result['digest'])} chars")
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", help="Generate for one country")
    parser.add_argument("--loop", action="store_true", help="Run in loop mode")
    parser.add_argument("--interval", type=int, default=86400, help="Interval between runs in seconds (default: 86400 = 24h)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [digest] %(levelname)s: %(message)s")

    if args.loop:
        logger.info(f"Starting digest loop (interval: {args.interval}s)")
        while True:
            try:
                run_once(args.country)
                logger.info(f"Digests complete. Sleeping {args.interval}s...")
            except Exception as e:
                logger.error(f"Digest generation failed: {e}")
            time.sleep(args.interval)
    else:
        run_once(args.country)


if __name__ == "__main__":
    main()
