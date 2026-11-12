"""SQLAlchemy tables (design §4). Tables carry no schema: `search_path` puts them in the configured
schema (storage/db.py)."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


__all__ = ["Base"]
