from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from scripts import backfill_google_news_attribution as backfill


NOW = datetime(2026, 7, 16, 8, tzinfo=timezone.utc)


LEGACY_DEDUP_CANDIDATES_SQL = """
    /* test-only: legacy gnews-backfill:dedup-candidates */
    WITH affected AS (
        SELECT article.id, publisher.id AS publisher_id,
               publisher.country_code AS country_code,
               article.external_id, article.title_normalized,
               article.published_at
        FROM articles article
        JOIN sources discovery ON discovery.id = article.source_id
        JOIN sources publisher ON publisher.id = CASE
            WHEN COALESCE(
                discovery.config->>'feed_mode', 'publisher'
            ) = 'publisher_discovery'
                THEN article.publisher_source_id
            ELSE COALESCE(article.publisher_source_id, article.source_id)
        END
        WHERE article.id = ANY(CAST(:affected_ids AS INTEGER[]))
          AND article.geo_status IN (
              'source_verified', 'publisher_verified', 'publisher_reassigned'
          )
    )
    SELECT DISTINCT candidate.id,
           candidate_publisher.id AS publisher_id,
           candidate_publisher.country_code AS country_code,
           candidate.external_id, candidate.title_normalized,
           candidate.published_at, candidate.is_duplicate,
           candidate.duplicate_of, candidate.reprint_count
    FROM articles candidate
    JOIN sources candidate_discovery
      ON candidate_discovery.id = candidate.source_id
    JOIN sources candidate_publisher ON candidate_publisher.id = CASE
        WHEN COALESCE(
            candidate_discovery.config->>'feed_mode', 'publisher'
        ) = 'publisher_discovery'
            THEN candidate.publisher_source_id
        ELSE COALESCE(candidate.publisher_source_id, candidate.source_id)
    END
    JOIN affected ON (
        candidate.id = affected.id
        OR (
            candidate.external_id IS NOT NULL
            AND candidate.external_id <> ''
            AND candidate.external_id = affected.external_id
            AND candidate_publisher.id = affected.publisher_id
        )
        OR (
            candidate.title_normalized IS NOT NULL
            AND candidate.title_normalized <> ''
            AND candidate.title_normalized = affected.title_normalized
            AND candidate_publisher.country_code = affected.country_code
            AND candidate.published_at BETWEEN
                affected.published_at - INTERVAL '48 hours'
                AND affected.published_at + INTERVAL '48 hours'
        )
    )
    WHERE candidate.geo_status IN (
        'source_verified', 'publisher_verified', 'publisher_reassigned'
    )
    ORDER BY candidate.id
"""


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

        if "gnews-backfill:article-high-water" in sql:
            return MemoryResult([{
                "max_article_id": max(
                    (article["id"] for article in self.articles),
                    default=0,
                ),
            }])

        if "gnews-backfill:invariant-counts" in sql:
            self.backend.invariant_snapshot_count += 1
            if self.backend.invariant_snapshot_hook is not None:
                self.backend.invariant_snapshot_hook(
                    self,
                    self.backend.invariant_snapshot_count,
                )
            return MemoryResult([{
                "article_rows": len(self.articles),
                "protected_article_rows": sum(
                    article["id"] <= params["max_article_id"]
                    for article in self.articles
                ),
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
                if article["id"] <= params["max_article_id"]
            ])

        if "gnews-backfill:batch" in sql:
            rows = []
            protected_predicate = (
                "article.geo_status IN (" in sql
                and "article.publisher_source_id IS NULL" in sql
                and "article.geo_method IS NULL" in sql
            )
            for article in sorted(self.articles, key=lambda item: item["id"]):
                source = self.backend.sources[article["source_id"]]
                if source["config"].get("feed_mode") != "publisher_discovery":
                    continue
                if protected_predicate and not (
                    article.get("publisher_source_id") is None
                    and article.get("geo_method") is None
                    and article.get("geo_verified_at") is None
                    and article.get("geo_status") in {
                        "source_verified", "unverified", "legacy_unverified",
                    }
                ):
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
                if any(
                    other["id"] != article["id"]
                    and article.get("external_id") is not None
                    and other.get("publisher_source_id")
                    == params["publisher_source_id"]
                    and other.get("external_id") == article.get("external_id")
                    for other in self.articles
                ):
                    raise RuntimeError(
                        "uq_articles_publisher_external_id unique violation"
                    )
                article.update(desired)
                article["geo_verified_at"] = NOW
            self.mutations.append((normalized, params))
            return MemoryResult(rowcount=int(changed))

        if "gnews-backfill:publisher-external-conflict" in sql:
            rows = [
                {"id": article["id"]}
                for article in sorted(self.articles, key=lambda item: item["id"])
                if article["id"] != params["article_id"]
                and article.get("publisher_source_id")
                == params["publisher_source_id"]
                and article.get("external_id") == params["external_id"]
            ]
            return MemoryResult(rows[:1])

        if "gnews-backfill:duplicate-family" in sql:
            seeds = set(params["seed_ids"])
            family = set(seeds)
            changed = True
            while changed:
                changed = False
                for article in self.articles:
                    if (
                        article.get("duplicate_of") in family
                        and article["id"] not in family
                    ):
                        family.add(article["id"])
                        changed = True
                    if (
                        article["id"] in family
                        and article.get("duplicate_of") is not None
                        and article["duplicate_of"] not in family
                    ):
                        family.add(article["duplicate_of"])
                        changed = True
            return MemoryResult([
                {"id": article["id"]}
                for article in sorted(self.articles, key=lambda item: item["id"])
                if article["id"] in family
            ])

        if "gnews-backfill:update-unclassified" in sql:
            changed = 0
            for article_id in params["article_ids"]:
                article = self._article(article_id)
                eligible = (
                    article.get("publisher_source_id") is None
                    and article.get("geo_method") is None
                    and article.get("geo_verified_at") is None
                    and article.get("geo_status") in {
                        "source_verified", "unverified", "legacy_unverified",
                    }
                )
                if eligible and article.get("geo_status") != "legacy_unverified":
                    article["geo_status"] = "legacy_unverified"
                    changed += 1
            self.mutations.append((normalized, params))
            return MemoryResult(rowcount=changed)

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
                    "country_code": (
                        article.get("geo_country_code")
                        or self.backend.sources[
                            article.get("publisher_source_id")
                            or article["source_id"]
                        ]["country_code"]
                    ),
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
        self.invariant_snapshot_count = 0
        self.invariant_snapshot_hook = None
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


def _append_article(backend, *args, **kwargs):
    article = backend._article(*args, **kwargs)
    backend.articles.append(article)
    return article


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


def test_apply_bulk_updates_500_unclassified_rows_once_per_batch(monkeypatch):
    backend = MemoryBackend()
    backend.articles = [
        backend._article(
            article_id,
            1,
            f"google-{article_id}",
            f"https://news.google.com/{article_id}",
            f"Ambiguous report {article_id} - Wire",
            f"ambiguous report {article_id}",
            NOW - timedelta(minutes=1),
        )
        for article_id in range(1_000, 1_500)
    ]
    checkpoint = InterruptingCheckpoint()
    checkpoint.interrupt = False
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    report = backfill.run_backfill(
        apply=True,
        since_days=104,
        batch_size=500,
        checkpoint=checkpoint,
    )

    unclassified_updates = [
        params
        for sql, params in backend.mutation_sql
        if "gnews-backfill:update-unclassified" in sql
    ]
    assert len(unclassified_updates) == 1
    assert unclassified_updates[0]["article_ids"] == list(range(1_000, 1_500))
    assert report.updated == 500
    assert report.unclassified == 500
    assert all(
        article["geo_status"] == "legacy_unverified"
        for article in backend.articles
    )


def test_invariants_allow_concurrent_appends_after_starting_high_water(monkeypatch):
    backend = MemoryBackend()

    def append_live_rows(session, snapshot_number):
        if snapshot_number != 2:
            return
        session.articles.append(backend._article(
            999, 2, "live-999", "https://elpais.com/live-999",
            "Live article", "live article", NOW,
            geo_status="source_verified",
        ))
        backend.analysis_rows += 1
        backend.story_article_rows += 1

    backend.invariant_snapshot_hook = append_live_rows
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    report = backfill.run_backfill(apply=False, since_days=104, batch_size=2)

    assert report.invariants["passed"] is True
    assert report.invariants["before"]["max_article_id"] == 103
    assert report.invariants["before"]["article_rows"] == 5
    assert report.invariants["before"]["protected_article_rows"] == 5
    assert report.invariants["after"]["article_rows"] == 6
    assert report.invariants["after"]["protected_article_rows"] == 5
    assert report.invariants["after"]["analysis_rows"] == 6
    assert report.invariants["after"]["story_article_rows"] == 5
    assert (
        report.invariants["after"]["provenance_sha256"]
        == report.invariants["before"]["provenance_sha256"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "delete_and_replace",
        "rewrite_provenance",
        "analysis_decrease",
        "story_article_decrease",
    ],
)
def test_invariants_still_reject_protected_mutations_and_count_decreases(
    monkeypatch, mutation,
):
    backend = MemoryBackend()

    def mutate_live_rows(session, snapshot_number):
        if snapshot_number != 2:
            return
        if mutation == "delete_and_replace":
            session.articles[:] = [
                article for article in session.articles if article["id"] != 50
            ]
            session.articles.append(backend._article(
                999, 2, "replacement-999", "https://elpais.com/replacement-999",
                "Replacement", "replacement", NOW,
                geo_status="source_verified",
            ))
        elif mutation == "rewrite_provenance":
            article = session._article(50)
            article.update({
                "source_id": 3,
                "external_id": "rewritten-50",
                "url": "https://reuters.com/rewritten-50",
            })
        elif mutation == "analysis_decrease":
            backend.analysis_rows -= 1
        else:
            backend.story_article_rows -= 1

    backend.invariant_snapshot_hook = mutate_live_rows
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    with pytest.raises(
        RuntimeError,
        match="backfill changed protected row counts or provenance",
    ):
        backfill.run_backfill(apply=False, since_days=104, batch_size=2)


def test_checkpoint_resume_keeps_original_high_water_while_rows_append(monkeypatch):
    backend = MemoryBackend()
    checkpoint = InterruptingCheckpoint()
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    with pytest.raises(RuntimeError, match="simulated interruption"):
        backfill.run_backfill(
            apply=True,
            since_days=104,
            batch_size=2,
            checkpoint=checkpoint,
        )

    original_before = deepcopy(checkpoint.state["before_invariants"])
    assert checkpoint.state["version"] == 2
    assert original_before["max_article_id"] == 103

    _append_article(
        backend, 999, 2, "live-999", "https://elpais.com/live-999",
        "Live article", "live article", NOW,
        geo_status="source_verified",
    )
    backend.analysis_rows += 1
    backend.story_article_rows += 1
    checkpoint.interrupt = False

    resumed = backfill.run_backfill(
        apply=True,
        since_days=104,
        batch_size=2,
        checkpoint=checkpoint,
    )

    assert resumed.invariants["passed"] is True
    assert resumed.invariants["before"] == original_before
    assert resumed.invariants["after"]["max_article_id"] == 103
    assert resumed.invariants["after"]["article_rows"] == 6
    assert resumed.invariants["after"]["protected_article_rows"] == 5
    assert resumed.invariants["after"]["analysis_rows"] == 6
    assert resumed.invariants["after"]["story_article_rows"] == 5


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
    assert resumed.scanned == 3
    assert resumed.classifiable == 2
    assert resumed.unclassified == 1
    assert resumed.counts["status"] == {
        "legacy_unverified": 1,
        "publisher_reassigned": 1,
        "publisher_verified": 1,
    }
    assert resumed.matrix == {"ES": {"ES": 1, "GB": 1}}
    assert checkpoint.state["audit"]["scanned"] == 3
    assert checkpoint.state["audit"]["matrix"] == {
        "ES": {"ES": 1, "GB": 1},
    }
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
    assert second == resumed

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


def test_apply_only_repairs_unresolved_legacy_rows(monkeypatch):
    backend = MemoryBackend()
    legacy = _append_article(
        backend, 104, 1, "legacy-104", "https://news.google.com/104",
        "Legacy row - Reuters", "legacy row", NOW - timedelta(minutes=30),
        geo_status="source_verified",
    )
    verified = _append_article(
        backend, 105, 1, "stage1-105", "https://news.google.com/105",
        "Stage one must survive – Wire", "stage one verified",
        NOW - timedelta(minutes=20), geo_status="publisher_verified",
    )
    verified.update({
        "publisher_source_id": 2,
        "publisher_name": "EL PAÍS",
        "publisher_domain": "elpais.com",
        "geo_country_code": "ES",
        "geo_method": "publisher_domain_registry",
        "geo_confidence": 1.0,
        "geo_verified_at": NOW - timedelta(minutes=20),
    })
    reassigned = _append_article(
        backend, 106, 1, "stage1-106", "https://news.google.com/106",
        "Stage one must survive — EL PAÍS", "stage one reassigned",
        NOW - timedelta(minutes=10), geo_status="publisher_reassigned",
    )
    reassigned.update({
        "publisher_source_id": 3,
        "publisher_name": "Reuters",
        "publisher_domain": "reuters.com",
        "geo_country_code": "GB",
        "geo_method": "publisher_domain_registry",
        "geo_confidence": 1.0,
        "geo_verified_at": NOW - timedelta(minutes=10),
    })
    protected_before = {105: deepcopy(verified), 106: deepcopy(reassigned)}
    checkpoint = InterruptingCheckpoint()
    checkpoint.interrupt = False
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    report = backfill.run_backfill(
        apply=True, since_days=104, batch_size=20, checkpoint=checkpoint,
    )

    assert report.scanned == 4
    assert legacy["publisher_source_id"] is None
    assert backend.article(104)["publisher_source_id"] == 3
    assert backend.article(105) == protected_before[105]
    assert backend.article(106) == protected_before[106]


def test_exact_publisher_external_collision_fails_closed_before_update(monkeypatch):
    backend = MemoryBackend()
    backend.sources[6] = {
        "id": 6,
        "name": "Google News (GB) — Россия",
        "url": "https://news.google.com/rss/search?q=Russia&hl=en-GB",
        "country_code": "GB",
        "config": {"feed_mode": "publisher_discovery"},
    }
    parent = _append_article(
        backend, 40, 3, "canonical-parent", "https://reuters.com/40",
        "Collision", "collision", NOW - timedelta(minutes=40),
        geo_status="source_verified",
    )
    parent["geo_country_code"] = "GB"
    first = _append_article(
        backend, 104, 6, "publisher-shared", "https://news.google.com/104",
        "Collision - Reuters", "collision", NOW - timedelta(minutes=30),
    )
    second = _append_article(
        backend, 105, 1, "publisher-shared", "https://news.google.com/105",
        "Collision - Reuters", "collision", NOW - timedelta(minutes=20),
    )
    checkpoint = InterruptingCheckpoint()
    checkpoint.interrupt = False
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    report = backfill.run_backfill(
        apply=True, since_days=104, batch_size=20, checkpoint=checkpoint,
    )

    assert report.done is True
    assert backend.article(first["id"])["publisher_source_id"] == 3
    assert backend.article(second["id"])["publisher_source_id"] is None
    assert backend.article(second["id"])["geo_status"] == "legacy_unverified"
    assert backend.article(second["id"])["is_duplicate"] is True
    assert backend.article(first["id"])["duplicate_of"] == parent["id"]
    assert backend.article(second["id"])["duplicate_of"] == parent["id"]
    assert backend.article(parent["id"])["reprint_count"] == 2


def test_empty_publisher_external_collision_is_not_treated_as_missing(monkeypatch):
    backend = MemoryBackend()
    backend.sources[6] = {
        "id": 6,
        "name": "Google News (GB) — Россия",
        "url": "https://news.google.com/rss/search?q=Russia&hl=en-GB",
        "country_code": "GB",
        "config": {"feed_mode": "publisher_discovery"},
    }
    first = _append_article(
        backend, 104, 6, "", "https://news.google.com/empty-104",
        "First empty ID - Reuters", "first empty id",
        NOW - timedelta(minutes=30),
    )
    second = _append_article(
        backend, 105, 1, "", "https://news.google.com/empty-105",
        "Second empty ID - Reuters", "second empty id",
        NOW - timedelta(minutes=20),
    )
    checkpoint = InterruptingCheckpoint()
    checkpoint.interrupt = False
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    report = backfill.run_backfill(
        apply=True, since_days=104, batch_size=20, checkpoint=checkpoint,
    )

    assert report.done is True
    assert backend.article(first["id"])["publisher_source_id"] == 3
    assert backend.article(second["id"])["publisher_source_id"] is None
    assert backend.article(second["id"])["geo_status"] == "legacy_unverified"
    assert backend.article(second["id"])["is_duplicate"] is True
    assert backend.article(second["id"])["duplicate_of"] == first["id"]
    assert backend.article(first["id"])["reprint_count"] == 1


def test_title_duplicate_reconciliation_stays_in_canonical_country(monkeypatch):
    backend = MemoryBackend()
    foreign = _append_article(
        backend, 70, 4, "wire-70", "https://wire-us.example/70",
        "España negocia", "espana negocia", NOW - timedelta(hours=2),
        geo_status="source_verified",
    )
    foreign["geo_country_code"] = "US"
    checkpoint = InterruptingCheckpoint()
    checkpoint.interrupt = False
    monkeypatch.setattr(backfill, "get_session", backend.session_factory)

    backfill.run_backfill(
        apply=True, since_days=104, batch_size=20, checkpoint=checkpoint,
    )

    assert backend.article(50)["reprint_count"] == 1
    assert backend.article(101)["duplicate_of"] == 50
    assert backend.article(70)["is_duplicate"] is False
    assert backend.article(70)["duplicate_of"] is None
    assert backend.article(70)["reprint_count"] == 0


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
        matrix={},
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
    assert "article.publisher_source_id IS NULL" in compact
    assert "article.geo_method IS NULL" in compact
    assert "article.geo_verified_at IS NULL" in compact
    assert "'source_verified', 'unverified', 'legacy_unverified'" in compact
    dedup_compact = " ".join(backfill.DEDUP_CANDIDATES_SQL.split())
    assert "candidate_publisher.country_code = affected.country_code" in dedup_compact
    assert "candidate_ids AS" in dedup_compact
    assert dedup_compact.count(" UNION ") == 4
    assert " OR " not in dedup_compact
    assert "JOIN affected ON (" not in dedup_compact
    assert dedup_compact.count(
        "candidate.external_id = affected.external_id"
    ) == 2
    assert dedup_compact.count(
        "candidate.title_normalized = affected.title_normalized"
    ) == 2
    assert dedup_compact.count("candidate.published_at BETWEEN") == 2
    assert dedup_compact.count(
        ") <> 'publisher_discovery'"
    ) == 2
    assert (
        "candidate.publisher_source_id = affected.publisher_id "
        "AND candidate.external_id = affected.external_id"
    ) in dedup_compact
    assert (
        "candidate.source_id = affected.publisher_id "
        "AND candidate.external_id = affected.external_id"
    ) in dedup_compact
    assert (
        "candidate.title_normalized = affected.title_normalized"
    ) in dedup_compact
    assert (
        "JOIN articles candidate ON candidate.id = candidate_ids.id"
    ) in dedup_compact
    assert "MAX(id)" in backfill.ARTICLE_HIGH_WATER_SQL
    invariant_compact = " ".join(backfill.INVARIANT_COUNTS_SQL.split())
    provenance_compact = " ".join(backfill.PROVENANCE_SQL.split())
    assert "articles WHERE id <= :max_article_id" in invariant_compact
    assert "WHERE id <= :max_article_id" in provenance_compact
    unclassified_compact = " ".join(backfill.UNCLASSIFIED_UPDATE_SQL.split())
    assert "id = ANY(CAST(:article_ids AS INTEGER[]))" in unclassified_compact
    assert "publisher_source_id IS NULL" in unclassified_compact
    assert "geo_method IS NULL" in unclassified_compact
    assert "geo_verified_at IS NULL" in unclassified_compact
    mutation_sql = " ".join(
        (backfill.CLASSIFIED_UPDATE_SQL, backfill.UNCLASSIFIED_UPDATE_SQL)
    ).upper()
    assert "DELETE " not in mutation_sql
    for column in ("SOURCE_ID", "EXTERNAL_ID", "URL"):
        assert f"SET {column}" not in mutation_sql
        assert f", {column}" not in mutation_sql


def _reset_postgres_backfill_schema(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        connection.exec_driver_sql("""
            CREATE TABLE sources (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                country_code CHAR(2) NOT NULL,
                config JSONB NOT NULL DEFAULT '{}'::jsonb
            )
        """)
        connection.exec_driver_sql("""
            CREATE TABLE publisher_domains (
                domain TEXT PRIMARY KEY,
                publisher_source_id INTEGER NOT NULL,
                country_code CHAR(2) NOT NULL,
                status TEXT NOT NULL
            )
        """)
        connection.exec_driver_sql("""
            CREATE TABLE articles (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                external_id TEXT,
                title TEXT,
                url TEXT,
                resolved_url TEXT,
                published_at TIMESTAMPTZ NOT NULL,
                title_normalized TEXT,
                publisher_source_id INTEGER,
                publisher_name TEXT,
                publisher_domain TEXT,
                geo_country_code CHAR(2),
                geo_status TEXT NOT NULL,
                geo_method TEXT,
                geo_confidence NUMERIC(4,3),
                geo_verified_at TIMESTAMPTZ,
                is_duplicate BOOLEAN NOT NULL DEFAULT FALSE,
                duplicate_of INTEGER,
                reprint_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        connection.exec_driver_sql("CREATE TABLE analysis (id INTEGER)")
        connection.exec_driver_sql(
            "CREATE TABLE story_articles (article_id INTEGER)"
        )
        connection.exec_driver_sql("""
            CREATE UNIQUE INDEX uq_articles_source_external_test
            ON articles (source_id, external_id)
        """)
        connection.exec_driver_sql("""
            CREATE UNIQUE INDEX uq_articles_publisher_external_id
            ON articles (publisher_source_id, external_id)
            WHERE publisher_source_id IS NOT NULL
        """)
        connection.exec_driver_sql("""
            CREATE INDEX idx_articles_title_trgm
            ON articles USING GIN (title_normalized gin_trgm_ops)
        """)


def test_postgres_split_dedup_candidates_are_exactly_legacy_equivalent():
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.getenv("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires an explicitly disposable PostgreSQL database")
    pytest.importorskip("psycopg2")
    engine = create_engine(dsn)

    affected_ids = [104, 100, 100, 101, 102, 103, 104, 105, 999_999]
    try:
        _reset_postgres_backfill_schema(engine)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO sources(id,name,url,country_code,config) VALUES
                  (1,'Google ES','https://news.google.com/es','ES',
                   '{"feed_mode":"publisher_discovery"}'::jsonb),
                  (2,'Publisher ES','https://es.example','ES','{}'::jsonb),
                  (3,'Publisher GB','https://gb.example','GB','{}'::jsonb),
                  (4,'Other ES','https://other-es.example','ES','{}'::jsonb),
                  (5,'Google GB','https://news.google.com/gb','GB',
                   '{"feed_mode":"publisher_discovery"}'::jsonb),
                  (6,'Override origin','https://origin.example','US','{}'::jsonb)
            """))
            connection.execute(text("""
                INSERT INTO articles(
                    id,source_id,external_id,published_at,title_normalized,
                    publisher_source_id,geo_status,is_duplicate,
                    duplicate_of,reprint_count
                ) VALUES
                  (100,1,'shared-ext',:now,'boundary-title',2,
                   'publisher_reassigned',FALSE,NULL,0),
                  (101,6,'override-ext',:now,'override-title',2,
                   'publisher_verified',FALSE,NULL,0),
                  (102,2,'native-ext',:now,'native-title',NULL,
                   'source_verified',FALSE,NULL,0),
                  (103,2,NULL,:now,NULL,NULL,
                   'source_verified',FALSE,NULL,0),
                  (104,2,'',:now,'',NULL,
                   'source_verified',FALSE,NULL,0),
                  (105,2,'invalid-affected',:now,'boundary-title',NULL,
                   'legacy_unverified',FALSE,NULL,0),

                  (200,2,'shared-ext',:now,'different-title',NULL,
                   'source_verified',FALSE,NULL,0),
                  (201,6,'explicit-title',:now,'boundary-title',2,
                   'publisher_verified',FALSE,NULL,0),
                  (202,4,'minus-boundary',:now - INTERVAL '48 hours',
                   'boundary-title',NULL,'source_verified',FALSE,NULL,0),
                  (203,4,'plus-boundary',:now + INTERVAL '48 hours',
                   'boundary-title',NULL,'source_verified',FALSE,NULL,0),
                  (204,4,'plus-outside',
                   :now + INTERVAL '48 hours 1 second','boundary-title',NULL,
                   'source_verified',FALSE,NULL,0),
                  (205,4,'minus-outside',
                   :now - INTERVAL '48 hours 1 second','boundary-title',NULL,
                   'source_verified',FALSE,NULL,0),
                  (206,3,'different-country',:now,'boundary-title',NULL,
                   'source_verified',FALSE,NULL,0),
                  (207,2,'different-everything',:now,'different-title',NULL,
                   'source_verified',FALSE,NULL,0),
                  (208,1,'broad-without-publisher',:now,'boundary-title',NULL,
                   'source_verified',FALSE,NULL,0),
                  (209,2,'invalid-candidate',:now,'boundary-title',NULL,
                   'legacy_unverified',FALSE,NULL,0),
                  (210,6,'native-ext',:now,'external-only',2,
                   'publisher_verified',FALSE,NULL,0),
                  (211,2,'override-ext',:now,'external-only',NULL,
                   'source_verified',FALSE,NULL,0),
                  (212,5,'override-ext',:now,'external-only',3,
                   'publisher_verified',FALSE,NULL,0),
                  (213,2,NULL,:now,NULL,NULL,
                   'source_verified',FALSE,NULL,0),
                  (214,4,'',:now,'',NULL,
                   'source_verified',FALSE,NULL,0)
            """), {"now": NOW})

        with engine.connect() as connection:
            legacy = [
                dict(row)
                for row in connection.execute(
                    text(LEGACY_DEDUP_CANDIDATES_SQL),
                    {"affected_ids": affected_ids},
                ).mappings()
            ]
            split = [
                dict(row)
                for row in connection.execute(
                    text(backfill.DEDUP_CANDIDATES_SQL),
                    {"affected_ids": affected_ids},
                ).mappings()
            ]

        assert split == legacy
        assert [row["id"] for row in split] == [
            100, 101, 102, 103, 104, 200, 201, 202, 203, 210, 211,
        ]
    finally:
        engine.dispose()


def _postgres_explain_dedup(connection, sql):
    payload = connection.execute(
        text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql),
        {"affected_ids": [200_001]},
    ).scalar_one()
    return payload[0]


def _postgres_plan_nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _postgres_plan_nodes(child)


def test_postgres_split_dedup_plan_uses_bounded_index_paths():
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.getenv("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires an explicitly disposable PostgreSQL database")
    pytest.importorskip("psycopg2")
    engine = create_engine(dsn)

    try:
        _reset_postgres_backfill_schema(engine)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO sources(id,name,url,country_code,config) VALUES
                  (1,'Google ES','https://news.google.com/es','ES',
                   '{"feed_mode":"publisher_discovery"}'::jsonb),
                  (2,'Publisher ES','https://es.example','ES','{}'::jsonb),
                  (3,'Publisher GB','https://gb.example','GB','{}'::jsonb)
            """))
            connection.execute(text("""
                INSERT INTO articles(
                    id,source_id,external_id,published_at,title_normalized,
                    publisher_source_id,geo_status
                )
                SELECT value,
                       CASE WHEN value % 2 = 0 THEN 2 ELSE 3 END,
                       'noise-external-' || value,
                       :now,
                       'noise-title-' || value,
                       NULL,
                       'source_verified'
                FROM generate_series(1, 100000) AS value
            """), {"now": NOW})
            connection.execute(text("""
                INSERT INTO articles(
                    id,source_id,external_id,published_at,title_normalized,
                    publisher_source_id,geo_status
                ) VALUES
                  (200001,1,'target-external',:now,'target-title',2,
                   'publisher_reassigned'),
                  (200002,2,'target-external',:now,'external-match',NULL,
                   'source_verified'),
                  (200003,2,'title-match',:now,'target-title',NULL,
                   'source_verified')
            """), {"now": NOW})
            connection.exec_driver_sql("ANALYZE articles")

        with engine.connect() as connection:
            # Warm both plans once so the printed execution comparison is not a
            # cold-cache artifact. Plan-shape and cost assertions remain the gate.
            connection.execute(
                text(LEGACY_DEDUP_CANDIDATES_SQL),
                {"affected_ids": [200_001]},
            ).all()
            connection.execute(
                text(backfill.DEDUP_CANDIDATES_SQL),
                {"affected_ids": [200_001]},
            ).all()
            legacy = _postgres_explain_dedup(
                connection, LEGACY_DEDUP_CANDIDATES_SQL,
            )
            split = _postgres_explain_dedup(
                connection, backfill.DEDUP_CANDIDATES_SQL,
            )

        legacy_nodes = list(_postgres_plan_nodes(legacy["Plan"]))
        split_nodes = list(_postgres_plan_nodes(split["Plan"]))
        split_indexes = {
            node["Index Name"]
            for node in split_nodes
            if node.get("Index Name")
        }
        legacy_candidate_seq_scans = [
            node for node in legacy_nodes
            if node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") == "articles"
            and node.get("Alias") == "candidate"
        ]
        split_candidate_seq_scans = [
            node for node in split_nodes
            if node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") == "articles"
            and node.get("Alias") == "candidate"
        ]
        benchmark = {
            "rows": 100_003,
            "legacy_execution_ms": round(legacy["Execution Time"], 3),
            "split_execution_ms": round(split["Execution Time"], 3),
            "legacy_total_cost": round(legacy["Plan"]["Total Cost"], 2),
            "split_total_cost": round(split["Plan"]["Total Cost"], 2),
        }
        print("dedup-plan-benchmark " + json.dumps(benchmark, sort_keys=True))

        assert legacy_candidate_seq_scans
        assert split_candidate_seq_scans == []
        assert {
            "articles_pkey",
            "uq_articles_source_external_test",
            "uq_articles_publisher_external_id",
            "idx_articles_title_trgm",
        }.issubset(split_indexes)
        assert split["Plan"]["Actual Rows"] == legacy["Plan"]["Actual Rows"]
        assert split["Plan"]["Total Cost"] < legacy["Plan"]["Total Cost"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("external_id", ["shared", ""])
def test_postgres_unique_publisher_external_collision_is_reconciled_safely(
    monkeypatch, tmp_path, external_id,
):
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.getenv("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires an explicitly disposable PostgreSQL database")
    pytest.importorskip("psycopg2")
    engine = create_engine(dsn)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def postgres_session():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    try:
        _reset_postgres_backfill_schema(engine)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO sources(id,name,url,country_code,config) VALUES
                  (1,'Google News GB A','https://news.google.com/a','GB',
                   '{"feed_mode":"publisher_discovery"}'::jsonb),
                  (2,'Google News GB B','https://news.google.com/b','GB',
                   '{"feed_mode":"publisher_discovery"}'::jsonb),
                  (3,'Reuters','https://reuters.com/rss','GB','{}'::jsonb)
            """))
            connection.execute(text("""
                INSERT INTO publisher_domains(
                    domain,publisher_source_id,country_code,status
                ) VALUES ('reuters.com',3,'GB','verified')
            """))
            connection.execute(text("""
                INSERT INTO articles(
                    id,source_id,external_id,title,url,published_at,
                    title_normalized,geo_status
                ) VALUES
                  (101,1,:external_id,'First - Reuters','https://news.google.com/101',
                   NOW(),'first','unverified'),
                  (102,2,:external_id,'Second - Reuters','https://news.google.com/102',
                   NOW(),'second','unverified')
            """), {"external_id": external_id})

        monkeypatch.setattr(backfill, "get_session", postgres_session)
        report = backfill.run_backfill(
            apply=True,
            since_days=104,
            batch_size=20,
            checkpoint=tmp_path / "postgres-checkpoint.json",
        )

        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT id,publisher_source_id,geo_status,is_duplicate,
                       duplicate_of,reprint_count
                FROM articles ORDER BY id
            """)).mappings().all()
        assert report.done is True
        assert [dict(row) for row in rows] == [
            {
                "id": 101,
                "publisher_source_id": 3,
                "geo_status": "publisher_verified",
                "is_duplicate": False,
                "duplicate_of": None,
                "reprint_count": 1,
            },
            {
                "id": 102,
                "publisher_source_id": None,
                "geo_status": "legacy_unverified",
                "is_duplicate": True,
                "duplicate_of": 101,
                "reprint_count": 0,
            },
        ]
    finally:
        engine.dispose()


def test_postgres_bulk_unclassified_update_resumes_from_nonzero_checkpoint(
    monkeypatch,
):
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.getenv("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires an explicitly disposable PostgreSQL database")
    pytest.importorskip("psycopg2")
    engine = create_engine(dsn)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    update_parameters = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture_bulk_update(
        connection, cursor, statement, parameters, context, executemany,
    ):
        if "gnews-backfill:update-unclassified" in statement:
            update_parameters.append(parameters)

    @contextmanager
    def postgres_session():
        session = Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    try:
        _reset_postgres_backfill_schema(engine)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO sources(id,name,url,country_code,config) VALUES
                  (1,'Google News GB','https://news.google.com/gb','GB',
                   '{"feed_mode":"publisher_discovery"}'::jsonb)
            """))
            connection.execute(text("""
                INSERT INTO articles(
                    id,source_id,external_id,title,url,published_at,
                    title_normalized,geo_status
                ) VALUES
                  (101,1,'unknown-101','Unknown 101',
                   'https://news.google.com/101',NOW(),'unknown 101','unverified'),
                  (102,1,'unknown-102','Unknown 102',
                   'https://news.google.com/102',NOW(),'unknown 102','unverified'),
                  (103,1,'unknown-103','Unknown 103',
                   'https://news.google.com/103',NOW(),'unknown 103','unverified')
            """))

        checkpoint = InterruptingCheckpoint()
        monkeypatch.setattr(backfill, "get_session", postgres_session)

        with pytest.raises(RuntimeError, match="simulated interruption"):
            backfill.run_backfill(
                apply=True,
                since_days=104,
                batch_size=2,
                checkpoint=checkpoint,
            )

        assert checkpoint.state["last_id"] == 102
        assert checkpoint.state["audit"]["updated"] == 2
        with engine.connect() as connection:
            statuses_after_interrupt = connection.execute(text(
                "SELECT id,geo_status FROM articles ORDER BY id"
            )).all()
        assert statuses_after_interrupt == [
            (101, "legacy_unverified"),
            (102, "legacy_unverified"),
            (103, "unverified"),
        ]

        checkpoint.interrupt = False
        report = backfill.run_backfill(
            apply=True,
            since_days=104,
            batch_size=2,
            checkpoint=checkpoint,
        )

        with engine.connect() as connection:
            statuses = connection.execute(text(
                "SELECT id,geo_status FROM articles ORDER BY id"
            )).all()
        assert statuses == [
            (101, "legacy_unverified"),
            (102, "legacy_unverified"),
            (103, "legacy_unverified"),
        ]
        assert report.done is True
        assert report.scanned == 3
        assert report.unclassified == 3
        assert report.updated == 3
        assert report.last_id == 103
        assert [params["article_ids"] for params in update_parameters] == [
            [101, 102],
            [103],
        ]
    finally:
        engine.dispose()
