import json
from pathlib import Path

import pytest

from scripts import audit_source_wave
from scripts.audit_source_wave import evaluate_wave


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PROTECTED_TABLES = (
    "articles",
    "article_discoveries",
    "publisher_domains",
    "analysis",
    "temperature",
    "signals",
    "signal_evidence",
    "briefs",
    "stories",
    "story_articles",
    "story_countries",
    "story_entities",
    "story_events",
    "content_embeddings",
)
WAVE1 = "2026-07-17-rss-1"
EXPECTED_WAVE1_IDENTITIES = (
    ("AL", "RTSH", "https://rtsh.al/feed/", "rtsh.al"),
    ("AL", "Reporter.al", "https://reporter.al/feed/", "reporter.al"),
    ("CY", "Philenews", "https://www.philenews.com/feed/", "philenews.com"),
    ("CY", "Politis", "https://www.politis.com.cy/feed/", "politis.com.cy"),
    ("DK", "Politiken", "https://politiken.dk/rss/senestenyt.rss", "politiken.dk"),
    ("DK", "Information", "https://www.information.dk/feed", "information.dk"),
    ("IE", "The Irish Times", "https://www.irishtimes.com/arc/outboundfeeds/rss/category/ireland/?outputType=xml", "irishtimes.com"),
    ("IE", "TheJournal.ie", "https://www.thejournal.ie/feed/", "thejournal.ie"),
    ("ME", "RTCG", "https://rtcg.me/vijesti/rss.html", "rtcg.me"),
    ("ME", "Vijesti", "https://www.vijesti.me/rss", "vijesti.me"),
    ("MK", "MRT", "https://www.mrt.com.mk/rss.xml", "mrt.com.mk"),
    ("MK", "Meta.mk", "https://meta.mk/feed/", "meta.mk"),
    ("PT", "Diário de Notícias", "https://www.dn.pt/api/v1/collections/ultimas.rss", "dn.pt"),
    ("PT", "Observador", "https://observador.pt/feed/", "observador.pt"),
    ("SG", "CNA Singapore", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416", "channelnewsasia.com"),
    ("SI", "RTV Slovenija", "https://www.rtvslo.si/feeds/01.xml", "rtvslo.si"),
    ("SI", "N1 Slovenija", "https://n1info.si/feed/", "n1info.si"),
)


def _healthy_row(identity, source_id):
    country_code, name, url, publisher_domain = identity
    return {
        "source_id": source_id,
        "name": name,
        "country_code": country_code,
        "url": url,
        "publisher_domain": publisher_domain,
        "last_status": "ok",
        "last_fetch_age_minutes": 20,
        "article_count": 4,
        "foreign_url_count": 0,
        "unsafe_geo_count": 0,
    }


def _healthy_wave_rows():
    return [
        _healthy_row(identity, source_id)
        for source_id, identity in enumerate(EXPECTED_WAVE1_IDENTITIES, start=1)
    ]


def test_audit_protects_all_persistent_user_and_global_data():
    assert audit_source_wave.PROTECTED_TABLES == EXPECTED_PROTECTED_TABLES


def test_wave1_audit_contract_pins_exact_17_source_identities():
    assert audit_source_wave.WAVE_SOURCE_IDENTITIES == {
        WAVE1: EXPECTED_WAVE1_IDENTITIES,
    }


@pytest.mark.parametrize("decreased_table", EXPECTED_PROTECTED_TABLES)
def test_canary_rejects_a_decrease_in_every_protected_table(decreased_table):
    baseline_counts = dict.fromkeys(EXPECTED_PROTECTED_TABLES, 10)
    protected_counts = baseline_counts.copy()
    protected_counts[decreased_table] -= 1

    report = evaluate_wave([], protected_counts, baseline_counts)

    assert report["protected_counts_ok"] is False


def test_canary_fails_closed_with_action_when_baseline_is_from_older_audit():
    protected_counts = dict.fromkeys(EXPECTED_PROTECTED_TABLES, 10)
    old_baseline_counts = {
        "articles": 10,
        "analysis": 10,
        "temperature": 10,
        "signals": 10,
        "stories": 10,
    }

    report = evaluate_wave([], protected_counts, old_baseline_counts)

    assert report["protected_counts_ok"] is False
    assert "article_discoveries" in " ".join(report["protected_count_errors"])
    assert "refresh baseline" in " ".join(report["protected_count_errors"])


def test_collector_image_contains_source_wave_release_scripts():
    dockerfile = (REPO_ROOT / "Dockerfile.collector").read_text()

    assert "scripts/audit_source_wave.py" in dockerfile
    assert "scripts/validate_source_candidates.py" in dockerfile


def test_rollback_verifies_returned_row_count_before_commit():
    runbook = (REPO_ROOT / "docs/release/national-source-wave1.md").read_text()
    rollback = runbook.split("## Failed-source rollback", 1)[1]

    assertion = "SELECT :ROW_COUNT::integer = 1 AS exactly_one \\gset"
    assert "\\set ON_ERROR_STOP on" in rollback
    assert assertion in rollback
    assert rollback.index(assertion) < rollback.index("COMMIT;")
    assert "\\if :exactly_one" in rollback
    assert "ROLLBACK;" in rollback
    assert "  \\quit 1\n" in rollback


def test_local_release_gate_uses_worktree_safe_interpreter():
    runbook = (REPO_ROOT / "docs/release/national-source-wave1.md").read_text()

    assert "git rev-parse --path-format=absolute --git-common-dir" in runbook
    assert '"$PYTHON" -m pytest -q' in runbook


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
    assert "--baseline-only" in audit_commands[0]
    assert "docker compose up -d --no-deps api collector" in runbook


def test_production_promotion_preflight_uses_read_only_inventory():
    runbook = (REPO_ROOT / "docs/release/national-source-wave1.md").read_text()

    production = runbook.split("# Production baseline; read-only", 1)[1]
    assert "SELECT json_build_object" in runbook
    assert "> backups/production-source-inventory.json" in runbook
    assert "--promotion-preflight" in runbook
    assert "--production-inventory /app/backups/production-source-inventory.json" in runbook
    assert "docker compose run --rm --no-deps" in runbook
    assert "set -euo pipefail" in production
    assert production.index("--promotion-preflight") < production.index("--baseline-only")
    assert production.index("--baseline-only") < production.index(
        "docker compose up -d --no-deps api collector"
    )


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


def test_baseline_only_succeeds_without_wave_sources_and_writes_snapshot(
    monkeypatch, tmp_path
):
    protected_counts = dict.fromkeys(EXPECTED_PROTECTED_TABLES, 10)
    monkeypatch.setattr(
        audit_source_wave,
        "load_snapshot",
        lambda wave: ([], protected_counts),
    )
    output_path = tmp_path / "baseline.json"

    exit_code = audit_source_wave.main([
        "--wave", WAVE1,
        "--baseline-only",
        "--json",
        "--out", str(output_path),
    ])

    report = json.loads(output_path.read_text())
    assert exit_code == 0
    assert report["schema_version"] == 1
    assert report["wave"] == WAVE1
    assert report["expected_source_identities"] == [
        {
            "country_code": country_code,
            "name": name,
            "url": url,
            "publisher_domain": publisher_domain,
        }
        for country_code, name, url, publisher_domain
        in EXPECTED_WAVE1_IDENTITIES
    ]
    assert report["wave_has_sources"] is False
    assert report["protected_counts"] == protected_counts
    assert report["protected_counts_ok"] is True


@pytest.mark.parametrize(
    "protected_counts",
    [
        pytest.param(
            {name: 10 for name in EXPECTED_PROTECTED_TABLES if name != "briefs"},
            id="incomplete",
        ),
        pytest.param(
            {**dict.fromkeys(EXPECTED_PROTECTED_TABLES, 10), "briefs": "10"},
            id="malformed",
        ),
    ],
)
def test_baseline_only_fails_closed_on_invalid_protected_counts(
    monkeypatch, protected_counts
):
    monkeypatch.setattr(
        audit_source_wave,
        "load_snapshot",
        lambda wave: ([], protected_counts),
    )

    assert audit_source_wave.main([
        "--wave", WAVE1,
        "--baseline-only",
        "--json",
    ]) == 1


def test_baseline_only_and_comparison_baseline_are_mutually_exclusive(tmp_path):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")

    with pytest.raises(SystemExit) as error:
        audit_source_wave.main([
            "--wave", "wave",
            "--baseline-only",
            "--baseline", str(baseline),
        ])

    assert error.value.code == 2


@pytest.mark.parametrize(
    "mutate_rows",
    [
        pytest.param(lambda rows: rows[:1], id="one-of-seventeen"),
        pytest.param(lambda rows: rows[:-1], id="sixteen-of-seventeen"),
        pytest.param(
            lambda rows: rows + [{**rows[0], "source_id": 99}],
            id="extra-duplicate",
        ),
        pytest.param(
            lambda rows: [{**rows[0], "url": "https://wrong.example/feed"}, *rows[1:]],
            id="wrong-identity",
        ),
    ],
)
def test_normal_canary_requires_exact_wave1_rows(monkeypatch, mutate_rows):
    protected_counts = dict.fromkeys(EXPECTED_PROTECTED_TABLES, 10)
    rows = mutate_rows(_healthy_wave_rows())
    monkeypatch.setattr(
        audit_source_wave,
        "load_snapshot",
        lambda wave: (rows, protected_counts),
    )

    assert audit_source_wave.main(["--wave", WAVE1, "--json"]) == 1


def test_normal_canary_accepts_exact_wave1_rows(monkeypatch):
    protected_counts = dict.fromkeys(EXPECTED_PROTECTED_TABLES, 10)
    monkeypatch.setattr(
        audit_source_wave,
        "load_snapshot",
        lambda wave: (_healthy_wave_rows(), protected_counts),
    )

    assert audit_source_wave.main(["--wave", WAVE1, "--json"]) == 0


@pytest.mark.parametrize(
    ("baseline_patch", "expected_error"),
    [
        pytest.param({"schema_version": 999}, "baseline_schema_version", id="schema"),
        pytest.param({"wave": "2026-07-18-rss-2"}, "baseline_wave_mismatch", id="cross-wave"),
        pytest.param(
            {"expected_source_identities": []},
            "baseline_expected_source_identities_mismatch",
            id="identity-contract",
        ),
    ],
)
def test_comparison_baseline_validates_schema_wave_and_identity_contract(
    monkeypatch, tmp_path, capsys, baseline_patch, expected_error
):
    protected_counts = dict.fromkeys(EXPECTED_PROTECTED_TABLES, 10)
    monkeypatch.setattr(
        audit_source_wave,
        "load_snapshot",
        lambda wave: (_healthy_wave_rows(), protected_counts),
    )
    baseline_path = tmp_path / "baseline.json"
    baseline = {
        "schema_version": 1,
        "wave": WAVE1,
        "expected_source_identities": [
            {
                "country_code": country_code,
                "name": name,
                "url": url,
                "publisher_domain": publisher_domain,
            }
            for country_code, name, url, publisher_domain
            in EXPECTED_WAVE1_IDENTITIES
        ],
        "protected_counts": protected_counts,
    }
    baseline.update(baseline_patch)
    baseline_path.write_text(json.dumps(baseline))

    assert audit_source_wave.main([
        "--wave", WAVE1,
        "--baseline", str(baseline_path),
        "--json",
    ]) == 1
    report = json.loads(capsys.readouterr().out)
    assert any(
        expected_error in error for error in report["baseline_errors"]
    )


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
