"""The consumption of the Cable Juice Planner chargers is left out of the house consumption (utils/cjp.py)."""
from __future__ import annotations

import datetime

import pytest
from homeassistant.config_entries import ConfigEntryChange, ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battmind.const import build_config
from custom_components.battmind.coordinator import BattMindCoordinator
from custom_components.battmind.engine import BattMindEngine
from custom_components.battmind.utils.cjp import cjp_charger_power_entities
from custom_components.battmind.utils.power import unique_entity_ids

from .conftest import MOCK_OPTIONS, async_join_workers, async_unload_and_join, form_defaults, set_source_states
from .test_engine import DAY, OPTIONS, TEXTS, TZ, _hourly_statistics, snapshot, started_engine

CJP = "cable_juice_planner"


def cjp_entry(hass: HomeAssistant, charger: str | None, announce: bool = False, key: str = "charger_power_entity_id", **kwargs) -> MockConfigEntry:
    """A CJP config entry. announce=True sends the signal a real new entry sends (add_to_hass does not).

    Like a real car it also has a house consumption sensor, in the option power_consumption_entity_id.
    """
    options = {"power_consumption_entity_id": "sensor.cjp_house"}
    if charger is not None:
        options[key] = charger
    kwargs.setdefault("data", {"name": "Car", "entity_prefix": "cjp_car"})
    entry = MockConfigEntry(domain=CJP, title="Cable Juice Planner", options=options, **kwargs)
    entry.add_to_hass(hass)
    if announce:
        hass.config_entries._async_dispatch(ConfigEntryChange.ADDED, entry)
    return entry


def test_unique_entity_ids_keeps_the_order_and_drops_empty_values() -> None:
    assert unique_entity_ids(["sensor.a", "", None, "sensor.b", "sensor.a"]) == ["sensor.a", "sensor.b"]
    assert unique_entity_ids(None) == []


async def test_chargers_of_the_cjp_cars_only(hass: HomeAssistant) -> None:
    """Cars with a charger; not the site entry, disabled entries or cars without one, and never a car's house sensor."""
    cjp_entry(hass, "sensor.zoe_power")
    cjp_entry(hass, "sensor.tesla_power", key="power_consumtion_entity_id")  # a car CJP has not migrated yet
    cjp_entry(hass, "sensor.zoe_power")  # the same charger twice
    cjp_entry(hass, "sensor.site_power", data={"site": True})
    cjp_entry(hass, "sensor.old_power", disabled_by=ConfigEntryDisabler.USER)
    cjp_entry(hass, "")
    cjp_entry(hass, None)
    MockConfigEntry(domain="other", options={"charger_power_entity_id": "sensor.other"}).add_to_hass(hass)

    assert cjp_charger_power_entities(hass) == ["sensor.tesla_power", "sensor.zoe_power"]  # not sensor.cjp_house


async def test_the_chargers_are_added_once_and_not_required(hass: HomeAssistant, mock_entry) -> None:
    cjp_entry(hass, "sensor.zoe_power")
    cjp_entry(hass, "sensor.tesla_power")
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry, options={**MOCK_OPTIONS, "ignore_consumption_from_entity_ids": ["sensor.zoe_power", "sensor.pool", "sensor.pool"]}
    )

    coordinator = BattMindCoordinator(hass, mock_entry)

    # the user's sensors first, then the chargers they did not list, nothing twice
    assert coordinator.config["home"]["entity_ids"]["ignore_consumption_from_entity_ids"] == ["sensor.zoe_power", "sensor.pool", "sensor.tesla_power"]
    assert coordinator.auto_ignored_entities == ["sensor.tesla_power"]
    # the charger of a car that is not set up yet must not hold BattMind up, but its history is kept and its unit read
    assert "sensor.tesla_power" not in coordinator.configured_entities
    assert "sensor.tesla_power" in coordinator.buffered_entities and "sensor.tesla_power" in coordinator.statistics_entities
    assert "sensor.tesla_power" in coordinator.snapshot_entities
    assert {"sensor.zoe_power", "sensor.pool"} <= set(coordinator.configured_entities)
    await async_join_workers(hass, coordinator)


async def test_switched_off_adds_no_charger(hass: HomeAssistant, mock_entry) -> None:
    cjp_entry(hass, "sensor.zoe_power")
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={**MOCK_OPTIONS, "ignore_cjp_chargers": False, "ignore_consumption_from_entity_ids": ["sensor.pool"]})

    coordinator = BattMindCoordinator(hass, mock_entry)

    assert coordinator.config["home"]["entity_ids"]["ignore_consumption_from_entity_ids"] == ["sensor.pool"]
    assert coordinator.auto_ignored_entities == [] and coordinator.cjp_chargers == []
    await async_join_workers(hass, coordinator)


def test_an_entry_without_the_option_ignores_the_chargers() -> None:
    """Entries stored before the option existed (and the PyScript import) get it on."""
    assert build_config({})["home"]["ignore_cjp_chargers"] is True
    assert build_config({"ignore_cjp_chargers": None})["home"]["ignore_cjp_chargers"] is True
    assert build_config({"ignore_cjp_chargers": False})["home"]["ignore_cjp_chargers"] is False


def test_a_sensor_listed_twice_is_subtracted_once() -> None:
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    start = now.replace(tzinfo=TZ).timestamp() - 7200

    def ignored(listed: list[str]) -> float:
        engine = BattMindEngine(build_config({**OPTIONS, "ignore_consumption_from_entity_ids": listed}), "test", TEXTS)
        snap = snapshot(now, 50)
        snap.buffers = {"sensor.consumption": [(start, 2000.0)], "sensor.battery_power": [(start, 0.0)], "sensor.charger": [(start, 700.0)]}
        snap.units["sensor.charger"] = "W"
        result = engine.run_job(snap, engine.power_values, period=60)
        assert result.error is None, result.error
        return result.value["ignored_consumption"]

    assert ignored(["sensor.charger"]) == 700.0
    assert ignored(["sensor.charger", "sensor.charger"]) == 700.0


def _backfill_engine(auto: bool):
    """An engine that ignores sensor.charger, which BattMind added itself (auto) or the user listed."""
    now = datetime.datetime.combine(DAY, datetime.time(12, 30))
    engine = started_engine(now, 50, with_history=False, options={"ignore_consumption_from_entity_ids": ["sensor.charger"]})
    engine.config["home"]["auto_ignored_entity_ids"] = ["sensor.charger"] if auto else []
    return engine, now


def _statistics(now, charger_watt: float | None) -> dict:
    statistics = {"sensor.consumption": _hourly_statistics(now, 2, 900.0), "sensor.battery_power": _hourly_statistics(now, 2, 0.0)}
    if charger_watt is not None:
        statistics["sensor.charger"] = _hourly_statistics(now, 2, charger_watt)
    return statistics


def test_backfill_leaves_out_a_found_charger_without_statistics() -> None:
    engine, now = _backfill_engine(auto=True)
    result = engine.run_job(snapshot(now, 50), engine.power_values_backfill, _statistics(now, None))
    assert result.error is None, result.error
    assert result.value == 2 * 24 + 12  # not zero hours because one sensor has no history
    assert engine.power_values_db[9]["ignored_consumption"][0][1] == 0.0


def test_backfill_still_requires_the_statistics_of_a_listed_sensor() -> None:
    engine, now = _backfill_engine(auto=False)
    assert engine.run_job(snapshot(now, 50), engine.power_values_backfill, _statistics(now, None)).value == 0


def test_backfill_subtracts_a_found_charger_that_has_statistics() -> None:
    engine, now = _backfill_engine(auto=True)
    snap = snapshot(now, 50)
    snap.units = {**snap.units, "sensor.charger": "kW"}
    assert engine.run_job(snap, engine.power_values_backfill, _statistics(now, 0.4)).value == 2 * 24 + 12
    row = engine.power_values_db[9]
    assert row["ignored_consumption"][0][1] == 400.0
    assert row["power_consumption_without_ignored"][0][1] == 500.0


@pytest.mark.parametrize(("auto", "mentioned"), [(True, False), (False, True)])
def test_what_if_leaves_out_a_found_charger_without_statistics(auto: bool, mentioned: bool) -> None:
    engine, now = _backfill_engine(auto=auto)
    stats = _statistics(now, None)
    # the rest of the data (prices) is not given, so it fails anyway: only whether the charger is named matters
    result = engine.run_job(snapshot(now, 50), engine.what_if, {"stats": stats}, {})
    assert result.error is not None
    assert ("sensor.charger" in str(result.error)) is mentioned


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_changed_set_of_chargers_reloads_battmind_once(hass: HomeAssistant, loaded_entry) -> None:
    """A car added, given another charger or removed reloads BattMind; the state changes of a CJP entry do not."""
    coordinators = [loaded_entry.runtime_data]

    async def changed() -> bool:
        await _settle(hass)
        if loaded_entry.runtime_data is coordinators[-1]:
            return False
        await async_join_workers(hass, coordinators[-1])
        coordinators.append(loaded_entry.runtime_data)
        assert loaded_entry.state is ConfigEntryState.LOADED
        return True

    car = cjp_entry(hass, "sensor.zoe_power", announce=True)
    assert await changed()
    assert coordinators[-1].auto_ignored_entities == ["sensor.zoe_power"]

    # a title change and a charger that stays the same: nothing to do
    hass.config_entries.async_update_entry(car, title="Zoe")
    hass.config_entries.async_update_entry(car, options={**car.options})
    assert not await changed()

    # a site entry and an unrelated integration are no cars
    cjp_entry(hass, "sensor.site_power", announce=True, data={"site": True})
    other = MockConfigEntry(domain="other", options={"charger_power_entity_id": "sensor.other"})
    other.add_to_hass(hass)
    hass.config_entries._async_dispatch(ConfigEntryChange.ADDED, other)
    assert not await changed()

    # a second car: one reload, and a state change of the new entry (loading it) does not start another
    second = cjp_entry(hass, "sensor.tesla_power", announce=True)
    assert await changed()
    assert coordinators[-1].auto_ignored_entities == ["sensor.tesla_power", "sensor.zoe_power"]
    hass.config_entries.async_update_entry(second, title="Tesla")
    assert not await changed()

    hass.config_entries.async_update_entry(car, options={"charger_power_entity_id": "sensor.zoe_wallbox"})
    assert await changed()
    assert coordinators[-1].auto_ignored_entities == ["sensor.tesla_power", "sensor.zoe_wallbox"]

    await hass.config_entries.async_remove(second.entry_id)
    assert await changed()
    assert coordinators[-1].auto_ignored_entities == ["sensor.zoe_wallbox"]


async def test_no_reload_when_the_chargers_are_not_ignored(hass: HomeAssistant, mock_entry) -> None:
    set_source_states(hass)
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={**MOCK_OPTIONS, "ignore_cjp_chargers": False})
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await _settle(hass)
    coordinator = mock_entry.runtime_data

    cjp_entry(hass, "sensor.zoe_power", announce=True)
    await _settle(hass)

    assert mock_entry.runtime_data is coordinator
    await async_unload_and_join(hass, mock_entry)


async def test_the_consumption_page_shows_the_chargers_and_the_switch(hass: HomeAssistant, loaded_entry) -> None:
    cjp_entry(hass, "sensor.zoe_power")
    old_coordinator = loaded_entry.runtime_data
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_consumption"})
    assert result["step_id"] == "consumption"
    assert result["description_placeholders"]["cjp_chargers"] == "sensor.zoe_power"
    assert form_defaults(result)["ignore_cjp_chargers"] is True
    # saved as given
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form_defaults(result), "ignore_cjp_chargers": False})
    assert result["step_id"] == "detection_review"  # the consumption flags are on their own page
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["ignore_cjp_chargers"] is False
    await _settle(hass)
    await async_join_workers(hass, old_coordinator)  # the option update reloaded the entry


async def test_a_cjp_charger_is_subtracted_from_the_consumption(hass: HomeAssistant, mock_entry) -> None:
    """End to end: the planner of the loaded entry sees the charger in the ignored list and reads its unit."""
    cjp_entry(hass, "sensor.zoe_power")
    hass.states.async_set("sensor.zoe_power", "7.0", {"unit_of_measurement": "kW", "device_class": "power"})
    set_source_states(hass)
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await _settle(hass)

    coordinator = mock_entry.runtime_data
    snap = await coordinator._async_snapshot()
    assert snap.units["sensor.zoe_power"] == "kW"
    assert coordinator.engine.config["home"]["entity_ids"]["ignore_consumption_from_entity_ids"] == ["sensor.zoe_power"]
    await async_unload_and_join(hass, mock_entry)
