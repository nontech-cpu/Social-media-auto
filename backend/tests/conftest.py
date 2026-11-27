"""Shared pytest fixtures for async DB, cipher, and test settings."""

import os
import sys
from pathlib import Path

import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("APP_SECRET_KEY", "test-app-secret-key-32bytes-minimum")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))

from app.core.config import Settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.core.security import TokenCipher  # noqa: E402
import app.models.entities  # noqa: F401, E402


@pytest_asyncio.fixture
async def db_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_session_factory):
    async with db_session_factory() as session:
        yield session


@pytest_asyncio.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="development",
        database_url="sqlite+aiosqlite:///:memory:",
        token_encryption_key=Fernet.generate_key().decode("utf-8"),
        app_secret_key="test-secret-for-unit-tests",
        meta_app_id="123456789",
        meta_app_secret="meta-secret-xyz",
        meta_redirect_uri="http://localhost:8000/api/auth/instagram/callback",
        enable_internal_scheduler=False,
    )


@pytest_asyncio.fixture
def cipher(test_settings: Settings) -> TokenCipher:
    return TokenCipher(key=test_settings.token_encryption_key)
