"""Power measurements, weather/solar prediction and the hourly databases (engine part)."""
from __future__ import annotations

import datetime
import logging
import math
from copy import deepcopy

from ...const import LEARNING_MIN_DAYS
from ...utils.helpers import average, get_specific_values, reset_time_to_hour, start_of_day
from ...utils.power import unit_factor_to_w

from .forecast import EnergyForecast
from .price import EnergyPrice
from .solar import EnergySolar

_LOGGER = logging.getLogger(__name__)


POWER_VALUE_KEYS = (
    "power_consumption",
    "ignored_consumption",
    "powerwall_charging_consumption",
    "powerwall_discharging_consumption",
    "solar_production",
    "power_consumption_without_ignored",
    "power_consumption_without_all_exclusion",
)
# the key the consumption forecast is made from
CONSUMPTION_KEY = "power_consumption_without_all_exclusion"


def derive_power_values(home: dict, consumption: float, ignored: float, charging: float, discharging: float, solar: float) -> dict:
    """POWER_VALUE_KEYS from the measured W (house consumption corrected for the battery like configured).

    After the correction the consumption never contains the battery charging, so it is not subtracted again.
    """
    if not home["power_consumption_entity_id_include_powerwall_discharging"]:
        consumption += discharging
    if home["power_consumption_entity_id_include_powerwall_charging"]:
        consumption -= charging
    without_ignored = round(consumption - ignored, 2)
    return {
        "power_consumption": consumption,
        "ignored_consumption": ignored,
        "powerwall_charging_consumption": charging,
        "powerwall_discharging_consumption": discharging,
        "solar_production": solar,
        "power_consumption_without_ignored": without_ignored,
        "power_consumption_without_all_exclusion": max(without_ignored, 0.0),
    }


class Energy(EnergyForecast, EnergySolar, EnergyPrice):
    """Power measurements, solar prediction and the databases filled every hour."""

    # ------------------------------------------------------------------ #
    # Power values (W, averaged over a window)
    # ------------------------------------------------------------------ #

    def _window(self, from_timestamp, to_timestamp, period):
        if period is not None:
            to_timestamp = self.now()
            from_timestamp = to_timestamp - datetime.timedelta(minutes=period)
        return from_timestamp, to_timestamp

    def get_powerwall_values(self, from_timestamp, to_timestamp) -> list[float]:
        """Interpolated battery power flow in W (100 points)."""
        entity_id = self.config["home"]["entity_ids"]["powerwall_watt_flow_entity_id"]
        return self._wc.short_values(entity_id, from_timestamp, to_timestamp) or [0.0]

    def _charging_watt(self, powerwall_values) -> float:
        positive_is_charging = self.config["home"]["invert_powerwall_watt_flow_entity_id"]
        values = get_specific_values(powerwall_values, positive_only=positive_is_charging, negative_only=not positive_is_charging)
        return abs(round(average(values), 0))

    def _discharging_watt(self, powerwall_values) -> float:
        positive_is_charging = self.config["home"]["invert_powerwall_watt_flow_entity_id"]
        values = get_specific_values(powerwall_values, positive_only=not positive_is_charging, negative_only=positive_is_charging)
        return abs(round(average(values), 0))

    def power_values(self, from_timestamp=None, to_timestamp=None, period=None) -> dict:
        """Average power (W) of consumption, battery and solar in a window. Memoised."""
        from_timestamp, to_timestamp = self._window(from_timestamp, to_timestamp, period)
        key = (from_timestamp, to_timestamp)
        if key not in self._power_cache:
            self._power_cache[key] = self._calculate_power_values(from_timestamp, to_timestamp)
        return self._power_cache[key]

    def _calculate_power_values(self, from_timestamp, to_timestamp) -> dict:
        home = self.config["home"]
        average_w = self._wc.average
        result = dict.fromkeys(POWER_VALUE_KEYS, 0.0)
        try:
            consumption = abs(round(float(average_w(home["entity_ids"]["power_consumption_entity_id"], from_timestamp, to_timestamp)), 2))
            ignored = abs(round(sum(
                float(average_w(entity_id, from_timestamp, to_timestamp))
                for entity_id in home["entity_ids"]["ignore_consumption_from_entity_ids"] or []
                if entity_id
            ), 2))
            powerwall_values = self.get_powerwall_values(from_timestamp, to_timestamp)
            charging = self._charging_watt(powerwall_values)
            discharging = self._discharging_watt(powerwall_values)
            solar = abs(round(float(average_w(self.config["solar"]["entity_ids"]["production_entity_id"], from_timestamp, to_timestamp)), 2))
            result.update(derive_power_values(home, consumption, ignored, charging, discharging, solar))
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to get power values from %s to %s: %s", from_timestamp, to_timestamp, err)
            self.notify(
                f"Failed to get power values from {from_timestamp} to {to_timestamp}: {err} {type(err)}",
                title=f"{self.title} error",
                notification_id=f"{self.name}_power_values",
            )
        return result

    # ------------------------------------------------------------------ #
    # Weather forecast
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Power values database: power per hour of the day (newest first)
    # ------------------------------------------------------------------ #

    def load_power_values_db(self, database) -> None:
        database = deepcopy(database) if isinstance(database, dict) else {}
        database.pop("version", None)
        for hour in range(24):
            values = database.setdefault(hour, {})
            for key in [k for k in values if k not in POWER_VALUE_KEYS]:
                del values[key]
        self.power_values_db = database

    def power_values_to_db(self, data: dict) -> None:
        # Recording only needs the consumption entity (the PyScript version also required solar).
        if not data or not self._power_consumption_available():
            return
        if not self.power_values_db:
            self.load_power_values_db({})
        keep = self.config["database"]["power_values_db_data_to_save"]
        hour_values = self.power_values_db[self.now().hour]
        for key, value in data.items():
            hour_values[key] = [[self.now(), value], *hour_values.get(key, [])][:keep]
        self._job_cache.pop("learning", None)
        self.mark_dirty("power_values")

    def consumption_days_known(self) -> int:
        """Days of consumption history of the hour of the day with the fewest."""
        return min(len(self.power_values_db.get(hour, {}).get(CONSUMPTION_KEY) or []) for hour in range(24))

    def is_learning(self) -> bool:
        """Too little consumption history to bet on the forecast (LEARNING_MIN_DAYS)."""
        if "learning" not in self._job_cache:
            self._job_cache["learning"] = self.consumption_days_known() < LEARNING_MIN_DAYS
        return self._job_cache["learning"]

    def power_values_backfill(self, statistics: dict) -> int:
        """Fill the hours missing in the consumption history from the hourly mean statistics.

        statistics: {entity_id: [(hour start epoch, mean)]}. Runs at every start and only adds
        days an hour of the day doesn't have yet, so a new installation gets a forecast right away
        and hours where Home Assistant was not running are filled later. An hour is only added when
        the consumption, the ignored consumers and the battery flow all have a mean (a missing
        solar mean counts as 0 W). The battery flow is the net mean of the hour, so an hour that
        both charged and discharged only counts the difference. Returns the number of hours added.
        """
        home = self.config["home"]
        ids = home["entity_ids"]
        consumption_id = ids["power_consumption_entity_id"]
        if not consumption_id or self._snap is None:
            return 0
        flow_id = ids["powerwall_watt_flow_entity_id"]
        solar_id = self.config["solar"]["entity_ids"]["production_entity_id"]
        ignored_ids = [entity_id for entity_id in ids["ignore_consumption_from_entity_ids"] or [] if entity_id]
        required = [consumption_id, *ignored_ids, *([flow_id] if flow_id else [])]

        means: dict[str, dict[datetime.datetime, float]] = {}
        for entity_id, rows in (statistics or {}).items():
            factor = unit_factor_to_w(self._snap.units.get(entity_id))
            means[entity_id] = {
                datetime.datetime.fromtimestamp(timestamp, self._snap.tz).replace(tzinfo=None): float(mean) * factor
                for timestamp, mean in rows
                if isinstance(mean, (int, float)) and math.isfinite(mean)
            }

        if not self.power_values_db:
            self.load_power_values_db({})
        keep = int(self.config["database"]["power_values_db_data_to_save"])
        current_hour = reset_time_to_hour(self.now())
        first = start_of_day(self.now()) - datetime.timedelta(days=keep)
        added = 0
        for hour_start in sorted(means.get(consumption_id, {})):
            if not first <= hour_start < current_hour or any(hour_start not in means.get(e, {}) for e in required):
                continue
            hour_values = self.power_values_db.setdefault(hour_start.hour, {})
            known_days = {row[0].date() for row in hour_values.get(CONSUMPTION_KEY) or [] if isinstance(row[0], datetime.datetime)}
            if hour_start.date() in known_days:
                continue
            flow = [means[flow_id][hour_start]] if flow_id else [0.0]
            values = derive_power_values(
                home,
                abs(means[consumption_id][hour_start]),
                abs(sum(means[e][hour_start] for e in ignored_ids)),
                self._charging_watt(flow),
                self._discharging_watt(flow),
                abs(means.get(solar_id, {}).get(hour_start, 0.0)) if solar_id else 0.0,
            )
            # stored like hour_end() does: at minute 59 of the hour
            stamp = hour_start.replace(minute=59)
            for key, value in values.items():
                rows = [*(hour_values.get(key) or []), [stamp, round(value, 2)]]
                rows.sort(key=lambda row: row[0] if isinstance(row[0], datetime.datetime) else datetime.datetime.min, reverse=True)
                hour_values[key] = rows[:keep]
            added += 1
        if added:
            self._job_cache.pop("learning", None)
            self.mark_dirty("power_values")
            _LOGGER.info("Consumption history: %s hours added from the long-term statistics", added)
        return added

    # ------------------------------------------------------------------ #
    # Solar database: solar surplus per hour and weather score (newest first)
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Price history: price per hour and weekday (newest first) + daily max/mean/min
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Solar prediction
    # ------------------------------------------------------------------ #
