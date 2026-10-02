"""Persistent, source-bound early signal dossiers and screening decisions."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from sqlalchemy import text

from src.countries import COUNTRIES
from src.db import get_session
from src.early_signals import MECHANISM, SIGNAL, STAGE, source_key
from src.signal_hypotheses import validate_dossier


NOTICE = "Это версии развития событий, а не прогнозы. Сопоставляйте их с источниками и новыми фактами."
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_CLASSIFICATION_KEYS = {"signal", "mechanism", "stage", "country", "status"}
_MOSCOW = ZoneInfo("Europe/Moscow")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _instant(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(timezone.utc)


def _loaded(value):
    return json.loads(value) if isinstance(value, str) else value


def _valid_classification(value) -> bool:
    return (isinstance(value, dict) and set(value) == _CLASSIFICATION_KEYS
            and all(isinstance(value[key], str) for key in _CLASSIFICATION_KEYS)
            and value["signal"] in SIGNAL and value["mechanism"] in MECHANISM
            and value["stage"] in STAGE
            and value["country"] in (COUNTRIES.keys() | {"none", "unknown"})
            and value["status"] == "needs_review")


def save_dossier(draft: dict, evidence: list[dict], as_of: datetime,
                 release: str = "draft", review_note: str = "") -> str:
    """Save a validated snapshot; publishing requires an explicit editorial note."""
    if release not in ("draft", "published"):
        raise ValueError("invalid editorial release")
    if not isinstance(review_note, str) or len(review_note) > 2000 or "<" in review_note or ">" in review_note:
        raise ValueError("invalid review note")
    if release == "published" and not review_note.strip():
        raise ValueError("publication requires a review note")
    now = _instant(as_of)
    if now > _utcnow():
        raise ValueError("future dossier")
    validated = validate_dossier(draft, evidence, as_of=now)
    cited = validated["evidence"]
    fingerprint = validated["source_snapshot_sha256"]
    dossier_id = hashlib.sha256(_json({"draft": draft, "as_of": validated["as_of"],
                                       "source_snapshot_sha256": fingerprint}).encode()).hexdigest()
    with get_session() as session:
        session.execute(text("""
            INSERT INTO early_signal_dossiers
              (id,as_of,horizon_date,country_codes,source_snapshot_sha256,draft,evidence,
               release,review_note,published_at)
            VALUES (:id,:as_of,:horizon_date,:country_codes,:hash,CAST(:draft AS jsonb),
                    CAST(:evidence AS jsonb),:release,:review_note,:published_at)
            ON CONFLICT (id) DO UPDATE SET
              release=CASE WHEN EXCLUDED.release='published' THEN 'published'
                           ELSE early_signal_dossiers.release END,
              review_note=CASE WHEN EXCLUDED.release='published' THEN EXCLUDED.review_note
                               ELSE early_signal_dossiers.review_note END,
              published_at=CASE WHEN EXCLUDED.release='published'
                                THEN COALESCE(early_signal_dossiers.published_at,now())
                                ELSE early_signal_dossiers.published_at END
            WHERE early_signal_dossiers.source_snapshot_sha256=EXCLUDED.source_snapshot_sha256
              AND early_signal_dossiers.draft=EXCLUDED.draft
              AND early_signal_dossiers.evidence=EXCLUDED.evidence
            RETURNING id
        """), {"id": dossier_id, "as_of": now, "horizon_date": draft["horizon_date"],
               "country_codes": draft["country_codes"], "hash": fingerprint,
               "draft": _json(draft), "evidence": _json(cited), "release": release,
               "review_note": review_note, "published_at": _utcnow() if release == "published" else None}).scalar_one()
    return dossier_id


def _public_row(row: dict, cutoff: datetime) -> dict | None:
    """Recheck stored source bytes and temporal bounds before disclosure."""
    try:
        note = row["review_note"]
        if (row["release"] != "published" or not isinstance(note, str) or not note.strip()
                or len(note) > 2000 or "<" in note or ">" in note):
            return None
        stored_as_of = _instant(row["as_of"])
        if stored_as_of > cutoff:
            return None
        draft, evidence = _loaded(row["draft"]), _loaded(row["evidence"])
        verified = validate_dossier(draft, evidence, as_of=stored_as_of)
        if verified["source_snapshot_sha256"] != row["source_snapshot_sha256"]:
            return None
        expected_id = hashlib.sha256(_json({"draft": draft, "as_of": verified["as_of"],
                                            "source_snapshot_sha256": verified["source_snapshot_sha256"]}).encode()).hexdigest()
        if expected_id != row["id"] or row["country_codes"] != draft["country_codes"]:
            return None
        if date.fromisoformat(draft["horizon_date"]) < cutoff.astimezone(_MOSCOW).date():
            return None
        newest = max(_instant(article["published_at"]) for article in verified["evidence"])
        if newest > cutoff or newest < cutoff - timedelta(days=7):
            return None
        if any(_instant(article["collected_at"]) > cutoff for article in verified["evidence"]):
            return None
        return {"id": row["id"], "status": "needs_review", "headline_ru": draft["headline_ru"],
                "observations": draft["observations"], "interpretation_ru": draft["interpretation_ru"],
                "hypothesis_ru": draft["hypothesis_ru"], "opportunity_ru": draft["opportunity_ru"],
                "counterargument_ru": draft["counterargument_ru"], "watch": draft["watch"],
                "country_codes": draft["country_codes"], "horizon_date": draft["horizon_date"],
                "russia_link": verified["russia_link"], "as_of": verified["as_of"],
                "evidence": [{key: article[key] for key in
                              ("id", "title", "source_name", "url", "published_at", "collected_at")}
                             for article in verified["evidence"]],
                "review_note": note}
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def list_dossiers(country: str | None = None, limit: int = 6,
                  as_of: datetime | None = None) -> dict:
    if country is not None and country not in COUNTRIES:
        raise ValueError("invalid country")
    if type(limit) is not int or not 1 <= limit <= 12:
        raise ValueError("limit must be 1..12")
    cutoff = min(_instant(as_of), _utcnow()) if as_of is not None else _utcnow()
    with get_session() as session:
        rows = session.execute(text("""
            SELECT id,as_of,horizon_date,country_codes,source_snapshot_sha256,
                   draft,evidence,release,review_note
            FROM early_signal_dossiers
            WHERE release='published' AND as_of<=:cutoff AND horizon_date>=:day
              AND (CAST(:country AS text) IS NULL OR CAST(:country AS text)=ANY(country_codes))
            ORDER BY as_of DESC,id DESC LIMIT 120
        """), {"cutoff": cutoff, "day": cutoff.astimezone(_MOSCOW).date(),
               "country": country}).mappings().all()
    items = []
    for row in rows:
        item = _public_row(dict(row), cutoff)
        if item is not None and (country is None or country in item["country_codes"]):
            items.append(item)
            if len(items) >= limit:
                break
    return {"as_of": cutoff.isoformat(), "items": items, "notice": NOTICE}


def save_screenings(records: list[dict]) -> int:
    """Idempotently retain decisions keyed by exact screened source text."""
    if not isinstance(records, list) or len(records) > 80:
        raise ValueError("records must be a list of at most 80")
    saved = 0
    with get_session() as session:
        for record in records:
            article = record["article"]
            key = source_key(article)
            if record.get("source_key") != key:
                raise ValueError("screening source changed")
            snapshot = hashlib.sha256(_json(article).encode()).hexdigest()
            if record.get("snapshot_hash") not in (None, snapshot):
                raise ValueError("screening snapshot changed")
            if not _valid_classification(record.get("classification")):
                raise ValueError("invalid screening classification")
            session.execute(text("""
                INSERT INTO early_signal_screenings
                (source_key,article_id,snapshot_hash,article,classification)
                VALUES (:key,:id,:snapshot,CAST(:article AS jsonb),CAST(:classification AS jsonb))
                ON CONFLICT (source_key) DO NOTHING
            """), {"key": key, "id": article["id"], "snapshot": snapshot,
                   "article": _json(article), "classification": _json(record["classification"])})
            saved += 1
    return saved


def load_screenings(keys: list[str]) -> dict[str, dict]:
    if (not isinstance(keys, list) or len(keys) > 80
            or any(not isinstance(k, str) or not _HEX.fullmatch(k) for k in keys)):
        raise ValueError("invalid source keys")
    if not keys:
        return {}
    with get_session() as session:
        rows = session.execute(text("""
            SELECT source_key,snapshot_hash,article,classification FROM early_signal_screenings
            WHERE source_key=ANY(CAST(:keys AS char(64)[]))
        """), {"keys": keys}).mappings().all()
    result = {}
    for row in rows:
        try:
            article = _loaded(row["article"])
            key = str(row["source_key"]).strip()
            classification = _loaded(row["classification"])
            if (key not in keys or key != source_key(article)
                    or hashlib.sha256(_json(article).encode()).hexdigest() != row["snapshot_hash"]
                    or not _valid_classification(classification)):
                continue
            result[key] = {"article": article, "source_key": key,
                           "snapshot_hash": row["snapshot_hash"],
                           "classification": classification}
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            continue
    return result
