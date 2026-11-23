"""Shared price helpers, the base provider and the combined provider."""
from __future__ import annotations

import datetime
import logging
import math
from collections.abc import Callable, Mapping
from typing import Any

from ..const import PRICE_ADDER_DAY_BETWEEN_DIVIDER
from ..management.value_checks import price_unit_wrong
from ..utils.helpers import days_between, float_or_none, reset_time_to_hour
from .intervals import common_interval_minutes, interval_slots, provider_intervals

_LOGGER = logging.getLogger(__name__)


TARIFF_STATE_KEYS = (
    "stromligning_sell_electricity_tax",
    "stromligning_sell_nettariff",
    "stromligning_sell_systemtariff",
    "stromligning_sell_surcharge",
)

_ZERO_TARIFFS = {
    "transmissions_nettarif": 0.0,
    "systemtarif": 0.0,
    "elafgift": 0.0,
    "tariffs": 0.0,
    "surcharge": 0.0,
}

# hass.states.get (or anything returning an object with .state and .attributes, or None)
GetState = Callable[[str], Any]


def calculate_sell_price(
    price: float, tariffs: dict[str, float], sell_fee: float, sell_costs: dict[str, float] | None = None
) -> float:
    """Sell price: the tariffs, taxes and surcharge in the price are subtracted (spot price), and
    then what selling a kWh costs: the sell fee plus Energinet's net and system tariff.

    tariffs: the tariffs included in price. sell_costs: the net and system tariff paid when
    selling (default: those in tariffs), for a raw sell price that includes no tariffs.
    """
    costs = tariffs if sell_costs is None else sell_costs
    return round(
        price
        - sum(tariffs.values())
        - abs(sell_fee)
        - costs.get("transmissions_nettarif", 0.0)
        - costs.get("systemtarif", 0.0),
        2,
    )


def is_number(value) -> bool:
    """A usable price or tariff: a finite number (not None, text, a bool, NaN or infinity)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sell_adder_sign(config: dict) -> float:
    """+1 or -1: how the forecast adder moves a SELL price so that a sale is worth less.

    A sold kWh is worth the hourly sell price, so a forecast sell price gets the adder subtracted (-1). With a
    fixed solar sell price (production_price != -1) it is worth fixed price minus the hourly price, so the
    hourly price has to be raised (+1) to make the sale worth less.
    """
    return 1.0 if config["solar"]["production_price"] != -1.0 else -1.0


def _naive(timestamp):
    return timestamp.replace(tzinfo=None) if isinstance(timestamp, datetime.datetime) else timestamp


def price_at(prices: dict, timestamp: datetime.datetime):
    """(price timestamp, price) of the period containing timestamp (same day), else None."""
    best = None
    for price_timestamp in prices:
        if price_timestamp <= timestamp and price_timestamp.date() == timestamp.date():
            if best is None or price_timestamp > best:
                best = price_timestamp
    return None if best is None else (best, prices[best])


def periods_in_hour(prices: dict, timestamp: datetime.datetime) -> int:
    """Number of price periods in the hour of timestamp (4 for 15 min prices). At least 1."""
    hour_start = timestamp.replace(minute=0, second=0, microsecond=0)
    hour_end = hour_start + datetime.timedelta(hours=1)
    return sum(1 for ts in prices if hour_start <= ts < hour_end) or 1


class BasePriceProvider:
    """Prices from one integration."""

    source_name = "base"
    # shown in the what-if result
    display_name = ""
    # config keys of the buy and sell price entities
    buy_key = ""
    sell_key = ""

    def __init__(self, config: dict) -> None:
        prices_config = config["prices"]
        self.entity_ids: dict[str, str] = dict(prices_config["entity_ids"])
        self.sell_fee = abs(prices_config.get("sell_fee", 0.0))
        self.sell_adder_sign = sell_adder_sign(config)
        self.attributes: dict[str, dict] = {}
        self.values: dict[str, float] = {}
        self.current_hour = reset_time_to_hour(datetime.datetime.now())
        self.buy_real_prices: dict = {}
        self.buy_forecast_prices: dict = {}
        self.sell_real_prices: dict = {}
        self.sell_forecast_prices: dict = {}

    def update(self, price_data: dict, now: datetime.datetime) -> None:
        """Recalculate all prices. On error the previous prices are kept."""
        self.attributes = price_data.get("attributes", {})
        self.values = price_data.get("values", {})
        self.current_hour = reset_time_to_hour(now)
        buy_real = self._load_buy_real_prices()
        buy_forecast = self._load_buy_forecast_prices()
        sell_real = {ts: self.calculate_sell_price(ts, p) for ts, p in self._load_sell_real_prices().items()}
        sell_forecast = {ts: self.calculate_sell_price(ts, p) for ts, p in self._load_sell_forecast_prices().items()}
        self.buy_real_prices, self.buy_forecast_prices = buy_real, buy_forecast
        self.sell_real_prices, self.sell_forecast_prices = sell_real, sell_forecast

    def _attr(self, entity_id: str) -> dict:
        if not entity_id or entity_id not in self.attributes:
            raise ValueError(f"{entity_id} not loaded")
        return self.attributes[entity_id] or {}

    def _has(self, entity_id: str) -> bool:
        return bool(entity_id) and entity_id in self.attributes

    @classmethod
    def dashboard_entities(cls, prices: dict[str, str]) -> dict[str, str]:
        """Template entity id -> configured entity id ("" = not configured) of this provider's price chart.

        The chart is dashboard/prices/<source_name>.yaml; every external entity it uses is mapped here."""
        return {}

    def _forecast_adder(self, timestamp: datetime.datetime, sell: bool = False) -> float:
        """Forecast prices get a small penalty per day ahead to prefer known prices.

        A forecast buy price is dearer, and a sale at a forecast sell price is worth less (sell_adder_sign)."""
        adder = days_between(self.current_hour, timestamp) / PRICE_ADDER_DAY_BETWEEN_DIVIDER
        return adder * self.sell_adder_sign if sell else adder

    def calculate_sell_price(self, timestamp: datetime.datetime, price: float) -> float:
        tariff = self.get_tariffs(timestamp)
        return calculate_sell_price(price, tariff, self.sell_fee, self.get_sell_costs(timestamp))

    def buy_price_entity_id(self) -> str:
        return self.entity_ids.get(self.buy_key, "")

    def history_source(self, get_state: GetState) -> dict | None:
        """The recorded price history of this provider (the what-if), None without a buy entity."""
        buy_id = self.buy_price_entity_id()
        if not buy_id:
            return None
        return {
            "name": self.display_name,
            "buy_entity_id": buy_id,
            "sell_entity_id": self.entity_ids.get(self.sell_key) or None,
            "sell_fee": self.sell_fee,
        }

    def get_tariffs(self, timestamp: datetime.datetime) -> dict:
        """The tariffs included in the sell price."""
        return dict(_ZERO_TARIFFS)

    def get_sell_costs(self, timestamp: datetime.datetime) -> dict | None:
        """Net and system tariff paid when selling, None = those of get_tariffs()."""
        return None

    def _load_buy_real_prices(self) -> dict:
        raise NotImplementedError

    def _load_buy_forecast_prices(self) -> dict:
        return {}

    def _load_sell_real_prices(self) -> dict:
        raise NotImplementedError

    def _load_sell_forecast_prices(self) -> dict:
        return {}


class CombinedPriceProvider:
    """Every price of the integration, from the configured providers in PRICE_PROVIDER_PRIORITY
    order (Strømligning before Energi Data Service).

    For each timestamp the first provider with a price wins, and real prices always win over
    forecasts. The rest of the integration only asks this class: the coordinator which
    entities to read (snapshot, recorded history, what-if sources), the engine the prices.

    The Home Assistant side methods only read the configuration, so the coordinator can call
    them on the event loop while the worker updates the prices.
    """

    def __init__(self, config: dict, providers: list[BasePriceProvider]) -> None:
        self.providers = providers
        prices_config = config["prices"]
        self.entity_ids: dict[str, str] = dict(prices_config["entity_ids"])
        self.refund = abs(prices_config.get("refund", 0.0))
        self.sell_adder_sign = sell_adder_sign(config)
        self.current_hour = reset_time_to_hour(datetime.datetime.now())
        self.buy_prices: dict = {}
        self.sell_prices: dict = {}
        self.buy_real_prices: dict = {}
        self.sell_real_prices: dict = {}
        self.buy_price_sources: dict = {}
        self.sell_price_sources: dict = {}

    # Home Assistant side ------------------------------------------------- #

    def price_entity_ids(self) -> list[str]:
        """Every configured price entity (each once)."""
        return list(dict.fromkeys(entity_id for entity_id in self.entity_ids.values() if entity_id))

    def price_unit_problems(self, get_state: GetState) -> tuple[list[tuple[str, str]], bool]:
        """The configured price entities in a unit the prices are not read in (price_unit_wrong): (entity id, unit) of each,
        and whether every entity was judged. An entity with no state, or unavailable/unknown without a unit, is not judged."""
        problems: list[tuple[str, str]] = []
        judged = True
        for entity_id in self.price_entity_ids():
            state = get_state(entity_id)
            if state is None:
                judged = False
                continue
            unit = state.attributes.get("unit_of_measurement") or None
            if unit is None and state.state in ("unavailable", "unknown"):
                judged = False
                continue
            if price_unit_wrong(unit):
                problems.append((entity_id, unit))
        return problems, judged

    def buy_price_entity_ids(self) -> list[str]:
        """The buy price entities of the providers, in priority order."""
        return [entity_id for entity_id in (p.buy_price_entity_id() for p in self.providers) if entity_id]

    def snapshot(self, get_state: GetState) -> dict:
        """The price entities for update_prices(): {"attributes": {...}, "values": {...}}."""
        attributes: dict[str, dict] = {}
        values: dict[str, float] = {}
        tariff_entities = {self.entity_ids.get(key) for key in TARIFF_STATE_KEYS} - {None, ""}
        for entity_id in self.price_entity_ids():
            state = get_state(entity_id)
            if state is None:
                continue
            attributes[entity_id] = dict(state.attributes)
            if entity_id in tariff_entities:
                value = float_or_none(state.state)
                # unavailable: 0 as before. NaN or infinity stays: it gives a sell price that is dropped, not a too high one
                values[entity_id] = value if value is not None else 0.0
        return {"attributes": attributes, "values": values}

    def history_sources(self, get_state: GetState) -> list[dict]:
        """The recorded price history per provider, in priority order (the what-if)."""
        return [source for source in (p.history_source(get_state) for p in self.providers) if source]

    def combine_history(self, history: Mapping[str, Mapping]) -> dict:
        """Recorded buy prices {entity_id: {timestamp: price}} -> {timestamp: price}: per
        timestamp the price of the first provider that has one."""
        combined: dict = {}
        for entity_id in self.buy_price_entity_ids():
            for timestamp, price in (history.get(entity_id) or {}).items():
                combined.setdefault(timestamp, price)
        return combined

    # prices (worker) ----------------------------------------------------- #

    def update_prices(self, price_data: dict | None, now: datetime.datetime) -> None:
        self.current_hour = reset_time_to_hour(now)
        for provider in self.providers:
            try:
                provider.update(price_data or {}, now)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Can't update prices from %s: %s", provider.source_name, err)
        self.buy_prices, self.buy_real_prices, self.buy_price_sources = self._combine("buy")
        self.sell_prices, self.sell_real_prices, self.sell_price_sources = self._combine("sell")

    def _combine(self, kind: str) -> tuple[dict, dict, dict]:
        """The prices of every provider on one common interval (the finest all of them fit on).

        A provider's price covers the interval to its next timestamp (an hourly price the whole hour) and is
        repeated over every slot of it. Per slot the first provider with a real price wins, then the first with a
        forecast, so one hour never gets a falsely cheap quarter from an hourly price next to another provider's
        quarters. A price that is no finite number covers its slots without a price, so another provider fills them."""
        prices: dict = {}
        real: dict = {}
        sources: dict = {}
        unusable = 0
        layers = []
        for provider in self.providers:
            entries: dict = {}
            real_starts: set = set()
            for label in ("forecast", "real"):
                for timestamp, price in getattr(provider, f"{kind}_{label}_prices").items():
                    if label == "real" and not is_number(price) and is_number(entries.get(timestamp)):
                        continue  # a real price that is no number leaves the provider's forecast for it, as before
                    entries[timestamp] = price
                    if label == "real":
                        real_starts.add(timestamp)
            try:
                layers.append((provider, provider_intervals(entries), real_starts))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Can't read the %s prices of %s: %s %s", kind, provider.source_name, err, type(err))
        grid = common_interval_minutes(end - start for _provider, intervals, _real in layers for start, end, _price in intervals)
        for want_real in (True, False):
            label = "real" if want_real else "forecast"
            for provider, intervals, real_starts in layers:
                for start, end, price in intervals:
                    if (start in real_starts) != want_real:
                        continue
                    if not is_number(price):
                        # e.g. NaN from a broken tariff: no price is better than one that makes the plan NaN. The slots are
                        # filled from the price history (or the next provider), never invented
                        unusable += 1
                        continue
                    price = round(price, 2)
                    for slot in interval_slots(start, end, grid):
                        if slot not in prices:
                            prices[slot] = price
                            if want_real:
                                real[slot] = price
                            sources[slot] = f"{provider.source_name}:{label}"
        if unusable:
            _LOGGER.warning("Ignoring %s %s prices that are not a finite number", unusable, kind)
        return dict(sorted(prices.items())), dict(sorted(real.items())), dict(sorted(sources.items()))

    def get_buy_prices(self) -> dict:
        """Real and forecast buy prices {timestamp: price}. Shared - do not modify."""
        return self.buy_prices

    def get_sell_prices(self) -> dict:
        """Real and forecast sell prices {timestamp: price}. Shared - do not modify."""
        return self.sell_prices

    def get_buy_real_prices(self) -> dict:
        return self.buy_real_prices

    def get_sell_real_prices(self) -> dict:
        return self.sell_real_prices

    def get_buy_forecast_prices(self, adder: bool = True) -> dict:
        """The forecast buy prices; adder=False without the penalty per day ahead the providers
        add to prefer known prices."""
        return self._forecast(self.buy_prices, self.buy_price_sources, adder, 1.0)

    def get_sell_forecast_prices(self, adder: bool = True) -> dict:
        """The forecast sell prices; adder=False without the penalty (which makes a sale worth less)."""
        return self._forecast(self.sell_prices, self.sell_price_sources, adder, self.sell_adder_sign)

    def _forecast(self, prices: dict, sources: dict, adder: bool, sign: float) -> dict:
        return {
            timestamp: price if adder else price - sign * days_between(self.current_hour, timestamp) / PRICE_ADDER_DAY_BETWEEN_DIVIDER
            for timestamp, price in prices.items()
            if str(sources.get(timestamp, "")).endswith(":forecast")
        }

    def get_price_sources(self, sell_prices: bool = False) -> dict:
        """{timestamp: "<provider>:real" or "<provider>:forecast"}. Shared - do not modify."""
        return self.sell_price_sources if sell_prices else self.buy_price_sources

    def price_source(self, timestamp: datetime.datetime, sell_prices: bool = False) -> str:
        """"<provider>:real", "<provider>:forecast" or "" for the price at exactly timestamp."""
        return (self.sell_price_sources if sell_prices else self.buy_price_sources).get(timestamp, "")

    def is_forecast(self, timestamp: datetime.datetime, sell_prices: bool = False) -> bool:
        return self.price_source(timestamp, sell_prices).endswith(":forecast")

    def get_buy_price(self, timestamp: datetime.datetime) -> float | None:
        """The real buy price of the period containing timestamp, None if unknown."""
        result = price_at(self.buy_real_prices, timestamp)
        return None if result is None else result[1]

    def get_sell_price(self, timestamp: datetime.datetime) -> float | None:
        """The (real or forecast) sell price of the period containing timestamp, None if unknown."""
        result = price_at(self.sell_prices, timestamp)
        return None if result is None else result[1]

    def buy_prices_available(self) -> bool:
        return bool(self.buy_prices)

    def buy_price_entity_id(self) -> str:
        return ", ".join(self.buy_price_entity_ids())

    def buy_periods_in_hour(self, timestamp: datetime.datetime) -> int:
        """Number of buy price periods in the hour (4 with 15 min prices), also for forecast days."""
        return periods_in_hour(self.buy_prices, timestamp)

    def buy_period_end(self, timestamp: datetime.datetime) -> datetime.datetime:
        """Where the buy price period at timestamp ends: at the next buy price of its hour, else at the end of the hour.

        A price period the provider left out (a gap) is covered by the one before it here, so the periods of an hour
        never overlap; an hour without prices of its own (filled from the price history) is one period."""
        hour_end = reset_time_to_hour(timestamp) + datetime.timedelta(hours=1)
        later = [price_timestamp for price_timestamp in self.buy_prices if timestamp < price_timestamp < hour_end]
        return min(later) if later else hour_end

    def sell_periods_in_hour(self, timestamp: datetime.datetime) -> int:
        """Number of sell price periods in the hour (4 with 15 min prices), also for forecast days."""
        return periods_in_hour(self.sell_prices, timestamp)

    def current_price_timestamp(self, timestamp: datetime.datetime, sell_prices: bool = False):
        result = price_at(self.sell_real_prices if sell_prices else self.buy_real_prices, timestamp)
        return None if result is None else result[0]
