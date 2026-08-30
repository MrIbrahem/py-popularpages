"""
SQLAlchemy model backing a single wiki/month pageviews cache file.

Each ``data/views/<wiki>/<YYYY-MM>.sqlite3`` file has exactly one table with
this schema. The wiki and month are encoded in the file path (mirroring the
old JSONL-per-file layout), so they are not repeated as columns here.
"""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class PageView(Base):
    """A single article title's total monthly pageviews for one wiki/month."""

    __tablename__ = "pageviews"

    title: Mapped[str] = mapped_column(String, primary_key=True)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"PageView(title={self.title!r}, views={self.views!r})"


__all__ = [
    "Base",
    "PageView",
]
