"""SQLAlchemy declarative base.

Tables are declared in ``app.db.models`` and imported here so that Alembic's
autogenerate sees the full metadata from a single import.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
