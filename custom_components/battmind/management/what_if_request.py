"""The inputs of a what-if simulation: the action schema and the validated capacity range and module options."""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

import voluptuous as vol

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from ..const import DOMAIN, NUMBER_SETTINGS, TEXT_SETTINGS, WHAT_IF_SWITCHES


def whole_module_count(value: float) -> int:
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
        vol.Optional("module_count"): vol.All(vol.Coerce(float), vol.Range(min=1, max=12), whole_module_count),
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


def what_if_range_values(data: dict) -> list[float] | None:
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


def what_if_module_options(data: dict) -> dict | None:
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
