from collections.abc import Generator

from sqlalchemy import create_engine, text
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
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Alinea SERIAL/IDENTITY con MAX(id). Tras un restore con IDs explícitos
# PostgreSQL no avanza la secuencia y el próximo INSERT choca el PK.
_RESYNC_SERIAL_SQL = """
DO $$
DECLARE
  r record;
  seq text;
  max_id bigint;
BEGIN
  FOR r IN
    SELECT n.nspname AS nsp, c.relname AS tbl, a.attname AS col
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_constraint pk ON pk.conrelid = c.oid AND pk.contype = 'p'
    JOIN pg_attribute a ON a.attrelid = c.oid
      AND a.attnum = ANY (pk.conkey)
      AND NOT a.attisdropped
    WHERE n.nspname = 'public'
      AND c.relkind = 'r'
      AND pg_get_serial_sequence(format('%I.%I', n.nspname, c.relname), a.attname) IS NOT NULL
  LOOP
    seq := pg_get_serial_sequence(format('%I.%I', r.nsp, r.tbl), r.col);
    EXECUTE format('SELECT COALESCE(MAX(%I), 0) FROM %I.%I', r.col, r.nsp, r.tbl)
      INTO max_id;
    IF max_id < 1 THEN
      PERFORM setval(seq, 1, false);
    ELSE
      PERFORM setval(seq, max_id, true);
    END IF;
  END LOOP;
END $$;
"""


def resync_serial_sequences(db: Session | None = None) -> None:
    """Fix out-of-sync integer PK sequences (user_consents, access_logs, …)."""
    bind = db.get_bind() if db is not None else engine
    if bind is None or bind.dialect.name != "postgresql":
        return
    if db is not None:
        db.execute(text(_RESYNC_SERIAL_SQL))
        db.commit()
        return
    with engine.begin() as conn:
        conn.execute(text(_RESYNC_SERIAL_SQL))

