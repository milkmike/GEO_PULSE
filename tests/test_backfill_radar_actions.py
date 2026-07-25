from datetime import datetime, timezone
from types import SimpleNamespace

from scripts.backfill_radar_actions import backfill_radar_actions


class _Result:
    def __init__(self, rows=(), scalar=None, rowcount=0):
        self._rows = tuple(rows)
        self._scalar = scalar
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _Session:
    def __init__(self):
        self.calls = []
        self.events = {}

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        if "radar_action_backfill_source" in sql:
            return _Result(rows=(SimpleNamespace(
                public_id="11111111-1111-1111-1111-111111111111",
                input_hash="a" * 64,
                country_code="ES",
                contour="action",
                subject_key="economy:trade:russia",
                direction="increase",
                metric="trade_change",
                observed_at=datetime(2025, 12, 31, tzinfo=timezone.utc),
                value=20,
                publisher_family_count=0,
                source_count=1,
                coverage_confidence=1,
                authority="registry",
                article_id=None,
                story_id=None,
                signal_id=None,
                canonical_entity_id=None,
                baseline={},
                evidence={
                    "dataset": "trade_data", "status": "verified",
                    "source_id": "trade:ES:2025:120:100:20",
                    "temporal_resolution": "year", "period_year": 2025,
                    "previous_value": 100, "current_value": 120, "delta": 20,
                    "evidence_ids": ("trade:ES:2025:120:100:20",),
                },
            ),))
        if "INSERT INTO action_events" in sql:
            event_id = self.events.setdefault(
                params["input_hash"], len(self.events) + 1,
            )
            return _Result(scalar=event_id)
        if "radar_action_backfill_evidence" in sql:
            return _Result(rowcount=1)
        return _Result()


def test_backfill_derives_events_additively_and_fills_missing_evidence_root():
    session = _Session()

    result = backfill_radar_actions(session)

    assert result == {"observations": 1, "events": 1, "evidence_links": 1}
    event_sql = next(
        sql for sql, _ in session.calls if "INSERT INTO action_events" in sql
    )
    link_sql = next(
        sql for sql, _ in session.calls
        if "radar_action_backfill_evidence" in sql
    )
    assert "ON CONFLICT (input_hash) DO NOTHING" in event_sql
    assert "action_event_id IS NULL" in link_sql
    assert all(
        "DELETE" not in sql.upper() and "TRUNCATE" not in sql.upper()
        for sql, _ in session.calls
    )
