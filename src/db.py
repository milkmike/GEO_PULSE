"""Database connection and models."""
import logging
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, ForeignKey,
    Integer, Numeric, SmallInteger, String, Text, UniqueConstraint, create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None  # pgvector not installed — embedding column won't have type

from src.config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    url = Column(String(500), nullable=False)
    country_code = Column(String(2), nullable=False)
    source_type = Column(String(20), nullable=False)
    weight = Column(Numeric(3, 2), default=1.0)
    language = Column(String(5), default="ru")
    config = Column(JSONB, default={})
    active = Column(Boolean, default=True)
    tier = Column(String(20), default="mainstream")
    state_affiliated = Column(Boolean, default=False)
    propaganda_risk = Column(String(10), default="low")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Article(Base):
    __tablename__ = "articles"
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, nullable=False)
    external_id = Column(Text)
    title = Column(Text)
    body = Column(Text)
    summary = Column(Text)
    url = Column(Text)
    published_at = Column(DateTime(timezone=True), nullable=False)
    collected_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    language = Column(String(5))
    title_normalized = Column(Text)
    is_duplicate = Column(Boolean, default=False)
    duplicate_of = Column(Integer)
    reprint_count = Column(Integer, default=0)
    is_backfill = Column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)


class Analysis(Base):
    __tablename__ = "analysis"
    id = Column(Integer, primary_key=True)
    article_id = Column(Integer, unique=True, nullable=False)
    is_relevant = Column(Boolean)
    relevance_score = Column(Numeric(3, 2))
    sentiment = Column(Numeric(3, 1))
    sentiment_confidence = Column(Numeric(3, 2))
    event_type = Column(String(20))
    event_key = Column(String(200))
    action_level = Column(Integer, default=1)
    model_used = Column(String(50))
    prompt_version = Column(String(20))
    raw_response = Column(JSONB)
    analyzed_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    # Embedding for semantic clustering (text-embedding-3-small, 1536 dims)
    # Column managed via raw SQL (pgvector vector type)
    entities = Column(JSONB)  # Future: extracted entities for Graphiti integration
    topics = Column(ARRAY(Text))  # Topic taxonomy labels (prompt v2.0+)


class Temperature(Base):
    __tablename__ = "temperature"
    time = Column(DateTime(timezone=True), primary_key=True)
    country_code = Column(String(2), primary_key=True)
    temperature = Column(Numeric(5, 2))
    raw_sentiment = Column(Numeric(4, 2))
    diplomatic = Column(Numeric(4, 2))
    military = Column(Numeric(4, 2))
    economic = Column(Numeric(4, 2))
    cultural = Column(Numeric(4, 2))
    security = Column(Numeric(4, 2))
    article_count = Column(Integer)
    source_count = Column(Integer)
    trend = Column(String(10))
    anomaly_score = Column(Numeric(4, 2))
    pattern_type = Column(String(20))


class Country(Base):
    __tablename__ = "countries"
    code = Column(String(2), primary_key=True)
    name_ru = Column(String(100), nullable=False)
    name_en = Column(String(100), nullable=False)
    iso3 = Column(String(3), nullable=False)
    fips = Column(String(2))
    flag = Column(String(8))
    region = Column(String(30), nullable=False)
    tier = Column(SmallInteger, default=2)
    memberships = Column(ARRAY(Text), default=[])
    unfriendly = Column(Boolean, default=False)
    sanctions_on_russia = Column(Boolean, default=False)
    war_with_russia = Column(Boolean, default=False)
    baseline_adj = Column(SmallInteger, default=0)
    baseline_note = Column(Text)
    active = Column(Boolean, default=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class RuIndex(Base):
    __tablename__ = "ru_index"
    time = Column(DateTime(timezone=True), primary_key=True)
    country_code = Column(String(2), primary_key=True)
    score = Column(Numeric(6, 2), nullable=False)
    structural = Column(Numeric(6, 2))
    media = Column(Numeric(6, 2))
    boost = Column(Numeric(6, 2))
    level = Column(String(12))
    delta_24h = Column(Numeric(6, 2))
    delta_7d = Column(Numeric(6, 2))
    article_count = Column(Integer)
    gdelt_volume = Column(Numeric(12, 2))
    gdelt_tone = Column(Numeric(6, 2))
    version = Column(String(8), default="v1")
    details = Column(JSONB)


class GdeltDaily(Base):
    __tablename__ = "gdelt_daily"
    day = Column(Date, primary_key=True)
    country_code = Column(String(2), primary_key=True)
    volume = Column(Numeric(12, 2))
    volume_share = Column(Numeric(10, 6))
    tone_avg = Column(Numeric(6, 2))
    article_samples = Column(JSONB)
    fetched_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True)
    signal_type = Column(String(30), nullable=False)
    country_code = Column(String(2))
    severity = Column(String(10), default="info")
    confidence = Column(Numeric(3, 2), default=0.70)
    title = Column(String(500))
    description = Column(Text)
    payload = Column(JSONB)
    dedup_key = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at = Column(DateTime(timezone=True))


class Brief(Base):
    __tablename__ = "briefs"
    id = Column(Integer, primary_key=True)
    scope = Column(String(10), nullable=False, default="world")
    content = Column(Text, nullable=False)
    model = Column(String(80))
    source_hash = Column(String(64))
    meta = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    country_code = Column(String(2), nullable=False)
    alert_type = Column(String(30))
    severity = Column(String(10))
    title = Column(String(500))
    description = Column(Text)
    data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    acknowledged = Column(Boolean, default=False)


class CanonicalEntity(Base):
    __tablename__ = "canonical_entities"
    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    kind = Column(String(24), nullable=False)
    canonical_name = Column(Text, nullable=False)
    normalized_name = Column(Text, nullable=False)
    labels = Column(JSONB, nullable=False, default=dict)
    country_codes = Column(ARRAY(Text), nullable=False, default=list)
    provenance = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint(
            "kind IN ('person','organization','location','event')",
            name="canonical_entities_kind_check",
        ),
        UniqueConstraint("kind", "normalized_name"),
    )


class EntityAlias(Base):
    __tablename__ = "entity_aliases"
    id = Column(BigInteger, primary_key=True)
    entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("canonical_entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias = Column(Text, nullable=False)
    normalized_alias = Column(Text, nullable=False)
    language = Column(String(8))
    ambiguous = Column(Boolean, nullable=False, default=False)
    provenance = Column(JSONB, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("entity_id", "normalized_alias"),)


class ArticleEntityMention(Base):
    __tablename__ = "article_entity_mentions"
    article_id = Column(
        Integer,
        ForeignKey("articles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("canonical_entities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mention_text = Column(Text)
    char_start = Column(Integer)
    char_end = Column(Integer)
    extractor = Column(String(80), primary_key=True)
    extractor_version = Column(String(40))
    confidence = Column(Numeric(4, 3), nullable=False, default=1.0)
    evidence = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class KnowledgeEdge(Base):
    __tablename__ = "knowledge_edges"
    id = Column(BigInteger, primary_key=True)
    source_node = Column(Text, nullable=False)
    target_node = Column(Text, nullable=False)
    relation = Column(String(80), nullable=False)
    confidence = Column(Numeric(4, 3), nullable=False)
    evidence = Column(JSONB, nullable=False, default=list)
    valid_from = Column(DateTime(timezone=True))
    valid_to = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("source_node", "target_node", "relation"),)


class EmbeddingProfile(Base):
    __tablename__ = "embedding_profiles"
    id = Column(Integer, primary_key=True)
    profile_key = Column(String(80), unique=True, nullable=False)
    provider = Column(String(40), nullable=False)
    model = Column(String(120), nullable=False)
    dimensions = Column(Integer, nullable=False)
    task = Column(String(40), nullable=False, default="text-matching")
    version = Column(String(40), nullable=False)
    active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("dimensions > 0", name="embedding_profiles_dimensions_check"),
    )


class ContentEmbedding(Base):
    __tablename__ = "content_embeddings"
    id = Column(BigInteger, primary_key=True)
    profile_id = Column(
        Integer,
        ForeignKey("embedding_profiles.id"),
        nullable=False,
    )
    object_type = Column(String(24), nullable=False)
    object_id = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    # The dimensionless pgvector embedding column is managed by raw SQL.
    status = Column(String(20), nullable=False, default="ready")
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint(
            "object_type IN ('article','entity','event','story')",
            name="content_embeddings_object_type_check",
        ),
        UniqueConstraint("profile_id", "object_type", "object_id", "content_hash"),
    )


class EmbeddingJob(Base):
    __tablename__ = "embedding_jobs"
    id = Column(BigInteger, primary_key=True)
    profile_id = Column(
        Integer,
        ForeignKey("embedding_profiles.id"),
        nullable=False,
    )
    object_type = Column(String(24), nullable=False)
    object_id = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text)
    available_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("profile_id", "object_type", "object_id", "content_hash"),
    )


class Story(Base):
    __tablename__ = "stories"
    id = Column(BigInteger, primary_key=True)
    slug = Column(Text, unique=True, nullable=False)
    title_ru = Column(Text, nullable=False)
    title_en = Column(Text)
    summary = Column(Text)
    lifecycle = Column(String(20), nullable=False)
    first_seen = Column(DateTime(timezone=True), nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=False)
    article_count = Column(Integer, nullable=False, default=0)
    source_count = Column(Integer, nullable=False, default=0)
    country_count = Column(Integer, nullable=False, default=0)
    highest_action_level = Column(Integer, nullable=False, default=1)
    clustering_confidence = Column(Numeric(4, 3), nullable=False, default=0)
    summary_model = Column(String(120))
    source_hash = Column(String(64))
    meta = Column(JSONB, nullable=False, default=dict)
    generated_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint(
            "lifecycle IN ('emerging','developing','escalating','cooling','resolved')",
            name="stories_lifecycle_check",
        ),
    )


class StoryArticle(Base):
    __tablename__ = "story_articles"
    story_id = Column(
        BigInteger,
        ForeignKey("stories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    article_id = Column(
        Integer,
        ForeignKey("articles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    membership_confidence = Column(Numeric(4, 3), nullable=False)
    evidence = Column(JSONB, nullable=False, default=dict)
    added_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class StoryCountry(Base):
    __tablename__ = "story_countries"
    story_id = Column(
        BigInteger,
        ForeignKey("stories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    country_code = Column(String(2), ForeignKey("countries.code"), primary_key=True)
    article_count = Column(Integer, nullable=False, default=0)
    source_count = Column(Integer, nullable=False, default=0)
    media_tone = Column(Numeric(6, 2))
    first_seen = Column(DateTime(timezone=True))
    last_seen = Column(DateTime(timezone=True))


class StoryEntity(Base):
    __tablename__ = "story_entities"
    story_id = Column(
        BigInteger,
        ForeignKey("stories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("canonical_entities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mentions = Column(Integer, nullable=False, default=0)
    confidence = Column(Numeric(4, 3), nullable=False)
    evidence = Column(JSONB, nullable=False, default=dict)


class StoryEvent(Base):
    __tablename__ = "story_events"
    story_id = Column(
        BigInteger,
        ForeignKey("stories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_id = Column(
        UUID(as_uuid=True),
        ForeignKey("canonical_entities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_key = Column(Text, nullable=False)
    event_at = Column(DateTime(timezone=True))
    action_level = Column(Integer, nullable=False, default=1)
    evidence = Column(JSONB, nullable=False, default=dict)


class SignalEvidence(Base):
    __tablename__ = "signal_evidence"
    id = Column(BigInteger, primary_key=True)
    signal_id = Column(
        Integer,
        ForeignKey("signals.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    detector = Column(String(80), nullable=False)
    detector_version = Column(String(40), nullable=False)
    threshold = Column(JSONB, nullable=False)
    observed = Column(JSONB, nullable=False)
    baseline = Column(JSONB, nullable=False)
    window_start = Column(DateTime(timezone=True))
    window_end = Column(DateTime(timezone=True))
    article_ids = Column(ARRAY(Integer), nullable=False, default=list)
    story_ids = Column(ARRAY(BigInteger), nullable=False, default=list)
    rri_points = Column(JSONB, nullable=False, default=list)
    confidence = Column(Numeric(4, 3), nullable=False)
    completeness = Column(String(20), nullable=False)
    explanation = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    __table_args__ = (
        CheckConstraint(
            "completeness IN ('complete','partial')",
            name="signal_evidence_completeness_check",
        ),
    )


class IndexChangeExplanation(Base):
    __tablename__ = "index_change_explanations"
    id = Column(BigInteger, primary_key=True)
    country_code = Column(String(2), ForeignKey("countries.code"), nullable=False)
    from_time = Column(DateTime(timezone=True), nullable=False)
    to_time = Column(DateTime(timezone=True), nullable=False)
    rri_version = Column(String(16), nullable=False)
    input_hash = Column(String(64), nullable=False)
    exact_changes = Column(JSONB, nullable=False)
    estimated_contributions = Column(JSONB, nullable=False, default=list)
    context = Column(JSONB, nullable=False, default=list)
    evidence_completeness = Column(String(20), nullable=False)
    limitations = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint(
            "country_code",
            "from_time",
            "to_time",
            "rri_version",
            "input_hash",
        ),
    )


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def wait_for_db(max_retries: int = 30, delay: float = 2.0):
    """Wait for database to be ready."""
    import time
    for i in range(max_retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database is ready")
            return
        except Exception as e:
            logger.warning(f"DB not ready (attempt {i+1}/{max_retries}): {e}")
            time.sleep(delay)
    raise RuntimeError("Database not available")
