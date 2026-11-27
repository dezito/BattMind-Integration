"""The history of the value checks read from a real recorder (management/value_checks.py async_recorded_numbers)."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.battmind.management.value_checks import async_recorded_numbers

ENTITY = "sensor.value_history_test"
OTHER = "sensor.value_history_other"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Replaces the conftest fixture: it sets up hass before the recorder, which the recorder fixture refuses. Nothing here
    needs the custom integration."""
    yield


async def test_the_numbers_come_from_the_real_recorder(recorder_mock, hass: HomeAssistant) -> None:
    """The states are read as numbers, a state that is not a number is left out, and an entity without states has none."""
    for state in ("0.5", "1.2", "unavailable", "3.4"):
        hass.states.async_set(ENTITY, state)
    await async_wait_recording_done(hass)
    assert await async_recorded_numbers(hass, [ENTITY]) == {ENTITY: [0.5, 1.2, 3.4]}
    assert await async_recorded_numbers(hass, [ENTITY, OTHER]) == {ENTITY: [0.5, 1.2, 3.4], OTHER: []}
