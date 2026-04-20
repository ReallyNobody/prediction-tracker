"""SQLAlchemy ORM models.

Every model must be imported here so Alembic's autogenerate sees it in
``Base.metadata``. Missing imports silently produce empty migrations.
"""

from rmn_dashboard.models.base import Base, TimestampMixin
from rmn_dashboard.models.cat_loss import CatLoss

__all__ = ["Base", "TimestampMixin", "CatLoss"]
