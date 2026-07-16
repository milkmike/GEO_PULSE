#!/usr/bin/env python3
"""Safely backfill persisted data for search, stories, signals and explanations.

The command is a dry-run unless ``--apply`` is supplied.  Apply mode commits in
bounded transactions and records an atomic JSON checkpoint after each committed
batch.  No stage calls an LLM or embedding provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from sqlalchemy import text

from src.db import get_session, wait_for_db
from src.engine.explanations import (
    load_explanation_from_session,
    persist_explanation_cache,
)
from src.knowledge import KnowledgeBackfillService
from src.stories import (
    cluster_story_candidates,
    derive_reactivation_pairs,
    fetch_story_candidates,
    persist_story_cluster,
    refresh_story_lifecycles,
)


logger = logging.getLogger("backfill-investigation-data")

CHECKPOINT_VERSION = 1
MAX_BATCH_SIZE = 1000
DEFAULT_BATCH_SIZE = 100
DEFAULT_CHECKPOINT = Path("backups/investigation-backfill-checkpoint.json")
STAGE_NAMES = (
    "knowledge_mentions",
    "story_membership",
    "signal_evidence",
    "signal_evidence_metadata",
    "explanation_warmup",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(name, default)
    return getattr(row, name, default)


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None
    return None


@dataclass(frozen=True)
class StageReport:
    eligible: int
    processed: int
    cursor: Any
    done: bool
    skipped: int = 0
    batches: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


class JsonCheckpointStore:
    """Small durable checkpoint with atomic replace and fsync."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": CHECKPOINT_VERSION, "stages": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid checkpoint {self.path}: {exc}") from exc
        if payload.get("version") != CHECKPOINT_VERSION:
            raise ValueError(
                f"unsupported checkpoint version: {payload.get('version')!r}"
            )
        if not isinstance(payload.get("stages"), dict):
            raise ValueError("checkpoint stages must be an object")
        return payload

    def save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_default,
        )
        fd, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


@dataclass
class StageContext:
    apply: bool
    batch_size: int
    session_factory: Callable[[], Any]
    checkpoint: JsonCheckpointStore
    checkpoint_state: dict[str, Any]
    stage_name: str

    def save_cursor(self, cursor: Any, *, done: bool) -> None:
        """Persist a cursor only after the caller's transaction has committed."""

        if not self.apply:
            return
        stages = self.checkpoint_state.setdefault("stages", {})
        stages[self.stage_name] = {
            "cursor": cursor,
            "done": bool(done),
            "updated_at": _utc_now().isoformat(),
        }
        self.checkpoint_state["updated_at"] = _utc_now().isoformat()
        self.checkpoint.save(self.checkpoint_state)

    @property
    def saved_stage(self) -> Mapping[str, Any]:
        return self.checkpoint_state.get("stages", {}).get(self.stage_name, {})


StageRunner = Callable[[StageContext, Any], StageReport]


def _scalar(session: Any, statement: str, params: Mapping[str, Any]) -> int:
    result = session.execute(text(statement), dict(params))
    if hasattr(result, "scalar_one"):
        return int(result.scalar_one())
    row = result.fetchone()
    return int(_value(row, "count", _value(row, "count_1", 0)) or 0)


def backfill_knowledge_mentions(context: StageContext, cursor: Any) -> StageReport:
    after_analysis_id = int(cursor or 0)
    with context.session_factory() as session:
        eligible = _scalar(
            session,
            """
            SELECT COUNT(*)
            FROM analysis
            WHERE id > :after_analysis_id AND entities IS NOT NULL
            """,
            {"after_analysis_id": after_analysis_id},
        )
    if not context.apply:
        return StageReport(eligible, 0, cursor, False)
    if context.saved_stage.get("done"):
        return StageReport(0, 0, cursor, True)

    service = KnowledgeBackfillService()
    with context.session_factory() as session:
        entity_count = service.seed_registry(session)

    processed = 0
    mentions = 0
    batches = 0
    done = False
    while not done:
        with context.session_factory() as session:
            batch = service.backfill_batch(
                session,
                after_analysis_id=after_analysis_id,
                batch_size=context.batch_size,
            )
        batches += 1
        processed += batch.analyses_seen
        mentions += batch.mentions_upserted
        after_analysis_id = batch.last_analysis_id
        done = bool(batch.done or batch.analyses_seen == 0)
        context.save_cursor(after_analysis_id, done=done)

    return StageReport(
        eligible=eligible,
        processed=processed,
        cursor=after_analysis_id,
        done=True,
        batches=batches,
        details={"entities": entity_count, "mentions": mentions},
    )


def story_candidate_snapshot_hash(candidates: list[Any]) -> str:
    """Fingerprint every clustering input used by a frozen story plan."""

    payload = []
    for candidate in sorted(candidates, key=lambda item: item.thread_id):
        payload.append({
            "thread_id": int(candidate.thread_id),
            "country_code": candidate.country_code,
            "event_key": candidate.event_key,
            "title": candidate.title,
            "article_ids": list(candidate.article_ids),
            "entities": sorted(candidate.entities),
            "topics": sorted(candidate.topics),
            "sources": sorted(candidate.sources),
            "first_seen": candidate.first_seen,
            "last_seen": candidate.last_seen,
            "highest_action_level": candidate.highest_action_level,
            "articles": [
                {
                    "article_id": article.article_id,
                    "title": article.title,
                    "published_at": article.published_at,
                    "sentiment": article.sentiment,
                    "action_level": article.action_level,
                    "event_key": article.event_key,
                    "entity_ids": sorted(article.entity_ids),
                }
                for article in candidate.articles
            ],
        })
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_story_candidates(
    session: Any,
    snapshot_max_thread_id: int | None = None,
) -> tuple[list[Any], frozenset[tuple[int, int]], str, int]:
    candidates = fetch_story_candidates(session)
    if snapshot_max_thread_id is None:
        snapshot_max_thread_id = max(
            (int(item.thread_id) for item in candidates),
            default=0,
        )
    candidates = [
        item for item in candidates
        if int(item.thread_id) <= snapshot_max_thread_id
    ]
    reactivation_pairs = derive_reactivation_pairs(session, candidates)
    return (
        candidates,
        reactivation_pairs,
        story_candidate_snapshot_hash(candidates),
        snapshot_max_thread_id,
    )


def _frozen_cluster_plan(clusters: list[Any]) -> list[list[int]]:
    plan = [
        sorted(int(item.thread_id) for item in cluster)
        for cluster in clusters
    ]
    return sorted(plan)


def _clusters_from_plan(
    candidates: list[Any],
    plan: list[list[int]],
) -> list[tuple[Any, ...]]:
    by_thread_id = {int(item.thread_id): item for item in candidates}
    clusters = []
    for thread_ids in plan:
        missing = [thread_id for thread_id in thread_ids if thread_id not in by_thread_id]
        if missing:
            raise RuntimeError(
                f"story candidate snapshot changed; missing threads {missing}"
            )
        clusters.append(tuple(by_thread_id[thread_id] for thread_id in thread_ids))
    return clusters


def backfill_story_membership(context: StageContext, cursor: Any) -> StageReport:
    if context.apply and context.saved_stage.get("done"):
        return StageReport(0, 0, cursor, True)

    frozen = dict(cursor) if isinstance(cursor, Mapping) else {}
    snapshot_bound = frozen.get("snapshot_max_thread_id")
    with context.session_factory() as session:
        candidates, derived_pairs, snapshot_hash, snapshot_bound = (
            _load_story_candidates(session, snapshot_bound)
        )

    if frozen:
        if frozen.get("snapshot_hash") != snapshot_hash:
            raise RuntimeError(
                "story candidate snapshot changed; restart with a new checkpoint"
            )
        plan = frozen.get("cluster_plan")
        if not isinstance(plan, list):
            raise ValueError("story checkpoint cluster_plan must be an array")
        reactivation_pairs = frozenset(
            tuple(sorted((int(pair[0]), int(pair[1]))))
            for pair in frozen.get("reactivation_pairs", [])
            if isinstance(pair, list) and len(pair) == 2
        )
        next_cluster_index = int(frozen.get("next_cluster_index", 0))
    else:
        clusters = cluster_story_candidates(
            candidates,
            reactivation_pairs=derived_pairs,
        )
        plan = _frozen_cluster_plan(clusters)
        reactivation_pairs = derived_pairs
        next_cluster_index = 0
        frozen = {
            "snapshot_max_thread_id": snapshot_bound,
            "snapshot_hash": snapshot_hash,
            "cluster_plan": plan,
            "reactivation_pairs": [list(pair) for pair in sorted(reactivation_pairs)],
            "next_cluster_index": 0,
        }
        if context.apply:
            context.save_cursor(frozen, done=False)

    clusters = _clusters_from_plan(candidates, plan)
    if not 0 <= next_cluster_index <= len(clusters):
        raise ValueError("story checkpoint next_cluster_index is out of bounds")
    pending = clusters[next_cluster_index:]
    if not context.apply:
        return StageReport(
            eligible=len(pending),
            processed=0,
            cursor=cursor,
            done=False,
            details={
                "candidate_clusters": len(clusters),
                "planned_memberships": sum(
                    len(item.article_ids)
                    for cluster in pending
                    for item in cluster
                ),
                "snapshot_max_thread_id": snapshot_bound,
                "snapshot_hash": snapshot_hash,
            },
        )

    processed = 0
    memberships = 0
    batches = 0
    for index in range(0, len(pending), context.batch_size):
        chunk = pending[index:index + context.batch_size]
        with context.session_factory() as session:
            for cluster in chunk:
                _, count = persist_story_cluster(
                    session,
                    cluster,
                    summarizer=None,
                    now=_utc_now(),
                    reactivation_pairs=reactivation_pairs,
                )
                memberships += count
        processed += len(chunk)
        batches += 1
        next_cluster_index += len(chunk)
        frozen["next_cluster_index"] = next_cluster_index
        context.save_cursor(frozen, done=False)

    with context.session_factory() as session:
        refresh_story_lifecycles(session, now=_utc_now())
    context.save_cursor(frozen, done=True)
    return StageReport(
        eligible=len(pending),
        processed=processed,
        cursor=frozen,
        done=True,
        batches=batches,
        details={"memberships": memberships},
    )


_SIGNAL_THRESHOLDS: dict[str, dict[str, Any]] = {
    "tier_convergence": {"minimum_distinct_tiers": 3},
    "official_silence": {
        "minimum_loud_articles": 3,
        "maximum_quiet_articles": 0,
        "minimum_silence_hours": 6,
    },
    "velocity_spike": {
        "minimum_articles_24h": 5,
        "minimum_baseline_ratio": 1.5,
    },
    "tone_shift": {
        "absolute_z_score_min": 1.6,
        "standard_deviation_floor": 0.3,
    },
    "volume_surge": {
        "minimum_share_ratio": 2.0,
        "minimum_daily_volume": 10,
    },
    "index_shift": {
        "absolute_delta_min": 7.0,
        "absolute_delta_sanity_max": 18.0,
    },
    "notable_event": {"minimum_action_level": 4},
    "fx_move": {"absolute_daily_change_percent_min": 2.0},
    "sanctions_escalation": {"minimum_new_targets": 25},
}
SIGNAL_EVIDENCE_METADATA_VERSION = 1


_MISSING_RECONSTRUCTED_METADATA = """
    se.detector_version = 'legacy-reconstructed-v1'
    AND (
        NOT (COALESCE(se.explanation, '{}'::jsonb) ? 'window_basis')
        OR NOT (COALESCE(se.explanation, '{}'::jsonb) ? 'window_status')
        OR (
            se.detector = ANY(CAST(:known_detectors AS text[]))
            AND NOT (
                COALESCE(se.explanation, '{}'::jsonb)
                ? 'current_rule_reference'
            )
        )
    )
"""


def _signal_metadata_cursor(cursor: Any) -> dict[str, int]:
    if cursor is None:
        return {
            "version": SIGNAL_EVIDENCE_METADATA_VERSION,
            "after_signal_id": 0,
        }
    if not isinstance(cursor, Mapping):
        raise ValueError("signal evidence metadata cursor must be an object")
    version = int(cursor.get("version", 0))
    if version != SIGNAL_EVIDENCE_METADATA_VERSION:
        raise ValueError(f"unsupported signal evidence metadata cursor: {version}")
    after_signal_id = int(cursor.get("after_signal_id", 0))
    if after_signal_id < 0:
        raise ValueError("signal evidence metadata cursor must be non-negative")
    return {"version": version, "after_signal_id": after_signal_id}


def _signal_metadata_additions(detector: str) -> dict[str, Any]:
    additions: dict[str, Any] = {
        "window_basis": "not_persisted",
        "window_status": "unknown",
    }
    if detector in _SIGNAL_THRESHOLDS:
        additions["current_rule_reference"] = dict(_SIGNAL_THRESHOLDS[detector])
    return additions


def _upgrade_reconstructed_signal_metadata(
    session: Any,
    *,
    signal_id: int,
    detector: str,
) -> int:
    """Atomically add only absent metadata keys to one reconstructed row."""

    additions = _signal_metadata_additions(detector)
    result = session.execute(text(f"""
        UPDATE signal_evidence AS se
        SET explanation = COALESCE(se.explanation, '{{}}'::jsonb) || (
            CAST(:additions AS jsonb) - ARRAY(
                SELECT jsonb_object_keys(
                    COALESCE(se.explanation, '{{}}'::jsonb)
                )
            )
        )
        WHERE se.signal_id = :signal_id
          AND {_MISSING_RECONSTRUCTED_METADATA}
    """), {
        "signal_id": signal_id,
        "known_detectors": sorted(_SIGNAL_THRESHOLDS),
        "additions": json.dumps(additions, ensure_ascii=False),
    })
    return max(0, int(getattr(result, "rowcount", 0) or 0))


def reconstruct_signal_evidence(row: Any) -> dict[str, Any] | None:
    """Reconstruct only fields retained on a legacy signal row.

    The result is always marked partial: publication IDs, exact detector window,
    and historical input snapshots were not persisted by the legacy emitter.
    """

    detector = str(_value(row, "signal_type", ""))
    payload = _json_object(_value(row, "payload"))
    if detector not in _SIGNAL_THRESHOLDS or payload is None:
        return None
    baseline: dict[str, Any] = {"status": "not_persisted"}
    if detector == "tone_shift":
        baseline = {
            "type": "historical_tone_distribution",
            "mean": payload.get("mean_90d"),
            "standard_deviation": payload.get("std"),
            "status": "reconstructed_from_signal_payload",
        }
    elif detector == "volume_surge":
        baseline = {
            "type": "historical_coverage_share",
            "share": payload.get("baseline_share"),
            "status": "reconstructed_from_signal_payload",
        }
    elif detector == "velocity_spike":
        baseline = {
            "type": "rolling_daily_average",
            "daily_average": payload.get("baseline_daily"),
            "status": "reconstructed_from_signal_payload",
        }
    elif detector == "sanctions_escalation":
        total = payload.get("target_count")
        delta = payload.get("delta")
        previous = total - delta if isinstance(total, int) and isinstance(delta, int) else None
        baseline = {
            "type": "previous_sanctions_snapshot",
            "previous_target_count": previous,
            "status": "reconstructed_from_signal_payload",
        }
    elif detector in {"tier_convergence", "notable_event"}:
        baseline = {"type": "not_applicable"}

    confidence = float(_value(row, "confidence", 0) or 0)
    return {
        "signal_id": int(_value(row, "id")),
        "detector": detector,
        "detector_version": "legacy-reconstructed-v1",
        "threshold": {},
        "observed": payload,
        "baseline": baseline,
        "window_start": None,
        "window_end": None,
        "article_ids": [],
        "story_ids": [],
        "rri_points": [],
        "confidence": max(0.0, min(1.0, confidence)),
        "completeness": "partial",
        "explanation": {
            "rule": "Восстановлено из сохранённого payload старого сигнала",
            "current_rule_reference": dict(_SIGNAL_THRESHOLDS[detector]),
            "window_basis": "not_persisted",
            "window_status": "unknown",
            "limitations": [
                "Точный исходный снимок, исторический порог, идентификаторы публикаций и окно детектора не сохранялись в старой версии."
            ],
            "evidence_ids": [f"legacy_signal:{int(_value(row, 'id'))}:payload"],
        },
    }


def _insert_reconstructed_signal(session: Any, evidence: Mapping[str, Any]) -> int:
    result = session.execute(text("""
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
        ON CONFLICT (signal_id) DO NOTHING
    """), {
        **evidence,
        "threshold": json.dumps(evidence["threshold"], ensure_ascii=False),
        "observed": json.dumps(evidence["observed"], ensure_ascii=False),
        "baseline": json.dumps(evidence["baseline"], ensure_ascii=False),
        "rri_points": json.dumps(evidence["rri_points"], ensure_ascii=False),
        "explanation": json.dumps(evidence["explanation"], ensure_ascii=False),
    })
    return max(0, int(getattr(result, "rowcount", 0) or 0))


def backfill_signal_evidence(context: StageContext, cursor: Any) -> StageReport:
    after_signal_id = int(cursor or 0)
    with context.session_factory() as session:
        eligible = _scalar(session, """
            SELECT COUNT(*)
            FROM signals s
            LEFT JOIN signal_evidence se ON se.signal_id = s.id
            WHERE s.id > :after_signal_id AND se.signal_id IS NULL
        """, {"after_signal_id": after_signal_id})
    if not context.apply:
        return StageReport(eligible, 0, cursor, False)
    if context.saved_stage.get("done"):
        return StageReport(0, 0, cursor, True)

    processed = 0
    skipped = 0
    batches = 0
    while True:
        with context.session_factory() as session:
            rows = session.execute(text("""
                SELECT s.id, s.signal_type, s.payload, s.confidence, s.created_at
                FROM signals s
                LEFT JOIN signal_evidence se ON se.signal_id = s.id
                WHERE s.id > :after_signal_id AND se.signal_id IS NULL
                ORDER BY s.id
                LIMIT :batch_size
            """), {
                "after_signal_id": after_signal_id,
                "batch_size": context.batch_size,
            }).fetchall()
            for row in rows:
                evidence = reconstruct_signal_evidence(row)
                if evidence is None:
                    skipped += 1
                else:
                    processed += _insert_reconstructed_signal(session, evidence)
        if not rows:
            context.save_cursor(after_signal_id, done=True)
            break
        batches += 1
        after_signal_id = int(_value(rows[-1], "id"))
        context.save_cursor(after_signal_id, done=False)

    return StageReport(
        eligible=eligible,
        processed=processed,
        skipped=skipped,
        cursor=after_signal_id,
        done=True,
        batches=batches,
    )


def backfill_signal_evidence_metadata(
    context: StageContext,
    cursor: Any,
) -> StageReport:
    """Upgrade pre-patch reconstructed explanations in bounded batches."""

    cursor_state = _signal_metadata_cursor(cursor)
    if context.apply and context.saved_stage.get("done"):
        return StageReport(0, 0, cursor_state, True)

    params = {
        "after_signal_id": cursor_state["after_signal_id"],
        "known_detectors": sorted(_SIGNAL_THRESHOLDS),
    }
    with context.session_factory() as session:
        eligible = _scalar(session, f"""
            SELECT COUNT(*)
            FROM signal_evidence se
            WHERE se.signal_id > :after_signal_id
              AND {_MISSING_RECONSTRUCTED_METADATA}
        """, params)
    if not context.apply:
        return StageReport(eligible, 0, cursor_state, False)

    processed = 0
    skipped = 0
    batches = 0
    after_signal_id = cursor_state["after_signal_id"]
    while True:
        with context.session_factory() as session:
            rows = session.execute(text(f"""
                SELECT signal_id, detector, explanation
                FROM signal_evidence se
                WHERE se.signal_id > :after_signal_id
                  AND {_MISSING_RECONSTRUCTED_METADATA}
                ORDER BY signal_id
                LIMIT :batch_size
            """), {
                "after_signal_id": after_signal_id,
                "known_detectors": sorted(_SIGNAL_THRESHOLDS),
                "batch_size": context.batch_size,
            }).fetchall()
            for row in rows:
                changed = _upgrade_reconstructed_signal_metadata(
                    session,
                    signal_id=int(_value(row, "signal_id")),
                    detector=str(_value(row, "detector", "")),
                )
                processed += changed
                skipped += int(changed == 0)
        if not rows:
            cursor_state = {
                "version": SIGNAL_EVIDENCE_METADATA_VERSION,
                "after_signal_id": after_signal_id,
            }
            context.save_cursor(cursor_state, done=True)
            break
        batches += 1
        after_signal_id = int(_value(rows[-1], "signal_id"))
        cursor_state = {
            "version": SIGNAL_EVIDENCE_METADATA_VERSION,
            "after_signal_id": after_signal_id,
        }
        context.save_cursor(cursor_state, done=False)

    return StageReport(
        eligible=eligible,
        processed=processed,
        skipped=skipped,
        cursor=cursor_state,
        done=True,
        batches=batches,
    )


def _warmup_rows(session: Any, cursor: Any, batch_size: int) -> list[Any]:
    params: dict[str, Any] = {"batch_size": batch_size}
    cursor_filter = ""
    if isinstance(cursor, Mapping) and cursor.get("time") and cursor.get("country_code"):
        cursor_filter = """
            AND (point_time > :cursor_time OR
                 (point_time = :cursor_time AND country_code > :cursor_country))
        """
        params.update({
            "cursor_time": datetime.fromisoformat(
                str(cursor["time"]).replace("Z", "+00:00")
            ),
            "cursor_country": str(cursor["country_code"]),
        })
    return session.execute(text(f"""
        WITH ordered AS (
            SELECT country_code, time AS point_time, COALESCE(version, 'v1') AS version,
                   LAG(time) OVER (PARTITION BY country_code, COALESCE(version, 'v1')
                                   ORDER BY time) AS previous_time
            FROM ru_index
        )
        SELECT country_code, point_time, previous_time, version
        FROM ordered
        WHERE previous_time IS NOT NULL
        {cursor_filter}
        ORDER BY point_time, country_code
        LIMIT :batch_size
    """), params).fetchall()


def backfill_explanation_warmup(context: StageContext, cursor: Any) -> StageReport:
    with context.session_factory() as session:
        eligible = _scalar(session, """
            SELECT COUNT(*) FROM (
                SELECT time, LAG(time) OVER (
                    PARTITION BY country_code, COALESCE(version, 'v1') ORDER BY time
                ) AS previous_time
                FROM ru_index
            ) points
            WHERE previous_time IS NOT NULL
        """, {})
    if not context.apply:
        return StageReport(eligible, 0, cursor, False)
    if context.saved_stage.get("done"):
        return StageReport(0, 0, cursor, True)

    processed = 0
    skipped = 0
    batches = 0
    while True:
        with context.session_factory() as session:
            rows = _warmup_rows(session, cursor, context.batch_size)
            for row in rows:
                try:
                    payload = load_explanation_from_session(
                        session,
                        country_code=str(_value(row, "country_code")),
                        from_time=_value(row, "previous_time"),
                        to_time=_value(row, "point_time"),
                        rri_version=str(_value(row, "version", "v1") or "v1"),
                    )
                    if payload.get("cache", {}).get("status") == "miss":
                        persist_explanation_cache(session, payload)
                    processed += 1
                except (LookupError, ValueError) as exc:
                    logger.warning("Skipping explanation warmup row: %s", exc)
                    skipped += 1
        if not rows:
            context.save_cursor(cursor, done=True)
            break
        batches += 1
        last = rows[-1]
        point_time = _value(last, "point_time")
        cursor = {
            "time": point_time.isoformat() if isinstance(point_time, datetime) else str(point_time),
            "country_code": str(_value(last, "country_code")),
        }
        context.save_cursor(cursor, done=False)

    return StageReport(
        eligible=eligible,
        processed=processed,
        skipped=skipped,
        cursor=cursor,
        done=True,
        batches=batches,
    )


DEFAULT_STAGES: Mapping[str, StageRunner] = {
    "knowledge_mentions": backfill_knowledge_mentions,
    "story_membership": backfill_story_membership,
    "signal_evidence": backfill_signal_evidence,
    "signal_evidence_metadata": backfill_signal_evidence_metadata,
    "explanation_warmup": backfill_explanation_warmup,
}


def run_backfill(
    *,
    apply: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    stages: Mapping[str, StageRunner] | None = None,
    session_factory: Callable[[], Any] = get_session,
) -> dict[str, Any]:
    """Run every selected stage and return a machine-readable summary."""

    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("batch size must be between 1 and 1000")
    selected_stages = dict(stages or DEFAULT_STAGES)
    unknown = set(selected_stages) - set(STAGE_NAMES)
    if unknown:
        raise ValueError(f"unknown backfill stages: {sorted(unknown)}")

    checkpoint = JsonCheckpointStore(Path(checkpoint_path))
    state = checkpoint.load() if apply else {"version": CHECKPOINT_VERSION, "stages": {}}
    summary: dict[str, Any] = {
        "mode": "apply" if apply else "dry-run",
        "batch_size": batch_size,
        "checkpoint": str(checkpoint.path),
        "started_at": _utc_now().isoformat(),
        "stages": {},
    }
    for stage_name in STAGE_NAMES:
        runner = selected_stages.get(stage_name)
        if runner is None:
            continue
        saved = state.get("stages", {}).get(stage_name, {})
        cursor = saved.get("cursor")
        context = StageContext(
            apply=apply,
            batch_size=batch_size,
            session_factory=session_factory,
            checkpoint=checkpoint,
            checkpoint_state=state,
            stage_name=stage_name,
        )
        report = runner(context, cursor)
        summary["stages"][stage_name] = asdict(report)
    summary["finished_at"] = _utc_now().isoformat()
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill investigation/search/story evidence safely"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit bounded batches; without this flag the command is read-only",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Rows or clusters per transaction (1..{MAX_BATCH_SIZE})",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=DEFAULT_CHECKPOINT,
        help="Durable JSON checkpoint path used only in --apply mode",
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=tuple(DEFAULT_STAGES),
        help="Run only this stage; repeat to select multiple stages",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    args = build_parser().parse_args()
    wait_for_db()
    selected_stages = (
        {stage_name: DEFAULT_STAGES[stage_name] for stage_name in args.stage}
        if args.stage
        else None
    )
    summary = run_backfill(
        apply=args.apply,
        batch_size=args.batch_size,
        checkpoint_path=args.checkpoint,
        stages=selected_stages,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=_json_default))


if __name__ == "__main__":
    main()
