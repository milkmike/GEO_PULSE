from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import sys

import pytest

from scripts import audit_radar_wave1 as audit


PROTECTED = (
    "articles",
    "analysis",
    "temperature",
    "ru_index",
    "signals",
    "briefs",
    "threads",
    "thread_articles",
    "stories",
    "story_articles",
)


def _report(*, phase: str = "after") -> dict:
    protected = {
        name: {
            "status": "available",
            "count": 10,
            "primary_key": {
                "columns": ["id"],
                "count": 10,
                "fingerprint": f"sha256:{name}",
                "baseline_check": {
                    "status": "unchanged",
                    "expected_count": 10,
                    "actual_count": 10,
                    "expected_fingerprint": f"sha256:{name}",
                    "actual_fingerprint": f"sha256:{name}",
                },
            },
        }
        for name in PROTECTED
    }
    return {
        "schema_version": 1,
        "phase": phase,
        "protected": {"tables": protected},
        "migrations": {"status": "complete", "missing_required": []},
        "radar": {
            "tables": {name: {"status": "available", "count": 1} for name in audit.RADAR_TABLES},
            "evidence": {
                "source": "database",
                "observations": {"total": 10, "valid": 10, "invalid": 0, "ratio": 1.0},
                "trend_links": {"total": 10, "valid": 10, "invalid": 0},
            },
            "coverage_suppression": {"confirmed_below_hard_gate": 0},
            "t0_sanity": {"invalid": 0},
            "duplicate_public_ids": {"duplicates": 0},
            "notification_idempotency": {"duplicate_delivery_keys": 0, "invalid_references": 0},
        },
        "public_get_write_verification": {
            "status": "not_requested",
            "changed_tables": [],
            "before": None,
            "after": {name: {"status": "available", "count": 10} for name in PROTECTED},
        },
    }


def test_protected_table_contract_is_complete_and_stable():
    assert audit.PROTECTED_TABLES == PROTECTED


@pytest.mark.parametrize(
    ("mutate", "failure_code"),
    [
        (
            lambda report: report["protected"]["tables"]["articles"].update(count=9),
            "protected_count_decreased",
        ),
        (
            lambda report: report["protected"]["tables"]["articles"]["primary_key"][
                "baseline_check"
            ].update(status="changed"),
            "protected_primary_key_changed",
        ),
        (
            lambda report: report["radar"]["evidence"]["observations"].update(
                valid=8, invalid=2, ratio=0.8
            ),
            "evidence_completeness_below_gate",
        ),
        (
            lambda report: report["radar"]["duplicate_public_ids"].update(duplicates=1),
            "duplicate_public_ids",
        ),
        (
            lambda report: report["radar"]["t0_sanity"].update(invalid=1),
            "t0_invariant_failed",
        ),
        (
            lambda report: report["radar"]["notification_idempotency"].update(
                duplicate_delivery_keys=1
            ),
            "notification_idempotency_failed",
        ),
    ],
)
def test_compare_reports_fails_each_release_invariant(mutate, failure_code):
    before = _report(phase="before")
    after = _report()
    mutate(after)

    comparison = audit.compare_reports(before, after, evidence_minimum=0.95)

    assert comparison["passed"] is False
    assert failure_code in {failure["code"] for failure in comparison["failures"]}


def test_compare_reports_fails_when_public_get_snapshot_changes():
    before = _report()
    after = _report()
    after["public_get_write_verification"] = {
        "status": "changed",
        "changed_tables": ["analysis_runs"],
        "before": {"analysis_runs": {"status": "available", "count": 2}},
        "after": {"analysis_runs": {"status": "available", "count": 3}},
    }

    comparison = audit.compare_reports(before, after, evidence_minimum=0.95)

    assert comparison["passed"] is False
    assert "public_get_wrote_rows" in {failure["code"] for failure in comparison["failures"]}


class _FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.connection.calls.append((sql, parameters))
        if "audit_table_exists" in sql:
            self.rows = [(None,)]
        elif "audit_schema_migrations_exists" in sql:
            self.rows = [(None,)]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def fetchmany(self, _size):
        if not self.rows:
            return []
        rows, self.rows = self.rows, []
        return rows

    def close(self):
        pass


class _FakeConnection:
    def __init__(self):
        self.calls = []
        self.rollbacks = 0

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1


def test_database_audit_is_read_only_and_marks_missing_tables_truthfully():
    connection = _FakeConnection()

    report = audit.collect_database_report(
        connection,
        phase="before",
        evidence_minimum=0.95,
        generated_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
    )

    assert "READ ONLY" in connection.calls[0][0]
    assert connection.rollbacks == 1
    assert report["database"] == {"driver": "postgresql", "identifier": "redacted"}
    assert report["generated_at"] == "2026-07-18T12:00:00Z"
    assert all(
        table["status"] == "missing" for table in report["protected"]["tables"].values()
    )
    statements = "\n".join(sql for sql, _ in connection.calls)
    assert not any(
        token in statements.upper()
        for token in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE ", "ALTER ", "DROP ", "TRUNCATE ")
    )


def test_empty_primary_key_baseline_is_unchanged_even_when_boundary_is_null():
    connection = _FakeConnection()
    empty_fingerprint = "sha256:" + audit._new_identity_hash().hexdigest()

    snapshot = audit._primary_key_snapshot(
        connection,
        "briefs",
        ["id"],
        {
            "columns": ["id"],
            "count": 0,
            "fingerprint": empty_fingerprint,
            "boundary": None,
        },
    )

    assert snapshot["baseline_check"]["status"] == "unchanged"


def test_shadow_report_uses_replay_metrics_without_claiming_they_are_persisted():
    connection = _FakeConnection()
    replay = {
        "candidates": 1,
        "emerging": 1,
        "confirmed": 1,
        "rejected": 0,
        "country_wave_count": 2,
        "meta_trend_count": 1,
        "collector_suppressed": 3,
        "country_coverage": {"ES": 0.8},
        "t0_distribution": {"automatic": 2, "unresolved": 1},
        "evidence_completeness": {"complete": 3, "incomplete": 0},
    }

    report = audit.collect_database_report(
        connection,
        phase="shadow",
        evidence_minimum=0.95,
        replay_report=replay,
        generated_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
    )

    assert report["radar"]["observation_count"] == 3
    assert report["radar"]["trend_count"] == 3
    assert report["radar"]["state_distribution"]["source"] == "shadow_replay"
    assert report["radar"]["coverage_suppression"]["collector_suppressed"] == 3
    assert report["radar"]["t0_sanity"]["distribution"] == {"automatic": 2, "unresolved": 1}
    assert report["radar"]["contour_completeness"]["status"] == "not_persisted"


def test_cli_contract_supports_release_inputs_without_exposing_database_url():
    args = audit.parse_args(
        [
            "--phase",
            "shadow",
            "--json",
            "--compare",
            "before.json",
            "--replay-report",
            "shadow.json",
            "--public-get-before",
            "pre-smoke.json",
            "--database-url",
            "postgresql://user:secret@db/database",
        ]
    )

    assert args.phase == "shadow"
    assert args.json is True
    assert args.compare == "before.json"
    assert args.public_get_before == "pre-smoke.json"
    assert "secret" not in audit.database_metadata(args.database_url).values()


def test_connection_errors_never_echo_database_secrets(monkeypatch, capsys):
    def fail_to_connect(url):
        raise RuntimeError(f"could not connect with {url}")

    monkeypatch.setitem(sys.modules, "psycopg2", SimpleNamespace(connect=fail_to_connect))

    status = audit.main(
        [
            "--phase",
            "before",
            "--database-url",
            "postgresql://operator:do-not-print@db/database",
        ]
    )

    assert status == 2
    assert "do-not-print" not in capsys.readouterr().err
