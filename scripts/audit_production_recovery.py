"""Read-only production recovery audit with protected-data gates."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PROTECTED_COUNTS = ("articles", "analysis", "temperature")
FRESHNESS_LIMITS = {
    "articles": 2 * 60 * 60,
    "analysis": 2 * 60 * 60,
    "stories": 24 * 60 * 60,
}
API_LIMIT_SECONDS = 20.0
API_PATHS = (
    "/api/v2/health",
    "/api/v2/countries",
    "/api/v2/stories",
    "/api/v2/radar",
)


def _redacted_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or "@" not in parsed.netloc:
        return value
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((
        parsed.scheme, f"***@{host}", parsed.path, parsed.query, parsed.fragment,
    ))


def _redact(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in (
        "password", "secret", "token", "api_key", "credential",
    )):
        return "***"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, str):
        return _redacted_url(value)
    return value


def _aware(value: str | datetime) -> datetime:
    parsed = (
        value if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("audit timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def evaluate_recovery(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate non-destructive release gates and return redacted evidence."""

    now = _aware(now or datetime.now(timezone.utc))
    failures: list[str] = []
    before_counts = before.get("protected_counts", {})
    after_counts = after.get("protected_counts", {})
    for name in PROTECTED_COUNTS:
        previous = int(before_counts.get(name, 0) or 0)
        current = int(after_counts.get(name, 0) or 0)
        if current < previous:
            failures.append(f"protected count decreased: {name}")

    freshness = after.get("freshness", {})
    for name, limit in FRESHNESS_LIMITS.items():
        value = freshness.get(name)
        if value is None or (now - _aware(value)).total_seconds() > limit:
            failures.append(f"pipeline stale: {name}")

    for name, state in after.get("workers", {}).items():
        if int(state.get("restart_delta", 0) or 0) > 0:
            failures.append(f"worker restarted during audit: {name}")

    for path, result in after.get("api", {}).items():
        if int(result.get("status", 0) or 0) != 200:
            failures.append(f"api failed: {path}")
        elif float(result.get("seconds", API_LIMIT_SECONDS + 1)) > API_LIMIT_SECONDS:
            failures.append(f"api slow: {path}")

    return _redact({
        "ok": not failures,
        "failures": failures,
        "before": before,
        "after": after,
        "checked_at": now.isoformat(),
    })


_DATABASE_AUDIT = text("""
WITH active_profile AS (
  SELECT MIN(id) AS profile_id
  FROM embedding_profiles
  WHERE active = TRUE
  HAVING COUNT(*) = 1 AND BOOL_AND(dimensions = 1024)
), eligible AS (
  SELECT article.id, CASE
         WHEN COALESCE(article.summary, '') <> '' THEN
           COALESCE(article.title, '') || E'\n' || article.summary
         WHEN COALESCE(article.body, '') <> '' THEN
           COALESCE(article.title, '') || E'\n' || LEFT(article.body, 1000)
         ELSE COALESCE(article.title, '') END AS content
  FROM articles article
  JOIN analysis result ON result.article_id = article.id
  WHERE result.is_relevant = TRUE
    AND article.is_duplicate = FALSE
    AND article.geo_country_code IS NOT NULL
    AND article.published_at >= NOW() - INTERVAL '30 days'
), ready AS (
  SELECT DISTINCT embedding.object_id, embedding.content_hash
  FROM content_embeddings embedding
  JOIN active_profile profile ON profile.profile_id = embedding.profile_id
  WHERE embedding.object_type = 'article'
    AND embedding.status = 'ready'
    AND embedding.embedding IS NOT NULL
)
SELECT
  (SELECT COUNT(*) FROM articles)::bigint AS articles,
  (SELECT COUNT(*) FROM analysis)::bigint AS analysis,
  (SELECT COUNT(*) FROM temperature)::bigint AS temperature,
  (SELECT MAX(collected_at) FROM articles) AS latest_article,
  (SELECT MAX(analyzed_at) FROM analysis) AS latest_analysis,
  (SELECT MAX(last_seen) FROM stories) AS latest_story,
  (SELECT COUNT(*) FROM stories
    WHERE lifecycle IN ('emerging', 'developing', 'escalating'))::bigint
    AS active_stories,
  (SELECT COUNT(*) FROM action_events)::bigint AS action_events,
  (SELECT COUNT(*) FROM radar_contour_links)::bigint AS contour_links,
  (SELECT COUNT(*) FROM radar_trends
    WHERE scope = 'meta' AND state IN ('emerging','confirmed','cooling')
      AND ABS(COALESCE(velocity, 0)) >= 0.05)::bigint AS public_radar_candidates,
  COUNT(eligible.id)::bigint AS embedding_eligible,
  COUNT(ready.object_id)::bigint AS embedding_ready_current
FROM eligible
LEFT JOIN ready ON ready.object_id = eligible.id::text
  AND ready.content_hash = encode(digest(eligible.content, 'sha256'), 'hex')
""")


def collect_database_snapshot(session: Any) -> dict[str, Any]:
    row = session.execute(_DATABASE_AUDIT).first()
    mapping = row._mapping if hasattr(row, "_mapping") else row
    eligible = int(mapping["embedding_eligible"] or 0)
    ready = int(mapping["embedding_ready_current"] or 0)
    return {
        "protected_counts": {
            name: int(mapping[name] or 0) for name in PROTECTED_COUNTS
        },
        "freshness": {
            "articles": mapping["latest_article"].isoformat()
            if mapping["latest_article"] else None,
            "analysis": mapping["latest_analysis"].isoformat()
            if mapping["latest_analysis"] else None,
            "stories": mapping["latest_story"].isoformat()
            if mapping["latest_story"] else None,
        },
        "stories": {"active": int(mapping["active_stories"] or 0)},
        "embeddings": {
            "eligible": eligible,
            "ready_current": ready,
            "coverage": ready / eligible if eligible else None,
        },
        "radar": {
            "public_candidates": int(mapping["public_radar_candidates"] or 0),
            "action_events": int(mapping["action_events"] or 0),
            "contour_links": int(mapping["contour_links"] or 0),
        },
    }


def collect_api_snapshot(base_url: str) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for path in API_PATHS:
        started = time.monotonic()
        try:
            with urlopen(base_url.rstrip("/") + path, timeout=30) as response:
                response.read(1)
                status = response.status
            results[path] = {
                "status": status,
                "seconds": round(time.monotonic() - started, 3),
            }
        except Exception as exc:
            results[path] = {
                "status": 0,
                "seconds": round(time.monotonic() - started, 3),
                "error": type(exc).__name__,
            }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit GEO PULSE recovery")
    parser.add_argument("--before", required=True)
    parser.add_argument("--base-url", default="http://api:8100")
    parser.add_argument("--workers-json")
    parser.add_argument("--out")
    args = parser.parse_args()

    from src.db import get_session, wait_for_db

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    wait_for_db()
    with get_session() as session:
        after = collect_database_snapshot(session)
    after["api"] = collect_api_snapshot(args.base_url)
    if args.workers_json:
        after["workers"] = json.loads(
            Path(args.workers_json).read_text(encoding="utf-8")
        )
    report = evaluate_recovery(before, after)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
