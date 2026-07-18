from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import re

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
            },
        }
        for name in PROTECTED
    }
    return {
        "schema_version": 1,
        "phase": phase,
        "protected": {"tables": protected},
        "migrations": {"status": "complete", "required": audit.required_migrations(), "missing_required": []},
        "radar": {
            "tables": {name: {"status": "available", "count": 1} for name in audit.RADAR_TABLES},
            "evidence": {
                "source": "database",
                "observations": {"status": "available", "total": 10, "valid": 10, "invalid": 0, "ratio": 1.0},
                "trend_links": {"status": "available", "total": 10, "valid": 10, "invalid": 0},
            },
            "coverage_suppression": {"status": "available", "confirmed_below_hard_gate": 0},
            "t0_sanity": {"status": "available", "invalid": 0},
            "duplicate_public_ids": {"status": "available", "duplicates": 0},
            "notification_idempotency": {"status": "available", "duplicate_delivery_keys": 0, "invalid_references": 0},
            "contour_completeness": {"status": "available", "identity_candidate_pairs": 10, "possible_pairs": 10, "linked_pairs": 8, "ratio": 0.8},
        },
        "write_activity": {
            name: {
                "status": "available",
                "count": 10,
                "stats": {"reset_identity": "reset-a", "n_tup_ins": 1, "n_tup_upd": 2, "n_tup_del": 3},
            }
            for name in audit.WRITE_SNAPSHOT_TABLES
        },
        "public_get_write_verification": {
            "status": "unchanged" if phase == "after" else "not_requested",
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
            lambda report: report["protected"]["tables"]["articles"]["primary_key"].update(
                fingerprint="sha256:changed"
            ),
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


def test_after_gate_rejects_protected_updates_during_bounded_apply():
    before = _report(phase="before")
    after = _report(phase="after")
    after["write_activity"]["articles"]["stats"]["n_tup_upd"] += 1
    # The public-GET baseline is intentionally captured after apply, so it can
    # remain unchanged while the apply itself mutated a protected table.
    after["public_get_write_verification"]["status"] = "unchanged"

    comparison = audit.compare_reports(before, after, evidence_minimum=0.95)

    assert comparison["passed"] is False
    assert "protected_write_activity_changed" in {
        failure["code"] for failure in comparison["failures"]
    }


@pytest.mark.parametrize("mutation", ("count", "counter"))
def test_shadow_replay_must_leave_radar_tables_and_write_counters_unchanged(mutation):
    before = _report(phase="before")
    shadow = _report(phase="shadow")
    if mutation == "count":
        shadow["radar"]["tables"]["radar_observations"]["count"] += 1
    else:
        shadow["write_activity"]["radar_observations"]["stats"]["n_tup_ins"] += 1

    comparison = audit.compare_reports(before, shadow, evidence_minimum=0.95)

    assert comparison["passed"] is False
    assert "shadow_replay_wrote_rows" in {
        failure["code"] for failure in comparison["failures"]
    }


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


def test_shadow_report_uses_replay_metrics_without_claiming_they_are_persisted():
    connection = _FakeConnection()
    replay = _rich_replay()

    report = audit.collect_database_report(
        connection,
        phase="shadow",
        evidence_minimum=0.95,
        replay_report=replay,
        generated_at=datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
    )

    assert report["radar"]["observation_count"] == 4
    assert report["radar"]["trend_count"] == 3
    assert report["radar"]["state_distribution"]["source"] == "shadow_replay"
    assert report["radar"]["coverage_suppression"]["collector_suppressed"] == 0
    assert report["radar"]["t0_sanity"]["distribution"] == {"automatic": 3, "unresolved": 0, "violations": 0}
    assert report["radar"]["contour_completeness"]["status"] == "available"


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
            "--database-url",
            "postgresql://user:secret@db/database",
        ]
    )

    assert args.phase == "shadow"
    assert args.json is True
    assert args.compare == "before.json"
    assert args.public_get_before is None
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


ROOT = Path(__file__).resolve().parents[1]


def test_temperature_image_packages_both_radar_clis_and_migrations():
    dockerfile = (ROOT / "Dockerfile.temperature").read_text(encoding="utf-8")

    assert "scripts/build_radar.py" in dockerfile
    assert "scripts/audit_radar_wave1.py" in dockerfile
    assert "COPY scripts/migrations/ scripts/migrations/" in dockerfile
    assert audit.required_migrations() == [
        "027_early_warning_radar.sql",
        "028_radar_evidence_roots.sql",
        "029_radar_evidence_relation_rows.sql",
        "030_radar_contour_alignment_identity.sql",
    ]


def test_identity_serialization_preserves_microseconds_and_types():
    instant = datetime(2026, 7, 18, 12, 30, 1, 123456, tzinfo=timezone.utc)

    timestamp_payload = audit.serialize_identity((instant, "ES"))

    assert "2026-07-18T12:30:01.123456Z" in timestamp_payload
    assert timestamp_payload != audit.serialize_identity(
        (instant + timedelta(microseconds=1), "ES")
    )
    assert audit.serialize_identity((1,)) != audit.serialize_identity(("1",))
    assert audit.serialize_identity((True,)) != audit.serialize_identity((1,))


class _RowsCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, statement, parameters=None):
        self.connection.calls.append((str(statement), parameters))
        self.rows = list(self.connection.rows)

    def fetchmany(self, size):
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class _RowsConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def cursor(self):
        return _RowsCursor(self)


def test_full_composite_pk_fingerprint_detects_interleaved_insert():
    before = audit._primary_key_snapshot(
        _RowsConnection([(1, 10), (2, 20)]), "story_articles", ["story_id", "article_id"]
    )
    after = audit._primary_key_snapshot(
        _RowsConnection([(1, 10), (1, 99), (2, 20)]),
        "story_articles",
        ["story_id", "article_id"],
    )

    assert before["fingerprint"] != after["fingerprint"]
    assert "boundary" not in before
    assert "baseline_check" not in after


def test_full_pk_fingerprint_distinguishes_timestamp_microsecond_collision():
    first = datetime(2026, 7, 18, 12, 30, 1, 1, tzinfo=timezone.utc)
    second = first + timedelta(microseconds=1)

    left = audit._primary_key_snapshot(
        _RowsConnection([(first, "ES")]), "temperature", ["time", "country_code"]
    )
    right = audit._primary_key_snapshot(
        _RowsConnection([(second, "ES")]), "temperature", ["time", "country_code"]
    )

    assert left["fingerprint"] != right["fingerprint"]


@pytest.mark.parametrize("phase", ["shadow", "after"])
def test_non_capture_phases_fail_closed_without_authoritative_before(phase):
    report = _report(phase=phase)

    comparison = audit.compare_reports(None, report, evidence_minimum=0.95)

    assert comparison["passed"] is False
    assert "authoritative_before_required" in {
        failure["code"] for failure in comparison["failures"]
    }


@pytest.mark.parametrize(
    ("path", "value", "failure_code"),
    [
        (("radar", "evidence", "observations", "ratio"), None, "evidence_unavailable"),
        (("radar", "coverage_suppression", "confirmed_below_hard_gate"), None, "coverage_suppression_unavailable"),
        (("radar", "t0_sanity", "invalid"), None, "t0_sanity_unavailable"),
        (("radar", "duplicate_public_ids", "duplicates"), None, "duplicate_public_ids_unavailable"),
        (("radar", "notification_idempotency", "duplicate_delivery_keys"), None, "notification_idempotency_unavailable"),
    ],
)
def test_none_metrics_are_gate_failures_not_zero(path, value, failure_code):
    before = _report(phase="before")
    current = _report()
    target = current
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    comparison = audit.compare_reports(before, current, evidence_minimum=0.95)

    assert failure_code in {failure["code"] for failure in comparison["failures"]}


def test_contour_unavailable_fails_and_empty_no_pair_case_is_explicit():
    before = _report(phase="before")
    unavailable = _report()
    unavailable["radar"]["contour_completeness"] = {
        "status": "unavailable",
        "identity_candidate_pairs": None,
        "possible_pairs": None,
        "linked_pairs": None,
        "ratio": None,
    }
    no_pairs = _report()
    no_pairs["radar"]["contour_completeness"] = {
        "status": "not_applicable_no_pairs",
        "identity_candidate_pairs": 2,
        "possible_pairs": 0,
        "linked_pairs": 0,
        "ratio": None,
    }

    unavailable_result = audit.compare_reports(before, unavailable, evidence_minimum=0.95)
    no_pairs_result = audit.compare_reports(before, no_pairs, evidence_minimum=0.95)

    assert "contour_completeness_unavailable" in {
        failure["code"] for failure in unavailable_result["failures"]
    }
    assert "contour_completeness_unavailable" not in {
        failure["code"] for failure in no_pairs_result["failures"]
    }


def test_contour_ratio_below_default_gate_fails():
    before = _report(phase="before")
    current = _report()
    current["radar"]["contour_completeness"] = {
        "status": "available",
        "identity_candidate_pairs": 10,
        "possible_pairs": 10,
        "linked_pairs": 7,
        "ratio": 0.7,
    }

    comparison = audit.compare_reports(
        before, current, evidence_minimum=0.95, contour_minimum=0.8
    )

    assert "contour_completeness_below_gate" in {
        failure["code"] for failure in comparison["failures"]
    }


def _write_snapshot(*, count=10, inserted=1, updated=2, deleted=3, reset="reset-a"):
    return {
        table: {
            "status": "available",
            "count": count,
            "stats": {
                "reset_identity": reset,
                "n_tup_ins": inserted,
                "n_tup_upd": updated,
                "n_tup_del": deleted,
            },
        }
        for table in audit.WRITE_SNAPSHOT_TABLES
    }


def test_public_get_same_count_update_is_detected_from_postgres_stats():
    baseline = _report(phase="before")
    baseline["write_activity"] = _write_snapshot()
    current = _write_snapshot(updated=3)

    result = audit._public_get_verification(current, baseline)

    assert result["status"] == "changed"
    assert result["changed_tables"] == list(audit.WRITE_SNAPSHOT_TABLES)


def test_public_get_stats_reset_identity_change_fails():
    baseline = _report(phase="before")
    baseline["write_activity"] = _write_snapshot()

    result = audit._public_get_verification(
        _write_snapshot(reset="reset-b"), baseline
    )

    assert result["status"] == "changed"
    assert result["reset_identity_changed"] is True


class _InheritedWriteStatsCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.connection.calls.append((sql, parameters))
        if "audit_stats_reset_identity" in sql:
            self.rows = [
                (datetime(2026, 7, 18, 12, 30, tzinfo=timezone.utc),)
            ]
        elif "audit_table_write_stats" in sql:
            recursively_aggregates = (
                "WITH RECURSIVE" in sql
                and "pg_catalog.pg_inherits" in sql
                and "SUM(" in sql.upper()
                and re.search(r"\bUNION\b(?!\s+ALL)", sql, re.IGNORECASE)
            )
            rejects_incomplete_stats = "LEFT JOIN" in sql and "COUNT(" in sql.upper()
            if self.connection.missing_descendant_stats and rejects_incomplete_stats:
                self.rows = [(None, None, None)]
            else:
                self.rows = [(11, 22, 33)] if recursively_aggregates else [(1, 2, 3)]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class _InheritedWriteStatsConnection:
    def __init__(self, *, missing_descendant_stats=False):
        self.calls = []
        self.missing_descendant_stats = missing_descendant_stats

    def cursor(self):
        return _InheritedWriteStatsCursor(self)


def test_write_activity_aggregates_root_and_all_inheritance_descendants():
    connection = _InheritedWriteStatsConnection()
    table_counts = {
        table: {"status": "unavailable", "count": None}
        for table in audit.WRITE_SNAPSHOT_TABLES
    }
    table_counts["articles"] = {"status": "available", "count": 5}

    snapshot = audit._write_activity_snapshot(connection, table_counts)

    assert snapshot["articles"] == {
        "status": "available",
        "count": 5,
        "stats": {
            "reset_identity": "2026-07-18T12:30:00.000000Z",
            "n_tup_ins": 11,
            "n_tup_upd": 22,
            "n_tup_del": 33,
        },
    }
    stats_sql, parameters = next(
        (sql, params)
        for sql, params in connection.calls
        if "audit_table_write_stats" in sql
    )
    assert "WITH RECURSIVE" in stats_sql
    assert "pg_catalog.pg_inherits" in stats_sql
    assert re.search(r"\bUNION\b(?!\s+ALL)", stats_sql, re.IGNORECASE)
    assert parameters == ("articles",)


def test_write_activity_fails_closed_when_any_descendant_has_no_stats_row():
    connection = _InheritedWriteStatsConnection(missing_descendant_stats=True)
    table_counts = {
        table: {"status": "unavailable", "count": None}
        for table in audit.WRITE_SNAPSHOT_TABLES
    }
    table_counts["articles"] = {"status": "available", "count": 5}

    snapshot = audit._write_activity_snapshot(connection, table_counts)

    assert snapshot["articles"] == {
        "status": "stats_unavailable",
        "count": 5,
        "stats": None,
    }


def test_postgres_stats_reset_identity_preserves_utc_microseconds():
    first = datetime(2026, 7, 18, 12, 30, 1, 1, tzinfo=timezone.utc)
    second = first + timedelta(microseconds=1)

    assert audit._utc_precise_text(first) == "2026-07-18T12:30:01.000001Z"
    assert audit._utc_precise_text(second) == "2026-07-18T12:30:01.000002Z"


@pytest.mark.parametrize(
    "argv",
    [
        ["--phase", "before", "--compare", "before.json"],
        ["--phase", "shadow"],
        ["--phase", "shadow", "--compare", "before.json"],
        ["--phase", "after"],
        ["--phase", "after", "--compare", "before.json"],
    ],
)
def test_cli_rejects_incomplete_or_mixed_phase_inputs(argv):
    with pytest.raises(SystemExit):
        audit.parse_args(argv)


def _rich_replay() -> dict:
    country_states = {
        state: 0
        for state in ("candidate", "emerging", "confirmed", "cooling", "resolved", "rejected")
    }
    country_states["confirmed"] = 2
    meta_states = dict(country_states)
    meta_states["confirmed"] = 1
    return {
        "as_of": "2026-07-18T12:00:00.123456Z",
        "shadow": True,
        "detector_version": "radar-wave-1",
        "lookback_days": 90,
        "observation_count": 4,
        "country_trend_count": 2,
        "meta_trend_count": 1,
        "country_state_counts": country_states,
        "meta_state_counts": meta_states,
        "evidence_validity": {"total": 4, "valid": 4, "invalid": 0, "ratio": 1.0},
        "collector_suppressed": 0,
        "confirmed_below_coverage_gate": 0,
        "t0_sanity": {"automatic": 3, "unresolved": 0, "violations": 0},
        "contour_completeness": {"identity_candidate_pairs": 2, "possible_pairs": 2, "linked_pairs": 2, "ratio": 1.0},
    }


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda value: value.update(shadow=False), "shadow=true"),
        (lambda value: value.update(lookback_days=89), "90"),
        (lambda value: value.update(as_of="2026-07-18T12:00:00"), "timezone"),
        (lambda value: value.update(detector_version="other"), "detector"),
        (lambda value: value.pop("evidence_validity"), "evidence_validity"),
        (lambda value: value["t0_sanity"].update(automatic=2), "t0_sanity"),
        (lambda value: value["t0_sanity"].update(violations=4), "t0_sanity"),
        (lambda value: value.update(collector_suppressed=3), "collector_suppressed"),
        (
            lambda value: value.update(confirmed_below_coverage_gate=3),
            "confirmed_below_coverage_gate",
        ),
    ],
)
def test_replay_contract_is_strict(mutate, match):
    replay = _rich_replay()
    mutate(replay)

    with pytest.raises(ValueError, match=match):
        audit.validate_replay_report(replay)


def test_apply_report_uses_the_same_strict_contract_with_shadow_false():
    replay = _rich_replay()
    replay["shadow"] = False

    audit.validate_replay_report(replay, expected_shadow=False)
    with pytest.raises(ValueError, match="shadow=true"):
        audit.validate_replay_report(replay)


def test_phase_report_contract_requires_before_reports_for_comparison_and_get_baseline():
    before = _report(phase="before")
    audit.validate_phase_reports(
        "shadow", compare_report=before, replay_report=_rich_replay(), public_get_before=None
    )
    audit.validate_phase_reports(
        "after", compare_report=before, replay_report=None, public_get_before=before
    )

    with pytest.raises(ValueError, match="phase=before"):
        audit.validate_phase_reports(
            "after", compare_report=_report(phase="after"), replay_report=None, public_get_before=before
        )
    with pytest.raises(ValueError, match="phase=before"):
        audit.validate_phase_reports(
            "after", compare_report=before, replay_report=None, public_get_before=_report(phase="after")
        )


def test_evidence_queries_require_typed_traceable_and_existing_roots():
    source = (ROOT / "scripts/audit_radar_wave1.py").read_text(encoding="utf-8")

    assert "jsonb_typeof" in source
    assert "jsonb_array_length" in source
    for table in ("articles", "stories", "signals", "canonical_entities"):
        assert f"public.{table}" in source
    assert "_relation_only" in source
    assert "source_record_id" in source


@pytest.mark.parametrize(
    ("field", "alias"),
    [
        ("article_ids", "article"),
        ("story_ids", "story"),
        ("signal_ids", "signal"),
        ("entity_ids", "entity"),
    ],
)
def test_observation_evidence_allows_empty_optional_arrays_without_treating_them_as_roots(
    field, alias
):
    source = (ROOT / "scripts/audit_radar_wave1.py").read_text(encoding="utf-8")
    observation_sql = source.split("/* audit_observation_evidence */", 1)[1].split(
        "/* audit_trend_evidence */", 1
    )[0]
    validation = re.search(
        rf"CASE WHEN observation\.evidence \? '{field}'.*?AS {alias}_array_valid",
        observation_sql,
        re.DOTALL,
    )

    assert validation is not None
    validation_sql = validation.group(0)
    # Empty optional arrays are well-formed. A populated array is valid only
    # when every referenced row exists, so malformed values and orphans still
    # fail closed through the anti-join.
    assert f"jsonb_array_length(observation.evidence->'{field}') > 0 AND" not in validation_sql
    assert "NOT EXISTS" in validation_sql
    assert "WHERE root.id IS NULL" in validation_sql

    # Well-formedness is deliberately separate from evidence existence: an
    # empty optional array must not make an otherwise rootless observation valid.
    assert f"{alias}_array_has_root" in observation_sql
    assert re.search(
        rf"{alias}_array_valid\s+AND\s+{alias}_array_has_root"
        rf"|{alias}_array_has_root\s+AND\s+{alias}_array_valid",
        observation_sql,
    )


def test_all_application_tables_are_public_schema_qualified():
    source = (ROOT / "scripts/audit_radar_wave1.py").read_text(encoding="utf-8")
    names = PROTECTED + audit.RADAR_TABLES + ("schema_migrations", "canonical_entities")
    unqualified = re.findall(
        rf"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?!(?:public|pg_catalog)\.)(?:{'|'.join(names)})\b",
        source,
    )

    assert unqualified == []


def test_contour_gate_uses_the_canonical_alignment_identity_from_migration_030():
    source = (ROOT / "scripts/audit_radar_wave1.py").read_text(encoding="utf-8")

    assert "action.alignment_subject = media.alignment_subject" in source
    assert "action.alignment_direction = media.alignment_direction" in source


def _database_contour_report(monkeypatch, *, action_at, links=()):
    media_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    bounds = [
        (1, "media", "ES", "energy:imports:russia", "hardening", "radar-wave-1", "media-wave", media_at, media_at),
        (2, "action", "ES", "energy:imports:russia", "hardening", "radar-wave-1", "action-wave", action_at, action_at),
    ]

    def execute(_connection, statement, parameters=None):
        del parameters
        if "audit_contour_episode_bounds" in statement:
            return bounds
        if "audit_contour_links" in statement:
            return list(links)
        # Old implementation: keep RED as an assertion failure, not a fixture error.
        if "audit_contour_completeness" in statement:
            return [(1, 1, 1, len(links))]
        raise AssertionError(statement)

    monkeypatch.setattr(audit, "_execute", execute)
    return audit._contour_completeness(
        object(),
        {"radar_trends", "radar_contour_links", "radar_trend_evidence", "radar_observations"},
    )


def test_database_contour_denominator_excludes_temporally_ineligible_candidate(monkeypatch):
    report = _database_contour_report(
        monkeypatch,
        action_at=datetime(2026, 7, 15, 0, 0, 1, tzinfo=timezone.utc),
    )

    assert report == {
        "status": "not_applicable_no_pairs",
        "media_trends": 1,
        "action_trends": 1,
        "identity_candidate_pairs": 1,
        "possible_pairs": 0,
        "linked_pairs": 0,
        "ratio": None,
    }


def test_database_contour_gate_detects_missing_eligible_link(monkeypatch):
    action_at = datetime(2026, 7, 15, tzinfo=timezone.utc)
    missing = _database_contour_report(monkeypatch, action_at=action_at)
    assert missing["identity_candidate_pairs"] == 1
    assert missing["possible_pairs"] == 1
    assert missing["linked_pairs"] == 0
    assert missing["ratio"] == 0.0

    linked = _database_contour_report(
        monkeypatch, action_at=action_at, links=((1, 2),),
    )
    assert linked["possible_pairs"] == 1
    assert linked["linked_pairs"] == 1
    assert linked["ratio"] == 1.0


def test_runbook_is_one_quiesced_fail_closed_sequence():
    runbook = (ROOT / "docs/release/early-warning-radar-wave1.md").read_text(encoding="utf-8")

    required_in_order = [
        "pg_dump",
        "git push origin main",
        "trap '",
        "docker compose stop",
        "--phase before",
        "--shadow --days 90",
        "--phase shadow",
        "--apply --days 90",
        "--phase before",  # fresh GET baseline while the same writers stay stopped
        "hidden API",
        "--phase after",
        "FEATURE_EARLY_WARNING_RADAR=true",
        "docker compose build web",
        "browser smoke",
        "--phase after",
        "docker compose start",
        "trap - EXIT",
    ]
    position = -1
    for marker in required_in_order:
        position = runbook.find(marker, position + 1)
        assert position >= 0, marker
    assert "umask 077" in runbook
    assert "mktemp -d" in runbook
    assert "printenv FEATURE_EARLY_WARNING_RADAR" not in runbook.split("api", 1)[-1]
    assert "FEATURE_EARLY_WARNING_RADAR=false" in runbook[runbook.index("trap '") :]
    assert "docker compose stop web" in runbook[runbook.index("fail_closed()") :]
    assert 'running_services="$(docker compose ps --status running --services)"' in runbook
    assert 'docker compose ps --status running --services | grep' not in runbook
    assert "--contour-minimum 0.80" in runbook
    assert 'RADAR_AS_OF="$(date -u' in runbook
    assert runbook.count("--as-of '$RADAR_AS_OF'") == 2
    assert "bounded-apply.json', encoding='utf-8')), expected_shadow=False" in runbook


def test_runbook_stabilizes_postgres_write_stats_before_authoritative_baseline():
    runbook = (ROOT / "docs/release/early-warning-radar-wave1.md").read_text(
        encoding="utf-8"
    )

    stop = runbook.index("docker compose stop $running_writers")
    definition = runbook.index("wait_for_write_stats_quiescence()")
    barrier = runbook.index("\nwait_for_write_stats_quiescence\n", stop)
    baseline = runbook.index("--phase before", barrier)

    assert stop < barrier < baseline
    assert "pg_catalog.pg_stat_all_tables" in runbook
    assert "pg_catalog.pg_stat_database" in runbook
    assert "count(relation.relid) <> count(table_stats.relid)" in runbook
    assert "for attempt in $(seq 1 30)" in runbook
    assert 'test "$stable_samples" -ge 3' in runbook
    assert definition < stop
    assert "sleep 2" in runbook[definition:stop]
    assert "default_transaction_read_only=on" in runbook
    assert runbook.count("\nwait_for_write_stats_quiescence\n") == 5


def test_runbook_waits_for_recreated_web_before_public_smoke():
    runbook = (ROOT / "docs/release/early-warning-radar-wave1.md").read_text(
        encoding="utf-8"
    )

    helper = runbook.index("wait_for_http_content()")
    recreate = runbook.index(
        "docker compose up -d --no-deps --force-recreate web", helper
    )
    api_wait = runbook.index(
        'wait_for_http_content "http://127.0.0.1:8100/api/v2/radar?limit=1" "items"',
        recreate,
    )
    web_wait = runbook.index(
        'wait_for_http_content "http://127.0.0.1:3334/radar" "Радар"',
        api_wait,
    )

    assert helper < recreate < api_wait < web_wait
    assert "for attempt in $(seq 1 30)" in runbook[helper:recreate]
    assert "curl --connect-timeout 2 --max-time 5 -fsS" in runbook[helper:recreate]
    assert "sleep 2" in runbook[helper:recreate]
    assert "done\n  return 1\n}" in runbook[helper:recreate]


def test_runbook_serializes_with_production_auto_update():
    runbook = (ROOT / "docs/release/early-warning-radar-wave1.md").read_text(
        encoding="utf-8"
    )
    auto_update = (ROOT / "deploy/auto-update.sh").read_text(encoding="utf-8")

    lock_fd = runbook.index('exec 8>".deploy-state/auto-update.lock"')
    lock_wait = runbook.index("flock -w 30 8", lock_fd)
    inspect_writers = runbook.index(
        'running_services="$(docker compose ps --status running --services)"'
    )

    assert lock_fd < lock_wait < inspect_writers
    assert '.deploy-state/auto-update.lock' in auto_update


def test_audit_output_uses_private_process_umask():
    source = (ROOT / "scripts/audit_radar_wave1.py").read_text(encoding="utf-8")

    assert "os.umask(0o077)" in source
