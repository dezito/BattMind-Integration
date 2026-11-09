"""BattMind - home battery planner for Home Assistant."""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from decimal import Decimal, InvalidOperation
import logging
import math
from typing import Any
from uuid import UUID

import voluptuous as vol

from homeassistant.components import persistent_notification, websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, Event, HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    PLATFORMS,
    SERVICE_DEBUG_INFO,
    SERVICE_ENFORCE_PLANNING,
    SERVICE_EXPORT_DEBUG,
    SERVICE_EXPORT_DATA,
    SERVICE_IMPORT_DATA,
    SERVICE_RECALC_HISTORY_TODAY,
    SERVICE_WHAT_IF,
    NUMBER_SETTINGS,
    TEXT_SETTINGS,
    WHAT_IF_SWITCHES,
)
from .management.backup import BACKUP_DIR, async_export, async_import
from .management.blueprint_install import async_install_blueprints
from .coordinator import BattMindCoordinator
from .management.debug_export import DEBUG_DIR, DebugDownloadView, async_export_debug, async_notify_debug, install_log_buffer
from .management.dashboard_build import async_remove_dashboard_file, async_setup_dashboard, async_setup_what_if_card
from .management.what_if_storage import DashboardJob, WhatIfStorage
from .engine.planner.replay import WhatIfCancelled

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

BattMindConfigEntry = ConfigEntry[BattMindCoordinator]

_WHAT_IF_STORES = f"{DOMAIN}_what_if_stores"
_WHAT_IF_WS_REGISTERED = f"{DOMAIN}_what_if_ws_registered"
_WHAT_IF_JOB_IDS = f"{DOMAIN}_what_if_job_ids"


@websocket_api.websocket_command(
    {vol.Required("type"): "battmind/what_if_last", vol.Required("config_entry_id"): cv.string}
)
@websocket_api.async_response
async def _websocket_what_if_last(hass: HomeAssistant, connection, msg: dict) -> None:
    """Return the last saved result for a loaded BattMind entry."""
    entry_id = msg["config_entry_id"]
    if not any(entry.entry_id == entry_id for entry in hass.config_entries.async_loaded_entries(DOMAIN)):
        connection.send_error(msg["id"], "not_found", "BattMind entry is not loaded")
        return
    store = hass.data.get(_WHAT_IF_STORES, {}).get(entry_id)
    if store is None or store.closed:
        connection.send_error(msg["id"], "not_found", "BattMind entry is not loaded")
        return
    try:
        record = await store.load()
    except Exception:  # noqa: BLE001
        connection.send_error(msg["id"], "unknown_error", "Could not load the saved what-if result")
        return
    connection.send_result(msg["id"], record)

_WHAT_IF_SETTINGS = (
    ("battery_size", "battery_size", "kWh"),
    ("minimum_level", "minimum_level", "%"),
    ("maximum_level", "maximum_level", "%"),
    ("starting_level", "starting_level", "%"),
    ("max_charging_power", "max_charging_power", "W"),
    ("max_discharging_power", "max_discharging_power", "W"),
    ("round_trip_loss", "round_trip_loss", "%"),
    ("wear_cost_per_kwh", "wear_cost_per_kwh", "{valuta}/kWh"),
    ("usage_reserve", "usage_reserve", "%"),
    ("min_profit_per_kwh", "min_profit_per_kwh", "{valuta}/kWh"),
    ("min_total_profit", "min_total_profit", "{valuta}"),
    ("min_sell_kwh", "min_sell_kwh", "kWh"),
    ("cheap_price_periods", "cheap_price_periods", ""),
    ("cheapest_price_rise_threshold", "cheapest_price_rise_threshold", "{valuta}/kWh"),
    ("cheap_price_period_rise_threshold", "cheap_price_period_rise_threshold", "{valuta}/kWh"),
    ("powerwall_max_hours_old", "powerwall_max_hours_old", "h"),
)
# switches without a charging_type description (labelled with ui.what_if.<key>)
_WHAT_IF_SETTING_SWITCHES = (
    "optimizer_control",
    "use_midnight_battery_level",
    "solar_charging",
    "enhance_battery_efficiency",
    "cheapest_hour_fill_up",
)


def _format_what_if_number(value: float, decimals: int | None = None) -> str:
    formatted = f"{value:.{decimals}f}" if decimals is not None else f"{value:g}"
    return formatted.replace(".", ",")


_WHAT_IF_METRICS = (
    ("house_kwh", "house_kwh", "kWh", False),
    ("house_cost", "house_cost", "{valuta}", False),
    ("battery_kwh", "battery_kwh", "kWh", False),
    ("battery_cost", "battery_cost", "{valuta}", False),
    ("solar_kwh", "solar_kwh", "kWh", False),
    ("solar_income", "solar_income", "{valuta}", True),
    ("charged_solar_kwh", "charged_solar_kwh", "kWh", False),
    ("sold_kwh", "sold_kwh", "kWh", False),
    ("sold_income", "sold_income", "{valuta}", True),
    ("wear", "wear", "{valuta}", False),
    ("paid", "paid", "{valuta}", False),
    ("earned", "earned", "{valuta}", True),
    ("bill", "bill", "{valuta}", False),
)


def _what_if_notification(coordinator: BattMindCoordinator, simulation: dict, overrides: dict) -> str:
    current = simulation["current_assumptions"]
    scenario = simulation["assumptions"]
    setting_rows = []
    for key, label_key, unit in _WHAT_IF_SETTINGS:
        if key not in overrides or current[key] == scenario[key]:
            continue
        translated_unit = unit.replace("{valuta}", coordinator.t("ui.common.valuta"))
        decimals = 1 if unit == "kWh" else 2 if unit.startswith("{valuta}") else None
        setting_rows.append(
            "| **{label}** | {current} | {scenario} |".format(
                label=coordinator.t(f"ui.what_if.{label_key}"),
                current=f"{_format_what_if_number(current[key], decimals)} {translated_unit}".rstrip(),
                scenario=f"{_format_what_if_number(scenario[key], decimals)} {translated_unit}".rstrip(),
            )
        )
    if "exclude_sell_hours" in overrides and current["exclude_sell_hours"] != scenario["exclude_sell_hours"]:
        setting_rows.append(
            "| **{label}** | {current} | {scenario} |".format(
                label=coordinator.t("ui.what_if.exclude_sell_hours"),
                current=current["exclude_sell_hours"] or "-",
                scenario=scenario["exclude_sell_hours"] or "-",
            )
        )
    for key in WHAT_IF_SWITCHES:
        current_enabled = simulation["current_assumptions"]["rules"][key]
        scenario_enabled = simulation["assumptions"]["rules"][key]
        if current_enabled == scenario_enabled:
            continue
        setting_rows.append(
            "| **{label}** | {current} | {scenario} |".format(
                label=coordinator.t(
                    f"ui.what_if.{key}" if key in _WHAT_IF_SETTING_SWITCHES else f"charging_type.{key}.description"
                ),
                current=coordinator.t("ui.what_if.enabled" if current_enabled else "ui.what_if.disabled"),
                scenario=coordinator.t("ui.what_if.enabled" if scenario_enabled else "ui.what_if.disabled"),
            )
        )

    currency = coordinator.t("ui.common.valuta")
    current_summary = simulation["current"]["with"]
    scenario_summary = simulation["scenario"]["with"]
    metric_rows = []
    for key, label_key, unit, income in _WHAT_IF_METRICS:
        current_value = current_summary[key] if key in current_summary else simulation["current"][key]
        scenario_value = scenario_summary[key] if key in scenario_summary else simulation["scenario"][key]
        if income:
            # 0.0 - value: no income is shown as 0,00 and not -0,00
            current_value = 0.0 - current_value
            scenario_value = 0.0 - scenario_value
        translated_unit = currency if unit == "{valuta}" else unit
        metric_rows.append(
            "| **{label}** | {current} | {scenario} | {unit} |".format(
                label=coordinator.t(f"ui.what_if.{label_key}"),
                current=_format_what_if_number(current_value, 1 if unit == "kWh" else 2),
                scenario=_format_what_if_number(scenario_value, 1 if unit == "kWh" else 2),
                unit=translated_unit,
            )
        )

    period = coordinator.t(
        "ui.what_if.period",
        days=simulation["period"]["days"],
        hours=simulation["period"]["hours"],
    )
    data_sources = coordinator.t(
        "ui.what_if.data_sources",
        statistics=simulation["data_sources"]["long_term_statistics_samples"],
        recorder=simulation["data_sources"]["recorder_history_fallback_samples"],
    )
    price_provider = simulation.get("price_provider")
    price_provider_hours = simulation.get("price_provider_hours") or {}
    if len(price_provider_hours) > 1:
        price_provider = ", ".join(
            coordinator.t("ui.what_if.price_provider_hours", provider=name, hours=hours)
            for name, hours in price_provider_hours.items()
        )
    tariff_estimated_hours = simulation.get("tariff_estimated_hours", 0)
    tariff_unavailable_hours = simulation.get("tariff_unavailable_hours", 0)
    control_mode = coordinator.t(
        "ui.what_if.control_mode",
        mode=coordinator.t(
            "ui.what_if.optimizer_mode"
            if simulation["current_assumptions"]["control_mode"] == "optimizer"
            else "ui.what_if.rules_mode"
        ),
    )
    heading = (
        f"| | **{coordinator.t('ui.what_if.current')}** "
        f"| **{coordinator.t('ui.what_if.scenario')}** |"
    )
    result_heading = (
        f"| | **{coordinator.t('ui.what_if.current')}** "
        f"| **{coordinator.t('ui.what_if.scenario')}** "
        "| |"
    )
    lines = [period]
    coverage = simulation.get("coverage")
    if coverage:
        lines.append(
            coordinator.t(
                "ui.what_if.missing_data",
                missing=_format_what_if_number(coverage["missing_hours"] / 24.0, 1),
                requested=coverage["requested_days"],
            )
        )
        if coverage.get("limited_by"):
            lines.append(
                coordinator.t(
                    "ui.what_if.limited_by",
                    entity=coverage["limited_by"]["entity_id"],
                    date=coverage["limited_by"]["first"][:10],
                )
            )
        if coverage.get("gap_hours"):
            lines.append(coordinator.t("ui.what_if.gap_hours", hours=coverage["gap_hours"]))
    lines.append(data_sources)
    if price_provider:
        lines.append(coordinator.t("ui.what_if.price_provider", provider=price_provider))
    if tariff_estimated_hours:
        lines.append(coordinator.t("ui.what_if.tariff_estimated", hours=tariff_estimated_hours))
    if tariff_unavailable_hours:
        lines.append(coordinator.t("ui.what_if.tariff_unavailable", hours=tariff_unavailable_hours))
    lines.extend((control_mode, ""))
    lines.extend((coordinator.t("dashboard.what_if_baseline_note"), ""))
    if setting_rows:
        lines.extend((f"**{coordinator.t('ui.what_if.changed_settings')}**", heading, "|---|---|---|", *setting_rows, ""))
    lines.extend(
        (
            f"**{coordinator.t('ui.what_if.results')}**",
            result_heading,
            "|---|---|---|---|",
            *metric_rows,
        )
    )
    profit_difference = round(simulation["difference"]["profit"], 2)
    bill_improvement = round(-simulation["difference"]["bill"], 2)
    lines.extend(
        (
            "",
            "**{label}:** {profit} {currency}".format(
                label=coordinator.t("ui.what_if.profit_difference"),
                profit=_format_what_if_number(profit_difference, 2),
                currency=currency,
            ),
        )
    )
    if bill_improvement != profit_difference:
        lines.append(
            "**{label}:** {bill} {currency}".format(
                label=coordinator.t("ui.what_if.bill_difference"),
                bill=_format_what_if_number(bill_improvement, 2),
                currency=currency,
            )
        )
    if coverage:
        lines.extend(("", *_what_if_estimate_lines(coordinator, simulation, coverage, result_heading)))
    lines.extend(("", coordinator.t("ui.what_if.note")))
    return "\n".join(lines)


def _what_if_estimate_lines(
    coordinator: BattMindCoordinator, simulation: dict, coverage: dict, heading: str
) -> list[str]:
    """The estimate of the whole requested period, kept apart from the simulated results."""
    estimate = simulation.get("estimate")
    if not estimate:
        return [coordinator.t("ui.what_if.estimate_unavailable", days=coverage["requested_days"])]
    currency = coordinator.t("ui.common.valuta")
    rows = (
        ("bill", "estimate_safe", {"days": simulation["period"]["days"]}),
        ("estimated", "estimate_missing", {"days": _format_what_if_number(estimate["estimated_hours"] / 24.0, 1)}),
        ("total", "estimate_total", {"days": estimate["days"]}),
    )
    lines = [
        f"**{coordinator.t('ui.what_if.estimate_title', days=estimate['days'])}**",
        heading,
        "|---|---|---|---|",
        *(
            "| **{label}** | {current} | {scenario} | {unit} |".format(
                label=coordinator.t(f"ui.what_if.{label_key}", **values),
                current=_format_what_if_number(estimate["current"][key], 2),
                scenario=_format_what_if_number(estimate["scenario"][key], 2),
                unit=currency,
            )
            for key, label_key, values in rows
        ),
        "",
        # like bill_difference: a lower bill in the scenario is an improvement
        "**{label}:** {bill} {currency}".format(
            label=coordinator.t("ui.what_if.estimate_difference"),
            bill=_format_what_if_number(0.0 - estimate["difference"], 2),
            currency=currency,
        ),
    ]
    if estimate["estimated_from_other_months"]:
        lines.append(
            coordinator.t(
                "ui.what_if.estimate_other_months", months=", ".join(estimate["estimated_from_other_months"])
            )
        )
    lines.append(coordinator.t("ui.what_if.estimate_note"))
    return lines


SERVICE_SCHEMA = vol.Schema({vol.Optional("config_entry_id"): cv.string})
IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry_id"): cv.string,
        vol.Required("file"): cv.string,
        vol.Optional("restore_configuration", default=False): cv.boolean,
    }
)


def _whole_module_count(value: float) -> int:
    if not value.is_integer():
        raise vol.Invalid("Module count must be a whole number")
    return int(value)


WHAT_IF_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
        vol.Optional("history_days", default=365): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
        vol.Optional("battery_size"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
        vol.Optional("range_start"): vol.Coerce(float),
        vol.Optional("range_end"): vol.Coerce(float),
        vol.Optional("range_step"): vol.Coerce(float),
        vol.Optional("module_capacity"): vol.All(vol.Coerce(float), vol.Range(min=0.001, max=1000)),
        vol.Optional("module_price"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1000000000)),
        vol.Optional("module_count"): vol.All(vol.Coerce(float), vol.Range(min=1, max=12), _whole_module_count),
        vol.Optional("minimum_level"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("maximum_level"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("starting_level"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("max_charging_power"): vol.All(vol.Coerce(float), vol.Range(min=100, max=100000)),
        vol.Optional("max_discharging_power"): vol.All(vol.Coerce(float), vol.Range(min=100, max=100000)),
        vol.Optional("round_trip_loss"): vol.All(vol.Coerce(float), vol.Range(min=0, max=90)),
        vol.Optional("wear_cost_per_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0, max=10)),
        vol.Optional("usage_reserve"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("min_profit_per_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0, max=10)),
        vol.Optional("min_total_profit"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("min_sell_kwh"): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=1.0)),
        **{
            vol.Optional(key): vol.All(
                vol.Coerce(float), vol.Range(min=NUMBER_SETTINGS[key]["min"], max=NUMBER_SETTINGS[key]["max"])
            )
            for key in (
                "cheap_price_periods",
                "cheapest_price_rise_threshold",
                "cheap_price_period_rise_threshold",
                "powerwall_max_hours_old",
            )
        },
        vol.Optional("exclude_sell_hours"): vol.All(
            cv.string, vol.Match(TEXT_SETTINGS["exclude_sell_hours"]["pattern"])
        ),
        **{vol.Optional(key): cv.boolean for key in WHAT_IF_SWITCHES},
    }
)


def _what_if_range_values(data: dict) -> list[float] | None:
    """Validate and expand an optional capacity interval without rounding its values."""
    keys = ("range_start", "range_end", "range_step")
    supplied = [key in data for key in keys]
    if not any(supplied):
        return None
    if not all(supplied):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="what_if_range_incomplete",
        )

    try:
        start, end, step = (Decimal(str(data[key])) for key in keys)
    except (InvalidOperation, ValueError):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="what_if_range_invalid",
        ) from None

    if (
        not all(value.is_finite() for value in (start, end, step))
        or start < 0
        or end > 1000
        or start > end
        or step <= 0
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="what_if_range_invalid",
        )

    scale = max(0, *(-value.as_tuple().exponent for value in (start, end, step)))

    def units(value: Decimal) -> int:
        sign, digits, exponent = value.as_tuple()
        coefficient = int("".join(map(str, digits)) or "0")
        if sign:
            coefficient = -coefficient
        return coefficient * 10 ** (exponent + scale)

    start_units, end_units, step_units = (units(value) for value in (start, end, step))
    count = (end_units - start_units) // step_units + 1
    if count > 12:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="what_if_range_invalid",
        )
    values = [
        float(Decimal(f"{start_units + step_units * index}e-{scale}"))
        for index in range(count)
    ]

    if (
        any(not math.isfinite(value) or value < 0 or value > 1000 for value in values)
        or any(left >= right for left, right in zip(values, values[1:]))
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="what_if_range_invalid",
        )
    return values


def _what_if_module_options(data: dict) -> dict | None:
    """Validate optional module comparison settings, preserving zero-priced modules."""
    has_capacity = "module_capacity" in data
    has_price = "module_price" in data
    has_count = "module_count" in data
    if not has_capacity and not has_price and not has_count:
        return None
    if not has_capacity or not has_price:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="what_if_module_incomplete",
        )
    capacity = float(data["module_capacity"])
    price = float(data["module_price"])
    count = int(data.get("module_count", 5))
    if (
        not math.isfinite(capacity)
        or capacity <= 0
        or capacity > 1000
        or not math.isfinite(price)
        or price < 0
        or not 1 <= count <= 12
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="what_if_module_invalid",
        )
    return {
        "module_capacity": capacity,
        "module_price": price,
        "module_count": count,
    }


def _validation_error(err: HomeAssistantError | str) -> ServiceValidationError:
    """A translated ServiceValidationError for an error from a service action."""
    if isinstance(err, HomeAssistantError) and err.translation_domain == DOMAIN and err.translation_key:
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=err.translation_key,
            translation_placeholders=err.translation_placeholders,
        )
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key="action_failed", translation_placeholders={"error": str(err)}
    )


def _coordinators(hass: HomeAssistant, call: ServiceCall) -> list[BattMindCoordinator]:
    entry_id = call.data.get("config_entry_id")
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if entry_id:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded", translation_placeholders={"entry_id": entry_id}
            )
    return [entry.runtime_data for entry in entries]


def _dashboard_entry(hass: HomeAssistant, entry_id: str):
    """Resolve only a loaded entry with an open result store."""
    store = hass.data.get(_WHAT_IF_STORES, {}).get(entry_id)
    if store is None or store.closed:
        return None, None
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if entry.entry_id == entry_id:
            return entry, store
    return None, None


async def _dashboard_what_if(hass, coordinator, store, job, data):
    """Run without persistent notifications, and commit only if not stopped."""
    try:
        capacity_range = _what_if_range_values(data)
        module_comparison = _what_if_module_options(data)
        overrides = {
            key: value for key, value in data.items()
            if key not in (
                "config_entry_id", "history_days", "range_start", "range_end", "range_step",
                "module_capacity", "module_price", "module_count",
            )
        }
        kwargs = {"cancel_check": job.check}
        if capacity_range is not None:
            kwargs["capacity_range"] = capacity_range
        if module_comparison is not None:
            kwargs["module_comparison"] = module_comparison
        job.check()
        result = await coordinator.async_what_if(
            overrides, data["history_days"], job.report, **kwargs
        )
        job.check()
        if result.error:
            raise result.error
        record = {
            "completed_at": dt_util.utcnow().isoformat(),
            "inputs": {key: value for key, value in data.items() if key != "config_entry_id"},
            "simulation": result.value,
        }
        await store.save(record, job)
        return {"job_id": job.job_id, "status": "completed", "result": result.value}
    except WhatIfCancelled:
        return {"job_id": job.job_id, "status": "cancelled"}
    finally:
        if store.dashboard_job is job:
            store.dashboard_job = None


@websocket_api.websocket_command({
    vol.Required("type"): "battmind/what_if_start",
    vol.Required("config_entry_id"): cv.string,
    vol.Required("job_id"): cv.string,
    vol.Required("data"): dict,
})
@websocket_api.require_admin
@websocket_api.async_response
async def _websocket_what_if_start(hass, connection, msg):
    entry, store = _dashboard_entry(hass, msg["config_entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "BattMind entry is not loaded")
        return
    try:
        if str(UUID(msg["job_id"])) != msg["job_id"]:
            raise ValueError("job ID must be a canonical UUID")
        if "config_entry_id" in msg["data"]:
            raise ValueError("config_entry_id must be outside data")
        data = WHAT_IF_SCHEMA({**msg["data"], "config_entry_id": entry.entry_id})
        _what_if_range_values(data)
        _what_if_module_options(data)
    except (ValueError, vol.Invalid, HomeAssistantError):
        connection.send_error(msg["id"], "invalid_format", "Invalid what-if inputs or job ID")
        return
    if store.dashboard_job is not None:
        connection.send_error(msg["id"], "already_running", "A dashboard what-if is already running")
        return
    used_ids = hass.data.setdefault(_WHAT_IF_JOB_IDS, {}).setdefault(entry.entry_id, set())
    if msg["job_id"] in used_ids:
        connection.send_error(msg["id"], "invalid_format", "Job ID was already used")
        return
    used_ids.add(msg["job_id"])
    job = DashboardJob(msg["job_id"])
    store.dashboard_job = job
    task = hass.async_create_task(_dashboard_what_if(hass, entry.runtime_data, store, job, data))
    store.track(task)
    try:
        result = await asyncio.shield(task)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Dashboard what-if simulation failed")
        connection.send_error(msg["id"], "unknown_error", "Could not run or save the what-if result")
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({
    vol.Required("type"): "battmind/what_if_status",
    vol.Required("config_entry_id"): cv.string,
})
@websocket_api.require_admin
@websocket_api.async_response
async def _websocket_what_if_status(hass, connection, msg):
    entry, store = _dashboard_entry(hass, msg["config_entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "BattMind entry is not loaded")
        return
    job = store.dashboard_job
    connection.send_result(msg["id"], {
        "job_id": job.job_id if job else None,
        "status": "stopping" if job and job.cancelled.is_set() else "running" if job else "idle",
        "progress": job.progress if job else None,
    })


@websocket_api.websocket_command({
    vol.Required("type"): "battmind/what_if_cancel",
    vol.Required("config_entry_id"): cv.string,
    vol.Required("job_id"): cv.string,
})
@websocket_api.require_admin
@websocket_api.async_response
async def _websocket_what_if_cancel(hass, connection, msg):
    entry, store = _dashboard_entry(hass, msg["config_entry_id"])
    job = store.dashboard_job if store else None
    if entry is None or job is None or job.job_id != msg["job_id"]:
        connection.send_error(msg["id"], "not_found", "Dashboard what-if job not found")
        return
    if job.committing:
        connection.send_error(msg["id"], "too_late", "The result is already being saved")
        return
    job.cancelled.set()
    connection.send_result(msg["id"], {"job_id": job.job_id, "status": "stopping"})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the BattMind services."""
    await async_setup_what_if_card(hass)
    if not hass.data.get(_WHAT_IF_WS_REGISTERED):
        websocket_api.async_register_command(hass, _websocket_what_if_last)
        websocket_api.async_register_command(hass, _websocket_what_if_start)
        websocket_api.async_register_command(hass, _websocket_what_if_status)
        websocket_api.async_register_command(hass, _websocket_what_if_cancel)
        hass.data[_WHAT_IF_WS_REGISTERED] = True

    async def _debug_info(call: ServiceCall) -> ServiceResponse:
        output = {}
        for coordinator in _coordinators(hass, call):
            output[coordinator.prefix] = await coordinator.async_debug_info()
        return {"debug_info": output} if call.return_response else None

    async def _export_debug(call: ServiceCall) -> ServiceResponse:
        files = {}
        for coordinator in _coordinators(hass, call):
            name = await async_export_debug(hass, coordinator)
            await async_notify_debug(hass, coordinator, name)
            files[coordinator.prefix] = f"{DEBUG_DIR}/{name}"
        return {"files": files} if call.return_response else None

    async def _recalc_history(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            await coordinator.async_recalc_charging_history_today()

    async def _enforce_planning(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass, call):
            coordinator.async_request_charge(force=True)

    async def _what_if_job(call: ServiceCall, coordinator: BattMindCoordinator) -> ServiceResponse:
        history_days = call.data["history_days"]
        capacity_range = _what_if_range_values(call.data)
        module_comparison = _what_if_module_options(call.data)
        range_keys = {
            "range_start", "range_end", "range_step", "module_capacity", "module_price", "module_count",
        }
        overrides = {
            key: value
            for key, value in call.data.items()
            if key not in ("config_entry_id", "history_days", *range_keys)
        }
        output = {}
        saved = {}
        for coordinator in (coordinator,):
            notification_id = f"{coordinator.prefix}_what_if"
            title = coordinator.t("ui.what_if.title", prefix=coordinator.prefix)
            persistent_notification.async_create(
                hass,
                coordinator.t("ui.what_if.started", days=history_days),
                title=title,
                notification_id=notification_id,
            )

            @callback
            def _progress(info: dict, coordinator: BattMindCoordinator = coordinator, title: str = title) -> None:
                # the same notification is updated, at most every PROGRESS_INTERVAL seconds
                if "phase" in info:
                    # the history is loaded before the simulation starts
                    message = coordinator.t(
                        "ui.what_if.loading",
                        days=history_days,
                        phase=coordinator.t(f"ui.what_if.phase_{info['phase']}"),
                        seconds=info["seconds"],
                    )
                else:
                    message = coordinator.t(
                        "ui.what_if.progress",
                        percent=info["percent"],
                        step=info["step"],
                        steps=info["steps"],
                        days=round(info["hours"] / 24.0),
                        total=round(info["total_hours"] / 24.0),
                    )
                persistent_notification.async_create(
                    hass, message, title=title, notification_id=f"{coordinator.prefix}_what_if"
                )

            try:
                kwargs = {}
                if capacity_range is not None:
                    kwargs["capacity_range"] = capacity_range
                if module_comparison is not None:
                    kwargs["module_comparison"] = module_comparison
                if kwargs:
                    result = await coordinator.async_what_if(
                        overrides, history_days, _progress, **kwargs
                    )
                else:
                    result = await coordinator.async_what_if(overrides, history_days, _progress)
            except HomeAssistantError as err:
                persistent_notification.async_create(
                    hass,
                    coordinator.t("ui.what_if.failed", error=err),
                    title=title,
                    notification_id=notification_id,
                )
                raise _validation_error(err) from err
            except Exception as err:
                _LOGGER.exception("Unexpected error while running the BattMind what-if simulation")
                persistent_notification.async_create(
                    hass,
                    coordinator.t("ui.what_if.failed", error=err),
                    title=title,
                    notification_id=notification_id,
                )
                raise
            if result.error:
                persistent_notification.async_create(
                    hass,
                    coordinator.t("ui.what_if.failed", error=result.error),
                    title=title,
                    notification_id=notification_id,
                )
                raise _validation_error(result.error)
            record = {
                "completed_at": dt_util.utcnow().isoformat(),
                "inputs": {key: value for key, value in call.data.items() if key != "config_entry_id"},
                "simulation": result.value,
            }
            try:
                await hass.data[_WHAT_IF_STORES][coordinator.entry.entry_id].save(record)
            except Exception as err:
                persistent_notification.async_create(
                    hass, coordinator.t("ui.what_if.failed", error="Could not save result"),
                    title=title, notification_id=notification_id,
                )
                raise _validation_error("Could not save the what-if result") from err
            output[coordinator.prefix] = result.value
            saved[coordinator.prefix] = record
            persistent_notification.async_create(
                hass,
                _what_if_notification(coordinator, result.value, overrides),
                title=title,
                notification_id=notification_id,
            )
        return {"simulations": output, "saved_results": saved}

    async def _what_if(call: ServiceCall) -> ServiceResponse:
        # Validate before launching any entry jobs.
        _what_if_range_values(call.data)
        output: dict = {"simulations": {}, "saved_results": {}}
        for coordinator in _coordinators(hass, call):
            entry = coordinator.entry
            store = hass.data[_WHAT_IF_STORES].get(entry.entry_id)
            if store is None or store.closed:
                raise _validation_error("BattMind entry is unloading")
            task = entry.async_create_task(hass, _what_if_job(call, coordinator), name="battmind_what_if")
            store.track(task)
            result = await asyncio.shield(task)
            output["simulations"].update(result["simulations"])
            output["saved_results"].update(result["saved_results"])
        return output

    async def _export_data(call: ServiceCall) -> ServiceResponse:
        files = {}
        for coordinator in _coordinators(hass, call):
            files[coordinator.prefix] = f"{BACKUP_DIR}/{await async_export(hass, coordinator.entry)}"
        return {"files": files} if call.return_response else None

    async def _import_data(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_an_entry",
                translation_placeholders={"entry_id": call.data["config_entry_id"]},
            )
        try:
            await async_import(hass, entry, call.data["file"].split("/")[-1], call.data["restore_configuration"])
        except HomeAssistantError as err:
            raise _validation_error(err) from err

    hass.services.async_register(
        DOMAIN, SERVICE_EXPORT_DATA, _export_data, schema=SERVICE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    # replaces the databases and can rewrite the entry options: administrators only
    async_register_admin_service(hass, DOMAIN, SERVICE_IMPORT_DATA, _import_data, schema=IMPORT_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_DEBUG_INFO, _debug_info, schema=SERVICE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, SERVICE_EXPORT_DEBUG, _export_debug, schema=SERVICE_SCHEMA, supports_response=SupportsResponse.OPTIONAL
    )
    install_log_buffer()
    if getattr(hass, "http", None) is not None:
        hass.http.register_view(DebugDownloadView())
    hass.services.async_register(DOMAIN, SERVICE_RECALC_HISTORY_TODAY, _recalc_history, schema=SERVICE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_ENFORCE_PLANNING, _enforce_planning, schema=SERVICE_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_WHAT_IF,
        _what_if,
        schema=WHAT_IF_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    await async_install_blueprints(hass)
    return True


async def _async_run_after_home_assistant_started(
    hass: HomeAssistant,
    entry: ConfigEntry,
    action: Callable[[], Coroutine[Any, Any, None]],
    name: str,
) -> None:
    """Run post-setup work after every integration has had its startup turn."""
    if hass.state is CoreState.running:
        await action()
        return

    async def _async_run() -> None:
        # Let all other homeassistant_started listeners run before inspecting
        # their entities. This keeps generated dashboards independent of setup order.
        await asyncio.sleep(0)
        await action()

    unsub: Callable[[], None] | None = None

    @callback
    def _started(_event: Event) -> None:
        # HA removed the fired one-time listener, removing it again on unload logs an error
        nonlocal unsub
        unsub = None
        entry.async_create_task(hass, _async_run(), name=name)

    @callback
    def _cancel() -> None:
        if unsub is not None:
            unsub()

    unsub = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
    entry.async_on_unload(_cancel)


async def async_setup_entry(hass: HomeAssistant, entry: BattMindConfigEntry) -> bool:
    coordinator = BattMindCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.async_setup()
    hass.data.setdefault(_WHAT_IF_STORES, {})[entry.entry_id] = WhatIfStorage(hass, entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    if coordinator.config.get("dashboard"):
        await _async_run_after_home_assistant_started(
            hass,
            entry,
            lambda: async_setup_dashboard(hass, entry, coordinator.config, coordinator.prefix),
            f"battmind_{coordinator.prefix}_dashboard_startup",
        )
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BattMindConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        stores = hass.data.get(_WHAT_IF_STORES, {})
        store = stores.get(entry.entry_id)
        if store is not None:
            await store.close()
            stores.pop(entry.entry_id, None)
        await entry.runtime_data.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: BattMindConfigEntry) -> None:
    from .management.storage import BattMindStorage

    # the data can be imported again (config flow of a new entry, or import_data)
    try:
        await async_export(hass, entry, "deleted")
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Could not back up BattMind %s, its databases are kept in .storage", entry.title)
        return
    await BattMindStorage(hass, entry.entry_id).async_remove()
    await WhatIfStorage(hass, entry.entry_id).store.async_remove()
    await async_remove_dashboard_file(hass, entry.entry_id)


async def _async_update_listener(hass: HomeAssistant, entry: BattMindConfigEntry) -> None:
    hass.config_entries.async_schedule_reload(entry.entry_id)
