"""The what-if view: its input entities, running and stopping a simulation, and the published result."""
from __future__ import annotations

import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.template import Template

from custom_components.battmind.const import DOMAIN, WHAT_IF_FLAGS, WHAT_IF_OVERRIDE_NUMBERS, WHAT_IF_RULE_SELECTS
from custom_components.battmind.engine import JobResult
from custom_components.battmind.engine.planner.replay import WhatIfCancelled, replay_history
from custom_components.battmind.management.backup import _collect_settings
from custom_components.battmind.management.dashboard_build import render_dashboard
from custom_components.battmind.management.what_if_storage import DashboardJob
from .conftest import PREFIX
from .test_planner_rules import DAY, _what_if_data, planned, realistic_snapshot
from .test_what_if_view import module_comparison, simulation


async def press(hass, key: str) -> None:
    if key in ("run_what_if", "stop_what_if"):  # one switch: on runs the simulation, off stops it
        service = "turn_on" if key == "run_what_if" else "turn_off"
        await hass.services.async_call("switch", service, {"entity_id": f"switch.{PREFIX}_what_if_run"}, blocking=True)
        return
    await hass.services.async_call("button", "press", {"entity_id": f"button.{PREFIX}_{key}"}, blocking=True)


async def set_number(hass, key: str, value: float) -> None:
    await hass.services.async_call("number", "set_value", {"entity_id": f"number.{PREFIX}_{key}", "value": value}, blocking=True)


async def select(hass, key: str, option: str) -> None:
    await hass.services.async_call("select", "select_option", {"entity_id": f"select.{PREFIX}_{key}", "option": option}, blocking=True)


async def switch(hass, key: str, on: bool) -> None:
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": f"switch.{PREFIX}_{key}"}, blocking=True)


def status(hass):
    return hass.states.get(f"sensor.{PREFIX}_what_if_status")


def result(hass):
    return hass.states.get(f"sensor.{PREFIX}_what_if")


async def run(hass) -> None:
    await press(hass, "run_what_if")
    await hass.async_block_till_done(wait_background_tasks=True)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


async def test_inputs_start_with_the_form_defaults_and_scenario_values_are_current(hass, loaded_entry):
    states = hass.states
    assert states.get(f"number.{PREFIX}_what_if_history_days").state == "365.0"
    assert states.get(f"number.{PREFIX}_what_if_battery_size").state == "20.0"
    assert states.get(f"number.{PREFIX}_what_if_range_step").state == "5.0"
    assert states.get(f"number.{PREFIX}_what_if_module_count").state == "5.0"
    assert states.get(f"switch.{PREFIX}_what_if_range").state == "off"
    assert states.get(f"switch.{PREFIX}_what_if_modules").state == "off"
    for key in WHAT_IF_RULE_SELECTS:
        assert states.get(f"select.{PREFIX}_{key}").state == "current", key
    # a scenario setting has no value, and so no override, until one is entered
    for key in WHAT_IF_OVERRIDE_NUMBERS:
        assert states.get(f"number.{PREFIX}_{key}").state == "unknown", key
    assert states.get(f"text.{PREFIX}_what_if_exclude_sell_hours").state == "unknown"
    assert status(hass).state == "idle"
    assert result(hass).state == "unknown"
    assert all(states.get(f"binary_sensor.{PREFIX}_{key}").state == "off" for key in WHAT_IF_FLAGS)


async def test_inputs_are_not_planner_settings_and_never_replan(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    before = dict(coordinator.settings)
    with patch.object(coordinator, "async_request_charge") as request:
        await set_number(hass, "what_if_battery_size", 33)
        await set_number(hass, "what_if_usage_reserve", 40)
        await select(hass, "what_if_most_profit", "off")
        await switch(hass, "what_if_range", True)
        await hass.services.async_call(
            "text", "set_value", {"entity_id": f"text.{PREFIX}_what_if_exclude_sell_hours", "value": "1,2"}, blocking=True
        )
        assert coordinator._settings_debounce is None
    request.assert_not_called()
    assert coordinator.settings == before
    assert coordinator.what_if_inputs["what_if_battery_size"] == 33.0
    assert coordinator.what_if_inputs["what_if_exclude_sell_hours"] == "1,2"
    # nor are they part of a backup
    assert not [key for key in _collect_settings(hass, loaded_entry.entry_id) if "what_if" in key]


async def test_inputs_and_overrides_survive_a_reload_and_reset_returns_to_current(hass, loaded_entry):
    await set_number(hass, "what_if_battery_size", 33)
    await set_number(hass, "what_if_min_sell_kwh", 0.5)
    await select(hass, "what_if_most_profit", "on")
    await switch(hass, "what_if_modules", True)

    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(f"number.{PREFIX}_what_if_battery_size").state == "33.0"
    assert hass.states.get(f"number.{PREFIX}_what_if_min_sell_kwh").state == "0.5"
    assert hass.states.get(f"number.{PREFIX}_what_if_usage_reserve").state == "unknown"
    assert hass.states.get(f"select.{PREFIX}_what_if_most_profit").state == "on"
    assert hass.states.get(f"switch.{PREFIX}_what_if_modules").state == "on"

    await press(hass, "reset_what_if_settings")
    await hass.async_block_till_done()
    assert hass.states.get(f"number.{PREFIX}_what_if_min_sell_kwh").state == "unknown"
    assert hass.states.get(f"select.{PREFIX}_what_if_most_profit").state == "current"
    # the form's own inputs are not scenario overrides: the reset keeps them
    assert hass.states.get(f"number.{PREFIX}_what_if_battery_size").state == "33.0"
    assert hass.states.get(f"switch.{PREFIX}_what_if_modules").state == "on"


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


async def test_run_publishes_the_result_without_notifications(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    sim = simulation()
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=sim))
    with patch("custom_components.battmind.persistent_notification.async_create") as notification:
        await run(hass)
    notification.assert_not_called()

    assert coordinator.async_what_if.call_args.args[:2] == ({"battery_size": 20.0}, 365)
    assert set(coordinator.async_what_if.call_args.kwargs) == {"cancel_check"}
    assert status(hass).state == "idle"
    sensor = result(hass)
    assert float(sensor.state) == 50.0 and sensor.attributes["unit_of_measurement"]
    assert sensor.attributes["result"] == sim
    assert sensor.attributes["inputs"] == {"history_days": 365, "battery_size": 20.0}
    assert sensor.attributes["view"]["summary"]["profit"]["difference"] == 50.0
    assert hass.states.get(f"binary_sensor.{PREFIX}_what_if_has_months").state == "on"
    assert hass.states.get(f"binary_sensor.{PREFIX}_what_if_has_range").state == "off"
    assert hass.states.get(f"binary_sensor.{PREFIX}_what_if_has_modules").state == "off"
    record = await coordinator.what_if_store.load()
    assert record["simulation"] == sim and record["inputs"] == sensor.attributes["inputs"]


async def test_range_modules_and_scenario_overrides_reach_the_simulation(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    sim = simulation(module_comparison=module_comparison(), range=[{"battery_size": 0.0, "profit": 0.0, "bill": 1.0}])
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=sim))
    await set_number(hass, "what_if_history_days", 30)
    await set_number(hass, "what_if_battery_size", 24)
    await switch(hass, "what_if_range", True)
    await set_number(hass, "what_if_range_start", 0)
    await set_number(hass, "what_if_range_end", 10)
    await set_number(hass, "what_if_range_step", 5)
    await switch(hass, "what_if_modules", True)
    await set_number(hass, "what_if_module_capacity", 2.5)
    await set_number(hass, "what_if_module_price", 0)
    await set_number(hass, "what_if_module_count", 3)
    await select(hass, "what_if_most_profit", "off")
    await select(hass, "what_if_solar_charging", "on")
    await select(hass, "what_if_negative_price_planner", "current")
    await set_number(hass, "what_if_usage_reserve", 0)  # 0 is a real value, not "current"
    await hass.services.async_call(
        "text", "set_value", {"entity_id": f"text.{PREFIX}_what_if_exclude_sell_hours", "value": "17,18"}, blocking=True
    )

    await run(hass)

    call = coordinator.async_what_if.call_args
    assert call.args[:2] == (
        {
            "battery_size": 24.0, "most_profit": False, "solar_charging": True, "usage_reserve": 0.0,
            "exclude_sell_hours": "17,18",
        },
        30,
    )
    assert call.kwargs["capacity_range"] == [0.0, 5.0, 10.0]
    assert call.kwargs["module_comparison"] == {"module_capacity": 2.5, "module_price": 0.0, "module_count": 3}
    record = await coordinator.what_if_store.load()
    assert record["inputs"]["range_end"] == 10.0 and record["inputs"]["module_count"] == 3.0
    assert hass.states.get(f"binary_sensor.{PREFIX}_what_if_has_range").state == "on"
    assert hass.states.get(f"binary_sensor.{PREFIX}_what_if_has_modules").state == "on"


async def test_a_rule_the_battery_cannot_run_is_not_a_scenario_value(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await select(hass, "what_if_most_profit", "on")
    await select(hass, "what_if_grid_charge_to_sell", "on")
    with patch(
        "custom_components.battmind.coordinator.what_if.unsupported_switches", return_value={"grid_charge_to_sell": "grid_charging"}
    ):
        await run(hass)
    assert coordinator.async_what_if.call_args.args[0] == {"battery_size": 20.0, "most_profit": True}


@pytest.mark.parametrize(
    ("inputs", "reason"),
    [
        ({"what_if_range_end": 100, "what_if_range_step": 1}, "range_invalid"),
        ({"what_if_range_start": 20, "what_if_range_end": 10}, "range_invalid"),
    ],
)
async def test_invalid_inputs_fail_with_a_reason_and_keep_the_previous_result(hass, loaded_entry, inputs, reason):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await run(hass)
    previous = await coordinator.what_if_store.load()

    await switch(hass, "what_if_range", True)
    for key, value in inputs.items():
        await set_number(hass, key, value)
    coordinator.async_what_if.reset_mock()
    await press(hass, "run_what_if")
    await hass.async_block_till_done(wait_background_tasks=True)

    coordinator.async_what_if.assert_not_called()
    assert status(hass).state == "failed" and status(hass).attributes["reason"] == reason
    assert await coordinator.what_if_store.load() == previous
    assert float(result(hass).state) == 50.0

    # a valid request clears the failure
    await switch(hass, "what_if_range", False)
    await run(hass)
    assert status(hass).state == "idle" and status(hass).attributes["reason"] is None


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (HomeAssistantError(translation_domain=DOMAIN, translation_key="what_if_no_recorder"), "no_recorder"),
        (HomeAssistantError(translation_domain=DOMAIN, translation_key="what_if_no_buy_price"), "no_buy_price"),
        (HomeAssistantError(translation_domain=DOMAIN, translation_key="what_if_history_failed"), "history_failed"),
        (RuntimeError("boom"), "internal_error"),
    ],
)
async def test_a_failed_simulation_reports_its_reason(hass, loaded_entry, failure, reason):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await run(hass)
    coordinator.async_what_if = AsyncMock(side_effect=failure)
    await run(hass)
    assert status(hass).state == "failed"
    assert status(hass).attributes["reason"] == reason
    assert status(hass).attributes["progress"] is None
    # the previous result is still shown
    assert float(result(hass).state) == 50.0
    # the same for a result that is an error
    coordinator.async_what_if = AsyncMock(return_value=JobResult(error=failure))
    await run(hass)
    assert status(hass).attributes["reason"] == reason


async def test_a_result_that_cannot_be_saved_fails_and_keeps_the_previous_one(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await run(hass)
    previous = await coordinator.what_if_store.load()
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation(difference={"profit": 99.0, "bill": 0.0})))
    with patch.object(Store, "async_save", new=AsyncMock(side_effect=OSError("disk full"))):
        await run(hass)
    assert status(hass).state == "failed" and status(hass).attributes["reason"] == "save_failed"
    assert await coordinator.what_if_store.load() == previous
    assert float(result(hass).state) == 50.0


async def test_a_result_that_cannot_be_summarised_is_still_published(hass, loaded_entry):
    loaded_entry.runtime_data.async_what_if = AsyncMock(return_value=JobResult(value={"ok": True}))
    await run(hass)
    assert status(hass).state == "idle"
    assert result(hass).attributes["result"] == {"ok": True}
    assert result(hass).attributes["view"] is None
    assert all(hass.states.get(f"binary_sensor.{PREFIX}_{key}").state == "off" for key in WHAT_IF_FLAGS)


async def test_the_last_result_is_shown_after_a_restart(hass, loaded_entry):
    sim = simulation()
    loaded_entry.runtime_data.async_what_if = AsyncMock(return_value=JobResult(value=sim))
    await run(hass)
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    sensor = result(hass)
    assert float(sensor.state) == 50.0 and sensor.attributes["result"] == sim
    assert hass.states.get(f"binary_sensor.{PREFIX}_what_if_has_months").state == "on"
    assert status(hass).state == "idle"


async def test_progress_shows_the_loading_phase_the_simulation_and_the_remaining_time(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    stages = [asyncio.Event() for _ in range(4)]
    release = asyncio.Event()

    async def simulate(_overrides, _days, progress, *, cancel_check):
        progress({"phase": "states", "seconds": 5})
        stages[0].set()
        await stages[1].wait()
        progress({"percent": 42, "step": 2, "steps": 3, "hours": 84, "total_hours": 200, "seconds": 25})
        stages[2].set()
        await stages[3].wait()
        progress({"percent": 60, "step": 2, "steps": 3, "hours": 120, "total_hours": 200, "seconds": 70})
        await release.wait()
        return JobResult(value=simulation())

    coordinator.async_what_if = simulate
    await set_number(hass, "what_if_history_days", 90)
    await press(hass, "run_what_if")
    await asyncio.wait_for(stages[0].wait(), 5)
    await hass.async_block_till_done()
    assert status(hass).state == "running"
    progress = status(hass).attributes["progress"]
    assert progress["phase"] == "states" and "90" in progress["message"] and "5" in progress["message"]

    stages[1].set()
    await asyncio.wait_for(stages[2].wait(), 5)
    await hass.async_block_till_done()
    progress = status(hass).attributes["progress"]
    assert progress["percent"] == 42 and "42" in progress["message"] and "eta_seconds" not in progress
    # a second run is ignored while one is running
    await press(hass, "run_what_if")

    stages[3].set()
    await hass.async_block_till_done()
    progress = status(hass).attributes["progress"]
    # (600 - 320 hours left) at the pace of the 36 hours done in 45 seconds
    assert progress["eta_seconds"] == 350 and "6 min" in progress["message"]
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert status(hass).state == "idle" and status(hass).attributes["progress"] is None


async def test_the_progress_sensor_is_zero_when_idle_and_at_least_one_while_it_runs(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    progress = lambda: hass.states.get(f"sensor.{PREFIX}_what_if_progress")  # noqa: E731

    async def publish(**changes):
        coordinator._publish_what_if_status(**changes)
        await hass.async_block_till_done()

    assert progress().state == "0.0" and progress().attributes["status"] == "idle"
    # loading the history: no percent is known yet, and 0 would be "finished" for the timer bar card
    await publish(status="running", progress={"phase": "history", "message": "Loading", "seconds": 3})
    assert progress().state == "1.0" and progress().attributes["status"] == "running"
    await publish(progress={"percent": 0, "message": "start"})
    assert progress().state == "1.0"
    await publish(progress={"percent": 37.5, "message": "replay"})
    assert progress().state == "37.5"
    # a value that is not a finite number is not a percent; the percent never leaves 1 to 100
    await publish(progress={"percent": float("nan")})
    assert progress().state == "1.0"
    await publish(progress={"percent": 250})
    assert progress().state == "100.0"
    # stopping keeps the percent; every other status is 0
    await publish(status="stopping", progress={"percent": 60})
    assert progress().state == "60.0" and progress().attributes["status"] == "stopping"
    for status_name in ("stopped", "failed", "idle"):
        await publish(status=status_name, progress={"percent": 60})
        assert progress().state == "0.0" and progress().attributes["status"] == status_name


async def test_stop_stops_the_worker_and_keeps_the_previous_result(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await run(hass)
    previous = await coordinator.what_if_store.load()
    started, finished = Event(), Event()

    def worker(cancel_check):
        started.set()
        try:
            while True:
                cancel_check()
                Event().wait(0.01)
        finally:
            finished.set()

    async def simulate(_overrides, _days, _progress, *, cancel_check):
        return await hass.loop.run_in_executor(coordinator._executor, worker, cancel_check)

    coordinator.async_what_if = simulate
    await press(hass, "run_what_if")
    assert await hass.async_add_executor_job(started.wait, 5)
    assert status(hass).state == "running"
    job = coordinator.what_if_job
    await press(hass, "stop_what_if")
    assert status(hass).state == "stopping"
    await hass.async_block_till_done(wait_background_tasks=True)

    assert finished.is_set() and job.cancelled.is_set()
    assert status(hass).state == "stopped" and status(hass).attributes["reason"] is None
    assert coordinator.what_if_job is None
    assert await coordinator.what_if_store.load() == previous
    assert float(result(hass).state) == 50.0
    # the next run is a new job
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await run(hass)
    assert status(hass).state == "idle"


async def test_the_run_switch_is_on_while_it_runs_and_off_stops_it(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    switch = lambda: hass.states.get(f"switch.{PREFIX}_what_if_run")  # noqa: E731
    assert switch().state == "off"
    started, release = asyncio.Event(), asyncio.Event()

    async def simulate(_overrides, _days, _progress, *, cancel_check):
        started.set()
        while not release.is_set():
            cancel_check()
            await asyncio.sleep(0.01)
        return JobResult(value=simulation())

    coordinator.async_what_if = simulate
    # on runs it, and the switch stays on while it runs (turning it on again starts nothing new)
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch().entity_id}, blocking=True)
    await asyncio.wait_for(started.wait(), 5)
    assert switch().state == "on" and status(hass).state == "running"
    job = coordinator.what_if_job
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch().entity_id}, blocking=True)
    assert coordinator.what_if_job is job
    # off asks it to stop: the switch is off at once while the status is stopping, and turning it on then does nothing
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch().entity_id}, blocking=True)
    assert status(hass).state == "stopping" and switch().state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch().entity_id}, blocking=True)
    assert coordinator.what_if_job is job and switch().state == "off"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert status(hass).state == "stopped" and switch().state == "off" and coordinator.what_if_job is None

    # a finished run turns it off by itself, a failed start leaves it off
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch().entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert status(hass).state == "idle" and switch().state == "off"
    await set_number(hass, "what_if_module_price", 100)
    await hass.services.async_call("switch", "turn_on", {"entity_id": f"switch.{PREFIX}_what_if_modules"}, blocking=True)
    await set_number(hass, "what_if_module_count", 1)
    coordinator.async_what_if = AsyncMock(side_effect=HomeAssistantError(translation_domain=DOMAIN, translation_key="what_if_no_recorder"))
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch().entity_id}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert status(hass).state == "failed" and switch().state == "off"


async def test_stop_without_a_running_simulation_does_nothing(hass, loaded_entry):
    await press(hass, "stop_what_if")
    await hass.async_block_till_done()
    assert status(hass).state == "idle"


async def test_the_save_commit_is_too_late_to_stop(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    started, release = asyncio.Event(), asyncio.Event()
    original = Store.async_save

    async def save(store, data):
        started.set()
        await release.wait()
        await original(store, data)

    with patch.object(Store, "async_save", new=save):
        await press(hass, "run_what_if")
        await asyncio.wait_for(started.wait(), 5)
        await press(hass, "stop_what_if")
        assert status(hass).state == "running"
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert status(hass).state == "idle"
    assert (await coordinator.what_if_store.load())["simulation"] == simulation()
    assert float(result(hass).state) == 50.0


async def test_stop_while_waiting_for_the_store_lock_keeps_the_previous_result(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await run(hass)
    previous = await coordinator.what_if_store.load()
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation(difference={"profit": 1.0, "bill": 0.0})))
    store = coordinator.what_if_store
    await store.lock.acquire()
    try:
        await press(hass, "run_what_if")
        for _ in range(100):
            if coordinator.async_what_if.await_count:
                break
            await asyncio.sleep(0)
        assert coordinator.async_what_if.await_count
        await press(hass, "stop_what_if")
    finally:
        store.lock.release()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert status(hass).state == "stopped"
    assert await store.load() == previous
    assert float(result(hass).state) == 50.0


async def test_unload_signals_and_waits_for_the_running_simulation(hass, loaded_entry):
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def simulate(_overrides, _days, _progress, *, cancel_check):
        entered.set()
        await release.wait()
        try:
            cancel_check()
        finally:
            exited.set()
        return JobResult(value=simulation())

    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = simulate
    await press(hass, "run_what_if")
    await asyncio.wait_for(entered.wait(), 5)
    store = coordinator.what_if_store
    unloading = asyncio.create_task(hass.config_entries.async_unload(loaded_entry.entry_id))
    for _ in range(100):
        if store.closed:
            break
        await asyncio.sleep(0)
    assert store.closed
    # unloading asks the running simulation to stop
    assert coordinator.what_if_job.cancelled.is_set()
    assert not unloading.done()
    release.set()
    assert await asyncio.wait_for(unloading, 5)
    assert exited.is_set()
    assert coordinator.what_if_job is None
    assert await store.load() is None
    assert coordinator.what_if_state["status"] == "stopped"


async def test_nothing_runs_on_an_unloading_entry(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    await coordinator.what_if_store.close()
    coordinator.async_run_what_if()
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator.async_what_if.assert_not_called()


# --------------------------------------------------------------------------- #
# The action
# --------------------------------------------------------------------------- #


async def test_service_still_notifies_and_updates_the_view(hass, loaded_entry):
    loaded_entry.runtime_data.async_what_if = AsyncMock(return_value=JobResult(value=simulation()))
    with (
        patch("custom_components.battmind.persistent_notification.async_create") as notification,
        patch("custom_components.battmind._what_if_notification", return_value="finished"),
    ):
        await hass.services.async_call(
            DOMAIN, "what_if", {"config_entry_id": loaded_entry.entry_id},
            blocking=True, return_response=True,
        )
    assert notification.call_count == 2
    assert notification.call_args.args[1] == "finished"
    # the result of the action is the one the what-if view shows
    assert float(result(hass).state) == 50.0


async def test_service_accepts_optional_module_comparison(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value={"module_comparison": {"rows": []}}))
    with (
        patch("custom_components.battmind.persistent_notification.async_create"),
        patch("custom_components.battmind._what_if_notification", return_value="finished"),
    ):
        await hass.services.async_call(
            DOMAIN,
            "what_if",
            {
                "config_entry_id": loaded_entry.entry_id,
                "battery_size": 24,
                "module_capacity": 2.5,
                "module_price": 0,
                "module_count": 3,
            },
            blocking=True,
            return_response=True,
        )

    assert coordinator.async_what_if.call_args.args[:2] == ({"battery_size": 24.0}, 365)
    assert coordinator.async_what_if.call_args.kwargs["module_comparison"] == {
        "module_capacity": 2.5,
        "module_price": 0.0,
        "module_count": 3,
    }


async def test_stop_does_not_target_the_ordinary_service(hass, loaded_entry):
    entered, release = asyncio.Event(), asyncio.Event()

    async def simulate(_overrides, _days, _progress):
        entered.set()
        await release.wait()
        return JobResult(value={"service": True})

    loaded_entry.runtime_data.async_what_if = simulate
    with (
        patch("custom_components.battmind.persistent_notification.async_create"),
        patch("custom_components.battmind._what_if_notification", return_value="finished"),
    ):
        service = asyncio.create_task(hass.services.async_call(
            DOMAIN, "what_if", {"config_entry_id": loaded_entry.entry_id},
            blocking=True, return_response=True,
        ))
        await asyncio.wait_for(entered.wait(), 5)
        assert status(hass).state == "idle"
        await press(hass, "stop_what_if")
        assert not service.done()
        release.set()
        result_ = await asyncio.wait_for(service, 5)
        assert result_["simulations"]["bmtest"] == {"service": True}


async def test_unload_waits_for_service_and_preserves_its_save(hass, loaded_entry):
    entered, release = asyncio.Event(), asyncio.Event()

    async def simulate(_overrides, _days, _progress):
        entered.set()
        await release.wait()
        return JobResult(value={"service": True})

    loaded_entry.runtime_data.async_what_if = simulate
    store = loaded_entry.runtime_data.what_if_store
    with (
        patch("custom_components.battmind.persistent_notification.async_create"),
        patch("custom_components.battmind._what_if_notification", return_value="finished"),
    ):
        service = asyncio.create_task(hass.services.async_call(
            DOMAIN, "what_if", {"config_entry_id": loaded_entry.entry_id},
            blocking=True, return_response=True,
        ))
        await asyncio.wait_for(entered.wait(), 5)
        unloading = asyncio.create_task(hass.config_entries.async_unload(loaded_entry.entry_id))
        for _ in range(100):
            if store.closed:
                break
            await asyncio.sleep(0)
        assert store.closed
        assert not unloading.done()
        release.set()
        result_ = await asyncio.wait_for(service, 5)
        assert result_["simulations"]["bmtest"] == {"service": True}
        assert await asyncio.wait_for(unloading, 5)
    assert (await store.load())["simulation"] == {"service": True}


async def test_worker_lock_stays_held_until_cancelled_worker_exits(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    entered, release, other_entered = Event(), Event(), Event()
    job = DashboardJob("00000000-0000-4000-8000-000000000000")

    def run_job(_snapshot, func, *args, **kwargs):
        return func(*args, **kwargs)

    def replay(*, cancel_check):
        entered.set()
        release.wait(5)
        cancel_check()
        return JobResult()

    def other():
        other_entered.set()
        return JobResult()

    fake_engine = SimpleNamespace(run_job=run_job, what_if=replay, debug_info=other)
    with (
        patch.object(coordinator, "engine", fake_engine),
        patch.object(coordinator, "_async_snapshot", new=AsyncMock(return_value=object())),
        patch.object(coordinator, "_async_publish", new=AsyncMock()),
        patch.object(coordinator.debug, "record"),
    ):
        running = asyncio.create_task(coordinator.async_run_job("what_if", cancel_check=job.check))
        assert await hass.async_add_executor_job(entered.wait, 5)
        job.cancelled.set()
        waiting = asyncio.create_task(coordinator.async_run_job("debug_info"))
        await asyncio.sleep(0)
        assert not other_entered.is_set()
        release.set()
        with pytest.raises(WhatIfCancelled):
            await asyncio.wait_for(running, 5)
        await asyncio.wait_for(waiting, 5)
        assert other_entered.is_set()


def test_replay_checks_cancellation_before_constructing_engine():
    def stop():
        raise WhatIfCancelled()

    with pytest.raises(WhatIfCancelled):
        replay_history(object, {}, "test", {}, {}, None, [], {}, 0, cancel_check=stop)


def test_real_replay_stops_after_first_planning_step():
    settings = {"optimizer_control": False}
    engine, _ = planned(3, 40, settings, solar=True)
    data = _what_if_data(engine, days=4)
    snapshot = realistic_snapshot(datetime.datetime.combine(DAY, datetime.time(3, 30)), 40, settings)
    stop = Event()
    reports = []

    def progress(info):
        reports.append(info)
        stop.set()

    def check():
        if stop.is_set():
            raise WhatIfCancelled()

    with ThreadPoolExecutor(max_workers=1) as executor:
        result_ = executor.submit(
            engine.run_job, snapshot, engine.what_if, data, {},
            progress=progress, cancel_check=check,
        ).result(timeout=30)
    assert reports
    assert isinstance(result_.error, WhatIfCancelled)
    assert result_.value is None


# --------------------------------------------------------------------------- #
# The dashboard cards render what the sensors publish
# --------------------------------------------------------------------------- #


def _what_if_markdown(coordinator) -> list[str]:
    dashboard = yaml.safe_load(render_dashboard(coordinator.config, PREFIX, currency="DKK"))
    view = next(view for view in dashboard["views"] if view["path"] == "what-if")

    def walk(node):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    return [node["content"] for node in walk(view) if node.get("type") == "markdown" and "content" in node]


def _render(hass, content: str) -> str:
    return Template(content, hass).async_render(parse_result=False)


async def test_the_markdown_cards_render_the_published_result_and_status(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    months = ["2026-01", "2026-02", "2026-03", "2026-04"]
    sizes = [
        {"battery_size": 0.0, "profit": 0.0, "bill": 1000.0, "months": {key: 0.0 for key in months}},
        {"battery_size": 10.0, "profit": 100.0, "bill": 900.0, "months": {key: 25.0 for key in months}},
        {"battery_size": 20.0, "profit": 150.0, "bill": 850.0, "months": {key: 37.5 for key in months}},
    ]
    sim = simulation(range=sizes, module_comparison=module_comparison(), coverage={"missing_hours": 12}, sell_cost_unknown_hours=5)
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value=sim))
    cards = _what_if_markdown(coordinator)
    assert len(cards) >= 6

    # nothing has run yet
    rendered = [_render(hass, content) for content in cards]
    assert any("Start en simulering" in text for text in rendered)
    assert not any("Error" in text or "UndefinedError" in text for text in rendered)

    await set_number(hass, "what_if_history_days", 90)
    await run(hass)
    rendered = "\n".join(_render(hass, content) for content in cards)
    # the scenario against current, the monthly table, the range, the module comparison and the rules
    for expected in (
        "150.00 kr", "100.00 kr", "50.00 kr", "850.00 kr", "2026-02", "**40.00 kr**",
        "10.0 kWh", "**150.00 kr**", "10.00 kr/kWh", "2000.00 kr", "10.00 år", "12.00 år", "20.00 år",
        "Sammenligning af batteristørrelser", "Modulsammenligning", "Historik (dage): 90",
    ):
        assert expected in rendered, expected
    assert "None" not in rendered and "Undefined" not in rendered
    assert "<ha-alert" in rendered  # the warnings: missing hours and unknown selling costs
    # the profit by size, the profit per added kWh and the payback are chart cards with a tooltip, not images
    assert "<img" not in rendered and "data:image" not in rendered
    view = hass.states.get(f"sensor.{PREFIX}_what_if").attributes["view"]
    assert view["range"]["profit_chart"]["series"] and view["range"]["interval_chart"]["series"]
    assert view["module"]["payback_apex"]["series"]
    assert "█" not in rendered and "<progress" not in rendered

    # while it runs, the status card shows the progress
    release = asyncio.Event()
    started = asyncio.Event()

    async def simulate(_overrides, _days, progress, *, cancel_check):
        progress({"percent": 42, "step": 1, "steps": 1, "hours": 84, "total_hours": 200, "seconds": 5})
        started.set()
        await release.wait()
        return JobResult(value=sim)

    coordinator.async_what_if = simulate
    await press(hass, "run_what_if")
    await asyncio.wait_for(started.wait(), 5)
    await hass.async_block_till_done()
    rendered = "\n".join(_render(hass, content) for content in cards)
    assert "█" not in rendered  # the bar is a timer bar card of the sensor
    # the progress sensor rises with the simulation and the card shows its message (the loading phase or the replay)
    progress = hass.states.get(f"sensor.{PREFIX}_what_if_progress")
    assert progress.state == "42.0" and progress.attributes["status"] == "running"
    assert status(hass).attributes["progress"]["message"] in rendered
    # the previous result is still shown meanwhile
    assert "150.00 kr" in rendered
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(f"sensor.{PREFIX}_what_if_progress").state == "0.0"

    # and a failure shows its reason
    coordinator.async_what_if = AsyncMock(side_effect=HomeAssistantError(translation_domain=DOMAIN, translation_key="what_if_no_recorder"))
    await run(hass)
    rendered = "\n".join(_render(hass, content) for content in cards)
    assert "Recorder er påkrævet" in rendered
