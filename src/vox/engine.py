"""
VOX POPULI — Vox Temperature Engine

Рассчитывает "народную температуру" по стране на основе
проанализированных комментариев.

Формула похожа на медийную температуру, но:
- Без тирового взвешивания (все комменты равны)
- С фильтрацией ботов (bot_score > 0.5 отсекаются)
- Экспоненциальное затухание τ=7 дней (комменты живут меньше статей)
- Считает elite_gap = media_temp - vox_temp
"""

import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("vox-engine")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
TAU_DAYS = float(os.environ.get("VOX_TAU_DAYS", "7"))
BOT_THRESHOLD = float(os.environ.get("VOX_BOT_THRESHOLD", "0.5"))
CALC_INTERVAL = int(os.environ.get("VOX_CALC_INTERVAL", "3600"))  # 1 час

COUNTRY_CODES = ["KZ", "AM", "UZ", "KG", "TJ", "TM", "AZ", "GE", "MD", "BY"]


def get_db():
    return psycopg2.connect(DATABASE_URL)


def get_media_temperature(conn, country_code: str) -> float | None:
    """Получить текущую медийную температуру страны."""
    cur = conn.cursor()
    cur.execute("""
        SELECT temperature FROM temperature
        WHERE country_code = %s
        ORDER BY time DESC
        LIMIT 1
    """, (country_code,))
    row = cur.fetchone()
    return float(row[0]) if row else None


def calculate_vox_temperature(conn, country_code: str) -> dict | None:
    """
    Рассчитать народную температуру для страны.

    Используем комментарии за последние 14 дней с экспоненциальным
    затуханием (τ=7 дней). Отсекаем вероятных ботов.
    """
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=14)

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT c.id, c.published_at, c.author_hash,
               ca.sentiment, ca.stance_russia, ca.emotion,
               ca.bot_score
        FROM comments c
        JOIN comment_analysis ca ON ca.comment_id = c.id
        WHERE c.country_code = %s
          AND c.published_at >= %s
          AND ca.bot_score < %s
        ORDER BY c.published_at DESC
    """, (country_code, window_start, BOT_THRESHOLD))

    rows = cur.fetchall()
    if not rows:
        return None

    # Подсчёт с экспоненциальным затуханием
    weighted_sum = 0.0
    weight_total = 0.0
    authors = set()
    emotions: dict[str, int] = {}
    pro_count = 0
    anti_count = 0
    neutral_count = 0

    for r in rows:
        dt = (now - r["published_at"]).total_seconds() / 86400  # дни
        weight = math.exp(-dt / TAU_DAYS)

        sentiment = float(r["sentiment"])
        weighted_sum += sentiment * weight
        weight_total += weight

        authors.add(r["author_hash"])

        emotion = r["emotion"] or "neutral"
        emotions[emotion] = emotions.get(emotion, 0) + 1

        stance = r["stance_russia"]
        if stance == "pro":
            pro_count += 1
        elif stance == "anti":
            anti_count += 1
        else:
            neutral_count += 1

    if weight_total == 0:
        return None

    # Температура: нормализуем как в медийном движке
    # sentiment -3..+3, масштабируем к -60..+60
    raw_temp = (weighted_sum / weight_total) * 20  # × 20 чтобы -3→-60, +3→+60
    temperature = max(-100, min(100, raw_temp))

    total = len(rows)
    dominant_emotion = max(emotions, key=emotions.get) if emotions else "neutral"

    # Считаем bot_ratio (включая отсечённых)
    cur.execute("""
        SELECT COUNT(*) as total,
               COUNT(*) FILTER (WHERE ca.bot_score >= %s) as bots
        FROM comments c
        JOIN comment_analysis ca ON ca.comment_id = c.id
        WHERE c.country_code = %s AND c.published_at >= %s
    """, (BOT_THRESHOLD, country_code, window_start))
    bot_row = cur.fetchone()
    bot_ratio = (bot_row["bots"] / bot_row["total"]) if bot_row and bot_row["total"] > 0 else 0

    # Elite gap
    media_temp = get_media_temperature(conn, country_code)
    elite_gap = (media_temp - temperature) if media_temp is not None else None

    return {
        "temperature": round(temperature, 2),
        "comment_count": total,
        "unique_authors": len(authors),
        "bot_ratio": round(bot_ratio, 2),
        "clean_temperature": round(temperature, 2),  # уже без ботов
        "elite_gap": round(elite_gap, 2) if elite_gap is not None else None,
        "media_temperature": round(media_temp, 2) if media_temp is not None else None,
        "dominant_emotion": dominant_emotion,
        "pro_ratio": round(pro_count / total, 2) if total > 0 else 0,
        "anti_ratio": round(anti_count / total, 2) if total > 0 else 0,
    }


def save_vox_temperature(conn, country_code: str, data: dict):
    """Сохранить народную температуру."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO vox_temperature
            (time, country_code, temperature, comment_count, unique_authors,
             bot_ratio, clean_temperature, elite_gap, media_temperature,
             dominant_emotion, pro_ratio, anti_ratio)
        VALUES
            (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        country_code,
        data["temperature"],
        data["comment_count"],
        data["unique_authors"],
        data["bot_ratio"],
        data["clean_temperature"],
        data["elite_gap"],
        data["media_temperature"],
        data["dominant_emotion"],
        data["pro_ratio"],
        data["anti_ratio"],
    ))
    conn.commit()


def calculate_all():
    """Рассчитать народную температуру для всех стран."""
    conn = get_db()
    log.info("═══ VOX POPULI Temperature calculation ═══")

    for code in COUNTRY_CODES:
        data = calculate_vox_temperature(conn, code)
        if data:
            save_vox_temperature(conn, code, data)
            elite_gap_str = f"gap={data['elite_gap']:+.1f}" if data["elite_gap"] is not None else "gap=N/A"
            log.info(
                f"  {code}: {data['temperature']:+.1f}° "
                f"({data['comment_count']} comments, {data['unique_authors']} authors, "
                f"bots={data['bot_ratio']:.0%}, {elite_gap_str})"
            )
        else:
            log.info(f"  {code}: no data")

    conn.close()
    log.info("═══ Calculation complete ═══")


def main():
    while True:
        try:
            calculate_all()
        except Exception as e:
            log.error(f"Calculation failed: {e}")

        log.info(f"Sleeping {CALC_INTERVAL}s...")
        time.sleep(CALC_INTERVAL)


if __name__ == "__main__":
    main()
