"""Config flow and options flow of BattMind."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from homeassistant import config_entries
from homeassistant.components.energy.data import async_get_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.battmind.const import CONF_ENTITY_PREFIX, DOMAIN
from custom_components.battmind.const.config import build_config, default_options
from custom_components.battmind.flow.detection import Detection
from custom_components.battmind.management.backup import BACKUP_DIR
from custom_components.battmind.utils.formatting import format_charging_rule

from .conftest import BATTERY_FLOW, BATTERY_LEVEL, CONSUMPTION, PRICE, PRODUCTION, form_defaults

FIXTURES = Path(__file__).parent / "fixtures"

BATTERY_SPECS = {
    "powerwall_battery_size": 13.5,
    "powerwall_battery_level_min": 10,
    "powerwall_battery_level_max": 95,
    "powerwall_charging_power_limit": 5000,
    "powerwall_discharging_power": 5000,
    "powerwall_charge_discharge_loss": 0.1,
    "use_charge_discharge_loss_from_history": True,
    "battery_temperature_min": 0,
    "battery_temperature_max": 50,
}


async def _start(hass: HomeAssistant, prefix: str = "bmflow", **extra):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITY_PREFIX: prefix, "language": "da", **extra})


async def _configure(hass: HomeAssistant, result, step_id: str, user_input: dict):
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == step_id, result
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def _to_country(hass: HomeAssistant, **extra):
    result = await _start(hass, **extra)
    result = await _configure(
        hass, result, "battery_entities",
        {"powerwall_battery_level_entity_id": BATTERY_LEVEL, "powerwall_watt_flow_entity_id": BATTERY_FLOW},
    )
    result = await _configure(hass, result, "battery_specs", BATTERY_SPECS)
    result = await _configure(hass, result, "battery_actions", {"unsupported_actions": ["blocked_solar_charge"]})
    result = await _configure(hass, result, "consumption", {"power_consumption_entity_id": CONSUMPTION})
    # no recorder in the tests: nothing is suggested, the user decides
    return await _configure(hass, result, "detection_review", {"invert_powerwall_watt_flow_entity_id": True})


async def _to_price_provider(hass: HomeAssistant, price_country: str | None = None, **extra):
    """Through the country page (the country of Home Assistant when none is given) to the price provider page."""
    result = await _to_country(hass, **extra)
    return await _configure(hass, result, "country", {"price_country": price_country} if price_country else {})


async def test_full_flow_creates_entry(hass: HomeAssistant) -> None:
    hass.config.country = "SE"
    result = await _to_price_provider(hass, dashboard_icon="mdi:solar-power")
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    result = await _configure(hass, result, "price_fees", {"refund": 0.0, "sell_fee": 0.03})
    result = await _configure(
        hass, result, "solar",
        {"production_entity_id": PRODUCTION, "inverter_discharging_power_limit": 6000, "production_price": -1},
    )
    result = await _configure(hass, result, "weather", {})
    result = await _configure(hass, result, "advanced", {"days_to_predict": 3, "cron_interval": "5", "dashboard": False})
    result = await _configure(
        hass, result, "database",
        {
            "power_values_db_data_to_save": 15,
            "solar_available_db_data_to_save": 10,
            "kwh_avg_prices_db_data_to_save": 15,
            "charging_history_db_data_to_save": 12,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BattMind (bmflow)"
    assert result["data"][CONF_ENTITY_PREFIX] == "bmflow"
    options = result["options"]
    assert options["price_country"] == "SE"  # a new entry: the country of Home Assistant is suggested and stored
    assert options["powerwall_battery_level_entity_id"] == BATTERY_LEVEL
    assert options["invert_powerwall_watt_flow_entity_id"] is True
    assert options["powerwall_battery_size"] == 13.5
    assert options["unsupported_actions"] == ["blocked_solar_charge"]
    assert options["cron_interval"] == 5  # the dropdown value is stored as int
    assert options["stromligning_buy_current_price"] == ""  # provider not chosen
    assert options["hourly_service_entity_id"] == ""
    assert options["dashboard_icon"] == "mdi:solar-power"


async def _finish_after_the_price_pages(hass: HomeAssistant, result):
    result = await _configure(hass, result, "solar", {"production_entity_id": PRODUCTION, "inverter_discharging_power_limit": 6000, "production_price": -1})
    result = await _configure(hass, result, "weather", {})
    result = await _configure(hass, result, "advanced", {"days_to_predict": 3, "cron_interval": "5", "dashboard": False})
    return await _configure(
        hass, result, "database",
        {"power_values_db_data_to_save": 15, "solar_available_db_data_to_save": 10, "kwh_avg_prices_db_data_to_save": 15, "charging_history_db_data_to_save": 12},
    )


@pytest.mark.parametrize(
    ("unit", "scale", "fee", "thresholds"),
    [
        ("DKK/kWh", 1.0, 0.03, None),
        ("EUR/kWh", 0.134, 0.004, {"cheapest_price_rise_threshold": 0.067, "cheap_price_period_rise_threshold": 0.013}),
        ("XYZ/kWh", None, 0.03, None),
        (None, None, 0.03, None),
    ],
)
async def test_a_new_entry_gets_the_price_scale_of_its_price_sensor(hass: HomeAssistant, unit, scale, fee, thresholds) -> None:
    """The scale comes from the unit of the price sensor, not from Home Assistant's currency (EUR by default); the suggested
    sell fee and wear cost (on the fee page, after the price pages) and the starting values of the two price rise
    thresholds follow it, in kroner they stay as they are."""
    hass.states.async_set(PRICE, "0.5", {"unit_of_measurement": unit} if unit else {})
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    assert result["step_id"] == "price_fees"
    assert form_defaults(result)["sell_fee"] == fee  # the suggestion is 0.03 kroner in the price currency
    wear = 0.013 if scale == 0.134 else 0.1  # 0.1 kroner in the price currency, shown before it is stored
    assert form_defaults(result)["powerwall_wear_cost_per_kwh"] == wear
    result = await _configure(hass, result, "price_fees", form_defaults(result))
    result = await _finish_after_the_price_pages(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = result["options"]
    assert options.get("price_scale", 1.0) == (scale or 1.0)
    assert options["sell_fee"] == fee and options["powerwall_wear_cost_per_kwh"] == wear
    assert {key: value for key, value in result["data"]["initial_settings"].items() if key.endswith("rise_threshold")} == (thresholds or {})


async def test_a_wear_cost_the_user_changed_is_kept_when_the_scale_is_found(hass: HomeAssistant) -> None:
    hass.states.async_set(PRICE, "0.5", {"unit_of_measurement": "EUR/kWh"})
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    result = await _configure(hass, result, "price_fees", {**form_defaults(result), "powerwall_wear_cost_per_kwh": 0.05})
    result = await _finish_after_the_price_pages(hass, result)
    assert result["options"]["price_scale"] == 0.134 and result["options"]["powerwall_wear_cost_per_kwh"] == 0.05


async def test_a_sell_fee_the_user_changed_is_kept_when_the_scale_is_found(hass: HomeAssistant) -> None:
    hass.states.async_set(PRICE, "0.5", {"unit_of_measurement": "EUR/kWh"})
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    result = await _configure(hass, result, "price_fees", {"refund": 0.0, "sell_fee": 0.05})
    result = await _finish_after_the_price_pages(hass, result)
    assert result["options"]["price_scale"] == 0.134 and result["options"]["sell_fee"] == 0.05


async def test_energy_price_forecast_page_refuses_cents_a_wholesale_buy_sensor_and_two_currencies(hass: HomeAssistant) -> None:
    retail = {"unit_of_measurement": "EUR/kWh", "raw_today": [], "retail_source": "estimate"}
    hass.states.async_set("sensor.epf_retail", "0.2", retail)
    hass.states.async_set("sensor.epf_cents", "20", {**retail, "unit_of_measurement": "ct/kWh"})
    hass.states.async_set("sensor.epf_wholesale", "0.1", {"unit_of_measurement": "EUR/kWh", "raw_today": []})
    hass.states.async_set("sensor.epf_wholesale_2", "0.1", {"unit_of_measurement": "EUR/kWh", "raw_today": []})
    hass.states.async_set(PRICE, "1.2", {"unit_of_measurement": "DKK/kWh"})
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energypriceforecast", "energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    assert result["step_id"] == "prices_energypriceforecast"
    sell = {"energypriceforecast_sell_entity_id": "sensor.epf_wholesale"}
    # the sell sensor is required: a retail price is more than a sale pays, so it is not taken for one
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(result["flow_id"], {"energypriceforecast_buy_entity_id": "sensor.epf_retail"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"energypriceforecast_buy_entity_id": "sensor.epf_cents", **sell})
    mismatch = {"energypriceforecast_sell_entity_id": "price_currency_mismatch"}  # the wholesale sell sensor is EUR, Energi Data Service DKK
    assert result["errors"] == {"energypriceforecast_buy_entity_id": "price_unit_subunit", **mismatch}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"energypriceforecast_buy_entity_id": "sensor.epf_wholesale", "energypriceforecast_sell_entity_id": "sensor.epf_wholesale_2"})
    assert result["errors"] == {"energypriceforecast_buy_entity_id": "price_sensor_not_retail", **mismatch}
    # the retail price is more than a sale pays: it is refused as the sell sensor (the sensor error comes before the currency one)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"energypriceforecast_buy_entity_id": "sensor.epf_retail", "energypriceforecast_sell_entity_id": "sensor.epf_retail"}
    )
    assert result["errors"] == {"energypriceforecast_buy_entity_id": "price_currency_mismatch", "energypriceforecast_sell_entity_id": "price_sensor_not_wholesale"}
    # a backup provider in another currency (EUR next to the DKK of Energi Data Service) would be planned as kroner
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"energypriceforecast_buy_entity_id": "sensor.epf_retail", **sell})
    assert result["errors"] == {"energypriceforecast_buy_entity_id": "price_currency_mismatch", **mismatch}


async def test_energy_price_forecast_alone_sets_the_scale_from_its_retail_sensor(hass: HomeAssistant) -> None:
    retail = {"unit_of_measurement": "EUR/kWh", "raw_today": [], "retail_source": "estimate"}
    hass.states.async_set("sensor.epf_retail", "0.2", retail)
    hass.states.async_set("sensor.epf_wholesale", "0.1", {"unit_of_measurement": "EUR/kWh", "raw_today": []})
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energypriceforecast"]})
    result = await _configure(
        hass, result, "prices_energypriceforecast", {"energypriceforecast_buy_entity_id": "sensor.epf_retail", "energypriceforecast_sell_entity_id": "sensor.epf_wholesale"}
    )
    assert result["step_id"] == "price_fees" and form_defaults(result)["sell_fee"] == 0.004
    result = await _configure(hass, result, "price_fees", form_defaults(result))
    result = await _finish_after_the_price_pages(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = result["options"]
    assert options["price_scale"] == 0.134 and options["energidataservice_buy_entity_id"] == ""
    assert options["energypriceforecast_buy_entity_id"] == "sensor.epf_retail" and options["energypriceforecast_sell_entity_id"] == "sensor.epf_wholesale"


async def test_options_flow_finds_the_price_scale_and_keeps_it_without_a_unit(hass: HomeAssistant, loaded_entry) -> None:
    async def save():
        result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_battery"})
        for _ in range(4):
            result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done(wait_background_tasks=True)

    price_entity = loaded_entry.options["energidataservice_buy_entity_id"]
    hass.states.async_set(price_entity, "0.5", {"unit_of_measurement": "GBP/kWh"})
    await save()
    assert loaded_entry.options["price_scale"] == 0.115
    sell_fee, wear = loaded_entry.options["sell_fee"], loaded_entry.options["powerwall_wear_cost_per_kwh"]
    # a sensor without a known unit (not loaded yet at startup): the entry keeps its scale
    hass.states.async_set(price_entity, "unavailable", {})
    await save()
    assert loaded_entry.options["price_scale"] == 0.115
    # an existing entry's amounts are never rescaled
    assert (loaded_entry.options["sell_fee"], loaded_entry.options["powerwall_wear_cost_per_kwh"]) == (sell_fee, wear)


async def test_stromligning_pages(hass: HomeAssistant) -> None:
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["stromligning"]})
    result = await _configure(
        hass, result, "prices_stromligning",
        {"stromligning_buy_current_price": "sensor.stromligning_current_price_vat", "stromligning_sell_current_price": "sensor.stromligning_sell"},
    )
    # the tariff page is only shown with a sell price
    result = await _configure(hass, result, "prices_stromligning_tariffs", {})
    assert result["step_id"] == "price_fees"


@pytest.mark.parametrize(
    ("provider", "step", "sell"),
    [
        ("energidataservice", "prices_energidataservice", {"energidataservice_sell_entity_id": "sensor.eds_sell"}),
        ("stromligning", "prices_stromligning", {"stromligning_sell_current_price": "sensor.stromligning_sell"}),
    ],
)
async def test_a_sell_entity_alone_is_rejected(hass: HomeAssistant, provider: str, step: str, sell: dict) -> None:
    """The planner, what-if and dashboard need the buy entity of every chosen provider."""
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": [provider]})
    with pytest.raises(InvalidData):
        await _configure(hass, result, step, sell)


async def test_weather_page_skipped_without_solar(hass: HomeAssistant) -> None:
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    result = await _configure(hass, result, "price_fees", {"refund": 0.0, "sell_fee": 0.03})
    result = await _configure(hass, result, "solar", {"inverter_discharging_power_limit": 6000, "production_price": -1})
    assert result["step_id"] == "advanced"


async def test_solcast_needs_production(hass: HomeAssistant) -> None:
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    result = await _configure(hass, result, "price_fees", {"refund": 0.0, "sell_fee": 0.03})
    result = await _configure(
        hass, result, "solar",
        {"forecast_entity_id": "sensor.solcast_pv_forecast_forecast_today", "inverter_discharging_power_limit": 6000, "production_price": -1},
    )
    assert result["errors"] == {"production_entity_id": "production_required"}


def test_the_solar_forecast_picker_offers_the_three_integrations() -> None:
    """One filter per integration (the picker offers an entity that matches any of them), still a single sensor picker."""
    from custom_components.battmind.flow.schema import SELECTORS

    config = SELECTORS["forecast_entity_id"].config
    assert [item["integration"] for item in config["filter"]] == ["solcast_solar", "forecast_solar", "helios_forecast"]
    assert all(item["domain"] == ["sensor"] for item in config["filter"]) and not config.get("multiple")
    # the selector does not check the value against the filter: an old Solcast option (and a backup or a PyScript
    # import with one) is still valid, as are the new integrations' sensors
    for value in ("sensor.solcast_pv_forecast_forecast_today", "sensor.helios_forecast_power_now", "sensor.energy_production_today"):
        assert SELECTORS["forecast_entity_id"](value) == value
    # the other pickers are built as before
    assert SELECTORS["energidataservice_buy_entity_id"].config["filter"] == [{"domain": ["sensor"], "integration": "energidataservice"}]
    assert SELECTORS["production_entity_id"].config["filter"] == [{"domain": ["sensor"], "device_class": ["power"]}]


@pytest.mark.parametrize("forecast", ["sensor.helios_forecast_power_now", "sensor.energy_production_today"])
async def test_a_forecast_solar_or_helios_sensor_is_accepted(hass: HomeAssistant, forecast: str) -> None:
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    result = await _configure(hass, result, "price_fees", {"refund": 0.0, "sell_fee": 0.03})
    result = await _configure(
        hass, result, "solar",
        {"production_entity_id": PRODUCTION, "forecast_entity_id": forecast, "inverter_discharging_power_limit": 6000, "production_price": -1},
    )
    assert result["step_id"] == "weather" and not result.get("errors")


async def test_invalid_prefix(hass: HomeAssistant) -> None:
    result = await _start(hass, prefix="1-bad prefix")
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_ENTITY_PREFIX: "invalid_prefix"}


async def test_prefix_in_use(hass: HomeAssistant, mock_entry) -> None:
    mock_entry.add_to_hass(hass)
    result = await _start(hass, prefix=mock_entry.data[CONF_ENTITY_PREFIX])
    assert result["errors"] == {CONF_ENTITY_PREFIX: "prefix_in_use"}


async def test_prefix_taken_by_pyscript(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.bmflow_overview", "PyScript")
    result = await _start(hass)
    assert result["errors"] == {CONF_ENTITY_PREFIX: "prefix_entities_exist"}


async def test_battery_min_above_max(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await _configure(hass, result, "battery_entities", {"powerwall_battery_level_entity_id": BATTERY_LEVEL, "powerwall_watt_flow_entity_id": BATTERY_FLOW})
    result = await _configure(hass, result, "battery_specs", {**BATTERY_SPECS, "powerwall_battery_level_min": 90, "powerwall_battery_level_max": 80})
    assert result["step_id"] == "battery_specs"
    assert result["errors"] == {"powerwall_battery_level_min": "min_above_max"}

    result = await _configure(
        hass,
        result,
        "battery_specs",
        {**BATTERY_SPECS, "battery_temperature_min": 50, "battery_temperature_max": 50},
    )
    assert result["step_id"] == "battery_specs"
    assert result["errors"] == {"battery_temperature_min": "min_above_max"}

    result = await _configure(
        hass,
        result,
        "battery_specs",
        {**BATTERY_SPECS, "battery_charging_efficiency_curve": {"1000": 0}},
    )
    assert result["step_id"] == "battery_specs"
    assert result["errors"] == {"battery_charging_efficiency_curve": "invalid_efficiency_curve"}


async def test_no_price_provider(hass: HomeAssistant) -> None:
    result = await _to_price_provider(hass)
    result = await _configure(hass, result, "price_provider", {"price_providers": []})
    assert result["errors"] == {"base": "no_price_provider"}


async def test_import_legacy_pyscript_config(hass: HomeAssistant, tmp_path: Path) -> None:
    """A PyScript battmind_config.yaml in the config dir is offered and its values prefill the pages."""
    shutil.copy(FIXTURES / "battmind_config.yaml", tmp_path / "battmind_config.yaml")
    hass.config.config_dir = str(tmp_path)
    hass.config.country = "DE"
    result = await _start(hass)
    assert result["step_id"] == "import_legacy"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"import_source": "battmind"})
    assert result["step_id"] == "battery_entities"
    assert form_defaults(result)["powerwall_battery_level_entity_id"] == "sensor.batteries_state_of_capacity"

    # accept every page as prefilled
    while result["type"] is FlowResultType.FORM:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["legacy_prefix"] == "battmind"
    assert result["data"]["initial_settings"]["cheap_price_periods"] == 2
    options = result["options"]
    assert options["powerwall_battery_size"] == 10.0
    assert options["powerwall_charge_discharge_loss"] == 0.25
    assert options["ignore_consumption_from_entity_ids"] == ["sensor.bil_lader_power"]
    assert options["energidataservice_sell_entity_id"] == "sensor.energi_data_service_salg"
    assert options["language"] == "da"
    assert options["price_country"] == "DK"  # an import keeps its own: Denmark, not the country of Home Assistant
    # the removed testing mode in the legacy file (testing_mode: true) is ignored, not imported
    assert "testing_mode" not in options


ENERGY_SOLAR = "sensor.energy_solar_power"
ENERGY_BATTERY = "sensor.energy_battery_power"


async def _energy_dashboard(hass: HomeAssistant) -> None:
    """An Energy dashboard with one solar and one battery source, each with a plain power sensor in W."""
    hass.config.components.add("energy")
    for entity_id in (ENERGY_SOLAR, ENERGY_BATTERY):
        hass.states.async_set(entity_id, "1000", {"unit_of_measurement": "W", "device_class": "power"})
    manager = await async_get_manager(hass)
    manager.data = {
        "energy_sources": [
            {"type": "solar", "stat_energy_from": "sensor.solar_energy", "stat_rate": ENERGY_SOLAR},
            {
                "type": "battery",
                "stat_energy_from": "sensor.battery_in",
                "stat_energy_to": "sensor.battery_out",
                "stat_rate": ENERGY_BATTERY,
            },
        ],
        "device_consumption": [],
    }


def _suggested(result, key: str):
    """The suggested value of a field of the form (what the frontend shows, not a default)."""
    for marker in result["data_schema"].schema:
        if str(marker) == key:
            return (marker.description or {}).get("suggested_value")
    raise AssertionError(f"{key} is not on the form")


async def _to_solar(hass: HomeAssistant, result: dict, battery: str = ENERGY_BATTERY) -> dict:
    """From the battery page (as returned by _start) to the solar page; the battery power sensor is kept as suggested."""
    result = await _configure(
        hass, result, "battery_entities",
        {"powerwall_battery_level_entity_id": BATTERY_LEVEL, "powerwall_watt_flow_entity_id": battery},
    )
    result = await _configure(hass, result, "battery_specs", BATTERY_SPECS)
    result = await _configure(hass, result, "battery_actions", {"unsupported_actions": []})
    result = await _configure(hass, result, "consumption", {"power_consumption_entity_id": CONSUMPTION})
    result = await _configure(hass, result, "detection_review", {"invert_powerwall_watt_flow_entity_id": True})
    result = await _configure(hass, result, "country", {})
    result = await _configure(hass, result, "price_provider", {"price_providers": ["energidataservice"]})
    result = await _configure(hass, result, "prices_energidataservice", {"energidataservice_buy_entity_id": PRICE})
    return await _configure(hass, result, "price_fees", {"refund": 0.0, "sell_fee": 0.03})


async def test_the_energy_dashboard_pre_fills_the_solar_and_battery_power(hass: HomeAssistant) -> None:
    await _energy_dashboard(hass)
    result = await _start(hass)
    assert result["step_id"] == "battery_entities"
    assert _suggested(result, "powerwall_watt_flow_entity_id") == ENERGY_BATTERY
    result = await _to_solar(hass, result)
    assert result["step_id"] == "solar"
    assert _suggested(result, "production_entity_id") == ENERGY_SOLAR


async def test_a_pre_filled_sensor_can_be_cleared_and_stays_empty(hass: HomeAssistant) -> None:
    await _energy_dashboard(hass)
    result = await _to_solar(hass, await _start(hass))
    assert _suggested(result, "production_entity_id") == ENERGY_SOLAR
    # the user clears the solar field: it is left out of the submitted form
    result = await _configure(hass, result, "solar", {"inverter_discharging_power_limit": 6000, "production_price": -1})
    assert result["step_id"] == "advanced"
    result = await _configure(hass, result, "advanced", {"days_to_predict": 3, "cron_interval": "5", "dashboard": False})
    result = await _configure(
        hass, result, "database",
        {
            "power_values_db_data_to_save": 15,
            "solar_available_db_data_to_save": 10,
            "kwh_avg_prices_db_data_to_save": 15,
            "charging_history_db_data_to_save": 12,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["production_entity_id"] == ""
    assert result["options"]["powerwall_watt_flow_entity_id"] == ENERGY_BATTERY


async def test_the_battery_sign_is_still_read_from_the_recorder_for_a_pre_filled_sensor(hass: HomeAssistant, monkeypatch) -> None:
    await _energy_dashboard(hass)
    calls = []

    async def fake_detect(hass, consumption, power, level, trusted=None, now=None, ignored=()):
        calls.append((consumption, power, level, trusted))
        return Detection()

    monkeypatch.setattr("custom_components.battmind.flow.pages.async_detect", fake_detect)
    await _to_solar(hass, await _start(hass))
    # the history is looked at for the pre-filled battery sensor, and nothing is trusted from before (the sign is unknown)
    assert calls == [(CONSUMPTION, ENERGY_BATTERY, BATTERY_LEVEL, None)]


async def test_the_pyscript_import_wins_over_the_energy_dashboard(hass: HomeAssistant, tmp_path: Path) -> None:
    shutil.copy(FIXTURES / "battmind_config.yaml", tmp_path / "battmind_config.yaml")
    hass.config.config_dir = str(tmp_path)
    await _energy_dashboard(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"import_source": "battmind"})
    assert result["step_id"] == "battery_entities"
    assert _suggested(result, "powerwall_watt_flow_entity_id") == "sensor.batteries_charge_discharge_power"
    while result["type"] is FlowResultType.FORM:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"]["production_entity_id"] == "sensor.inverter_input_power"
    assert result["options"]["powerwall_watt_flow_entity_id"] == "sensor.batteries_charge_discharge_power"


BACKUP_BATTERY = "sensor.backup_battery_power"


def _write_backup(config_dir: Path, options: dict) -> str:
    """A BattMind backup in the backup folder of the test config; returns its file name."""
    folder = config_dir / BACKUP_DIR
    folder.mkdir(parents=True, exist_ok=True)
    backup = {"integration": DOMAIN, "title": "BattMind test", "created": "2026-11-01T10:00:00+01:00", "options": options, "databases": {}}
    (folder / "battmind_backup_test.json").write_text(json.dumps(backup), encoding="utf-8")
    return "battmind_backup_test.json"


async def _finish_without_solar(hass: HomeAssistant, result: dict) -> dict:
    """From the solar page to the end, with the solar sensor left out (the user clears the field)."""
    result = await _configure(hass, result, "solar", {"inverter_discharging_power_limit": 6000, "production_price": -1})
    result = await _configure(hass, result, "advanced", {"days_to_predict": 3, "cron_interval": "5", "dashboard": False})
    return await _configure(
        hass, result, "database",
        {
            "power_values_db_data_to_save": 15,
            "solar_available_db_data_to_save": 10,
            "kwh_avg_prices_db_data_to_save": 15,
            "charging_history_db_data_to_save": 12,
        },
    )


async def test_a_restored_backup_wins_over_the_energy_dashboard(hass: HomeAssistant, tmp_path: Path) -> None:
    """The backup replaces the options after the pre-fill: its empty solar field stays empty, its battery sensor is kept."""
    hass.config.config_dir = str(tmp_path)
    await _energy_dashboard(hass)
    name = _write_backup(tmp_path, {"production_entity_id": "", "powerwall_watt_flow_entity_id": BACKUP_BATTERY})
    result = await _start(hass)
    assert result["step_id"] == "restore_backup"
    result = await _configure(hass, result, "restore_backup", {"backup": name})
    assert result["step_id"] == "battery_entities"
    assert _suggested(result, "powerwall_watt_flow_entity_id") == BACKUP_BATTERY
    result = await _to_solar(hass, result, battery=BACKUP_BATTERY)
    assert result["step_id"] == "solar"
    assert _suggested(result, "production_entity_id") in (None, "")
    result = await _finish_without_solar(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["restore_backup"] == name
    assert result["options"]["production_entity_id"] == ""
    assert result["options"]["powerwall_watt_flow_entity_id"] == BACKUP_BATTERY


async def test_options_flow_never_changes_the_existing_sensors(hass: HomeAssistant, loaded_entry) -> None:
    await _energy_dashboard(hass)
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_battery"})
    assert result["step_id"] == "battery_entities"
    assert _suggested(result, "powerwall_watt_flow_entity_id") == BATTERY_FLOW
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "battery_specs"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "detection_review"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded_entry.options["powerwall_watt_flow_entity_id"] == BATTERY_FLOW
    assert loaded_entry.options["production_entity_id"] == PRODUCTION


def test_removed_testing_mode_is_ignored() -> None:
    """A testing_mode left in stored options (or the legacy file) changes nothing: no marker, no config key."""
    config = build_config({**default_options(), "testing_mode": True})
    assert "testing_mode" not in config
    assert "testing_mode" not in default_options()
    assert format_charging_rule("  Charge \n\nSell  ") == "Charge\nSell"


async def test_options_flow_changes_battery_size(hass: HomeAssistant, loaded_entry) -> None:
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert "menu_battery" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_battery"})
    assert result["step_id"] == "battery_entities"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "battery_specs"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form_defaults(result), "powerwall_battery_size": 20.0})
    assert result["step_id"] == "battery_actions"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "detection_review"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded_entry.options["powerwall_battery_size"] == 20.0
    # the update listener reloads the entry with the new configuration
    assert loaded_entry.runtime_data.config["solar"]["powerwall_battery_size"] == 20.0


async def test_options_flow_drops_a_stored_testing_mode(hass: HomeAssistant, loaded_entry) -> None:
    """A removed option that the stored options still hold (old backup) is not saved again."""
    hass.config_entries.async_update_entry(loaded_entry, options={**loaded_entry.options, "testing_mode": True})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_battery"})
    for _ in range(4):  # battery_entities, battery_specs, battery_actions, detection_review
        result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "testing_mode" not in result["data"]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "testing_mode" not in loaded_entry.options


async def test_options_flow_switches_off_the_rules_of_an_unsupported_action(hass: HomeAssistant, loaded_entry) -> None:
    """The rules of a battery action the inverter can't run count as off; their switches keep their value."""
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_battery"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "battery_actions"
    assert form_defaults(result) == {"unsupported_actions": []}
    # only the controllable actions can be chosen
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(result["flow_id"], {"unsupported_actions": ["discharge_allowed"]})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"unsupported_actions": ["force_discharge"]})
    assert result["step_id"] == "detection_review"
    result = await hass.config_entries.options.async_configure(result["flow_id"], form_defaults(result))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded_entry.options["unsupported_actions"] == ["force_discharge"]
    prefix = loaded_entry.data[CONF_ENTITY_PREFIX]
    most_profit = hass.states.get(f"switch.{prefix}_most_profit")
    assert most_profit.attributes["unsupported_action"] == "force_discharge"
    assert "unsupported_action" not in hass.states.get(f"switch.{prefix}_cheapest_hour_fill_planner").attributes


async def test_options_flow_dashboard_icon(hass: HomeAssistant, loaded_entry) -> None:
    async def open_general():
        result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "menu_general"})
        assert result["step_id"] == "general"
        return result

    result = await open_general()
    assert form_defaults(result)["dashboard_icon"] == "mdi:home-battery"

    # not an icon name: rejected, the page is shown again
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form_defaults(result), "dashboard_icon": "battery"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"dashboard_icon": "invalid_icon"}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {**form_defaults(result), "dashboard_icon": " mdi:solar-power "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded_entry.options["dashboard_icon"] == "mdi:solar-power"

    # a cleared field is left out by the frontend (an empty string also counts): both mean the default icon
    result = await open_general()
    assert form_defaults(result)["dashboard_icon"] == "mdi:solar-power"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {k: v for k, v in form_defaults(result).items() if k != "dashboard_icon"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert loaded_entry.options["dashboard_icon"] == "mdi:home-battery"


async def test_config_flow_rejects_an_invalid_dashboard_icon(hass: HomeAssistant) -> None:
    result = await _start(hass, dashboard_icon="battery")
    assert result["step_id"] == "user"
    assert result["errors"] == {"dashboard_icon": "invalid_icon"}
    # the icon is optional: left out it is the default one
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ENTITY_PREFIX: "bmflow", "language": "da"})
    assert result["step_id"] == "battery_entities"
