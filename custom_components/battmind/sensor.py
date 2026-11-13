"""BattMind output sensors."""
from __future__ import annotations

import math

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import MATCH_ALL, PERCENTAGE, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import POWERWALL_ACTION_STATES, SENSOR_KEYS, WHAT_IF_STATUSES
from .coordinator import BattMindCoordinator
from .entity import BattMindUpdatingEntity

# the entities read the coordinator's data and change only the integration's own settings
PARALLEL_UPDATES = 0

MAX_STATE_LENGTH = 255


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BattMindCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        BattMindSensor(coordinator, key) for key in SENSOR_KEYS if key != "kwh_charged_by_solar"
    ]
    if coordinator.config["solar"]["entity_ids"]["production_entity_id"]:
        entities.append(BattMindSolarKwhSensor(coordinator))
    entities.extend((BattMindWhatIfSensor(coordinator), BattMindWhatIfStatusSensor(coordinator), BattMindWhatIfProgressSensor(coordinator)))
    async_add_entities(entities)


class BattMindSensor(BattMindUpdatingEntity, SensorEntity):
    """State and attributes published by the BattMind worker."""

    # the markdown/plan attributes are large; keep them out of the recorder database
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator: BattMindCoordinator, key: str) -> None:
        super().__init__(coordinator, "sensor", key)
        if key == "battery_level_needed":
            self._attr_native_unit_of_measurement = PERCENTAGE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key == "powerwall_action":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(POWERWALL_ACTION_STATES)
        elif key == "analysis":
            # state: savings this month
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_suggested_display_precision = 2

    @property
    def native_unit_of_measurement(self):
        if self._key == "analysis":
            return self.hass.config.currency if self.hass else None
        return super().native_unit_of_measurement

    @property
    def native_value(self):
        value = self.coordinator.data.get(self._key)
        if self._key == "powerwall_action" and value not in POWERWALL_ACTION_STATES:
            return None
        if isinstance(value, str) and len(value) > MAX_STATE_LENGTH:
            return value[: MAX_STATE_LENGTH - 1] + "…"
        return value

    @property
    def extra_state_attributes(self):
        return self.coordinator.attributes.get(self._key)


class BattMindSolarKwhSensor(BattMindUpdatingEntity, RestoreSensor):
    """kWh charged into the home battery from solar (accumulated, like the old input_number)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: BattMindCoordinator) -> None:
        super().__init__(coordinator, "sensor", "kwh_charged_by_solar")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        value = self.coordinator.restore_settings.get("sensor.kwh_charged_by_solar")
        if value is None and (last := await self.async_get_last_sensor_data()) is not None:
            value = last.native_value
        if value is None and (legacy := self.legacy_state()) is not None:
            value = legacy.state
        try:
            self.coordinator.solar_kwh_total = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            self.coordinator.solar_kwh_total = 0.0

    @property
    def native_value(self):
        return self.coordinator.solar_kwh_total


class BattMindWhatIfSensor(BattMindUpdatingEntity, SensorEntity):
    """The last successful what-if simulation: the profit difference of the scenario, the result in the attributes."""

    # the result is large and not history: keep it out of the recorder database
    _unrecorded_attributes = frozenset({MATCH_ALL})
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: BattMindCoordinator) -> None:
        super().__init__(coordinator, "sensor", "what_if")

    @property
    def native_unit_of_measurement(self):
        return self.hass.config.currency if self.hass else None

    @property
    def native_value(self):
        view = self.coordinator.what_if_view
        return view["summary"]["profit"]["difference"] if view else None

    @property
    def extra_state_attributes(self):
        record = self.coordinator.what_if_record
        if not record:
            return None
        # result: the simulation as calculated, view: the values derived from it for the dashboard
        return {
            "completed_at": record.get("completed_at"),
            "inputs": record.get("inputs"),
            "result": record.get("simulation"),
            "view": self.coordinator.what_if_view,
        }


class BattMindWhatIfStatusSensor(BattMindUpdatingEntity, SensorEntity):
    """Whether a what-if simulation is running, with its progress, or why the last attempt failed or stopped."""

    _unrecorded_attributes = frozenset({MATCH_ALL})
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(WHAT_IF_STATUSES)

    def __init__(self, coordinator: BattMindCoordinator) -> None:
        super().__init__(coordinator, "sensor", "what_if_status")

    @property
    def native_value(self):
        return self.coordinator.what_if_state["status"]

    @property
    def extra_state_attributes(self):
        state = self.coordinator.what_if_state
        return {"reason": state["reason"], "error": state["error"], "progress": state["progress"]}


class BattMindWhatIfProgressSensor(BattMindUpdatingEntity, SensorEntity):
    """How far the running what-if simulation is, in percent.

    0 when none is running. While one runs it is at least 1, also while the history loads and no percent is known yet:
    the timer bar card of the dashboard takes the value as the time left and treats 0 as finished. The attribute
    status is the status of the simulation (the card reads it to know that the bar is active).
    """

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: BattMindCoordinator) -> None:
        super().__init__(coordinator, "sensor", "what_if_progress")

    @property
    def native_value(self) -> float:
        state = self.coordinator.what_if_state
        if state["status"] not in ("running", "stopping"):
            return 0.0
        progress = state["progress"]
        percent = progress.get("percent") if progress else None
        known = isinstance(percent, (int, float)) and not isinstance(percent, bool) and math.isfinite(percent)
        return min(max(float(percent), 1.0), 100.0) if known else 1.0

    @property
    def extra_state_attributes(self):
        return {"status": self.coordinator.what_if_state["status"]}
