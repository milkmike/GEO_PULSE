"""
Headline endpoint — добавить в main.py API.
Генерирует копирайтерский заголовок на основе данных.
"""

# === ДОБАВИТЬ В КОНЕЦ main.py ===

import json as _json
import hashlib
import httpx
import redis


_redis = None
def _get_redis():
    global _redis
    if _redis is None:
        import os
        _redis = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    return _redis


HEADLINE_CACHE_KEY = "geopulse:headline:v1"
HEADLINE_TTL = 3600  # 1 hour


@app.get("/api/v1/headline")
def get_headline(force: bool = False):
    """Generate editorial headline from current data."""
    # Check cache
    if not force:
        try:
            cached = _get_redis().get(HEADLINE_CACHE_KEY)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    # Gather insights
    with get_session() as session:
        # 1. Tier divergence — find max
        divergence_data = []
        for code, name in COUNTRY_NAMES.items():
            tier_rows = session.execute(
                text("""
                    SELECT COALESCE(s.tier, 'mainstream') AS tier,
                           AVG(an.sentiment) AS avg_sent,
                           COUNT(*) as cnt
                    FROM analysis an
                    JOIN articles ar ON an.article_id = ar.id
                    JOIN sources s ON ar.source_id = s.id
                    WHERE s.country_code = :cc
                      AND an.is_relevant = true
                      AND an.sentiment IS NOT NULL
                      AND ar.published_at > NOW() - INTERVAL '14 days'
                    GROUP BY s.tier
                    HAVING COUNT(*) >= 3
                """),
                {"cc": code},
            ).fetchall()

            if len(tier_rows) >= 2:
                tiers_info = []
                for r in tier_rows:
                    tier_label = {
                        "official": "официальные СМИ",
                        "mainstream": "мейнстрим",
                        "independent": "независимые",
                        "domestic_opposition": "оппозиция",
                        "analytics": "аналитика",
                        "western_proxy": "западные СМИ",
                    }.get(r.tier, r.tier)
                    tiers_info.append({
                        "tier": r.tier,
                        "label": tier_label,
                        "sentiment": round(float(r.avg_sent), 2),
                        "count": r.cnt,
                    })

                sents = [t["sentiment"] for t in tiers_info]
                div = round(max(sents) - min(sents), 2)
                most_pos = max(tiers_info, key=lambda x: x["sentiment"])
                most_neg = min(tiers_info, key=lambda x: x["sentiment"])

                divergence_data.append({
                    "code": code,
                    "name": name,
                    "divergence": div,
                    "most_positive": most_pos,
                    "most_negative": most_neg,
                    "tiers": tiers_info,
                })

        divergence_data.sort(key=lambda x: -x["divergence"])

        # 2. Latest critical alerts
        alert_rows = session.execute(
            text("""
                SELECT country_code, severity, description, data, created_at
                FROM alerts
                WHERE created_at > NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC
                LIMIT 5
            """),
        ).fetchall()

        alerts = [
            {
                "country": COUNTRY_NAMES.get(r.country_code, r.country_code),
                "code": r.country_code,
                "severity": r.severity,
                "description": r.description,
                "z_score": r.data.get("z_score") if r.data else None,
                "temperature": r.data.get("temperature") if r.data else None,
            }
            for r in alert_rows
        ]

        # 3. Temperature extremes
        temp_rows = session.execute(
            text("""
                SELECT DISTINCT ON (country_code) country_code, temperature, trend
                FROM temperature
                ORDER BY country_code, time DESC
            """),
        ).fetchall()

        temps = {
            r.country_code: {"temp": float(r.temperature) if r.temperature else 0, "trend": r.trend}
            for r in temp_rows
        }

        hottest = max(temps.items(), key=lambda x: x[1]["temp"])
        coldest = min(temps.items(), key=lambda x: x[1]["temp"])

    # Build context for LLM
    context = f"""Данные GeoPulse — аналитика медийной температуры стран СНГ по отношению к России.
Шкала: −50° (полное сотрудничество) до +50° (конфликт).

РАСХОЖДЕНИЕ НАРРАТИВОВ (разница между тирами источников за 14 дней):
"""
    for d in divergence_data[:5]:
        context += f"- {d['name']}: расхождение {d['divergence']}. "
        context += f"{d['most_positive']['label']} = {d['most_positive']['sentiment']:+.2f}, "
        context += f"{d['most_negative']['label']} = {d['most_negative']['sentiment']:+.2f}\n"

    if alerts:
        context += "\nАНОМАЛИИ (последние 24 часа):\n"
        for a in alerts[:3]:
            context += f"- {a['country']}: {a['severity']}, z-score={a['z_score']}, температура={a['temperature']}°\n"

    context += f"\nЭКСТРЕМУМЫ ТЕМПЕРАТУРЫ:\n"
    context += f"- Самый горячий: {COUNTRY_NAMES.get(hottest[0], hottest[0])} = {hottest[1]['temp']:+.1f}°, тренд {hottest[1]['trend']}\n"
    context += f"- Самый холодный: {COUNTRY_NAMES.get(coldest[0], coldest[0])} = {coldest[1]['temp']:+.1f}°, тренд {coldest[1]['trend']}\n"

    # Call LLM
    import os
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # Fallback: template-based
        top = divergence_data[0] if divergence_data else None
        if top:
            result = {
                "headline": f"{top['name']}: {top['most_positive']['label']} и {top['most_negative']['label']} видят разные страны",
                "subline": f"Расхождение нарративов {top['divergence']} — максимальное в СНГ. {top['most_positive']['label']} ({top['most_positive']['sentiment']:+.2f}) vs {top['most_negative']['label']} ({top['most_negative']['sentiment']:+.2f})",
                "country_code": top["code"],
                "type": "divergence",
                "generated": False,
            }
        else:
            result = {"headline": None, "subline": None}
        return result

    prompt = f"""{context}

Ты — редактор-копирайтер аналитического издания. Напиши ONE заголовок и ONE подзаголовок для hero-блока дашборда.

СТИЛЬ:
- Заголовок: 5-10 слов, дерзкий, журналистский, как The Economist или Bloomberg. Без кавычек. Без эмодзи. Без цифр.
- Подзаголовок: 1-2 предложения, можно цифры, раскрывает суть. Конкретика. Не общие слова.
- Тон: умный, ироничный, острый. Не сухой, не академичный.
- Выбери САМЫЙ интересный инсайт из данных. Это может быть:
  а) максимальное расхождение нарративов
  б) аномальное движение температуры
  в) интересная комбинация факторов
- Пиши по-русски.

Ответь ТОЛЬКО в формате JSON:
{{"headline": "...", "subline": "...", "country_code": "XX", "type": "divergence|anomaly|temperature"}}"""

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "anthropic/claude-sonnet-4",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0.8,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        # Parse JSON from response
        # Handle potential markdown code blocks
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        result = _json.loads(content.strip())
        result["generated"] = True
    except Exception as e:
        logger.warning(f"Headline LLM failed: {e}")
        # Fallback
        top = divergence_data[0] if divergence_data else None
        if top:
            result = {
                "headline": f"{top['name']}: {top['most_positive']['label']} и {top['most_negative']['label']} видят разные страны",
                "subline": f"Расхождение нарративов {top['divergence']} — максимальное в СНГ",
                "country_code": top["code"],
                "type": "divergence",
                "generated": False,
            }
        else:
            result = {"headline": None, "subline": None, "generated": False}

    # Cache
    try:
        _get_redis().setex(HEADLINE_CACHE_KEY, HEADLINE_TTL, _json.dumps(result, ensure_ascii=False))
    except Exception:
        pass

    return result
