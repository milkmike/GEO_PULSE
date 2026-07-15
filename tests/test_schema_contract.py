from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def migration(name: str) -> str:
    return (ROOT / "scripts" / "migrations" / name).read_text()


def test_search_and_knowledge_schema_contract():
    sql = migration("019_search_knowledge.sql")
    for fragment in (
        "ADD COLUMN IF NOT EXISTS search_vector tsvector",
        "CREATE INDEX IF NOT EXISTS idx_articles_search_vector",
        "CREATE TABLE IF NOT EXISTS canonical_entities",
        "CREATE TABLE IF NOT EXISTS article_entity_mentions",
        "CREATE TABLE IF NOT EXISTS knowledge_edges",
        "CREATE TABLE IF NOT EXISTS embedding_profiles",
        "CREATE TABLE IF NOT EXISTS content_embeddings",
        "CREATE TABLE IF NOT EXISTS embedding_jobs",
        "ALTER TABLE analysis ADD COLUMN IF NOT EXISTS embedding vector",
    ):
        assert fragment in sql


def test_story_schema_contract():
    sql = migration("020_global_stories.sql")
    for table in ("stories", "story_articles", "story_countries", "story_entities", "story_events"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_evidence_schema_contract():
    sql = migration("021_signal_evidence_explanations.sql")
    assert "CREATE TABLE IF NOT EXISTS signal_evidence" in sql
    assert "CREATE TABLE IF NOT EXISTS index_change_explanations" in sql


def test_init_schema_mirrors_new_tables():
    init = (ROOT / "data" / "init.sql").read_text()
    for table in (
        "canonical_entities", "entity_aliases", "article_entity_mentions",
        "knowledge_edges", "embedding_profiles", "content_embeddings",
        "embedding_jobs", "stories", "story_articles", "story_countries",
        "story_entities", "story_events", "signal_evidence",
        "index_change_explanations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in init
