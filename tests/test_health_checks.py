"""Health checks (management/health_checks.py): repairs for what was set up earlier and has gone wrong since."""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import itertools
import json
import logging
import re
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.battmind.const import DOMAIN, build_config
from custom_components.battmind.engine.analysis.plan_check import CHECKED_ACTIONS
from custom_components.battmind.management.automation_inputs import automations_using_blueprint, blueprint_automation_ids
from custom_components.battmind.management.dependency_checks import (
    RECHECK_DELAY,
    DependencyWatcher,
    remove_dependency_issues,
    remove_issues,
)
from custom_components.battmind.management.health_checks import (
    EVENT_AUTOMATION_RELOADED,
    CONSUMPTION_GRACE,
    FROZEN_AFTER,
    PLAN_MIN_CHECKED_HOURS,
    RECHECK_INTERVAL,
    SAVES_KEY,
    HealthWatcher,
    _frozen_value,
    _pending_saves,
    async_remove_health_store,
    store_key,
)
from custom_components.battmind.management.value_checks import POWER_SCALE_DAYS, VALUE_RECHECK
from custom_components.battmind.utils.formatting import load_texts, translate
from custom_components.battmind.utils.solcast import solcast_endings_for_days

from .conftest import (
    BATTERY_FLOW,
    BATTERY_LEVEL,
    CONSUMPTION,
    MOCK_OPTIONS,
    PREFIX,
    PRICE,
    PRODUCTION,
    async_unload_and_join,
    set_source_states,
)

ACTION = f"sensor.{PREFIX}_powerwall_action"
CONFIGURED = [BATTERY_LEVEL, BATTERY_FLOW, CONSUMPTION, PRODUCTION, PRICE]
DISABLED = er.RegistryEntryDisabler.INTEGRATION
AUTOMATION = "automation.battery_control"
BLUEPRINT_AUTOMATION = f"""- id: auto1
  alias: Battery control
  use_blueprint:
    path: battmind/battery_control.yaml
    input:
      battmind_action: {ACTION}
"""
# the same blueprint, but for another action sensor: never the automation of this entry
OTHER_ACTION = """- id: auto2
  alias: Other
  use_blueprint:
    path: battmind/battery_control.yaml
    input:
      battmind_action: sensor.other_powerwall_action
"""
# the same blueprint again, under another id and entity (the automation that replaced the first one)
THIRD_AUTOMATION = """- id: auto3
  alias: Third
  use_blueprint:
    path: battmind/battery_control.yaml
    input:
      battmind_action: """ + ACTION + "\n"
# the texts of the entry's language (the watcher names the actions of the plan check with them)
DA_TEXTS = load_texts("da")
# the placeholders each repair's texts use, and the watcher passes (checked in every language below)
EXPECTED_PLACEHOLDERS = {
    "entities_missing": {"prefix", "disabled", "missing"},
    "entity_units": {"prefix", "entities"},
    "blueprint_automation_lost": {"prefix", "automations"},
    "power_without_unit": {"prefix", "entities", "max", "days"},
    "battery_level_fraction": {"prefix", "entities", "days"},
    "price_unit_scale": {"prefix", "entities"},
    "plan_not_followed": {"prefix", "days", "checked", "mismatches", "percent", "min_checked", "min_mismatches", "max_percent", "latest"},
    "consumption_frozen": {"prefix", "entity", "value", "since"},
}
# hours the hourly check fires at, always later than the one before (async_fire_time_changed moves the clock forward)
_HOURS = itertools.count(1)


def _da(key: str, default: str | None = None) -> str:
    return translate(DA_TEXTS, key, default)
# the watchers of a test, stopped when it ends
_WATCHERS: list[HealthWatcher] = []


@pytest.fixture(autouse=True)
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """The automations.yaml and the stores of the watchers are written in the config folder: keep them in a temp folder."""
    hass.config.config_dir = str(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
async def _stop_watchers() -> AsyncGenerator[None]:
    yield
    for watcher in _WATCHERS:
        watcher.stop()
    _WATCHERS.clear()


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    config_entry = MockConfigEntry(domain=DOMAIN, title=f"BattMind ({PREFIX})", options=dict(MOCK_OPTIONS))
    config_entry.add_to_hass(hass)
    return config_entry


async def start_watcher(
    hass: HomeAssistant, entry: MockConfigEntry, *, covered: set[str] = frozenset(), entities: list[str] = CONFIGURED,
    config: dict[str, Any] | None = None, price_units: Any = None,
) -> HealthWatcher:
    """A started watcher, like the coordinator starts it (its first check has run when this returns)."""
    watcher = HealthWatcher(
        hass, entry, PREFIX, build_config(config or MOCK_OPTIONS), entities, lambda: set(covered), _da, price_units=price_units
    )
    await watcher.async_load()
    watcher.start()
    await hass.async_block_till_done(wait_background_tasks=True)
    _WATCHERS.append(watcher)
    return watcher


async def hourly_check(hass: HomeAssistant) -> None:
    """What the hourly timer does: every watcher checks again."""
    async_fire_time_changed(hass, dt_util.utcnow() + RECHECK_INTERVAL * next(_HOURS))
    await hass.async_block_till_done(wait_background_tasks=True)


def issue(hass: HomeAssistant, entry: MockConfigEntry, name: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_{name}")


def stored(hass_storage: dict[str, Any], entry: MockConfigEntry) -> dict[str, Any] | None:
    """What the watcher's store holds (the hass fixture keeps every store in hass_storage, not on the disk)."""
    saved = hass_storage.get(store_key(entry.entry_id))
    return saved["data"] if saved else None


def write_automations(config_dir: Path, text: str) -> None:
    (config_dir / "automations.yaml").write_text(text, encoding="utf-8")


def register_automation(hass: HomeAssistant, state: str | None) -> None:
    """The automation of the blueprint as Home Assistant has it: a registry entry (unique id = its id) and a state."""
    er.async_get(hass).async_get_or_create("automation", "automation", "auto1", suggested_object_id="battery_control")
    if state is not None:
        hass.states.async_set(AUTOMATION, state, {"friendly_name": "Battery control"})


def plan(checked: int, matching: int, latest: list[dict] | None = None) -> dict[str, Any]:
    """The plan check as the action sensor publishes it (plan_check.summarize_plan_check)."""
    return {
        "days": 14,
        "checked_hours": checked,
        "matching_hours": matching,
        "skipped_hours": 3,
        "match_percent": round(100 * matching / checked) if checked else None,
        "latest_mismatches": latest
        if latest is not None
        else [{"hour": "2026-11-20T14", "action": "grid_charging", "charged_kwh": 0.0, "discharged_kwh": 0.0}],
    }


def show_plan(hass: HomeAssistant, value: Any, state: str = "grid_charging") -> None:
    hass.states.async_set(ACTION, state, {"plan_check": value})


# --- the automation ids in automations.yaml (management/automation_inputs.py) -------------------------------------


def test_the_ids_of_the_blueprint_automations_of_a_sensor(config_dir: Path) -> None:
    write_automations(
        config_dir,
        "\n".join(
            [
                BLUEPRINT_AUTOMATION.rstrip(),
                OTHER_ACTION.rstrip(),
                "- alias: no id",
                "  use_blueprint: {path: battmind/battery_control.yaml, input: {battmind_action: " + ACTION + "}}",
                "- id: 42",
                "  use_blueprint: {path: battmind/battery_control.yaml, input: {battmind_action: " + ACTION + "}}",
                "- id: other_blueprint",
                "  use_blueprint: {path: other/blueprint.yaml, input: {battmind_action: " + ACTION + "}}",
                "- id: plain",
                "  trigger: []",
            ]
        ),
    )
    path = str(config_dir / "automations.yaml")
    assert blueprint_automation_ids(path, ACTION) == ["auto1", "42"]
    assert blueprint_automation_ids(path, "sensor.other_powerwall_action") == ["auto2"]
    # the inputs of the test feature are unchanged: one entry per automation with the blueprint for this sensor,
    # the one without an id included (it is only skipped for the ids)
    assert len(automations_using_blueprint(path, ACTION)) == 3


def test_no_file_and_an_empty_file_hold_no_automation(config_dir: Path) -> None:
    path = str(config_dir / "automations.yaml")
    assert blueprint_automation_ids(path, ACTION) == []
    write_automations(config_dir, "")
    assert blueprint_automation_ids(path, ACTION) == []


@pytest.mark.parametrize(
    "content",
    [
        "- id: auto1\n  use_blueprint: [\n",  # not valid YAML
        "id: auto1\n",  # a mapping, not the list automations.yaml holds
    ],
)
def test_a_file_that_can_not_be_read_is_unknown(config_dir: Path, content: str) -> None:
    write_automations(config_dir, content)
    assert blueprint_automation_ids(str(config_dir / "automations.yaml"), ACTION) is None


def test_a_file_that_is_not_utf8_is_unknown(config_dir: Path) -> None:
    (config_dir / "automations.yaml").write_bytes(b"- id: \xff\xfe\x00broken\n")
    assert blueprint_automation_ids(str(config_dir / "automations.yaml"), ACTION) is None


# --- entities_missing -----------------------------------------------------------------------------------------------


async def test_a_disabled_sensor_is_a_repair_until_it_is_enabled(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    # registry first: Home Assistant gives a new registry entry another id when a state already has that id
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "test", "consumption", suggested_object_id="house_consumption", disabled_by=DISABLED)
    set_source_states(hass)
    await start_watcher(hass, entry)

    found = issue(hass, entry, "entities_missing")
    assert found is not None and not found.is_fixable and found.severity == ir.IssueSeverity.WARNING
    assert found.translation_placeholders["prefix"] == PREFIX
    assert found.translation_placeholders["disabled"] == f"- {CONSUMPTION}"
    assert found.translation_placeholders["missing"] == "–"

    registry.async_update_entity(CONSUMPTION, disabled_by=None)
    await hourly_check(hass)
    assert issue(hass, entry, "entities_missing") is None


async def test_an_ignored_missing_sensor_stays_ignored(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    er.async_get(hass).async_get_or_create("sensor", "test", "consumption", suggested_object_id="house_consumption", disabled_by=DISABLED)
    set_source_states(hass)
    await start_watcher(hass, entry)
    ir.async_ignore_issue(hass, DOMAIN, f"{entry.entry_id}_entities_missing", True)

    await hourly_check(hass)

    found = issue(hass, entry, "entities_missing")
    assert found is not None and found.dismissed_version is not None


async def test_a_sensor_that_is_in_neither_registry_nor_states_is_missing(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    hass.states.async_remove(PRODUCTION)  # deleted or renamed: nothing in the registry or the states
    await start_watcher(hass, entry)

    assert issue(hass, entry, "entities_missing").translation_placeholders["missing"] == f"- {PRODUCTION}"
    # a state without a registry entry (YAML without a unique id) is fine
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "entities_missing") is None


async def test_a_registry_entry_without_a_state_is_not_missing(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """An integration that is not loaded keeps its registry entries and has no states: that is not a deleted sensor."""
    er.async_get(hass).async_get_or_create("sensor", "test", "production", suggested_object_id="solar_production")
    set_source_states(hass)
    hass.states.async_remove(PRODUCTION)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "entities_missing") is None


async def test_a_sensor_judged_by_the_strom_or_solcast_repair_is_left_out(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    er.async_get(hass).async_get_or_create("sensor", "stromligning", "buy", suggested_object_id="strom_buy", disabled_by=DISABLED)
    await start_watcher(hass, entry, covered={"sensor.strom_buy"}, entities=[*CONFIGURED, "sensor.strom_buy"])
    assert issue(hass, entry, "entities_missing") is None


async def test_the_missing_check_waits_until_home_assistant_has_started(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    hass.states.async_remove(PRODUCTION)
    hass.set_state(CoreState.starting)
    try:
        await start_watcher(hass, entry)
        assert issue(hass, entry, "entities_missing") is None
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done(wait_background_tasks=True)
    finally:
        hass.set_state(CoreState.running)
    assert issue(hass, entry, "entities_missing") is not None


async def test_a_registry_change_is_checked_a_minute_later(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "test", "production", suggested_object_id="solar_production")
    set_source_states(hass)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "entities_missing") is None

    registry.async_update_entity(PRODUCTION, disabled_by=DISABLED)
    await hass.async_block_till_done()
    assert issue(hass, entry, "entities_missing") is None, "Home Assistant reloads the integration first"

    async_fire_time_changed(hass, dt_util.utcnow() + datetime.timedelta(seconds=RECHECK_DELAY + 1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "entities_missing").translation_placeholders["disabled"] == f"- {PRODUCTION}"


# --- entity_units ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("unit", "flagged"),
    [("kVA", True), ("A", True), ("kW", False), ("W", False), ("kWh", False), ("Wh", False), ("", False), (None, False), ("MW", False)],
)
async def test_a_power_sensor_with_a_unit_the_code_can_not_read(hass: HomeAssistant, entry: MockConfigEntry, unit: str | None, flagged: bool) -> None:
    set_source_states(hass)
    attributes = {"device_class": "power"} | ({"unit_of_measurement": unit} if unit is not None else {})
    hass.states.async_set(BATTERY_FLOW, "0", attributes)
    await start_watcher(hass, entry)

    found = issue(hass, entry, "entity_units")
    assert (found is not None) == flagged
    if flagged:
        assert found.translation_placeholders["entities"] == f"- {BATTERY_FLOW} ({unit})"


async def test_a_battery_level_in_another_unit_than_percent(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    hass.states.async_set(BATTERY_LEVEL, "50", {"unit_of_measurement": "kWh"})
    await start_watcher(hass, entry)
    assert issue(hass, entry, "entity_units").translation_placeholders["entities"] == f"- {BATTERY_LEVEL} (kWh)"


async def test_a_sensor_without_a_state_keeps_its_verdict(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    watcher = await start_watcher(hass, entry)
    assert issue(hass, entry, "entity_units") is not None

    # unavailable is not a verdict: the repair stays, and so does the sensor's unit it was flagged with
    hass.states.async_set(PRODUCTION, "unavailable", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "entity_units") is not None

    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "entity_units") is None
    watcher.stop()


async def test_after_a_restart_an_unreadable_sensor_keeps_the_repair(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """A new watcher remembers nothing: a sensor it can't read yet must not take the repair away."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    first = await start_watcher(hass, entry)
    first.stop()
    assert issue(hass, entry, "entity_units") is not None

    hass.states.async_set(PRODUCTION, "unavailable")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "entity_units") is not None

    # with every sensor readable and fine the repair goes
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "entity_units") is None


async def test_a_fixed_sensor_removes_the_repair_while_another_sensor_can_not_be_judged(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The repair lists the sensors it was raised for: the one that is fixed leaves it, an unreadable one that was never
    flagged does not keep it."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    await start_watcher(hass, entry)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(BATTERY_FLOW, "unavailable")
    await hourly_check(hass)
    assert issue(hass, entry, "entity_units") is None


# --- blueprint_automation_lost ---------------------------------------------------------------------------------------


async def test_an_automation_that_was_on_and_is_now_off_is_a_repair(
    hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)
    assert stored(hass_storage, entry)["automation_seen"] is True, "seen in use: remembered"
    assert issue(hass, entry, "blueprint_automation_lost") is None

    hass.states.async_set(AUTOMATION, "off", {"friendly_name": "Battery control"})
    await hass.async_block_till_done(wait_background_tasks=True)  # the state change alone checks again
    found = issue(hass, entry, "blueprint_automation_lost")
    assert found is not None
    assert found.translation_placeholders["automations"] == "- Battery control"

    hass.states.async_set(AUTOMATION, "on", {"friendly_name": "Battery control"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "blueprint_automation_lost") is None


async def test_a_reloaded_automation_file_is_checked_at_once(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    """The reload of the automations (EVENT_AUTOMATION_RELOADED) checks again without waiting for the hour."""
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "blueprint_automation_lost") is None

    write_automations(config_dir, "[]\n")  # the automation was deleted from the file
    hass.bus.async_fire("automation_reloaded")
    await hass.async_block_till_done(wait_background_tasks=True)

    assert issue(hass, entry, "blueprint_automation_lost").translation_placeholders["automations"] == "–"


async def test_an_automation_that_was_never_seen_on_is_never_a_repair(
    hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "off")
    await start_watcher(hass, entry)
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost") is None
    assert "automation_seen" not in (stored(hass_storage, entry) or {})


async def test_an_automation_outside_automations_yaml_is_never_a_repair(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    """The blueprint is used by an automation in another file (or a packages file): nothing is found, nothing is raised."""
    set_source_states(hass)
    write_automations(config_dir, "")
    hass.states.async_set("automation.in_a_package", "on")
    await start_watcher(hass, entry)
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost") is None


async def test_a_deleted_automation_is_a_repair_once_it_was_seen(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)

    write_automations(config_dir, "[]\n")
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost").translation_placeholders["automations"] == "–"


async def test_an_unreadable_file_or_an_unknown_automation_leaves_the_repair(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)
    hass.states.async_set(AUTOMATION, "off", {"friendly_name": "Battery control"})
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost") is not None

    # the file can't be read: nothing is known, the repair stays
    write_automations(config_dir, "- id: auto1\n  use_blueprint: [\n")
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost") is not None

    # an automation the registry does not know yet, and the one it lists is off: unclear, the repair stays
    write_automations(config_dir, BLUEPRINT_AUTOMATION + "- id: auto3\n  use_blueprint: {path: battmind/battery_control.yaml, input: {battmind_action: " + ACTION + "}}\n")
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost") is not None


async def test_an_unavailable_automation_is_unclear(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)
    hass.states.async_set(AUTOMATION, "off", {"friendly_name": "Battery control"})
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost") is not None

    hass.states.async_set(AUTOMATION, "unavailable", {"friendly_name": "Battery control"})
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost") is not None


async def test_an_automation_disabled_in_the_registry_is_off(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)
    er.async_get(hass).async_update_entity(AUTOMATION, disabled_by=DISABLED)
    hass.states.async_remove(AUTOMATION)
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost").translation_placeholders["automations"] == f"- {AUTOMATION}"
    # named by its registry name when it has one (a disabled automation has no state, so no friendly_name)
    er.async_get(hass).async_update_entity(AUTOMATION, original_name="Battery control")
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost").translation_placeholders["automations"] == "- Battery control"
    er.async_get(hass).async_update_entity(AUTOMATION, name="Batteristyring")
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost").translation_placeholders["automations"] == "- Batteristyring"


async def test_the_seen_flag_survives_a_restart(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    first = await start_watcher(hass, entry)
    first.stop()

    hass.states.async_set(AUTOMATION, "off", {"friendly_name": "Battery control"})
    await start_watcher(hass, entry)
    assert issue(hass, entry, "blueprint_automation_lost") is not None


async def test_an_ignored_automation_repair_stays_ignored(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)
    hass.states.async_set(AUTOMATION, "off", {"friendly_name": "Battery control"})
    await hourly_check(hass)
    ir.async_ignore_issue(hass, DOMAIN, f"{entry.entry_id}_blueprint_automation_lost", True)

    await hourly_check(hass)
    await hourly_check(hass)

    found = issue(hass, entry, "blueprint_automation_lost")
    assert found is not None and found.dismissed_version is not None


# --- plan_not_followed ----------------------------------------------------------------------------------------------


async def test_a_plan_that_is_not_followed_is_a_repair(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    show_plan(hass, plan(30, 20))
    await start_watcher(hass, entry)

    found = issue(hass, entry, "plan_not_followed")
    assert found is not None and not found.is_fixable
    placeholders = found.translation_placeholders
    assert placeholders["days"] == "14"
    assert placeholders["checked"] == "30"
    assert placeholders["mismatches"] == "10"
    assert placeholders["percent"] == "67"
    assert placeholders["min_checked"] == str(PLAN_MIN_CHECKED_HOURS)
    assert placeholders["latest"] == "- 2026-11-20 14:00: Lad fra nettet – 0.00 / 0.00 kWh"


async def test_the_latest_mismatches_are_listed_with_their_measurements(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    latest = [
        {"hour": "2026-11-21T03", "action": "force_discharge", "charged_kwh": 0.5, "discharged_kwh": 0.0},
        {"hour": "2026-11-20T23", "action": "grid_charging", "charged_kwh": 0.02, "discharged_kwh": 1.25},
        {"hour": "2026-11-20T22", "action": "no_such_action", "charged_kwh": 0, "discharged_kwh": 0},
    ]
    show_plan(hass, plan(30, 20, latest))
    await start_watcher(hass, entry)
    # the actions are named in the entry's language; an action without a name is shown by its key
    assert issue(hass, entry, "plan_not_followed").translation_placeholders["latest"] == (
        "- 2026-11-21 03:00: Tvungen afladning – 0.50 / 0.00 kWh\n"
        "- 2026-11-20 23:00: Lad fra nettet – 0.02 / 1.25 kWh\n"
        "- 2026-11-20 22:00: no_such_action – 0.00 / 0.00 kWh"
    )


async def test_a_plan_that_is_followed_again_removes_the_repair(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    show_plan(hass, plan(30, 20))
    await start_watcher(hass, entry)
    assert issue(hass, entry, "plan_not_followed") is not None

    show_plan(hass, plan(30, 29))
    await hourly_check(hass)
    assert issue(hass, entry, "plan_not_followed") is None


@pytest.mark.parametrize(
    ("checked", "matching", "raised"),
    [
        (24, 18, True),  # exactly 6 mismatches and 75 %
        (24, 19, False),  # 5 mismatches: not enough
        (100, 80, False),  # 20 mismatches, but 80 % is not below the limit
        (100, 79, True),  # 79 % is below it
    ],
)
async def test_the_limits_of_the_plan_repair(hass: HomeAssistant, entry: MockConfigEntry, checked: int, matching: int, raised: bool) -> None:
    set_source_states(hass)
    show_plan(hass, plan(checked, matching))
    await start_watcher(hass, entry)
    assert (issue(hass, entry, "plan_not_followed") is not None) == raised


async def test_fewer_checked_hours_are_unclear_and_keep_the_repair(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    show_plan(hass, plan(30, 20))
    await start_watcher(hass, entry)

    show_plan(hass, plan(23, 0))  # 23 hours is not enough to judge: the repair stays
    await hourly_check(hass)
    assert issue(hass, entry, "plan_not_followed") is not None


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        None,
        {"days": 14, "checked_hours": 30, "matching_hours": 20, "match_percent": 67},  # no latest list
        {"days": 14, "checked_hours": True, "matching_hours": 20, "match_percent": 67, "latest_mismatches": []},
        {"days": 14, "checked_hours": 30, "matching_hours": 40, "match_percent": 67, "latest_mismatches": []},  # more matches than hours
        {"days": 14, "checked_hours": 30, "matching_hours": 20, "match_percent": 67, "latest_mismatches": [{"hour": 5}]},
    ],
)
async def test_a_malformed_plan_check_is_unclear(hass: HomeAssistant, entry: MockConfigEntry, value: Any) -> None:
    set_source_states(hass)
    show_plan(hass, plan(30, 20))
    await start_watcher(hass, entry)

    show_plan(hass, value)
    await hourly_check(hass)
    assert issue(hass, entry, "plan_not_followed") is not None


async def test_a_plan_check_that_is_missing_or_unavailable_is_unclear(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    show_plan(hass, plan(30, 20))
    await start_watcher(hass, entry)

    show_plan(hass, plan(30, 20), state="unavailable")
    await hourly_check(hass)
    assert issue(hass, entry, "plan_not_followed") is not None

    hass.states.async_set(ACTION, "grid_charging")  # the attribute is gone
    await hourly_check(hass)
    assert issue(hass, entry, "plan_not_followed") is not None


async def test_the_plan_repair_is_checked_when_the_plan_check_is_published(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "plan_not_followed") is None

    show_plan(hass, plan(30, 20))  # a new plan check, without the hourly timer
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "plan_not_followed") is not None


async def test_an_ignored_plan_repair_stays_ignored(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    show_plan(hass, plan(30, 20))
    await start_watcher(hass, entry)
    ir.async_ignore_issue(hass, DOMAIN, f"{entry.entry_id}_plan_not_followed", True)

    show_plan(hass, plan(31, 21))
    await hourly_check(hass)

    found = issue(hass, entry, "plan_not_followed")
    assert found is not None and found.dismissed_version is not None


# --- lifecycle: stop, dismissal, removal ----------------------------------------------------------------------------


async def test_stopping_the_watcher_keeps_its_repairs(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    watcher = await start_watcher(hass, entry)
    watcher.stop()
    assert issue(hass, entry, "entity_units") is not None

    # nothing is judged after the stop: a repair that would be deleted stays
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "entity_units") is not None


async def test_a_dependency_check_with_nothing_to_watch_keeps_the_health_repairs(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Regression: the Strømligning and Solcast watcher with nothing to watch must not delete the health repairs, or the
    user's "ignore" would be lost at every start."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    await start_watcher(hass, entry)
    ir.async_ignore_issue(hass, DOMAIN, f"{entry.entry_id}_entity_units", True)

    DependencyWatcher(hass, entry, PREFIX, [], "", solcast_endings_for_days(7)).start()
    await hass.async_block_till_done()

    found = issue(hass, entry, "entity_units")
    assert found is not None and found.dismissed_version is not None


async def test_an_ignored_repair_stays_ignored_over_a_reload(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, mock_entry, "entity_units") is not None
    ir.async_ignore_issue(hass, DOMAIN, f"{mock_entry.entry_id}_entity_units", True)

    assert await hass.config_entries.async_reload(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    found = issue(hass, mock_entry, "entity_units")
    assert found is not None and found.dismissed_version is not None
    await async_unload_and_join(hass, mock_entry)


async def test_removing_the_entry_removes_its_repairs_and_its_store(
    hass: HomeAssistant, mock_entry: MockConfigEntry, config_dir: Path, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, mock_entry, "entity_units") is not None
    assert stored(hass_storage, mock_entry)["automation_seen"] is True

    await async_unload_and_join(hass, mock_entry)
    assert issue(hass, mock_entry, "entity_units") is not None, "an unload keeps the repair"
    assert stored(hass_storage, mock_entry) is not None, "an unload keeps the store"

    await hass.config_entries.async_remove(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, mock_entry, "entity_units") is None
    assert issue(hass, mock_entry, "blueprint_automation_lost") is None
    assert stored(hass_storage, mock_entry) is None


async def test_the_store_and_the_repairs_are_removed_with_the_entry(
    hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    watcher = await start_watcher(hass, entry)
    watcher.stop()
    assert stored(hass_storage, entry) is not None

    remove_issues(hass, entry.entry_id)
    await async_remove_health_store(hass, entry.entry_id)
    assert issue(hass, entry, "entity_units") is None
    assert stored(hass_storage, entry) is None


@pytest.mark.parametrize("error", [OSError("permission denied"), HomeAssistantError("unreadable")])
async def test_an_unreadable_store_starts_with_the_safe_defaults(
    hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path, error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """A store that can't be read must not stop the entry: no automation seen (no blueprint repair), the checks still run."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "off")
    with patch("custom_components.battmind.management.health_checks.Store.async_load", AsyncMock(side_effect=error)):
        watcher = await start_watcher(hass, entry)
    assert "Could not read the BattMind health check store" in caplog.text
    assert watcher._automation_seen is False
    assert issue(hass, entry, "blueprint_automation_lost") is None, "an automation never seen on is no repair"
    assert issue(hass, entry, "entity_units") is not None, "the other checks still run"


async def test_the_dependency_removal_leaves_the_health_repairs(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """remove_dependency_issues is for the Strømligning and Solcast repairs only; remove_issues (an entry removed) takes all."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    await start_watcher(hass, entry)

    remove_dependency_issues(hass, entry.entry_id)
    assert issue(hass, entry, "entity_units") is not None

    remove_issues(hass, entry.entry_id)
    assert issue(hass, entry, "entity_units") is None


async def test_a_health_store_that_can_not_be_deleted_does_not_stop_the_removal(
    hass: HomeAssistant, mock_entry: MockConfigEntry, config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    failing = MagicMock()
    failing.return_value.async_remove = AsyncMock(side_effect=OSError("access denied"))
    with patch("custom_components.battmind.management.health_checks.Store", failing):
        await hass.config_entries.async_remove(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert issue(hass, mock_entry, "entity_units") is None, "the repairs are removed before the store"
    assert hass.config_entries.async_get_entry(mock_entry.entry_id) is None, "the removal went on"
    assert "Could not delete the health check state" in caplog.text


# --- the unit repair keeps the sensors it lists -------------------------------------------------------------------------


async def test_the_flagged_sensors_are_kept_in_the_store(hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, Any]) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    await start_watcher(hass, entry)
    assert stored(hass_storage, entry)["units_flagged"] == {PRODUCTION: "kVA"}


async def test_a_fixed_sensor_leaves_the_others_in_the_repair(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    hass.states.async_set(BATTERY_LEVEL, "50", {"unit_of_measurement": "kWh"})
    await start_watcher(hass, entry)
    assert issue(hass, entry, "entity_units").translation_placeholders["entities"].split("\n") == sorted(
        [f"- {PRODUCTION} (kVA)", f"- {BATTERY_LEVEL} (kWh)"]
    )

    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "entity_units").translation_placeholders["entities"] == f"- {BATTERY_LEVEL} (kWh)"


async def test_without_a_stored_list_an_unreadable_sensor_keeps_an_existing_repair(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The store is gone: nothing says which sensors were flagged, so the repair stays until every sensor can be read."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{entry.entry_id}_entity_units",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="entity_units",
        translation_placeholders={"prefix": PREFIX, "entities": f"- {PRODUCTION} (kVA)"},
    )
    set_source_states(hass)
    hass.states.async_set(BATTERY_FLOW, "unavailable")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "entity_units") is not None

    hass.states.async_set(BATTERY_FLOW, "0", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "entity_units") is None


async def test_a_unit_that_is_not_text_is_flagged_and_the_other_checks_still_run(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    set_source_states(hass)
    hass.states.async_set(BATTERY_FLOW, "0", {"unit_of_measurement": 5})
    show_plan(hass, plan(30, 20))
    await start_watcher(hass, entry)
    assert issue(hass, entry, "entity_units").translation_placeholders["entities"] == f"- {BATTERY_FLOW} (5)"
    assert issue(hass, entry, "plan_not_followed") is not None


async def test_a_failing_check_is_logged_and_the_others_still_run(hass: HomeAssistant, entry: MockConfigEntry, caplog: pytest.LogCaptureFixture) -> None:
    set_source_states(hass)
    show_plan(hass, plan(30, 20))
    with patch.object(HealthWatcher, "_check_units", AsyncMock(side_effect=RuntimeError("broken unit"))):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "plan_not_followed") is not None
    assert "broken unit" in caplog.text


# --- the automation state listener and renames ---------------------------------------------------------------------------


async def test_the_state_listener_follows_the_automations_found_last_time(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)

    # the blueprint automation is replaced by another one, which is on
    er.async_get(hass).async_get_or_create("automation", "automation", "auto3", suggested_object_id="third")
    hass.states.async_set("automation.third", "on", {"friendly_name": "Third"})
    write_automations(config_dir, THIRD_AUTOMATION)
    await hourly_check(hass)
    assert issue(hass, entry, "blueprint_automation_lost") is None

    # the new automation is listened to: its change is checked at once, without the hourly timer
    hass.states.async_set("automation.third", "off", {"friendly_name": "Third"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "blueprint_automation_lost").translation_placeholders["automations"] == "- Third"


async def test_a_renamed_automation_is_checked_a_minute_later_under_its_new_id(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    set_source_states(hass)
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    await start_watcher(hass, entry)

    # the automation is renamed (the registry change is announced under the old and the new id), and switched off under
    # its new id; nothing else announces that state, so only the check after the registry change finds it
    er.async_get(hass).async_update_entity(AUTOMATION, new_entity_id="automation.renamed")
    await hass.async_block_till_done()
    hass.states.async_set("automation.renamed", "off", {"friendly_name": "Battery control"})
    await hass.async_block_till_done()
    assert issue(hass, entry, "blueprint_automation_lost") is None, "the registry change is checked a minute later"

    async_fire_time_changed(hass, dt_util.utcnow() + datetime.timedelta(seconds=RECHECK_DELAY + 1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "blueprint_automation_lost").translation_placeholders["automations"] == "- Battery control"


def test_the_automation_reloaded_event_is_the_one_home_assistant_fires() -> None:
    from homeassistant.components.automation import EVENT_AUTOMATION_RELOADED as HA_EVENT

    assert EVENT_AUTOMATION_RELOADED == HA_EVENT


# --- the texts: every language, and the placeholders the watcher passes -------------------------------------------------


def test_the_repair_texts_are_in_every_language_with_the_same_placeholders() -> None:
    component = Path(__file__).parents[1] / "custom_components" / "battmind"
    sources = [component / "strings.json", *(component / "translations").glob("*.json")]
    assert len(sources) == 6
    for path in sources:
        issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
        for key, placeholders in EXPECTED_PLACEHOLDERS.items():
            entry = issues[key]
            assert entry["title"] and entry["description"], (path.name, key)
            assert set(re.findall(r"\{(\w+)\}", entry["title"] + entry["description"])) == placeholders, (path.name, key)


def test_every_language_names_the_actions_of_the_plan_check() -> None:
    texts = Path(__file__).parents[1] / "custom_components" / "battmind" / "texts"
    files = list(texts.glob("*.json"))
    assert len(files) == 5
    for path in files:
        actions = json.loads(path.read_text(encoding="utf-8"))["ui"]["automation_test"]["action"]
        assert set(CHECKED_ACTIONS) <= set(actions), path.name


async def test_every_repair_passes_the_placeholders_its_texts_use(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path, clock) -> None:
    # registry first: Home Assistant gives a new registry entry another id when a state already has that id
    er.async_get(hass).async_get_or_create("sensor", "test", "consumption", suggested_object_id="house_consumption", disabled_by=DISABLED)
    set_source_states(hass)
    set_consumption(hass, "412.7")  # the same value for FROZEN_AFTER: the consumption_frozen repair is raised too
    age_consumption(hass, clock, FROZEN_AFTER)
    hass.states.async_set(BATTERY_FLOW, "0", {"unit_of_measurement": "kVA"})
    hass.states.async_set(PRODUCTION, "1.5")
    hass.states.async_set(BATTERY_LEVEL, "0.6", {"unit_of_measurement": "%"})
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    show_plan(hass, plan(30, 20))
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY, BATTERY_LEVEL: SHARE_HISTORY})):
        await start_watcher(hass, entry, price_units=lambda: ([(PRICE, "øre/kWh")], True))
        hass.states.async_set(AUTOMATION, "off", {"friendly_name": "Battery control"})
        await hass.async_block_till_done(wait_background_tasks=True)

    for key, placeholders in EXPECTED_PLACEHOLDERS.items():
        found = issue(hass, entry, key)
        assert found is not None, key
        assert set(found.translation_placeholders) == placeholders, key


# --- power_without_unit, battery_level_fraction and price_unit_scale ----------------------------------------------------

HEALTH = "custom_components.battmind.management.health_checks"
# the numbers of the last days as the recorder gives them: power in kW-looking values, a battery level as a share
KW_HISTORY = [0.5, 1.2, 3.4, 0.8, 2.2, 4.2, 1.1, 0.3, 2.5, 3.3]
WHOLE_HISTORY = [10, 20, 30, 40, 15, 25, 35, 45, 12, 18]  # whole numbers under 50: never judged, neither kW nor W
SHARE_HISTORY = [0.85, 0.9, 0.5, 0.97, 0.12, 0.33, 0.6, 0.7, 0.8, 0.99]


def recorded(history: dict[str, list[float]] | None) -> AsyncMock:
    """async_recorded_numbers, patched: the numbers of each entity asked for, or None (no recorder)."""

    async def numbers(_hass: HomeAssistant, entity_ids: list[str], days: int = POWER_SCALE_DAYS) -> dict[str, list[float]] | None:
        if history is None:
            return None
        return {entity_id: history.get(entity_id, []) for entity_id in entity_ids}

    return AsyncMock(side_effect=numbers)


@pytest.fixture
def clock() -> Iterator[list[datetime.datetime]]:
    """The clock of the history reads (VALUE_RECHECK): the test moves it forward."""
    now = [dt_util.utcnow()]
    with patch(f"{HEALTH}._utcnow", side_effect=lambda: now[0]):
        yield now


async def test_a_power_without_unit_that_looks_like_kw_is_a_repair(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    found = issue(hass, entry, "power_without_unit")
    assert found is not None
    assert found.translation_placeholders["entities"] == f"- {PRODUCTION} (≤ 4.2)"
    assert found.translation_placeholders["max"] == "50"
    assert found.translation_placeholders["days"] == "3"


async def test_a_power_sensor_with_a_unit_is_judged_without_history(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5", {"unit_of_measurement": "kW"})
    numbers = recorded({PRODUCTION: KW_HISTORY})
    with patch(f"{HEALTH}.async_recorded_numbers", numbers):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is None
    assert numbers.await_count == 0


async def test_a_power_sensor_that_gets_a_unit_clears_the_repair(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None

    hass.states.async_set(PRODUCTION, "1.5", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is None


async def test_a_power_above_fifty_in_watts_clears_the_repair(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None

    hass.states.async_set(PRODUCTION, "1200")
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is None


async def test_unclear_values_keep_the_power_repair(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None

    # the history can't be read now (no recorder): nothing is decided, the repair stays
    clock[0] += VALUE_RECHECK
    with patch(f"{HEALTH}.async_recorded_numbers", recorded(None)):
        await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is not None

    # unavailable is not a verdict either
    hass.states.async_set(PRODUCTION, "unavailable")
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is not None


async def test_without_a_recorder_nothing_is_raised(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is None


async def test_the_history_is_read_at_most_once_per_recheck(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    numbers = recorded({PRODUCTION: KW_HISTORY})
    with patch(f"{HEALTH}.async_recorded_numbers", numbers):
        await start_watcher(hass, entry)
        assert numbers.await_count == 1

        clock[0] += datetime.timedelta(hours=1)
        await hourly_check(hass)
        clock[0] += datetime.timedelta(hours=4)
        await hourly_check(hass)
        assert numbers.await_count == 1, "the verdict is kept for VALUE_RECHECK"

        clock[0] += datetime.timedelta(hours=1)
        await hourly_check(hass)
        assert numbers.await_count == 2
    assert issue(hass, entry, "power_without_unit") is not None


async def test_all_flagged_sensors_are_read_in_one_call(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    hass.states.async_set(BATTERY_FLOW, "2.0")
    numbers = recorded({PRODUCTION: KW_HISTORY, BATTERY_FLOW: KW_HISTORY})
    with patch(f"{HEALTH}.async_recorded_numbers", numbers):
        await start_watcher(hass, entry)
    assert numbers.await_count == 1
    assert sorted(numbers.await_args.args[1]) == sorted([PRODUCTION, BATTERY_FLOW])
    assert issue(hass, entry, "power_without_unit").translation_placeholders["entities"] == (
        f"- {BATTERY_FLOW} (≤ 4.2)\n- {PRODUCTION} (≤ 4.2)"
    )


async def test_a_flagged_power_sensor_is_kept_in_the_store(hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, Any]) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        first = await start_watcher(hass, entry)
    assert stored(hass_storage, entry)["values_flagged"] == {PRODUCTION: "power_without_unit"}
    first.stop()

    # a new watcher can't read the sensor yet: the repair stays
    hass.states.async_set(PRODUCTION, "unavailable")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None
    hass.states.async_set(PRODUCTION, "1.5", {"unit_of_measurement": "W"})
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is None


async def test_a_battery_level_that_is_a_share_is_a_repair_until_it_is_a_percentage(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(BATTERY_LEVEL, "0.85", {"unit_of_measurement": "%"})
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({BATTERY_LEVEL: SHARE_HISTORY})):
        await start_watcher(hass, entry)
    found = issue(hass, entry, "battery_level_fraction")
    assert found is not None
    assert found.translation_placeholders["entities"] == f"- {BATTERY_LEVEL} (≤ 0.99)"
    assert found.translation_placeholders["days"] == "3"

    hass.states.async_set(BATTERY_LEVEL, "85", {"unit_of_measurement": "%"})
    await hourly_check(hass)
    assert issue(hass, entry, "battery_level_fraction") is None


@pytest.mark.parametrize(("attributes", "flagged"), [({"unit_of_measurement": "%"}, True), ({}, True), ({"unit_of_measurement": "kWh"}, False)])
async def test_the_unit_of_the_battery_level_decides_whether_it_is_judged(
    hass: HomeAssistant, entry: MockConfigEntry, clock, attributes: dict[str, str], flagged: bool
) -> None:
    set_source_states(hass)
    hass.states.async_set(BATTERY_LEVEL, "0.85", attributes)
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({BATTERY_LEVEL: SHARE_HISTORY})):
        await start_watcher(hass, entry)
    assert (issue(hass, entry, "battery_level_fraction") is not None) == flagged


async def test_a_price_in_a_subunit_is_a_repair_until_it_is_in_kr(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    units: dict[str, Any] = {"result": ([(PRICE, "øre/kWh")], True)}
    await start_watcher(hass, entry, price_units=lambda: units["result"])
    found = issue(hass, entry, "price_unit_scale")
    assert found is not None
    assert found.translation_placeholders["entities"] == f"- {PRICE} (øre/kWh)"

    units["result"] = ([], True)
    await hourly_check(hass)
    assert issue(hass, entry, "price_unit_scale") is None


async def test_a_price_provider_that_can_not_judge_keeps_the_repair(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    units: dict[str, Any] = {"result": ([(PRICE, "ct/kWh")], True)}
    await start_watcher(hass, entry, price_units=lambda: units["result"])
    assert issue(hass, entry, "price_unit_scale") is not None

    # no price provider yet, or a price sensor with no state: nothing is judged, so the repair stays
    units["result"] = None
    await hourly_check(hass)
    assert issue(hass, entry, "price_unit_scale") is not None
    units["result"] = ([], False)
    await hourly_check(hass)
    assert issue(hass, entry, "price_unit_scale") is not None


async def test_removing_the_entry_removes_the_value_and_price_repairs(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    for name in ("power_without_unit", "battery_level_fraction", "price_unit_scale", "consumption_frozen"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{entry.entry_id}_{name}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=name,
            translation_placeholders={"prefix": PREFIX},
        )
    remove_issues(hass, entry.entry_id)
    for name in ("power_without_unit", "battery_level_fraction", "price_unit_scale", "consumption_frozen"):
        assert issue(hass, entry, name) is None, name


# --- the value repairs: the stored list, the flapping, a failed read, the language of the numbers ------------------------


async def test_a_fixed_value_sensor_is_deleted_while_another_is_unclear(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None

    hass.states.async_set(PRODUCTION, "1200")  # fixed: positively fine
    hass.states.async_set(BATTERY_FLOW, "unavailable")  # never flagged, and can't be judged now
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is None


async def test_a_fresh_start_without_a_store_knows_its_list_so_a_fixed_sensor_removes_the_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    """No store and no repair in the registry: nothing could have been forgotten, so the list starts known. A sensor that is
    never judged (whole numbers under 50 W) must not keep the repair of another sensor once that one is fixed."""
    set_source_states(hass)
    hass.states.async_set(CONSUMPTION, "20")  # no unit, whole numbers under 50: never judged
    hass.states.async_set(PRODUCTION, "1.5")  # no unit, kW-like: flagged
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({CONSUMPTION: WHOLE_HISTORY, PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None
    assert "power_without_unit" in stored(hass_storage, entry)["values_known"]

    hass.states.async_set(PRODUCTION, "1.5", {"unit_of_measurement": "kW"})  # the sensor gets a unit: fine
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({CONSUMPTION: WHOLE_HISTORY})):
        await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is None


async def test_a_fixed_value_sensor_is_deleted_after_a_restart_while_another_is_unclear(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        first = await start_watcher(hass, entry)
    assert stored(hass_storage, entry)["values_flagged"] == {PRODUCTION: "power_without_unit"}
    first.stop()

    # the list is read from the store again: the fixed sensor is judged, the unclear one is not flagged
    hass.states.async_set(PRODUCTION, "1200")
    hass.states.async_set(BATTERY_FLOW, "unavailable")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is None


async def test_without_a_stored_list_an_unclear_sensor_keeps_the_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        first = await start_watcher(hass, entry)
    first.stop()

    # the store is lost: without a known list an unclear sensor keeps the repair, as before
    hass_storage.pop(store_key(entry.entry_id), None)
    hass.states.async_set(PRODUCTION, "1200")
    hass.states.async_set(BATTERY_FLOW, "unavailable")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None


async def test_the_repair_text_follows_the_flagged_list_and_keeps_the_ignore(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    hass.states.async_set(BATTERY_FLOW, "2.0")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY, BATTERY_FLOW: KW_HISTORY})):
        await start_watcher(hass, entry)
    found = issue(hass, entry, "power_without_unit")
    assert found.translation_placeholders["entities"].count("\n") == 1

    ir.async_ignore_issue(hass, DOMAIN, f"{entry.entry_id}_power_without_unit", True)
    hass.states.async_set(BATTERY_FLOW, "1200")
    await hourly_check(hass)
    found = issue(hass, entry, "power_without_unit")
    assert found.translation_placeholders["entities"] == f"- {PRODUCTION} (≤ 4.2)"
    assert found.dismissed_version is not None, "the Ignore of the user stays"


async def test_a_fine_state_clears_the_cached_verdict_so_an_old_true_is_not_raised_again(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None

    hass.states.async_set(PRODUCTION, "1200")
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is None

    # an hour later it reads like kW again, but the False of the 1200 is kept until VALUE_RECHECK: the history is not read again
    clock[0] += datetime.timedelta(hours=1)
    hass.states.async_set(PRODUCTION, "1.5")
    history = recorded({PRODUCTION: [*KW_HISTORY, 1200.0]})
    with patch(f"{HEALTH}.async_recorded_numbers", history):
        await hourly_check(hass)
    history.assert_not_awaited()
    assert issue(hass, entry, "power_without_unit") is None


async def test_a_lost_store_keeps_a_repair_whose_sensor_was_never_judged(hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, Any], clock) -> None:
    """The known list is per repair: a battery level judged in a check says nothing about the power sensor, which was never judged."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        first = await start_watcher(hass, entry)
    first.stop()
    assert issue(hass, entry, "power_without_unit") is not None

    # the store is lost; the power sensor is unavailable, and the battery level is a share
    hass_storage.pop(store_key(entry.entry_id), None)
    hass.states.async_set(PRODUCTION, "unavailable")
    hass.states.async_set(BATTERY_LEVEL, "0.85", {"unit_of_measurement": "%"})
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({BATTERY_LEVEL: SHARE_HISTORY})):
        await start_watcher(hass, entry)
        assert issue(hass, entry, "battery_level_fraction") is not None
        assert stored(hass_storage, entry)["values_known"] == ["battery_level_fraction"]
        await hourly_check(hass)

    assert issue(hass, entry, "power_without_unit") is not None, "the power sensor was never judged, so its repair stays"


async def test_a_store_without_a_known_list_is_read_as_unknown(hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, Any], clock) -> None:
    """A store with values_flagged and no values_known is not read as complete: the flagged sensor is fine now, but the
    unclear sensor is not judged, so the repair stays until every sensor of it is judged."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        first = await start_watcher(hass, entry)
    first.stop()
    assert issue(hass, entry, "power_without_unit") is not None
    assert "power_without_unit" in stored(hass_storage, entry)["values_known"]

    del hass_storage[store_key(entry.entry_id)]["data"]["values_known"]
    hass.states.async_set(PRODUCTION, "1200")
    hass.states.async_set(BATTERY_FLOW, "unavailable")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None
    assert "power_without_unit" not in stored(hass_storage, entry)["values_known"]


async def test_a_malformed_entry_in_the_flagged_list_makes_the_known_lists_unknown(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, Any], clock
) -> None:
    """An entry that is not a value repair means the store was not read intact: no list is known, so the unclear sensor keeps
    the repair."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        first = await start_watcher(hass, entry)
    first.stop()
    assert issue(hass, entry, "power_without_unit") is not None

    hass_storage[store_key(entry.entry_id)]["data"]["values_flagged"]["sensor.old_sensor"] = "some_other_repair"
    hass.states.async_set(PRODUCTION, "1200")
    hass.states.async_set(BATTERY_FLOW, "unavailable")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None
    assert "power_without_unit" not in stored(hass_storage, entry)["values_known"]


async def test_a_lost_store_keeps_a_repair_until_every_sensor_of_it_is_judged(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, Any], clock
) -> None:
    """Two power sensors in one repair and the store lost: the flagged one is fixed, the other is unavailable, so the repair
    stays; once the other one is judged fine, the repair goes."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    hass.states.async_set(BATTERY_FLOW, "2.0")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY, BATTERY_FLOW: KW_HISTORY})):
        first = await start_watcher(hass, entry)
    first.stop()
    assert issue(hass, entry, "power_without_unit") is not None

    hass_storage.pop(store_key(entry.entry_id), None)
    hass.states.async_set(PRODUCTION, "1200")
    hass.states.async_set(BATTERY_FLOW, "unavailable")
    await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit") is not None
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is not None, "the other sensor is not judged yet"

    hass.states.async_set(BATTERY_FLOW, "1200")
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit") is None


async def test_a_failed_history_read_is_logged_once_and_not_retried_before_the_recheck(
    hass: HomeAssistant, entry: MockConfigEntry, clock, caplog: pytest.LogCaptureFixture
) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)

    numbers = AsyncMock(side_effect=RuntimeError("the recorder is busy"))
    with patch(f"{HEALTH}.async_recorded_numbers", numbers), caplog.at_level(logging.DEBUG, logger=HEALTH):
        clock[0] += VALUE_RECHECK
        await hourly_check(hass)
        assert numbers.await_count == 1
        clock[0] += datetime.timedelta(hours=1)
        await hourly_check(hass)
        assert numbers.await_count == 1, "the failed read is not repeated before VALUE_RECHECK"
        clock[0] += VALUE_RECHECK
        await hourly_check(hass)
        assert numbers.await_count == 2

    assert issue(hass, entry, "power_without_unit") is not None, "the verdicts are kept"
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING and "could not read the history" in record.getMessage()]
    assert len(warnings) == 1
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


async def test_the_numbers_of_the_repair_text_follow_the_language_of_home_assistant(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    hass.config.language = "da"
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    found = issue(hass, entry, "power_without_unit")
    assert found.translation_placeholders["entities"] == f"- {PRODUCTION} (≤ 4,2)"
    assert found.translation_placeholders["max"] == "50"


async def test_the_highest_value_seen_is_the_current_value_when_it_is_above_the_history(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        await start_watcher(hass, entry)
    hass.states.async_set(PRODUCTION, "4.9")  # no new history read: the clock is not moved
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit").translation_placeholders["entities"] == f"- {PRODUCTION} (≤ 4.9)"
    hass.states.async_set(PRODUCTION, "2.0")  # lower again, still no new history read: the highest value seen stays
    await hourly_check(hass)
    assert issue(hass, entry, "power_without_unit").translation_placeholders["entities"] == f"- {PRODUCTION} (≤ 4.9)"


async def test_the_highest_value_seen_is_rounded_up_so_it_is_never_below_the_value(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: [*KW_HISTORY, 4.234]})):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit").translation_placeholders["entities"] == f"- {PRODUCTION} (≤ 4.24)"


async def test_a_flagged_sensor_without_a_known_highest_value_shows_no_number(
    hass: HomeAssistant, entry: MockConfigEntry, hass_storage: dict[str, Any], clock
) -> None:
    """After a restart with the history not readable, the highest value is not known: the current value alone would be
    below it, so no number is shown."""
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", recorded({PRODUCTION: KW_HISTORY})):
        first = await start_watcher(hass, entry)
    first.stop()

    hass.states.async_set(PRODUCTION, "1.5")
    with patch(f"{HEALTH}.async_recorded_numbers", AsyncMock(side_effect=RuntimeError("the recorder is busy"))):
        await start_watcher(hass, entry)
    assert issue(hass, entry, "power_without_unit").translation_placeholders["entities"] == f"- {PRODUCTION}"


def test_the_highest_value_seen_is_said_without_sign_in_every_language() -> None:
    component = Path(__file__).parents[1] / "custom_components" / "battmind"
    phrases = {"strings": "without sign", "da": "uden fortegn", "de": "ohne Vorzeichen", "en": "without sign", "pl": "bez znaku", "uk": "без знака"}
    for path in [component / "strings.json", *(component / "translations").glob("*.json")]:
        issues = json.loads(path.read_text(encoding="utf-8"))["issues"]
        for key in ("power_without_unit", "battery_level_fraction"):
            text = issues[key]["description"]
            language = "strings" if path.name == "strings.json" else path.stem
            assert phrases[language] in text, (path.name, key)


async def test_the_price_units_of_the_real_price_provider_raise_and_clear_the_repair(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    coordinator = loaded_entry.runtime_data
    assert coordinator._price_unit_problems() == ([], True)

    state = hass.states.get(PRICE)
    hass.states.async_set(PRICE, state.state, {**state.attributes, "unit_of_measurement": "øre/kWh"})
    assert coordinator._price_unit_problems() == ([(PRICE, "øre/kWh")], True)
    await hourly_check(hass)
    found = issue(hass, loaded_entry, "price_unit_scale")
    assert found is not None
    assert found.translation_placeholders["entities"] == f"- {PRICE} (øre/kWh)"

    state = hass.states.get(PRICE)
    hass.states.async_set(PRICE, state.state, {**state.attributes, "unit_of_measurement": "kr/kWh"})
    await hourly_check(hass)
    assert issue(hass, loaded_entry, "price_unit_scale") is None


async def test_without_a_price_provider_the_lifecycle_judges_no_price(hass: HomeAssistant, loaded_entry: MockConfigEntry, monkeypatch) -> None:
    coordinator = loaded_entry.runtime_data
    monkeypatch.setattr(coordinator, "engine", None)
    assert coordinator._price_unit_problems() is None


# --- consumption_frozen: the house consumption sensor that shows the same value for FROZEN_AFTER -----------------------


def set_consumption(hass: HomeAssistant, value: str) -> None:
    """The house consumption sensor in a state. A state of the same value keeps its last_changed, so it is removed first (as a
    sensor that is set up again is)."""
    hass.states.async_remove(CONSUMPTION)
    hass.states.async_set(CONSUMPTION, value, {"unit_of_measurement": "W"})


def age_consumption(hass: HomeAssistant, clock: list[datetime.datetime], age: datetime.timedelta) -> None:
    """The clock of the watcher, the given time after the last change of the consumption sensor."""
    clock[0] = hass.states.get(CONSUMPTION).last_changed + age


def saved_frozen(hass_storage: dict[str, Any], entry: MockConfigEntry) -> Any:
    return (stored(hass_storage, entry) or {}).get("consumption_frozen")


def hold_health_writes(monkeypatch: pytest.MonkeyPatch, store: Store) -> tuple[asyncio.Event, asyncio.Event]:
    """The writes of one health store wait at a gate (on this instance only, so the class is left as the test storage has it).
    The write runs as a task of its own, as an executor job does: `entered` is set when a write starts, the write goes on once
    `gate` is set, and cancelling the save that started it does not stop it."""
    gate, entered = asyncio.Event(), asyncio.Event()
    written = store._async_write_data

    async def gated(data: dict) -> None:
        await gate.wait()
        await written(data)

    async def held(data: dict) -> None:
        write = asyncio.ensure_future(gated(data))
        entered.set()
        await asyncio.shield(write)

    monkeypatch.setattr(store, "_async_write_data", held)
    return gate, entered


async def remove_while_held(hass: HomeAssistant, entry: MockConfigEntry, gate: asyncio.Event) -> None:
    """Start the removal of the entry's health store while a write is held at the gate, check that it waits, then let the
    write go on and wait for the removal. The removal has run up to its first wait when this test continues."""
    under_way = asyncio.Event()

    async def removing() -> None:
        under_way.set()
        await async_remove_health_store(hass, entry.entry_id)

    removal = asyncio.ensure_future(removing())
    try:
        await under_way.wait()
        assert not removal.done(), "the store is not deleted while a save of it is still being written"
    finally:
        gate.set()
    await removal


async def test_a_consumption_that_is_the_same_for_six_hours_is_a_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)

    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and not found.is_fixable and found.severity == ir.IssueSeverity.WARNING
    assert found.translation_placeholders["entity"] == CONSUMPTION
    assert found.translation_placeholders["value"] == "413"  # number_text: three significant digits
    last_changed = hass.states.get(CONSUMPTION).last_changed
    assert found.translation_placeholders["since"] == dt_util.as_local(last_changed).strftime("%Y-%m-%d %H:%M")
    assert saved_frozen(hass_storage, entry) == {"entity": CONSUMPTION, "value": "412.7", "since": last_changed.isoformat()}


async def test_a_consumption_that_is_the_same_for_five_hours_is_no_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, datetime.timedelta(hours=5))
    await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


@pytest.mark.parametrize("state", ["unavailable", "unknown", "not a number"])
async def test_a_consumption_without_a_number_is_no_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], state: str
) -> None:
    set_source_states(hass)
    set_consumption(hass, state)
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


async def test_an_unavailable_consumption_keeps_the_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None

    set_consumption(hass, "unavailable")
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is not None
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"


async def test_the_frozen_value_keeps_the_repair_after_a_restart(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    since = issue(hass, entry, "consumption_frozen").translation_placeholders["since"]
    first.stop()

    # Home Assistant starts again: the sensor is set up with the same value, so its last_changed is new
    set_consumption(hass, "412.7")
    clock[0] = hass.states.get(CONSUMPTION).last_changed
    await start_watcher(hass, entry)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None
    assert found.translation_placeholders["since"] == since, "the earlier time of the saved value is shown"
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"


async def test_a_changed_value_deletes_the_repair(hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None

    set_consumption(hass, "412.70")  # the same number: the repair stays
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is not None

    clock[0] += CONSUMPTION_GRACE  # the grace is over: the next check judges the value
    set_consumption(hass, "415")
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


async def test_a_consumption_that_is_no_longer_configured_removes_the_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None
    first.stop()

    await start_watcher(hass, entry, config={**MOCK_OPTIONS, "power_consumption_entity_id": ""})
    assert issue(hass, entry, "consumption_frozen") is not None, "the grace keeps the repair"
    assert saved_frozen(hass_storage, entry) is not None
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)  # the configuration answer comes at the first check after the grace
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


async def test_a_replaced_consumption_sensor_removes_the_repair_of_the_old_one(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None
    first.stop()

    # another sensor is chosen: it is not frozen, and the repair was about the sensor that is no longer used
    hass.states.async_set("sensor.other_consumption", "100", {"unit_of_measurement": "W"})
    await start_watcher(hass, entry, config={**MOCK_OPTIONS, "power_consumption_entity_id": "sensor.other_consumption"})
    assert issue(hass, entry, "consumption_frozen") is not None, "the grace keeps the repair of the old sensor"
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


async def test_an_ignored_consumption_repair_stays_ignored(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    ir.async_ignore_issue(hass, DOMAIN, f"{entry.entry_id}_consumption_frozen", True)

    await hourly_check(hass)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.dismissed_version is not None


async def test_the_frozen_check_waits_until_home_assistant_has_started(hass: HomeAssistant, entry: MockConfigEntry, clock) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    hass.set_state(CoreState.starting)
    try:
        await start_watcher(hass, entry)
        assert issue(hass, entry, "consumption_frozen") is None
        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done(wait_background_tasks=True)
    finally:
        hass.set_state(CoreState.running)
    assert issue(hass, entry, "consumption_frozen") is not None


@pytest.mark.parametrize("saved", [None, "412.7", {"entity": CONSUMPTION, "value": "412.7"}], ids=["missing", "not_a_dict", "no_time"])
async def test_a_lost_or_malformed_saved_value_keeps_the_repair_until_the_value_changes(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], saved: Any
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None
    first.stop()

    data = hass_storage[store_key(entry.entry_id)]["data"]
    if saved is None:
        data.pop("consumption_frozen")
    else:
        data["consumption_frozen"] = saved
    # the restart: during the grace the repair stays, and no baseline is taken from the lost or malformed store
    set_consumption(hass, "412.7")
    clock[0] = hass.states.get(CONSUMPTION).last_changed
    await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None

    # after the grace the first check takes the value seen as the baseline: the repair stays, and a later change removes it
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is not None
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"

    hass.states.async_set(CONSUMPTION, "420", {"unit_of_measurement": "W"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "consumption_frozen") is None


@pytest.mark.parametrize(
    "saved",
    [
        {"entity": CONSUMPTION, "value": "412.7", "since": "2026-11-20T10:00:00"},  # no time zone
        {"entity": CONSUMPTION, "value": "nan", "since": "2026-11-20T10:00:00+00:00"},
        {"entity": CONSUMPTION, "value": "inf", "since": "2026-11-20T10:00:00+00:00"},
        {"entity": "", "value": "412.7", "since": "2026-11-20T10:00:00+00:00"},
        {"entity": CONSUMPTION, "value": "412.7", "since": 1234},
        None,
    ],
    ids=["no_time_zone", "nan", "inf", "no_entity", "since_not_text", "missing"],
)
def test_a_saved_frozen_value_that_is_not_in_the_shape_of_the_check_is_judged_anew(saved: Any) -> None:
    assert _frozen_value(saved) is None


async def test_a_saved_time_in_the_future_is_set_back_to_the_last_change(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    first.stop()

    hass_storage[store_key(entry.entry_id)]["data"]["consumption_frozen"] = {
        "entity": CONSUMPTION,
        "value": "412.7",
        "since": (dt_util.utcnow() + datetime.timedelta(days=1)).isoformat(),
    }
    set_consumption(hass, "412.7")
    last_changed = hass.states.get(CONSUMPTION).last_changed
    clock[0] = last_changed + FROZEN_AFTER
    await start_watcher(hass, entry)
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)  # the grace is over: the check sets the saved time back to the last change

    found = issue(hass, entry, "consumption_frozen")
    assert found is not None
    assert found.translation_placeholders["since"] == dt_util.as_local(last_changed).strftime("%Y-%m-%d %H:%M")
    assert saved_frozen(hass_storage, entry)["since"] == last_changed.isoformat()


async def test_a_value_that_changes_and_comes_back_between_two_checks_removes_the_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)  # the grace is over

    set_consumption(hass, "415")  # the change is seen as it happens, not at the next check
    set_consumption(hass, "412.7")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None

    clock[0] = hass.states.get(CONSUMPTION).last_changed  # the value is new now: not frozen for long
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


async def test_an_unavailable_consumption_that_comes_back_to_the_same_value_keeps_the_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    since = issue(hass, entry, "consumption_frozen").translation_placeholders["since"]

    set_consumption(hass, "unavailable")
    set_consumption(hass, "412.7")
    await hass.async_block_till_done(wait_background_tasks=True)
    await hourly_check(hass)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.translation_placeholders["since"] == since
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"


async def test_a_saved_time_keeps_the_repair_when_the_repair_is_gone_from_the_registry(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    since = issue(hass, entry, "consumption_frozen").translation_placeholders["since"]
    first.stop()
    ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_consumption_frozen")

    # a restart: the value was set again just now (last_changed starts again), the saved time is still six hours old
    set_consumption(hass, "412.7")
    await start_watcher(hass, entry)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.translation_placeholders["since"] == since


async def test_a_repair_that_names_another_sensor_is_removed_when_the_saved_value_is_lost(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None
    first.stop()
    hass_storage[store_key(entry.entry_id)]["data"].pop("consumption_frozen")

    # the sensor was changed in the options and the store was lost: the old sensor's repair is no longer right
    hass.states.async_set("sensor.other_consumption", "100", {"unit_of_measurement": "W"})
    clock[0] = hass.states.get("sensor.other_consumption").last_changed
    await start_watcher(hass, entry, config={**MOCK_OPTIONS, "power_consumption_entity_id": "sensor.other_consumption"})
    assert issue(hass, entry, "consumption_frozen") is not None, "the grace keeps the repair"
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


def restore_issue_as_after_a_restart(hass: HomeAssistant, entry: MockConfigEntry, name: str, **changes: Any) -> None:
    """The issue registry after a restart: a non-persistent issue comes back inactive with its texts unset (the way
    issue_registry._async_load builds it), so it still exists while its placeholders are None."""
    registry = ir.async_get(hass)
    key = (DOMAIN, f"{entry.entry_id}_{name}")
    registry.issues[key] = dataclasses.replace(
        registry.issues[key],
        active=False,
        breaks_in_ha_version=None,
        data=None,
        is_fixable=None,
        issue_domain=None,
        learn_more_url=None,
        severity=None,
        translation_key=None,
        translation_placeholders=None,
        **changes,
    )


async def test_a_restored_repair_without_placeholders_is_judged_by_the_six_hours_after_a_lost_store(
    hass: HomeAssistant, entry: MockConfigEntry, clock
) -> None:
    """The store is lost and the issue registry brought the repair back inactive, without its placeholders: a sensor that
    changed just now is not raised at once, and it is raised once it has shown the same value for six hours."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{entry.entry_id}_consumption_frozen",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="consumption_frozen",
        translation_placeholders={"prefix": PREFIX, "entity": CONSUMPTION, "value": "413", "since": "2026-11-20 10:00"},
    )
    restore_issue_as_after_a_restart(hass, entry, "consumption_frozen", dismissed_version="2026.1.0")

    await start_watcher(hass, entry)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.active is False and found.translation_placeholders is None
    await hourly_check(hass)  # the check that a bare "the issue exists" would raise on
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.active is False and found.translation_placeholders is None

    age_consumption(hass, clock, FROZEN_AFTER)
    await hourly_check(hass)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.active is True
    assert found.translation_placeholders["entity"] == CONSUMPTION
    assert found.dismissed_version == "2026.1.0", "the user's ignore is kept"


async def test_a_save_from_a_state_change_survives_the_cancellation_of_the_entry_tasks(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unload cancels the background tasks of the entry; a save that the state listener started must still be written."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    watcher = await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)  # the grace is over, so the change below is an answer

    gate = asyncio.Event()
    written = watcher._store.async_save

    async def slow_save(data: Any) -> None:
        await gate.wait()
        await written(data)

    monkeypatch.setattr(watcher._store, "async_save", slow_save)
    set_consumption(hass, "415")  # the repair is deleted and the save is started
    for task in list(entry._background_tasks):  # what unload does
        task.cancel()
    gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert saved_frozen(hass_storage, entry) is None, "the save that was started is written"


async def test_a_restored_repair_without_placeholders_is_removed_by_a_change_once_its_baseline_is_kept(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    """The baseline of an unclear restored repair is what lets a later change remove it (the stale repair is cleaned up)."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{entry.entry_id}_consumption_frozen",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="consumption_frozen",
        translation_placeholders={"prefix": PREFIX, "entity": CONSUMPTION, "value": "413", "since": "2026-11-20 10:00"},
    )
    restore_issue_as_after_a_restart(hass, entry, "consumption_frozen")

    await start_watcher(hass, entry)
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)  # the grace is over: the value seen first is the baseline (no placeholders, so nothing is raised)
    assert issue(hass, entry, "consumption_frozen").translation_placeholders is None
    assert saved_frozen(hass_storage, entry)["value"] == "412.7", "the value seen first is the baseline"

    set_consumption(hass, "415")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


async def test_a_save_still_running_when_the_entry_is_removed_is_written_before_the_store_is_deleted(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removal waits for a save that is still running: otherwise the save writes the store again after it was deleted. The
    save is the one a state change starts (the listener's own task); its write is held in the store itself."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    watcher = await start_watcher(hass, entry)
    assert stored(hass_storage, entry) is not None
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)  # the grace is over, so the change below is an answer

    gate, entered = hold_health_writes(monkeypatch, watcher._store)
    set_consumption(hass, "415")  # the listener starts the save of the change, and its write waits at the gate
    await asyncio.wait_for(entered.wait(), timeout=10)
    await remove_while_held(hass, entry, gate)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert stored(hass_storage, entry) is None, "the save was written before the store was deleted, so the key is gone"


async def test_a_save_of_a_check_is_still_written_before_the_store_is_deleted_after_the_entry_stops(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unload stops the watcher and cancels its check while the check's save is being written: the write goes on, and the
    removal of the entry waits for it, so the store is deleted after the write and its key is gone."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    watcher = await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is None

    gate, entered = hold_health_writes(monkeypatch, watcher._store)
    try:
        age_consumption(hass, clock, FROZEN_AFTER)
        async_fire_time_changed(hass, dt_util.utcnow() + RECHECK_INTERVAL * next(_HOURS))  # the hourly check raises the repair
        await asyncio.wait_for(entered.wait(), timeout=10)  # the save of that repair is being written, at the gate
        watcher.stop()  # what unload does: the check is cancelled
        await remove_while_held(hass, entry, gate)
    finally:
        gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert stored(hass_storage, entry) is None, "the save was written before the store was deleted, so the key is gone"


async def restart_with_frozen_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, sensor_state: str, *, restored: bool = False
) -> None:
    """A repair for 412.7 that has been there for six hours, ignored by the user; then Home Assistant starts again and the
    house consumption sensor shows sensor_state. restored: the issue registry brings the repair back without its placeholders
    (as after a restart)."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None
    ir.async_ignore_issue(hass, DOMAIN, f"{entry.entry_id}_consumption_frozen", True)
    first.stop()
    if restored:
        restore_issue_as_after_a_restart(hass, entry, "consumption_frozen", dismissed_version="2026.1.0")

    set_consumption(hass, sensor_state)
    await start_watcher(hass, entry)


@pytest.mark.parametrize("restored", [False, True], ids=["repair_in_place", "repair_restored_without_placeholders"])
async def test_a_different_value_during_the_grace_keeps_the_repair_and_its_ignore(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], restored: bool
) -> None:
    """A sensor that shows 0 right after the start is no answer during the grace: the repair stays with its Ignore, and a check
    that reads the saved value again keeps it too."""
    await restart_with_frozen_repair(hass, entry, clock, "0", restored=restored)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.dismissed_version is not None
    assert found.translation_placeholders["value"] == "413"
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"

    set_consumption(hass, "412.7")
    await hourly_check(hass)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.dismissed_version is not None
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"


async def test_during_the_grace_a_restored_repair_with_ignore_survives_a_plan_check_and_a_change(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    """Grace, restored repair: the sensor starts at 0. One minute later the plan check runs while the sensor still reads 0, and
    then the sensor changes from 0 to a value (a real listener event, ignored during the grace). The repair is kept with its
    Ignore, its texts come from the store, and the store is unchanged."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    first = await start_watcher(hass, entry)
    ir.async_ignore_issue(hass, DOMAIN, f"{entry.entry_id}_consumption_frozen", True)
    first.stop()
    restore_issue_as_after_a_restart(hass, entry, "consumption_frozen", dismissed_version="2026.1.0")

    set_consumption(hass, "0")  # Home Assistant starts with the sensor at 0
    clock[0] = hass.states.get(CONSUMPTION).last_changed
    await start_watcher(hass, entry)
    saved_before = stored(hass_storage, entry)

    clock[0] += datetime.timedelta(minutes=1)
    show_plan(hass, plan(0, 0))  # a check, while the sensor reads 0
    await hass.async_block_till_done(wait_background_tasks=True)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.active is True and found.dismissed_version == "2026.1.0"
    assert found.translation_placeholders["value"] == "413"

    hass.states.async_set(CONSUMPTION, "415", {"unit_of_measurement": "W"})  # 0 -> 415 during the grace: ignored
    await hass.async_block_till_done(wait_background_tasks=True)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.active is True and found.dismissed_version == "2026.1.0"
    assert stored(hass_storage, entry) == saved_before
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"


async def test_during_the_grace_a_real_change_to_zero_and_back_keeps_the_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    """The listener ignores every change during the grace: a real 412.7 -> 0 -> 412.7 sequence removes nothing."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None

    hass.states.async_set(CONSUMPTION, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(CONSUMPTION, "412.7", {"unit_of_measurement": "W"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "consumption_frozen") is not None
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"


async def test_a_different_number_during_the_grace_is_no_answer_and_the_first_check_after_it_decides(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    """A real change to another number during the grace: the listener ignores it and the repair is kept. Only the check after the
    grace can delete it, and it does."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    assert issue(hass, entry, "consumption_frozen") is not None

    hass.states.async_set(CONSUMPTION, "415", {"unit_of_measurement": "W"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "consumption_frozen") is not None
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"

    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


async def test_after_the_grace_a_real_change_of_the_sensor_removes_the_repair_at_once(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    await start_watcher(hass, entry)
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)  # the grace is over; the value is still the saved one
    assert issue(hass, entry, "consumption_frozen") is not None

    hass.states.async_set(CONSUMPTION, "415", {"unit_of_measurement": "W"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


async def test_a_lost_store_with_a_repair_for_this_sensor_takes_its_baseline_only_after_the_grace(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any]
) -> None:
    """The store is lost and the repair of this sensor is still open. During the grace neither a reading of 0 nor the real value
    is a baseline: the baseline is the value of the first check after the grace. A later change then removes the repair."""
    set_source_states(hass)
    set_consumption(hass, "0")
    started = hass.states.get(CONSUMPTION).last_changed
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{entry.entry_id}_consumption_frozen",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="consumption_frozen",
        translation_placeholders={"prefix": PREFIX, "entity": CONSUMPTION, "value": "413", "since": "2026-11-20 10:00"},
    )
    clock[0] = started
    await start_watcher(hass, entry)
    assert saved_frozen(hass_storage, entry) is None

    clock[0] = started + datetime.timedelta(minutes=1)
    await hourly_check(hass)  # reads 0 during the grace: no baseline
    assert saved_frozen(hass_storage, entry) is None

    set_consumption(hass, "412.7")
    clock[0] = started + datetime.timedelta(minutes=10)
    await hourly_check(hass)  # the real value during the grace: still no baseline
    assert saved_frozen(hass_storage, entry) is None
    assert issue(hass, entry, "consumption_frozen") is not None

    clock[0] = started + CONSUMPTION_GRACE
    await hourly_check(hass)  # the first check after the grace takes the value seen now
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"
    since = hass.states.get(CONSUMPTION).last_changed
    assert issue(hass, entry, "consumption_frozen").translation_placeholders["since"] == dt_util.as_local(since).strftime("%Y-%m-%d %H:%M")

    hass.states.async_set(CONSUMPTION, "415", {"unit_of_measurement": "W"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


@pytest.mark.parametrize("later", ["0", "415"])
async def test_a_different_value_after_the_grace_deletes_the_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], later: str
) -> None:
    """The sensor shows 0 at the start, which the grace keeps. After the grace the first check that reads a number other than the
    saved one is the answer."""
    await restart_with_frozen_repair(hass, entry, clock, "0")
    assert issue(hass, entry, "consumption_frozen") is not None

    clock[0] += CONSUMPTION_GRACE
    if later != "0":
        set_consumption(hass, later)
    await hourly_check(hass)
    assert issue(hass, entry, "consumption_frozen") is None
    assert saved_frozen(hass_storage, entry) is None


@pytest.mark.parametrize("sensor_state", ["unavailable", "not a number"])
async def test_an_unclear_consumption_after_the_grace_keeps_the_saved_repair(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], sensor_state: str
) -> None:
    await restart_with_frozen_repair(hass, entry, clock, sensor_state)
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)
    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.dismissed_version is not None
    assert saved_frozen(hass_storage, entry)["value"] == "412.7"


@pytest.mark.parametrize("sensor_state", ["unavailable", "not a number"])
async def test_a_restored_repair_without_placeholders_and_a_lost_store_is_unchanged_while_the_sensor_is_unclear(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], sensor_state: str
) -> None:
    """The issue registry restored the repair without placeholders and the store is lost. An unavailable sensor or a text is no
    answer: the repair stays as it is (with its Ignore) and nothing about it is saved."""
    set_source_states(hass)
    set_consumption(hass, sensor_state)
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{entry.entry_id}_consumption_frozen",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="consumption_frozen",
        translation_placeholders={"prefix": PREFIX, "entity": CONSUMPTION, "value": "413", "since": "2026-11-20 10:00"},
    )
    restore_issue_as_after_a_restart(hass, entry, "consumption_frozen", dismissed_version="2026.1.0")

    await start_watcher(hass, entry)
    saved_before = stored(hass_storage, entry)
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)  # the grace is over: an unavailable sensor or a text is still no answer

    found = issue(hass, entry, "consumption_frozen")
    assert found is not None and found.active is False and found.translation_placeholders is None
    assert found.dismissed_version == "2026.1.0"
    assert stored(hass_storage, entry) == saved_before
    assert saved_frozen(hass_storage, entry) is None


async def test_a_cancelled_removal_does_not_cancel_the_saves_it_waits_for(
    hass: HomeAssistant, entry: MockConfigEntry, clock, hass_storage: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removal waits for the saves of the entry with asyncio.wait: when the removal is cancelled, the saves stay running (gather
    would cancel them, so the store would not be written)."""
    set_source_states(hass)
    set_consumption(hass, "412.7")
    age_consumption(hass, clock, FROZEN_AFTER)
    watcher = await start_watcher(hass, entry)
    clock[0] += CONSUMPTION_GRACE
    await hourly_check(hass)

    gate, entered = hold_health_writes(monkeypatch, watcher._store)
    hass.states.async_set(CONSUMPTION, "415", {"unit_of_measurement": "W"})  # the listener starts a save, written at the gate
    await asyncio.wait_for(entered.wait(), timeout=10)
    saves = set(_pending_saves(hass, entry.entry_id))
    assert saves and not any(task.done() for task in saves)

    under_way = asyncio.Event()

    async def removing() -> None:
        under_way.set()
        await async_remove_health_store(hass, entry.entry_id)

    removal = asyncio.ensure_future(removing())
    await under_way.wait()  # the removal is waiting for the save now
    assert not removal.done()
    removal.cancel()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await removal
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not any(task.cancelled() for task in saves), "the cancelled removal did not cancel the saves it waited for"
    assert all(task.done() for task in saves)
    assert stored(hass_storage, entry) is not None, "the save was written; the cancelled removal did not delete the store"


async def test_a_removal_that_completes_forgets_the_saves_of_the_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    _pending_saves(hass, entry.entry_id)
    assert entry.entry_id in hass.data[SAVES_KEY]
    await async_remove_health_store(hass, entry.entry_id)
    assert entry.entry_id not in hass.data[SAVES_KEY]
