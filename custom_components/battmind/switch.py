"""BattMind setting switches (replaces the input_boolean helpers)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SWITCH_SETTINGS, WHAT_IF_INPUT_SWITCHES, WHAT_IF_RUN_SWITCH, unsupported_actions, unsupported_switches
from .coordinator import BattMindCoordinator
from .entity import BattMindEntity, BattMindUpdatingEntity

# the entities read the coordinator's data and change only the integration's own settings
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BattMindCoordinator = entry.runtime_data
    # removed settings: deactivate_script (automations are switched off instead) and
    # optimizer_shadow_plan (the optimisation is always calculated)
    registry = er.async_get(hass)
    for key in ("deactivate_script", "optimizer_shadow_plan"):
        if entity_id := registry.async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_{key}"):
            registry.async_remove(entity_id)
    async_add_entities(BattMindSwitch(coordinator, key, info) for key, info in SWITCH_SETTINGS.items())
    async_add_entities(BattMindWhatIfSwitch(coordinator, key, info) for key, info in WHAT_IF_INPUT_SWITCHES.items())
    async_add_entities([BattMindWhatIfRunSwitch(coordinator, "switch", WHAT_IF_RUN_SWITCH)])


class BattMindSwitch(BattMindEntity, SwitchEntity, RestoreEntity):
    """A planner setting stored in the coordinator."""

    def __init__(self, coordinator: BattMindCoordinator, key: str, info: dict[str, Any]) -> None:
        super().__init__(coordinator, "switch", key)
        self._default = info["default"]
        self._attr_entity_category = EntityCategory.CONFIG
        # the rule needs a battery action the inverter can't run (configuration): the planner
        # treats it as off, the switch keeps its value for when the action is supported again
        if action := unsupported_switches(unsupported_actions(coordinator.config)).get(key):
            self._attr_extra_state_attributes = {"unsupported_action": action}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        value = self._default
        if (last := await self.async_get_restored_state()) is not None and last.state in ("on", "off"):
            value = last.state == STATE_ON
        elif (legacy := self.legacy_state()) is not None and legacy.state in ("on", "off"):
            value = legacy.state == STATE_ON
        self.coordinator.async_set_setting(self._key, value, initial=True)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.settings.get(self._key, self._default))

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.async_set_setting(self._key, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.async_set_setting(self._key, False)
        self.async_write_ha_state()


class BattMindWhatIfSwitch(BattMindEntity, SwitchEntity, RestoreEntity):
    """Whether the what-if form uses a capacity interval or the module comparison. Only feeds a simulation."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BattMindCoordinator, key: str, info: dict[str, Any]) -> None:
        super().__init__(coordinator, "switch", key)
        self._default = bool(info["default"])

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        value = self._default
        if (last := await self.async_get_last_state()) is not None and last.state in ("on", "off"):
            value = last.state == STATE_ON
        self.coordinator.async_set_what_if_input(self._key, value)

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.what_if_inputs.get(self._key, self._default))

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.async_set_what_if_input(self._key, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.async_set_what_if_input(self._key, False)
        self.async_write_ha_state()


class BattMindWhatIfRunSwitch(BattMindUpdatingEntity, SwitchEntity):
    """On while a what-if simulation runs. Turn it on to run one with the what-if inputs, off to stop it.

    The live plan and the settings are not touched. A stopped or failed run keeps the previous result; the status and
    the reason are on the what_if_status sensor. Only one simulation runs at a time: it is off while a stop is
    pending (the status is stopping), and turning it on then does nothing.
    """

    def __init__(self, coordinator: BattMindCoordinator, platform: str, key: str) -> None:
        super().__init__(coordinator, platform, key)

    @callback
    def _handle_update(self, key: Any) -> None:
        if key == "what_if_status":
            self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self.coordinator.what_if_state["status"] == "running"

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.async_run_what_if()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.async_stop_what_if()
