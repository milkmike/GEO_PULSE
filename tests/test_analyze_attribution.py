from contextlib import contextmanager

import scripts.analyze as analyze


class _Rows:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _Session:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params or {}))
        return _Rows()


@contextmanager
def _context(session):
    yield session


def test_queue_analyzer_uses_canonical_article_country_facts(monkeypatch):
    session = _Session()
    monkeypatch.setattr(analyze, "get_session", lambda: _context(session))

    assert analyze._analyze_article_by_id(123) is True

    [(sql, _)] = session.statements
    assert "JOIN article_country_facts source ON source.article_id = ar.id" in sql
    assert "source.name as source_name, source.country_code, source.weight" in sql
    assert "JOIN sources" not in sql


def test_batch_analyzer_uses_canonical_article_country_facts(monkeypatch):
    session = _Session()
    monkeypatch.setattr(analyze, "get_session", lambda: _context(session))

    assert analyze.analyze_new_articles(batch_size=25) == 0

    [(sql, params)] = session.statements
    assert "ORDER BY ar.collected_at DESC, ar.id DESC" in sql
    assert "LIMIT :scan_limit" in sql
    assert "LEFT JOIN analysis" not in sql
    assert params == {"scan_limit": 1000}


def test_batch_analyzer_uses_bounded_two_stage_unanalyzed_selection(monkeypatch):
    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Session:
        def __init__(self):
            self.statements = []

        def execute(self, statement, params=None):
            self.statements.append((str(statement), params or {}))
            return Rows([(101,), (102,)]) if len(self.statements) == 1 else Rows([])

    session = Session()
    monkeypatch.setattr(analyze, "get_session", lambda: _context(session))

    assert analyze.analyze_new_articles(batch_size=100) == 0

    candidate_sql, candidate_params = session.statements[0]
    rows_sql, rows_params = session.statements[1]
    assert "ORDER BY ar.collected_at DESC, ar.id DESC" in candidate_sql
    assert "LIMIT :scan_limit" in candidate_sql
    assert "LEFT JOIN analysis" not in candidate_sql
    assert "ar.geo_country_code IS NOT NULL" in candidate_sql
    assert (
        "ar.geo_status IN ('source_verified', 'publisher_verified', "
        "'publisher_reassigned')" in " ".join(candidate_sql.split())
    )
    assert candidate_params == {"scan_limit": 2000}
    assert rows_params == {"article_ids": [101, 102]}
    assert "WHERE ar.id = ANY(CAST(:article_ids AS integer[]))" in rows_sql
    assert "AND NOT EXISTS (SELECT 1 FROM analysis an WHERE an.article_id = ar.id)" in rows_sql
    assert "JOIN article_country_facts source ON source.article_id = ar.id" in rows_sql


def test_empty_queue_runs_bounded_fallback_at_most_hourly(monkeypatch):
    calls = []
    monkeypatch.setattr(analyze, "process_from_queue", lambda: analyze.QUEUE_EMPTY)
    monkeypatch.setattr(analyze, "analyze_new_articles", lambda batch_size: calls.append(batch_size) or 0)
    monkeypatch.setattr(analyze, "_last_empty_queue_fallback_at", None)

    first = analyze.run_loop_iteration(25, now=analyze.datetime(2026, 7, 25, 12, 0, tzinfo=analyze.timezone.utc))
    second = analyze.run_loop_iteration(25, now=analyze.datetime(2026, 7, 25, 12, 30, tzinfo=analyze.timezone.utc))
    third = analyze.run_loop_iteration(25, now=analyze.datetime(2026, 7, 25, 13, 0, tzinfo=analyze.timezone.utc))

    assert (first, second, third) == (0, None, 0)
    assert calls == [25, 25]


def test_redis_transport_error_never_runs_database_fallback(monkeypatch):
    monkeypatch.setattr(analyze, "process_from_queue", lambda: analyze.QUEUE_UNAVAILABLE)
    monkeypatch.setattr(analyze, "analyze_new_articles", lambda _: (_ for _ in ()).throw(AssertionError("fallback")))

    assert analyze.run_loop_iteration(25) is None
