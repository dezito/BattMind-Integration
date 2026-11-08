"""Strømligning price provider."""
from __future__ import annotations

import datetime
import logging

from ..utils.helpers import to_datetime
from .base import BasePriceProvider, GetState, _naive, is_number, price_at

_LOGGER = logging.getLogger(__name__)


class StromligningPriceProvider(BasePriceProvider):
    """Strømligning ('prices' attribute with start/price, tariffs as separate sensors)."""

    source_name = "stromligning"
    display_name = "Strømligning"
    buy_key = "stromligning_buy_current_price"
    sell_key = "stromligning_sell_current_price"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        get = self.entity_ids.get
        self.buy_current_price = get(self.buy_key, "")
        self.buy_tomorrow_available = get("stromligning_buy_tomorrow_available", "")
        self.buy_forecasts = get("stromligning_buy_forecasts", "")
        self.sell_current_price = get(self.sell_key, "")
        self.sell_tomorrow_available = get("stromligning_sell_tomorrow_available", "")
        self.sell_forecasts = get("stromligning_sell_forecasts", "")
        self.sell_distribution = get("stromligning_sell_distribution", "")
        self.sell_electricity_tax = get("stromligning_sell_electricity_tax", "")
        self.sell_nettariff = get("stromligning_sell_nettariff", "")
        self.sell_systemtariff = get("stromligning_sell_systemtariff", "")
        self.sell_surcharge = get("stromligning_sell_surcharge", "")
        self._distribution_prices: dict = {}

    def update(self, price_data: dict, now: datetime.datetime) -> None:
        self.attributes = price_data.get("attributes", {})
        self._distribution_prices = {}
        if self._has(self.sell_distribution):
            self._distribution_prices = dict(self._rows(self.attributes[self.sell_distribution].get("prices")))
        super().update(price_data, now)

    def history_source(self, get_state: GetState) -> dict | None:
        source = super().history_source(get_state)
        if source:
            source["sell_tariff_entity_ids"] = {
                tariff: self.entity_ids[key]
                for tariff, key in (
                    ("transmissions_nettarif", "stromligning_sell_nettariff"),
                    ("systemtarif", "stromligning_sell_systemtariff"),
                    ("elafgift", "stromligning_sell_electricity_tax"),
                    ("tariffs", "stromligning_sell_distribution"),
                    ("surcharge", "stromligning_sell_surcharge"),
                )
                if self.entity_ids.get(key)
            }
        return source

    @staticmethod
    def _rows(rows):
        for raw in rows or []:
            timestamp = _naive(to_datetime(raw.get("start")))
            price = raw.get("price")
            if isinstance(timestamp, datetime.datetime) and is_number(price):
                yield timestamp, price

    def _add(self, prices: dict, entity_id: str, forecast: bool, sell: bool = False) -> None:
        if not entity_id:
            return
        attr = self._attr(entity_id)
        if "prices" not in attr:
            raise ValueError(f"prices not in {entity_id} attributes")
        for timestamp, price in self._rows(attr["prices"]):
            if timestamp not in prices:
                prices[timestamp] = price + self._forecast_adder(timestamp, sell) if forecast else price

    def _tomorrow_is_forecast(self, entity_id: str) -> bool | None:
        """None when the entity is missing, else whether its prices are forecasts."""
        if not self._has(entity_id):
            return None
        return bool(self.attributes[entity_id].get("forecast_data", False))

    def _real(self, current_entity_id: str, tomorrow_entity_id: str) -> dict:
        prices: dict = {}
        self._add(prices, current_entity_id, forecast=False)
        if self._tomorrow_is_forecast(tomorrow_entity_id) is False:
            self._add(prices, tomorrow_entity_id, forecast=False)
        return dict(sorted(prices.items()))

    def _forecast(self, forecast_entity_id: str, tomorrow_entity_id: str, sell: bool = False) -> dict:
        prices: dict = {}
        if self._tomorrow_is_forecast(tomorrow_entity_id):
            self._add(prices, tomorrow_entity_id, forecast=True, sell=sell)
        self._add(prices, forecast_entity_id, forecast=True, sell=sell)
        return dict(sorted(prices.items()))

    def _load_buy_real_prices(self):
        return self._real(self.buy_current_price, self.buy_tomorrow_available)

    def _load_buy_forecast_prices(self):
        return self._forecast(self.buy_forecasts, self.buy_tomorrow_available)

    def _load_sell_real_prices(self):
        return self._real(self.sell_current_price, self.sell_tomorrow_available)

    def _load_sell_forecast_prices(self):
        return self._forecast(self.sell_forecasts, self.sell_tomorrow_available, sell=True)

    def _state(self, entity_id: str) -> float:
        value = self.values.get(entity_id, 0.0) if self._has(entity_id) else 0.0
        # a missing tariff is 0 as before; NaN or infinity is kept on purpose: the sell price is then dropped (CombinedPriceProvider)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    def _distribution(self, timestamp: datetime.datetime) -> float:
        found = price_at(self._distribution_prices, timestamp)
        if found is None and self._distribution_prices:
            # The sensor only has today's tariffs, but tomorrow's and the forecast prices include
            # them too. The tariff follows the time of day: use the latest known day's period.
            latest = max(self._distribution_prices).date()
            found = price_at(self._distribution_prices, datetime.datetime.combine(latest, timestamp.time()))
        return found[1] if found else 0.0

    def get_tariffs(self, timestamp):
        # Same keys as Energi Data Service: "tariffs" is the grid company's tariff of the
        # period (distribution), "transmissions_nettarif" Energinet's net tariff.
        return {
            "transmissions_nettarif": self._state(self.sell_nettariff),
            "systemtarif": self._state(self.sell_systemtariff),
            "elafgift": self._state(self.sell_electricity_tax),
            "tariffs": self._distribution(timestamp),
            "surcharge": self._state(self.sell_surcharge),
        }
