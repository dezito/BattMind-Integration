"""Derived values of a what-if result for the dashboard.

The what-if view is made of standard cards (markdown and charts), which only format values. Everything that is
calculated from a simulation lives here, so it is tested in Python: the interval rates of a capacity range, the best
month, the payback of a battery or module and the plain-language summary of the module comparison.

Nothing is invented: a value that cannot be calculated from the result stays ``None`` and the sentence or row that
needs it is left out (never filled with 0).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .what_if_charts import category_chart, payback_chart

# the payback graph shows at most this many years
PAYBACK_MAX_YEARS = 30
PAYBACK_MIN_YEARS = 5
# the monthly bar charts of a range or a module comparison show at most this many setups
MAX_MONTHLY_SERIES = 6
# a calendar chart is only useful with more than this many months
MIN_MONTHS = 4

Translate = Callable[..., str]

# switches of the what-if simulator that are rules of a group (the others are controller switches)
_RULE_GROUPS = (
    ("charging", (
        "solar_charging", "cheapest_hour_fill_planner", "cheapest_hour_fill_up", "most_expensive_planner",
        "needed_before_max_level_planner", "negative_price_planner", "optimize_solar_charging",
    ), ("cheap_price_periods", "cheapest_price_rise_threshold", "cheap_price_period_rise_threshold")),
    ("discharging", (
        "only_discharge_on_profit", "prioritize_discharge_hours_by_energy_cost", "force_powerwall_max_hours_old_discharge",
    ), ("powerwall_max_hours_old",)),
    ("selling", (
        "grid_charge_to_sell", "most_profit", "sell_excess_kwh_available", "discharge_before_solar",
    ), ("min_total_profit", "min_sell_kwh", "exclude_sell_hours")),
    ("shared", (), ("usage_reserve", "min_profit_per_kwh")),
)
_PARAMETER_UNITS = {
    "usage_reserve": "%", "min_profit_per_kwh": "currency_kwh", "min_total_profit": "currency",
    "min_sell_kwh": "kWh", "cheap_price_periods": "", "cheapest_price_rise_threshold": "currency_kwh",
    "cheap_price_period_rise_threshold": "currency_kwh", "powerwall_max_hours_old": "h", "exclude_sell_hours": "text",
}
_SELLING_RULES = ("most_profit", "sell_excess_kwh_available", "grid_charge_to_sell", "force_powerwall_max_hours_old_discharge")


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if _finite(value) else None


def capacity_text(value: float) -> str:
    """A battery capacity with one decimal (20.0, 2.5)."""
    return f"{value:.1f}"


def money_text(value: float) -> str:
    return f"{value:.2f}"


def plural_category(language: str, count: int) -> str:
    """The CLDR plural category (one, few or other) of a whole number in the supported languages."""
    language = (language or "en").lower().split("-")[0]
    if language in ("pl", "uk"):
        if language == "uk" and count % 10 == 1 and count % 100 != 11:
            return "one"
        if language == "pl" and count == 1:
            return "one"
        if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
            return "few"
        return "other"
    return "one" if count == 1 else "other"


def module_count_label(count: int, language: str, t: Translate) -> str:
    category = plural_category(language, count)
    key = {
        "one": "dashboard.what_if_module_graph_module",
        "few": "dashboard.what_if_module_graph_modules_few",
    }.get(category, "dashboard.what_if_module_graph_modules")
    return f"{count} {t(key)}"


def duration_text(years: float, t: Translate) -> str:
    """Years and months, rounded to a whole month ("2 yr 3 mo")."""
    total_months = round(years * 12)
    whole, months = divmod(total_months, 12)
    parts = []
    if whole > 0:
        parts.append(t("dashboard.what_if_duration_years", n=whole))
    if months > 0 or not parts:
        parts.append(t("dashboard.what_if_duration_months", n=months))
    return " ".join(parts)


def spread(items: list, limit: int) -> list:
    """At most limit items spread evenly over the list, the first and last included."""
    if len(items) <= limit:
        return list(items)
    return [items[round(index * (len(items) - 1) / (limit - 1))] for index in range(limit)]


def _month_values(months: dict | None, key: str = "profit") -> dict[str, float]:
    return {
        month: value[key] if isinstance(value, dict) else value
        for month, value in (months or {}).items()
        if _finite(value[key] if isinstance(value, dict) else value)
    }


def _monthly_series(simulation: dict) -> dict | None:
    """Current, scenario and their difference per calendar month (only with more than three months)."""
    scenario = simulation.get("scenario", {}).get("months")
    current = simulation.get("current", {}).get("months")
    keys = sorted(scenario or {})
    if len(keys) < MIN_MONTHS or not current:
        return None
    current_values = _month_values(current)
    scenario_values = _month_values(scenario)
    # a month that one side has no value for has no difference (it is not counted as 0)
    difference = {
        key: scenario_values[key] - current_values[key] for key in keys if key in scenario_values and key in current_values
    }
    series = {"current": current_values, "scenario": scenario_values, "difference": difference}
    return {
        "order": keys,
        **{name: {key: _round(value) for key, value in values.items()} for name, values in series.items()},
        "best": {
            name: max(values, key=values.get) if values else None
            for name, values in series.items()
        },
    }


def _range_view(simulation: dict, months: dict | None, t: Translate) -> dict | None:
    items = simulation.get("range")
    if not items:
        return None
    profits = [item["profit"] for item in items if _finite(item.get("profit"))]
    highest_profit = max(profits) if len(profits) > 1 else None
    rates: list[float | None] = []
    for index, item in enumerate(items):
        previous = items[index - 1] if index else None
        capacity = item["battery_size"] - previous["battery_size"] if previous else None
        if (
            previous is None or not _finite(capacity) or capacity <= 0
            or not _finite(item.get("profit")) or not _finite(previous.get("profit"))
        ):
            rates.append(None)
        else:
            rates.append((item["profit"] - previous["profit"]) / capacity)
    finite_rates = [rate for rate in rates if rate is not None]
    highest_rate = max(finite_rates) if finite_rates else None
    rows = []
    intervals = []
    for index, item in enumerate(items):
        previous = items[index - 1] if index else None
        rate = rates[index]
        row = {
            "battery_size": item["battery_size"],
            "profit": _round(item.get("profit")),
            "bill": _round(item.get("bill")),
            "best_profit": highest_profit is not None and item.get("profit") == highest_profit,
            "interval_profit": _round(item["profit"] - previous["profit"]) if rate is not None else None,
            "rate": _round(rate, 4),
            "best_rate": rate is not None and rate == highest_rate,
        }
        rows.append(row)
        if rate is not None:
            intervals.append({
                "label": f"{capacity_text(previous['battery_size'])}–{capacity_text(item['battery_size'])} kWh",
                "start": previous["battery_size"],
                "end": item["battery_size"],
                "position": (previous["battery_size"] + item["battery_size"]) / 2,
                "rate": _round(rate, 4),
                "best": rate == highest_rate,
            })
    monthly = None
    if months and all(item.get("months") for item in items):
        picked = spread(items, MAX_MONTHLY_SERIES)
        monthly = [
            {"battery_size": item["battery_size"], "months": {key: _round(item["months"].get(key)) for key in months["order"]}}
            for item in picked
        ]
    # the charts without a time axis (see what_if_charts): the profit of each size, and the profit per added kWh of
    # each interval
    profit_chart = category_chart(
        "profit",
        t("dashboard.what_if_profit"),
        [
            {"label": capacity_text(row["battery_size"]), "tooltip": f"{capacity_text(row['battery_size'])} kWh",
             "y": row["profit"], "best": row["best_profit"], "rate": row["rate"]}
            for row in rows
        ],
        gradient="segments",  # each segment is coloured by the profit per added kWh it adds
    )
    interval_chart = category_chart(
        "interval",
        t("dashboard.what_if_interval_per_kwh"),
        [
            {"label": f"{capacity_text(item['start'])}–{capacity_text(item['end'])}",
             "tooltip": f"{capacity_text(item['start'])}–{capacity_text(item['end'])} kWh", "y": item["rate"], "best": item["best"]}
            for item in intervals
        ],
        gradient="values",
    )
    return {
        "rows": rows,
        "intervals": intervals,
        "monthly": monthly,
        "profit_chart": profit_chart,
        "interval_chart": interval_chart,
    }


def payback_series(comparison: dict, annual: dict, t: Translate, language: str) -> list[dict] | None:
    """One result per setup against no battery (price, yearly saving, payback), or None when the numbers are unknown.

    The accumulated cost over time at a constant yearly bill is ``start + rate * years``. The dashed "without battery"
    line starts at 0 and rises with the whole yearly bill. The current battery and every +N modules start at the
    purchase price (current battery price plus the added modules) and rise with the yearly bill of that setup.

    Two times from the start (None = never, there is no saving):
    * ``total_years``: where the line crosses the no-battery line, the whole setup has paid back;
    * ``years``: where the line crosses the line before it. For the current battery that is the no-battery line, for
      +N modules the last added module on its own (its price divided by its extra yearly saving).

    The series is None when the price of the current battery is unknown, negative or (with a current battery) zero,
    or the yearly bill without a battery is not a number: a missing price is never treated as 0. A negative bill is a
    real net-exporter bill and is drawn.
    """
    rows = annual.get("rows") or []
    base_capacity = comparison.get("base_capacity")
    module_capacity = comparison.get("module_capacity")
    module_price = comparison.get("module_price")
    if not all(_finite(value) for value in (base_capacity, module_capacity, module_price)) or module_capacity <= 0:
        return None
    has_current_battery = base_capacity > 0
    current_cost = base_capacity / module_capacity * module_price
    usable = [
        row for row in rows
        if _finite((row.get("annual_bill") or {}).get("with")) and _finite(row.get("cumulative_cost"))
    ]
    without = next(
        (row["annual_bill"]["without"] for row in rows if _finite((row.get("annual_bill") or {}).get("without"))), None
    )
    if (
        not any(row.get("module_count") == 0 for row in usable) or not _finite(without)
        or not _finite(current_cost) or current_cost < 0 or (has_current_battery and current_cost == 0)
    ):
        return None
    none = {
        "modules": -1, "dashed": True, "label": t("dashboard.what_if_module_payback_none"),
        "capacity": 0, "extra": module_capacity, "start": 0.0, "rate": without, "years": None, "total_years": None,
    }
    setups = []
    for row in usable:
        if row["module_count"] > 0 or has_current_battery:
            setups.append({
                "modules": row["module_count"],
                "dashed": False,
                "label": t("dashboard.what_if_module_payback_current") if row["module_count"] == 0
                else f"+{module_count_label(row['module_count'], language, t)}",
                "capacity": base_capacity + row["module_count"] * module_capacity,
                "extra": module_capacity,
                "start": current_cost + row["cumulative_cost"],
                "rate": row["annual_bill"]["with"],
                "years": None,
                "total_years": None,
            })
    setups = [item for item in setups if item["start"] > 0]
    reference = none
    for item in setups:
        saving = reference["rate"] - item["rate"]
        years = (item["start"] - reference["start"]) / saving if saving > 0 else None
        item["years"] = years if years is not None and math.isfinite(years) and years >= 0 else None
        total_saving = none["rate"] - item["rate"]
        total = item["start"] / total_saving if total_saving > 0 else None
        item["total_years"] = total if total is not None and math.isfinite(total) and total >= 0 else None
        reference = item
    return [none, *setups] if setups else None


def _payback_chart(lines: list[dict], t: Translate) -> dict | None:
    """Two points per line (0 years and the horizon) and a marker where a setup has paid back."""
    crossings = [value for item in lines for value in (item["years"], item["total_years"]) if value is not None]
    horizon = min(PAYBACK_MAX_YEARS, max(PAYBACK_MIN_YEARS, math.ceil(max([0, *crossings]) * 1.6 / 5) * 5))

    def value_at(item: dict, time: float) -> float:
        return item["start"] + item["rate"] * time

    if any(not math.isfinite(value_at(item, time)) for item in lines for time in (0, horizon)):
        return None

    def name(item: dict) -> str:
        return item["label"] if item["dashed"] else f"{capacity_text(item['capacity'])} kWh · {item['label']}"

    legend = _payback_legend(lines, t)
    markers = []
    chart_lines = []
    for index, item in enumerate(lines):
        chart_lines.append({
            "name": name(item),
            "legend": legend[index],
            "dashed": item["dashed"],
            "points": [[0, _round(value_at(item, 0))], [horizon, _round(value_at(item, horizon))]],
        })
        total = item["total_years"]
        if not item["dashed"] and total is not None and total <= horizon:
            markers.append({
                "name": f"{capacity_text(item['capacity'])} kWh {t('dashboard.what_if_module_in_total')} · {duration_text(total, t)}",
                "x": round(total, 3),
                "y": _round(value_at(lines[0], total)),
            })
    return {"horizon": horizon, "lines": chart_lines, "markers": markers}


def _payback_legend(lines: list[dict], t: Translate) -> list[str]:
    def duration(value: float | None) -> str:
        return duration_text(value, t) if value is not None else t("dashboard.what_if_module_graph_no_payback")

    legend = []
    for item in lines:
        if item["dashed"]:
            legend.append(item["label"])
            continue
        title = f"{capacity_text(item['capacity'])} kWh · {item['label']}"
        if item["modules"] == 0:
            legend.append(f"{title}: {duration(item['years'])}")
        else:
            extra = t("dashboard.what_if_module_extra_only", extra=capacity_text(item["extra"]))
            legend.append(
                f"{title}: {t('dashboard.what_if_module_in_total')} {duration(item['total_years'])} · {extra} {duration(item['years'])}"
            )
    return legend


def module_summary(annual: dict, comparison: dict, lines: list[dict] | None, t: Translate, language: str) -> list[str]:
    """Plain-language sentences for the module comparison. A sentence is left out unless every number in it is known."""
    currency = t("ui.common.valuta")
    sentences = []

    def line(modules: int) -> dict | None:
        return next((item for item in lines or [] if item["modules"] == modules), None)

    current = line(0)
    if current and current["total_years"] is not None:
        sentences.append(t(
            "dashboard.what_if_module_summary_current",
            capacity=capacity_text(current["capacity"]), price=money_text(current["start"]),
            gain=money_text(lines[0]["rate"] - current["rate"]), currency=currency,
            time=duration_text(current["total_years"], t),
        ))
    step = next((row.get("marginal_vs_previous") for row in annual["rows"] if row.get("module_count") == 1), None)
    base_capacity = comparison.get("base_capacity")
    module_capacity, module_price = comparison.get("module_capacity"), comparison.get("module_price")
    if (
        step and _finite(step.get("annual_gain")) and _finite(module_capacity) and module_capacity > 0
        and _finite(module_price) and module_price > 0 and _finite(base_capacity)
    ):
        payback = step.get("payback_years")
        values = {
            "modules": module_count_label(1, language, t),
            "capacity": capacity_text(base_capacity + comparison["module_capacity"]),
            "extra": capacity_text(comparison["module_capacity"]),
            "price": money_text(comparison["module_price"]),
            "gain": money_text(step["annual_gain"]),
            "currency": currency,
            "time": duration_text(payback, t) if _finite(payback) else "",
        }
        key = (
            "dashboard.what_if_module_summary_added"
            if step["annual_gain"] > 0 and _finite(payback) else "dashboard.what_if_module_summary_added_none"
        )
        sentences.append(t(key, **values))
    first = line(1)
    if current and first and first["total_years"] is not None:
        # The whole battery's time is an average of the current battery and the module: it is only shorter than the
        # module alone when the current battery pays back faster, so only then say that (and why).
        payback = (step or {}).get("payback_years")
        faster = not _finite(payback) or first["total_years"] < payback
        sentences.append(t(
            "dashboard.what_if_module_summary_total" if faster else "dashboard.what_if_module_summary_total_plain",
            capacity=capacity_text(first["capacity"]), time=duration_text(first["total_years"], t),
        ))
    if sentences:
        sentences.append(t("dashboard.what_if_module_summary_advice"))
    return sentences


def _module_view(simulation: dict, months: dict | None, t: Translate, language: str) -> dict | None:
    comparison = simulation.get("module_comparison")
    if not comparison:
        return None
    annualization = comparison.get("annualization") or {}
    annual = comparison.get("annual")
    eligibility_known = isinstance(annualization.get("months"), dict)
    has_annual = bool(annual) and annual.get("method") in ("strict", "estimated") and isinstance(annual.get("rows"), list)
    annualization_available = has_annual or (not annual and eligibility_known and bool(annualization.get("available")))
    reason = (annual or {}).get("status_reason") or annualization.get("reason")
    if annualization_available:
        status = None
    elif not eligibility_known:
        status = "rerun"
    else:
        status = reason if reason in ("missing_sell_price", "missing_sell_cost") else "estimate_unavailable"
    warnings = comparison.get("warnings") or {}
    estimated = (annual or {}).get("method") == "estimated"
    provenance = (annual or {}).get("provenance") or {}
    lines = payback_series(comparison, annual, t, language) if has_annual else None

    rows = []
    has_no_payback = False
    for row in comparison["rows"]:
        annual_row = next((item for item in annual["rows"] if item.get("module_count") == row["added_modules"]), None) if has_annual else None
        marginal = (annual_row or {}).get("marginal_vs_previous") or {}
        gain = marginal.get("annual_gain") if has_annual else row.get("annualized_incremental_gain")
        years = marginal.get("payback_years") if has_annual else row.get("payback_years")
        no_payback = row["added_modules"] > 0 and annualization_available and not _finite(years)
        line = next((item for item in lines or [] if item["modules"] == row["added_modules"]), None)
        whole_years = line["total_years"] if line else None
        whole_no_payback = bool(line) and whole_years is None
        has_no_payback = has_no_payback or no_payback or whole_no_payback
        rows.append({
            "added_modules": row["added_modules"],
            "battery_size": row["battery_size"],
            "incremental_gain": _round(row.get("incremental_gain")),
            "annual_gain": _round(gain) if annualization_available else None,
            "module_price": _round(row.get("module_price")),
            "payback_years": _round(years) if row["added_modules"] > 0 and annualization_available else None,
            "no_payback": no_payback,
            "whole_cost": _round(line["start"]) if line else None,
            "whole_gain": _round(lines[0]["rate"] - line["rate"]) if line else None,
            "whole_payback_years": _round(whole_years) if line else None,
            "whole_no_payback": whole_no_payback,
        })

    monthly = None
    baseline = next((row for row in comparison["rows"] if row["added_modules"] == 0), None)
    added = [row for row in comparison["rows"] if row["added_modules"] > 0 and row.get("months")]
    if months and baseline and baseline.get("months") and added:
        monthly = [
            {
                "label": module_count_label(row["added_modules"], language, t),
                "months": {
                    key: _round(row["months"][key] - baseline["months"][key])
                    for key in months["order"]
                    if _finite(row["months"].get(key)) and _finite(baseline["months"].get(key))
                },
            }
            for row in spread(added, MAX_MONTHLY_SERIES)
        ]

    chart = _payback_chart(lines, t) if lines else None
    payback_apex = payback_chart(chart, t("dashboard.what_if_module_years")) if chart else None
    return {
        "status": status,
        "method": (annual or {}).get("method"),
        "matched_hours": annualization.get("matched_hours"),
        "represented_months": len(annualization.get("represented_months") or []),
        "estimated": estimated,
        "estimated_hours": provenance.get("estimated_hours") if estimated else None,
        "estimated_months": list(provenance.get("estimated_from_other_months") or []) if estimated else [],
        "warnings": {key: warnings.get(key) or 0 for key in (
            "dst_replay_resets", "tariff_estimated_hours", "raw_current_sell_cost_hours", "raw_unknown_sell_cost_hours",
        )},
        "summary": module_summary(annual, comparison, lines, t, language) if has_annual else [],
        "rows": rows,
        "has_no_payback": has_no_payback,
        "payback_missing": has_annual and lines is None,
        "payback_chart": chart,
        "payback_apex": payback_apex,
        "monthly": monthly,
    }


def _rules_view(simulation: dict, t: Translate, currency: str) -> dict:
    """The rules and settings used by Current and the scenario, one row per rule that is active on either side."""
    sides = [simulation.get("current_assumptions"), simulation.get("assumptions")]
    known = [bool(side) and isinstance(side.get("rules"), dict) for side in sides]

    def active(side: dict | None) -> bool:
        return bool(side) and _finite(side.get("battery_size")) and side["battery_size"] > 0

    def enabled(side: dict | None, key: str) -> bool:
        if not active(side) or not isinstance(side.get("rules"), dict) or side["rules"].get(key) is not True:
            return False
        if key == "cheapest_hour_fill_up" and side["rules"].get("cheapest_hour_fill_planner") is not True:
            return False
        return not (key in ("optimize_solar_charging", "discharge_before_solar") and side["rules"].get("solar_charging") is not True)

    def selling(side: dict | None) -> bool:
        return active(side) and (
            any(enabled(side, key) for key in _SELLING_RULES)
            or (enabled(side, "discharge_before_solar") and enabled(side, "solar_charging"))
        )

    dash = "—"
    result = {"known": known, "groups": []}
    if not any(known):
        return result
    for group, rules, parameters in _RULE_GROUPS:
        rows: list[list[str]] = []
        if group == "charging" and any(known[index] and active(side) for index, side in enumerate(sides)):
            rows.append([
                t("dashboard.what_if_rule_mode"),
                *[
                    t("dashboard.what_if_rule_optimizer") if known[index] and active(side) and side.get("control_mode") == "optimizer"
                    else t("dashboard.what_if_rule_rules") if known[index] and active(side) and side.get("control_mode") == "rules"
                    else dash
                    for index, side in enumerate(sides)
                ],
            ])
        for key in rules:
            if any(known[index] and enabled(side, key) for index, side in enumerate(sides)):
                rows.append([
                    t(f"dashboard.what_if_rule_{key}"),
                    *[t("dashboard.what_if_rule_on") if known[index] and enabled(side, key) else dash for index, side in enumerate(sides)],
                ])
        for key in parameters:
            unit = _PARAMETER_UNITS[key]
            values = []
            for index, side in enumerate(sides):
                values.append(_parameter_text(side, key, unit, known[index], active, enabled, selling, currency))
            if any(value is not None for value in values):
                rows.append([t(f"dashboard.what_if_rule_{key}"), *[value if value is not None else dash for value in values]])
        if rows:
            result["groups"].append({"group": group, "title": t(f"dashboard.what_if_rule_group_{group}"), "rows": rows})
    return result


def _parameter_text(side, key, unit, known, active, enabled, selling, currency) -> str | None:
    if not known or not active(side):
        return None
    if key.startswith("cheap") and not enabled(side, "cheapest_hour_fill_planner"):
        return None
    if key == "powerwall_max_hours_old" and not enabled(side, "force_powerwall_max_hours_old_discharge"):
        return None
    if key in ("min_total_profit", "min_sell_kwh", "exclude_sell_hours") and not selling(side):
        return None
    value = side.get(key)
    if unit == "text":
        hours = [hour for hour in value.replace(" ", "").split(",") if hour.isdigit()] if isinstance(value, str) else []
        return ",".join(hours) if hours else None
    if not _finite(value):
        return None
    if unit in ("currency", "currency_kwh"):
        return f"{value:.2f} {currency}{'/kWh' if unit == 'currency_kwh' else ''}"
    if unit == "kWh":
        return f"{value:.1f} kWh"
    return f"{value:g}{' ' + unit if unit else ''}"


_INPUT_LABELS = {
    "history_days": "dashboard.what_if_history",
    "battery_size": "dashboard.what_if_battery",
    "range_start": "dashboard.what_if_range_start",
    "range_end": "dashboard.what_if_range_end",
    "range_step": "dashboard.what_if_range_step",
    "module_capacity": "dashboard.what_if_module_capacity",
    "module_price": "dashboard.what_if_module_price",
    "module_count": "dashboard.what_if_module_count",
}


# a capacity is shown with one decimal, a price with two, the days and the number of modules as they are
_INPUT_DECIMALS = {
    "battery_size": 1, "range_start": 1, "range_end": 1, "range_step": 1, "module_capacity": 1, "module_price": 2,
}


def _input_text(key: str, value: float) -> str:
    decimals = _INPUT_DECIMALS.get(key)
    return f"{value:g}" if decimals is None else f"{value:.{decimals}f}"


def _inputs_view(inputs: dict | None, t: Translate) -> list[dict[str, str]]:
    """The values the simulation was started with, as label and value (a rule switch as enabled or disabled)."""
    result = []
    for key, value in (inputs or {}).items():
        if key == "config_entry_id":
            continue
        label = None
        for candidate in (_INPUT_LABELS.get(key), f"dashboard.what_if_rule_{key}", f"ui.what_if.{key}"):
            if candidate and t(candidate) != candidate:
                label = t(candidate)
                break
        if isinstance(value, bool):
            text = t("ui.what_if.enabled" if value else "ui.what_if.disabled")
        elif _finite(value):
            text = _input_text(key, value)
        else:
            text = str(value)
        result.append({"label": label or key, "value": text})
    return result


def build_view(
    simulation: dict, t: Translate, language: str, currency: str, inputs: dict | None = None
) -> dict:
    """The dashboard values of one simulation result (see the module docstring).

    t: the runtime text lookup of the entry (coordinator.t); language: its language code;
    inputs: the values the simulation was started with (shown as the selected values).
    """
    current = simulation["current"]
    scenario = simulation["scenario"]
    months = _monthly_series(simulation)
    return {
        "inputs": _inputs_view(inputs, t),
        "summary": {
            "current_capacity": simulation["current_assumptions"]["battery_size"],
            "scenario_capacity": simulation["assumptions"]["battery_size"],
            "profit": {
                "current": _round(current["profit"]), "scenario": _round(scenario["profit"]),
                "difference": _round(simulation["difference"]["profit"]),
            },
            "bill": {
                "current": _round(current["with"]["bill"]), "scenario": _round(scenario["with"]["bill"]),
                "difference": _round(simulation["difference"]["bill"]),
            },
        },
        "months": months,
        "range": _range_view(simulation, months, t),
        "module": _module_view(simulation, months, t, language),
        "rules": _rules_view(simulation, t, currency),
    }
