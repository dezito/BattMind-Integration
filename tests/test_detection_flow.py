"""The review page of the config and options flows: when the suggestions are made, and what they change."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.battmind.const import CONF_ENTITY_PREFIX, DOMAIN
from custom_components.battmind.flow import pages
from custom_components.battmind.flow.detection import Detection, Verdict

from .conftest import BATTERY_FLOW, BATTERY_LEVEL, CONSUMPTION, PRICE, form_defaults
from .test_config_flow import BATTERY_SPECS, FIXTURES

INVERT = "invert_powerwall_watt_flow_entity_id"
CHARGING = "power_consumption_entity_id_include_powerwall_charging"
DISCHARGING = "power_consumption_entity_id_include_powerwall_discharging"

FOUND = Detection(
    Verdict(True, 2, {"soc_start": 41.2, "soc_end": 63.9, "power_avg": 1840.4}),
    Verdict(False, 1, {"c_charging": 412.0, "c_idle": 389.0, "p_charging": 2100.0}),
    Verdict(None, None, {"c_discharging": 150.0, "c_idle": 389.0, "p_discharging": 900.0}),
)


class Detector:
    """Stands in for the recorder: counts the calls and answers with what the test chose."""

    def __init__(self, answer: Detection = FOUND) -> None:
        self.answer = answer
        self.calls: list[tuple] = []

    async def __call__(self, hass, consumption, power, level, trusted=None, now=None) -> Detection:
        self.calls.append((consumption, power, level, trusted))
        return self.answer


@pytest.fixture
def detector(monkeypatch) -> Detector:
    stand_in = Detector()
    monkeypatch.setattr(pages, "async_detect", stand_in)
    return stand_in


async def _to_review(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITY_PREFIX: "bmflow", "language": "da"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"powerwall_battery_level_entity_id": BATTERY_LEVEL, "powerwall_watt_flow_entity_id": BATTERY_FLOW}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], BATTERY_SPECS)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"unsupported_actions": []})
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"power_consumption_entity_id": CONSUMPTION})


async def test_new_sensors_are_looked_at_and_the_numbers_are_shown(hass: HomeAssistant, detector: Detector) -> None:
    result = await _to_review(hass)

    assert result["step_id"] == "detection_review"
    assert detector.calls == [(CONSUMPTION, BATTERY_FLOW, BATTERY_LEVEL, None)]
    placeholders = result["description_placeholders"]
    assert (placeholders["invert_mark"], placeholders["charging_mark"], placeholders["discharging_mark"]) == ("✅", "⬜", "❔")
    assert (placeholders["soc_start"], placeholders["soc_end"], placeholders["power_avg"], placeholders["invert_window"]) == ("41", "64", "1840", "2 h")
    assert placeholders["c_charging"] == "412" and placeholders["p_charging"] == "2100" and placeholders["c_idle_charging"] == "389"
    # what was decided is suggested, the rest has its default (nothing is turned on that nobody knows)
    defaults = form_defaults(result)
    assert defaults[INVERT] is True and defaults[CHARGING] is False and defaults[DISCHARGING] is False
    assert defaults["redetect"] is False


async def test_the_user_decides_and_the_switch_is_not_saved(hass: HomeAssistant, detector: Detector) -> None:
    result = await _to_review(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {INVERT: False, CHARGING: True, DISCHARGING: True})
    while result["type"] is FlowResultType.FORM:
        answer = form_defaults(result)
        if result["step_id"] == "prices_energidataservice":
            answer["energidataservice_buy_entity_id"] = PRICE
        result = await hass.config_entries.flow.async_configure(result["flow_id"], answer)
    options = result["options"]
    assert (options[INVERT], options[CHARGING], options[DISCHARGING]) == (False, True, True)
    assert "redetect" not in options
    assert len(detector.calls) == 1  # not again for the pages after it


async def test_the_switch_looks_again_and_a_second_visit_does_not(hass: HomeAssistant, detector: Detector) -> None:
    result = await _to_review(hass)
    detector.answer = Detection(Verdict(False, 1, {}), Verdict(True, 1, {}), Verdict(True, 3, {}))

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {INVERT: True, CHARGING: False, DISCHARGING: False, "redetect": True})

    assert result["step_id"] == "detection_review" and len(detector.calls) == 2
    defaults = form_defaults(result)
    assert (defaults[INVERT], defaults[CHARGING], defaults[DISCHARGING]) == (False, True, True)
    assert result["description_placeholders"]["discharging_window"] == "n=3"


async def test_an_import_keeps_its_values_and_nothing_is_looked_at(hass: HomeAssistant, detector: Detector, tmp_path: Path) -> None:
    """The PyScript file has invert: true; the sensors are the imported ones, so nothing is detected and nothing overwritten."""
    shutil.copy(FIXTURES / "battmind_config.yaml", tmp_path / "battmind_config.yaml")
    hass.config.config_dir = str(tmp_path)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITY_PREFIX: "bmflow", "language": "da"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"import_source": "battmind"})
    while result["step_id"] != "detection_review":
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form_defaults(result))

    assert detector.calls == []
    assert form_defaults(result)[INVERT] is True
    assert result["description_placeholders"]["invert_mark"] == "❔"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form_defaults(result))
    while result["type"] is FlowResultType.FORM:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form_defaults(result))
    assert result["options"][INVERT] is True and detector.calls == []


async def test_an_unchanged_entry_is_not_looked_at_until_asked(hass: HomeAssistant, loaded_entry, detector: Detector) -> None:
    hass.config_entries.async_update_entry(loaded_entry, options={**loaded_entry.options, INVERT: True, DISCHARGING: True})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_consumption"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))  # the consumption page
    assert result["step_id"] == "detection_review" and detector.calls == []
    assert form_defaults(result)[INVERT] is True and form_defaults(result)[DISCHARGING] is True

    # asked: the sign of the stored setting is trusted for the same battery sensor, and what stays unknown keeps its value
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form_defaults(result), "redetect": True})
    assert detector.calls[-1][3] is True
    defaults = form_defaults(result)
    assert defaults[INVERT] is True and defaults[CHARGING] is False and defaults[DISCHARGING] is True  # discharging: unknown, kept
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "redetect" not in result["data"]
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_the_trusted_sign_is_the_stored_one_not_what_the_form_says_now(hass: HomeAssistant, loaded_entry, detector: Detector) -> None:
    hass.config_entries.async_update_entry(loaded_entry, options={**loaded_entry.options, INVERT: True})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_consumption"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "detection_review"

    # the user flips the sign by hand and asks again: that guess is not what the history is read with
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form_defaults(result), INVERT: False, "redetect": True})

    assert detector.calls[-1][3] is True
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_changed_battery_sensor_is_looked_at_without_a_trusted_sign(hass: HomeAssistant, loaded_entry, detector: Detector) -> None:
    # present, so the reload after saving does not wait two minutes for a missing entity
    hass.states.async_set("sensor.other_battery_power", "0", {"unit_of_measurement": "W", "device_class": "power"})
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_battery"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**form_defaults(result), "powerwall_watt_flow_entity_id": "sensor.other_battery_power"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))  # battery_specs
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))  # battery_actions

    assert result["step_id"] == "detection_review"
    assert detector.calls == [(CONSUMPTION, "sensor.other_battery_power", BATTERY_LEVEL, None)]
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded_entry.options[INVERT] is True and loaded_entry.options["powerwall_watt_flow_entity_id"] == "sensor.other_battery_power"
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_nothing_found_changes_nothing(hass: HomeAssistant) -> None:
    """No recorder (or no data): the page is shown with ❔ and dashes, the values are the ones in the options."""
    result = await _to_review(hass)
    assert result["step_id"] == "detection_review"
    assert {value for key, value in result["description_placeholders"].items() if key.endswith("mark")} == {"❔"}
    assert result["description_placeholders"]["power_avg"] == "–"
    assert form_defaults(result)[INVERT] is False
