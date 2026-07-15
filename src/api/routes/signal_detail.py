"""Read-only evidence detail for detector signals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import text

from src.api.public_urls import safe_public_url
from src.api.signal_article_context import load_signal_article_previews
from src.countries import country_name_ru
from src.db import get_session
from src.engine.signals import SignalEvidence


router = APIRouter(prefix="/api/v2/signals", tags=["signals"])

MAX_SIGNAL_ARTICLES = 100
MAX_SIGNAL_STORY_IDS = 100
MAX_SIGNAL_RRI_POINTS = 100
MAX_SIGNAL_EVIDENCE_IDS = 200
MAX_SIGNAL_COUNTRIES = 50


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(name, default)
    return getattr(row, name, default)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def legacy_signal_evidence(signal: Any) -> SignalEvidence:
    """Expose only fields actually stored on a pre-evidence signal."""
    confidence = float(_value(signal, "signal_confidence", _value(signal, "confidence", 0)) or 0)
    payload = _value(signal, "payload", {}) or {}
    return SignalEvidence(
        detector=_value(signal, "signal_type") or "legacy_signal",
        detector_version="legacy-unversioned",
        threshold={},
        observed=dict(payload) if isinstance(payload, Mapping) else {},
        baseline={},
        window_start=None,
        window_end=None,
        confidence=max(0.0, min(1.0, confidence)),
        completeness="partial",
        explanation={
            "rule": "Точное правило срабатывания не сохранено",
            "current_rule_reference": None,
            "window_basis": "not_persisted",
            "window_status": "unknown",
            "limitations": [
                "Порог, базовое значение и окно детектора не были сохранены в момент срабатывания."
            ],
        },
    )


class SignalDetailService(Protocol):
    def detail(self, *, signal_id: int) -> dict[str, Any] | None: ...


class SqlSignalDetailService:
    """Load one immutable signal evidence snapshot without generation side effects."""

    def detail(self, *, signal_id: int) -> dict[str, Any] | None:
        with get_session() as session:
            signal = session.execute(
                text(
                    """
                    SELECT s.id, s.signal_type, s.country_code, s.severity,
                           s.confidence AS signal_confidence,
                           s.title, s.description, s.payload,
                           s.created_at, s.expires_at,
                           se.detector AS evidence_detector,
                           se.detector_version, se.threshold, se.observed,
                           se.baseline, se.window_start, se.window_end,
                           se.article_ids, se.story_ids, se.rri_points,
                           se.confidence AS evidence_confidence,
                           se.completeness, se.explanation
                    FROM signals s
                    LEFT JOIN signal_evidence se ON se.signal_id = s.id
                    WHERE s.id = :signal_id
                    """
                ),
                {"signal_id": signal_id},
            ).fetchone()
            if signal is None:
                return None

            if _value(signal, "evidence_detector"):
                explanation = dict(_value(signal, "explanation", {}) or {})
                evidence = SignalEvidence(
                    detector=_value(signal, "evidence_detector"),
                    detector_version=_value(signal, "detector_version"),
                    threshold=dict(_value(signal, "threshold", {}) or {}),
                    observed=dict(_value(signal, "observed", {}) or {}),
                    baseline=dict(_value(signal, "baseline", {}) or {}),
                    window_start=_value(signal, "window_start"),
                    window_end=_value(signal, "window_end"),
                    article_ids=tuple(_value(signal, "article_ids", ()) or ()),
                    story_ids=tuple(_value(signal, "story_ids", ()) or ()),
                    rri_points=tuple(_value(signal, "rri_points", ()) or ()),
                    evidence_ids=tuple(explanation.get("evidence_ids") or ()),
                    confidence=float(_value(signal, "evidence_confidence") or 0),
                    completeness=_value(signal, "completeness"),
                    explanation=explanation,
                )
            else:
                evidence = legacy_signal_evidence(signal)

            bounded_article_ids = evidence.article_ids[:MAX_SIGNAL_ARTICLES]
            bounded_story_ids = evidence.story_ids[:MAX_SIGNAL_STORY_IDS]
            articles = self._load_articles(session, bounded_article_ids)
            story = self._load_story(
                session,
                story_ids=bounded_story_ids,
                article_ids=bounded_article_ids,
            )
            countries, countries_total = self._load_countries(
                session,
                story_id=_value(story, "id") if story else None,
                signal_country=_value(signal, "country_code"),
                payload=_value(signal, "payload", {}) or {},
            )
            context_preview = None
            if not articles:
                preview = load_signal_article_previews(
                    session,
                    [signal],
                    limit=20,
                ).get(
                    int(_value(signal, "id")),
                    {
                        "kind": "unavailable",
                        "articles": [],
                        "total": 0,
                        "window_hours": None,
                        "window_start": None,
                        "window_end": None,
                    },
                )
                if preview["kind"] in {"context", "unavailable"}:
                    context_preview = preview

        return self._serialize(
            signal=signal,
            evidence=evidence,
            article_rows=articles,
            context_preview=context_preview,
            story_row=story,
            countries=countries,
            countries_total=countries_total,
        )

    @staticmethod
    def _load_articles(session: Any, article_ids: tuple[int, ...]) -> list[Any]:
        article_ids = article_ids[:MAX_SIGNAL_ARTICLES]
        if not article_ids:
            return []
        return session.execute(
            text(
                """
                SELECT ar.id, ar.title, ar.url, ar.published_at,
                       s.name AS source_name, s.country_code,
                       a.sentiment, a.action_level, a.event_key
                FROM articles ar
                JOIN sources s ON s.id = ar.source_id
                LEFT JOIN analysis a ON a.article_id = ar.id
                WHERE ar.id = ANY(CAST(:article_ids AS integer[]))
                ORDER BY array_position(CAST(:article_ids AS integer[]), ar.id), ar.id
                """
            ),
            {"article_ids": list(article_ids)},
        ).fetchall()

    @staticmethod
    def _load_story(
        session: Any,
        *,
        story_ids: tuple[int, ...],
        article_ids: tuple[int, ...],
    ) -> Any | None:
        story_ids = story_ids[:MAX_SIGNAL_STORY_IDS]
        article_ids = article_ids[:MAX_SIGNAL_ARTICLES]
        if not story_ids and not article_ids:
            return None
        return session.execute(
            text(
                """
                WITH RECURSIVE candidates AS (
                    SELECT value AS story_id, 0 AS priority
                    FROM unnest(CAST(:story_ids AS bigint[])) AS value
                    UNION ALL
                    SELECT sa.story_id, 1 AS priority
                    FROM story_articles sa
                    WHERE sa.article_id = ANY(CAST(:article_ids AS integer[]))
                ), seed AS (
                    SELECT story_id, MIN(priority) AS priority
                    FROM candidates
                    GROUP BY story_id
                ), story_walk(original_id, current_id, priority, path) AS (
                    SELECT seed.story_id, seed.story_id, seed.priority,
                           ARRAY[seed.story_id]::bigint[]
                    FROM seed
                    UNION ALL
                    SELECT walk.original_id,
                           merged.story_id,
                           walk.priority,
                           walk.path || merged.story_id
                    FROM story_walk walk
                    JOIN stories st ON st.id = walk.current_id
                    CROSS JOIN LATERAL (
                        SELECT CASE
                            WHEN (st.meta->>'merged_into_story_id')
                                     ~ '^[1-9][0-9]{0,18}$'
                             AND (
                                 length(st.meta->>'merged_into_story_id') < 19
                                 OR (st.meta->>'merged_into_story_id')
                                      <= '9223372036854775807'
                             )
                            THEN (st.meta->>'merged_into_story_id')::bigint
                        END AS story_id
                    ) merged
                    JOIN stories merged_story ON merged_story.id = merged.story_id
                    WHERE merged.story_id IS NOT NULL
                      AND NOT (merged.story_id = ANY(walk.path))
                ), terminal AS (
                    SELECT DISTINCT ON (original_id)
                           walk.original_id, walk.current_id, walk.priority
                    FROM story_walk walk
                    JOIN stories current_story ON current_story.id = walk.current_id
                    LEFT JOIN LATERAL (
                        SELECT CASE
                            WHEN (current_story.meta->>'merged_into_story_id')
                                     ~ '^[1-9][0-9]{0,18}$'
                             AND (
                                 length(current_story.meta->>'merged_into_story_id') < 19
                                 OR (current_story.meta->>'merged_into_story_id')
                                      <= '9223372036854775807'
                             )
                            THEN (current_story.meta->>'merged_into_story_id')::bigint
                        END AS story_id
                    ) terminal_merge ON TRUE
                    LEFT JOIN stories terminal_target
                      ON terminal_target.id = terminal_merge.story_id
                    WHERE terminal_target.id IS NULL
                    ORDER BY walk.original_id, cardinality(walk.path) DESC
                ), ranked AS (
                    SELECT current_id AS story_id, MIN(priority) AS priority
                    FROM terminal
                    GROUP BY current_id
                )
                SELECT st.id, st.slug, st.title_ru, st.summary, st.lifecycle,
                       st.last_seen, st.clustering_confidence
                FROM ranked r
                JOIN stories st ON st.id = r.story_id
                ORDER BY r.priority, st.last_seen DESC, st.id
                LIMIT 1
                """
            ),
            {
                "story_ids": list(story_ids),
                "article_ids": list(article_ids),
            },
        ).fetchone()

    @staticmethod
    def _load_countries(
        session: Any,
        *,
        story_id: int | None,
        signal_country: str | None,
        payload: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], int]:
        if story_id is not None:
            rows = session.execute(
                text(
                    """
                    SELECT sc.country_code, c.name_ru, sc.article_count, sc.media_tone,
                           COUNT(*) OVER() AS total_count
                    FROM story_countries sc
                    JOIN countries c ON c.code = sc.country_code
                    WHERE sc.story_id = :story_id
                    ORDER BY CASE WHEN sc.country_code = :signal_country THEN 0 ELSE 1 END,
                             sc.article_count DESC, sc.country_code
                    LIMIT :limit
                    """
                ),
                {
                    "story_id": story_id,
                    "signal_country": signal_country,
                    "limit": MAX_SIGNAL_COUNTRIES,
                },
            ).fetchall()
            total = (
                int(_value(rows[0], "total_count"))
                if rows and _value(rows[0], "total_count") is not None
                else len(rows)
            )
            result = [
                {
                    "code": (_value(row, "country_code") or "").strip(),
                    "name": _value(row, "name_ru"),
                    "article_count": int(_value(row, "article_count") or 0),
                    "media_tone": (
                        float(_value(row, "media_tone"))
                        if _value(row, "media_tone") is not None
                        else None
                    ),
                }
                for row in rows[:MAX_SIGNAL_COUNTRIES]
            ]
            if result:
                return result, total

        codes: list[str] = []
        if signal_country:
            codes.append(signal_country.strip().upper())
        payload_countries = payload.get("countries", ())
        if isinstance(payload_countries, (list, tuple)):
            codes.extend(str(code).strip().upper() for code in payload_countries if code)
        total = len(codes)
        result = [
            {"code": code, "name": country_name_ru(code)}
            for index, code in enumerate(codes)
            if code and code not in codes[:index]
        ]
        return result[:MAX_SIGNAL_COUNTRIES], min(total, len(result))

    @staticmethod
    def _serialize(
        *,
        signal: Any,
        evidence: SignalEvidence,
        article_rows: list[Any],
        context_preview: dict[str, Any] | None,
        story_row: Any | None,
        countries: list[dict[str, Any]],
        countries_total: int,
    ) -> dict[str, Any]:
        explanation = dict(evidence.explanation)
        expires_at = _value(signal, "expires_at")
        active = bool(expires_at and expires_at > datetime.now(timezone.utc))
        articles = []
        for row in article_rows:
            sentiment = _value(row, "sentiment")
            articles.append(
                {
                    "id": int(_value(row, "id")),
                    "title": _value(row, "title"),
                    "url": safe_public_url(_value(row, "url")),
                    "published_at": _iso(_value(row, "published_at")),
                    "source_name": _value(row, "source_name"),
                    "country_code": (_value(row, "country_code") or "").strip() or None,
                    "sentiment": float(sentiment) if sentiment is not None else None,
                    "action_level": _value(row, "action_level"),
                    "event_key": _value(row, "event_key"),
                }
            )

        related_story = None
        if story_row is not None:
            related_story = {
                "id": int(_value(story_row, "id")),
                "slug": _value(story_row, "slug"),
                "title": _value(story_row, "title_ru"),
                "summary": _value(story_row, "summary"),
                "lifecycle": _value(story_row, "lifecycle"),
                "last_seen": _iso(_value(story_row, "last_seen")),
                "confidence": float(_value(story_row, "clustering_confidence") or 0),
            }

        article_ids = evidence.article_ids[:MAX_SIGNAL_ARTICLES]
        story_ids = evidence.story_ids[:MAX_SIGNAL_STORY_IDS]
        rri_points = evidence.rri_points[:MAX_SIGNAL_RRI_POINTS]
        evidence_ids = evidence.evidence_ids[:MAX_SIGNAL_EVIDENCE_IDS]

        def truncation(total: int, returned: int) -> dict[str, int | bool]:
            return {
                "total": total,
                "returned": returned,
                "truncated": total > returned,
            }

        return {
            "id": int(_value(signal, "id")),
            "type": _value(signal, "signal_type"),
            "severity": _value(signal, "severity"),
            "summary": {
                "headline": _value(signal, "title"),
                "description": _value(signal, "description"),
                "what_changed": explanation.get("summary") or _value(signal, "description"),
            },
            "rule": {
                "detector": evidence.detector,
                "version": evidence.detector_version,
                "description": explanation.get("rule"),
                "threshold": dict(evidence.threshold),
                "current_rule_reference": explanation.get(
                    "current_rule_reference"
                ),
            },
            "values": {
                "observed": dict(evidence.observed),
                "baseline": dict(evidence.baseline),
                "window": {
                    "start": _iso(evidence.window_start),
                    "end": _iso(evidence.window_end),
                    "basis": explanation.get("window_basis"),
                    "status": explanation.get("window_status"),
                },
            },
            "chart_points": [dict(point) for point in rri_points],
            "articles": articles,
            "context_preview": context_preview,
            "articles_page": {
                "total": len(evidence.article_ids),
                "returned": len(articles),
                "limit": MAX_SIGNAL_ARTICLES,
                "truncated": len(evidence.article_ids) > MAX_SIGNAL_ARTICLES,
                "has_more": len(evidence.article_ids) > MAX_SIGNAL_ARTICLES,
            },
            "related_story": related_story,
            "countries": countries,
            "state": {
                "created_at": _iso(_value(signal, "created_at")),
                "expires_at": _iso(expires_at),
                "active": active,
                "status": "active" if active else "expired",
            },
            "confidence": float(evidence.confidence),
            "evidence_completeness": evidence.completeness,
            "evidence_ids": list(evidence_ids),
            "evidence": {
                "article_ids": list(article_ids),
                "story_ids": list(story_ids),
                "rri_points": [dict(point) for point in rri_points],
            },
            "evidence_truncation": {
                "evidence_ids": truncation(
                    len(evidence.evidence_ids), len(evidence_ids)
                ),
                "article_ids": truncation(
                    len(evidence.article_ids), len(article_ids)
                ),
                "story_ids": truncation(len(evidence.story_ids), len(story_ids)),
                "rri_points": truncation(len(evidence.rri_points), len(rri_points)),
                "countries": truncation(countries_total, len(countries)),
            },
            "limitations": list(explanation.get("limitations") or ()),
        }


def get_signal_detail_service() -> SignalDetailService:
    return SqlSignalDetailService()


@router.get("/{signal_id}")
def signal_detail(
    signal_id: int = Path(..., ge=1),
    service: SignalDetailService = Depends(get_signal_detail_service),
):
    result = service.detail(signal_id=signal_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return result
