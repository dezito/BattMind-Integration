"""Price providers (Energi Data Service and Strømligning).

Everything about prices goes through ``CombinedPriceProvider`` (see ``create_price_provider``):
the coordinator asks it which entities to read and gives it a plain snapshot of them on the
event loop::

    {"attributes": {entity_id: {...}}, "values": {entity_id: float}}

and the engine asks it for the ``{naive local datetime: price}`` dicts it calculates from that
in the worker. With several providers the first one (``PRICE_PROVIDER_PRIORITY``: Strømligning,
then Energi Data Service) with a price for a timestamp wins; real prices always win over
forecasts.
"""
from .base import BasePriceProvider, CombinedPriceProvider, periods_in_hour, price_at
from .currency import detect_price_scale
from .energidataservice import EnergiDataServicePriceProvider
from .dashboard import price_dashboard_entities
from .factory import create_price_provider, providers_from_options
from .stromligning import (
    STROMLIGNING_PLATFORM,
    STROMLIGNING_UNIQUE_ID_PREFIXES,
    StromligningPriceProvider,
    has_stromligning_sell_prices,
    stromligning_entities_to_watch,
    stromligning_problems,
)

__all__ = [
    "STROMLIGNING_PLATFORM",
    "STROMLIGNING_UNIQUE_ID_PREFIXES",
    "BasePriceProvider",
    "CombinedPriceProvider",
    "EnergiDataServicePriceProvider",
    "StromligningPriceProvider",
    "create_price_provider",
    "detect_price_scale",
    "has_stromligning_sell_prices",
    "price_dashboard_entities",
    "providers_from_options",
    "stromligning_entities_to_watch",
    "stromligning_problems",
    "periods_in_hour",
    "price_at",
]
