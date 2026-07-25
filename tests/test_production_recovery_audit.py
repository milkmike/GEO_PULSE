from datetime import datetime, timedelta, timezone

from scripts.audit_production_recovery import evaluate_recovery


NOW = datetime(2026, 7, 25, 20, 0, tzinfo=timezone.utc)


def _snapshot():
    return {
        "protected_counts": {
            "articles": 1_200_000,
            "analysis": 1_100_000,
            "temperature": 100_000,
        },
        "freshness": {
            "articles": (NOW - timedelta(minutes=5)).isoformat(),
            "analysis": (NOW - timedelta(minutes=10)).isoformat(),
            "stories": (NOW - timedelta(hours=2)).isoformat(),
        },
        "workers": {
            "tg-collector": {"restart_delta": 0},
            "embedding-worker": {"restart_delta": 0},
        },
        "api": {
            "/api/v2/countries": {"status": 200, "seconds": 1.2},
            "/api/v2/stories": {"status": 200, "seconds": 1.8},
        },
        "proxy_url": "http://geopulse:secret@85.192.31.89:18888",
    }


def test_recovery_audit_rejects_protected_count_decrease_and_stale_pipeline():
    before = _snapshot()
    after = _snapshot()
    after["protected_counts"]["articles"] -= 1
    after["freshness"]["analysis"] = (NOW - timedelta(hours=7)).isoformat()

    report = evaluate_recovery(before, after, now=NOW)

    assert report["ok"] is False
    assert "protected count decreased: articles" in report["failures"]
    assert "pipeline stale: analysis" in report["failures"]


def test_recovery_audit_redacts_credentials_and_accepts_healthy_snapshot():
    before = _snapshot()
    after = _snapshot()

    report = evaluate_recovery(before, after, now=NOW)
    rendered = str(report)

    assert report["ok"] is True
    assert "secret" not in rendered
    assert "***@" in rendered
