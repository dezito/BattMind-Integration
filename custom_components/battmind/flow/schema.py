"""Selectors, schema building and validation of the config flow pages."""
from __future__ import annotations

from typing import Any
import re

import voluptuous as vol

from homeassistant.helpers import selector

from ..const import (
    CONTROLLABLE_ACTIONS,
    DASHBOARD_ICON_RE,
    DEFAULT_DASHBOARD_ICON,
    ENTITY_OPTION_KEYS,
    LANGUAGES,
    OPTION_PATHS,
    currency_symbol,
    dashboard_icon,
    default_options,
    normalize_language,
)
from ..utils.helpers import get_dict_value_with_path
from ..utils.efficiency import parse_efficiency_curve
from ..utils.solar_forecast import KEY_SEMANTICS
from ..utils.solcast import SOLCAST_PLATFORM


PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]{0,49}$")
IMPORT_NONE = "none"
BACKUP_EXPORT = "export"
CRON_INTERVALS = ["1", "2", "3", "4", "5", "6", "10", "12", "15", "20", "30"]
PRICE_PROVIDERS = ["energidataservice", "stromligning", "energypriceforecast", "spotforecast", "elprognos", "wattcast", "epexspot", "gespot", "octopus", "nordpool"]
# the names of the providers (proper names, not translated): the price provider page lists the ones a country does not have
PRICE_PROVIDER_NAMES = {
    "energidataservice": "Energi Data Service",
    "stromligning": "Strømligning",
    "energypriceforecast": "Energy Price Forecast",
    "spotforecast": "Spot Forecast",
    "elprognos": "Elprisprognos",
    "wattcast": "Wattcast",
    "epexspot": "EPEX Spot",
    "gespot": "GE-Spot",
    "octopus": "Octopus Energy",
    "nordpool": "Nord Pool",
}
# a provider not confirmed in the country is listed under this value, translated as its name with 🧪
UNCONFIRMED_SUFFIX = "_unconfirmed"


def provider_of(value: str) -> str:
    """The provider name of a value of the price provider list."""
    return value.removesuffix(UNCONFIRMED_SUFFIX)


class ProviderSelector(selector.SelectSelector):
    """The list of price providers (or solar forecast sources): a value is the name, with UNCONFIRMED_SUFFIX where it is not
    confirmed (a price provider per country, a solar source in general).

    The frontend takes the label of a choice from the translation of its value, in the user's own language, and the same
    provider can be confirmed in one country and not in another. A plain provider name is accepted too and becomes the
    listed value; only provider_of() of a value is stored."""

    def __call__(self, data: Any) -> Any:
        listed = {provider_of(value): value for value in self.config["options"]}
        if isinstance(data, str):
            data = listed.get(data, data)
        elif isinstance(data, list):
            data = [listed.get(value, value) if isinstance(value, str) else value for value in data]
        return super().__call__(data)


# the providers of wholesale prices: the VAT and the surcharge on the fee page are for them
SPOT_PRICE_PROVIDERS = ("spotforecast", "elprognos", "wattcast")
ENERGIDATASERVICE_URL = "https://github.com/MTrab/energidataservice"  # {eds_url} in the step descriptions
CONFIG_FLOW_STEPS = [
    "battery_entities",
    "battery_specs",
    "battery_actions",
    "consumption",
    "detection_review",
    "country",
    "price_provider",
    "prices_energidataservice",
    "prices_energypriceforecast",
    "prices_spotforecast",
    "prices_elprognos",
    "prices_wattcast",
    "prices_epexspot",
    "prices_gespot",
    "prices_octopus",
    "prices_nordpool",
    "prices_stromligning",
    "prices_stromligning_tariffs",
    "price_fees",
    "solar_forecast_source",
    "solar",
    "weather",
    "advanced",
    "database",
]
LEGACY_SETTING_PATHS = {
    "cheap_price_periods": "prices.cheap_price_periods",
    "cheap_price_period_rise_threshold": "prices.cheap_price_period_rise_threshold",
    "cheapest_price_rise_threshold": "prices.cheapest_price_rise_threshold",
}


def forecast_selector(source: str) -> selector.EntitySelector:
    """The picker of the forecast sensor of one source."""
    return _entity("sensor", integration=source)


def _entity(
    domain: str | list[str],
    *,
    device_class: str | None = None,
    integration: str | tuple[str, ...] | None = None,
    multiple: bool = False,
) -> selector.EntitySelector:
    """Entity picker limited to the relevant entities (domain + device class or integration).

    Several integrations give one filter each: the picker offers an entity that matches any of them.
    """
    entity_filter: dict[str, Any] = {"domain": domain}
    if device_class:
        entity_filter["device_class"] = device_class
    integrations = (integration,) if isinstance(integration, str) else integration or ()
    filters = [{**entity_filter, "integration": name} for name in integrations] or [entity_filter]
    return selector.EntitySelector(selector.EntitySelectorConfig(filter=filters, multiple=multiple))


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> selector.NumberSelector:
    config: dict[str, Any] = {"min": minimum, "max": maximum, "step": step, "mode": selector.NumberSelectorMode.BOX}
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**config))


BOOLEAN = selector.BooleanSelector()
OBJECT = selector.ObjectSelector()
SELECTORS: dict[str, Any] = {
    "price_country": selector.CountrySelector(),
    "language": selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=code, label=label) for code, label in LANGUAGES.items()],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    ),
    # home battery
    "powerwall_battery_level_entity_id": _entity("sensor", device_class="battery"),
    "powerwall_watt_flow_entity_id": _entity("sensor", device_class="power"),
    "battery_temperature_entity_id": _entity("sensor", device_class="temperature"),
    "invert_powerwall_watt_flow_entity_id": BOOLEAN,
    "powerwall_battery_size": _number(0.1, 1000, 0.1, "kWh"),
    "powerwall_battery_level_min": _number(0, 100, 1, "%"),
    "powerwall_battery_level_max": _number(0, 100, 1, "%"),
    "battery_temperature_min": _number(-50, 100, 1, "°C"),
    "battery_temperature_max": _number(-50, 100, 1, "°C"),
    "powerwall_charging_power_limit": _number(100, 100000, 100, "W"),
    "powerwall_discharging_power": _number(100, 100000, 100, "W"),
    "powerwall_charge_discharge_loss": _number(0, 0.9, 0.01),
    "use_charge_discharge_loss_from_history": BOOLEAN,
    "battery_charging_efficiency_curve": OBJECT,
    "battery_discharging_efficiency_curve": OBJECT,
    "powerwall_wear_cost_per_kwh": _number(0, 10, 0.001, "{valuta_kwh}"),  # 0.001: 0.1 kroner in euro is 0.013
    "unsupported_actions": selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(CONTROLLABLE_ACTIONS),
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
            translation_key="unsupported_actions",
        )
    ),
    # consumption
    "power_consumption_entity_id": _entity("sensor", device_class="power"),
    "power_consumption_entity_id_include_powerwall_charging": BOOLEAN,
    "power_consumption_entity_id_include_powerwall_discharging": BOOLEAN,
    "ignore_consumption_from_entity_ids": _entity("sensor", device_class="power", multiple=True),
    "ignore_cjp_chargers": BOOLEAN,
    # prices (only entities from the price integrations)
    "energidataservice_buy_entity_id": _entity("sensor", integration="energidataservice"),
    "energidataservice_sell_entity_id": _entity("sensor", integration="energidataservice"),
    "energypriceforecast_buy_entity_id": _entity("sensor", integration="energypriceforecast"),
    "energypriceforecast_sell_entity_id": _entity("sensor", integration="energypriceforecast"),
    "spotforecast_buy_entity_id": _entity("sensor", integration="spot_forecast"),
    "elprognos_buy_entity_id": _entity("sensor", integration="elprisprognos"),
    "wattcast_buy_entity_id": _entity("sensor", integration="wattcast"),
    "wattcast_forecast_entity_id": _entity("sensor", integration="wattcast"),
    "epexspot_buy_entity_id": _entity("sensor", integration="epex_spot"),
    "epexspot_sell_entity_id": _entity("sensor", integration="epex_spot"),
    "gespot_buy_entity_id": _entity("sensor", integration="ge_spot"),
    "octopus_buy_entity_id": _entity("event", integration="octopus_energy"),
    "octopus_buy_next_day_entity_id": _entity("event", integration="octopus_energy"),
    "octopus_sell_entity_id": _entity("event", integration="octopus_energy"),
    "octopus_sell_next_day_entity_id": _entity("event", integration="octopus_energy"),
    "nordpool_buy_entity_id": _entity("sensor", integration="nordpool"),
    "nordpool_sell_entity_id": _entity("sensor", integration="nordpool"),
    "stromligning_buy_current_price": _entity("sensor", integration="stromligning"),
    "stromligning_buy_tomorrow_available": _entity("binary_sensor", integration="stromligning"),
    "stromligning_buy_forecasts": _entity("sensor", integration="stromligning"),
    "stromligning_sell_current_price": _entity("sensor", integration="stromligning"),
    "stromligning_sell_tomorrow_available": _entity("binary_sensor", integration="stromligning"),
    "stromligning_sell_forecasts": _entity("sensor", integration="stromligning"),
    "stromligning_sell_distribution": _entity("sensor", integration="stromligning"),
    "stromligning_sell_electricity_tax": _entity("sensor", integration="stromligning"),
    "stromligning_sell_nettariff": _entity("sensor", integration="stromligning"),
    "stromligning_sell_systemtariff": _entity("sensor", integration="stromligning"),
    "stromligning_sell_surcharge": _entity("sensor", integration="stromligning"),
    "refund": _number(0, 10, 0.001, "{valuta_kwh}"),
    "sell_fee": _number(0, 10, 0.001, "{valuta_kwh}"),
    "spot_vat": _number(0, 50, 0.1, "%"),
    "spot_surcharge": _number(0, 10, 0.001, "{valuta_kwh}"),
    # solar / weather
    "production_entity_id": _entity("sensor", device_class="power"),
    "inverter_discharging_power_limit": _number(100, 100000, 100, "W"),
    "production_price": _number(-1, 10, 0.01, "{valuta_kwh}"),
    # Solcast (its detailedHourly), or Forecast.Solar / Helios Forecast (their solar forecast for the Energy dashboard)
    "forecast_entity_id": _entity("sensor", integration=(SOLCAST_PLATFORM, *KEY_SEMANTICS)),
    "hourly_service_entity_id": _entity("weather"),
    "daily_service_entity_id": _entity("weather"),
    # storm warning: MeteoAlarm's binary sensor (device class safety), and the lowest level that counts
    "storm_warning_entity_id": _entity("binary_sensor", device_class="safety"),
    "storm_min_level": _number(2, 4, 1),
    # push notifications: the notify services and entities (the options are made at the page, build_schema), what to send, the hour
    "notify_list": selector.SelectSelector(selector.SelectSelectorConfig(options=[], multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)),
    "notify_problems": BOOLEAN,
    "notify_storm": BOOLEAN,
    "notify_learning": BOOLEAN,
    "notify_summary": BOOLEAN,
    "notify_summary_hour": _number(0, 23, 1, "h"),
    # advanced / database
    "cron_interval": selector.SelectSelector(
        selector.SelectSelectorConfig(options=CRON_INTERVALS, mode=selector.SelectSelectorMode.DROPDOWN)
    ),
    "days_to_predict": _number(1, 7, 1, "d"),
    "dashboard": BOOLEAN,
    "dashboard_icon": selector.IconSelector(selector.IconSelectorConfig(placeholder=DEFAULT_DASHBOARD_ICON)),
    "enable_diagnostic_file_log": BOOLEAN,
    "power_values_db_data_to_save": _number(1, 100, 1, "d"),
    "solar_available_db_data_to_save": _number(1, 100, 1),
    "kwh_avg_prices_db_data_to_save": _number(1, 100, 1, "d"),
    "charging_history_db_data_to_save": _number(1, 60, 1),  # months, see the field description
}
REQUIRED_KEYS = {
    "language",
    "price_country",
    "powerwall_battery_level_entity_id",
    "powerwall_watt_flow_entity_id",
    "power_consumption_entity_id",
    "powerwall_battery_size",
    "powerwall_battery_level_min",
    "powerwall_battery_level_max",
    "battery_temperature_min",
    "battery_temperature_max",
    "powerwall_charging_power_limit",
    "powerwall_discharging_power",
    "powerwall_charge_discharge_loss",
    "powerwall_wear_cost_per_kwh",
    "energidataservice_buy_entity_id",
    "energypriceforecast_buy_entity_id",
    "energypriceforecast_sell_entity_id",
    "spotforecast_buy_entity_id",
    "elprognos_buy_entity_id",
    "wattcast_buy_entity_id",
    "wattcast_forecast_entity_id",
    "epexspot_buy_entity_id",
    "epexspot_sell_entity_id",
    "gespot_buy_entity_id",
    "octopus_buy_entity_id",
    "octopus_buy_next_day_entity_id",
    "nordpool_buy_entity_id",
    "nordpool_sell_entity_id",
    "spot_vat",
    "spot_surcharge",
    "stromligning_buy_current_price",
    "refund",
    "sell_fee",
    "inverter_discharging_power_limit",
    "production_price",
    "days_to_predict",
    "cron_interval",
    "power_values_db_data_to_save",
    "solar_available_db_data_to_save",
    "kwh_avg_prices_db_data_to_save",
    "charging_history_db_data_to_save",
    "notify_summary_hour",
}
# the PyScript version had a notify_list of its own: the push notifications of this one are not turned on by an import (they are
# chosen on the page Push notifications)
NOT_IMPORTED_OPTIONS = {"notify_list", "notify_problems", "notify_storm", "notify_learning", "notify_summary", "notify_summary_hour"}
LIST_KEYS = {"ignore_consumption_from_entity_ids", "unsupported_actions", "notify_list"}
INT_KEYS = {
    "storm_min_level",
    "notify_summary_hour",
    "days_to_predict",
    "cron_interval",
    "power_values_db_data_to_save",
    "solar_available_db_data_to_save",
    "kwh_avg_prices_db_data_to_save",
    "charging_history_db_data_to_save",
}


def _form_value(key: str, value: Any) -> Any:
    if key == "cron_interval":
        return str(int(value)) if value not in (None, "") else "5"
    if key in LIST_KEYS:
        return [value] if isinstance(value, str) and value else list(value or [])
    return value


def _selector(key: str, currency: str | None) -> Any:
    """SELECTORS[key] with {valuta_kwh} in the unit replaced by the currency of Home Assistant."""
    field = SELECTORS[key]
    if isinstance(field, selector.NumberSelector) and "{valuta_kwh}" in str(field.config.get("unit_of_measurement")):
        unit = field.config["unit_of_measurement"].replace("{valuta_kwh}", f"{currency_symbol(currency) or 'kr'}/kWh")
        return selector.NumberSelector(selector.NumberSelectorConfig(**{**field.config, "unit_of_measurement": unit}))
    return field


def build_schema(keys: list[str], values: dict[str, Any], currency: str | None = None, notify_services: list[str] | None = None) -> vol.Schema:
    """notify_services: the notify services and entities to offer on the notifications page ("notify.<name>"); what is chosen
    already stays in the list, also when its service is not registered at the moment (the mobile app registers it late)."""
    schema: dict[Any, Any] = {}
    for key in keys:
        value = _form_value(key, values.get(key))
        field = _selector(key, currency)
        if key in ENTITY_OPTION_KEYS:
            marker = vol.Required if key in REQUIRED_KEYS else vol.Optional
            schema[marker(key, description={"suggested_value": value or None})] = field
        elif key == "ignore_consumption_from_entity_ids":
            schema[vol.Optional(key, description={"suggested_value": value})] = field
        elif key == "unsupported_actions":
            schema[vol.Optional(key, default=value)] = field
        elif key == "notify_list":
            options = sorted({*(notify_services or []), *(str(item) for item in value)})
            schema[vol.Optional(key, description={"suggested_value": list(value)})] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)
            )
        elif key in REQUIRED_KEYS:
            # no value yet (the VAT and the surcharge of the wholesale prices have none to suggest): a required field without a default
            schema[vol.Required(key) if value is None else vol.Required(key, default=value)] = field
        elif field is OBJECT:
            schema[vol.Optional(key, default=value or {})] = field
        elif key == "dashboard_icon":
            # no default: a cleared field is left out by the frontend, and a default would bring back the old icon
            schema[vol.Optional(key, description={"suggested_value": value or dashboard_icon(None)})] = field
        else:
            schema[vol.Optional(key, default=bool(value))] = field
    return vol.Schema(schema)


def normalize_input(keys: list[str], user_input: dict[str, Any]) -> dict[str, Any]:
    """Convert form values to option values (cleared optional entities become '')."""
    result: dict[str, Any] = {}
    for key in keys:
        value = user_input.get(key)
        if key in ENTITY_OPTION_KEYS:
            result[key] = value or ""
        elif key == "ignore_consumption_from_entity_ids":
            result[key] = list(value or [])
        elif key == "unsupported_actions":
            result[key] = [action for action in CONTROLLABLE_ACTIONS if action in (value or [])]
        elif key == "notify_list":
            result[key] = [str(item) for item in (value or []) if str(item).strip()]
        elif key in INT_KEYS:
            result[key] = int(float(value))
        elif key == "dashboard_icon":
            # a cleared field means the default icon; an invalid name is kept so validate_step can reject it
            result[key] = str(value or "").strip() or dashboard_icon(None)
        elif SELECTORS[key] is BOOLEAN:
            result[key] = bool(value)
        elif isinstance(value, (int, float)):
            result[key] = float(value)
        else:
            result[key] = value
    return result


def validate_step(step: str, values: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if step == "battery_specs" and values["powerwall_battery_level_min"] >= values["powerwall_battery_level_max"]:
        errors["powerwall_battery_level_min"] = "min_above_max"
    if step == "battery_specs" and values["battery_temperature_min"] >= values["battery_temperature_max"]:
        errors["battery_temperature_min"] = "min_above_max"
    if step == "battery_specs":
        for key in ("battery_charging_efficiency_curve", "battery_discharging_efficiency_curve"):
            try:
                parse_efficiency_curve(values.get(key))
            except ValueError:
                errors[key] = "invalid_efficiency_curve"
    if "dashboard_icon" in values and not DASHBOARD_ICON_RE.match(values["dashboard_icon"]):
        errors["dashboard_icon"] = "invalid_icon"
    if step == "solar" and values.get("forecast_entity_id") and not values.get("production_entity_id"):
        errors["production_entity_id"] = "production_required"
    return errors


def options_from_legacy(legacy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Flatten a PyScript <prefix>_config.yaml into config entry options + initial entity values."""
    options = default_options()
    for key, path in OPTION_PATHS.items():
        value = get_dict_value_with_path(legacy, path)
        if value is None or key in NOT_IMPORTED_OPTIONS:
            continue
        if key in ENTITY_OPTION_KEYS:
            options[key] = value or ""
        elif key == "ignore_consumption_from_entity_ids":
            options[key] = [e for e in value if e] if isinstance(value, list) else []
        elif key in INT_KEYS:
            options[key] = int(value)
        else:
            options[key] = value
    options["language"] = normalize_language(options.get("language"), "da")
    if str(options.get("cron_interval")) not in CRON_INTERVALS:
        options["cron_interval"] = 5
    settings = {}
    for key, path in LEGACY_SETTING_PATHS.items():
        value = get_dict_value_with_path(legacy, path)
        if isinstance(value, (int, float)):
            settings[key] = value
    return options, settings
