import logging
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


def resolve_database_url() -> str:
    """Resolve database URL to absolute path for SQLite to prevent CWD divergence."""
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite:///"):
        db_path_str = url.replace("sqlite:///", "", 1)
        clean_name = Path(db_path_str).name
        
        base_dir = Path(__file__).resolve().parent.parent  # Pyrolens-app/backend
        root_dir = base_dir.parent  # Pyrolens-app
        
        candidates = [
            base_dir / clean_name,
            root_dir / clean_name,
            Path(db_path_str).resolve(),
        ]
        
        target = None
        for cand in candidates:
            if cand.exists() and cand.stat().st_size > 0:
                target = cand.resolve()
                break
        
        if target is None:
            target = (base_dir / clean_name).resolve()
        
        return f"sqlite:///{target.as_posix()}"

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


# Initialize engine and SessionLocal on module import so they are NEVER None
_resolved_url = resolve_database_url()
_connect_args = {"check_same_thread": False} if _resolved_url.startswith("sqlite") else {}
engine = create_engine(_resolved_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    """Configure the database and create any missing tables.

    This is safe to call from the API, workers, and tests. In particular, it
    must never drop existing tables: an application restart should not erase
    FIRMS observations or ingestion history.
    """
    global engine, SessionLocal
    if engine is None or SessionLocal is None:
        db_url = resolve_database_url()
        c_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        engine = create_engine(db_url, connect_args=c_args, pool_pre_ping=True)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    # Import models so they're registered with Base metadata
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Seed default operational users if none exist
    _seed_default_users()


def _seed_default_users() -> None:
    """Seed default administrative, analyst, and viewer accounts if table is empty or missing them."""
    from . import models
    from .security import hash_password
    import uuid
    from sqlalchemy import select

    try:
        with SessionLocal() as db:
            default_password_hash = hash_password("PyroLens2026!")
            seed_accounts = [
                ("admin", "admin@pyrolens.org", "PyroLens Administrator", "admin"),
                ("analyst", "analyst@pyrolens.org", "Operations Duty Analyst", "analyst"),
                ("viewer", "viewer@pyrolens.org", "Public Dashboard Viewer", "viewer"),
            ]
            for username, email, full_name, role in seed_accounts:
                user = db.scalar(select(models.User).where(models.User.username == username))
                if user is None:
                    db.add(
                        models.User(
                            id=str(uuid.uuid4()),
                            username=username,
                            email=email,
                            hashed_password=default_password_hash,
                            full_name=full_name,
                            role=role,
                            is_active=1,
                        )
                    )
                else:
                    if not user.is_active:
                        user.is_active = 1
            db.commit()
    except Exception as exc:
        logger.warning("Default user seeding warning: %s", exc)


def get_db() -> Generator[Session, None, None]:
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Auto-initialize database on import so tables and seeded accounts exist immediately
try:
    init_db()
except Exception as _init_exc:
    logger.warning("Auto-init database warning: %s", _init_exc)

