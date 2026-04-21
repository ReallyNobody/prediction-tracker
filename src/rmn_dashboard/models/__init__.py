"""SQLAlchemy ORM models.

Every model must be imported here so Alembic's autogenerate sees it in
``Base.metadata``. Missing imports silently produce empty migrations.
"""

from rmn_dashboard.models.base import Base, TimestampMixin
from rmn_dashboard.models.carrier_exposure import CarrierExposure
from rmn_dashboard.models.cat_bond_quote import CatBondQuote
from rmn_dashboard.models.cat_loss import CatLoss
from rmn_dashboard.models.daily_snapshot import DailySnapshot
from rmn_dashboard.models.forecast import Forecast
from rmn_dashboard.models.prediction_market import PredictionMarket
from rmn_dashboard.models.storm import Storm

__all__ = [
    "Base",
    "TimestampMixin",
    "CatLoss",
    "Storm",
    "Forecast",
    "CarrierExposure",
    "CatBondQuote",
    "PredictionMarket",
    "DailySnapshot",
]
