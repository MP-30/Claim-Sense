import os

from psycopg.conninfo import make_conninfo
from sqlalchemy import URL, create_engine, make_url
from sqlalchemy.orm import sessionmaker

from claim_sense.core import config
from claim_sense.models.models import Base


if config.DATABASE_URL:
    database_url = make_url(config.DATABASE_URL)
    if database_url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("DATABASE_URL must use postgresql:// or postgresql+psycopg://")
else:
    database_url = URL.create(
        "postgresql",
        username=os.getenv("POSTGRES_USER", "claimsense"),
        password=os.getenv("POSTGRES_PASSWORD", "claimsense_dev"),
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "claimsense"),
    )

engine = create_engine(database_url.set(drivername="postgresql+psycopg"), pool_pre_ping=True)
SessionLocal = sessionmaker(engine)


def init_db() -> None:
    Base.metadata.create_all(engine)


def checkpoint_dsn() -> str:
    args, kwargs = engine.dialect.create_connect_args(engine.url)
    # The SQLAlchemy adapter registry is a Python object, not a libpq option.
    kwargs.pop("context", None)
    return make_conninfo(*args, **kwargs)
