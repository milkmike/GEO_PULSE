"""Deterministic explanations for shifts between persisted RRI snapshots.

RRI decomposition uses only stored ``ru_index`` values and their stored formula
weights. Thermometer article inputs are used solely for explicitly estimated
counterfactual media contributions.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text

from src.methodology import TEMPERATURE_METHODOLOGY


COUNTERFACTUAL_METHOD = "counterfactual_event_cluster_removal_v1"
ACTION_MULTIPLIERS = TEMPERATURE_METHODOLOGY.action_level_weights
EVENT_TYPE_WEIGHTS = TEMPERATURE_METHODOLOGY.event_type_weights
TAU = TEMPERATURE_METHODOLOGY.time_decay_tau_seconds


@dataclass(frozen=True, slots=True)
class RriPoint:
    country_code: str
    time: datetime
    score: float
    structural: float | None
    media: float | None
    boost: float | None
    version: str
    article_count: int | None
    gdelt_volume: float | None
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class TemperatureArticleInput:
    article_id: int
    event_key: str | None
    sentiment: float
    published_at: datetime
    source_weight: float | None
    event_type: str | None
    action_level: int | None
    reprint_count: int


def _round(value: float, digits: int = 6) -> float:
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


def _weights(point: RriPoint) -> tuple[float, float]:
    weights = point.details.get("weights", {}) if isinstance(point.details, Mapping) else {}
    if not isinstance(weights, Mapping):
        weights = {}
    structural_weight = weights.get("structural")
    media_weight = weights.get("media")
    return (
        float(structural_weight) if structural_weight is not None else 1.0,
        float(media_weight) if media_weight is not None else 1.0,
    )


def _component_contributions(point: RriPoint) -> dict[str, float]:
    if point.media is None:
        return {
            "structural": float(point.structural or 0),
            "media": 0.0,
            "boost": 0.0,
        }
    structural_weight, media_weight = _weights(point)
    return {
        "structural": float(point.structural or 0) * structural_weight,
        "media": float(point.media or 0) * media_weight,
        "boost": float(point.boost or 0),
    }


def _calculation_adjustment(
    point: RriPoint,
    contributions: Mapping[str, float],
) -> float:
    formula_total = sum(contributions.values())
    details = point.details if isinstance(point.details, Mapping) else {}
    if details.get("bound_rule"):
        return float(point.score) - formula_total
    clamped_total = max(-100.0, min(100.0, formula_total))
    return clamped_total - formula_total


def decompose_rri_shift(previous: RriPoint, current: RriPoint) -> dict[str, Any]:
    """Return the exact stored RRI delta and formula-component deltas."""

    if previous.country_code != current.country_code:
        raise ValueError("RRI points must belong to the same country")
    if previous.time >= current.time:
        raise ValueError("previous RRI point must be earlier than current point")
    if previous.version != current.version:
        raise ValueError("RRI points must use the same formula version")

    before = _component_contributions(previous)
    after = _component_contributions(current)
    structural_delta = _round(after["structural"] - before["structural"])
    media_delta = _round(after["media"] - before["media"])
    boost_delta = _round(after["boost"] - before["boost"])
    exact_subtotal = _round(structural_delta + media_delta + boost_delta)
    before_adjustment = _calculation_adjustment(previous, before)
    after_adjustment = _calculation_adjustment(current, after)
    calculation_adjustment_delta = _round(after_adjustment - before_adjustment)
    total_delta = _round(current.score - previous.score)
    rounding_residual = _round(
        total_delta - exact_subtotal - calculation_adjustment_delta
    )

    return {
        "from_value": float(previous.score),
        "to_value": float(current.score),
        "total_delta": total_delta,
        "structural_delta": structural_delta,
        "media_delta": media_delta,
        "boost_delta": boost_delta,
        "exact_subtotal": exact_subtotal,
        "calculation_adjustment_delta": calculation_adjustment_delta,
        "rounding_residual": rounding_residual,
        "from_time": previous.time.isoformat(),
        "to_time": current.time.isoformat(),
        "rri_version": current.version,
        "weights": {
            "from": dict(zip(("structural", "media"), _weights(previous))),
            "to": dict(zip(("structural", "media"), _weights(current))),
        },
        "calculation_adjustments": {
            "from": _round(before_adjustment),
            "to": _round(after_adjustment),
            "from_rule": previous.details.get("bound_rule"),
            "to_rule": current.details.get("bound_rule"),
        },
        "input_counts": {
            "from_articles": previous.article_count,
            "to_articles": current.article_count,
            "from_gdelt_volume": previous.gdelt_volume,
            "to_gdelt_volume": current.gdelt_volume,
        },
    }


def _normalized_event_key(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value)
    if len(raw) < TEMPERATURE_METHODOLOGY.event_key_min_length:
        return None
    return raw.lower().strip()


def _temperature_from_articles(
    articles: Sequence[TemperatureArticleInput],
    *,
    at: datetime,
) -> float | None:
    clusters: dict[str, list[TemperatureArticleInput]] = {}
    unclustered: list[TemperatureArticleInput] = []
    for article in articles:
        event_key = _normalized_event_key(article.event_key)
        if event_key is not None:
            clusters.setdefault(event_key, []).append(article)
        else:
            unclustered.append(article)

    weighted: list[tuple[TemperatureArticleInput, float]] = []
    for cluster in clusters.values():
        cluster = sorted(
            cluster,
            key=lambda item: float(
                item.source_weight or TEMPERATURE_METHODOLOGY.source_weight_default
            ),
            reverse=True,
        )
        weighted.extend(
            (
                article,
                TEMPERATURE_METHODOLOGY.cluster_diminishing_base ** index,
            )
            for index, article in enumerate(cluster)
        )
    weighted.extend(
        (article, TEMPERATURE_METHODOLOGY.unclustered_weight)
        for article in unclustered
    )

    numerator = 0.0
    denominator = 0.0
    for article, cluster_decay in weighted:
        published_at = article.published_at
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=at.tzinfo)
        age_seconds = (at - published_at).total_seconds()
        time_decay = math.exp(-age_seconds / TAU)
        source_weight = float(
            article.source_weight or TEMPERATURE_METHODOLOGY.source_weight_default
        )
        event_weight = float(
            EVENT_TYPE_WEIGHTS.get(article.event_type, EVENT_TYPE_WEIGHTS[None])
        )
        importance = (
            TEMPERATURE_METHODOLOGY.reprint_importance_base
            + math.log1p(article.reprint_count)
        )
        action_weight = float(
            ACTION_MULTIPLIERS.get(
                article.action_level or 1,
                ACTION_MULTIPLIERS[1],
            )
        )
        weight = (
            source_weight
            * event_weight
            * time_decay
            * importance
            * action_weight
            * cluster_decay
        )
        numerator += float(article.sentiment) * weight
        denominator += abs(weight)
    if denominator == 0:
        return None
    return numerator / denominator * TEMPERATURE_METHODOLOGY.normalization_factor


def estimate_event_contribution(
    *,
    event_key: str | None,
    articles: Sequence[TemperatureArticleInput],
    at: datetime,
    media_weight: float,
) -> dict[str, Any]:
    """Estimate an event cluster's RRI effect by removing its article inputs."""

    input_ids = sorted(article.article_id for article in articles)
    normalized_target = _normalized_event_key(event_key)
    base = {
        "label": "estimated",
        "method": COUNTERFACTUAL_METHOD,
        "event_key": event_key,
        "input_article_ids": input_ids,
    }

    def audit_fields(
        removed_ids: Sequence[int],
        *,
        cluster_present: bool,
    ) -> dict[str, Any]:
        removed_ids = list(removed_ids)
        return {
            "why_included": (
                "event_cluster_present_in_reconstructed_temperature_window"
                if cluster_present
                else "counterfactual_requested_for_event_cluster"
            ),
            "relevance_score": (
                _round(len(removed_ids) / len(input_ids), 3)
                if cluster_present and input_ids
                else None
            ),
            "confidence": 0.7 if cluster_present else None,
            "evidence": {
                "input_article_ids": input_ids,
                "removed_article_ids": removed_ids,
                "relevance_basis": "share_of_temperature_article_inputs",
            },
        }

    if not articles:
        return {
            "status": "omitted",
            **base,
            "removed_article_ids": [],
            **audit_fields([], cluster_present=False),
            "reason": "article_inputs_missing",
        }
    removed = [
        article
        for article in articles
        if _normalized_event_key(article.event_key) == normalized_target
    ]
    if normalized_target is None or not removed:
        return {
            "status": "omitted",
            **base,
            "removed_article_ids": [],
            **audit_fields([], cluster_present=False),
            "reason": "event_cluster_not_found",
        }
    remaining = [article for article in articles if article not in removed]
    actual_temperature = _temperature_from_articles(articles, at=at)
    counterfactual_temperature = _temperature_from_articles(remaining, at=at)
    removed_ids = sorted(article.article_id for article in removed)
    if actual_temperature is None or counterfactual_temperature is None:
        return {
            "status": "omitted",
            **base,
            "removed_article_ids": removed_ids,
            **audit_fields(removed_ids, cluster_present=True),
            "reason": "counterfactual_inputs_insufficient",
        }
    return {
        "status": "estimated",
        **base,
        "removed_article_ids": removed_ids,
        **audit_fields(removed_ids, cluster_present=True),
        "estimated_delta": _round(
            (actual_temperature - counterfactual_temperature) * float(media_weight),
            2,
        ),
        "actual_media_estimate": _round(actual_temperature, 2),
        "counterfactual_media_estimate": _round(counterfactual_temperature, 2),
    }


def build_explanation(
    previous: RriPoint,
    current: RriPoint,
    *,
    estimated_contributions: Iterable[Mapping[str, Any]] = (),
    context: Iterable[Mapping[str, Any]] = (),
    evidence_completeness: str = "complete",
    limitations: Iterable[str] = ("contextual_proximity_is_not_causation",),
) -> dict[str, Any]:
    """Assemble separated exact, estimated, and contextual explanation layers."""

    context_items = [dict(item) for item in context]
    return {
        "country_code": current.country_code,
        "from_time": previous.time.isoformat(),
        "to_time": current.time.isoformat(),
        "rri_version": current.version,
        "exact_changes": decompose_rri_shift(previous, current),
        "estimated_contributions": [dict(item) for item in estimated_contributions],
        "context": context_items,
        "related_story_ids": sorted({
            int(item["id"])
            for item in context_items
            if item.get("scope") == "story" and isinstance(item.get("id"), int)
        }),
        "related_signal_ids": sorted({
            int(item["id"])
            for item in context_items
            if item.get("scope") == "signal" and isinstance(item.get("id"), int)
        }),
        "evidence_completeness": evidence_completeness,
        "limitations": list(limitations),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(timezone.utc).isoformat()
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(value)
    raise TypeError(f"Unsupported hash input: {type(value).__name__}")


def compute_explanation_input_hash(
    *,
    country_code: str,
    from_time: datetime,
    to_time: datetime,
    rri_version: str,
    inputs: Mapping[str, Any],
) -> str:
    """Bind an explanation cache entry to request identity and persisted inputs."""

    if (
        from_time.tzinfo is None
        or from_time.utcoffset() is None
        or to_time.tzinfo is None
        or to_time.utcoffset() is None
    ):
        raise ValueError("explanation cache timestamps must include a timezone")
    canonical_from = from_time.astimezone(timezone.utc)
    canonical_to = to_time.astimezone(timezone.utc)
    encoded = json.dumps(
        {
            "country_code": country_code.upper(),
            "from_time": canonical_from.isoformat(),
            "to_time": canonical_to.isoformat(),
            "rri_version": rri_version,
            "inputs": inputs,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    mapping = getattr(row, "_mapping", row if isinstance(row, Mapping) else {})
    return mapping.get(name, default)


def _json_value(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if value is not None else default


def _rri_point_from_row(row: Any) -> RriPoint:
    details = _json_value(_row_value(row, "details"), {})
    return RriPoint(
        country_code=str(_row_value(row, "country_code")).strip().upper(),
        time=_row_value(row, "time"),
        score=float(_row_value(row, "score")),
        structural=(
            float(_row_value(row, "structural"))
            if _row_value(row, "structural") is not None
            else None
        ),
        media=(
            float(_row_value(row, "media"))
            if _row_value(row, "media") is not None
            else None
        ),
        boost=(
            float(_row_value(row, "boost"))
            if _row_value(row, "boost") is not None
            else None
        ),
        version=str(_row_value(row, "version")),
        article_count=_row_value(row, "article_count"),
        gdelt_volume=(
            float(_row_value(row, "gdelt_volume"))
            if _row_value(row, "gdelt_volume") is not None
            else None
        ),
        details=details if isinstance(details, Mapping) else {},
    )


def _load_rri_point(
    session: Any,
    *,
    country_code: str,
    target_time: datetime,
    rri_version: str,
) -> RriPoint | None:
    row = session.execute(text("""
        SELECT country_code, time, score, structural, media, boost, version,
               article_count, gdelt_volume, details
        FROM ru_index
        WHERE country_code = :country_code
          AND version = :rri_version
          AND time <= :target_time
        ORDER BY time DESC
        LIMIT 1
    """), {
        "country_code": country_code,
        "target_time": target_time,
        "rri_version": rri_version,
    }).fetchone()
    return _rri_point_from_row(row) if row else None


def _load_temperature_articles(
    session: Any,
    *,
    country_code: str,
    to_time: datetime,
) -> list[TemperatureArticleInput]:
    window_start = to_time - timedelta(days=TEMPERATURE_METHODOLOGY.window_days)
    rows = session.execute(text("""
        SELECT ar.id AS article_id, a.event_key, a.sentiment, ar.published_at,
               s.weight AS source_weight, a.event_type, a.action_level,
               COALESCE(ar.reprint_count, 0) AS reprint_count
        FROM analysis a
        JOIN articles ar ON ar.id = a.article_id
        JOIN article_country_facts s ON s.article_id = ar.id
        WHERE s.country_code = :country_code
          AND a.is_relevant = TRUE
          AND a.sentiment IS NOT NULL
          AND ar.is_backfill = FALSE
          AND ar.published_at > :window_start
          AND ar.published_at <= :to_time
        ORDER BY ar.published_at, ar.id
    """), {
        "country_code": country_code,
        "window_start": window_start,
        "to_time": to_time,
    }).fetchall()
    return [
        TemperatureArticleInput(
            article_id=int(_row_value(row, "article_id")),
            event_key=_row_value(row, "event_key"),
            sentiment=float(_row_value(row, "sentiment")),
            published_at=_row_value(row, "published_at"),
            source_weight=(
                float(_row_value(row, "source_weight"))
                if _row_value(row, "source_weight") is not None
                else None
            ),
            event_type=_row_value(row, "event_type"),
            action_level=_row_value(row, "action_level"),
            reprint_count=int(_row_value(row, "reprint_count", 0) or 0),
        )
        for row in rows
    ]


def _load_context(
    session: Any,
    *,
    country_code: str,
    from_time: datetime,
    to_time: datetime,
) -> list[dict[str, Any]]:
    rows = session.execute(text("""
        SELECT 'article' AS context_scope, ar.id::bigint AS context_id,
               ar.title AS label, ar.url, ar.published_at AS occurred_at,
               1.0::numeric AS relevance_score,
               COALESCE(a.sentiment_confidence, 0.5) AS confidence,
               jsonb_build_object('published_at', ar.published_at) AS evidence
        FROM articles ar
        JOIN article_country_facts s ON s.article_id = ar.id
        LEFT JOIN analysis a ON a.article_id = ar.id
        WHERE s.country_code = :country_code
          AND ar.published_at >= :from_time AND ar.published_at <= :to_time
        UNION ALL
        SELECT 'story' AS context_scope, st.id AS context_id,
               st.title_ru AS label, NULL::text AS url, st.last_seen AS occurred_at,
               COALESCE(st.clustering_confidence, 0) AS relevance_score,
               COALESCE(st.clustering_confidence, 0) AS confidence,
               jsonb_build_object('lifecycle', st.lifecycle) AS evidence
        FROM stories st
        JOIN story_countries sc ON sc.story_id = st.id
        WHERE sc.country_code = :country_code
          AND st.last_seen >= :from_time AND st.first_seen <= :to_time
        UNION ALL
        SELECT 'signal' AS context_scope, sg.id::bigint AS context_id,
               sg.title AS label, NULL::text AS url, sg.created_at AS occurred_at,
               COALESCE(sg.confidence, 0) AS relevance_score,
               COALESCE(sg.confidence, 0) AS confidence,
               COALESCE(sg.payload, '{}'::jsonb) AS evidence
        FROM signals sg
        WHERE sg.country_code = :country_code
          AND sg.created_at >= :from_time AND sg.created_at <= :to_time
        ORDER BY occurred_at DESC NULLS LAST, context_scope, context_id
        LIMIT 100
    """), {
        "country_code": country_code,
        "from_time": from_time,
        "to_time": to_time,
    }).fetchall()
    return [{
        "scope": str(_row_value(row, "context_scope")),
        "id": int(_row_value(row, "context_id")),
        "label": _row_value(row, "label"),
        "url": _row_value(row, "url"),
        "occurred_at": (
            _row_value(row, "occurred_at").isoformat()
            if isinstance(_row_value(row, "occurred_at"), datetime)
            else _row_value(row, "occurred_at")
        ),
        "why_included": "published_or_active_in_selected_window",
        "relevance_score": float(_row_value(row, "relevance_score", 0) or 0),
        "confidence": float(_row_value(row, "confidence", 0) or 0),
        "evidence": _json_value(_row_value(row, "evidence"), {}),
    } for row in rows]


def _point_hash_payload(point: RriPoint) -> dict[str, Any]:
    return {
        "country_code": point.country_code,
        "time": point.time,
        "score": point.score,
        "structural": point.structural,
        "media": point.media,
        "boost": point.boost,
        "version": point.version,
        "article_count": point.article_count,
        "gdelt_volume": point.gdelt_volume,
        "details": point.details,
    }


def _article_hash_payload(article: TemperatureArticleInput) -> dict[str, Any]:
    effective_source_weight = float(
        article.source_weight or TEMPERATURE_METHODOLOGY.source_weight_default
    )
    effective_action_level = article.action_level or 1
    return {
        "article_id": article.article_id,
        "event_key": _normalized_event_key(article.event_key),
        "sentiment": float(article.sentiment),
        "published_at": article.published_at,
        "source_weight": (
            float(article.source_weight)
            if article.source_weight is not None
            else None
        ),
        "effective_source_weight": effective_source_weight,
        "event_type": article.event_type,
        "event_type_weight": float(
            EVENT_TYPE_WEIGHTS.get(article.event_type, EVENT_TYPE_WEIGHTS[None])
        ),
        "action_level": article.action_level,
        "action_level_weight": int(
            ACTION_MULTIPLIERS.get(
                effective_action_level,
                ACTION_MULTIPLIERS[1],
            )
        ),
        "reprint_count": article.reprint_count,
    }


def _cached_payload(
    row: Any,
    *,
    country_code: str,
    from_time: datetime,
    to_time: datetime,
    rri_version: str,
    input_hash: str,
) -> dict[str, Any]:
    context = _json_value(_row_value(row, "context"), [])
    context = context if isinstance(context, list) else []
    return {
        "country_code": country_code,
        "from_time": from_time.isoformat(),
        "to_time": to_time.isoformat(),
        "rri_version": rri_version,
        "exact_changes": _json_value(_row_value(row, "exact_changes"), {}),
        "estimated_contributions": _json_value(
            _row_value(row, "estimated_contributions"), []
        ),
        "context": context,
        "related_story_ids": sorted({
            int(item["id"])
            for item in context
            if isinstance(item, Mapping)
            and item.get("scope") == "story"
            and isinstance(item.get("id"), int)
        }),
        "related_signal_ids": sorted({
            int(item["id"])
            for item in context
            if isinstance(item, Mapping)
            and item.get("scope") == "signal"
            and isinstance(item.get("id"), int)
        }),
        "evidence_completeness": _row_value(
            row, "evidence_completeness", "partial"
        ),
        "limitations": _json_value(_row_value(row, "limitations"), []),
        "cache": {"status": "hit", "input_hash": input_hash},
    }


def load_explanation_from_session(
    session: Any,
    *,
    country_code: str,
    from_time: datetime,
    to_time: datetime,
    rri_version: str,
) -> dict[str, Any]:
    """Read persisted inputs/cache and deterministically explain an RRI interval.

    This function deliberately performs no INSERT, UPDATE, generation, LLM, or
    embedding work, so it is safe to call from a public GET endpoint.
    """

    country_code = country_code.strip().upper()
    if (
        from_time.tzinfo is None
        or from_time.utcoffset() is None
        or to_time.tzinfo is None
        or to_time.utcoffset() is None
    ):
        raise ValueError("explanation timestamps must include a timezone")
    from_time = from_time.astimezone(timezone.utc)
    to_time = to_time.astimezone(timezone.utc)
    previous = _load_rri_point(
        session,
        country_code=country_code,
        target_time=from_time,
        rri_version=rri_version,
    )
    current = _load_rri_point(
        session,
        country_code=country_code,
        target_time=to_time,
        rri_version=rri_version,
    )
    if not previous or not current:
        raise LookupError("stored RRI points are unavailable for the selected interval")
    if previous.time >= current.time:
        raise LookupError("the selected interval does not contain two distinct RRI points")

    articles = _load_temperature_articles(
        session,
        country_code=country_code,
        to_time=current.time,
    )
    context = _load_context(
        session,
        country_code=country_code,
        from_time=from_time,
        to_time=to_time,
    )
    hash_inputs = {
        "previous": _point_hash_payload(previous),
        "current": _point_hash_payload(current),
        "counterfactual_method": COUNTERFACTUAL_METHOD,
        "thermometer_methodology_version": TEMPERATURE_METHODOLOGY.version,
        "articles": [
            _article_hash_payload(article)
            for article in sorted(articles, key=lambda item: item.article_id)
        ],
        "context": context,
    }
    input_hash = compute_explanation_input_hash(
        country_code=country_code,
        from_time=from_time,
        to_time=to_time,
        rri_version=rri_version,
        inputs=hash_inputs,
    )
    cache_params = {
        "country_code": country_code,
        "from_time": from_time,
        "to_time": to_time,
        "rri_version": rri_version,
        "input_hash": input_hash,
    }
    cached = session.execute(text("""
        SELECT exact_changes, estimated_contributions, context,
               evidence_completeness, limitations
        FROM index_change_explanations
        WHERE country_code = :country_code
          AND from_time = :from_time
          AND to_time = :to_time
          AND rri_version = :rri_version
          AND input_hash = :input_hash
        LIMIT 1
    """), cache_params).fetchone()
    if cached:
        return _cached_payload(cached, **cache_params)

    media_details = (
        current.details.get("media", {})
        if isinstance(current.details, Mapping)
        else {}
    )
    weights = (
        current.details.get("weights", {})
        if isinstance(current.details, Mapping)
        else {}
    )
    media_weight = (
        float(weights.get("media", 0)) if isinstance(weights, Mapping) else 0.0
    )
    uses_temperature_media = (
        isinstance(media_details, Mapping)
        and media_details.get("source") == "temperature"
    )
    estimates: list[dict[str, Any]] = []
    limitations = ["contextual_proximity_is_not_causation"]
    if not uses_temperature_media:
        input_article_ids = sorted(article.article_id for article in articles)
        estimates.append({
            "status": "omitted",
            "label": "estimated",
            "method": COUNTERFACTUAL_METHOD,
            "event_key": None,
            "input_article_ids": input_article_ids,
            "removed_article_ids": [],
            "why_included": "counterfactual_status_disclosed_for_selected_rri_window",
            "relevance_score": None,
            "confidence": None,
            "evidence": {
                "input_article_ids": input_article_ids,
                "removed_article_ids": [],
                "relevance_basis": "share_of_temperature_article_inputs",
            },
            "reason": "media_component_not_article_temperature",
        })
        limitations.append("counterfactual_requires_article_temperature_media")
    else:
        limitations.append(
            "counterfactual_reconstructs_media_window_not_historical_input_snapshot"
        )
    if uses_temperature_media and not articles:
        estimates.append(estimate_event_contribution(
            event_key=None,
            articles=[],
            at=current.time,
            media_weight=media_weight,
        ))
        limitations.append("counterfactual_article_inputs_missing")
    elif uses_temperature_media:
        event_keys = sorted({
            key
            for article in articles
            if (key := _normalized_event_key(article.event_key)) is not None
        })
        estimates.extend(
            estimate_event_contribution(
                event_key=event_key,
                articles=articles,
                at=current.time,
                media_weight=media_weight,
            )
            for event_key in event_keys
        )
        if not event_keys:
            limitations.append("counterfactual_event_clusters_unavailable")

    completeness = (
        "complete"
        if estimates and all(item.get("status") == "estimated" for item in estimates)
        else "partial"
    )
    payload = build_explanation(
        previous,
        current,
        estimated_contributions=estimates,
        context=context,
        evidence_completeness=completeness,
        limitations=limitations,
    )
    payload["from_time"] = from_time.isoformat()
    payload["to_time"] = to_time.isoformat()
    payload["cache"] = {"status": "miss", "input_hash": input_hash}
    return payload


def load_index_explanation(
    *,
    country_code: str,
    from_time: datetime,
    to_time: datetime,
    rri_version: str,
) -> dict[str, Any]:
    """Open a read transaction and load one persisted explanation response."""

    from src.db import get_session

    with get_session() as session:
        return load_explanation_from_session(
            session,
            country_code=country_code,
            from_time=from_time,
            to_time=to_time,
            rri_version=rri_version,
        )


def _payload_timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} is not a valid timestamp") from exc
    else:
        raise ValueError(f"{field} is not a valid timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def persist_explanation_cache(session: Any, payload: Mapping[str, Any]) -> None:
    """Persist a deterministic explanation from an explicit background warmup."""

    cache = payload.get("cache", {})
    input_hash = cache.get("input_hash") if isinstance(cache, Mapping) else None
    if not isinstance(input_hash, str) or len(input_hash) != 64:
        raise ValueError("cache input_hash must be a SHA-256 hex digest")
    params = {
        "country_code": str(payload["country_code"]).strip().upper(),
        "from_time": _payload_timestamp(payload.get("from_time"), "from_time"),
        "to_time": _payload_timestamp(payload.get("to_time"), "to_time"),
        "rri_version": str(payload["rri_version"]),
        "input_hash": input_hash,
        "exact_changes": json.dumps(
            payload.get("exact_changes", {}),
            ensure_ascii=False,
            default=_json_default,
        ),
        "estimated_contributions": json.dumps(
            payload.get("estimated_contributions", []),
            ensure_ascii=False,
            default=_json_default,
        ),
        "context": json.dumps(
            payload.get("context", []),
            ensure_ascii=False,
            default=_json_default,
        ),
        "evidence_completeness": str(
            payload.get("evidence_completeness", "partial")
        ),
        "limitations": json.dumps(
            payload.get("limitations", []),
            ensure_ascii=False,
            default=_json_default,
        ),
    }
    if params["from_time"] >= params["to_time"]:
        raise ValueError("from_time must be earlier than to_time")
    session.execute(text("""
        INSERT INTO index_change_explanations (
            country_code, from_time, to_time, rri_version, input_hash,
            exact_changes, estimated_contributions, context,
            evidence_completeness, limitations
        ) VALUES (
            :country_code, :from_time, :to_time, :rri_version, :input_hash,
            CAST(:exact_changes AS jsonb),
            CAST(:estimated_contributions AS jsonb),
            CAST(:context AS jsonb),
            :evidence_completeness,
            CAST(:limitations AS jsonb)
        )
        ON CONFLICT (country_code, from_time, to_time, rri_version, input_hash)
        DO UPDATE SET
            exact_changes = EXCLUDED.exact_changes,
            estimated_contributions = EXCLUDED.estimated_contributions,
            context = EXCLUDED.context,
            evidence_completeness = EXCLUDED.evidence_completeness,
            limitations = EXCLUDED.limitations,
            updated_at = NOW()
    """), params)
