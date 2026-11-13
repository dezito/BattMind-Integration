from __future__ import annotations

from typing import Any
from .base import CHARGING_HISTORY_COMBINE_AFTER

# --------------------------------------------------------------------------- #
# Settings entities (dashboard "Indstillinger")
# --------------------------------------------------------------------------- #
# The values are restored on restart and migrated once from input_*.<legacy prefix>_<key>.
SWITCH_SETTINGS: dict[str, dict[str, Any]] = {
    "solar_charging": {"default": True},
    "cheapest_hour_fill_planner": {"default": True},
    "cheapest_hour_fill_up": {"default": True},
    "most_expensive_planner": {"default": True},
    "needed_before_max_level_planner": {"default": True},
    "only_discharge_on_profit": {"default": True},
    "prioritize_discharge_hours_by_energy_cost": {"default": True},
    "sell_excess_kwh_available": {"default": True},
    "use_midnight_battery_level": {"default": False},
    "most_profit": {"default": True},
    "force_powerwall_max_hours_old_discharge": {"default": True},
    "optimize_solar_charging": {"default": True},
    "negative_price_planner": {"default": True},
    "grid_charge_to_sell": {"default": True},
    "discharge_before_solar": {"default": True},
    "optimizer_control": {"default": True},
    # use the configured power-dependent charging/discharging efficiency curves
    "enhance_battery_efficiency": {"default": True},
    # correct the price forecast with its measured bias (engine/analysis)
    "price_forecast_correction": {"default": True},
    # use the consumption forecast type that hit best the last 30 days (engine/analysis)
    "auto_forecast_type": {"default": False},
}

NUMBER_SETTINGS: dict[str, dict[str, Any]] = {
    "cheap_price_periods": {
        "min": 1,
        "max": 10,
        "step": 1,
        "mode": "slider",
        "unit": None,
        "default": 2,
    },
    "cheapest_price_rise_threshold": {
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "mode": "box",
        "unit": "{valuta_kwh}",
        "default": 0.5,
    },
    "cheap_price_period_rise_threshold": {
        "min": 0.0,
        "max": 1.0,
        "step": 0.01,
        "mode": "box",
        "unit": "{valuta_kwh}",
        "default": 0.1,
    },
    "min_profit_per_kwh": {
        "min": 0.0,
        "max": 10.0,
        "step": 0.01,
        "mode": "box",
        "unit": "{valuta_kwh}",
        "default": 0.0,
    },
    "min_total_profit": {
        "min": 0.0,
        "max": 100.0,
        "step": 0.01,
        "mode": "box",
        "unit": "{valuta}",
        "default": 0.0,
    },
    "min_sell_kwh": {
        "min": 0.2,
        "max": 1.0,
        "step": 0.1,
        "mode": "box",
        "unit": "kWh",
        "default": 0.2,
    },
    "usage_reserve": {
        "min": 0.0,
        "max": 100.0,
        "step": 1.0,
        "mode": "slider",
        "unit": "%",
        "default": 0.0,
    },
    "powerwall_max_hours_old": {
        # a shorter limit makes no sense: energy is not "old" after a few hours (decided with the user 2026-11-08)
        "min": 12,
        "max": CHARGING_HISTORY_COMBINE_AFTER,
        "step": 1,
        "mode": "box",
        "unit": "h",
        "default": 48,
    },
}

FORECAST_TYPE_OPTIONS = [
    "Average",
    "EMA (Exponential Moving Average)",
    "Trend"
]
SELECT_SETTINGS: dict[str, dict[str, Any]] = {
    "forecast_type": {
        
        "options": FORECAST_TYPE_OPTIONS,
        "default": "EMA (Exponential Moving Average)",
    },
}

TEXT_SETTINGS: dict[str, dict[str, Any]] = {
    "exclude_sell_hours": {
        
        "pattern": r"^(unknown|unavailable|([0-9]|1[0-9]|2[0-3])(,([0-9]|1[0-9]|2[0-3]))*)?$",
        "default": "",
    },
}

# Settings the what-if simulator can change for its scenario (the rest is planning only:
# the forecast type and the price forecast correction, as the history is known)
WHAT_IF_SWITCHES = (
    "optimizer_control",
    "use_midnight_battery_level",
    "solar_charging",
    "enhance_battery_efficiency",
    "cheapest_hour_fill_planner",
    "cheapest_hour_fill_up",
    "most_expensive_planner",
    "needed_before_max_level_planner",
    "negative_price_planner",
    "grid_charge_to_sell",
    "most_profit",
    "sell_excess_kwh_available",
    "force_powerwall_max_hours_old_discharge",
    "discharge_before_solar",
    "only_discharge_on_profit",
    "prioritize_discharge_hours_by_energy_cost",
    "optimize_solar_charging",
)
WHAT_IF_NUMBERS = (
    "usage_reserve",
    "min_profit_per_kwh",
    "min_total_profit",
    "min_sell_kwh",
    "cheap_price_periods",
    "cheapest_price_rise_threshold",
    "cheap_price_period_rise_threshold",
    "powerwall_max_hours_old",
)
WHAT_IF_TEXTS = ("exclude_sell_hours",)

# --------------------------------------------------------------------------- #
# What-if inputs (dashboard "What-if" view)
# --------------------------------------------------------------------------- #
# They only feed a simulation. They are not planner settings: they are not part of NUMBER_SETTINGS or
# SWITCH_SETTINGS (initial settings, backups, the planner) and changing one never triggers a planning run.
WHAT_IF_INPUT_NUMBERS: dict[str, dict[str, Any]] = {
    "what_if_history_days": {"min": 1, "max": 365, "step": 1, "mode": "box", "unit": "d", "default": 365},
    "what_if_battery_size": {"min": 0, "max": 1000, "step": 0.1, "mode": "box", "unit": "kWh", "default": 20},
    # a capacity interval: start, end and step (the action takes finer values than the 0.1 kWh of these entities)
    "what_if_range_start": {"min": 0, "max": 1000, "step": 0.1, "mode": "box", "unit": "kWh", "default": 0},
    "what_if_range_end": {"min": 0, "max": 1000, "step": 0.1, "mode": "box", "unit": "kWh", "default": 30},
    "what_if_range_step": {"min": 0.1, "max": 1000, "step": 0.1, "mode": "box", "unit": "kWh", "default": 5},
    # the module comparison
    "what_if_module_capacity": {"min": 0.1, "max": 1000, "step": 0.1, "mode": "box", "unit": "kWh", "default": 5},
    "what_if_module_price": {"min": 0, "max": 1000000000, "step": 1, "mode": "box", "unit": "{valuta}", "default": 0},
    "what_if_module_count": {"min": 1, "max": 12, "step": 1, "mode": "box", "unit": None, "default": 5},
}
# the two optional groups of the form: a capacity interval and the module comparison
WHAT_IF_INPUT_SWITCHES: dict[str, dict[str, Any]] = {
    "what_if_range": {"default": False},
    "what_if_modules": {"default": False},
}
# A scenario value that stays "current" (no override) until the user chooses one. The scenario rules and settings are
# overrides: only a changed value is sent to the simulation, Current always uses the live settings.
WHAT_IF_OVERRIDE_CURRENT = "current"
WHAT_IF_RULE_OPTIONS = (WHAT_IF_OVERRIDE_CURRENT, "on", "off")
WHAT_IF_RULE_SELECTS: dict[str, str] = {f"what_if_{key}": key for key in WHAT_IF_SWITCHES}
# unknown (no override) until a value is entered; the button reset_what_if_settings clears them again
WHAT_IF_OVERRIDE_NUMBERS: dict[str, str] = {f"what_if_{key}": key for key in WHAT_IF_NUMBERS}
WHAT_IF_OVERRIDE_TEXTS: dict[str, str] = {f"what_if_{key}": key for key in WHAT_IF_TEXTS}
# the entities of the what-if inputs that are not overrides (their state is always a value)
WHAT_IF_INPUT_KEYS = (*WHAT_IF_INPUT_NUMBERS, *WHAT_IF_INPUT_SWITCHES)
# the switch that runs (on) and stops (off) a simulation, and the button that clears the scenario
WHAT_IF_RUN_SWITCH = "what_if_run"
WHAT_IF_BUTTON_KEYS = ("reset_what_if_settings",)
# binary sensors: which parts the last what-if result has (the dashboard shows only the parts that exist)
WHAT_IF_FLAGS = {
    "what_if_has_months": "months",
    "what_if_has_range": "range",
    "what_if_has_modules": "module",
}
# the state of the what_if_status sensor
WHAT_IF_STATUSES = ("idle", "running", "stopping", "stopped", "failed")

# Output sensors (icons in icons.json)
SENSOR_KEYS = (
    "current_charging_rule",
    "powerwall_action",
    "overview",
    "charging_history",
    "emoji_description",
    "battery_level_needed",
    "kwh_charged_by_solar",
    "analysis",
)
