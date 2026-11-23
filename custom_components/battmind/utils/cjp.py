"""The chargers of Cable Juice Planner, whose consumption BattMind leaves out of the house consumption."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

CJP_DOMAIN = "cable_juice_planner"
# The option of a CJP car entry with the power sensor of its charger: charger_power_entity_id, or the old misspelled
# power_consumtion_entity_id while CJP has not migrated the entry. Never read power_consumption_entity_id: in CJP
# that option is the HOUSE consumption sensor.
CJP_CHARGER_POWER_KEYS = ("charger_power_entity_id", "power_consumtion_entity_id")
# The option of a CJP car entry that says whether the house consumption sensor includes its charger (default True). A car
# with False has its charger on its own meter: the house consumption does not include it, so there is nothing to subtract.
CJP_CHARGER_IN_HOUSE_METER_KEY = "charger_in_house_meter"


def cjp_charger_power_entities(hass: HomeAssistant, *, include_own_meter: bool = False) -> list[str]:
    """The power sensors of the chargers of every Cable Juice Planner car, sorted and without repeats.

    Read from the options of the config entries, not from a running CJP, so the answer is the same
    whatever order the integrations start in. The shared site entry (no charger) and disabled entries are left out, and
    so are cars whose charger is on its own meter (CJP option charger_in_house_meter False) unless include_own_meter:
    the house consumption does not include them, so there is nothing to subtract, but they are still chargers.
    """
    found: set[str] = set()
    for entry in hass.config_entries.async_entries(CJP_DOMAIN):
        if entry.data.get("site") or entry.disabled_by:
            continue
        if not include_own_meter and entry.options.get(CJP_CHARGER_IN_HOUSE_METER_KEY, True) is False:
            continue
        for key in CJP_CHARGER_POWER_KEYS:
            entity_id = entry.options.get(key)
            if isinstance(entity_id, str) and entity_id.strip():
                found.add(entity_id.strip())
                break
    return sorted(found)
