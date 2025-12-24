"""
Database models and connection management using SQLAlchemy.
"""

from datetime import datetime
from typing import AsyncGenerator, Optional, List
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, TIMESTAMP,
    BigInteger, ForeignKey, CheckConstraint, Index, ARRAY, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func

from app.config import settings


# ====================
# Base Model
# ====================

class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


# ====================
# Database Models
# ====================

class RawArticle(Base):
    """Сырые статьи из RSS и других источников."""

    __tablename__ = "raw_articles"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(Text, unique=True, nullable=False)
    title = Column(Text, nullable=False)
    content = Column(Text)
    source_name = Column(String(100), nullable=False, index=True)
    published_at = Column(TIMESTAMP)
    fetched_at = Column(TIMESTAMP, default=datetime.utcnow, index=True)
    status = Column(String(20), default='new', index=True)
    relevance_score = Column(Float)

    # Relationships
    drafts = relationship("PostDraft", back_populates="article", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status IN ('new', 'filtered', 'processed', 'rejected')",
            name='chk_status'
        ),
    )


class LegalKnowledge(Base):
    """База знаний юридических документов для RAG."""

    __tablename__ = "legal_knowledge"

    id = Column(Integer, primary_key=True, index=True)
    doc_name = Column(String(200), nullable=False, index=True)
    article_number = Column(String(50))
    text_chunk = Column(Text, nullable=False)
    keywords = Column(ARRAY(Text))
    # ts_vector handled by PostgreSQL generated column


class PostDraft(Base):
    """Драфты постов для модерации."""

    __tablename__ = "post_drafts"

    id = Column(Integer, primary_key=True, index=True)
    article_id = Column(Integer, ForeignKey("raw_articles.id", ondelete="CASCADE"))
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    legal_context = Column(Text)
    image_path = Column(Text)
    audio_path = Column(Text)
    confidence_score = Column(Float)
    created_at = Column(TIMESTAMP, default=datetime.utcnow, index=True)
    reviewed_at = Column(TIMESTAMP)
    reviewed_by = Column(Integer)
    status = Column(String(20), default='pending_review', index=True)
    rejection_reason = Column(Text)

    # Relationships
    article = relationship("RawArticle", back_populates="drafts")
    publications = relationship("Publication", back_populates="draft", cascade="all, delete-orphan")
    media_files = relationship("MediaFile", back_populates="draft", cascade="all, delete-orphan")
    feedback = relationship("FeedbackLabel", back_populates="draft", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'edited')",
            name='chk_draft_status'
        ),
    )


class Publication(Base):
    """Опубликованные посты в Telegram канале."""

    __tablename__ = "publications"

    id = Column(Integer, primary_key=True, index=True)
    draft_id = Column(Integer, ForeignKey("post_drafts.id", ondelete="CASCADE"), index=True)
    message_id = Column(BigInteger, nullable=False, index=True)
    channel_id = Column(BigInteger, nullable=False)
    published_at = Column(TIMESTAMP, default=datetime.utcnow, index=True)
    views = Column(Integer, default=0)
    reactions = Column(JSONB, default={})
    forwards = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)

    # Relationships
    draft = relationship("PostDraft", back_populates="publications")
    analytics = relationship("PostAnalytics", back_populates="publication", cascade="all, delete-orphan")


class PostAnalytics(Base):
    """Аналитика по опубликованным постам."""

    __tablename__ = "post_analytics"

    id = Column(Integer, primary_key=True, index=True)
    publication_id = Column(Integer, ForeignKey("publications.id", ondelete="CASCADE"), index=True)
    views = Column(Integer, default=0)
    reactions = Column(JSONB, default={})
    utm_clicks = Column(Integer, default=0)
    avg_read_time = Column(Integer)  # seconds
    collected_at = Column(TIMESTAMP, default=datetime.utcnow, index=True)

    # Relationships
    publication = relationship("Publication", back_populates="analytics")


class FeedbackLabel(Base):
    """Обучающие данные для ML (feedback loop)."""

    __tablename__ = "feedback_labels"

    id = Column(Integer, primary_key=True, index=True)
    draft_id = Column(Integer, ForeignKey("post_drafts.id", ondelete="CASCADE"), index=True)
    admin_action = Column(String(20), nullable=False)
    rejection_reason = Column(Text)
    performance_score = Column(Float)
    created_at = Column(TIMESTAMP, default=datetime.utcnow, index=True)

    # Relationships
    draft = relationship("PostDraft", back_populates="feedback")

    __table_args__ = (
        CheckConstraint(
            "admin_action IN ('published', 'rejected', 'edited')",
            name='chk_admin_action'
        ),
    )


class Source(Base):
    """Управление источниками новостей."""

    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    url = Column(Text, nullable=False)
    type = Column(String(20), nullable=False)
    enabled = Column(Boolean, default=True, index=True)
    last_fetch = Column(TIMESTAMP)
    fetch_errors = Column(Integer, default=0)
    quality_score = Column(Float, default=0.5, index=True)

    __table_args__ = (
        CheckConstraint(
            "type IN ('rss', 'web', 'telegram')",
            name='chk_source_type'
        ),
    )


class SystemLog(Base):
    """Системные логи приложения."""

    __tablename__ = "system_logs"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String(10), nullable=False, index=True)
    message = Column(Text, nullable=False)
    context = Column(JSONB)
    created_at = Column(TIMESTAMP, default=datetime.utcnow, index=True)

    __table_args__ = (
        CheckConstraint(
            "level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')",
            name='chk_log_level'
        ),
    )


class MediaFile(Base):
    """Медиа файлы (обложки, аудио)."""

    __tablename__ = "media_files"

    id = Column(Integer, primary_key=True, index=True)
    draft_id = Column(Integer, ForeignKey("post_drafts.id", ondelete="CASCADE"), index=True)
    file_type = Column(String(10), nullable=False, index=True)
    file_path = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.utcnow)

    # Relationships
    draft = relationship("PostDraft", back_populates="media_files")

    __table_args__ = (
        CheckConstraint(
            "file_type IN ('image', 'audio')",
            name='chk_file_type'
        ),
    )


# ====================
# Database Connection
# ====================

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,  # Проверяет соединение перед использованием
    pool_recycle=3600,  # Пересоздает соединения каждый час (до timeout PostgreSQL)
    pool_size=10,
    max_overflow=20,
    connect_args={
        "server_settings": {"jit": "off"},  # Отключаем JIT для стабильности
        "command_timeout": 60,  # Таймаут команд 60 секунд
    }
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting async database session.

    Usage:
        async def some_function(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()


# ====================
# Database Utilities
# ====================

async def check_db_connection() -> bool:
    """
    Check if database connection is alive.

    Returns:
        bool: True if connection is successful, False otherwise.
    """
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            return True
    except Exception:
        return False


async def log_to_db(
    level: str,
    message: str,
    context: Optional[dict] = None
) -> None:
    """
    Log event to database.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        message: Log message
        context: Additional context as dictionary
    """
    async with AsyncSessionLocal() as session:
        log_entry = SystemLog(
            level=level,
            message=message,
            context=context or {}
        )
        session.add(log_entry)
        await session.commit()
