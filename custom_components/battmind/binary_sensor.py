"""BattMind binary sensors: which parts the last what-if result has."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import WHAT_IF_FLAGS
from .coordinator import BattMindCoordinator
from .entity import BattMindUpdatingEntity

# the entities read the coordinator's data and change nothing
PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BattMindCoordinator = entry.runtime_data
    async_add_entities(BattMindWhatIfFlag(coordinator, key, part) for key, part in WHAT_IF_FLAGS.items())


class BattMindWhatIfFlag(BattMindUpdatingEntity, BinarySensorEntity):
    """On when the last what-if result has this part (monthly data, a capacity interval or a module comparison).

    The what-if view uses it to show a chart only when there is something to show.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BattMindCoordinator, key: str, part: str) -> None:
        super().__init__(coordinator, "binary_sensor", key)
        self._part = part

    @property
    def is_on(self) -> bool:
        view = self.coordinator.what_if_view
        return bool(view) and view.get(self._part) is not None
