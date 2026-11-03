from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

_settings = get_settings()
_url = _settings.database_url
# Render / Heroku / Neon hand out postgres:// URLs; SQLAlchemy needs the driver named explicitly.
for prefix in ("postgres://", "postgresql://"):
    if _url.startswith(prefix):
        _url = "postgresql+psycopg://" + _url[len(prefix):]
        break
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}

engine = create_engine(_url, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
