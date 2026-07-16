from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from scripts import backfill_google_news_attribution as backfill


NOW = datetime(2026, 7, 16, 8, tzinfo=timezone.utc)


class MemoryResult:
    def __init__(self, rows=(), *, rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def scalar_one(self):
        row = self.rows[0]
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row


class MemorySession:
    def __init__(self, backend, articles):
        self.backend = backend
        self.articles = articles
        self.mutations = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = dict(params or {})
        normalized = " ".join(sql.split())

        if "gnews-backfill:registry" in sql:
            rows = []
            for registry in self.backend.registry:
                source = self.backend.sources[registry["publisher_source_id"]]
                rows.append({
                    "domain": registry["domain"],
                    "publisher_source_id": registry["publisher_source_id"],
                    "country_code": registry["country_code"],
                    "source_name": source["name"],
                    "source_url": source["url"],
                })
            return MemoryResult(rows)

        if "gnews-backfill:invariant-counts" in sql:
            return MemoryResult([{
                "article_rows": len(self.articles),
                "analysis_rows": self.backend.analysis_rows,
                "story_article_rows": self.backend.story_article_rows,
            }])

        if "gnews-backfill:provenance" in sql:
            return MemoryResult([
                {
                    "id": article["id"],
                    "source_id": article["source_id"],
                    "external_id": article["external_id"],
                    "url": article["url"],
                }
                for article in sorted(self.articles, key=lambda item: item["id"])
            ])

        if "gnews-backfill:batch" in sql:
            rows = []
            for article in sorted(self.articles, key=lambda item: item["id"]):
                source = self.backend.sources[article["source_id"]]
                if source["config"].get("feed_mode") != "publisher_discovery":
                    continue
                if article["id"] <= params["last_id"]:
                    continue
                if article["published_at"] < NOW - timedelta(days=params["since_days"]):
                    continue
                rows.append({
                    **article,
                    "discovery_country_code": source["country_code"],
                })
            return MemoryResult(rows[: params["batch_size"]])

        if "gnews-backfill:update-classified" in sql:
            article = self._article(params["article_id"])
            desired = {
                "publisher_source_id": params["publisher_source_id"],
                "publisher_name": params["publisher_name"],
                "publisher_domain": params["publisher_domain"],
                "geo_country_code": params["country_code"],
                "geo_status": params["status"],
                "geo_method": "legacy_title_suffix",
                "geo_confidence": 1.0,
            }
            changed = any(article.get(key) != value for key, value in desired.items())
            if changed:
                article.update(desired)
                article["geo_verified_at"] = NOW
            self.mutations.append((normalized, params))
            return MemoryResult(rowcount=int(changed))

        if "gnews-backfill:update-unclassified" in sql:
            article = self._article(params["article_id"])
            changed = article.get("geo_status") != "legacy_unverified"
            if changed:
                article["geo_status"] = "legacy_unverified"
            self.mutations.append((normalized, params))
            return MemoryResult(rowcount=int(changed))

        if "gnews-backfill:dedup-candidates" in sql:
            return MemoryResult([
                {
                    "id": article["id"],
                    "publisher_id": (
                        article.get("publisher_source_id") or article["source_id"]
                    ),
                    "external_id": article["external_id"],
                    "title_normalized": article.get("title_normalized"),
                    "published_at": article["published_at"],
                    "is_duplicate": article.get("is_duplicate", False),
                    "duplicate_of": article.get("duplicate_of"),
                    "reprint_count": article.get("reprint_count", 0),
                }
                for article in self.articles
                if article.get("geo_status") in {
                    "source_verified", "publisher_verified", "publisher_reassigned"
                }
            ])

        if "gnews-backfill:update-duplicate" in sql:
            article = self._article(params["article_id"])
            desired = {
                "is_duplicate": params["is_duplicate"],
                "duplicate_of": params["duplicate_of"],
                "reprint_count": params["reprint_count"],
            }
            changed = any(article.get(key) != value for key, value in desired.items())
            if changed:
                article.update(desired)
            self.mutations.append((normalized, params))
            return MemoryResult(rowcount=int(changed))

        raise AssertionError(f"unexpected SQL: {normalized}")

    def _article(self, article_id):
        return next(item for item in self.articles if item["id"] == article_id)


class MemoryBackend:
    def __init__(self):
        self.sources = {
            1: {
                "id": 1,
                "name": "Google News (ES) — Россия",
                "url": "https://news.google.com/rss/search?q=Russia",
                "country_code": "ES",
                "config": {"feed_mode": "publisher_discovery"},
            },
            2: {
                "id": 2,
                "name": "EL PAÍS",
                "url": "https://elpais.com/rss",
                "country_code": "ES",
                "config": {},
            },
            3: {
                "id": 3,
                "name": "Reuters",
                "url": "https://reuters.com/rss",
                "country_code": "GB",
                "config": {},
            },
            4: {
                "id": 4,
                "name": "Wire",
                "url": "https://wire-us.example/rss",
                "country_code": "US",
                "config": {},
            },
            5: {
                "id": 5,
                "name": "Wire",
                "url": "https://wire-ca.example/rss",
                "country_code": "CA",
                "config": {},
            },
        }
        self.registry = [
            {"domain": "elpais.com", "publisher_source_id": 2,
             "country_code": "ES"},
            {"domain": "reuters.com", "publisher_source_id": 3,
             "country_code": "GB"},
            {"domain": "wire-us.example", "publisher_source_id": 4,
             "country_code": "US"},
            {"domain": "wire-ca.example", "publisher_source_id": 5,
             "country_code": "CA"},
        ]
        self.articles = [
            self._article(
                50, 2, "local-50", "https://elpais.com/local-50",
                "España negocia", "espana negocia", NOW - timedelta(hours=3),
                geo_status="source_verified",
            ),
            self._article(
                60, 3, "reuters-shared", "https://reuters.com/local-60",
                "Earlier Reuters copy", "earlier reuters copy",
                NOW - timedelta(hours=2), geo_status="source_verified",
            ),
            self._article(
                101, 1, "google-101", "https://news.google.com/101",
                "España negocia — EL PAÍS", "espana negocia",
                NOW - timedelta(hours=1), resolved_url="https://elpais.com/101",
            ),
            self._article(
                102, 1, "reuters-shared", "https://news.google.com/102",
                "Global update - Reuters", "global update",
                NOW - timedelta(minutes=50), resolved_url="https://reuters.com/102",
            ),
            self._article(
                103, 1, "google-103", "https://news.google.com/103",
                "Ambiguous report – Wire", "ambiguous report",
                NOW - timedelta(minutes=40),
            ),
        ]
        self.analysis_rows = 5
        self.story_article_rows = 4
        self.mutation_sql = []

    @staticmethod
    def _article(article_id, source_id, external_id, url, title,
                 title_normalized, published_at, *, resolved_url=None,
                 geo_status="unverified"):
        return {
            "id": article_id,
            "source_id": source_id,
            "external_id": external_id,
            "url": url,
            "resolved_url": resolved_url,
            "title": title,
            "title_normalized": title_normalized,
            "published_at": published_at,
            "publisher_source_id": None,
            "publisher_name": None,
            "publisher_domain": None,
            "geo_country_code": None,
            "geo_status": geo_status,
            "geo_method": None,
            "geo_confidence": None,
            "geo_verified_at": None,
            "is_duplicate": False,
            "duplicate_of": None,
            "reprint_count": 0,
        }

    @contextmanager
    def session_factory(self):
        working = deepcopy(self.articles)
        session = MemorySession(self, working)
        try:
            yield session
        except Exception:
            raise
        else:
            self.articles = working
            self.mutation_sql.extend(session.mutations)

    def article(self, article_id):
        return next(item for item in self.articles if item["id"] == article_id)


class InterruptingCheckpoint:
    def __init__(self):
        self.state = None
        self.interrupt = True
        self.interrupted = False

    def load(self):
        return deepcopy(self.state)

    def save(self, state):
        self.state = deepcopy(state)
        if (
            self.interrupt
            and not self.interrupted
            and int(state.get("last_id", 0)) > 0
        ):
            self.interrupted = True
            raise RuntimeError("simulated interruption after committed batch")


def _provenance(backend):
    return [
        (row["id"], row["source_id"], row["external_id"], row["url"])
        for row in sorted(backend.articles, key=lambda item: item["id"])
    ]


def test_dry_run_is_default_read_only_and_reports_exact_classification(
    monkeypatch, tmp_path,
):
    backend = MemoryBackend()
    before = deepcopy(backend.articles)
    checkpoint = tmp_path / "checkpoint.json"
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    report = backfill.run_backfill(
        apply=False,
        since_days=104,
        batch_size=2,
        checkpoint=checkpoint,
    )

    assert report.updated == 0
    assert report.classifiable == 2
    assert report.unclassified == 1
    assert report.scanned == 3
    assert backend.articles == before
    assert not checkpoint.exists()
    assert report.counts["discovery_country"] == {"ES": 3}
    assert report.counts["publisher_country"] == {"ES": 1, "GB": 1}
    assert report.counts["status"] == {
        "legacy_unverified": 1,
        "publisher_reassigned": 1,
        "publisher_verified": 1,
    }
    assert report.counts["domain"] == {
        "(unknown)": 1,
        "elpais.com": 1,
        "reuters.com": 1,
    }
    assert report.invariants["passed"] is True


def test_apply_resumes_after_committed_batch_and_reruns_are_idempotent(monkeypatch):
    backend = MemoryBackend()
    checkpoint = InterruptingCheckpoint()
    before_provenance = _provenance(backend)
    before_rows = len(backend.articles)
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        backfill.run_backfill(
            apply=True,
            since_days=104,
            batch_size=2,
            checkpoint=checkpoint,
        )

    assert checkpoint.state["last_id"] == 102
    assert backend.article(101)["publisher_source_id"] == 2
    assert backend.article(102)["publisher_source_id"] == 3
    assert backend.article(103)["geo_status"] == "unverified"

    checkpoint.interrupt = False
    resumed = backfill.run_backfill(
        apply=True,
        since_days=104,
        batch_size=2,
        checkpoint=checkpoint,
    )

    assert resumed.done is True
    assert backend.article(103)["geo_status"] == "legacy_unverified"
    assert backend.article(101)["is_duplicate"] is True
    assert backend.article(101)["duplicate_of"] == 50
    assert backend.article(50)["reprint_count"] == 1
    assert backend.article(102)["is_duplicate"] is True
    assert backend.article(102)["duplicate_of"] == 60
    assert backend.article(60)["reprint_count"] == 1
    assert len(backend.articles) == before_rows
    assert _provenance(backend) == before_provenance
    assert resumed.invariants["passed"] is True

    verified_at = {
        article_id: backend.article(article_id)["geo_verified_at"]
        for article_id in (101, 102)
    }
    second = backfill.run_backfill(
        apply=True,
        since_days=104,
        batch_size=2,
        checkpoint=checkpoint,
    )
    assert second.updated == 0
    assert second.duplicates_updated == 0

    fresh_checkpoint = InterruptingCheckpoint()
    fresh_checkpoint.interrupt = False
    fresh = backfill.run_backfill(
        apply=True,
        since_days=104,
        batch_size=2,
        checkpoint=fresh_checkpoint,
    )
    assert fresh.updated == 0
    assert fresh.duplicates_updated == 0
    assert {
        article_id: backend.article(article_id)["geo_verified_at"]
        for article_id in (101, 102)
    } == verified_at
    assert backend.article(103)["geo_status"] == "legacy_unverified"
    assert all("DELETE " not in sql.upper() for sql, _ in backend.mutation_sql)
    for sql, _ in backend.mutation_sql:
        set_clause = sql.partition(" SET ")[2].partition(" WHERE ")[0]
        for forbidden in ("source_id", "external_id", "url"):
            assert not set_clause.startswith(f"{forbidden} =")
            assert f", {forbidden} =" not in set_clause


def test_cli_surface_is_dry_run_first_and_report_is_json(tmp_path):
    parser = backfill.build_parser()

    assert parser.parse_args([]).apply is False
    assert parser.parse_args(["--apply"]).apply is True
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }
    assert option_strings == {
        "--apply", "--since-days", "--batch-size", "--checkpoint", "--report"
    }

    report_path = tmp_path / "report.json"
    report = backfill.BackfillReport(
        mode="dry-run",
        scanned=0,
        classifiable=0,
        unclassified=0,
        updated=0,
        duplicates_updated=0,
        batches=0,
        last_id=0,
        done=True,
        counts={},
        invariants={"passed": True},
    )
    backfill.write_report(report_path, report)

    assert json.loads(report_path.read_text(encoding="utf-8"))["mode"] == "dry-run"


def test_batch_sql_is_keyset_bounded_and_mutations_preserve_provenance():
    compact = " ".join(backfill.ARTICLE_BATCH_SQL.split())

    assert "source.config->>'feed_mode' = 'publisher_discovery'" in compact
    assert "article.published_at >= NOW() - make_interval(days => :since_days)" in compact
    assert "article.id > :last_id" in compact
    assert "ORDER BY article.id" in compact
    assert "LIMIT :batch_size" in compact
    mutation_sql = " ".join(
        (backfill.CLASSIFIED_UPDATE_SQL, backfill.UNCLASSIFIED_UPDATE_SQL)
    ).upper()
    assert "DELETE " not in mutation_sql
    for column in ("SOURCE_ID", "EXTERNAL_ID", "URL"):
        assert f"SET {column}" not in mutation_sql
        assert f", {column}" not in mutation_sql
