"""
VOX POPULI — Comment Analyzer

Анализирует комментарии через LLM:
- Тональность (sentiment -3..+3)
- Эмоция (anger/fear/joy/sadness/disgust/surprise/neutral)
- Позиция к России (pro/neutral/anti)
- Темы
- Вероятность бота

Использует лёгкую модель (Haiku/Gemini Flash) — комменты короткие,
нужна скорость и низкая стоимость.
"""

import json
import logging
import os
import time

import psycopg2
import psycopg2.extras
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("vox-analyzer")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("VOX_MODEL", "anthropic/claude-haiku:beta")
BATCH_SIZE = int(os.environ.get("VOX_BATCH_SIZE", "20"))
ANALYZE_INTERVAL = int(os.environ.get("VOX_ANALYZE_INTERVAL", "120"))  # 2 мин

SYSTEM_PROMPT = """Ты анализатор комментариев к новостным статьям о постсоветском пространстве и отношениях с Россией.

Для каждого комментария определи:
1. sentiment: число от -3 (крайне негативно к России) до +3 (крайне позитивно к России). 0 = нейтрально.
2. emotion: одно из: anger, fear, joy, sadness, disgust, surprise, neutral
3. stance_russia: "pro" (про-российский), "neutral", "anti" (анти-российский)
4. topics: массив из 1-3 ключевых тем (короткие фразы на русском)
5. bot_score: вероятность что это бот/спам от 0.0 до 1.0. Признаки ботов: копипаст, слишком формально, ссылки на каналы, промо-текст.
6. language: код языка (ru, en, kk, uz, ky, hy, ka, ro, tg, tk, az)

Отвечай ТОЛЬКО валидным JSON массивом. Каждый элемент соответствует комментарию по порядку."""

USER_PROMPT_TEMPLATE = """Проанализируй {n} комментариев. Страна: {country}.

{comments}

Верни JSON массив из {n} объектов:
[{{"sentiment": 0.5, "emotion": "neutral", "stance_russia": "neutral", "topics": ["тема"], "bot_score": 0.1, "language": "ru"}}, ...]"""


def get_db():
    return psycopg2.connect(DATABASE_URL)


def get_pending_comments(conn, limit: int = 20) -> list[dict]:
    """Получить комментарии без анализа."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.id, c.text, c.country_code, c.platform, c.author_hash
        FROM comments c
        LEFT JOIN comment_analysis ca ON ca.comment_id = c.id
        WHERE ca.comment_id IS NULL
        ORDER BY c.published_at DESC
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def call_llm(comments: list[dict], country: str) -> list[dict] | None:
    """Вызвать LLM для анализа батча комментариев."""
    formatted = ""
    for i, c in enumerate(comments, 1):
        text = c["text"][:500]  # обрезаем длинные комменты
        formatted += f"[{i}] {text}\n\n"

    user_msg = USER_PROMPT_TEMPLATE.format(
        n=len(comments),
        country=country,
        comments=formatted
    )

    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg}
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]

            # Парсим JSON из ответа
            # Иногда LLM оборачивает в ```json ... ```
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1]
                if content.endswith("```"):
                    content = content[:-3]

            results = json.loads(content)
            if isinstance(results, list) and len(results) == len(comments):
                return results
            else:
                log.warning(f"LLM returned {len(results)} results for {len(comments)} comments")
                return results[:len(comments)] if isinstance(results, list) else None
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        return None


def save_analyses(conn, comment_ids: list[int], analyses: list[dict]):
    """Сохранить результаты анализа."""
    cur = conn.cursor()
    saved = 0

    for cid, analysis in zip(comment_ids, analyses):
        try:
            topics = analysis.get("topics", [])
            if isinstance(topics, list):
                topics_arr = topics
            else:
                topics_arr = [str(topics)]

            cur.execute("""
                INSERT INTO comment_analysis
                    (comment_id, sentiment, emotion, stance_russia, topics, bot_score, model_used)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (comment_id) DO NOTHING
            """, (
                cid,
                float(analysis.get("sentiment", 0)),
                analysis.get("emotion", "neutral"),
                analysis.get("stance_russia", "neutral"),
                topics_arr,
                float(analysis.get("bot_score", 0)),
                MODEL,
            ))

            # Обновляем язык комментария если определён
            lang = analysis.get("language")
            if lang:
                cur.execute(
                    "UPDATE comments SET language = %s WHERE id = %s AND language IS NULL",
                    (lang, cid)
                )

            saved += 1
        except Exception as e:
            log.warning(f"Failed to save analysis for comment {cid}: {e}")
            conn.rollback()
            continue

    conn.commit()
    return saved


def analyze_batch():
    """Анализировать один батч комментариев."""
    conn = get_db()
    comments = get_pending_comments(conn, BATCH_SIZE)

    if not comments:
        conn.close()
        return 0

    # Группируем по стране для контекста
    by_country: dict[str, list[dict]] = {}
    for c in comments:
        by_country.setdefault(c["country_code"], []).append(c)

    total_saved = 0
    for country, batch in by_country.items():
        log.info(f"Analyzing {len(batch)} comments for {country}...")
        results = call_llm(batch, country)
        if results:
            ids = [c["id"] for c in batch]
            saved = save_analyses(conn, ids, results)
            total_saved += saved
            log.info(f"  → {saved} analyses saved")
        else:
            log.warning(f"  → LLM returned no results for {country}")

        time.sleep(1)  # rate limiting

    conn.close()
    return total_saved


def main():
    log.info("═══ VOX POPULI Analyzer started ═══")
    while True:
        try:
            n = analyze_batch()
            if n > 0:
                log.info(f"Batch complete: {n} comments analyzed")
                # Если есть ещё — сразу следующий батч
                continue
        except Exception as e:
            log.error(f"Analysis cycle failed: {e}")

        time.sleep(ANALYZE_INTERVAL)


if __name__ == "__main__":
    main()
