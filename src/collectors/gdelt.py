"""GDELT DOC 2.0 collector — how each country's media covers Russia.

For every country in the registry we query GDELT for articles published by
that country's outlets (sourcecountry:FIPS) that mention Russia, and store
daily aggregates: coverage volume, share of national news flow, average tone,
plus a sample of top headlines. Free API, no key required.
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

# Articles mentioning Russia (GDELT indexes translated coverage, so English
# keywords match articles in all 65 monitored languages)
RUSSIA_QUERY = '(russia OR russian OR putin OR kremlin OR moscow)'

# GDELT politeness: the DOC API rate-limits per IP aggressively (429/503 on
# bursts). ~5s sustained pacing keeps a 99-country pass (~300 requests)
# within tolerance; tune via env if your IP is luckier/unluckier.
REQUEST_PAUSE_SEC = float(os.environ.get("GDELT_PAUSE_SEC", "5.0"))
MAX_RETRIES = 3  # per request, on 429/503 with growing backoff
TIMEOUT_SEC = 30.0

USER_AGENT = "GeoPulse/2.0 (research project; Russia-relations monitoring)"


def _gdelt_get(params: dict) -> dict | None:
    """Single GDELT DOC API call with 429/503 backoff. Returns JSON or None."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.get(
                GDELT_DOC_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT_SEC,
            )
            if resp.status_code in (429, 503):
                # honour Retry-After when present; otherwise 30s, 90s, 270s
                try:
                    retry_after = int(resp.headers.get("retry-after") or 0)
                except ValueError:
                    retry_after = 0
                wait = max(retry_after, 30 * (3 ** attempt))
                logger.warning(
                    f"GDELT {resp.status_code}, backing off {wait}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            # GDELT returns plain-text error messages with HTTP 200 on bad queries
            text = resp.text.strip()
            if not text.startswith("{"):
                logger.warning(f"GDELT non-JSON response: {text[:120]}")
                return None
            return resp.json()
        except httpx.HTTPError as e:
            logger.warning(f"GDELT request failed: {e}")
            return None
        except ValueError as e:
            logger.warning(f"GDELT JSON parse failed: {e}")
            return None
    logger.warning("GDELT rate limit persisted after retries, skipping request")
    return None


def _timeline_points(data: dict | None) -> dict[str, dict]:
    """Flatten a GDELT timeline response into {YYYY-MM-DD: {value, norm}}."""
    points: dict[str, dict] = {}
    if not data:
        return points
    for series in data.get("timeline", []):
        for item in series.get("data", []):
            raw_date = item.get("date", "")
            if len(raw_date) < 8:
                continue
            day = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            entry = points.setdefault(day, {})
            entry["value"] = item.get("value")
            if "norm" in item:
                entry["norm"] = item.get("norm")
    return points


def fetch_country_timelines(fips: str, days: int = 7) -> dict[str, dict]:
    """Fetch volume + tone timelines for one country's coverage of Russia.

    Returns {day: {volume, volume_share, tone_avg}}.
    """
    # GDELT DOC API has no AND operator — terms are ANDed implicitly
    query = f"sourcecountry:{fips} {RUSSIA_QUERY}"
    timespan = f"{max(1, days)}d"

    vol = _gdelt_get({
        "query": query, "mode": "timelinevolraw",
        "timespan": timespan, "format": "json",
    })
    time.sleep(REQUEST_PAUSE_SEC)
    tone = _gdelt_get({
        "query": query, "mode": "timelinetone",
        "timespan": timespan, "format": "json",
    })
    time.sleep(REQUEST_PAUSE_SEC)

    vol_points = _timeline_points(vol)
    tone_points = _timeline_points(tone)

    result: dict[str, dict] = {}
    for day in set(vol_points) | set(tone_points):
        v = vol_points.get(day, {})
        volume = v.get("value")
        norm = v.get("norm")
        share = None
        if volume is not None and norm:
            share = round(volume / norm, 6)
        result[day] = {
            "volume": volume,
            "volume_share": share,
            "tone_avg": tone_points.get(day, {}).get("value"),
        }
    return result


def fetch_top_articles(fips: str, max_records: int = 12, timespan: str = "2d") -> list[dict]:
    """Top recent articles from this country's media about Russia."""
    query = f"sourcecountry:{fips} {RUSSIA_QUERY}"
    data = _gdelt_get({
        "query": query, "mode": "artlist", "maxrecords": max_records,
        "timespan": timespan, "sort": "hybridrel", "format": "json",
    })
    time.sleep(REQUEST_PAUSE_SEC)
    if not data:
        return []
    articles = []
    for art in data.get("articles", [])[:max_records]:
        articles.append({
            "title": (art.get("title") or "")[:300],
            "url": art.get("url", ""),
            "domain": art.get("domain", ""),
            "language": art.get("language", ""),
            "seendate": art.get("seendate", ""),
        })
    return articles


def collect_country(country: dict, days: int = 7, with_samples: bool = True) -> list[dict]:
    """Collect daily GDELT rows for one country. Returns rows for upsert."""
    fips = country["fips"]
    code = country["code"]

    timelines = fetch_country_timelines(fips, days=days)
    if not timelines:
        logger.info(f"[{code}] GDELT: no timeline data")
        return []

    samples = fetch_top_articles(fips) if with_samples else []
    today = datetime.now(timezone.utc).date().isoformat()

    rows = []
    for day, vals in sorted(timelines.items()):
        if vals.get("volume") is None and vals.get("tone_avg") is None:
            continue
        rows.append({
            "day": day,
            "country_code": code,
            "volume": vals.get("volume"),
            "volume_share": vals.get("volume_share"),
            "tone_avg": vals.get("tone_avg"),
            # samples only attached to the most recent day to keep rows light
            "article_samples": samples if day == today and samples else None,
        })
    return rows
