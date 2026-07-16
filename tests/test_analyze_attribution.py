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
        self.statements.append(str(statement))
        return _Rows()


@contextmanager
def _context(session):
    yield session


def test_queue_analyzer_uses_canonical_article_country_facts(monkeypatch):
    session = _Session()
    monkeypatch.setattr(analyze, "get_session", lambda: _context(session))

    assert analyze._analyze_article_by_id(123) is True

    [sql] = session.statements
    assert "JOIN article_country_facts source ON source.article_id = ar.id" in sql
    assert "source.name as source_name, source.country_code, source.weight" in sql
    assert "JOIN sources" not in sql


def test_batch_analyzer_uses_canonical_article_country_facts(monkeypatch):
    session = _Session()
    monkeypatch.setattr(analyze, "get_session", lambda: _context(session))

    assert analyze.analyze_new_articles(batch_size=25) == 0

    [sql] = session.statements
    assert "JOIN article_country_facts source ON source.article_id = ar.id" in sql
    assert "source.name as source_name, source.country_code, source.weight" in sql
    assert "JOIN sources" not in sql
