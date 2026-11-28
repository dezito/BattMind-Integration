"""Setup pages that must never trap the user, mislead them or relabel what they have."""
from __future__ import annotations

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

from custom_components.battmind.utils.solcast import SOLCAST_UNIQUE_IDS, solcast_endings_for_days

from .conftest import PRICE, form_defaults, providers_input
from .test_config_flow import _to_price_provider

BASE = "sensor.solcast_pv_forecast_forecast"


def _register(hass: HomeAssistant, platform: str, unique_id: str, entity_id: str, disabled: bool = False) -> None:
    er.async_get(hass).async_get_or_create(
        "sensor", platform, unique_id, suggested_object_id=entity_id.split(".", 1)[1],
        disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
    )


def _solcast(hass: HomeAssistant, disabled: tuple[str, ...] = ()) -> None:
    for ending in solcast_endings_for_days(7):
        _register(hass, "solcast_solar", SOLCAST_UNIQUE_IDS[ending], f"{BASE}_{ending}", ending in disabled)
        if ending not in disabled:
            hass.states.async_set(f"{BASE}_{ending}", "1", {"detailedHourly": []})


async def _menu(hass: HomeAssistant, entry, group: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": f"menu_{group}"})


@pytest.mark.parametrize(("provider", "sensors", "missing", "other"), [
    ("stromligning", {}, "stromligning_buy_current_price", "energidataservice"),
    ("energidataservice", {}, "energidataservice_buy_entity_id", "energypriceforecast"),
    # one required sensor of two: the other may not exist at all
    ("energypriceforecast", {"energypriceforecast_buy_entity_id": "sensor.epf"}, "energypriceforecast_sell_entity_id", "energidataservice"),
])
async def test_a_price_page_sent_twice_without_its_sensor_leads_back_to_the_providers(
    hass: HomeAssistant, loaded_entry, provider, sensors, missing, other
) -> None:
    """An integration that is not set up leaves the picker empty, and a flow has no back button."""
    options = hass.config_entries.options
    result = await _menu(hass, loaded_entry, "prices")
    result = await options.async_configure(result["flow_id"], providers_input(provider))
    # keeping the provider after going back never finishes: its page again, which says so again before it goes back
    for _ in range(2):
        assert result["step_id"].startswith(f"prices_{provider}"), result
        # the first time the page stays: a forgotten field costs nothing, and the field says how to leave
        result = await options.async_configure(result["flow_id"], sensors)
        assert result["step_id"].startswith(f"prices_{provider}") and result["errors"] == {missing: "price_sensor_required"}, result
        result = await options.async_configure(result["flow_id"], sensors)
        assert result["step_id"] == "price_provider" and result["errors"] == {"base": "price_sensor_not_chosen"}, result
        result = await options.async_configure(result["flow_id"], providers_input(provider))
    result = await options.async_configure(result["flow_id"], sensors)
    result = await options.async_configure(result["flow_id"], sensors)
    # another choice goes on through its own price page
    result = await options.async_configure(result["flow_id"], providers_input(other))
    assert result["step_id"] == f"prices_{other}", result


async def test_a_sensor_forgotten_once_is_chosen_on_the_same_page(hass: HomeAssistant, loaded_entry) -> None:
    options = hass.config_entries.options
    result = await _menu(hass, loaded_entry, "prices")
    result = await options.async_configure(result["flow_id"], providers_input("energidataservice"))
    result = await options.async_configure(result["flow_id"], {})
    result = await options.async_configure(result["flow_id"], {"energidataservice_buy_entity_id": PRICE})
    assert result["step_id"] == "price_fees" and not result.get("errors"), result


async def test_another_error_on_the_page_keeps_it(hass: HomeAssistant, loaded_entry) -> None:
    hass.states.async_set("sensor.epf_cents", "20", {"unit_of_measurement": "ct/kWh", "raw_today": [], "retail_source": "estimate"})
    options = hass.config_entries.options
    result = await _menu(hass, loaded_entry, "prices")
    result = await options.async_configure(result["flow_id"], providers_input("energypriceforecast"))
    for _ in range(2):
        result = await options.async_configure(result["flow_id"], {"energypriceforecast_buy_entity_id": "sensor.epf_cents"})
        assert result["step_id"] == "prices_energypriceforecast", result
        assert result["errors"]["energypriceforecast_sell_entity_id"] == "price_sensor_required", result
        assert result["errors"]["energypriceforecast_buy_entity_id"] == "price_unit_subunit", result


async def test_a_new_setup_goes_back_from_a_price_page_and_still_walks_every_page(hass: HomeAssistant) -> None:
    flow = hass.config_entries.flow
    result = await _to_price_provider(hass, price_country="DK")
    result = await flow.async_configure(result["flow_id"], providers_input("stromligning"))
    result = await flow.async_configure(result["flow_id"], {})
    result = await flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "price_provider" and result["errors"], result
    # keeping the provider leads to its page again: the setup never goes past a required sensor
    result = await flow.async_configure(result["flow_id"], providers_input("stromligning"))
    assert result["step_id"].startswith("prices_stromligning"), result
    result = await flow.async_configure(result["flow_id"], {})
    result = await flow.async_configure(result["flow_id"], {})
    result = await flow.async_configure(result["flow_id"], providers_input("energidataservice"))
    assert result["step_id"] == "prices_energidataservice", result
    result = await flow.async_configure(result["flow_id"], {"energidataservice_buy_entity_id": PRICE})
    assert result["step_id"] == "price_fees", result
    result = await flow.async_configure(result["flow_id"], form_defaults(result))
    assert result["step_id"] == "solar_forecast_source", result  # the pages after the prices are all still there


async def _solar_page(hass: HomeAssistant, entry, forecast: str, source: str = "solcast_solar"):
    hass.config_entries.async_update_entry(entry, options={**entry.options, "forecast_entity_id": forecast, "production_entity_id": "sensor.pv"})
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await _menu(hass, entry, "solar")
    return result if source is None else await hass.config_entries.options.async_configure(result["flow_id"], {"solar_forecast_source": source})


async def test_a_sensor_chosen_on_the_ready_page_is_judged_too_and_shown_once(hass: HomeAssistant, loaded_entry) -> None:
    _solcast(hass)
    _register(hass, "solcast_solar", "api_used", "sensor.solcast_api_used")
    options = hass.config_entries.options
    result = await _solar_page(hass, loaded_entry, "")
    assert result["step_id"] == "solar_solcast_ready", result
    values = {**form_defaults(result), "forecast_entity_id": "sensor.solcast_api_used", "production_entity_id": "sensor.pv"}
    result = await options.async_configure(result["flow_id"], values)
    assert result["step_id"] == "solar_solcast" and result["errors"] == {"base": "solcast_check_again"}, result
    assert result["description_placeholders"]["solcast_problems"]
    # it informs, it does not block: the same choice again goes on
    result = await options.async_configure(result["flow_id"], values)
    assert result.get("step_id") not in ("solar_solcast", "solar_solcast_ready") and not result.get("errors"), result


@pytest.mark.parametrize("platform", [None, "template"])
async def test_a_stored_sensor_of_no_forecast_source_preselects_nothing(hass: HomeAssistant, loaded_entry, platform) -> None:
    """A guess would label the sensor with a forecast it is not from."""
    if platform:
        _register(hass, platform, "other", "sensor.restored_forecast")
    result = await _solar_page(hass, loaded_entry, "sensor.restored_forecast", source=None)
    assert result["step_id"] == "solar_forecast_source_unknown", result
    assert result["description_placeholders"]["stored_sensor"] == "sensor.restored_forecast"
    key = next(key for key in result["data_schema"].schema if str(key) == "solar_forecast_source")
    assert isinstance(key, vol.Required) and key.default is vol.UNDEFINED
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"solar_forecast_source": "solcast_solar"})
    suggested = [(key.description or {}).get("suggested_value") for key in result["data_schema"].schema if str(key) == "forecast_entity_id"]
    assert suggested == ([None] if platform else ["sensor.restored_forecast"])  # an unknown integration's sensor is never cleared


async def test_the_source_of_the_check_is_the_platform_the_rows_are_read_by(hass: HomeAssistant, loaded_entry, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both readers use the platform cached at the start: the registry may say something else by now."""
    coordinator = loaded_entry.runtime_data
    solar = coordinator.config["solar"]["entity_ids"]
    stored = {key: solar[key] for key in ("forecast_entity_id", "production_entity_id")}
    solar.update(forecast_entity_id="sensor.late_forecast", production_entity_id="sensor.pv")
    try:
        _register(hass, "forecast_solar", "late", "sensor.late_forecast")  # after the start: nothing reads it
        assert (await coordinator._async_snapshot(include_forecast=True)).solar_source == ""
        monkeypatch.setitem(coordinator._entity_platforms, "sensor.late_forecast", "solcast_solar")
        assert (await coordinator._async_snapshot(include_forecast=True)).solar_source == "solcast_solar"
    finally:
        solar.update(stored)


async def test_all_seven_days_are_judged_while_the_days_to_plan_are_still_to_be_chosen(hass: HomeAssistant, loaded_entry) -> None:
    _solcast(hass, disabled=("day_5",))
    # the options menu: the stored days count
    hass.config_entries.async_update_entry(loaded_entry, options={**loaded_entry.options, "days_to_predict": 3})
    result = await _solar_page(hass, loaded_entry, "")
    assert result["step_id"] == "solar_solcast_ready", result
    # a new setup chooses them on a later page: a restored backup's fewer days may still be raised there
    flow = hass.config_entries.flow
    result = await _to_price_provider(hass, price_country="DK")
    result = await flow.async_configure(result["flow_id"], providers_input("energidataservice"))
    result = await flow.async_configure(result["flow_id"], {"energidataservice_buy_entity_id": PRICE})
    result = await flow.async_configure(result["flow_id"], form_defaults(result))
    flow._progress[result["flow_id"]]._options["days_to_predict"] = 3
    result = await flow.async_configure(result["flow_id"], {"solar_forecast_source": "solcast_solar"})
    assert result["step_id"] == "solar_solcast" and "day_5" in result["description_placeholders"]["solcast_problems"], result
