"""Public release keeps source snapshots intact and excludes stale or corrupt rows."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import early_signal_store as store
from src.early_signals import source_key
from src.api.main import app
from src.signal_hypotheses import validate_dossier

FIXTURE = json.loads((Path(__file__).parent / "fixtures/early_signal_example.json").read_text())
NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def sample_row():
    draft = deepcopy(FIXTURE["draft"])
    evidence = deepcopy(FIXTURE["context"]["articles"])
    as_of = datetime.fromisoformat(FIXTURE["context"]["as_of"])
    verified = validate_dossier(draft, evidence, as_of=as_of)
    import hashlib
    dossier_id = hashlib.sha256(store._json({"draft": draft, "as_of": verified["as_of"],
                                             "source_snapshot_sha256": verified["source_snapshot_sha256"]}).encode()).hexdigest()
    return {"id": dossier_id, "as_of": as_of, "horizon_date": draft["horizon_date"],
            "country_codes": draft["country_codes"],
            "source_snapshot_sha256": verified["source_snapshot_sha256"],
            "draft": draft, "evidence": verified["evidence"], "release": "published",
            "review_note": "Редактор проверил связь с источниками."}


def test_public_projection_excludes_excerpts_and_uses_event_country():
    row = sample_row()
    row["evidence"][0]["country_code"] = "PL"  # Publisher location differs from event.
    assert store._public_row(row, NOW) is None  # Snapshot has been altered.
    row = sample_row()
    result = store._public_row(row, NOW)
    assert result["country_codes"] == ["RS"]
    assert result["status"] == "needs_review"
    assert result["russia_link"] == "hypothesis"
    assert all("excerpt" not in source for source in result["evidence"])
    assert "excerpt" not in store._json(result)


def test_public_projection_rejects_corrupt_quote_future_and_expiry():
    row = sample_row()
    row["evidence"][0]["excerpt"] = "A changed article"
    assert store._public_row(row, NOW) is None
    row = sample_row()
    row["evidence"][0]["collected_at"] = (NOW + timedelta(days=1)).isoformat()
    assert store._public_row(row, NOW) is None
    row = sample_row()
    assert store._public_row(row, datetime(2026, 10, 12, tzinfo=timezone.utc)) is None
    row = sample_row()
    assert store._public_row(row, datetime(2026, 11, 1, tzinfo=timezone.utc)) is None
    row = sample_row()
    assert store._public_row(row, datetime(2026, 10, 31, 22, tzinfo=timezone.utc)) is None
    row = sample_row()
    row["review_note"] = ""
    assert store._public_row(row, NOW) is None


class _Result:
    def __init__(self, value):
        self.value = value
    def scalar_one(self):
        return self.value
    def all(self):
        return self.value
    def mappings(self):
        return self


class _Session:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []
    def execute(self, statement, params):
        self.calls.append(params)
        return _Result(self.rows if "SELECT" in str(statement) else params["id"])


def test_duplicate_save_has_stable_id_and_publication_requires_note(monkeypatch):
    from contextlib import contextmanager
    session = _Session()
    @contextmanager
    def fake_session():
        yield session
    monkeypatch.setattr(store, "get_session", fake_session)
    monkeypatch.setattr(store, "_utcnow", lambda: NOW)
    draft = deepcopy(FIXTURE["draft"])
    sources = deepcopy(FIXTURE["context"]["articles"])
    at = datetime.fromisoformat(FIXTURE["context"]["as_of"])
    first = store.save_dossier(draft, sources, as_of=at)
    second = store.save_dossier(draft, sources, as_of=at)
    assert first == second
    assert len(session.calls) == 2
    with pytest.raises(ValueError, match="review note"):
        store.save_dossier(draft, sources, as_of=at, release="published")
    assert store.save_dossier(draft, sources, as_of=at, release="published", review_note="Проверено") == first
    sources[0]["url"] = "http://127.0.0.1/private"
    with pytest.raises(ValueError, match="url"):
        store.save_dossier(draft, sources, as_of=at)


def test_api_country_validation_and_read_only_projection(monkeypatch):
    from src.api.routes import early_signals as route
    monkeypatch.setattr(route, "list_dossiers", lambda country=None, limit=6: {
        "as_of": NOW.isoformat(), "items": [], "notice": store.NOTICE,
        "country": country, "limit": limit})
    client = TestClient(app)
    assert client.get("/api/v2/early-signals?country=RS&limit=12").json()["country"] == "RS"
    assert client.get("/api/v2/early-signals?country=rs").json()["country"] == "RS"
    for query in ("country=ZZ", "country=RUS", "limit=0", "limit=13"):
        assert client.get(f"/api/v2/early-signals?{query}").status_code == 422


def test_screening_cache_skips_bad_rows_and_bounds_requests(monkeypatch):
    from contextlib import contextmanager
    import hashlib
    article = deepcopy(FIXTURE["context"]["articles"][0])
    key = source_key(article)
    classification = {"signal": "change", "mechanism": "education",
                      "stage": "proposal", "country": "RS", "status": "needs_review"}
    valid = {"source_key": key, "snapshot_hash": hashlib.sha256(store._json(article).encode()).hexdigest(),
             "article": article, "classification": classification}
    malformed = {**valid, "source_key": "f" * 64, "article": {"id": 1},
                 "classification": {"status": "published"}}
    session = _Session(rows=[malformed, valid])
    @contextmanager
    def fake_session():
        yield session
    monkeypatch.setattr(store, "get_session", fake_session)
    assert store.load_screenings([key]) == {key: valid}
    assert store.save_screenings([valid]) == 1
    with pytest.raises(ValueError, match="classification"):
        store.save_screenings([{**valid, "classification": {"status": "published"}}])
    with pytest.raises(ValueError):
        store.load_screenings([key] * 81)
    with pytest.raises(ValueError):
        store.save_screenings([valid] * 81)
