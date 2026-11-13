"""BattMind number settings (replaces the input_number helpers)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import NUMBER_SETTINGS, WHAT_IF_INPUT_NUMBERS, WHAT_IF_OVERRIDE_NUMBERS
from .coordinator import BattMindCoordinator
from .entity import BattMindEntity, BattMindUpdatingEntity
from .utils.helpers import float_or_none

# the entities read the coordinator's data and change only the integration's own settings
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BattMindCoordinator = entry.runtime_data
    async_add_entities(BattMindNumber(coordinator, key, info) for key, info in NUMBER_SETTINGS.items())
    # the what-if inputs and the scenario settings, kept apart from the planner settings
    async_add_entities(BattMindWhatIfNumber(coordinator, key, info, False) for key, info in WHAT_IF_INPUT_NUMBERS.items())
    async_add_entities(
        BattMindWhatIfNumber(coordinator, key, NUMBER_SETTINGS[setting], True)
        for key, setting in WHAT_IF_OVERRIDE_NUMBERS.items()
    )


class BattMindNumber(BattMindEntity, NumberEntity, RestoreEntity):
    """A numeric planner setting stored in the coordinator."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BattMindCoordinator, key: str, info: dict[str, Any]) -> None:
        super().__init__(coordinator, "number", key)
        self._attr_native_min_value = info["min"]
        self._attr_native_max_value = info["max"]
        self._attr_native_step = info["step"]
        self._attr_mode = NumberMode.BOX if info["mode"] == "box" else NumberMode.SLIDER
        # {valuta} / {valuta_kwh}: the currency of Home Assistant
        self._attr_native_unit_of_measurement = (
            info["unit"].format(valuta=coordinator.t("ui.common.valuta"), valuta_kwh=coordinator.t("ui.common.valuta_kwh"))
            if info["unit"]
            else None
        )
        initial = coordinator.entry.data.get("initial_settings", {})
        # a value from an import or an older version is kept inside the limits of the entity
        self._default = self._valid(initial.get(key))
        if self._default is None:
            self._default = float(info["default"])

    def _valid(self, value) -> float | None:
        value = float_or_none(value)
        if value is None:
            return None
        return min(max(value, self.native_min_value), self.native_max_value)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        value = None
        if (last := await self.async_get_restored_state()) is not None:
            value = self._valid(last.state)
        if value is None and (legacy := self.legacy_state()) is not None:
            value = self._valid(legacy.state)
        if value is None:
            value = self._default
        self.coordinator.async_set_setting(self._key, value, initial=True)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.settings.get(self._key, self._default)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.async_set_setting(self._key, value)
        self.async_write_ha_state()


class BattMindWhatIfNumber(BattMindUpdatingEntity, NumberEntity, RestoreEntity):
    """A what-if input. It only feeds a simulation: the planner and its settings are never changed or replanned.

    An override (the scenario's value of a planner setting) has no value, and so is not sent to the simulation, until
    one is entered: the scenario then uses the current setting. The button reset_what_if_settings clears it again.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BattMindCoordinator, key: str, info: dict[str, Any], override: bool) -> None:
        super().__init__(coordinator, "number", key)
        self._attr_native_min_value = info["min"]
        self._attr_native_max_value = info["max"]
        self._attr_native_step = info["step"]
        self._attr_mode = NumberMode.BOX
        self._attr_native_unit_of_measurement = (
            info["unit"].format(valuta=coordinator.t("ui.common.valuta"), valuta_kwh=coordinator.t("ui.common.valuta_kwh"))
            if info["unit"]
            else None
        )
        # an input starts with its default, an override without a value
        self._default = None if override else float(info["default"])

    def _valid(self, value) -> float | None:
        value = float_or_none(value)
        if value is None or not self.native_min_value <= value <= self.native_max_value:
            return None
        return value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        value = None
        if (last := await self.async_get_last_state()) is not None:
            value = self._valid(last.state)
        if value is None:
            value = self._default
        self.coordinator.async_set_what_if_input(self._key, value)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.what_if_inputs.get(self._key)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.async_set_what_if_input(self._key, value)
        self.async_write_ha_state()
