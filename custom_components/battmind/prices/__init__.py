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
from .energidataservice import EnergiDataServicePriceProvider
from .factory import create_price_provider
from .stromligning import StromligningPriceProvider

__all__ = [
    "BasePriceProvider",
    "CombinedPriceProvider",
    "EnergiDataServicePriceProvider",
    "StromligningPriceProvider",
    "create_price_provider",
    "periods_in_hour",
    "price_at",
]
