"""Persistence contract tests for the BattMind what-if service: the last result is saved, published and kept."""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from custom_components.battmind.const import DOMAIN
from custom_components.battmind.engine import JobResult


async def _run_what_if(hass, entry, **inputs):
    """Call the public service while suppressing its presentation-only notification."""
    with (
        patch("custom_components.battmind.persistent_notification.async_create"),
        patch("custom_components.battmind._what_if_notification", return_value="finished"),
    ):
        return await hass.services.async_call(
            DOMAIN,
            "what_if",
            {"config_entry_id": entry.entry_id, **inputs},
            blocking=True,
            return_response=True,
        )


async def _last_record(entry):
    """The record saved in .storage."""
    return await entry.runtime_data.what_if_store.load()


def _published(hass) -> dict | None:
    """The result as the what-if sensor shows it (None until there is one)."""
    state = hass.states.get("sensor.bmtest_what_if")
    return state.attributes.get("result") if state else None


def _mock_simulation(coordinator, simulation: dict) -> None:
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation))


async def test_last_result_is_null_until_a_what_if_completes(hass, loaded_entry) -> None:
    assert await _last_record(loaded_entry) is None
    assert _published(hass) is None


async def test_service_persists_validated_inputs_and_the_sensor_shows_the_same_result(
    hass, loaded_entry
) -> None:
    simulation = {"scenario": {"bill": 12.5}, "run_id": "first"}
    _mock_simulation(loaded_entry.runtime_data, simulation)

    response = await _run_what_if(
        hass,
        loaded_entry,
        history_days="42",
        battery_size="22",
        optimizer_control=False,
        range_start="0",
        range_end="0.6",
        range_step="0.3",
    )

    record = response["saved_results"]["bmtest"]
    assert set(record) == {"completed_at", "inputs", "simulation"}
    completed_at = dt_util.parse_datetime(record["completed_at"])
    assert isinstance(completed_at, datetime)
    assert completed_at.tzinfo is not None
    assert record["inputs"] == {
        "history_days": 42,
        "battery_size": 22.0,
        "optimizer_control": False,
        "range_start": 0.0,
        "range_end": 0.6,
        "range_step": 0.3,
    }
    assert record["simulation"] == simulation

    assert await _last_record(loaded_entry) == record
    assert _published(hass) == simulation
    assert hass.states.get("sensor.bmtest_what_if").attributes["inputs"] == record["inputs"]

    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await _last_record(loaded_entry) == record
    assert _published(hass) == simulation


async def test_simulation_failure_preserves_the_previous_result(hass, loaded_entry) -> None:
    coordinator = loaded_entry.runtime_data
    first_simulation = {"run_id": "successful"}
    _mock_simulation(coordinator, first_simulation)
    first_response = await _run_what_if(hass, loaded_entry, battery_size=20)
    previous_record = first_response["saved_results"]["bmtest"]

    coordinator.async_what_if = AsyncMock(side_effect=HomeAssistantError("simulation failed"))
    with pytest.raises(ServiceValidationError):
        await _run_what_if(hass, loaded_entry, battery_size=21)

    assert await _last_record(loaded_entry) == previous_record
    assert _published(hass) == first_simulation


async def test_store_failure_preserves_previous_result_and_reports_action_failed(
    hass, loaded_entry
) -> None:
    coordinator = loaded_entry.runtime_data
    _mock_simulation(coordinator, {"run_id": "successful"})
    first_response = await _run_what_if(hass, loaded_entry, battery_size=20)
    previous_record = first_response["saved_results"]["bmtest"]

    _mock_simulation(coordinator, {"run_id": "not-saved"})
    with patch.object(Store, "async_save", new=AsyncMock(side_effect=OSError("disk full"))):
        with pytest.raises(ServiceValidationError) as error:
            await _run_what_if(hass, loaded_entry, battery_size=21)

    assert error.value.translation_key == "action_failed"
    assert await _last_record(loaded_entry) == previous_record
    assert _published(hass) == {"run_id": "successful"}


async def test_cancelling_service_caller_does_not_cancel_result_persistence(
    hass, loaded_entry
) -> None:
    coordinator = loaded_entry.runtime_data
    _mock_simulation(coordinator, {"run_id": "shielded"})
    save_started = asyncio.Event()
    release_save = asyncio.Event()
    save_finished = asyncio.Event()
    notification_finished = asyncio.Event()
    original_save = Store.async_save

    async def delayed_save(store, data) -> None:
        save_started.set()
        await release_save.wait()
        await original_save(store, data)
        save_finished.set()

    def finished_notification(*_args, **_kwargs) -> str:
        notification_finished.set()
        return "finished"

    with (
        patch.object(Store, "async_save", new=delayed_save),
        patch("custom_components.battmind._what_if_notification", side_effect=finished_notification),
    ):
        caller = asyncio.create_task(_run_what_if(hass, loaded_entry, battery_size=20))
        await asyncio.wait_for(save_started.wait(), timeout=5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        release_save.set()
        await asyncio.wait_for(save_finished.wait(), timeout=5)
        await asyncio.wait_for(notification_finished.wait(), timeout=5)

    assert (await _last_record(loaded_entry))["simulation"] == {"run_id": "shielded"}


async def test_unload_waits_for_pending_persistence(
    hass, loaded_entry
) -> None:
    coordinator = loaded_entry.runtime_data
    _mock_simulation(coordinator, {"run_id": "unloading"})
    save_started = asyncio.Event()
    release_save = asyncio.Event()
    save_finished = asyncio.Event()

    async def blocked_save(_store, _data) -> None:
        save_started.set()
        await release_save.wait()
        save_finished.set()

    with patch.object(Store, "async_save", new=blocked_save):
        caller = asyncio.create_task(_run_what_if(hass, loaded_entry, battery_size=20))
        await asyncio.wait_for(save_started.wait(), timeout=5)

        unloading = asyncio.create_task(hass.config_entries.async_unload(loaded_entry.entry_id))
        await asyncio.sleep(0)
        assert not unloading.done()
        release_save.set()
        assert await unloading
        assert save_finished.is_set()
        await asyncio.wait_for(asyncio.gather(caller, return_exceptions=True), timeout=5)
        assert caller.done()

    await hass.async_add_executor_job(coordinator._executor.shutdown, True)


async def test_same_entry_persistence_writes_are_serialized(
    hass, loaded_entry
) -> None:
    coordinator = loaded_entry.runtime_data
    second_simulation_started = asyncio.Event()
    simulation_count = 0

    async def simulate(overrides, _history_days, _progress):
        nonlocal simulation_count
        simulation_count += 1
        if simulation_count == 2:
            second_simulation_started.set()
        return JobResult(value={"battery_size": overrides["battery_size"]})

    coordinator.async_what_if = simulate
    first_save_started = asyncio.Event()
    release_first_save = asyncio.Event()
    save_count = 0
    active_saves = 0
    max_active_saves = 0
    original_save = Store.async_save

    async def ordered_save(store, data) -> None:
        nonlocal save_count, active_saves, max_active_saves
        save_count += 1
        active_saves += 1
        max_active_saves = max(max_active_saves, active_saves)
        try:
            if save_count == 1:
                first_save_started.set()
                await release_first_save.wait()
            await original_save(store, data)
        finally:
            active_saves -= 1

    with (
        patch.object(Store, "async_save", new=ordered_save),
        patch("custom_components.battmind.persistent_notification.async_create"),
        patch("custom_components.battmind._what_if_notification", return_value="finished"),
    ):
        first = asyncio.create_task(_run_what_if(hass, loaded_entry, battery_size=20))
        await asyncio.wait_for(first_save_started.wait(), timeout=5)
        second = asyncio.create_task(_run_what_if(hass, loaded_entry, battery_size=21))
        await asyncio.wait_for(second_simulation_started.wait(), timeout=5)
        assert save_count == 1

        release_first_save.set()
        first_response, second_response = await asyncio.gather(first, second)

    assert max_active_saves == 1
    assert second_response["saved_results"]["bmtest"]["simulation"] == {"battery_size": 21.0}
