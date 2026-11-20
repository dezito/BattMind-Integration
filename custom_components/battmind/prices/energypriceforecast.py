"""Energy Price Forecast price provider (a price and its forecast for most of Europe, also outside Denmark)."""
from __future__ import annotations

import datetime

from ..utils.helpers import days_between
from .base import BasePriceProvider, is_number, price_timestamp, price_unit_divisor


class EnergyPriceForecastPriceProvider(BasePriceProvider):
    """The Energy Price Forecast integration (domain energypriceforecast): raw_today / raw_tomorrow are published prices, raw_forecast is the forecast.

    Every row is {"start", "end", "value"}: the start is an ISO timestamp with an offset (UTC with a "Z" is normal) that is
    converted to Home Assistant's time zone, and a row is an hour or a quarter of an hour. The buy sensor is the retail
    price sensor (the price a kWh costs the household). The config flow requires a sell sensor (the wholesale price: the
    retail price is higher than what a sale pays). An entry without one (a restored backup or an import) takes the buy
    sensor's price, which is higher than a sale pays. The price has no tariffs attribute, so no
    tariff is taken out of the sell price: it is the price minus the sell fee.
    """

    source_name = "energypriceforecast"
    display_name = "Energy Price Forecast"
    buy_key = "energypriceforecast_buy_entity_id"
    sell_key = "energypriceforecast_sell_entity_id"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.buy_entity_id = self.entity_ids.get(self.buy_key, "")
        self.sell_entity_id = self.entity_ids.get(self.sell_key, "") or self.buy_entity_id

    @classmethod
    def dashboard_entities(cls, prices: dict[str, str]) -> dict[str, str]:
        return {
            "sensor.energy_price_forecast": prices["energypriceforecast_buy_entity_id"],
            "sensor.energy_price_forecast_sell": prices["energypriceforecast_sell_entity_id"],
        }

    def _divisor(self, entity_id: str) -> float:
        """1000 for a sensor that gives the price per MWh (its prices are converted to kWh), else 1."""
        return price_unit_divisor(self._attr(entity_id).get("unit_of_measurement"))

    def _rows(self, rows, divisor: float = 1.0):
        """(start as naive local time, price per kWh) of every row with a timestamp and a number; the first row of a start wins
        (the repeated hour when the clocks go back is one start in local time, like the other providers)."""
        seen = set()
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, dict):
                continue
            timestamp = price_timestamp(raw.get("start"), self.tz)
            price = raw.get("value")
            if isinstance(timestamp, datetime.datetime) and is_number(price) and timestamp not in seen:
                seen.add(timestamp)
                yield timestamp, price / divisor

    def _real(self, entity_id: str) -> dict:
        return dict(sorted(self._published(entity_id).items()))

    def _published(self, entity_id: str) -> dict:
        """The published prices of today and tomorrow, from both lists: the integration may split the days by its own
        market day or roll the lists over after midnight, so a row is judged by its own local date, not by its list
        (today's rows can still be in raw_tomorrow, and an hour of tomorrow in raw_today). The first row of a start wins."""
        attr = self._attr(entity_id)
        prices: dict = {}
        for attribute in ("raw_today", "raw_tomorrow"):
            for timestamp, price in self._rows(attr.get(attribute), self._divisor(entity_id)):
                if 0 <= days_between(self.current_hour, timestamp) <= 1:
                    prices.setdefault(timestamp, price)
        return prices

    def _forecast(self, entity_id: str, sell: bool = False) -> dict:
        attr = self._attr(entity_id)
        real = self._published(entity_id)
        # a forecast never replaces a published price (the rows do not overlap, but a start must not be both)
        return {
            ts: p + self._forecast_adder(ts, sell)
            for ts, p in sorted(self._rows(attr.get("raw_forecast"), self._divisor(entity_id)))
            if days_between(self.current_hour, ts) > 0 and ts not in real
        }

    def _load_buy_real_prices(self):
        return self._real(self.buy_entity_id)

    def _load_buy_forecast_prices(self):
        return self._forecast(self.buy_entity_id)

    def _load_sell_real_prices(self):
        return self._real(self.sell_entity_id)

    def _load_sell_forecast_prices(self):
        return self._forecast(self.sell_entity_id, sell=True)
