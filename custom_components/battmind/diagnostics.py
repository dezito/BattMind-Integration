"""Diagnostics (Settings → Devices & services → BattMind → ⋮ → Download diagnostics).

The same anonymised data as the debug export (management/debug_export.py), without the
databases: they can be large, and the debug export has them.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .management.debug_export import async_debug_data, jsonable


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    data, redactor = await async_debug_data(hass, entry.runtime_data, dt_util.now(), databases=False)
    return await hass.async_add_executor_job(jsonable, data, redactor)
