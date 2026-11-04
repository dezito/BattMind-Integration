"""Debug export for GitHub issues (management/debug_export.py) and its replay (tests/debug_replay.py)."""
from __future__ import annotations

import datetime
import gzip
import json
import logging
from pathlib import Path

import pytest

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battmind.const import DOMAIN
from custom_components.battmind.management.debug_export import DEBUG_DIR, KEEP_FILES, Redactor, async_debug_data, jsonable

from . import debug_replay
from .conftest import CONSUMPTION, PREFIX, PRICE

BUTTON = f"button.{PREFIX}_export_debug"


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    hass.config.config_dir = str(tmp_path)
    return tmp_path / DEBUG_DIR


def _read(path: Path) -> tuple[dict, str]:
    with gzip.open(path, "rt", encoding="utf-8") as file:
        text = file.read()
    return json.loads(text), text


async def test_export_is_anonymised(hass: HomeAssistant, config_dir: Path, loaded_entry) -> None:
    hass.config.location_name = "Sommerhuset Bakkebo"
    hass.states.async_set("person.thomas", "home", {"friendly_name": "Thomas Sloth", "latitude": 55.6761, "longitude": 12.5683})
    logging.getLogger("custom_components.battmind.engine").warning(
        "Thomas Sloth at 192.168.1.20 (thomas@example.com, https://example.com/x) has %s", CONSUMPTION
    )

    response = await hass.services.async_call(DOMAIN, "export_debug", {}, blocking=True, return_response=True)
    file = response["files"][PREFIX]
    data, text = _read(config_dir.parent / file)

    assert data["integration"] == DOMAIN and data["prefix"] == PREFIX
    assert data["config"]["solar"]["battery_temperature_min"] == 0.0
    assert data["config"]["solar"]["battery_temperature_max"] == 50.0
    assert data["jobs"] and data["snapshots"] and data["engine"]["plan"] is not None
    assert set(data["databases"]) >= {"charging_history", "power_values"}
    # other integrations' entities are named after their config key, own entities are kept
    for secret in (CONSUMPTION, PRICE, "Thomas", "Sloth", "Bakkebo", "192.168.1.20", "example.com"):
        assert secret not in text, secret
    assert "sensor.power_consumption" in text
    assert data["config"]["home"]["entity_ids"]["power_consumption_entity_id"] == "sensor.power_consumption"
    assert f"sensor.{PREFIX}_" in text
    # the replay falls back to these prices when the snapshots have none
    assert data["price_data"]["attributes"]
    message = next(record["message"] for record in data["log"] if "192.168" not in record["message"] and "<ip>" in record["message"])
    assert message == "<redacted> at <ip> (<email>, <url>) has sensor.power_consumption"

    item = hass.data["persistent_notification"][f"{PREFIX}_debug_export"]
    assert "issues/new?template=bug_report.yml" in item["message"]
    assert file.split("/")[-1] in item["message"]


AUTOMATIONS = """
- id: '1700000000000'
  alias: Thomas battery
  use_blueprint:
    path: battmind/battery_control.yaml
    input:
      battmind_action: {action_sensor}
      grid_charging:
        - action: number.set_value
          target:
            entity_id: number.thomas_wallbox_current
          data:
            value: 16
        - action: goodwe.set_parameter
          data:
            device_id: {device_id}
        - action: notify.mobile_app_thomas_phone
          data:
            message: "{{{{ states('sensor.thomas_inverter_soc') }}}}"
- id: '1700000000001'
  alias: Other blueprint
  use_blueprint:
    path: homeassistant/motion_light.yaml
    input: {{}}
"""


async def test_export_has_blueprint_automations_and_integrations(hass: HomeAssistant, config_dir: Path, loaded_entry) -> None:
    """The blueprint automations with their inputs, the devices and the integrations they use, anonymised."""
    hass.states.async_set("person.thomas", "home", {"friendly_name": "Thomas Sloth"})
    wallbox = MockConfigEntry(domain="wallbox", title="Thomas' Wallbox")
    wallbox.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=wallbox.entry_id, identifiers={("wallbox", "WB123456")}, name="Thomas lader",
        manufacturer="Wallbox", model="Pulsar Plus", sw_version="6.2.1", serial_number="WB123456",
    )
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("number", "wallbox", "WB123456_current", suggested_object_id="thomas_wallbox_current", device_id=device.id, config_entry=wallbox)
    hass.states.async_set("number.thomas_wallbox_current", "16", {"unit_of_measurement": "A"})
    ent_reg.async_get_or_create("automation", "automation", "1700000000000", suggested_object_id="thomas_battery")
    hass.states.async_set("automation.thomas_battery", "on", {"last_triggered": None, "current": 0})
    hass.services.async_register("number", "set_value", lambda call: None)
    action_sensor = next(
        entry.entity_id for entry in er.async_entries_for_config_entry(ent_reg, loaded_entry.entry_id) if entry.entity_id.endswith("_powerwall_action")
    )
    Path(hass.config.path("automations.yaml")).write_text(AUTOMATIONS.format(action_sensor=action_sensor, device_id=device.id), encoding="utf-8")

    response = await hass.services.async_call(DOMAIN, "export_debug", {}, blocking=True, return_response=True)
    data, text = _read(config_dir.parent / response["files"][PREFIX])

    for secret in ("Thomas", "thomas", "WB123456", "mobile_app"):
        assert secret not in text, secret
    assert data["blueprints"]["files"]["battmind/battery_control.yaml"] == {"installed": True, "bundled": True, "up_to_date": True}
    [automation] = data["blueprints"]["automations"]
    assert automation["alias"] == "<redacted> battery" and automation["this_instance"] is True
    assert automation["entity"]["state"] == "on"
    # entities are named after the input, service names are kept, the notify service is not
    actions = automation["input"]["grid_charging"]
    assert actions[0] == {"action": "number.set_value", "target": {"entity_id": "number.grid_charging"}, "data": {"value": 16}}
    assert actions[1]["action"] == "goodwe.set_parameter"
    assert actions[2]["action"].startswith("notify.entity_")
    assert actions[2]["data"]["message"] == "{{ states('sensor.grid_charging') }}"
    assert automation["input"]["battmind_action"] == action_sensor

    entity = data["entities"]["number.grid_charging"]
    assert entity["platform"] == "wallbox" and entity["blueprint"] is True and entity["unit"] == "A"
    device_info = {"manufacturer": "Wallbox", "model": "Pulsar Plus", "sw_version": "6.2.1", "integrations": ["wallbox"]}
    assert entity["device"] == device_info
    assert data["devices"] == {device.id: device_info}
    assert data["integrations"]["wallbox"] == {"custom": False, "version": None, "entries": ["not_loaded"]}
    assert data["integrations"]["goodwe"]["custom"] is False
    assert "number" not in data["integrations"] and DOMAIN not in data["integrations"]

    # a changed blueprint (old version or own changes) comes with its content
    blueprint = Path(hass.config.path("blueprints", "automation", DOMAIN, "battery_control.yaml"))
    blueprint.write_text(blueprint.read_text(encoding="utf-8").replace("BattMind - control", "BattMind (mine) - control"), encoding="utf-8")
    raw, redactor = await async_debug_data(hass, loaded_entry.runtime_data, dt_util.now(), databases=False)
    file = (await hass.async_add_executor_job(jsonable, raw, redactor))["blueprints"]["files"]["battmind/battery_control.yaml"]
    assert file["up_to_date"] is False and "BattMind (mine) - control" in file["content"]


async def test_button_keeps_the_newest_files(hass: HomeAssistant, config_dir: Path, loaded_entry) -> None:
    config_dir.mkdir(parents=True)
    for index in range(KEEP_FILES + 2):
        (config_dir / f"{PREFIX}_debug_20200101_00000{index}.json.gz").write_bytes(b"")
    (config_dir / "other_debug_20200101_000000.json.gz").write_bytes(b"")
    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    own = sorted(p.name for p in config_dir.iterdir() if p.name.startswith(f"{PREFIX}_debug_"))
    assert len(own) == KEEP_FILES and not own[-1].startswith(f"{PREFIX}_debug_2020")
    assert (config_dir / "other_debug_20200101_000000.json.gz").exists()


async def test_download_view(hass: HomeAssistant, config_dir: Path, loaded_entry, hass_client_no_auth) -> None:
    await async_setup_component(hass, "http", {})
    response = await hass.services.async_call(DOMAIN, "export_debug", {}, blocking=True, return_response=True)
    name = response["files"][PREFIX].split("/")[-1]
    client = await hass_client_no_auth()
    assert (await client.get(f"/api/{DOMAIN}/debug/{name}")).status == 401
    signed = async_sign_path(hass, f"/api/{DOMAIN}/debug/{name}", datetime.timedelta(minutes=5), use_content_user=True)
    download = await client.get(signed)
    assert download.status == 200
    assert gzip.decompress(await download.read()).startswith(b"{")
    assert (await client.get(async_sign_path(hass, f"/api/{DOMAIN}/debug/..%2Fsecrets.yaml", datetime.timedelta(minutes=5), use_content_user=True))).status == 404


async def test_replay_reproduces_the_plan(hass: HomeAssistant, config_dir: Path, loaded_entry) -> None:
    coordinator = loaded_entry.runtime_data
    await coordinator.async_run_job("charge_if_needed", True, include_forecast=True)
    recorded = coordinator.data["current_charging_rule"]
    response = await hass.services.async_call(DOMAIN, "export_debug", {}, blocking=True, return_response=True)
    export = debug_replay.load_export(str(config_dir.parent / response["files"][PREFIX]))
    assert export["snapshots"][-1]["job"] == "charge_if_needed"
    assert export["planning_job"]["job"] == "charge_if_needed"

    result = await hass.async_add_executor_job(debug_replay.replay, export)
    assert result.error is None
    assert result.value == coordinator.engine.last_powerwall_action
    assert result.states["current_charging_rule"] == recorded


async def test_replay_adds_temperature_defaults_to_old_export(hass: HomeAssistant, config_dir: Path, loaded_entry) -> None:
    response = await hass.services.async_call(DOMAIN, "export_debug", {}, blocking=True, return_response=True)
    export = debug_replay.load_export(str(config_dir.parent / response["files"][PREFIX]))
    export["config"]["solar"].pop("battery_temperature_min")
    export["config"]["solar"].pop("battery_temperature_max")

    engine = debug_replay.build_engine(export)

    assert engine.config["solar"]["battery_temperature_min"] == 0.0
    assert engine.config["solar"]["battery_temperature_max"] == 50.0


def test_redactor_aliases() -> None:
    config = {
        "solar": {"entity_ids": {"forecast_entity_id": "sensor.solcast_home_forecast_today"}},
        "home": {"entity_ids": {"ignore_consumption_from_entity_ids": ["sensor.thomas_car", "switch.pool"]}},
    }
    redactor = Redactor("bm", config, ["Thomas"])
    assert redactor.text("sensor.solcast_home_forecast_day_3") == "sensor.forecast_day_3"
    assert redactor.text("sensor.thomas_car, switch.pool") == "sensor.ignore_consumption_from_1, switch.ignore_consumption_from_2"
    assert redactor.text("sensor.bm_overview custom_components/battmind/sensor.py") == "sensor.bm_overview custom_components/battmind/sensor.py"
    assert redactor.text("sensor.unknown_thing") == "sensor.entity_4"
    assert redactor.value({"latitude": 55.6761, "elevation": 37, "api_key": "abc", "friendly_name": "Thomas"}) == {
        "latitude": 56, "elevation": 0, "api_key": "<redacted>", "friendly_name": "<redacted>"
    }
