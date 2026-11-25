"""Battery level, charging loss and efficiency."""
from __future__ import annotations

import datetime
import logging
import math

from ..utils.capacity import capacity_summary, used_capacity
from ..utils.efficiency import EfficiencyCurve, efficiency_at_power, learned_efficiency_curve, parse_efficiency_curve
from ..utils.helpers import float_or_none
from ..utils.taper import TaperCaps, charge_energy_kwh, taper_caps

_LOGGER = logging.getLogger(__name__)

# How long ago a battery level sensor may have stopped giving numbers for its last recorded number to still be used
BATTERY_LEVEL_MAX_AGE = datetime.timedelta(hours=2)


class Battery:
    """Battery level, charging loss and efficiency."""

    def get_battery_level_or_none(self, stale_ok: bool = True) -> float | None:
        """The battery level from a real sample (the sensor, or with stale_ok its last number if it was lost less than
        BATTERY_LEVEL_MAX_AGE ago), None when there is none: never the fail-safe level of get_battery_level()."""
        entity_id = self.config["home"]["entity_ids"]["powerwall_battery_level_entity_id"]
        value = float_or_none(self.entity_state(entity_id))
        if value is not None and not math.isfinite(value):
            value = None  # "nan" and "inf" are not a battery level: handled like an unavailable sensor
        if value is None and stale_ok and self._wc is not None:
            value = self._wc.last_numeric(entity_id, self.now() - BATTERY_LEVEL_MAX_AGE)
        return value

    def get_battery_level(self) -> float:
        value = self.get_battery_level_or_none()
        if value is None:
            entity_id = self.config["home"]["entity_ids"]["powerwall_battery_level_entity_id"]
            _LOGGER.warning("Cant get the battery level from %s (no number for the last %s minutes), using 100", entity_id, int(BATTERY_LEVEL_MAX_AGE.total_seconds() // 60))
            return 100.0
        return value

    def charge_taper(self) -> TaperCaps:
        """The battery level bands where the battery takes less than its charge rate ((band start %, cap kW), ...), () when it
        doesn't taper, has too few measurements or the switch learn_charge_taper is off."""
        if "charge_taper" not in self._job_cache:
            self._job_cache["charge_taper"] = (
                taper_caps(self.analysis_db.get("taper"), self.max_kwh_charging) if self.enabled("learn_charge_taper") else ()
            )
        return self._job_cache["charge_taper"]

    def charge_rate_kwh(self, level: float | None, max_kwh: float | None = None) -> float:
        """kWh (AC) the battery can take in an hour that starts at this battery level: the charge rate, or less where the battery
        tapers (the hour is walked through the bands). Without a level, or a taper, the charge rate."""
        max_kwh = self.max_kwh_charging if max_kwh is None else max_kwh
        caps = self.charge_taper()
        if not caps or level is None:
            return max_kwh
        return charge_energy_kwh(caps, level, 1.0, max_kwh, self.percentage_to_kwh(1.0), self.battery_efficiency())

    def measured_efficiency_in_use(self) -> bool:
        """The plan uses a measured efficiency curve (for a direction without a configured one): it was measured with the configured
        battery size, so it already takes a size that is too big into account (utils/efficiency.py)."""
        if not self.enabled("enhance_battery_efficiency"):
            return False
        return any(
            not parse_efficiency_curve(self.config["solar"].get(f"battery_{direction}_efficiency_curve")) and self.learned_efficiency_curve(direction)
            for direction in ("charging", "discharging")
        )

    def battery_size_used(self) -> tuple[float, str]:
        """(kWh per 100 % of the battery level the plan counts on, where it comes from).

        The configured size ("configured"), except with the switch learn_battery_capacity on, when the measured capacity is clearly
        below it (utils/capacity.used_capacity: the charging bound, rounded down, never above the configured size): then that
        ("measured"). With a measured efficiency curve in use the configured size stays ("efficiency"): that curve was measured with
        it and already absorbs the difference, so changing the size too would correct twice. The measuring itself, the efficiency
        and the taper measurements, and the what-if (no measurements of its own) always use the configured size.

        It must never call charging_efficiency(), discharging_efficiency() or get_charging_loss(): get_charging_loss() calls
        percentage_to_kwh(), which calls this (measured_efficiency_in_use() only looks at the curves, never at the loss)."""
        if "battery_size" not in self._job_cache:
            configured = float(self.config["solar"]["powerwall_battery_size"])
            size, source = configured, "configured"
            if self.enabled("learn_battery_capacity"):
                database = getattr(self, "analysis_db", None) or {}
                learned = used_capacity(capacity_summary(database.get("capacity"), configured), configured)
                if learned is not None:
                    size, source = (configured, "efficiency") if self.measured_efficiency_in_use() else (learned, "measured")
            self._job_cache["battery_size"] = (size, source)
        return self._job_cache["battery_size"]

    def kwh_to_percentage(self, kwh: float) -> float:
        return kwh / self.battery_size_used()[0] * 100

    def percentage_to_kwh(self, percentage: float) -> float:
        return percentage * self.battery_size_used()[0] / 100

    def kwh_needed_for_charging(self, target_level=None, battery=None) -> float:
        target_level = self.config["solar"]["powerwall_battery_level_min"] if target_level is None else target_level
        battery = self.get_battery_level() if battery is None else battery
        return max(round(self.percentage_to_kwh(target_level - battery), 2), 0.0)

    def get_charging_loss(self) -> float:
        """Round trip loss: configured, or measured after one battery capacity of charging."""
        if "charging_loss" in self._job_cache:
            return self._job_cache["charging_loss"]
        solar = self.config["solar"]
        loss = solar["powerwall_charge_discharge_loss"]
        charged_kwh = self.charging_history_total.get("charged_kwh", 0.0)
        if solar["use_charge_discharge_loss_from_history"] and charged_kwh >= solar["powerwall_battery_size"]:
            in_battery = self.percentage_to_kwh(self.get_battery_level() - solar["powerwall_battery_level_min"])
            discharged_kwh = self.charging_history_total.get("discharged_kwh", 0.0) + in_battery
            loss = 1 - discharged_kwh / charged_kwh
        self._job_cache["charging_loss"] = loss
        return loss

    def _fallback_efficiency(self) -> float:
        return math.sqrt(1.0 - min(abs(self.get_charging_loss()), 0.9))

    def learned_efficiency_curve(self, direction: str) -> EfficiencyCurve:
        """The curve measured by the analysis ("charging"/"discharging"), empty until there are enough measurements."""
        key = f"learned_efficiency_{direction}"
        if key not in self._job_cache:
            database = getattr(self, "analysis_db", None) or {}
            steps = (database.get("efficiency") or {}).get(direction)
            self._job_cache[key] = learned_efficiency_curve(steps, direction == "charging")
        return self._job_cache[key]

    def efficiency_curve(self, direction: str) -> EfficiencyCurve:
        """The configured curve, else the measured one; none while enhanced efficiency is disabled."""
        if not self.enabled("enhance_battery_efficiency"):
            return ()
        return parse_efficiency_curve(self.config["solar"].get(f"battery_{direction}_efficiency_curve")) or self.learned_efficiency_curve(direction)

    def charging_efficiency_curve(self) -> EfficiencyCurve:
        return self.efficiency_curve("charging")

    def discharging_efficiency_curve(self) -> EfficiencyCurve:
        return self.efficiency_curve("discharging")

    def charging_efficiency(self, power_kw: float | None = None) -> float:
        """Charging efficiency at AC power, falling back to half the round-trip loss."""
        power_kw = self.max_kwh_charging if power_kw is None else power_kw
        return efficiency_at_power(self.charging_efficiency_curve(), power_kw * 1000.0, self._fallback_efficiency())

    def discharging_efficiency(self, power_kw: float | None = None) -> float:
        """Discharging efficiency at AC power, falling back to half the round-trip loss."""
        power_kw = abs(self.max_kwh_discharging) if power_kw is None else power_kw
        return efficiency_at_power(self.discharging_efficiency_curve(), power_kw * 1000.0, self._fallback_efficiency())

    def battery_efficiency(self) -> float:
        """Equivalent one-way efficiency used by the legacy rule planner."""
        return math.sqrt(1.0 - self.effective_round_trip_loss())

    def effective_round_trip_loss(self) -> float:
        """Round-trip loss at configured maximum power, including optional curves."""
        return 1.0 - self.charging_efficiency() * self.discharging_efficiency()

    def calc_battery_loss_cost(self, price: float) -> float:
        """Loss part of the price of 1 kWh out of the battery: it costs price / (1 - round trip loss)."""
        loss = self.effective_round_trip_loss()
        return price * loss / (1.0 - loss)
