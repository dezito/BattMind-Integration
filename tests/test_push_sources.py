"""The sources of the push notifications (engine, health checks, start, options) and their way to a notify service."""
from __future__ import annotations

import datetime
import time
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.battmind.const import DOMAIN, CONF_ENTITY_PREFIX, build_config
from custom_components.battmind.coordinator.coordinator import BattMindCoordinator
from custom_components.battmind.engine import BattMindEngine, JobResult
from custom_components.battmind.management.dependency_checks import (
    ISSUE_CONSUMPTION_FROZEN,
    ISSUE_ENTITIES_MISSING,
    ISSUE_PLAN_NOT_FOLLOWED,
)
from custom_components.battmind.management.health_checks import HealthWatcher

from .conftest import BATTERY_FLOW, CONSUMPTION, MOCK_OPTIONS, PREFIX, PRICE, async_unload_and_join, set_source_states
from .test_engine import DAY, OPTIONS, TEXTS, TZ, _power_values_db, plan, snapshot, started_engine

SERVICE = "mobile_app_test"
PUSH_OPTIONS = {"notify_list": [f"notify.{SERVICE}"], "notify_problems": True, "notify_storm": True, "notify_learning": True, "notify_summary": True}
NOW = datetime.datetime.combine(DAY, datetime.time(10, 30))


# --- the engine ----------------------------------------------------------------------------------------------------------------


def _run(engine, snap) -> JobResult:
    result = engine.run_job(snap, engine.charge_if_needed, True)
    assert result.error is None, result.error
    return result


def test_a_lost_battery_level_is_pushed_as_a_condition_and_a_good_one_as_over() -> None:
    engine = started_engine(NOW, 50)
    entity = "sensor.battery_level"

    assert _run(engine, snapshot(NOW, 50)).conditions["battery_level_lost"] == (False, {"entity": entity})
    lost = snapshot(NOW, 50)
    lost.entity_states[entity] = "unavailable"  # (and no state in the buffers within BATTERY_LEVEL_MAX_AGE: nothing to go on)
    assert _run(engine, lost).conditions["battery_level_lost"] == (True, {"entity": entity})


@pytest.mark.parametrize(("temperature", "stopped"), [(20, False), (-1, True), (51, True)])
def test_the_temperature_stop_is_a_condition(temperature: float, stopped: bool) -> None:
    engine = BattMindEngine(
        build_config({**OPTIONS, "battery_temperature_entity_id": "sensor.battery_temperature", "battery_temperature_min": 0, "battery_temperature_max": 50}),
        "test", TEXTS,
    )
    snap = snapshot(NOW, 50)
    snap.entity_states["sensor.battery_temperature"] = str(temperature)
    snap.units["sensor.battery_temperature"] = "°C"
    assert engine.run_job(snap, engine.startup, {"power_values": _power_values_db()}).error is None

    active, params = _run(engine, snap).conditions["temperature_stop"]

    assert active is stopped
    assert bool(params["detail"]) is stopped  # the stop is explained with the rule text of the plan (in the user's language)


def test_an_unavailable_temperature_sensor_stops_the_battery_and_says_so() -> None:
    engine = BattMindEngine(build_config({**OPTIONS, "battery_temperature_entity_id": "sensor.battery_temperature"}), "test", TEXTS)
    snap = snapshot(NOW, 50)
    snap.entity_states["sensor.battery_temperature"] = "unavailable"
    assert engine.run_job(snap, engine.startup, {"power_values": _power_values_db()}).error is None

    assert _run(engine, snap).conditions["temperature_stop"][0] is True


def test_without_a_temperature_sensor_there_is_no_temperature_condition() -> None:
    engine = started_engine(NOW, 50)

    assert "temperature_stop" not in _run(engine, snapshot(NOW, 50)).conditions


def test_a_job_that_fails_three_times_in_a_row_is_one_condition_until_it_works_again() -> None:
    engine = started_engine(NOW, 50)

    with patch.object(BattMindEngine, "cheap_grid_charge_hours", side_effect=RuntimeError("no prices")):
        first = _run(engine, snapshot(NOW, 50))
        second = _run(engine, snapshot(NOW, 50))
        third = _run(engine, snapshot(NOW, 50))
    assert "job_failing" not in first.conditions and "job_failing" not in second.conditions  # (the error is caught inside the job)
    assert third.request_reload is True and third.conditions["job_failing"] == (True, {"error": "no prices"})

    assert _run(engine, snapshot(NOW, 50)).conditions["job_failing"] == (False, {})


def _storm_snapshot(start: str, end: str, state: str = "on"):
    snap = snapshot(NOW, 50)
    snap.storm_alert = {
        "entity_id": "binary_sensor.meteoalarm",
        "state": state,
        "attributes": {"awareness_level": "3; orange; Severe", "awareness_type": "1; wind", "onset": start, "expires": end},
    }
    return snap


@pytest.mark.parametrize(
    ("start", "end", "state", "expected"),
    [
        ("2026-10-22T08:00:00+02:00", "2026-10-22T23:00:00+02:00", "on", True),  # going on
        ("2026-10-22T18:00:00+02:00", "2026-10-22T23:00:00+02:00", "on", True),  # coming
        ("2026-10-21T08:00:00+02:00", "2026-10-22T05:00:00+02:00", "on", False),  # over
        ("2026-10-22T18:00:00+02:00", "2026-10-22T23:00:00+02:00", "off", False),  # no warning
        ("2026-10-22T18:00:00+02:00", "2026-10-22T23:00:00+02:00", "unavailable", None),  # nothing is known: not evaluated
        ("2027-02-01T08:00:00+01:00", "2027-02-01T23:00:00+01:00", "on", None),  # later: after the hours the plan covers, no storm yet
    ],
)
def test_the_storm_is_one_condition_from_coming_to_over(start: str, end: str, state: str, expected: bool | None) -> None:
    engine = started_engine(NOW, 50, options={"storm_warning_entity_id": "binary_sensor.meteoalarm"})

    result = _run(engine, _storm_snapshot(start, end, state))
    conditions = result.conditions
    if start.startswith("2027"):
        assert result.states["storm_warning"] == "later"  # (the storm path did run: the plan's hours end before it)

    if expected is None:
        assert conditions.get("storm", (None, {}))[0] is None  # (a later one says None: not an earlier True either)
        return
    assert conditions["storm"][0] is expected
    if expected:
        assert set(conditions["storm"][1]) == {"level", "start", "end", "keep"} and conditions["storm"][1]["level"] == 3


def test_learning_is_reported_with_the_hour_end() -> None:
    engine = started_engine(NOW, 50)

    result = engine.run_job(snapshot(NOW.replace(minute=59), 50), engine.hour_end, NOW.replace(minute=0))

    assert result.error is None, result.error
    # ten days of consumption history: learned; nothing measured yet for the efficiency and the capacity
    assert result.ready == {"learning_consumption": True, "learning_efficiency": False, "learning_capacity": False}


def _summary_engine(**config):
    engine = started_engine(datetime.datetime.combine(DAY, datetime.time(20, 30)), 50)
    engine.config.update({"notify_list": ["notify.x"], "notify_summary": True, "notify_summary_hour": 20, **config})
    plan(engine, datetime.datetime.combine(DAY, datetime.time(20, 30)), 50)
    return engine


def _hour_end(engine, hour: int) -> JobResult:
    at = datetime.datetime.combine(DAY, datetime.time(hour, 59))
    result = engine.run_job(snapshot(at, 50), engine.hour_end, at.replace(minute=0))
    assert result.error is None, result.error
    return result


def test_the_summary_of_tomorrow_comes_from_the_summary_hour_when_the_prices_are_known() -> None:
    engine = _summary_engine()
    tomorrow = DAY + datetime.timedelta(days=1)

    assert _hour_end(engine, 19).summary is None  # not yet
    summary = _hour_end(engine, 20).summary

    assert summary is not None and summary["date"] == tomorrow.isoformat() and summary["day"] == tomorrow.strftime("%d/%m")
    assert set(summary) == {"date", "day", "grid", "hours", "sale", "level"}
    figures = engine._plan_figures(tomorrow)  # the same figures as the analysis keeps for the day
    assert summary["grid"] == f"{figures[1]:.1f}" and summary["sale"] == f"{figures[2]:.1f}"
    assert _hour_end(engine, 22).summary is not None  # (a summary that could not be sent goes on being offered until it is)


@pytest.mark.parametrize(("today", "hours"), [(datetime.date(2026, 10, 24), 25), (datetime.date(2026, 3, 28), 23)])
def test_the_summary_comes_on_the_day_before_the_clocks_change(today: datetime.date, hours: int) -> None:
    """Tomorrow has 25 or 23 hours: the prices are per naive local hour, so the repeated hour is one of them."""
    now = datetime.datetime.combine(today, datetime.time(20, 30))
    engine = started_engine(now, 50)
    engine.config.update({"notify_list": ["notify.x"], "notify_summary": True, "notify_summary_hour": 20})
    plan(engine, now, 50)
    assert engine._local_day_hours((today + datetime.timedelta(days=1)).isoformat(), TZ) == hours

    at = now.replace(minute=59)
    result = engine.run_job(snapshot(at, 50), engine.hour_end, at.replace(minute=0))

    assert result.error is None, result.error
    assert result.summary is not None and result.summary["date"] == (today + datetime.timedelta(days=1)).isoformat()


def test_the_summary_waits_for_every_hour_of_tomorrow() -> None:
    engine = _summary_engine()
    provider = engine.price_provider
    tomorrow = DAY + datetime.timedelta(days=1)
    provider.buy_real_prices = {hour: price for hour, price in provider.buy_real_prices.items() if not (hour.date() == tomorrow and hour.hour == 13)}

    assert _hour_end(engine, 20).summary is None


def test_every_push_category_is_on_by_default() -> None:
    from custom_components.battmind.const.config import DEFAULT_CONFIG

    assert [DEFAULT_CONFIG[key] for key in ("notify_problems", "notify_storm", "notify_learning", "notify_summary")] == [True] * 4
    assert not DEFAULT_CONFIG["notify_list"]  # (nothing is sent until a service is chosen)


def test_without_the_option_or_a_service_there_is_no_summary() -> None:
    assert _hour_end(_summary_engine(notify_summary=False), 20).summary is None
    assert _hour_end(_summary_engine(notify_list=[]), 20).summary is None


# --- the health checks ----------------------------------------------------------------------------------------------------------


class Recorder:
    def __init__(self) -> None:
        self.reports: list[tuple[str, bool | None, dict]] = []

    def report(self, kind: str, active: bool | None, **params: Any) -> None:
        self.reports.append((kind, active, params))


def _da(key: str, default: str | None = None, **fmt) -> str:
    return default if default is not None else key


async def _watch(hass: HomeAssistant, entities: list[str], recorder: Recorder) -> HealthWatcher:
    entry = MockConfigEntry(domain=DOMAIN, title=f"BattMind ({PREFIX})", options=dict(MOCK_OPTIONS))
    entry.add_to_hass(hass)
    watcher = HealthWatcher(hass, entry, PREFIX, build_config(MOCK_OPTIONS), entities, lambda: set(), _da, notifier=recorder)
    await watcher.async_load()
    watcher.start()
    await hass.async_block_till_done(wait_background_tasks=True)
    return watcher


async def test_a_missing_sensor_is_a_condition_and_a_present_one_is_over(hass: HomeAssistant, hass_storage) -> None:
    recorder = Recorder()
    watcher = await _watch(hass, ["sensor.nowhere"], recorder)
    try:
        assert ("entities_missing", True, {"entities": "- sensor.nowhere"}) in recorder.reports
    finally:
        watcher.stop()

    recorder = Recorder()
    hass.states.async_set("sensor.nowhere", "5")
    watcher = await _watch(hass, ["sensor.nowhere"], recorder)
    try:
        assert ("entities_missing", False, {}) in recorder.reports
    finally:
        watcher.stop()


async def test_the_repairs_that_are_pushed_name_the_kind_and_carry_their_numbers(hass: HomeAssistant, hass_storage) -> None:
    recorder = Recorder()
    watcher = await _watch(hass, [], recorder)
    try:
        recorder.reports.clear()
        watcher._create(ISSUE_PLAN_NOT_FOLLOWED, {"percent": "40", "mismatches": "9", "checked": "24"})
        watcher._create(ISSUE_CONSUMPTION_FROZEN, {"entity": "sensor.x", "value": "5", "since": "2026-11-25 04:00"})
        watcher._delete(ISSUE_PLAN_NOT_FOLLOWED)
        watcher._delete(ISSUE_ENTITIES_MISSING)
        watcher._create("price_unit_scale", {"entities": "x"})  # a repair that is not pushed
    finally:
        watcher.stop()

    assert recorder.reports == [
        ("plan_not_followed", True, {"percent": "40", "mismatches": "9", "checked": "24"}),
        ("consumption_frozen", True, {"entity": "sensor.x", "value": "5", "since": "2026-11-25 04:00"}),
        ("plan_not_followed", False, {}),
        ("entities_missing", False, {}),
    ]


# --- the start, the way to the service and the options -----------------------------------------------------------------------------


def _push_entry(**options) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"BattMind ({PREFIX})",
        data={CONF_ENTITY_PREFIX: PREFIX, "legacy_prefix": None, "initial_settings": {}},
        options={**MOCK_OPTIONS, **PUSH_OPTIONS, **options},
    )


async def test_a_failed_start_is_pushed_in_the_language_of_the_entry(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    set_source_states(hass)
    entry = _push_entry()
    entry.add_to_hass(hass)
    with patch.object(BattMindEngine, "startup", side_effect=RuntimeError("engine boom")):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1
    assert calls[0].data["title"] == "BattMind: kunne ikke starte"  # (language: da)
    assert "engine boom" in calls[0].data["message"]
    await async_unload_and_join(hass, entry)


async def test_a_condition_reported_by_a_job_is_pushed_once_and_over_when_it_ends(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    set_source_states(hass)
    entry = _push_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator: BattMindCoordinator = entry.runtime_data
    calls.clear()

    for _ in range(3):
        coordinator._push_notices(JobResult(conditions={"plan_not_followed": (True, {"percent": "40", "mismatches": "9", "checked": "24"})}))
        await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 1 and "40 %" in calls[0].data["message"]

    coordinator._push_notices(JobResult(conditions={"plan_not_followed": (None, {})}))  # not evaluated: nothing
    coordinator._push_notices(JobResult(conditions={"plan_not_followed": (False, {})}))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 2 and calls[1].data["data"] == calls[0].data["data"]  # the same tag: "over" replaces the problem
    await async_unload_and_join(hass, entry)


async def test_a_category_that_is_off_sends_nothing(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    set_source_states(hass)
    entry = _push_entry(notify_storm=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    entry.runtime_data._push_notices(JobResult(conditions={"storm": (True, {"level": 3, "start": "a", "end": "b", "keep": "80"})}))  # (storm is off)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert calls == []
    await async_unload_and_join(hass, entry)


async def test_the_notifications_page_offers_the_services_and_saves_the_choice(hass: HomeAssistant, loaded_entry) -> None:
    async_mock_service(hass, "notify", SERVICE)
    async_mock_service(hass, "notify", "send_message")  # (not offered: it needs an entity)
    hass.states.async_set("notify.alice_phone", "unknown")

    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert "menu_notifications" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_notifications"})
    assert result["step_id"] == "notifications"
    field = next(key for key in result["data_schema"].schema if key == "notify_list")
    offered = result["data_schema"].schema[field].config["options"]
    assert f"notify.{SERVICE}" in offered and "notify.alice_phone" in offered and "notify.send_message" not in offered

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "notify_list": [f"notify.{SERVICE}", "notify.alice_phone"], "notify_problems": True, "notify_storm": False, "notify_learning": True,
            "notify_summary": True, "notify_summary_hour": 21.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    options = loaded_entry.options
    assert options["notify_list"] == [f"notify.{SERVICE}", "notify.alice_phone"] and options["notify_storm"] is False
    assert options["notify_summary"] is True and options["notify_summary_hour"] == 21 and isinstance(options["notify_summary_hour"], int)
    config = loaded_entry.runtime_data.config
    assert config["notify_list"] == options["notify_list"] and config["notify_summary_hour"] == 21


async def test_a_service_that_is_chosen_stays_in_the_list_when_it_is_not_registered_yet(hass: HomeAssistant, loaded_entry) -> None:
    hass.config_entries.async_update_entry(loaded_entry, options={**loaded_entry.options, "notify_list": ["notify.mobile_app_late"]})
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_notifications"})

    field = next(key for key in result["data_schema"].schema if key == "notify_list")
    assert "notify.mobile_app_late" in result["data_schema"].schema[field].config["options"]


def test_the_push_texts_of_every_kind_exist_in_english() -> None:
    from custom_components.battmind.management.notifier import CATEGORIES
    from custom_components.battmind.utils.formatting import load_texts

    texts = load_texts("en")
    for kind in CATEGORIES:
        assert f"ui.push.{kind}.title" in texts, kind
        assert any(f"ui.push.{kind}.{phase}" in texts for phase in ("problem", "once", "summary")), kind


async def test_the_battery_level_sensor_is_not_in_the_missing_sensors_push(hass: HomeAssistant, hass_storage) -> None:
    level = MOCK_OPTIONS["powerwall_battery_level_entity_id"]
    recorder = Recorder()
    watcher = await _watch(hass, [level, "sensor.other_gone"], recorder)
    try:
        assert ("entities_missing", True, {"entities": "- sensor.other_gone"}) in recorder.reports  # battery_level_lost says the level
    finally:
        watcher.stop()

    recorder = Recorder()
    watcher = await _watch(hass, [level], recorder)
    try:
        assert ("entities_missing", False, {}) in recorder.reports and not any(report[1] is True and report[0] == "entities_missing" for report in recorder.reports)
    finally:
        watcher.stop()


def test_the_pyscript_import_does_not_turn_the_push_notifications_on() -> None:
    from custom_components.battmind.flow.schema import options_from_legacy

    options, _ = options_from_legacy({"notify_list": ["mobile_app_old_phone"], "days_to_predict": 3})

    assert options["notify_list"] == [] and options["days_to_predict"] == 3


async def test_a_cleared_list_stays_cleared(hass: HomeAssistant, loaded_entry) -> None:
    hass.config_entries.async_update_entry(loaded_entry, options={**loaded_entry.options, "notify_list": [f"notify.{SERVICE}"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_notifications"})

    # the frontend leaves a cleared multiple select out
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"notify_problems": True, "notify_storm": True, "notify_learning": True, "notify_summary": False, "notify_summary_hour": 20}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["notify_list"] == []


async def test_a_failing_hour_end_is_pushed_after_three_tries_and_over_when_it_works(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    set_source_states(hass)
    entry = _push_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator: BattMindCoordinator = entry.runtime_data
    calls.clear()

    with patch.object(BattMindEngine, "hour_end", side_effect=RuntimeError("history down")), patch("custom_components.battmind.coordinator.events.asyncio.sleep"):
        await coordinator._async_hour_end()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 1 and "history down" in calls[0].data["message"]

    await coordinator._async_hour_end()  # works again
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 2 and calls[1].data["data"] == calls[0].data["data"]
    await async_unload_and_join(hass, entry)


async def test_a_started_entry_calls_a_failed_start_over(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    set_source_states(hass)
    entry = _push_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator: BattMindCoordinator = entry.runtime_data
    calls.clear()

    coordinator.notice("startup_failed", True, error="boom")
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator.notice("startup_failed", False)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 2 and "boom" in calls[0].data["message"] and calls[1].data["message"] == "BattMind er startet."
    await async_unload_and_join(hass, entry)


async def test_removing_the_entry_removes_the_push_notification_store(hass: HomeAssistant, hass_storage) -> None:
    async_mock_service(hass, "notify", SERVICE)
    set_source_states(hass)
    entry = _push_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator: BattMindCoordinator = entry.runtime_data
    coordinator.notice("startup_failed", True, error="x")
    await hass.async_block_till_done(wait_background_tasks=True)
    await coordinator.notifier._store.async_save(coordinator.notifier.state)
    key = f"battmind.{entry.entry_id}.notices"
    assert key in hass_storage

    # (the removal also saves a backup of the entry in the config folder: not in this test, it would be left for the other tests)
    with patch("custom_components.battmind.async_export"):
        await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert key not in hass_storage


async def test_only_the_consumption_and_the_battery_power_sensor_count_as_unavailable(hass: HomeAssistant, hass_storage) -> None:
    hass.states.async_set(CONSUMPTION, "unavailable", timestamp=time.time() - 3600)
    hass.states.async_set(BATTERY_FLOW, "5")
    hass.states.async_set(PRICE, "unavailable", timestamp=time.time() - 3600)  # prices have their own checks
    hass.states.async_set("sensor.solar_like", "unavailable", timestamp=time.time() - 3600)  # (unavailable every night for many inverters)
    hass.states.async_set("sensor.stuck", "unknown")
    recorder = Recorder()
    watcher = await _watch(hass, [CONSUMPTION, BATTERY_FLOW, PRICE, "sensor.solar_like", "sensor.stuck"], recorder)
    try:
        assert ("sensors_unavailable", True, {"entities": f"- {CONSUMPTION}"}) in recorder.reports
    finally:
        watcher.stop()

    # the consumption is back: over, whatever the price, the solar sensor and an unrelated sensor stuck at unknown do
    hass.states.async_set(CONSUMPTION, "600")
    recorder = Recorder()
    watcher = await _watch(hass, [CONSUMPTION, BATTERY_FLOW, PRICE, "sensor.solar_like", "sensor.stuck"], recorder)
    try:
        assert ("sensors_unavailable", False, {}) in recorder.reports
    finally:
        watcher.stop()


async def test_an_essential_sensor_that_may_still_be_unavailable_does_not_say_it_is_over(hass: HomeAssistant, hass_storage) -> None:
    """After a restart last_changed starts again, and unknown is no answer: only a sensor with a real state is 'available again'."""
    for state in ("unavailable", "unknown"):
        hass.states.async_set(CONSUMPTION, state)
        hass.states.async_set(BATTERY_FLOW, "5")
        recorder = Recorder()
        watcher = await _watch(hass, [CONSUMPTION, BATTERY_FLOW], recorder)
        try:
            assert ("sensors_unavailable", None, {}) in recorder.reports and not any(report[:2] == ("sensors_unavailable", False) for report in recorder.reports)
        finally:
            watcher.stop()


def test_a_capacity_under_suspicion_is_neither_ready_nor_reset() -> None:
    engine = started_engine(NOW, 50)
    # five runs per direction that say more energy came out than went in
    runs = lambda low, high, ac: [{"l0": low, "l1": high, "t0": 1.0 + i, "t1": 2.0 + i, "ac": ac} for i in range(5)]  # noqa: E731
    ids = engine.config["home"]["entity_ids"]
    identity = [ids["powerwall_watt_flow_entity_id"], ids["powerwall_battery_level_entity_id"], bool(engine.config["home"]["invert_powerwall_watt_flow_entity_id"]), 10.0]
    engine.analysis_db["capacity"] = {"episodes": {"charging": runs(10.0, 60.0, 3.0), "discharging": runs(60.0, 10.0, 5.0)}, "open": None, "sensors": identity}

    result = engine.run_job(snapshot(NOW.replace(minute=59), 50), engine.hour_end, NOW.replace(minute=0))

    assert result.ready["learning_capacity"] is None
