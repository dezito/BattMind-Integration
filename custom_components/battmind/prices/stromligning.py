"""Strømligning price provider."""
from __future__ import annotations

import datetime
import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import FLOW_STEPS
from .base import BasePriceProvider, GetState, is_number, price_at, price_timestamp

_LOGGER = logging.getLogger(__name__)


class StromligningPriceProvider(BasePriceProvider):
    """Strømligning ('prices' attribute with start/price, tariffs as separate sensors)."""

    source_name = "stromligning"
    display_name = "Strømligning"
    buy_key = "stromligning_buy_current_price"
    countries = frozenset({"DK"})  # Strømligning is a Danish service
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

    def update(self, price_data: dict, now: datetime.datetime, tz: datetime.tzinfo | None = None) -> None:
        self.tz = tz  # before the distribution rows are read
        self.attributes = price_data.get("attributes", {})
        self._distribution_prices = {}
        if self._has(self.sell_distribution):
            self._distribution_prices = dict(self._rows(self.attributes[self.sell_distribution].get("prices")))
        super().update(price_data, now, tz)

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

    def _rows(self, rows):
        for raw in rows or []:
            timestamp = price_timestamp(raw.get("start"), self.tz)
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

    @classmethod
    def dashboard_entities(cls, prices: dict[str, str]) -> dict[str, str]:
        return {
            "sensor.stromligning_current_price_vat": prices["stromligning_buy_current_price"],
            "sensor.stromligning_forecasts_vat": prices["stromligning_buy_forecasts"],
            "binary_sensor.stromligning_tomorrow_available_vat": prices["stromligning_buy_tomorrow_available"],
            # the sell price chart uses the configured sell sensors and tariffs, like the planner
            "sensor.stromligning_15min_current_price_ex_vat": prices["stromligning_sell_current_price"],
            "sensor.stromligning_15min_forecasts_ex_vat": prices["stromligning_sell_forecasts"],
            "binary_sensor.stromligning_15min_tomorrow_available_ex_vat": prices["stromligning_sell_tomorrow_available"],
            "sensor.stromligning_15min_distribution_ex_vat": prices["stromligning_sell_distribution"],
            "sensor.stromligning_15min_electricity_tax_ex_vat": prices["stromligning_sell_electricity_tax"],
            "sensor.stromligning_15min_nettariff_ex_vat": prices["stromligning_sell_nettariff"],
            "sensor.stromligning_15min_systemtariff_ex_vat": prices["stromligning_sell_systemtariff"],
            "sensor.stromligning_15min_surcharge_ex_vat": prices["stromligning_sell_surcharge"],
        }

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

# ---------------------------------------------------------------------------------------------------------------------
# Flow time and repairs: no provider exists yet, so these read the options / the registry themselves - here, in prices/
# ---------------------------------------------------------------------------------------------------------------------

STROMLIGNING_PLATFORM = "stromligning"

# the Strømligning sensors that can be selected on the price pages (the start of their unique ids)
STROMLIGNING_UNIQUE_ID_PREFIXES = (
    "current_price",
    "tomorrow_available",
    "forecasts",
    "distribution",
    "electricity_tax",
    "nettariff",
    "systemtariff",
    "surcharge",
)


def stromligning_entities_to_watch(prices: dict[str, str]) -> list[str]:
    """The configured Strømligning sensors that count: the tariff sensors only with a sell sensor (their page is hidden without it)."""
    tariffs = FLOW_STEPS["prices_stromligning_tariffs"] if not prices.get("stromligning_sell_current_price") else []
    return [entity_id for key, entity_id in prices.items() if key.startswith("stromligning_") and entity_id and key not in tariffs]


def stromligning_problems(hass: HomeAssistant, entity_ids: list[str]) -> dict[str, list[str]]:
    """Which of the configured Strømligning sensors are disabled, and which the integration doesn't provide.

    A sensor with no state, or only the state Home Assistant restores for an entity nobody provides, is not provided
    (e.g. the price forecasts when they are off in the options of Strømligning). That is only a finding while the
    Strømligning entry that owns the sensor runs: during an outage every sensor is unavailable, which is not a setting.
    """
    registry = er.async_get(hass)
    disabled: list[str] = []
    not_provided: list[str] = []
    for entity_id in dict.fromkeys(entity_ids):
        entry = registry.async_get(entity_id)
        if entry is not None and entry.disabled_by:
            disabled.append(entity_id)
            continue
        state = hass.states.get(entity_id)
        if (state is None or state.attributes.get("restored")) and _integration_running(hass, entry):
            not_provided.append(entity_id)
    return {"disabled": disabled, "not_provided": not_provided}


def _integration_running(hass: HomeAssistant, entry: er.RegistryEntry | None) -> bool:
    """Whether the Strømligning entry that owns the sensor runs (there can be one for buying and one for selling).

    Without a registry entry (the sensor was never created) any running Strømligning entry will do.
    """
    owner = hass.config_entries.async_get_entry(entry.config_entry_id) if entry is not None and entry.config_entry_id else None
    if owner is not None:
        return owner.state is ConfigEntryState.LOADED
    return any(candidate.state is ConfigEntryState.LOADED for candidate in hass.config_entries.async_entries(STROMLIGNING_PLATFORM))


def has_stromligning_sell_prices(options: dict) -> bool:
    """Whether the options (the flat options of a config entry) have a Strømligning sell sensor, so the tariff page is needed."""
    return bool(options.get(StromligningPriceProvider.sell_key))
