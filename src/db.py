"""Database connection and models."""
import logging
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, Numeric, String, Text,
    UniqueConstraint, create_engine, text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class Article(Base):
    __tablename__ = "articles"
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, nullable=False)
    external_id = Column(String(500))
    title = Column(Text)
    body = Column(Text)
    summary = Column(Text)
    url = Column(String(1000))
    published_at = Column(DateTime(timezone=True), nullable=False)
    collected_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    language = Column(String(5))
    title_normalized = Column(Text)
    is_duplicate = Column(Boolean, default=False)
    duplicate_of = Column(Integer)
    reprint_count = Column(Integer, default=0)
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
