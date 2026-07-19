from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from backend.app.config import get_settings

settings = get_settings()

url = settings.database_url
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
# SQLAlchemy + psycopg3
if url.startswith("postgresql://") and "+psycopg" not in url:
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
elif url.startswith("postgresql+psycopg2://"):
    url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)


connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}

engine = create_engine(
    url,
    connect_args=connect_args,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=int(getattr(settings, "db_pool_size", 20) or 20),
    max_overflow=int(getattr(settings, "db_max_overflow", 20) or 20),
    pool_timeout=30,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
