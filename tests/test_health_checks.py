"""Health checks (management/health_checks.py): repairs for what was set up earlier and has gone wrong since."""
from __future__ import annotations

import datetime
import itertools
import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
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
    PLAN_MIN_CHECKED_HOURS,
    RECHECK_INTERVAL,
    HealthWatcher,
    async_remove_health_store,
    store_key,
)
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
    "plan_not_followed": {"prefix", "days", "checked", "mismatches", "percent", "min_checked", "min_mismatches", "max_percent", "latest"},
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
    config: dict[str, Any] | None = None,
) -> HealthWatcher:
    """A started watcher, like the coordinator starts it (its first check has run when this returns)."""
    watcher = HealthWatcher(hass, entry, PREFIX, build_config(config or MOCK_OPTIONS), entities, lambda: set(covered), _da)
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
    assert stored(hass_storage, entry) is None


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


async def test_every_repair_passes_the_placeholders_its_texts_use(hass: HomeAssistant, entry: MockConfigEntry, config_dir: Path) -> None:
    # registry first: Home Assistant gives a new registry entry another id when a state already has that id
    er.async_get(hass).async_get_or_create("sensor", "test", "consumption", suggested_object_id="house_consumption", disabled_by=DISABLED)
    set_source_states(hass)
    hass.states.async_set(PRODUCTION, "0", {"unit_of_measurement": "kVA"})
    write_automations(config_dir, BLUEPRINT_AUTOMATION)
    register_automation(hass, "on")
    show_plan(hass, plan(30, 20))
    await start_watcher(hass, entry)
    hass.states.async_set(AUTOMATION, "off", {"friendly_name": "Battery control"})
    await hass.async_block_till_done(wait_background_tasks=True)

    for key, placeholders in EXPECTED_PLACEHOLDERS.items():
        found = issue(hass, entry, key)
        assert found is not None, key
        assert set(found.translation_placeholders) == placeholders, key
