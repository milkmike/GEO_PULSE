"""Signal intelligence engine — Russia-lens event signals.

Detectors (worldmonitor-inspired, adapted to GEO PULSE's tier structure):

  tier_convergence  3+ source tiers cover the same event_key within 24h —
                    the event is genuinely significant, not one outlet's spin.
  official_silence  opposition/social/independent tiers are loud about an
                    event while official+mainstream stay silent — a coverage
                    suppression signature unique to our tiered setup.
  velocity_spike    a country's relevant article flow ≥ 1.5× the 30d baseline.
  tone_shift        GDELT tone z-score |z| ≥ 1.6 vs the country's own 90d norm.
  volume_surge      GDELT Russia-coverage share ≥ 2.0× the 30d average.
  index_shift       RRI moved ≥ 7 points in 24h or crossed a level boundary.

Article-volume detectors (tier_convergence, official_silence, velocity_spike)
exclude is_backfill rows so a historical archive backfill (old published_at,
ingested today) can't masquerade as a live information storm. GDELT-, FX- and
index-based detectors read aggregate tables and never see backfill.

Anti-fatigue: every signal carries a dedup_key and a type-specific TTL;
a signal is skipped while an unexpired twin exists (worldmonitor pattern).
"""
import json
import logging
import re
import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.countries import COUNTRIES, country_name_ru, tier1_codes
from src.db import get_session

logger = logging.getLogger(__name__)

LOUD_TIERS = ("social", "domestic_opposition", "independent", "western_proxy")
QUIET_TIERS = ("official", "mainstream")

# Russian labels for the pipeline's event_type taxonomy.
EVENT_TYPE_RU = {
    "diplomatic": "дипломатия",
    "military": "военное",
    "economic": "экономика",
    "cultural": "культура",
    "security": "безопасность",
}

# Mirror the global-headlines demotion: high-action-level (war) coverage from
# these countries would otherwise permanently occupy the top of the feed.
NOTABLE_DEMOTED = ("UA",)

TTL_HOURS = {
    "tier_convergence": 24,
    "official_silence": 24,
    "velocity_spike": 12,
    "tone_shift": 24,
    "volume_surge": 24,
    "index_shift": 24,
    "fx_move": 24,
    "notable_event": 48,
}


def _emit(session, signal_type: str, country_code: str | None, dedup_key: str,
          title: str, description: str, payload: dict,
          severity: str = "info", confidence: float = 0.7) -> bool:
    """Insert a signal unless an unexpired one with the same dedup_key exists."""
    existing = session.execute(
        text("""
            SELECT id FROM signals
            WHERE dedup_key = :dk AND expires_at > NOW()
            LIMIT 1
        """),
        {"dk": dedup_key},
    ).fetchone()
    if existing:
        return False

    ttl = TTL_HOURS.get(signal_type, 24)
    session.execute(
        text("""
            INSERT INTO signals (signal_type, country_code, severity, confidence,
                                 title, description, payload, dedup_key,
                                 created_at, expires_at)
            VALUES (:type, :cc, :severity, :confidence, :title, :description,
                    CAST(:payload AS jsonb), :dk, NOW(), NOW() + make_interval(hours => :ttl))
        """),
        {
            "type": signal_type, "cc": country_code, "severity": severity,
            "confidence": round(confidence, 2), "title": title[:500],
            "description": description, "payload": json.dumps(payload, ensure_ascii=False),
            "dk": dedup_key[:200], "ttl": ttl,
        },
    )
    logger.info(f"  SIGNAL {signal_type} [{country_code}] {title[:80]}")
    return True


def detect_tier_convergence(session) -> int:
    """Same event_key reported by ≥3 distinct tiers within 24h."""
    rows = session.execute(
        text("""
            SELECT s.country_code, a.event_key,
                   COUNT(DISTINCT s.tier) AS tiers,
                   COUNT(*) AS n,
                   AVG(a.sentiment) AS avg_sent,
                   MAX(a.action_level) AS max_al,
                   ARRAY_AGG(DISTINCT s.tier) AS tier_list
            FROM analysis a
            JOIN articles ar ON a.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE ar.published_at > NOW() - INTERVAL '24 hours'
              AND ar.is_backfill = FALSE
              AND a.is_relevant = TRUE
              AND a.event_key IS NOT NULL AND a.event_key <> ''
            GROUP BY s.country_code, a.event_key
            HAVING COUNT(DISTINCT s.tier) >= 3
        """)
    ).fetchall()

    emitted = 0
    for r in rows:
        confidence = min(0.95, 0.5 + 0.1 * int(r.tiers))
        severity = "warning" if (r.max_al or 1) >= 4 else "info"
        emitted += _emit(
            session, "tier_convergence", r.country_code,
            dedup_key=f"tier_convergence:{r.country_code}:{r.event_key}",
            title=f"Конвергенция тиров: «{r.event_key}» ({country_name_ru(r.country_code)})",
            description=(
                f"Событие освещают {r.tiers} разных тира источников "
                f"({', '.join(r.tier_list)}), {r.n} статей за 24ч. "
                f"Средний тон {float(r.avg_sent or 0):+.1f}, max action level {r.max_al}."
            ),
            payload={"event_key": r.event_key, "tiers": r.tier_list,
                     "articles": int(r.n), "avg_sentiment": float(r.avg_sent or 0),
                     "max_action_level": int(r.max_al or 1)},
            severity=severity, confidence=confidence,
        )
    return emitted


def detect_official_silence(session) -> int:
    """Loud tiers cover an event ≥6h old with ≥3 articles; official tiers: zero."""
    rows = session.execute(
        text("""
            SELECT s.country_code, a.event_key,
                   COUNT(*) FILTER (WHERE s.tier = ANY(:loud)) AS loud_n,
                   COUNT(*) FILTER (WHERE s.tier = ANY(:quiet)) AS quiet_n,
                   MIN(ar.published_at) AS first_seen,
                   AVG(a.sentiment) AS avg_sent
            FROM analysis a
            JOIN articles ar ON a.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE ar.published_at > NOW() - INTERVAL '24 hours'
              AND ar.is_backfill = FALSE
              AND a.is_relevant = TRUE
              AND a.event_key IS NOT NULL AND a.event_key <> ''
            GROUP BY s.country_code, a.event_key
            HAVING COUNT(*) FILTER (WHERE s.tier = ANY(:loud)) >= 3
               AND COUNT(*) FILTER (WHERE s.tier = ANY(:quiet)) = 0
               AND MIN(ar.published_at) < NOW() - INTERVAL '6 hours'
        """),
        {"loud": list(LOUD_TIERS), "quiet": list(QUIET_TIERS)},
    ).fetchall()

    emitted = 0
    for r in rows:
        # Only meaningful where official sources actually exist and are active
        has_official = session.execute(
            text("""
                SELECT 1 FROM sources
                WHERE country_code = :cc AND tier = ANY(:quiet) AND active = TRUE
                LIMIT 1
            """),
            {"cc": r.country_code, "quiet": list(QUIET_TIERS)},
        ).fetchone()
        if not has_official:
            continue

        age_h = (datetime.now(timezone.utc) - r.first_seen).total_seconds() / 3600
        emitted += _emit(
            session, "official_silence", r.country_code,
            dedup_key=f"official_silence:{r.country_code}:{r.event_key}",
            title=f"Официальные СМИ молчат: «{r.event_key}» ({country_name_ru(r.country_code)})",
            description=(
                f"{r.loud_n} публикаций в независимых/оппозиционных/соц. источниках "
                f"за {age_h:.0f}ч, ноль в официальных и мейнстримных. "
                f"Тон {float(r.avg_sent or 0):+.1f}."
            ),
            payload={"event_key": r.event_key, "loud_articles": int(r.loud_n),
                     "hours_silent": round(age_h, 1),
                     "avg_sentiment": float(r.avg_sent or 0)},
            severity="warning", confidence=min(0.9, 0.55 + 0.05 * int(r.loud_n)),
        )
    return emitted


def detect_velocity_spike(session) -> int:
    """Relevant-article flow ≥2× the 30-day daily baseline (min 6 articles)."""
    rows = session.execute(
        text("""
            WITH daily AS (
                SELECT s.country_code,
                       COUNT(*) FILTER (WHERE ar.published_at > NOW() - INTERVAL '24 hours') AS last24,
                       COUNT(*) FILTER (WHERE ar.published_at <= NOW() - INTERVAL '24 hours') / 29.0 AS base
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE ar.published_at > NOW() - INTERVAL '30 days'
                  AND ar.is_backfill = FALSE
                  AND a.is_relevant = TRUE
                GROUP BY s.country_code
            )
            SELECT * FROM daily
            WHERE last24 >= 5 AND last24 >= 1.5 * GREATEST(base, 1.0)
        """)
    ).fetchall()

    emitted = 0
    day_bucket = datetime.now(timezone.utc).strftime("%Y%m%d")
    for r in rows:
        ratio = float(r.last24) / max(float(r.base), 1.0)
        emitted += _emit(
            session, "velocity_spike", r.country_code,
            dedup_key=f"velocity_spike:{r.country_code}:{day_bucket}",
            title=f"Информационный шторм: {country_name_ru(r.country_code)}",
            description=(
                f"{int(r.last24)} релевантных статей за 24ч против средних "
                f"{float(r.base):.1f}/день за 30 дней (×{ratio:.1f})."
            ),
            payload={"articles_24h": int(r.last24), "baseline_daily": round(float(r.base), 1),
                     "ratio": round(ratio, 1)},
            severity="warning" if ratio >= 3 else "info",
            confidence=min(0.9, 0.5 + 0.1 * ratio),
        )
    return emitted


def detect_gdelt_shifts(session) -> int:
    """GDELT tone z-score shifts and volume surges per country."""
    rows = session.execute(
        text("""
            SELECT country_code,
                   ARRAY_AGG(tone_avg ORDER BY day DESC) AS tones,
                   ARRAY_AGG(volume_share ORDER BY day DESC) AS shares,
                   ARRAY_AGG(volume ORDER BY day DESC) AS volumes
            FROM gdelt_daily
            WHERE day > CURRENT_DATE - 91 AND tone_avg IS NOT NULL
            GROUP BY country_code
            HAVING COUNT(*) >= 14
        """)
    ).fetchall()

    emitted = 0
    day_bucket = datetime.now(timezone.utc).strftime("%Y%m%d")
    for r in rows:
        tones = [float(t) for t in r.tones if t is not None]
        if len(tones) < 14:
            continue
        current = tones[0]
        baseline = tones[2:]  # exclude the two most recent days from the norm
        mean = statistics.mean(baseline)
        std = statistics.stdev(baseline) if len(baseline) > 1 else 1.0
        std = max(std, 0.3)  # tone is bounded; avoid hair-trigger z-scores
        z = (current - mean) / std

        if abs(z) >= 1.6:
            direction = "потеплел" if z > 0 else "похолодел"
            emitted += _emit(
                session, "tone_shift", r.country_code,
                dedup_key=f"tone_shift:{r.country_code}:{day_bucket}",
                title=f"Сдвиг тона о России: {country_name_ru(r.country_code)} ({direction})",
                description=(
                    f"Тон освещения России {direction}: {current:+.1f} против нормы "
                    f"{mean:+.1f}±{std:.1f} за 90 дней (z={z:+.1f})."
                ),
                payload={"tone": round(current, 2), "mean_90d": round(mean, 2),
                         "std": round(std, 2), "z_score": round(z, 2)},
                severity="critical" if abs(z) >= 3 else "warning",
                confidence=min(0.9, 0.5 + 0.1 * abs(z)),
            )

        shares = [float(s) for s in r.shares if s is not None]
        if len(shares) >= 14:
            cur_share = shares[0]
            base_share = statistics.mean(shares[2:32]) if len(shares) > 3 else 0
            cur_vol = float(r.volumes[0]) if r.volumes and r.volumes[0] is not None else 0
            if base_share > 0 and cur_vol >= 10 and cur_share >= 2.0 * base_share:
                emitted += _emit(
                    session, "volume_surge", r.country_code,
                    dedup_key=f"volume_surge:{r.country_code}:{day_bucket}",
                    title=f"Всплеск внимания к России: {country_name_ru(r.country_code)}",
                    description=(
                        f"Доля «российской» повестки в национальных медиа выросла до "
                        f"{cur_share*100:.2f}% против средних {base_share*100:.2f}% "
                        f"(×{cur_share/base_share:.1f}, {cur_vol:.0f} статей/день)."
                    ),
                    payload={"share": round(cur_share, 5), "baseline_share": round(base_share, 5),
                             "ratio": round(cur_share / base_share, 1), "volume": cur_vol},
                    severity="warning", confidence=0.75,
                )
    return emitted


def detect_index_shifts(session) -> int:
    """RRI 24h move ≥ 10 points, or level boundary crossed."""
    rows = session.execute(
        text("""
            SELECT DISTINCT ON (country_code)
                   country_code, score, level, delta_24h, time
            FROM ru_index
            ORDER BY country_code, time DESC
        """)
    ).fetchall()

    emitted = 0
    day_bucket = datetime.now(timezone.utc).strftime("%Y%m%d")
    for r in rows:
        if r.delta_24h is None:
            continue
        delta = float(r.delta_24h)
        if abs(delta) < 7:
            continue
        # Upper sanity cap: a genuine 24h RRI move can't exceed the boost layer's
        # ±15 reach by much. Anything beyond 18 points in a day is data instability
        # (e.g. a fresh baseline after a bulk re-analysis), not real diplomacy —
        # skip it instead of crying wolf with a critical "Скачок индекса".
        if abs(delta) > 18:
            continue
        direction = "вверх" if delta > 0 else "вниз"
        emitted += _emit(
            session, "index_shift", r.country_code,
            dedup_key=f"index_shift:{r.country_code}:{day_bucket}",
            title=f"Скачок индекса: {country_name_ru(r.country_code)} {delta:+.1f} за 24ч",
            description=(
                f"Индекс отношений с Россией сдвинулся {direction} на {abs(delta):.1f} "
                f"пунктов за сутки: сейчас {float(r.score):+.1f} [{r.level}]."
            ),
            payload={"score": float(r.score), "delta_24h": delta, "level": r.level},
            severity="critical" if abs(delta) >= 14 else "warning",
            confidence=0.8,
        )
    return emitted


def detect_notable_events(session) -> int:
    """Surface real high-action events (AL≥4, last 48h, not backfill) into the feed.

    Picks one representative article per event_key (highest action_level, then most
    reprinted, then newest), ranks globally by impact, and diversifies so no single
    country floods the feed: ≤3 per country, ≤2 for Ukraine, and Ukraine is sorted
    after every non-UA event (mirrors the global-headlines demotion).
    """
    rows = session.execute(
        text("""
            SELECT DISTINCT ON (a.event_key)
                   a.event_key, a.event_type, a.action_level, a.sentiment,
                   ar.title, ar.reprint_count, ar.published_at, s.country_code
            FROM analysis a
            JOIN articles ar ON a.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE a.is_relevant = TRUE
              AND ar.is_backfill = FALSE
              AND a.action_level >= 4
              AND a.event_type IN ('diplomatic', 'economic', 'military', 'security')
              AND a.event_key IS NOT NULL AND a.event_key != ''
              AND ar.published_at > NOW() - INTERVAL '48 hours'
              -- Russia anchor: the relevance filter has false positives on local
              -- crime/court news; require an explicit Russia-orbit mention so the
              -- signal feed stays genuinely about Russia relations.
              AND (ar.title || ' ' || COALESCE(ar.body, '')) ~*
                  'russia|росси|кремл|kreml|putin|путин|moscow|москв|лавров|lavrov|одкб|csto|еаэс|eaeu|\mснг\M|\mсоюз'
            ORDER BY a.event_key, a.action_level DESC,
                     ar.reprint_count DESC NULLS LAST, ar.published_at DESC
        """)
    ).fetchall()

    # Rank by impact: demoted countries last, then action_level, then reprints.
    ranked = sorted(
        rows,
        key=lambda r: (
            1 if r.country_code in NOTABLE_DEMOTED else 0,
            -int(r.action_level or 0),
            -int(r.reprint_count or 0),
        ),
    )

    # Diversify: ≤3 per country, ≤2 for Ukraine, ≤45 total.
    per_country: dict[str | None, int] = {}
    kept = []
    for r in ranked:
        cc = r.country_code
        cap = 2 if cc in NOTABLE_DEMOTED else 3
        if per_country.get(cc, 0) >= cap:
            continue
        per_country[cc] = per_country.get(cc, 0) + 1
        kept.append(r)
        if len(kept) >= 45:
            break

    emitted = 0
    for r in kept:
        title = (r.title or "").strip() or r.event_key
        # Some source titles carry raw markdown link syntax [text](url) — unwrap to text.
        title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", title).strip()
        al = int(r.action_level or 0)
        # Demoted countries (Ukraine) are capped at "warning" so their war coverage
        # never leads the feed as red criticals (consistent with the headlines policy).
        if r.country_code in NOTABLE_DEMOTED:
            severity = "warning" if al >= 5 else "info"
        else:
            severity = "critical" if al >= 6 else "warning" if al == 5 else "info"
        emitted += _emit(
            session, "notable_event", r.country_code,
            dedup_key=f"notable:{r.event_key}"[:200],
            title=title,
            description=(
                f"{country_name_ru(r.country_code)} · "
                f"{EVENT_TYPE_RU.get(r.event_type, r.event_type)}"
            ),
            payload={"event_key": r.event_key, "action_level": al,
                     "event_type": r.event_type,
                     "sentiment": float(r.sentiment) if r.sentiment is not None else None},
            severity=severity,
            confidence=min(0.9, 0.55 + 0.07 * al),
        )
    return emitted


def detect_fx_moves(session) -> int:
    """Currency moved ≥2% vs RUB in a day — did media signals precede it?

    «Медиа ведут рынки»: if a tone_shift/volume_surge fired for the currency's
    countries within the prior 72h, the move was "predicted"; otherwise it's a
    silent move worth attention.
    """
    from src.collectors.fx import CURRENCY_COUNTRIES

    rows = session.execute(
        text("""
            SELECT DISTINCT ON (currency) currency, day, rate_to_rub, change_1d_pct
            FROM fx_rates
            WHERE day > CURRENT_DATE - 3 AND change_1d_pct IS NOT NULL
            ORDER BY currency, day DESC
        """)
    ).fetchall()

    emitted = 0
    for r in rows:
        change = float(r.change_1d_pct)
        if abs(change) < 2.0:
            continue
        countries = CURRENCY_COUNTRIES.get(r.currency, [])
        media = session.execute(
            text("""
                SELECT COUNT(*) AS n FROM signals
                WHERE signal_type IN ('tone_shift', 'volume_surge', 'velocity_spike')
                  AND country_code = ANY(:codes)
                  AND created_at > NOW() - INTERVAL '72 hours'
            """),
            {"codes": countries or ["--"]},
        ).fetchone()
        predicted = bool(media and media.n)

        direction = "укрепилась к рублю" if change > 0 else "ослабла к рублю"
        names = ", ".join(country_name_ru(c) for c in countries[:4]) or r.currency
        emitted += _emit(
            session, "fx_move", countries[0] if countries else None,
            dedup_key=f"fx_move:{r.currency}:{r.day}",
            title=(f"Валютный сдвиг {r.currency} {change:+.1f}% — "
                   + ("медиа предупреждали" if predicted else "тихий сдвиг")),
            description=(
                f"{r.currency} ({names}) {direction} на {abs(change):.1f}% за день, "
                f"курс {float(r.rate_to_rub):.4f} ₽. "
                + ("Медиа-сигналы по стране были в предыдущие 72ч — медиа вели рынок."
                   if predicted else
                   "Медиа-сигналов по стране не было — сдвиг без информационного следа.")
            ),
            payload={"currency": r.currency, "change_1d_pct": change,
                     "rate_to_rub": float(r.rate_to_rub), "countries": countries,
                     "media_preceded": predicted},
            severity="warning" if abs(change) >= 4 else "info",
            confidence=0.8 if predicted else 0.65,
        )
    return emitted


def cleanup_expired(session, keep_days: int = 30) -> int:
    """Drop long-expired signals to keep the table lean."""
    res = session.execute(
        text("DELETE FROM signals WHERE expires_at < NOW() - make_interval(days => :d)"),
        {"d": keep_days},
    )
    return res.rowcount or 0


def detect_all() -> dict:
    """Run every detector in its own session so one failure can't poison
    the others' transaction. Returns counts per detector."""
    counts = {}
    for name, fn in (
        ("tier_convergence", detect_tier_convergence),
        ("official_silence", detect_official_silence),
        ("velocity_spike", detect_velocity_spike),
        ("gdelt_shifts", detect_gdelt_shifts),
        ("index_shifts", detect_index_shifts),
        ("notable_events", detect_notable_events),
        ("fx_moves", detect_fx_moves),
    ):
        try:
            with get_session() as session:
                counts[name] = fn(session)
        except Exception as e:
            logger.error(f"Detector {name} failed: {e}", exc_info=True)
            counts[name] = -1
    try:
        with get_session() as session:
            counts["cleaned"] = cleanup_expired(session)
    except Exception as e:
        logger.error(f"Signal cleanup failed: {e}")
    return counts
