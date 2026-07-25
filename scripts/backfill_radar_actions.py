"""Additively materialize action events from existing Radar observations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.radar.repository import upsert_action_events
from src.radar.service import _observations_from_rows


_ACTION_OBSERVATIONS = text("""
/* radar_action_backfill_source */
SELECT public_id, input_hash, country_code, contour, subject_key, direction,
       metric, observed_at, value, publisher_family_count, source_count,
       coverage_confidence, authority, article_id, story_id, signal_id,
       canonical_entity_id, baseline, evidence
FROM radar_observations
WHERE contour = 'action'
  AND authority IN ('registry', 'formal')
  AND evidence->>'status' = 'verified'
ORDER BY id
""")

_LINK_MISSING_EVIDENCE = text("""
/* radar_action_backfill_evidence */
UPDATE radar_trend_evidence evidence
SET action_event_id = event.id
FROM radar_observations observation
JOIN action_events event ON event.input_hash = observation.input_hash
WHERE evidence.observation_id = observation.id
  AND observation.contour = 'action'
  AND evidence.action_event_id IS NULL
""")


def backfill_radar_actions(session: Any) -> dict[str, int]:
    observations = _observations_from_rows(
        session.execute(_ACTION_OBSERVATIONS).fetchall()
    )
    event_ids = upsert_action_events(session, observations)
    linked = session.execute(_LINK_MISSING_EVIDENCE)
    return {
        "observations": len(observations),
        "events": len(event_ids),
        "evidence_links": max(0, int(getattr(linked, "rowcount", 0) or 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill immutable Radar action events additively",
    )
    parser.parse_args()
    from src.db import get_session, wait_for_db

    wait_for_db()
    with get_session() as session:
        result = backfill_radar_actions(session)
        session.commit()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
