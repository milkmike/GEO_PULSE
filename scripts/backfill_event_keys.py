#!/usr/bin/env python3
"""Backfill event_key for existing analyzed articles using LLM."""

import os
import json
import time
import psycopg2
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PARAMS = {
    "host": "db", "port": 5432,
    "dbname": "cis_thermometer", "user": "thermo",
    "password": "thermo",
}

API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "anthropic/claude-sonnet-4"
BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

BATCH_PROMPT = """Для каждой статьи определи event_key — краткий идентификатор СОБЫТИЯ (3-5 слов, lowercase, без дат).
Все статьи об ОДНОМ событии должны получить ОДИНАКОВЫЙ event_key.

Примеры:
- "Комику Сабурову запретили въезд в Россию на 50 лет" → "запрет въезда сабурову в россию"
- "Нурлан Сабуров прокомментировал запрет на въезд" → "запрет въезда сабурову в россию"
- "Армения вышла из ОДКБ" → "выход армении из одкб"
- "Байконур готовится к запуску" → "запуск с байконура"

Статьи:
{articles}

Ответь ТОЛЬКО JSON массивом: [{{"id": N, "event_key": "..."}}]"""


def get_articles():
    """Get relevant articles without event_key."""
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("""
        SELECT a.id, ar.title, s.country_code
        FROM analysis a
        JOIN articles ar ON a.article_id = ar.id
        JOIN sources s ON ar.source_id = s.id
        WHERE a.is_relevant = true
          AND a.sentiment IS NOT NULL
          AND (a.event_key IS NULL OR a.event_key = '')
          AND ar.published_at > NOW() - INTERVAL '30 days'
        ORDER BY ar.published_at DESC
        LIMIT 500
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def process_batch(batch):
    """Send batch of articles to LLM for event_key extraction."""
    articles_text = "\n".join(f"ID {a[0]}: [{a[2]}] {a[1]}" for a in batch)
    
    try:
        resp = requests.post(BASE_URL, headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": BATCH_PROMPT.format(articles=articles_text)}],
            "temperature": 0.1,
            "max_tokens": 2000,
        }, timeout=60)
        
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        
        # Extract JSON from response
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        
        results = json.loads(content)
        return results
    except Exception as e:
        print(f"  Error: {e}")
        return []


def save_event_keys(results):
    """Save event_keys to DB."""
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    updated = 0
    for r in results:
        ek = r.get("event_key", "").strip().lower()
        aid = r.get("id")
        if ek and aid and len(ek) > 3:
            cur.execute("UPDATE analysis SET event_key = %s WHERE id = %s", (ek, aid))
            updated += 1
    conn.commit()
    cur.close()
    conn.close()
    return updated


def main():
    if not API_KEY:
        print("ERROR: OPENROUTER_API_KEY not set")
        return
    
    articles = get_articles()
    print(f"Found {len(articles)} articles without event_key")
    
    if not articles:
        return
    
    # Process in batches of 30 (fits in context easily)
    batch_size = 30
    total_updated = 0
    
    for i in range(0, len(articles), batch_size):
        batch = articles[i:i+batch_size]
        print(f"\nBatch {i//batch_size + 1}/{(len(articles)-1)//batch_size + 1} ({len(batch)} articles)...")
        
        results = process_batch(batch)
        if results:
            updated = save_event_keys(results)
            total_updated += updated
            print(f"  Updated {updated} event_keys")
        
        time.sleep(1)
    
    print(f"\n{'='*50}")
    print(f"Total updated: {total_updated}")
    
    # Show top clusters
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    cur.execute("""
        SELECT event_key, count(*) as cnt 
        FROM analysis 
        WHERE event_key IS NOT NULL AND LENGTH(event_key) > 3
        GROUP BY event_key 
        HAVING count(*) >= 2
        ORDER BY cnt DESC 
        LIMIT 15
    """)
    print("\nTop event clusters:")
    for r in cur.fetchall():
        print(f"  {r[1]:3d} articles | {r[0]}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
