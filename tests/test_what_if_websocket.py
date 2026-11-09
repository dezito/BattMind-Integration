"""Dashboard what-if lifecycle and cooperative cancellation."""
from __future__ import annotations

import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.storage import Store

from custom_components.battmind import (
    _WHAT_IF_STORES,
    _websocket_what_if_cancel,
    _websocket_what_if_start,
    _websocket_what_if_status,
)
from custom_components.battmind.const import DOMAIN
from custom_components.battmind.engine import JobResult
from custom_components.battmind.engine.planner.replay import WhatIfCancelled, replay_history
from custom_components.battmind.management.what_if_storage import DashboardJob
from .test_planner_rules import DAY, _what_if_data, planned, realistic_snapshot


async def _send(client, entry, command, **extra):
    await client.send_json_auto_id({
        "type": f"battmind/what_if_{command}", "config_entry_id": entry.entry_id, **extra
    })
    return await client.receive_json()


async def test_dashboard_completes_without_notifications(hass, hass_ws_client, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value={"ok": True}))
    client = await hass_ws_client(hass)
    with patch("custom_components.battmind.persistent_notification.async_create") as notification:
        reply = await _send(client, loaded_entry, "start", job_id=str(uuid4()), data={"history_days": 2})
    assert reply["success"]
    assert reply["result"]["status"] == "completed"
    assert reply["result"]["result"] == {"ok": True}
    notification.assert_not_called()
    last = await _send(client, loaded_entry, "last")
    assert last["result"]["simulation"] == {"ok": True}


async def test_dashboard_module_options_are_simulated_and_persisted(hass, hass_ws_client, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value={"module_comparison": {"rows": []}}))
    client = await hass_ws_client(hass)
    data = {
        "history_days": 30,
        "battery_size": 24,
        "module_capacity": 2.5,
        "module_price": 0,
        "module_count": 3,
    }

    reply = await _send(client, loaded_entry, "start", job_id=str(uuid4()), data=data)

    assert reply["success"]
    assert coordinator.async_what_if.call_args.args[:2] == ({"battery_size": 24.0}, 30)
    assert coordinator.async_what_if.call_args.kwargs["module_comparison"] == {
        "module_capacity": 2.5,
        "module_price": 0.0,
        "module_count": 3,
    }
    saved = await _send(client, loaded_entry, "last")
    assert saved["result"]["inputs"] == data
    assert saved["result"]["simulation"] == {"module_comparison": {"rows": []}}


async def test_dashboard_status_exposes_loading_phase_and_real_simulation_progress(
    hass, hass_ws_client, loaded_entry
):
    coordinator = loaded_entry.runtime_data
    phase_reported = asyncio.Event()
    allow_simulation, simulation_reported, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def simulate(_overrides, _days, progress, *, cancel_check):
        progress({"phase": "states", "seconds": 5})
        phase_reported.set()
        await allow_simulation.wait()
        progress({
            "percent": 42, "step": 2, "steps": 3, "hours": 84, "total_hours": 200, "seconds": 25,
        })
        simulation_reported.set()
        await finish.wait()
        return JobResult(value={"ok": True})

    coordinator.async_what_if = simulate
    starter, observer = await hass_ws_client(hass), await hass_ws_client(hass)
    job_id = str(uuid4())
    await starter.send_json_auto_id({
        "type": "battmind/what_if_start", "config_entry_id": loaded_entry.entry_id,
        "job_id": job_id, "data": {"history_days": 2},
    })
    await asyncio.wait_for(phase_reported.wait(), 5)
    phase = await _send(observer, loaded_entry, "status")
    assert phase["result"] == {
        "job_id": job_id, "status": "running",
        "progress": {"phase": "states", "seconds": 5},
    }

    allow_simulation.set()
    await asyncio.wait_for(simulation_reported.wait(), 5)
    progress = await _send(observer, loaded_entry, "status")
    assert progress["result"]["progress"] == {
        "percent": 42, "step": 2, "steps": 3, "hours": 84, "total_hours": 200, "seconds": 25,
    }
    finish.set()
    completed = await starter.receive_json()
    assert completed["result"]["status"] == "completed"


async def test_dashboard_rejects_incomplete_or_invalid_module_options(hass, hass_ws_client, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value={}))
    client = await hass_ws_client(hass)
    for data in (
        {"module_capacity": 2.5},
        {"module_price": 100},
        {"module_count": 2},
        {"module_capacity": 0, "module_price": 100},
        {"module_capacity": 2.5, "module_price": -1},
        {"module_capacity": 2.5, "module_price": 100, "module_count": 13},
        {"module_capacity": 2.5, "module_price": 100, "module_count": 1.9},
    ):
        reply = await _send(client, loaded_entry, "start", job_id=str(uuid4()), data=data)
        assert reply["error"]["code"] == "invalid_format"
    coordinator.async_what_if.assert_not_called()


async def test_service_still_notifies(hass, loaded_entry):
    loaded_entry.runtime_data.async_what_if = AsyncMock(return_value=JobResult(value={"ok": True}))
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


async def test_cancel_from_another_client_stops_worker_and_preserves_result(hass, hass_ws_client, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value={"old": True}))
    starter = await hass_ws_client(hass)
    other = await hass_ws_client(hass)
    first = await _send(starter, loaded_entry, "start", job_id=str(uuid4()), data={})
    assert first["success"]
    started = Event()
    finished = Event()

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
    job_id = str(uuid4())
    await starter.send_json_auto_id({
        "type": "battmind/what_if_start", "config_entry_id": loaded_entry.entry_id,
        "job_id": job_id, "data": {},
    })
    assert await hass.async_add_executor_job(started.wait, 5)
    status = await _send(other, loaded_entry, "status")
    assert status["result"] == {"job_id": job_id, "status": "running", "progress": None}
    wrong = await _send(other, loaded_entry, "cancel", job_id=str(uuid4()))
    assert wrong["error"]["code"] == "not_found"
    duplicate = await _send(other, loaded_entry, "start", job_id=str(uuid4()), data={})
    assert duplicate["error"]["code"] == "already_running"
    stop = await _send(other, loaded_entry, "cancel", job_id=job_id)
    assert stop["result"] == {"job_id": job_id, "status": "stopping"}
    reply = await starter.receive_json()
    assert reply["result"] == {"job_id": job_id, "status": "cancelled"}
    assert finished.is_set()
    assert (await _send(other, loaded_entry, "last"))["result"]["simulation"] == {"old": True}
    assert (await _send(other, loaded_entry, "status"))["result"]["status"] == "idle"
    reused = await _send(other, loaded_entry, "start", job_id=job_id, data={})
    assert reused["error"]["code"] == "invalid_format"


async def test_save_commit_is_too_late_to_cancel(hass, hass_ws_client, loaded_entry):
    loaded_entry.runtime_data.async_what_if = AsyncMock(return_value=JobResult(value={"saved": True}))
    started, release = asyncio.Event(), asyncio.Event()
    original = Store.async_save

    async def save(store, data):
        started.set()
        await release.wait()
        await original(store, data)

    client, other = await hass_ws_client(hass), await hass_ws_client(hass)
    job_id = str(uuid4())
    with patch.object(Store, "async_save", new=save):
        await client.send_json_auto_id({
            "type": "battmind/what_if_start", "config_entry_id": loaded_entry.entry_id,
            "job_id": job_id, "data": {},
        })
        await asyncio.wait_for(started.wait(), 5)
        cancel = await _send(other, loaded_entry, "cancel", job_id=job_id)
        assert cancel["error"]["code"] == "too_late"
        release.set()
        assert (await client.receive_json())["result"]["status"] == "completed"
    assert (await _send(other, loaded_entry, "last"))["result"]["simulation"] == {"saved": True}


async def test_cancel_while_waiting_for_store_lock_keeps_previous_result(hass, hass_ws_client, loaded_entry):
    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value={"old": True}))
    client, other = await hass_ws_client(hass), await hass_ws_client(hass)
    assert (await _send(client, loaded_entry, "start", job_id=str(uuid4()), data={}))["success"]
    store = hass.data[_WHAT_IF_STORES][loaded_entry.entry_id]
    coordinator.async_what_if = AsyncMock(return_value=JobResult(value={"new": True}))
    job_id = str(uuid4())
    await store.lock.acquire()
    try:
        await client.send_json_auto_id({
            "type": "battmind/what_if_start", "config_entry_id": loaded_entry.entry_id,
            "job_id": job_id, "data": {},
        })
        for _ in range(100):
            if coordinator.async_what_if.await_count:
                break
            await asyncio.sleep(0)
        assert coordinator.async_what_if.await_count
        assert (await _send(other, loaded_entry, "cancel", job_id=job_id))["success"]
    finally:
        store.lock.release()
    assert (await client.receive_json())["result"]["status"] == "cancelled"
    assert (await _send(other, loaded_entry, "last"))["result"]["simulation"] == {"old": True}


async def test_unload_signals_and_waits_for_dashboard_worker(hass, hass_ws_client, loaded_entry):
    entered, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def simulate(_overrides, _days, _progress, *, cancel_check):
        entered.set()
        await release.wait()
        try:
            cancel_check()
        finally:
            exited.set()

    coordinator = loaded_entry.runtime_data
    coordinator.async_what_if = simulate
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({
        "type": "battmind/what_if_start", "config_entry_id": loaded_entry.entry_id,
        "job_id": str(uuid4()), "data": {},
    })
    await asyncio.wait_for(entered.wait(), 5)
    store = hass.data[_WHAT_IF_STORES][loaded_entry.entry_id]
    unloading = asyncio.create_task(hass.config_entries.async_unload(loaded_entry.entry_id))
    for _ in range(100):
        if store.closed:
            break
        await asyncio.sleep(0)
    assert store.closed
    assert not unloading.done()
    release.set()
    assert await asyncio.wait_for(unloading, 5)
    assert exited.is_set()
    assert (await client.receive_json())["result"]["status"] == "cancelled"


async def test_cancel_does_not_target_ordinary_service(hass, hass_ws_client, loaded_entry):
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
        client = await hass_ws_client(hass)
        assert (await _send(client, loaded_entry, "status"))["result"]["status"] == "idle"
        assert (await _send(client, loaded_entry, "cancel", job_id=str(uuid4())))["error"]["code"] == "not_found"
        assert not service.done()
        release.set()
        result = await asyncio.wait_for(service, 5)
        assert result["simulations"]["bmtest"] == {"service": True}


async def test_unload_waits_for_service_and_preserves_its_save(hass, loaded_entry):
    entered, release = asyncio.Event(), asyncio.Event()

    async def simulate(_overrides, _days, _progress):
        entered.set()
        await release.wait()
        return JobResult(value={"service": True})

    loaded_entry.runtime_data.async_what_if = simulate
    store = hass.data[_WHAT_IF_STORES][loaded_entry.entry_id]
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
        result = await asyncio.wait_for(service, 5)
        assert result["simulations"]["bmtest"] == {"service": True}
        assert await asyncio.wait_for(unloading, 5)
    assert (await store.load())["simulation"] == {"service": True}


async def test_worker_lock_stays_held_until_cancelled_worker_exits(hass, loaded_entry):
    coordinator = loaded_entry.runtime_data
    entered, release, other_entered = Event(), Event(), Event()
    job = DashboardJob(str(uuid4()))

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


@pytest.mark.parametrize("command", [_websocket_what_if_start, _websocket_what_if_status, _websocket_what_if_cancel])
def test_commands_require_admin(command):
    connection = SimpleNamespace(user=SimpleNamespace(is_admin=False))
    with pytest.raises(Unauthorized):
        command(None, connection, {"id": 1})


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
        result = executor.submit(
            engine.run_job, snapshot, engine.what_if, data, {},
            progress=progress, cancel_check=check,
        ).result(timeout=30)
    assert reports
    assert isinstance(result.error, WhatIfCancelled)
    assert result.value is None
