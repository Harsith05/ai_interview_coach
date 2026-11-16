"""SQLAlchemy engine/session and the LangGraph Postgres checkpointer."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from coach.config import DATABASE_URL


def _sqlalchemy_url(url: str) -> str:
    # Use the psycopg (v3) driver for SQLAlchemy.
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


engine = create_engine(_sqlalchemy_url(DATABASE_URL), pool_pre_ping=True, pool_size=10)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@contextmanager
def db_session():
    """Short-lived session for use outside request handlers (e.g. background jobs)."""
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI dependency."""
    with db_session() as s:
        yield s


# LangGraph checkpoints live in the same database, so interviews survive server restarts.
checkpoint_pool = ConnectionPool(
    conninfo=DATABASE_URL,
    max_size=10,
    kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row, "connect_timeout": 10},
    open=False,
)


def _migrate():
    """Add columns introduced after a table was first created (create_all never alters tables)."""
    from sqlalchemy import text

    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS login_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE interviews ADD COLUMN IF NOT EXISTS seniority VARCHAR(20)",
        "ALTER TABLE interviews ADD COLUMN IF NOT EXISTS focus_topics JSON",
    ]
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))


def init_db():
    from coach import models  # noqa: F401  (register tables)
    from langgraph.checkpoint.postgres import PostgresSaver

    Base.metadata.create_all(engine)
    _migrate()
    checkpoint_pool.open(wait=True)
    PostgresSaver(checkpoint_pool).setup()
    # LangGraph long-term memory store (Talk companion memories) in the same database.
    from coach.memory import get_store
    get_store().setup()
