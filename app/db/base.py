"""SQLAlchemy declarative base shared by all models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Common declarative base for every ORM model."""
    pass
