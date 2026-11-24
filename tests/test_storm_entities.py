"""Storm warning entities, snapshot, dashboard section and settings page: all of it exists only with the option."""

from __future__ import annotations

import datetime

import yaml

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.battmind.const import (
    CONF_ENTITY_PREFIX,
    DOMAIN,
    FLOW_STEPS,
    OPTIONS_MENU,
    STORM_NUMBER,
    STORM_NUMBER_KEY,
    STORM_SENSOR_KEY,
    STORM_STATUSES,
)
from custom_components.battmind.engine import storm
from custom_components.battmind.management.dashboard_build import render_dashboard
from custom_components.battmind.const import build_config
from custom_components.battmind.flow.schema import SELECTORS, normalize_input, validate_step

from .conftest import MOCK_OPTIONS, PREFIX, async_unload_and_join, form_defaults, set_source_states
from .test_engine import OPTIONS

ENTITY = "binary_sensor.meteoalarm"
SENSOR = f"sensor.{PREFIX}_{STORM_SENSOR_KEY}"
NUMBER = f"number.{PREFIX}_{STORM_NUMBER_KEY}"


def with_option(entry, hass: HomeAssistant, value: str = ENTITY) -> None:
    hass.config_entries.async_update_entry(entry, options={**entry.options, "storm_warning_entity_id": value})


async def start(hass: HomeAssistant, mock_entry, option: str = ENTITY):
    set_source_states(hass)
    if option:
        hass.states.async_set(
            ENTITY,
            "on",
            {
                "awareness_level": "3; orange; Severe",
                "awareness_type": "1; wind",
                "onset": "2026-11-25T18:00:00+01:00",
                "expires": "2026-11-25T23:00:00+01:00",
                "headline": "Gale warning for Somewhere Street",  # names a place: never copied into the snapshot
                "description": "Long text",
            },
        )
    mock_entry.add_to_hass(hass)
    with_option(mock_entry, hass, option)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return mock_entry.runtime_data


async def test_with_the_option_the_sensor_and_the_number_are_made(hass: HomeAssistant, mock_entry) -> None:
    coordinator = await start(hass, mock_entry)
    registry = er.async_get(hass)
    assert registry.async_get(SENSOR) is not None and registry.async_get(NUMBER) is not None
    number = hass.states.get(NUMBER)
    assert float(number.state) == STORM_NUMBER["default"] == storm.STORM_DEFAULT_HOURS
    assert (number.attributes["min"], number.attributes["max"]) == (STORM_NUMBER["min"], STORM_NUMBER["max"])
    assert coordinator.settings[STORM_NUMBER_KEY] == STORM_NUMBER["default"]
    state = hass.states.get(SENSOR)
    assert state.state in STORM_STATUSES  # the warning is in the future of the test clock or past: any status, never unknown
    await async_unload_and_join(hass, mock_entry)


async def test_the_snapshot_keeps_only_the_attributes_the_plan_reads(hass: HomeAssistant, mock_entry) -> None:
    coordinator = await start(hass, mock_entry)
    alert = (await coordinator._async_snapshot()).storm_alert
    assert alert["entity_id"] == ENTITY and alert["state"] == "on"
    assert set(alert["attributes"]) == {"awareness_level", "awareness_type", "onset", "expires"}
    assert set(alert["attributes"]) <= set(storm.STORM_ATTRIBUTES)
    # not a startup requirement: an unavailable MeteoAlarm sensor must not hold BattMind back (the status says it)
    assert ENTITY not in coordinator.configured_entities
    await async_unload_and_join(hass, mock_entry)


async def test_a_missing_warning_entity_is_unavailable_not_an_error(hass: HomeAssistant, mock_entry) -> None:
    coordinator = await start(hass, mock_entry)
    hass.states.async_remove(ENTITY)
    alert = (await coordinator._async_snapshot()).storm_alert
    assert alert == {"entity_id": ENTITY, "state": None, "attributes": {}}
    assert storm.parse_alert(alert["state"], alert["attributes"], datetime.datetime(2026, 11, 25, 10), None).status == storm.STATUS_UNAVAILABLE
    await async_unload_and_join(hass, mock_entry)


async def test_without_the_option_nothing_is_made(hass: HomeAssistant, mock_entry) -> None:
    coordinator = await start(hass, mock_entry, option="")
    registry = er.async_get(hass)
    assert registry.async_get(SENSOR) is None and registry.async_get(NUMBER) is None
    assert hass.states.get(SENSOR) is None and hass.states.get(NUMBER) is None
    assert (await coordinator._async_snapshot()).storm_alert is None
    assert STORM_NUMBER_KEY not in coordinator.settings
    await async_unload_and_join(hass, mock_entry)


async def test_clearing_the_option_removes_the_entities(hass: HomeAssistant, mock_entry) -> None:
    await start(hass, mock_entry)
    registry = er.async_get(hass)
    assert registry.async_get(SENSOR) is not None
    await async_unload_and_join(hass, mock_entry)
    with_option(mock_entry, hass, "")
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert registry.async_get(SENSOR) is None and registry.async_get(NUMBER) is None
    await async_unload_and_join(hass, mock_entry)


def test_the_status_sensor_has_a_state_for_every_status_of_the_engine() -> None:
    engine_statuses = {value for name, value in vars(storm).items() if name.startswith("STATUS_") and value != storm.STATUS_OFF}
    assert engine_statuses == set(STORM_STATUSES)


# --- dashboard -------------------------------------------------------------------------------------------------


def storm_section(option: str) -> list[dict] | None:
    """The cards of the grid with the storm heading in the settings view (None: there is no such section)."""
    content = yaml.safe_load(render_dashboard(build_config({**OPTIONS, "language": "en", "storm_warning_entity_id": option}), "battmind"))
    found = []

    def walk(node):
        if isinstance(node, dict):
            if any(card.get("heading") == "Storm warning" for card in node.get("cards", ()) if isinstance(card, dict)):
                found.append(node["cards"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(content["views"])
    assert len(found) <= 1
    return found[0] if found else None


def test_the_dashboard_has_the_storm_section_only_with_the_option() -> None:
    assert storm_section("") is None  # no heading, no Read more, no rows
    cards = storm_section(ENTITY)
    assert [card["type"] for card in cards] == ["heading", "markdown", "entities"]
    assert "Read more" in cards[1]["content"] and "Storm warning" in cards[1]["content"]
    rows = cards[2]["entities"]
    assert {row["entity"] for row in rows if "entity" in row} == {"sensor.battmind_storm_warning", "number.battmind_storm_cover_hours"}
    # the rows of the sensor: the start, the end, the level kept, the use and the buffer
    assert [row["attribute"] for row in rows if row.get("type") == "attribute"] == ["start", "end", "keep_percent", "expected_use_kwh", "buffer_percent"]
    text = yaml.dump(storm_section(ENTITY))
    assert "@@" not in text  # every placeholder has its text


def test_the_read_more_text_names_what_the_section_shows() -> None:
    """The Read more text covers every row below it: the status, when it starts and ends, the level, the use, the buffer and the number."""
    from custom_components.battmind.utils.formatting import load_texts

    for language in ("da", "de", "en", "pl", "uk"):
        texts = load_texts(language, None)
        help_text = texts["dashboard.help_storm"]
        assert texts["dashboard.section_storm"] and help_text
        # the rows are named like in the text: the status sensor and the number are bold, the attributes are described
        assert f"***{texts['dashboard.section_storm']}***" in help_text
        number_name = yaml.safe_load(open(f"custom_components/battmind/translations/{language}.json", encoding="utf-8"))["entity"]["number"][STORM_NUMBER_KEY]["name"]
        assert f"***{number_name}***" in help_text
        for key in ("storm_start", "storm_end", "storm_keep_percent", "storm_expected_use", "storm_buffer"):
            assert texts[f"dashboard.{key}"]  # the names of the rows the text describes


# --- the settings page -----------------------------------------------------------------------------------------


def test_the_storm_page_is_in_the_options_menu_but_not_in_the_first_setup() -> None:
    from custom_components.battmind.flow.schema import CONFIG_FLOW_STEPS

    assert FLOW_STEPS["storm"] == ["storm_warning_entity_id", "storm_min_level"]
    assert "storm" in OPTIONS_MENU["solar"] and "storm" not in CONFIG_FLOW_STEPS


def test_the_page_values_are_read_as_an_entity_or_nothing_and_a_whole_level() -> None:
    assert normalize_input(FLOW_STEPS["storm"], {"storm_warning_entity_id": ENTITY, "storm_min_level": 2.0}) == {"storm_warning_entity_id": ENTITY, "storm_min_level": 2}
    assert normalize_input(FLOW_STEPS["storm"], {"storm_min_level": 4}) == {"storm_warning_entity_id": "", "storm_min_level": 4}  # cleared = off
    assert build_config({**OPTIONS})["home"]["entity_ids"]["storm_warning_entity_id"] == ""  # off by default
    assert build_config({**OPTIONS})["home"]["storm_min_level"] == storm.STORM_DEFAULT_LEVEL
    assert validate_step("storm", {"storm_warning_entity_id": ENTITY, "storm_min_level": 3}) == {}
    assert set(SELECTORS) >= set(FLOW_STEPS["storm"])


async def test_the_options_flow_sets_the_storm_warning(hass: HomeAssistant, loaded_entry) -> None:
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert "menu_solar" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_solar"})
    assert result["step_id"] == "solar"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "weather"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "storm"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"storm_warning_entity_id": ENTITY, "storm_min_level": 4})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded_entry.options["storm_warning_entity_id"] == ENTITY and loaded_entry.options["storm_min_level"] == 4
    assert loaded_entry.runtime_data.config["home"]["entity_ids"]["storm_warning_entity_id"] == ENTITY
    assert er.async_get(hass).async_get(SENSOR) is not None  # the reload made the entities


async def test_an_unavailable_warning_entity_does_not_stop_the_startup(hass: HomeAssistant, mock_entry) -> None:
    set_source_states(hass)
    hass.states.async_set(ENTITY, "unavailable")
    mock_entry.add_to_hass(hass)
    with_option(mock_entry, hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = mock_entry.runtime_data
    assert coordinator.started, coordinator.data.get("current_charging_rule")
    assert hass.states.get(SENSOR).state == "entity_unavailable"  # never "unavailable": the entity itself is there
    await async_unload_and_join(hass, mock_entry)
