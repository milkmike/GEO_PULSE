from pathlib import Path

from scripts import audit_source_wave
from scripts.audit_source_wave import evaluate_wave


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_collector_image_contains_source_wave_audit_script():
    dockerfile = (REPO_ROOT / "Dockerfile.collector").read_text()

    assert "scripts/audit_source_wave.py" in dockerfile


def test_rollback_verifies_returned_row_count_before_commit():
    runbook = (REPO_ROOT / "docs/release/national-source-wave1.md").read_text()
    rollback = runbook.split("## Failed-source rollback", 1)[1]

    assertion = "SELECT :ROW_COUNT::integer = 1 AS exactly_one \\gset"
    assert "\\set ON_ERROR_STOP on" in rollback
    assert assertion in rollback
    assert rollback.index(assertion) < rollback.index("COMMIT;")
    assert "\\if :exactly_one" in rollback
    assert "ROLLBACK;" in rollback
    assert "  \\quit\n" in rollback


def test_production_audits_and_startup_do_not_start_dependencies():
    runbook = (REPO_ROOT / "docs/release/national-source-wave1.md").read_text()
    audit_commands = [
        line for line in runbook.splitlines()
        if "scripts/audit_source_wave.py" in line
    ]

    assert len(audit_commands) == 3
    assert all(
        "docker compose run --rm --no-deps -v" in command
        for command in audit_commands
    )
    assert "docker compose up -d --no-deps api collector" in runbook


def test_cli_fails_closed_when_wave_has_no_sources(monkeypatch):
    protected_counts = {
        "articles": 100,
        "analysis": 90,
        "temperature": 80,
        "signals": 70,
        "stories": 60,
    }
    monkeypatch.setattr(
        audit_source_wave,
        "load_snapshot",
        lambda wave: ([], protected_counts),
    )

    report = evaluate_wave([], protected_counts)
    assert report["wave_has_sources"] is False
    assert audit_source_wave.main(["--wave", "missing", "--json"]) == 1


def test_canary_rejects_fetch_timestamp_in_the_future():
    report = evaluate_wave(
        [{
            "source_id": 10,
            "name": "Future",
            "country_code": "AL",
            "last_status": "ok",
            "last_fetch_age_minutes": -1,
            "article_count": 1,
            "foreign_url_count": 0,
            "unsafe_geo_count": 0,
        }],
        protected_counts={"articles": 100, "analysis": 90, "temperature": 80,
                          "signals": 70, "stories": 60},
    )

    assert report["summary"] == {"total": 1, "passed": 0, "failed": 1}
    assert report["sources"][0]["reasons"] == ["fetch_not_recent"]


def test_canary_requires_fresh_ok_fetch_and_safe_attribution():
    report = evaluate_wave(
        [{
            "source_id": 10,
            "name": "RTSH",
            "country_code": "AL",
            "last_status": "ok",
            "last_fetch_age_minutes": 20,
            "article_count": 4,
            "foreign_url_count": 0,
            "unsafe_geo_count": 0,
        }],
        protected_counts={"articles": 100, "analysis": 90, "temperature": 80,
                          "signals": 70, "stories": 60},
    )

    assert report["summary"] == {"total": 1, "passed": 1, "failed": 0}


def test_canary_fails_closed_on_attribution_or_protected_count_loss():
    report = evaluate_wave(
        [{
            "source_id": 10,
            "name": "Bad",
            "country_code": "AL",
            "last_status": "ok",
            "last_fetch_age_minutes": 20,
            "article_count": 2,
            "foreign_url_count": 1,
            "unsafe_geo_count": 1,
        }],
        protected_counts={"articles": 99, "analysis": 90, "temperature": 80,
                          "signals": 70, "stories": 60},
        baseline_counts={"articles": 100, "analysis": 90, "temperature": 80,
                         "signals": 70, "stories": 60},
    )

    assert report["summary"]["failed"] == 1
    assert report["protected_counts_ok"] is False
