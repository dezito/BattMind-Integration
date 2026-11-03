"""Diagnostics download (diagnostics.py): the anonymised debug data without the databases."""
from __future__ import annotations

import json

from homeassistant.core import HomeAssistant

from custom_components.battmind.const import DOMAIN
from custom_components.battmind.diagnostics import async_get_config_entry_diagnostics

from .conftest import CONSUMPTION, PREFIX, PRICE


async def test_diagnostics_are_anonymised(hass: HomeAssistant, loaded_entry) -> None:
    hass.config.location_name = "Sommerhuset Bakkebo"
    hass.states.async_set("person.thomas", "home", {"friendly_name": "Thomas Sloth"})

    data = await async_get_config_entry_diagnostics(hass, loaded_entry)
    text = json.dumps(data)

    assert data["integration"] == DOMAIN and data["prefix"] == PREFIX
    assert data["jobs"] and data["engine"]["plan"] is not None
    assert data["databases"] == "<only in the debug export>"
    assert data["config"]["home"]["entity_ids"]["power_consumption_entity_id"] == "sensor.power_consumption"
    for secret in (CONSUMPTION, PRICE, "Thomas", "Sloth", "Bakkebo"):
        assert secret not in text, secret
