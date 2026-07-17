from scripts.audit_source_wave import evaluate_wave


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
