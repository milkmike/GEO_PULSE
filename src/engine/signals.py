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
  index_shift       RRI moved by 7–18 points in 24h.

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
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Mapping

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
    "sanctions_escalation": 72,
}

DETECTOR_VERSIONS = {
    "tier_convergence": "1.0",
    "official_silence": "1.0",
    "velocity_spike": "1.0",
    "tone_shift": "1.0",
    "volume_surge": "1.0",
    "index_shift": "1.0",
    "fx_move": "1.0",
    "notable_event": "1.0",
    "sanctions_escalation": "1.0",
}


@dataclass(frozen=True)
class SignalEvidence:
    """Immutable detector inputs captured at signal creation time."""

    detector: str
    detector_version: str
    threshold: Mapping[str, Any]
    observed: Mapping[str, Any]
    baseline: Mapping[str, Any]
    window_start: datetime | None
    window_end: datetime | None
    confidence: float
    completeness: Literal["complete", "partial"]
    explanation: Mapping[str, Any]
    article_ids: tuple[int, ...] = ()
    story_ids: tuple[int, ...] = ()
    rri_points: tuple[Mapping[str, Any], ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.detector or not self.detector_version:
            raise ValueError("detector and detector_version are required")
        if self.completeness not in {"complete", "partial"}:
            raise ValueError("completeness must be complete or partial")
        if not 0 <= float(self.confidence) <= 1:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(
            self,
            "article_ids",
            tuple(sorted({int(value) for value in self.article_ids})),
        )
        object.__setattr__(
            self,
            "story_ids",
            tuple(sorted({int(value) for value in self.story_ids})),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            tuple(sorted({str(value) for value in self.evidence_ids if value})),
        )


def _evidence(
    detector: str,
    *,
    threshold: Mapping[str, Any],
    observed: Mapping[str, Any],
    baseline: Mapping[str, Any],
    window_start: datetime,
    window_end: datetime,
    confidence: float,
    rule: str,
    limitations: tuple[str, ...],
    completeness: Literal["complete", "partial"] = "complete",
    article_ids: tuple[int, ...] = (),
    rri_points: tuple[Mapping[str, Any], ...] = (),
    evidence_ids: tuple[str, ...] = (),
) -> SignalEvidence:
    return SignalEvidence(
        detector=detector,
        detector_version=DETECTOR_VERSIONS[detector],
        threshold=threshold,
        observed=observed,
        baseline=baseline,
        window_start=window_start,
        window_end=window_end,
        article_ids=article_ids,
        rri_points=rri_points,
        evidence_ids=evidence_ids,
        confidence=confidence,
        completeness=completeness,
        explanation={"rule": rule, "limitations": list(limitations)},
    )


def _row_article_ids(row: Any) -> tuple[int, ...]:
    return tuple(int(value) for value in (getattr(row, "article_ids", None) or ()))


def _article_evidence_ids(article_ids: tuple[int, ...]) -> tuple[str, ...]:
    return tuple(f"article:{article_id}" for article_id in article_ids)


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _emit(session, signal_type: str, country_code: str | None, dedup_key: str,
          title: str, description: str, payload: dict,
          *, evidence: SignalEvidence,
          severity: str = "info") -> bool:
    """Insert a signal unless an unexpired one with the same dedup_key exists."""
    if evidence.detector != signal_type:
        raise ValueError(
            f"evidence detector {evidence.detector!r} does not match {signal_type!r}"
        )
    expected_version = DETECTOR_VERSIONS.get(signal_type)
    if expected_version and evidence.detector_version != expected_version:
        raise ValueError(
            f"evidence version {evidence.detector_version!r} does not match "
            f"detector version {expected_version!r}"
        )
    normalized_dedup_key = dedup_key[:200]
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:dk, 0))"),
        {"dk": normalized_dedup_key},
    )
    existing = session.execute(
        text("""
            SELECT id FROM signals
            WHERE dedup_key = :dk AND expires_at > NOW()
            LIMIT 1
        """),
        {"dk": normalized_dedup_key},
    ).fetchone()
    if existing:
        return False

    ttl = TTL_HOURS.get(signal_type, 24)
    inserted = session.execute(
        text("""
            INSERT INTO signals (signal_type, country_code, severity, confidence,
                                 title, description, payload, dedup_key,
                                 created_at, expires_at)
            VALUES (:type, :cc, :severity, :confidence, :title, :description,
                    CAST(:payload AS jsonb), :dk, NOW(), NOW() + make_interval(hours => :ttl))
            RETURNING id
        """),
        {
            "type": signal_type, "cc": country_code, "severity": severity,
            "confidence": round(float(evidence.confidence), 2), "title": title[:500],
            "description": description, "payload": json.dumps(payload, ensure_ascii=False),
            "dk": normalized_dedup_key, "ttl": ttl,
        },
    ).fetchone()
    signal_id = int(inserted.id)

    story_ids = set(evidence.story_ids)
    if evidence.article_ids:
        related_stories = session.execute(
            text("""
                SELECT DISTINCT story_id
                FROM story_articles
                WHERE article_id = ANY(CAST(:article_ids AS integer[]))
                ORDER BY story_id
            """),
            {"article_ids": list(evidence.article_ids)},
        ).fetchall()
        story_ids.update(int(row.story_id) for row in related_stories)

    explanation = dict(evidence.explanation)
    explanation["evidence_ids"] = list(evidence.evidence_ids)
    session.execute(
        text("""
            INSERT INTO signal_evidence (
                signal_id, detector, detector_version, threshold, observed,
                baseline, window_start, window_end, article_ids, story_ids,
                rri_points, confidence, completeness, explanation
            ) VALUES (
                :signal_id, :detector, :detector_version,
                CAST(:threshold AS jsonb), CAST(:observed AS jsonb),
                CAST(:baseline AS jsonb), :window_start, :window_end,
                :article_ids, :story_ids, CAST(:rri_points AS jsonb),
                :confidence, :completeness, CAST(:explanation AS jsonb)
            )
        """),
        {
            "signal_id": signal_id,
            "detector": evidence.detector,
            "detector_version": evidence.detector_version,
            "threshold": json.dumps(
                dict(evidence.threshold), ensure_ascii=False, default=_json_default
            ),
            "observed": json.dumps(
                dict(evidence.observed), ensure_ascii=False, default=_json_default
            ),
            "baseline": json.dumps(
                dict(evidence.baseline), ensure_ascii=False, default=_json_default
            ),
            "window_start": evidence.window_start,
            "window_end": evidence.window_end,
            "article_ids": list(evidence.article_ids),
            "story_ids": sorted(story_ids),
            "rri_points": json.dumps(
                list(evidence.rri_points), ensure_ascii=False, default=_json_default
            ),
            "confidence": round(float(evidence.confidence), 3),
            "completeness": evidence.completeness,
            "explanation": json.dumps(
                explanation, ensure_ascii=False, default=_json_default
            ),
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
                   ARRAY_AGG(DISTINCT s.tier) AS tier_list,
                   ARRAY_AGG(DISTINCT ar.id ORDER BY ar.id) AS article_ids
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
    detected_at = datetime.now(timezone.utc)
    for r in rows:
        confidence = min(0.95, 0.5 + 0.1 * int(r.tiers))
        severity = "warning" if (r.max_al or 1) >= 4 else "info"
        article_ids = _row_article_ids(r)
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
            evidence=_evidence(
                "tier_convergence",
                threshold={"minimum_distinct_tiers": 3},
                observed={
                    "event_key": r.event_key,
                    "distinct_tiers": int(r.tiers),
                    "tiers": list(r.tier_list or ()),
                    "article_count": int(r.n),
                    "average_sentiment": float(r.avg_sent or 0),
                    "maximum_action_level": int(r.max_al or 1),
                },
                baseline={"type": "not_applicable"},
                window_start=detected_at - timedelta(hours=24),
                window_end=detected_at,
                confidence=confidence,
                rule="Не менее трёх разных тиров освещают одно событие за 24 часа",
                limitations=(
                    "Учитываются только собранные и проиндексированные публикации за последние 24 часа.",
                ),
                article_ids=article_ids,
                evidence_ids=_article_evidence_ids(article_ids),
            ),
            severity=severity,
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
                   AVG(a.sentiment) AS avg_sent,
                   ARRAY_AGG(DISTINCT ar.id ORDER BY ar.id)
                       FILTER (WHERE s.tier = ANY(:loud)) AS article_ids
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
    detected_at = datetime.now(timezone.utc)
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
        confidence = min(0.9, 0.55 + 0.05 * int(r.loud_n))
        article_ids = _row_article_ids(r)
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
            evidence=_evidence(
                "official_silence",
                threshold={
                    "minimum_loud_articles": 3,
                    "maximum_quiet_articles": 0,
                    "minimum_silence_hours": 6,
                },
                observed={
                    "event_key": r.event_key,
                    "loud_articles": int(r.loud_n),
                    "quiet_articles": int(r.quiet_n),
                    "hours_silent": round(age_h, 1),
                    "average_sentiment": float(r.avg_sent or 0),
                },
                baseline={
                    "type": "active_source_availability",
                    "official_or_mainstream_sources_available": True,
                },
                window_start=detected_at - timedelta(hours=24),
                window_end=detected_at,
                confidence=confidence,
                rule=(
                    "Не менее трёх публикаций в громких тирах, ноль в официальных "
                    "и мейнстримных источниках спустя не менее шести часов"
                ),
                limitations=(
                    "Молчание означает отсутствие публикаций только среди активных проиндексированных источников.",
                ),
                article_ids=article_ids,
                evidence_ids=_article_evidence_ids(article_ids),
            ),
            severity="warning",
        )
    return emitted


def detect_velocity_spike(session) -> int:
    """Relevant-article flow ≥1.5× the 30-day daily baseline (min 5 articles)."""
    rows = session.execute(
        text("""
            WITH daily AS (
                SELECT s.country_code,
                       COUNT(*) FILTER (WHERE ar.published_at > NOW() - INTERVAL '24 hours') AS last24,
                       COUNT(*) FILTER (WHERE ar.published_at <= NOW() - INTERVAL '24 hours') / 29.0 AS base,
                       ARRAY_AGG(DISTINCT ar.id ORDER BY ar.id)
                           FILTER (WHERE ar.published_at > NOW() - INTERVAL '24 hours') AS article_ids
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
    detected_at = datetime.now(timezone.utc)
    day_bucket = detected_at.strftime("%Y%m%d")
    for r in rows:
        ratio = float(r.last24) / max(float(r.base), 1.0)
        confidence = min(0.9, 0.5 + 0.1 * ratio)
        article_ids = _row_article_ids(r)
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
            evidence=_evidence(
                "velocity_spike",
                threshold={
                    "minimum_articles_24h": 5,
                    "minimum_baseline_ratio": 1.5,
                },
                observed={
                    "articles_24h": int(r.last24),
                    "ratio": round(ratio, 2),
                },
                baseline={
                    "type": "rolling_daily_average",
                    "daily_average": round(float(r.base), 2),
                    "comparison_days": 29,
                },
                window_start=detected_at - timedelta(days=30),
                window_end=detected_at,
                confidence=confidence,
                rule=(
                    "Не менее пяти релевантных статей за 24 часа и поток не менее "
                    "чем в 1,5 раза выше среднего за предыдущие 29 дней"
                ),
                limitations=(
                    "Базовая линия отражает только релевантные статьи доступных проиндексированных источников.",
                ),
                article_ids=article_ids,
                evidence_ids=_article_evidence_ids(article_ids),
            ),
            severity="warning" if ratio >= 3 else "info",
        )
    return emitted


def detect_gdelt_shifts(session) -> int:
    """GDELT tone z-score shifts and volume surges per country."""
    rows = session.execute(
        text("""
            SELECT country_code,
                   MIN(day) AS earliest_day,
                   MAX(day) AS latest_day,
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
    detected_at = datetime.now(timezone.utc)
    day_bucket = detected_at.strftime("%Y%m%d")
    for r in rows:
        latest_day = r.latest_day or detected_at.date()
        earliest_day = r.earliest_day or latest_day
        latest_day_start = _day_start(latest_day)
        earliest_day_start = _day_start(earliest_day)
        gdelt_evidence_id = f"gdelt_daily:{r.country_code}:{latest_day.isoformat()}"
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
            confidence = min(0.9, 0.5 + 0.1 * abs(z))
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
                evidence=_evidence(
                    "tone_shift",
                    threshold={
                        "absolute_z_score_min": 1.6,
                        "standard_deviation_floor": 0.3,
                    },
                    observed={
                        "tone": round(current, 2),
                        "z_score": round(z, 2),
                    },
                    baseline={
                        "type": "historical_tone_distribution",
                        "mean": round(mean, 2),
                        "standard_deviation": round(std, 2),
                        "sample_days": len(baseline),
                        "excluded_recent_days": 2,
                    },
                    window_start=earliest_day_start,
                    window_end=latest_day_start + timedelta(days=1),
                    confidence=confidence,
                    rule="Абсолютный z-score тона не меньше 1,6 к собственной 90-дневной норме",
                    limitations=(
                        "Агрегат GDELT описывает изменение тона и сам по себе не устанавливает причину.",
                    ),
                    evidence_ids=(gdelt_evidence_id,),
                ),
                severity="critical" if abs(z) >= 3 else "warning",
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
                    evidence=_evidence(
                        "volume_surge",
                        threshold={
                            "minimum_share_ratio": 2.0,
                            "minimum_daily_volume": 10,
                        },
                        observed={
                            "share": round(cur_share, 5),
                            "ratio": round(cur_share / base_share, 2),
                            "daily_volume": cur_vol,
                        },
                        baseline={
                            "type": "historical_coverage_share",
                            "share": round(base_share, 5),
                            "sample_days": len(shares[2:32]),
                            "excluded_recent_days": 2,
                        },
                        window_start=earliest_day_start,
                        window_end=latest_day_start + timedelta(days=1),
                        confidence=0.75,
                        rule=(
                            "Доля российской повестки не менее чем вдвое выше 30-дневной "
                            "нормы при объёме не менее десяти статей"
                        ),
                        limitations=(
                            "Агрегат GDELT показывает изменение доли повестки без доказательства причинности.",
                        ),
                        evidence_ids=(gdelt_evidence_id,),
                    ),
                    severity="warning",
                )
    return emitted


def detect_index_shifts(session) -> int:
    """RRI 24h move from 7 through 18 points."""
    rows = session.execute(
        text("""
            WITH latest AS (
                SELECT DISTINCT ON (country_code)
                       country_code, score, level, delta_24h, time
                FROM ru_index
                ORDER BY country_code, time DESC
            )
            SELECT l.country_code, l.score, l.level, l.delta_24h, l.time,
                   previous.time AS baseline_time,
                   previous.score AS baseline_score,
                   previous.level AS baseline_level
            FROM latest l
            LEFT JOIN LATERAL (
                SELECT r.time, r.score, r.level
                FROM ru_index r
                WHERE r.country_code = l.country_code
                  AND r.time <= l.time - INTERVAL '18 hours'
                ORDER BY r.time DESC
                LIMIT 1
            ) previous ON TRUE
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
        confidence = 0.8
        point_time = r.time
        observed_point = {
            "country_code": r.country_code,
            "time": point_time.isoformat(),
            "score": float(r.score),
            "delta_24h": delta,
            "level": r.level,
            "role": "observed",
        }
        rri_points = []
        evidence_ids = []
        has_baseline = r.baseline_time is not None and r.baseline_score is not None
        if has_baseline:
            rri_points.append({
                "country_code": r.country_code,
                "time": r.baseline_time.isoformat(),
                "score": float(r.baseline_score),
                "level": r.baseline_level,
                "role": "baseline",
            })
            evidence_ids.append(
                f"rri:{r.country_code}:{r.baseline_time.isoformat()}"
            )
        rri_points.append(observed_point)
        evidence_ids.append(f"rri:{r.country_code}:{point_time.isoformat()}")
        baseline_evidence = {
            "type": "rri_point",
            "status": "available" if has_baseline else "missing",
            "comparison_hours": 24,
            "score": float(r.baseline_score) if has_baseline else None,
            "time": r.baseline_time.isoformat() if has_baseline else None,
            "level": r.baseline_level if has_baseline else None,
        }
        emitted += _emit(
            session, "index_shift", r.country_code,
            dedup_key=f"index_shift:{r.country_code}:{day_bucket}",
            title=f"Скачок индекса: {country_name_ru(r.country_code)} {delta:+.1f} за 24ч",
            description=(
                f"Индекс отношений с Россией сдвинулся {direction} на {abs(delta):.1f} "
                f"пунктов за сутки: сейчас {float(r.score):+.1f} [{r.level}]."
            ),
            payload={"score": float(r.score), "delta_24h": delta, "level": r.level},
            evidence=_evidence(
                "index_shift",
                threshold={
                    "absolute_delta_min": 7.0,
                    "absolute_delta_sanity_max": 18.0,
                },
                observed={
                    "score": float(r.score),
                    "delta_24h": delta,
                    "level": r.level,
                },
                baseline=baseline_evidence,
                window_start=r.baseline_time or point_time - timedelta(hours=24),
                window_end=point_time,
                confidence=confidence,
                rule=(
                    "Абсолютный сдвиг RRI за 24 часа от 7 до 18 пунктов; "
                    "большие значения считаются нестабильностью данных"
                ),
                limitations=(
                    "Сигнал фиксирует изменение RRI, но сам по себе не устанавливает новостную причину.",
                ) + ((
                    "Сохранённая точка сравнения RRI недоступна; доказательство сдвига частичное.",
                ) if not has_baseline else ()),
                completeness="complete" if has_baseline else "partial",
                rri_points=tuple(rri_points),
                evidence_ids=tuple(evidence_ids),
            ),
            severity="critical" if abs(delta) >= 14 else "warning",
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
                   ar.id AS article_id,
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
                  'russia|росси|кремл|kreml|putin|путин|moscow|москв|лавров|lavrov|одкб|csto|еаэс|eaeu|\\mснг\\M|\\mсоюз'
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
    detected_at = datetime.now(timezone.utc)
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
        confidence = min(0.9, 0.55 + 0.07 * al)
        article_ids = (int(r.article_id),)
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
            evidence=_evidence(
                "notable_event",
                threshold={"minimum_action_level": 4},
                observed={
                    "event_key": r.event_key,
                    "action_level": al,
                    "event_type": r.event_type,
                    "sentiment": (
                        float(r.sentiment) if r.sentiment is not None else None
                    ),
                    "reprint_count": int(r.reprint_count or 0),
                },
                baseline={"type": "not_applicable"},
                window_start=detected_at - timedelta(hours=48),
                window_end=detected_at,
                confidence=confidence,
                rule=(
                    "Релевантное событие дипломатического, экономического, военного "
                    "или безопасностного типа с action level не ниже 4 за 48 часов"
                ),
                limitations=(
                    "Сохранена одна репрезентативная статья события, а не полный список перепечаток.",
                ),
                article_ids=article_ids,
                evidence_ids=_article_evidence_ids(article_ids),
            ),
            severity=severity,
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
            WITH latest AS (
                SELECT DISTINCT ON (currency)
                       currency, day, rate_to_rub, change_1d_pct
                FROM fx_rates
                WHERE day > CURRENT_DATE - 3 AND change_1d_pct IS NOT NULL
                ORDER BY currency, day DESC
            )
            SELECT l.currency, l.day, l.rate_to_rub, l.change_1d_pct,
                   previous.day AS baseline_day,
                   previous.rate_to_rub AS baseline_rate
            FROM latest l
            LEFT JOIN LATERAL (
                SELECT f.day, f.rate_to_rub
                FROM fx_rates f
                WHERE f.currency = l.currency AND f.day < l.day
                ORDER BY f.day DESC
                LIMIT 1
            ) previous ON TRUE
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
        confidence = 0.8 if predicted else 0.65
        rate_day = r.day
        rate_day_start = _day_start(rate_day)
        has_baseline = r.baseline_day is not None and r.baseline_rate is not None
        baseline = {
            "type": "previous_fx_rate",
            "status": "available" if has_baseline else "missing",
            "day": r.baseline_day.isoformat() if has_baseline else None,
            "rate_to_rub": float(r.baseline_rate) if has_baseline else None,
        }
        evidence_ids = [f"fx_rate:{r.currency}:{rate_day.isoformat()}"]
        if has_baseline:
            evidence_ids.append(
                f"fx_rate:{r.currency}:{r.baseline_day.isoformat()}"
            )

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
            evidence=_evidence(
                "fx_move",
                threshold={"absolute_daily_change_percent_min": 2.0},
                observed={
                    "currency": r.currency,
                    "change_1d_percent": change,
                    "rate_to_rub": float(r.rate_to_rub),
                    "media_preceded": predicted,
                    "preceding_media_signal_count": int(media.n if media else 0),
                    "media_lookback_hours": 72,
                    "countries": list(countries),
                },
                baseline=baseline,
                window_start=(
                    _day_start(r.baseline_day) if has_baseline else rate_day_start
                ),
                window_end=rate_day_start + timedelta(days=1),
                confidence=confidence,
                rule="Абсолютное дневное движение валюты к рублю не меньше 2%",
                limitations=(
                    "Предшествование медиа-сигналов валютному движению не доказывает причинность.",
                ) + ((
                    "Предыдущая сохранённая валютная ставка недоступна; доказательство движения частичное.",
                ) if not has_baseline else ()),
                completeness="complete" if has_baseline else "partial",
                evidence_ids=tuple(evidence_ids),
            ),
            severity="warning" if abs(change) >= 4 else "info",
        )
    return emitted


def detect_sanctions_escalation(session) -> int:
    """A jurisdiction tightened sanctions since the last snapshot.

    Reads the structural `sanctions_pressure` table (OpenSanctions, refreshed by
    scripts/collect_sanctions.py). The table is optional/new, so the query runs
    inside begin_nested() per the project convention — a missing table yields no
    signals instead of poisoning the session. Only registry countries are
    surfaced (skips supranational 'EU' etc.).
    """
    MIN_DELTA = 25  # ignore routine churn; flag meaningful batches of new targets
    try:
        with session.begin_nested():
            rows = session.execute(
                text("""
                    SELECT country_code, delta, target_count, lists_count, last_change
                    FROM sanctions_pressure
                    WHERE delta >= :min_delta
                      AND prev_target_count > 0   -- skip the first snapshot (no baseline)
                      AND updated_at > NOW() - INTERVAL '2 days'
                """),
                {"min_delta": MIN_DELTA},
            ).fetchall()
    except Exception as e:  # table absent or not yet populated
        logger.info(f"sanctions_pressure unavailable, skipping: {e}")
        return 0

    emitted = 0
    detected_at = datetime.now(timezone.utc)
    month_bucket = detected_at.strftime("%Y%m")
    for r in rows:
        if r.country_code not in COUNTRIES:
            continue
        emitted += _emit(
            session, "sanctions_escalation", r.country_code,
            dedup_key=f"sanctions_escalation:{r.country_code}:{month_bucket}",
            title=f"Санкционное ужесточение: {country_name_ru(r.country_code)} (+{int(r.delta)} целей)",
            description=(
                f"{country_name_ru(r.country_code)} расширила санкционные списки на "
                f"{int(r.delta)} целей (всего {int(r.target_count)} в {int(r.lists_count)} "
                f"программах, обновление {r.last_change})."
            ),
            payload={"delta": int(r.delta), "target_count": int(r.target_count),
                     "lists_count": int(r.lists_count),
                     "last_change": str(r.last_change) if r.last_change else None},
            evidence=_evidence(
                "sanctions_escalation",
                threshold={"minimum_new_targets": MIN_DELTA},
                observed={
                    "new_targets": int(r.delta),
                    "target_count": int(r.target_count),
                    "lists_count": int(r.lists_count),
                    "last_change": str(r.last_change) if r.last_change else None,
                },
                baseline={
                    "type": "previous_sanctions_snapshot",
                    "previous_target_count": int(r.target_count) - int(r.delta),
                },
                window_start=detected_at - timedelta(days=2),
                window_end=detected_at,
                confidence=0.7,
                rule=(
                    "Не менее 25 новых целей с существующим предыдущим снимком "
                    "санкционного реестра за последние два дня"
                ),
                limitations=(
                    "Охват зависит от актуальности и состава внешнего санкционного реестра.",
                ),
                evidence_ids=(
                    f"sanctions_pressure:{r.country_code}:{r.last_change}",
                ),
            ),
            severity="warning" if r.delta >= 100 else "info",
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
        ("sanctions_escalation", detect_sanctions_escalation),
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
