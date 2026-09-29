"""
db/engine.py — Database engine and session factory.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config.settings import get_config
from db.models import Base

_engine = None
_session_factory = None


def _get_db_url() -> str:
    cfg = get_config()
    url = cfg.db.url
    # Ensure data directory exists for SQLite
    if "sqlite" in url:
        db_path = url.split("///")[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return url


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _get_db_url(),
            echo=False,
            connect_args={"check_same_thread": False} if "sqlite" in _get_db_url() else {},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncSession:
    """Dependency for FastAPI routes."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def create_all_tables() -> None:
    """Create all tables (idempotent)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all_tables() -> None:
    """For tests only."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
