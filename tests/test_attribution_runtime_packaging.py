from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_temperature_image_contains_attribution_maintenance_commands():
    dockerfile = (ROOT / "Dockerfile.temperature").read_text(encoding="utf-8")

    for script in (
        "backfill_google_news_attribution.py",
        "recompute_attribution_window.py",
        "audit_google_news_attribution.py",
    ):
        assert script in dockerfile
