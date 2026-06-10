"""Russia Relations Index (RRI v1) — world-wide index of relations with Russia.

Methodology (inspired by worldmonitor's CII v8 two-layer design):

    score = w_structural × structural + w_media × media + boost
    clamped to [-100, +100], then floors/caps from hard facts are enforced.

Layers:
  structural — slow-moving baseline: bloc memberships (CSTO/EAEU/NATO/EU/...),
               sanctions participation, UN voting alignment (where loaded),
               curated adjustments from the registry.
  media      — fast layer: tier-1 countries use the LLM media temperature,
               tier-2 countries use GDELT tone of their Russia coverage.
  boost      — recent high-action events (AL≥5) push the index for 14 days.

Weights differ by tier because tier-1 media signal (own-source LLM pipeline)
is far higher fidelity than GDELT tone:
  tier 1: 0.45 structural / 0.55 media
  tier 2: 0.60 structural / 0.40 media

Levels: ally ≥ +45 · partner +15..45 · neutral −15..+15 ·
        cooling −40..−15 · tension −70..−40 · hostile < −70
"""
import json
import logging
import math
import statistics
from datetime import datetime, timezone

from sqlalchemy import text

from src.countries import COUNTRIES
from src.db import get_session

logger = logging.getLogger(__name__)

INDEX_VERSION = "v1"

MEMBERSHIP_WEIGHTS = {
    "union_state": 20,
    "csto": 25,
    "csto_suspended": 5,
    "eaeu": 15,
    "cis": 10,
    "sco": 5,
    "brics": 10,
    "nato": -25,
    "eu": -15,
    "g7": -5,
}

UNFRIENDLY_PENALTY = -10      # listed in RF "unfriendly states" decree
SANCTIONS_PENALTY = -20       # imposed sanctions on Russia
WAR_PENALTY = -60             # active armed conflict

# UN voting alignment contribution (when un_votes data is loaded): -15..+15
UN_VOTES_SPAN = 15

TIER1_WEIGHTS = (0.45, 0.55)  # (structural, media)
TIER2_WEIGHTS = (0.60, 0.40)

# GDELT tone (~ -10..+10 in practice) → media scale -100..+100
GDELT_TONE_SCALE = 12.0

# Recent AL≥5 events shift the index (max ±15)
BOOST_PER_EVENT = 5.0
BOOST_MAX = 15.0
BOOST_WINDOW_DAYS = 14

LEVELS = [
    (45, "ally"),
    (15, "partner"),
    (-15, "neutral"),
    (-40, "cooling"),
    (-70, "tension"),
    (-101, "hostile"),
]


def level_for(score: float) -> str:
    for threshold, name in LEVELS:
        if score >= threshold:
            return name
    return "hostile"


def _clamp(v: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def structural_baseline(country: dict, session) -> tuple[float, dict]:
    """Slow layer: memberships, sanctions/unfriendly status, UN votes, curated adj."""
    detail = {}
    score = 0.0

    for m in country["memberships"]:
        w = MEMBERSHIP_WEIGHTS.get(m, 0)
        score += w
        detail[m] = w

    if country["unfriendly"]:
        score += UNFRIENDLY_PENALTY
        detail["unfriendly"] = UNFRIENDLY_PENALTY
    if country["sanctions_on_russia"]:
        score += SANCTIONS_PENALTY
        detail["sanctions"] = SANCTIONS_PENALTY
    if country["war_with_russia"]:
        score += WAR_PENALTY
        detail["war"] = WAR_PENALTY

    if country["baseline_adj"]:
        score += country["baseline_adj"]
        detail["curated_adj"] = country["baseline_adj"]

    # UN voting alignment refinement (graceful if table absent/empty);
    # savepoint keeps the outer transaction usable when the table is missing
    try:
        with session.begin_nested():
            row = session.execute(
                text("""
                    SELECT AVG(agreement_pct) AS pct FROM un_votes
                    WHERE country_code = :cc
                      AND year >= EXTRACT(YEAR FROM NOW()) - 3
                """),
                {"cc": country["code"]},
            ).fetchone()
        if row and row.pct is not None:
            # 50% agreement = neutral; 100% = +span, 0% = -span
            un_component = round((float(row.pct) - 50.0) / 50.0 * UN_VOTES_SPAN, 1)
            score += un_component
            detail["un_votes"] = un_component
    except Exception:
        pass  # table may not exist yet

    return _clamp(score), detail


def media_component(country: dict, session) -> tuple[float | None, dict]:
    """Fast layer: LLM temperature (tier 1) or GDELT tone (tier 2).

    Tier-1 countries fall back to GDELT if the temperature pipeline has no
    fresh reading; tier-2 countries with own sources get temperature too.
    """
    detail = {}
    code = country["code"]

    # Own-media LLM temperature (preferred when fresh: < 48h old)
    temp_row = session.execute(
        text("""
            SELECT temperature, article_count, time FROM temperature
            WHERE country_code = :cc ORDER BY time DESC LIMIT 1
        """),
        {"cc": code},
    ).fetchone()
    if temp_row and temp_row.temperature is not None:
        age_h = (datetime.now(timezone.utc) - temp_row.time).total_seconds() / 3600
        if age_h <= 48:
            detail["source"] = "temperature"
            detail["article_count"] = temp_row.article_count
            return float(temp_row.temperature), detail

    # GDELT tone, 7-day weighted average (recent days matter more)
    rows = session.execute(
        text("""
            SELECT day, tone_avg, volume FROM gdelt_daily
            WHERE country_code = :cc AND tone_avg IS NOT NULL
              AND day > CURRENT_DATE - 8
            ORDER BY day DESC
        """),
        {"cc": code},
    ).fetchall()
    if rows:
        num, den = 0.0, 0.0
        for i, r in enumerate(rows):
            w = 0.8 ** i
            num += float(r.tone_avg) * w
            den += w
        tone = num / den
        detail["source"] = "gdelt"
        detail["gdelt_tone_7d"] = round(tone, 2)
        detail["gdelt_volume_latest"] = float(rows[0].volume or 0)
        return _clamp(tone * GDELT_TONE_SCALE), detail

    return None, {"source": "none"}


def event_boost(code: str, session) -> tuple[float, dict]:
    """Recent high-action events (AL≥5) from the deep pipeline shift the index."""
    try:
        with session.begin_nested():
            rows = session.execute(
                text("""
                    SELECT a.sentiment, a.action_level, COUNT(*) AS n
                    FROM analysis a
                    JOIN articles ar ON a.article_id = ar.id
                    JOIN sources s ON ar.source_id = s.id
                    WHERE s.country_code = :cc
                      AND a.is_relevant = TRUE
                      AND a.action_level >= 5
                      AND ar.published_at > NOW() - make_interval(days => :days)
                    GROUP BY a.sentiment, a.action_level
                """),
                {"cc": code, "days": BOOST_WINDOW_DAYS},
            ).fetchall()
    except Exception:
        return 0.0, {}

    boost = 0.0
    events = 0
    for r in rows:
        if r.sentiment is None:
            continue
        direction = 1 if float(r.sentiment) > 0 else (-1 if float(r.sentiment) < 0 else 0)
        boost += direction * BOOST_PER_EVENT * min(int(r.n), 2)
        events += int(r.n)

    boost = _clamp(boost, -BOOST_MAX, BOOST_MAX)
    return boost, ({"al5_events_14d": events} if events else {})


def apply_floors_caps(score: float, country: dict) -> tuple[float, str | None]:
    """Hard facts bound the index regardless of media tone (CII-style floors)."""
    rule = None
    if country["war_with_russia"]:
        if score > -75:
            score, rule = -75.0, "war_cap"
    elif country["sanctions_on_russia"] and country["unfriendly"]:
        if score > -5:
            score, rule = -5.0, "sanctions_cap"
    if "union_state" in country["memberships"] and score < 25:
        score, rule = 25.0, "union_state_floor"
    elif "csto" in country["memberships"] and score < -25:
        score, rule = -25.0, "csto_floor"
    return score, rule


def calculate_ru_index(code: str) -> dict | None:
    """Compute the current RRI snapshot for one country."""
    country = COUNTRIES.get(code)
    if not country:
        return None

    now = datetime.now(timezone.utc)
    with get_session() as session:
        structural, s_detail = structural_baseline(country, session)
        media, m_detail = media_component(country, session)
        boost, b_detail = event_boost(code, session)

        w_s, w_m = TIER1_WEIGHTS if country["tier"] == 1 else TIER2_WEIGHTS
        if media is None:
            # No media signal at all — index rests on the structural layer
            combined = structural
            w_s, w_m = 1.0, 0.0
        else:
            combined = w_s * structural + w_m * media + boost

        combined = _clamp(combined)
        combined, bound_rule = apply_floors_caps(combined, country)
        score = round(combined, 2)

        # Deltas from history
        delta_24h = delta_7d = None
        hist = session.execute(
            text("""
                SELECT time, score FROM ru_index
                WHERE country_code = :cc AND time > NOW() - INTERVAL '8 days'
                ORDER BY time DESC
            """),
            {"cc": code},
        ).fetchall()
        for target_hours, attr in ((24, "delta_24h"), (168, "delta_7d")):
            best = None
            for h in hist:
                age_h = (now - h.time).total_seconds() / 3600
                if age_h >= target_hours * 0.75:
                    best = float(h.score)
                    break
            if best is not None:
                if attr == "delta_24h":
                    delta_24h = round(score - best, 2)
                else:
                    delta_7d = round(score - best, 2)

        details = {
            "structural": s_detail,
            "media": m_detail,
            "boost": b_detail,
            "weights": {"structural": w_s, "media": w_m},
        }
        if bound_rule:
            details["bound_rule"] = bound_rule

        return {
            "time": now,
            "country_code": code,
            "score": score,
            "structural": round(structural, 2),
            "media": round(media, 2) if media is not None else None,
            "boost": round(boost, 2),
            "level": level_for(score),
            "delta_24h": delta_24h,
            "delta_7d": delta_7d,
            "article_count": m_detail.get("article_count"),
            "gdelt_volume": m_detail.get("gdelt_volume_latest"),
            "gdelt_tone": m_detail.get("gdelt_tone_7d"),
            "version": INDEX_VERSION,
            "details": details,
        }


def save_ru_index(data: dict):
    with get_session() as session:
        session.execute(
            text("""
                INSERT INTO ru_index (time, country_code, score, structural, media,
                                      boost, level, delta_24h, delta_7d, article_count,
                                      gdelt_volume, gdelt_tone, version, details)
                VALUES (:time, :country_code, :score, :structural, :media,
                        :boost, :level, :delta_24h, :delta_7d, :article_count,
                        :gdelt_volume, :gdelt_tone, :version, CAST(:details AS jsonb))
                ON CONFLICT (time, country_code) DO NOTHING
            """),
            {**data, "details": json.dumps(data["details"], ensure_ascii=False)},
        )


def warm_cache():
    """Server-authoritative precompute: hot /api/v2/countries payload in Redis."""
    try:
        from src.queue import get_redis
    except ImportError:
        return

    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT DISTINCT ON (r.country_code)
                       r.country_code, r.score, r.structural, r.media, r.level,
                       r.delta_24h, r.delta_7d, r.article_count,
                       r.gdelt_volume, r.gdelt_tone, r.time
                FROM ru_index r
                ORDER BY r.country_code, r.time DESC
            """)
        ).fetchall()

    payload = []
    for r in rows:
        c = COUNTRIES.get(r.country_code)
        if not c:
            continue
        payload.append({
            "code": r.country_code,
            "name": c["name_ru"],
            "name_en": c["name_en"],
            "iso3": c["iso3"],
            "flag": c["flag"],
            "region": c["region"],
            "tier": c["tier"],
            "score": float(r.score),
            "structural": float(r.structural) if r.structural is not None else None,
            "media": float(r.media) if r.media is not None else None,
            "level": r.level,
            "delta_24h": float(r.delta_24h) if r.delta_24h is not None else None,
            "delta_7d": float(r.delta_7d) if r.delta_7d is not None else None,
            "article_count": r.article_count,
            "gdelt_volume": float(r.gdelt_volume) if r.gdelt_volume is not None else None,
            "gdelt_tone": float(r.gdelt_tone) if r.gdelt_tone is not None else None,
            "updated_at": r.time.isoformat(),
        })

    try:
        get_redis().setex(
            "cache:v2:countries", 3900,
            json.dumps({"countries": payload,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "version": INDEX_VERSION}, ensure_ascii=False),
        )
        logger.info(f"Warmed cache:v2:countries with {len(payload)} countries")
    except Exception as e:
        logger.warning(f"Redis cache warm failed: {e}")
