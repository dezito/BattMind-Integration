"""The newer planner rules and the whole horizon optimiser (shadow plan)."""
from __future__ import annotations

import datetime
import math
import re
import sys
from copy import deepcopy

import pytest

from custom_components.battmind.const import CHARGING_TYPES, build_config
from custom_components.battmind.engine import BattMindEngine, Snapshot
from custom_components.battmind.engine.optimizer import (
    RULE_SWITCHES,
    OptimizerConfig,
    OptimizerHour,
    OptimizerResult,
    explain,
    optimize,
    simulate,
    step_hour,
)
from custom_components.battmind.engine.planner.replay import _known_until, _next_planning
from custom_components.battmind.engine.planner import what_if as what_if_module
from custom_components.battmind.engine.planner.what_if import _nearest_eds_tariffs, _reference_months
from custom_components.battmind.prices.base import calculate_sell_price

from .test_engine import DAY, OPTIONS, SOLAR_SURPLUS, TEXTS, TZ, _power_values_db, charge_hours, snapshot

NEGATIVE_HOURS = (1, 2, 3, 12, 13)
OFF = {
    "optimizer_control": False,
    "cheapest_hour_fill_planner": False,
    "needed_before_max_level_planner": False,
    "only_discharge_on_profit": False,
}


def test_what_if_uses_nearest_historical_eds_tariff_and_live_sell_formula() -> None:
    history = [
        (
            100.0,
            {
                "additional_tariffs": {
                    "transmissions_nettarif": 0.1,
                    "systemtarif": 0.1,
                    "elafgift": 0.5,
                },
                "tariffs": {"12": 0.2},
            },
        ),
        (
            300.0,
            {
                "additional_tariffs": {
                    "transmissions_nettarif": 0.1,
                    "systemtarif": 0.1,
                    "elafgift": 0.8,
                },
                "tariffs": {"12": 0.3},
            },
        ),
    ]

    nearest = _nearest_eds_tariffs(history, 200.0, 12)

    assert nearest is not None
    assert nearest == (
        {
            "transmissions_nettarif": 0.1,
            "systemtarif": 0.1,
            "elafgift": 0.5,
            "tariffs": 0.2,
            "surcharge": 0.0,
        },
        False,
    )
    assert calculate_sell_price(0.8, nearest[0], 0.03) == pytest.approx(-0.33)


def realistic_snapshot(
    now: datetime.datetime, battery_level: float, settings: dict | None = None, negative: bool = False, price_fn=None, quarters: bool = False
) -> Snapshot:
    """Sell price = buy - 0.9; negative: buy -0.4 (sell -0.9) in NEGATIVE_HOURS today.

    price_fn(hour timestamp) -> buy price replaces the default price shape of every known day.
    quarters: every hour is four price periods of 15 minutes with the price of the hour."""
    snap = snapshot(now, battery_level, settings)
    buy = snap.price_data["attributes"]["sensor.price"]
    if price_fn is not None:
        for rows in (buy["raw_today"], buy["raw_tomorrow"], buy["forecast"]):
            for row in rows:
                row["price"] = price_fn(datetime.datetime.fromisoformat(row["hour"]))
    if negative:
        for row in buy["raw_today"]:
            if datetime.datetime.fromisoformat(row["hour"]).hour in NEGATIVE_HOURS:
                row["price"] = -0.4
    snap.price_data["attributes"]["sensor.sell"] = {
        key: [{**row, "price": round(row["price"] - (0.9 if row["price"] > 0 else 0.5), 3)} for row in rows] if isinstance(rows, list) else rows
        for key, rows in buy.items()
    }
    snap.entity_states["sensor.sell"] = "0.3"
    if quarters:
        for entity in ("sensor.price", "sensor.sell"):
            attributes = snap.price_data["attributes"][entity]
            for key, rows in list(attributes.items()):
                if isinstance(rows, list) and rows and "hour" in rows[0]:
                    attributes[key] = [
                        {**row, "hour": (datetime.datetime.fromisoformat(row["hour"]) + datetime.timedelta(minutes=15 * quarter)).isoformat()}
                        for row in rows
                        for quarter in range(4)
                    ]
    return snap


def planned(
    hour: int, battery_level: float, settings: dict, solar: bool | list = False, negative: bool = False, days: int | None = None, price_fn=None,
    options: dict | None = None, quarters: bool = False,
) -> tuple[BattMindEngine, object]:
    now = datetime.datetime.combine(DAY, datetime.time(hour, 30))
    options = {**OPTIONS, "energidataservice_sell_entity_id": "sensor.sell", **(options or {})}
    if days:
        options["days_to_predict"] = days
    engine = BattMindEngine(build_config(options), "test", TEXTS)
    assert engine.run_job(
        realistic_snapshot(now, battery_level, negative=negative, price_fn=price_fn, quarters=quarters), engine.startup, {"power_values": _power_values_db()}
    ).error is None
    if solar:
        # solar=True: the default surplus, or a list with the surplus (kWh) per hour of every day
        surplus = SOLAR_SURPLUS if solar is True else solar
        engine.solar_prediction_db = {day: {"total": surplus, "total_sell": [0.5] * 24} for day in range(9)}
    result = engine.run_job(
        realistic_snapshot(now, battery_level, settings, negative=negative, price_fn=price_fn, quarters=quarters), engine.charge_if_needed, True
    )
    assert result.error is None, result.error
    assert not [message for message, title, _ in result.notifications if "error" in title.lower()]
    return engine, result


def sales(engine: BattMindEngine) -> dict:
    return {ts: sale for day in engine.plan if isinstance(day, int) for ts, sale in engine.plan[day]["force_discharge_timestamps"].items()}


def test_negative_prices_fill_the_battery() -> None:
    engine, _ = planned(0, 30, {**OFF, "negative_price_planner": True}, negative=True)
    hours = [ts for ts in charge_hours(engine) if engine.charge_hours[ts].get("negative_price_planner")]
    assert hours and {ts.hour for ts in hours} <= set(NEGATIVE_HOURS)
    assert max(sum(engine.plan[0]["battery_level_flow"][hour]) for hour in (1, 2, 3)) == pytest.approx(100.0, abs=1.0)


def test_grid_charge_to_sell_buys_cheap_and_sells_expensive() -> None:
    engine, _ = planned(0, 5, {**OFF, "grid_charge_to_sell": True})
    bought = [ts for ts in charge_hours(engine) if engine.charge_hours[ts].get("grid_charge_to_sell")]
    sold = {ts: sale for ts, sale in sales(engine).items() if sale["title"] == "Sælger strøm købt billigt"}
    assert bought and sold
    assert all(sale["profit"] > 0.0 for sale in sold.values())
    assert min(sold) > min(bought)


def test_discharge_before_solar_when_the_solar_export_costs_money() -> None:
    """Solar surplus sold at -0.9 at noon: empty the battery in the morning so the sun has room."""
    engine, _ = planned(4, 95, {**OFF, "discharge_before_solar": True}, solar=True, negative=True)
    before_solar = [ts for ts, sale in sales(engine).items() if sale["title"] == "Sælger før solen fylder batteriet"]
    assert before_solar and all(ts.date() == DAY and ts.hour < 9 for ts in before_solar)


def _kwh_per_hour(engine: BattMindEngine) -> dict:
    """Planned grid kWh per clock hour (the periods of an hour together)."""
    per_hour: dict = {}
    for timestamp in charge_hours(engine):
        hour = timestamp.replace(minute=0)
        per_hour[hour] = per_hour.get(hour, 0.0) + engine.charge_hours[timestamp]["kWh"]
    return per_hour


def _only(rule: str) -> dict:
    return {key: False for key in RULES_BESIDES_MOST_PROFIT} | {"optimizer_control": False, "most_profit": False, rule: True}


@pytest.mark.parametrize("limit_watt", [2500.0, 3300.0])
def test_planned_charging_never_exceeds_the_charge_power(limit_watt: float) -> None:
    """The kWh left in an hour was rounded to 0.1 kWh, which rounds up (2.51 kWh in an hour at 2.5 kW)."""
    engine, _ = planned(0, 5, _only("most_expensive_planner"), options={"powerwall_charging_power_limit": limit_watt})
    per_hour = _kwh_per_hour(engine)
    assert per_hour
    assert max(per_hour.values()) <= limit_watt / 1000 + 1e-9


@pytest.mark.parametrize("rule", ["most_expensive_planner", "cheapest_hour_fill_planner", "grid_charge_to_sell"])
def test_planned_charging_with_15_minute_prices_stays_within_the_charge_power(rule: str) -> None:
    """Each price period takes its share of the charge power: the four periods of an hour together plan at most
    what the hour takes (a period used to be given what was left of the hour: up to 2.5 times the charge power)."""
    engine, _ = planned(3, 5, _only(rule), options={"powerwall_charging_power_limit": 1000.0}, quarters=True)
    per_hour = _kwh_per_hour(engine)
    assert per_hour and max(per_hour.values()) <= 1.0 + 1e-9
    assert all(engine.charge_hours[timestamp]["kWh"] <= 0.25 + 1e-9 for timestamp in charge_hours(engine))


def test_battery_level_forecast_counts_every_charge_period_of_the_hour() -> None:
    """With 15 minute prices the hour has up to four charge periods: the forecast level adds all of them."""
    engine, _ = planned(3, 5, _only("most_expensive_planner"), options={"powerwall_charging_power_limit": 1000.0}, quarters=True)
    day = engine.plan[0]
    hour = min(timestamp.hour for timestamp in day["charging_sessions"] if timestamp.hour > 3)
    sessions = [session["battery_level"] for timestamp, session in day["charging_sessions"].items() if timestamp.hour == hour]
    assert len(sessions) >= 2
    assert day["battery_level_flow"][hour][-1] == pytest.approx(sum(sessions), abs=0.06)


@pytest.mark.parametrize("reserve", [20.0, 50.0])
def test_buy_to_sell_does_not_export_below_the_reserve_for_the_house(reserve: float) -> None:
    """Reserve for the house: no rule plans an export below it, also not the one that buys the kWh to sell."""
    only_buy_to_sell = {key: False for key in RULES_BESIDES_MOST_PROFIT} | {"optimizer_control": False, "most_profit": False, "grid_charge_to_sell": True}
    free, _ = planned(7, 50, {**only_buy_to_sell, "usage_reserve": 0.0})
    assert [sale for sale in sales(free).values() if sale["emoji"] == BUY_TO_SELL], "without a reserve the scenario sells"
    engine, _ = planned(7, 50, {**only_buy_to_sell, "usage_reserve": reserve})
    for timestamp, sale in sales(engine).items():
        if sale["emoji"] == BUY_TO_SELL:
            level_after = sum(engine.plan[(timestamp.date() - DAY).days]["battery_level_flow"][timestamp.hour])
            assert level_after >= reserve - 1.0, f"{timestamp:%d %H}h ends at {level_after:.1f}% under the reserve {reserve}%"


@pytest.mark.parametrize("optimizer_control", [False, True])
@pytest.mark.parametrize("level", [5, 100])
def test_nothing_is_sold_with_a_reserve_of_100_percent(optimizer_control: bool, level: float) -> None:
    """With every rule on and the whole battery reserved for the house, no plan exports anything."""
    for price_fn in (None, negative_midday_price):
        engine, _ = planned(7, level, {"optimizer_control": optimizer_control, "usage_reserve": 100.0}, solar=True, price_fn=price_fn)
        assert sales(engine) == {}


def inverted_price(ts: datetime.datetime) -> float:
    """Dear at night, cheap in the day, dearest in the evening."""
    return round(2.5 - (1.5 if 17 <= ts.hour <= 20 else 0.6 if 7 <= ts.hour <= 16 else 0.0) + ts.hour / 100, 3)


def test_solar_blocking_does_not_rely_on_a_later_grid_charge_to_refill_the_battery() -> None:
    """Planned at 12:30 with 80 % in a 3 kWh battery, 0.5 kWh of sun per hour until 14:00 and a grid charge in the evening.

    Blocking 13:00 sells the sun, and the evening grid charge would fill the battery again: a bet on the grid, not on
    the later sun (docs/rules/solar.md), so the 80 % margin does not allow it."""
    sun = [0.0] * 10 + [0.5] * 4 + [0.0] * 10
    settings = {"optimizer_control": False, "discharge_before_solar": False, "optimize_solar_charging": True, "solar_charging": True}
    engine, _ = planned(
        12, 80, settings, solar=sun, price_fn=inverted_price,
        options={"powerwall_battery_size": 3.0, "powerwall_charge_discharge_loss": 0.0, "sell_fee": 0.2},
    )
    assert [ts for ts in charge_hours(engine) if ts.date() == DAY and ts.hour > 14], "the scenario charges from the grid in the evening"
    assert not [ts for day in engine.plan if isinstance(day, int) for ts in engine.plan[day]["blocked_solar_charge_timestamps"]]


RULES_BESIDES_MOST_PROFIT = (
    "cheapest_hour_fill_planner", "cheapest_hour_fill_up", "most_expensive_planner", "needed_before_max_level_planner",
    "only_discharge_on_profit", "prioritize_discharge_hours_by_energy_cost", "sell_excess_kwh_available",
    "negative_price_planner", "grid_charge_to_sell", "discharge_before_solar", "force_powerwall_max_hours_old_discharge",
    "optimize_solar_charging", "use_midnight_battery_level",
)
DUCK_BATTERY = {
    "powerwall_battery_level_min": 20.0, "powerwall_battery_level_max": 80.0, "powerwall_charging_power_limit": 1000.0,
    "powerwall_discharging_power": 1000.0, "powerwall_wear_cost_per_kwh": 0.5, "sell_fee": 0.2,
}


def duck_price(ts: datetime.datetime) -> float:
    """Cheap in the middle of the day, dear in the morning and evening."""
    return 0.2 if 10 <= ts.hour <= 15 else 2.2 if 6 <= ts.hour <= 9 or 17 <= ts.hour <= 21 else 0.9


@pytest.mark.parametrize("min_total_profit", [0.5, 2.0])
def test_most_profit_sale_earns_the_min_total_profit_shown_in_the_plan(min_total_profit: float) -> None:
    """The profit of a sale is the sell price minus what the kWh cost in the battery (incl. loss and wear).

    With a high wear the battery kWh costs more than the house would pay for it, so the gain compared with using the
    energy (what was checked before) is bigger than the profit shown, and a sale of 0.04 kr passed 0.50 kr."""
    only_most_profit = {key: False for key in RULES_BESIDES_MOST_PROFIT} | {"optimizer_control": False, "most_profit": True}

    def most_profit_sales(minimum: float) -> list[dict]:
        engine, _ = planned(14, 100, {**only_most_profit, "min_total_profit": minimum}, price_fn=duck_price, options=DUCK_BATTERY)
        return [sale for sale in sales(engine).values() if sale["emoji"] == "🤑"]

    assert most_profit_sales(0.0), "without the limit the scenario sells"
    assert all(sale["profit"] >= min_total_profit - 0.011 for sale in most_profit_sales(min_total_profit))


@pytest.mark.parametrize("optimizer_control", [False, True])
@pytest.mark.parametrize("negative", [False, True])
def test_never_charge_and_sell_in_the_same_hour(negative: bool, optimizer_control: bool) -> None:
    rules = {
        key: True
        for key in (
            "most_expensive_planner", "prioritize_discharge_hours_by_energy_cost", "sell_excess_kwh_available", "most_profit",
            "negative_price_planner", "grid_charge_to_sell", "discharge_before_solar", "optimize_solar_charging",
        )
    }
    engine, _ = planned(0, 30, {**rules, "optimizer_control": optimizer_control}, solar=True, negative=negative)
    charged = {ts.replace(minute=0) for ts in charge_hours(engine)}
    sold = {ts.replace(minute=0) for ts in sales(engine)}
    assert charged and not charged & sold


@pytest.mark.parametrize("days", [2, 5])
def test_shadow_plan_covers_days_to_predict_and_beats_the_rules(days: int) -> None:
    engine, result = planned(3, 30, {"optimizer_control": False, "most_profit": True}, solar=True, days=days)
    shadow = engine.optimizer_shadow
    # prices exist for today + 3 days, the horizon is days_to_predict as far as there are prices
    assert len(shadow["hours"]) == 21 + (min(days, 4) - 1) * 24
    assert shadow["optimal"] <= shadow["rules"] + 0.01
    assert shadow["optimal"] <= shadow["self_consumption"] + 0.01
    summary = result.attributes["powerwall_action"]["optimizer_shadow_summary"]
    assert summary["optimal"] == shadow["optimal"]


def negative_midday_price(ts: datetime.datetime) -> float:
    """The default price shape with negative prices at night and at noon, every day."""
    if ts.hour in NEGATIVE_HOURS:
        return -0.4
    return 1.0 + (1.5 if 17 <= ts.hour <= 20 else 0.6 if 7 <= ts.hour <= 16 else 0.0) + ts.hour / 100


BUY_TO_SELL = CHARGING_TYPES["grid_charge_to_sell"]["emoji"]


def test_optimiser_follows_a_rule_switch_that_needs_many_rounds() -> None:
    """Forbidding a sale moves it to the next best hour, which is forbidden in the next round: with negative prices
    every day it takes more than 6 rounds before no sale is left that belongs to Buy to sell (the limit used to be 6)."""
    engine, _ = planned(18, 50, {"optimizer_control": True, "grid_charge_to_sell": False}, price_fn=negative_midday_price, days=3)
    assert engine.optimizer_shadow["controls"] and engine.optimizer_shadow["converged"]
    assert [ts for ts, sale in sales(engine).items() if sale["emoji"] == BUY_TO_SELL] == []
    # the switch on: the same plan does use Buy to sell (the check above is not trivial)
    on, _ = planned(18, 50, {"optimizer_control": True, "grid_charge_to_sell": True}, price_fn=negative_midday_price, days=3)
    assert [ts for ts, sale in sales(on).items() if sale["emoji"] == BUY_TO_SELL]


def test_rules_control_the_battery_when_the_optimiser_does_not_settle(monkeypatch) -> None:
    """A plan that still has actions of switched off rules after the last round is not used: the rules take over."""
    from custom_components.battmind.engine import optimizer as optimizer_module

    monkeypatch.setattr(optimizer_module.optimize_with_rules, "__defaults__", (1,))
    engine, result = planned(18, 50, {"optimizer_control": True, "grid_charge_to_sell": False}, price_fn=negative_midday_price, days=3)
    shadow = engine.optimizer_shadow
    assert shadow["converged"] is False and not shadow.get("controls")
    assert result.attributes["powerwall_action"]["optimizer_shadow_summary"]["converged"] is False
    assert result.states["powerwall_action"]  # a valid plan from the rules


def _not_finite(value, depth: int = 0) -> list:
    """Paths of NaN / infinity in nested dicts, lists and tuples."""
    found: list = []
    if depth > 8:
        return found
    if isinstance(value, float) and not math.isfinite(value):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found += _not_finite(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found += _not_finite(item, depth + 1)
    return found


@pytest.mark.parametrize("optimizer_control", [False, True])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_a_price_that_is_not_a_number_does_not_reach_the_plan(bad: float, optimizer_control: bool) -> None:
    """One broken hour price used to make the totals NaN (or a sale in an hour that earns infinity)."""

    def price(ts: datetime.datetime) -> float:
        return bad if (ts.date() - DAY).days == 1 and ts.hour == 14 else negative_midday_price(ts)

    engine, result = planned(3, 30, {"optimizer_control": optimizer_control}, solar=True, price_fn=price)
    assert _not_finite(result.attributes) == [] and _not_finite(engine.plan) == [] and _not_finite(engine.optimizer_shadow) == []
    assert all(math.isfinite(value) for value in engine.price_provider.get_buy_prices().values())
    assert datetime.datetime.combine(DAY + datetime.timedelta(days=1), datetime.time(14)) not in engine.price_provider.get_buy_prices()


UNSUPPORTED_ATTRIBUTES = {
    "grid_charging": "charge_timestamps",
    "force_discharge": "force_discharge_timestamps",
    "force_solar_only_charging": "force_solar_only_charging_timestamps",
    "blocked_solar_charge": "blocked_solar_charge_timestamps",
}


@pytest.mark.parametrize("optimizer_control", [False, True])
@pytest.mark.parametrize("action", sorted(UNSUPPORTED_ATTRIBUTES))
def test_an_unsupported_action_is_not_announced_for_any_hour(action: str, optimizer_control: bool) -> None:
    """BattMind never plans an action the inverter can't run: the hours of the plan are listed under the actions that
    run, for dashboards and automations (the hours without another action run discharge_allowed instead of
    force_solar_only_charging)."""
    engine, result = planned(3, 30, {"optimizer_control": optimizer_control}, solar=True, options={"unsupported_actions": [action]})
    attributes = result.attributes["powerwall_action"]
    assert attributes[UNSUPPORTED_ATTRIBUTES[action]] == []
    assert result.states["powerwall_action"] != action
    # every planned hour is listed under an action that runs (the hours without one run discharge_allowed)
    running = ["discharge_timestamps", *(attribute for name, attribute in UNSUPPORTED_ATTRIBUTES.items() if name != action)]
    announced = set().union(*(set(attributes[name]) for name in running))
    assert {row["hour"] for row in attributes["battery_level_flow"]} <= announced


def spread_price(ts: datetime.datetime) -> float:
    """Cheap at night, dear in the evening, in between a middle price."""
    return 0.3 if ts.hour < 6 else (4.0 if 17 <= ts.hour <= 20 else 1.5)


def test_an_hour_that_charges_the_battery_is_not_also_held_back() -> None:
    """Profit only / Prioritise by energy cost hold discharging back before the charge hours are planned: an hour that
    gets a charge loses its hold (its reason and level would be stale)."""
    now = datetime.datetime.combine(DAY, datetime.time(2, 30))
    engine = BattMindEngine(build_config({**OPTIONS, "energidataservice_sell_entity_id": "sensor.sell", "days_to_predict": 1, "powerwall_charge_discharge_loss": 0.0}), "test", TEXTS)
    assert engine.run_job(realistic_snapshot(now, 40, price_fn=spread_price), engine.startup, {"power_values": _power_values_db()}).error is None
    for ago, pct, price in ((129, 50, 0.72), (12, 5, 0.92)):
        engine.charging_history_db[now.replace(minute=0) - datetime.timedelta(hours=ago)] = {
            "charged": {"percentage": float(pct), "kWh": pct / 10, "kWh_from_grid": pct / 10, "kWh_from_local_energy": 0.0, "cost": pct / 10 * price, "price": price}
        }
    settings = {"optimizer_control": False, "only_discharge_on_profit": True, "prioritize_discharge_hours_by_energy_cost": True}
    result = engine.run_job(realistic_snapshot(now, 40, settings, price_fn=spread_price), engine.charge_if_needed, True)
    assert result.error is None, result.error
    day = engine.plan[0]
    held, charged = {ts.hour for ts in day["blocked_discharge_timestamps"]}, {ts.hour for ts in day["charging_sessions"]}
    assert charged, "the scenario charges"
    assert not held & charged


@pytest.mark.parametrize("hour", [0, 12])
def test_profit_of_an_optimised_sale_is_after_what_the_kwh_cost_in_the_battery(hour: int) -> None:
    """The plan shows what a sale earns: sell price minus the cost of the kWh in the battery (not the revenue).

    The default wear is 0.10 kr/kWh, so the profit per kWh is at least that much less than the sell price."""
    engine, _ = planned(hour, 5, {"optimizer_control": True, "grid_charge_to_sell": True, "most_profit": True}, solar=True)
    sell = {row["hour"]: row["sell"] for row in engine.optimizer_shadow["hours"]}
    sold = list(sales(engine).items())
    assert sold, "the scenario has to sell"
    for timestamp, sale in sold:
        revenue = sale["kwh"] * sell[timestamp.replace(minute=0)]
        assert sale["profit"] <= revenue - sale["kwh"] * 0.10 + 0.01


def test_profit_requirements_are_shown_as_a_steering_figure_for_every_plan() -> None:
    """The optimisation adds the profit requirements to the cost of its actions (not money): they are reported next
    to the real totals, calculated the same way for the optimal plan, the rules and plain self consumption."""
    zero_engine, _ = planned(3, 30, {"optimizer_control": True}, solar=True)
    assert zero_engine.optimizer_shadow["requirements"] == {"optimal": 0.0, "rules": 0.0, "self_consumption": 0.0}

    engine, result = planned(3, 30, {"optimizer_control": True, "min_profit_per_kwh": 0.5, "min_total_profit": 1.0}, solar=True)
    requirements = engine.optimizer_shadow["requirements"]
    assert set(requirements) == {"optimal", "rules", "self_consumption"}
    # self consumption delivers the battery's kWh to the house: 0.50 per kWh out, no sales
    assert requirements["self_consumption"] > 0.0
    assert requirements["optimal"] > 0.0 and requirements["rules"] > 0.0
    assert result.attributes["powerwall_action"]["optimizer_shadow_summary"]["requirements"] == requirements
    overview = result.attributes["overview"]["overview"]
    assert f"{requirements['optimal']:.2f}" in overview.split("🎯", 1)[1].splitlines()[0]
    assert "🎯" not in zero_engine.last_overview


def test_what_if_simulator_does_not_change_the_active_plan() -> None:
    now = datetime.datetime.combine(DAY, datetime.time(3, 30))
    engine, _ = planned(3, 40, {"optimizer_control": True}, solar=True)
    active_plan = engine.optimizer_shadow
    snap = realistic_snapshot(now, 40, {"optimizer_control": True})
    ids = engine.config["home"]["entity_ids"]
    solar_id = engine.config["solar"]["entity_ids"]["production_entity_id"]
    buy_id, sell_id = "sensor.historical_buy", "sensor.historical_sell"
    start = datetime.datetime.combine(DAY - datetime.timedelta(days=1), datetime.time())
    timestamps = [(start + datetime.timedelta(hours=hour)).timestamp() for hour in range(48)]
    data = {
        "buy_entity_id": buy_id,
        "sell_entity_id": sell_id,
        "stats": {
            ids["power_consumption_entity_id"]: [(ts, 1000.0) for ts in timestamps],
            ids["powerwall_watt_flow_entity_id"]: [(ts, 0.0) for ts in timestamps],
            solar_id: [
                (ts, 1500.0 if 9 <= datetime.datetime.fromtimestamp(ts).hour <= 15 else 0.0)
                for ts in timestamps
            ],
            buy_id: [(ts, 0.5 if datetime.datetime.fromtimestamp(ts).hour < 6 else 2.0) for ts in timestamps],
            sell_id: [(ts, 0.4 if datetime.datetime.fromtimestamp(ts).hour < 6 else 1.5) for ts in timestamps],
        },
    }
    result = engine.run_job(
        snap,
        engine.what_if,
        data,
        {
            "battery_size": 20,
            "max_charging_power": 8000,
            "round_trip_loss": 5,
            "wear_cost_per_kwh": 0,
        },
    )

    assert result.error is None, result.error
    simulation = result.value
    assert simulation["assumptions"]["battery_size"] == 20
    assert simulation["assumptions"]["max_charging_power"] == 8000
    assert simulation["assumptions"]["round_trip_loss"] == pytest.approx(5)
    assert simulation["scenario"]["start"] == simulation["current"]["start"]
    assert simulation["period"]["hours"] == 48
    assert simulation["period"]["days"] == 2.0
    assert simulation["data_sources"]["long_term_statistics_samples"] > 0
    assert simulation["data_sources"]["recorder_history_fallback_samples"] == 0
    assert "plan" not in simulation
    assert engine.optimizer_shadow is active_plan

    unchanged = engine.run_job(snap, engine.what_if, data, {})
    assert unchanged.error is None, unchanged.error
    assert unchanged.value["difference"] == {"profit": 0.0, "bill": 0.0}

    no_sell_history = engine.run_job(
        snap,
        engine.what_if,
        {**data, "sell_price_history_available": False},
        {},
    )
    assert no_sell_history.error is None, no_sell_history.error
    assert no_sell_history.value["assumptions"]["sell_price_history_available"] is False
    assert no_sell_history.value["current"]["without"]["solar_income"] == 0.0

    changed_rules = engine.run_job(
        snap,
        engine.what_if,
        data,
        {"most_profit": False, "min_profit_per_kwh": 0.25},
    )
    assert changed_rules.error is None, changed_rules.error
    assert (
        changed_rules.value["current_assumptions"]["rules"]["most_profit"]
        == engine.enabled("most_profit")
    )
    assert changed_rules.value["assumptions"]["rules"]["most_profit"] is False
    assert changed_rules.value["assumptions"]["min_profit_per_kwh"] == pytest.approx(0.25)

    profitable_sell_data = {
        **data,
        "stats": {
            **data["stats"],
            sell_id: [
                (ts, 3.0 if datetime.datetime.fromtimestamp(ts).hour >= 6 else 0.4)
                for ts in timestamps
            ],
        },
    }
    no_sales = engine.run_job(
        snap,
        engine.what_if,
        profitable_sell_data,
        {
            "most_profit": False,
            "sell_excess_kwh_available": False,
            "force_powerwall_max_hours_old_discharge": False,
            "discharge_before_solar": False,
            "grid_charge_to_sell": False,
        },
    )
    assert no_sales.error is None, no_sales.error
    assert no_sales.value["scenario"]["profit"] < no_sales.value["current"]["profit"]

    eds_buy_id, eds_sell_id = "sensor.eds_buy", "sensor.eds_sell"
    provider_stats = {
        **data["stats"],
        buy_id: [(ts, value) for ts, value in data["stats"][buy_id] if ts >= timestamps[24]],
        sell_id: [(ts, value) for ts, value in data["stats"][sell_id] if ts >= timestamps[24]],
        eds_buy_id: [(ts, 1.0) for ts in timestamps],
        eds_sell_id: [(ts, 0.8) for ts in timestamps],
    }
    eds_provider = {
        "name": "Energi Data Service",
        "buy_entity_id": eds_buy_id,
        "sell_entity_id": eds_sell_id,
        "sell_fee": 0.03,
        "sell_tariff_history": [
            (
                timestamps[0],
                {
                    "additional_tariffs": {
                        "transmissions_nettarif": 0.1,
                        "systemtarif": 0.1,
                        "elafgift": 0.5,
                    },
                    "tariffs": {str(hour): 0.2 for hour in range(24)},
                },
            ),
            (
                timestamps[24],
                {
                    "additional_tariffs": {
                        "transmissions_nettarif": 0.1,
                        "systemtarif": 0.1,
                        "elafgift": 0.8,
                    },
                    "tariffs": {str(hour): 0.3 for hour in range(24)},
                },
            ),
        ],
    }
    providers = [
        {"name": "Strømligning", "buy_entity_id": buy_id, "sell_entity_id": sell_id},
        eds_provider,
    ]
    provider_data = {**data, "stats": provider_stats, "price_providers": providers}
    # Strømligning has priority; Energi Data Service only fills the hours it lacks.
    fallback_result = engine.run_job(snap, engine.what_if, provider_data, {})
    assert fallback_result.error is None, fallback_result.error
    assert fallback_result.value["price_provider"] == "Strømligning, Energi Data Service"
    assert fallback_result.value["price_provider_hours"] == {
        "Strømligning": len(timestamps) - 24,
        "Energi Data Service": 24,
    }
    assert fallback_result.value["period"]["hours"] == len(timestamps)
    assert fallback_result.value["tariff_estimated_hours"] == 23
    assert fallback_result.value["tariff_unavailable_hours"] == 0
    priority_result = engine.run_job(
        snap,
        engine.what_if,
        {**provider_data, "stats": {**provider_stats, buy_id: data["stats"][buy_id], sell_id: data["stats"][sell_id]}},
        {},
    )
    assert priority_result.error is None, priority_result.error
    assert priority_result.value["price_provider_hours"] == {"Strømligning": len(timestamps)}

    eds_data = {**provider_data, "price_providers": [eds_provider]}
    provider_result = engine.run_job(snap, engine.what_if, eds_data, {})
    assert provider_result.error is None, provider_result.error
    assert provider_result.value["price_provider"] == "Energi Data Service"
    assert provider_result.value["period"]["hours"] == len(timestamps)
    assert provider_result.value["tariff_estimated_hours"] == len(timestamps) - 2
    assert provider_result.value["tariff_unavailable_hours"] == 0
    # Hours without known tariffs are left out entirely (no purchases without the matching sales).
    no_eds_tariffs = {**eds_provider, "sell_tariff_history": []}
    missing_tariffs = engine.run_job(
        snap,
        engine.what_if,
        {**provider_data, "price_providers": [providers[0], no_eds_tariffs]},
        {},
    )
    assert missing_tariffs.error is None, missing_tariffs.error
    assert missing_tariffs.value["tariff_unavailable_hours"] == 24
    assert missing_tariffs.value["period"]["hours"] == len(timestamps) - 24
    assert missing_tariffs.value["price_provider_hours"] == {"Strømligning": len(timestamps) - 24}
    only_missing = engine.run_job(
        snap, engine.what_if, {**eds_data, "price_providers": [no_eds_tariffs]}, {}
    )
    assert only_missing.error is not None
    # A raw Energi Data Service price (tariffs disabled) needs no tariff history.
    raw_eds = engine.run_job(
        snap,
        engine.what_if,
        {**eds_data, "price_providers": [{**no_eds_tariffs, "sell_includes_tariffs": False}]},
        {},
    )
    assert raw_eds.error is None, raw_eds.error
    assert raw_eds.value["period"]["hours"] == len(timestamps)
    assert raw_eds.value["tariff_unavailable_hours"] == 0
    assert raw_eds.value["tariff_estimated_hours"] == 0
    assert raw_eds.value["current"]["without"]["solar_income"] != 0.0
    # A provider whose tariff sensors have no history falls back to the next provider.
    no_stromligning_tariffs = engine.run_job(
        snap,
        engine.what_if,
        {
            **provider_data,
            "price_providers": [
                {**providers[0], "sell_tariff_entity_ids": {"elafgift": "sensor.no_tax_history"}},
                eds_provider,
            ],
        },
        {},
    )
    assert no_stromligning_tariffs.error is None, no_stromligning_tariffs.error
    assert no_stromligning_tariffs.value["price_provider_hours"] == {"Energi Data Service": len(timestamps)}
    assert no_stromligning_tariffs.value["tariff_unavailable_hours"] == 0

    missing_indices = (12, 35)
    incomplete_stats = dict(data["stats"])
    for entity_id, gap_index in zip(
        (buy_id, ids["powerwall_watt_flow_entity_id"]), missing_indices
    ):
        incomplete_stats[entity_id] = [
            row for row in incomplete_stats[entity_id] if row[0] != timestamps[gap_index]
        ]
    incomplete = engine.run_job(
        snap, engine.what_if, {**data, "stats": incomplete_stats}, {}
    )
    assert incomplete.error is None, incomplete.error
    assert incomplete.value["period"]["hours"] == len(timestamps) - len(missing_indices)
    assert incomplete.value["period"]["start"] == datetime.datetime.fromtimestamp(
        timestamps[0], snap.tz
    ).replace(tzinfo=None).isoformat()
    assert incomplete.value["period"]["end"] == datetime.datetime.fromtimestamp(
        timestamps[-1], snap.tz
    ).replace(tzinfo=None).isoformat()
    statistics_per_hour = (
        unchanged.value["data_sources"]["long_term_statistics_samples"] // len(timestamps)
    )
    assert incomplete.value["data_sources"]["long_term_statistics_samples"] == (
        unchanged.value["data_sources"]["long_term_statistics_samples"]
        - statistics_per_hour * len(missing_indices)
    )


CFG = OptimizerConfig(battery_size=10.0, level_min=5.0, level_max=100.0, max_charge=5.0, max_discharge=5.0, efficiency=0.95, wear=0.1)


def test_optimizer_charges_cheap_for_the_expensive_hour() -> None:
    hours = [OptimizerHour(0, 0.5, 0.0, 1.0, 0.0), OptimizerHour(1, 3.0, 0.0, 3.0, 0.0)]
    result = optimize(hours, 5.0, CFG, end_value_per_kwh=0.0)
    assert result.actions == ["grid_charging", "discharge_allowed"]
    assert result.total < simulate(hours, 5.0, CFG, ["discharge_allowed"] * 2, 0.0).total


def test_optimizer_keeps_energy_worth_more_than_selling_it() -> None:
    hours = [OptimizerHour(0, 1.0, 0.2, 0.0, 0.0)]
    assert optimize(hours, 80.0, CFG, end_value_per_kwh=1.0).actions == ["discharge_allowed"]
    assert optimize(hours, 80.0, CFG, end_value_per_kwh=0.0).actions == ["force_discharge"]


def test_optimizer_respects_min_profit_per_kwh() -> None:
    """A grid charging hour charges at full rate (like charge_if_needed), ~0.13/kWh profit here."""
    hours = [OptimizerHour(0, 1.0, 0.0, 0.0, 0.0), OptimizerHour(1, 1.4, 0.0, 5.0, 0.0)]
    assert optimize(hours, 5.0, CFG, 0.0).actions[0] == "grid_charging"
    strict = OptimizerConfig(**{**CFG.__dict__, "min_profit_per_kwh": 0.5})
    assert optimize(hours, 5.0, strict, 0.0).actions[0] != "grid_charging"


@pytest.mark.parametrize(("later_solar", "blocked"), [(5.5, False), (7.0, True)])
def test_optimizer_blocks_solar_charging_only_with_a_margin(later_solar: float, blocked: bool) -> None:
    """Room 5 kWh: 5.5 kWh later sun fills it only if all of it comes, 7 kWh also with 80 % of it."""
    cfg = OptimizerConfig(**{**CFG.__dict__, "max_charge": 10.0})
    hours = [
        OptimizerHour(datetime.datetime.combine(DAY, datetime.time(10)), 2.0, 1.0, 0.0, 5.0),
        OptimizerHour(datetime.datetime.combine(DAY, datetime.time(11)), 2.0, 0.1, 0.0, later_solar),
        OptimizerHour(datetime.datetime.combine(DAY, datetime.time(18)), 3.0, 0.5, 4.0, 0.0),
    ]
    for hour in hours:
        hour.forbidden = {"force_discharge"}  # selling the battery too would beat both
    assert (optimize(hours, 50.0, cfg, 0.0).actions[0] == "blocked_solar_charge") is blocked


def test_optimizer_blocks_without_margin_when_selling_beats_storing() -> None:
    """Selling at 3.0 beats any later use (buy 2.0): no bet on the later sun, so no margin is needed."""
    hours = [
        OptimizerHour(datetime.datetime.combine(DAY, datetime.time(10)), 3.5, 3.0, 0.0, 5.0),
        OptimizerHour(datetime.datetime.combine(DAY, datetime.time(18)), 2.0, 0.5, 4.0, 0.0),
    ]
    for hour in hours:
        hour.forbidden = {"force_discharge"}  # selling the battery too would beat both
    assert optimize(hours, 50.0, CFG, 0.0).actions[0] == "blocked_solar_charge"
    hours[0].block_allowed = False
    assert optimize(hours, 50.0, CFG, 0.0).actions[0] != "blocked_solar_charge"


def test_optimizer_never_sells_in_excluded_hours() -> None:
    hours = [OptimizerHour(0, 1.0, 5.0, 0.0, 0.0, can_sell=False)]
    assert optimize(hours, 80.0, CFG, end_value_per_kwh=0.0).actions != ["force_discharge"]


def test_usage_reserve_is_kept_for_house_consumption() -> None:
    """Forced export keeps the reserve, while normal self-consumption may use it."""
    cfg = OptimizerConfig(**{**CFG.__dict__, "usage_reserve": 40.0})
    sale = step_hour(OptimizerHour(0, 1.0, 5.0, 0.0, 0.0), 50.0, "force_discharge", cfg)
    house = step_hour(OptimizerHour(0, 5.0, 0.0, 3.0, 0.0), 50.0, "discharge_allowed", cfg)

    assert sale[0] == pytest.approx(40.0, abs=0.1)
    assert house[0] < 40.0


def test_house_use_has_priority_over_export_reserve() -> None:
    """House use may cross the reserve in a sale hour, but no energy is exported below it."""
    cfg = OptimizerConfig(**{**CFG.__dict__, "usage_reserve": 40.0})
    details = {}
    level, _, _, _ = step_hour(
        OptimizerHour(0, 5.0, 5.0, 2.0, 0.0), 50.0, "force_discharge", cfg, details=details
    )

    assert level < 40.0
    assert details["house"] == pytest.approx(2.0)
    assert details["sold"] == pytest.approx(0.0)


def test_simulated_sale_is_limited_to_the_planned_kwh() -> None:
    """A rule sale of 0.5 kWh in a 15 min period must not be simulated as a full hour of discharging."""
    hours = [OptimizerHour(0, 1.0, 0.5, 0.0, 0.0)]
    full = simulate(hours, 80.0, CFG, ["force_discharge"], 0.0)
    planned = simulate(hours, 80.0, CFG, ["force_discharge"], 0.0, [0.5])
    assert planned.levels[0] == pytest.approx(80.0 - 0.5 / 0.95 / 10.0 * 100.0, abs=0.1)
    assert full.levels[0] < planned.levels[0]


def test_optimizer_interpolates_efficiency_at_the_actual_power() -> None:
    cfg = OptimizerConfig(
        **{
            **CFG.__dict__,
            "charge_efficiency_curve": ((1000.0, 0.8), (5000.0, 0.96)),
            "discharge_efficiency_curve": ((1000.0, 0.75), (5000.0, 0.95)),
        }
    )
    hour = OptimizerHour(0, 1.0, 0.0, 1.0, 0.0)
    charged = step_hour(hour, 5.0, "grid_charging", cfg, limit=1.0)
    discharged = step_hour(hour, 50.0, "discharge_allowed", cfg)

    assert charged[0] == pytest.approx(13.0)
    assert discharged[0] == pytest.approx(50.0 - 1.0 / 0.75 / 10.0 * 100.0)


def test_min_total_profit_does_not_block_using_the_battery() -> None:
    """min_total_profit is for trades - an hour of house consumption earns less than 1.00 but must not be blocked."""

    def no_profit_blocks(min_total_profit: float) -> set:
        # the rule (the optimiser plans other charges with another min_total_profit)
        engine, _ = planned(17, 90, {"optimizer_control": False, "only_discharge_on_profit": True, "min_total_profit": min_total_profit})
        return {
            ts for day in engine.plan if isinstance(day, int)
            for ts, reason in engine.plan[day]["blocked_discharge_timestamps"].items() if "Ingen fortjeneste" in reason
        }

    assert no_profit_blocks(100.0) == no_profit_blocks(0.0)



@pytest.mark.parametrize("negative", [False, True])
def test_optimizer_controls_the_battery(negative: bool) -> None:
    """The optimiser's actions become the plan charge_if_needed() executes, and its battery levels match the plan."""
    engine, result = planned(0, 30, {}, solar=True, negative=negative)
    shadow = engine.optimizer_shadow
    assert shadow["controls"] is True
    assert shadow["optimal"] <= shadow["rules"] + 0.01
    for row in shadow["hours"]:
        day = engine.plan[(row["hour"].date() - DAY).days]
        if row["action"] == "grid_charging" and row["grid_kwh"] >= 0.1:
            assert any(engine.charge_hours[row["hour"]].get(rule) for rule in RULE_SWITCHES)
            assert engine.charge_hours[row["hour"]]["kWh"] == pytest.approx(row["grid_kwh"], abs=0.01)
        elif row["action"] == "force_discharge" and row["sold_kwh"] > 0.01:
            sold = sum(sale["kwh"] for ts, sale in day["force_discharge_timestamps"].items() if ts.replace(minute=0) == row["hour"])
            assert sold == pytest.approx(row["sold_kwh"], abs=0.01)
        elif row["action"] == "discharge_allowed":
            assert row["hour"] in day["discharge_timestamps"]
        assert sum(day["battery_level_flow"][row["hour"].hour]) == pytest.approx(row["battery_level"], abs=1.0)
    assert result.states["powerwall_action"] in ("grid_charging", "discharge_allowed", "force_solar_only_charging", "force_discharge", "blocked_solar_charge")


def test_optimizer_min_total_profit_limits_sales_only() -> None:
    """min_total_profit is a cost per sale hour: it never blocks charging for the house."""
    strict = OptimizerConfig(**{**CFG.__dict__, "min_total_profit": 5.0})
    use = [OptimizerHour(0, 1.0, 0.0, 0.0, 0.0), OptimizerHour(1, 1.4, 0.0, 5.0, 0.0)]
    assert optimize(use, 5.0, CFG, 0.0).actions[0] == "grid_charging"
    assert optimize(use, 5.0, strict, 0.0).actions[0] == "grid_charging"
    sell = [OptimizerHour(0, 1.0, 0.0, 0.0, 0.0), OptimizerHour(1, 3.0, 2.0, 0.0, 0.0)]
    assert optimize(sell, 5.0, CFG, 0.0).actions == ["grid_charging", "force_discharge"]
    assert "force_discharge" not in optimize(sell, 5.0, strict, 0.0).actions


def test_negative_prices_ignore_min_profit_per_kwh() -> None:
    """min_profit_per_kwh is for kWh out of the battery; a negative price charge pays without them."""
    engine, _ = planned(0, 30, {**OFF, "negative_price_planner": True, "min_profit_per_kwh": 5.0}, negative=True)
    assert [ts for ts in charge_hours(engine) if engine.charge_hours[ts].get("negative_price_planner")]



def test_optimizer_respects_switched_off_rules() -> None:
    """With the optimiser in control the rule switches decide which actions it may use."""
    engine, _ = planned(0, 30, {"grid_charge_to_sell": False, "optimize_solar_charging": False}, solar=True, negative=True)
    assert not [ts for ts in charge_hours(engine) if engine.charge_hours[ts].get("grid_charge_to_sell")]
    assert not [sale for sale in sales(engine).values() if sale["title"] in ("Sælger strøm købt billigt", "Sælger før billig opladning")]
    assert not any(engine.plan[day]["blocked_solar_charge_timestamps"] for day in engine.plan if isinstance(day, int))
    # the negative prices are still used for the own consumption
    assert [ts for ts in charge_hours(engine) if ts.date() == DAY and ts.hour in NEGATIVE_HOURS]


@pytest.mark.parametrize(
    ("off", "held"),
    [
        ((), True),
        (("only_discharge_on_profit",), True),
        (("prioritize_discharge_hours_by_energy_cost",), False),
        (("only_discharge_on_profit", "prioritize_discharge_hours_by_energy_cost"), False),
    ],
)
def test_holding_for_a_more_expensive_hour_needs_only_its_own_switch(off: tuple, held: bool) -> None:
    """The battery holds 1 kWh: the house uses it at 4.0 instead of 2.5. Switching "Prioritise by energy
    cost" off forbids holding it, also while "Profit only" is on (before, either switch allowed it)."""
    from custom_components.battmind.engine.optimizer import optimize_with_rules

    cfg = OptimizerConfig(**{**CFG.__dict__, "unsupported": frozenset({"grid_charging"})})  # only hold or use
    hours = [OptimizerHour(0, 2.5, 0.0, 1.0, 0.0), OptimizerHour(1, 4.0, 0.0, 1.0, 0.0)]
    result, explanations = optimize_with_rules(hours, 15.0, cfg, 0.0, lambda switch: switch not in off)
    if held:
        assert result.actions == ["force_solar_only_charging", "discharge_allowed"]
        assert explanations[0] == {"rule": "prioritize_discharge_hours_by_energy_cost", "target": 1}
    else:
        assert result.actions[0] == "discharge_allowed"


@pytest.mark.parametrize("profit_only", [True, False])
def test_holding_without_a_more_expensive_hour_is_profit_only(profit_only: bool) -> None:
    """Using the battery at 1.0 doesn't pay when a kept kWh is worth 3.0: "Profit only" holds it,
    switched off the battery is used (also with "Prioritise by energy cost" on)."""
    from custom_components.battmind.engine.optimizer import optimize_with_rules

    cfg = OptimizerConfig(**{**CFG.__dict__, "unsupported": frozenset({"grid_charging"})})  # only hold or use
    hours = [OptimizerHour(0, 1.0, 0.0, 1.0, 0.0)]
    off = () if profit_only else ("only_discharge_on_profit",)
    result, explanations = optimize_with_rules(hours, 50.0, cfg, 3.0, lambda switch: switch not in off)
    if profit_only:
        assert result.actions == ["force_solar_only_charging"]
        assert explanations[0] == {"rule": "only_discharge_on_profit", "target": None}
    else:
        assert result.actions == ["discharge_allowed"]


def test_optimizer_actions_are_explained_as_rules() -> None:
    engine, _ = planned(0, 30, {}, solar=True, negative=True)
    for ts in charge_hours(engine):
        reason = engine.plan[(ts.date() - DAY).days]["charging_sessions"][ts]["reason"]
        assert any(title in reason for title in ("Billigste timer", "Dyreste timer", "Brug for", "Negativ pris", "Køb for at sælge"))
    titles = {sale["title"] for sale in sales(engine).values()}
    assert titles <= {"Sælger strøm købt billigt", "Sælger før billig opladning", "Sælger før solen fylder batteriet", "Sælger i stedet for at bruge"}


@pytest.mark.parametrize("emptied", [True, False])
def test_a_charge_is_not_explained_by_a_sale_after_the_battery_was_empty(emptied: bool) -> None:
    """Charge at 2.0 used at 3.8, the battery empties, the sun fills it and that energy is sold:
    the sale is not the charge's ("Køb for at sælge" with a loss), unless the charge is still there."""
    hours = [
        OptimizerHour(datetime.datetime.combine(DAY, datetime.time(hour)), buy, sell, consumption, solar)
        for hour, buy, sell, consumption, solar in (
            (12, 2.0, 1.5, 0.5, 0.0),
            (17, 3.8, 2.4, 4.0, 0.0),
            (12, 2.1, 1.6, 0.0, 9.0),
            (18, 3.9, 2.4, 0.5, 0.0),
        )
    ]
    hours[2].timestamp += datetime.timedelta(days=1)
    hours[3].timestamp += datetime.timedelta(days=1)
    result = OptimizerResult(
        actions=["grid_charging", "discharge_allowed", "discharge_allowed", "force_discharge"],
        grid_kwh=[4.8, 0.0, 0.0, 0.0],
        sold_kwh=[0.0, 0.0, 0.0, 1.2],
        levels=[50.0, 5.0 if emptied else 30.0, 100.0 if emptied else 60.0, 80.0 if emptied else 40.0],
    )
    explanation = explain(hours, result, CFG)[0]
    if emptied:
        assert explanation["rule"] in ("cheapest_hour_fill_planner", "most_expensive_planner")
        assert explanation["target"] == 1
    else:
        assert explanation["rule"] == "grid_charge_to_sell"
        assert explanation["target"] == 3


def test_a_target_on_another_day_shows_its_date_in_the_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """"Dyreste timer prioriteret (17:00)" at 21:00 reads as today's (past) 17:00 when it is tomorrow's."""
    original = realistic_snapshot

    def pricier_tomorrow(*args, **kwargs) -> Snapshot:
        # no cheap night to charge in: the battery is kept from this evening for tomorrow
        snap = original(*args, **kwargs)
        for entity_id in ("sensor.price", "sensor.sell"):
            for row in snap.price_data["attributes"][entity_id]["raw_tomorrow"]:
                row["price"] = round(row["price"] + 1.0, 3)
        return snap

    monkeypatch.setattr(sys.modules[__name__], "realistic_snapshot", pricier_tomorrow)
    no_grid_charging = {"cheapest_hour_fill_planner": False, "most_expensive_planner": False, "needed_before_max_level_planner": False, "grid_charge_to_sell": False}
    seen_other_day = False
    for hour in (21, 22):
        engine, _ = planned(hour, 40, no_grid_charging)
        for day in (day for day in engine.plan if isinstance(day, int)):
            for ts, reason in engine.plan[day]["blocked_discharge_timestamps"].items():
                title = re.search(r"<summary>.*?\(([^()]*)\)</summary>", reason)
                target = re.search(r"\*\*(\d{2}/\d{2}) \d{2}:\d{2}\*\*", reason)
                if not title or not target:
                    continue
                if target.group(1) == ts.strftime("%d/%m"):
                    assert re.fullmatch(r"\d{2}:\d{2}", title.group(1)), reason
                else:
                    assert title.group(1).startswith(target.group(1)), reason
                    seen_other_day = True
    assert seen_other_day


def test_hour_rules_group_consecutive_hours_with_the_same_rule() -> None:
    from custom_components.battmind.engine.planner._helpers import _group_hour_rules, _joined_reason

    def hour(h: int) -> datetime.datetime:
        return datetime.datetime.combine(DAY, datetime.time(h))

    def reason(title: str, values: str) -> str:
        return f"<details><summary>⏳{title} ({values})</summary>body {values}<br></details>"

    rows = [
        (hour(1), reason("Dyreste timer", "1.00/10%")),
        (hour(2), reason("Dyreste timer", "2.00/20%")),
        (hour(4), reason("Dyreste timer", "3.00/30%")),  # not consecutive
        (hour(5), reason("Billigste timer", "0.50/5%")),
    ]
    groups = _group_hour_rules(rows)
    assert [len(group) for group in groups] == [2, 1, 1]
    joined = _joined_reason(groups[0])
    assert joined.startswith("<details><summary>📦⏳Dyreste timer (1.50/15%)</summary>")
    assert "<summary>01:00 (1.00/10%)</summary>body 1.00/10%" in joined
    # clock times are kept, not averaged
    same = _joined_reason([(hour(1), reason("Prioriteret", "17:00")), (hour(2), reason("Prioriteret", "17:00"))])
    assert "Prioriteret (17:00)</summary>" in same
    differ = _joined_reason([(hour(1), reason("Prioriteret", "17:00")), (hour(2), reason("Prioriteret", "19:00"))])
    assert "Prioriteret (17:00-19:00)</summary>" in differ


def test_single_sell_period_shows_its_end_time() -> None:
    from custom_components.battmind.engine.planner._helpers import _join_timestamps_by_hour

    timestamp = datetime.datetime.combine(DAY, datetime.time(20))
    reason = "<details><summary>Sælger</summary>body</details>"
    joined = _join_timestamps_by_hour(
        {timestamp: {"title": "Sælger", "emoji": "", "kwh": 0.9, "profit": 2.0, "reason": reason}},
        "kr",
        "I alt",
        lambda _timestamp: 4,
    )

    assert joined[timestamp]["period_end"] == timestamp + datetime.timedelta(minutes=15)


@pytest.mark.parametrize("optimizer_control", [True, False])
def test_overview_has_the_plan_and_its_totals(optimizer_control: bool) -> None:
    engine, _ = planned(0, 30, {"optimizer_control": optimizer_control}, solar=True, negative=True)
    overview = engine.last_overview
    assert "## 📅 Ladnings- og afladningsplan ##" in overview
    assert "Hour rules" not in overview and "📖" not in overview
    summary = engine.optimizer_shadow["summary_optimal" if optimizer_control else "summary_rules"]
    assert f"💰 I alt: {summary['profit']:.2f} kr ({summary['profit_per_kwh']:.2f} kr/kWh)" in overview
    assert f"**{-summary['profit']:.2f}** |" in overview  # the bill row's difference = the title
    without, with_battery = summary["without"], summary["with"]
    # income is shown as a positive amount that is taken off the bill, the difference is of the shown amounts
    earned_without, earned_with = -without["earned"], -with_battery["earned"]
    assert f"| **💵 Tjent på salg** | **{earned_without:.2f} kr** | **{earned_with:.2f} kr** | **{earned_with - earned_without:.2f}** |" in overview
    assert summary["profit"] == pytest.approx(without["bill"] - with_battery["bill"], abs=0.02)
    for column in (without, with_battery):
        # the items add up to the bill
        assert column["paid"] + column["earned"] + column["wear"] + column["energy_value"] == pytest.approx(column["bill"], abs=0.03)
        assert column["house_cost"] + column["battery_cost"] == pytest.approx(column["paid"], abs=0.02)
        assert column["solar_income"] + column["sold_income"] == pytest.approx(column["earned"], abs=0.02)
    assert ("Optimering af hele perioden" if optimizer_control else "Reglerne") in overview


def test_overview_layout_result_first_and_ranges_end_when_the_next_begins() -> None:
    import re

    engine, _ = planned(0, 30, {}, days=4)
    overview = engine.last_overview
    assert overview.index("<summary><b>💰 ") < overview.index("#### I dag · ")  # the result before the schedule
    assert overview.index("#### I dag · ") < overview.index("#### I morgen · ")
    assert not re.search(r"\| \*\*\d\d:\d\d-\d\d:59\*\* \|", overview)  # no "inclusive" ends in the rows
    ranges = re.findall(r"\| \*\*(\d\d):(\d\d)-(\d\d):(\d\d)\*\*", overview)
    assert ranges
    for start_h, start_m, end_h, end_m in ranges:
        start, end = int(start_h) * 60 + int(start_m), int(end_h) * 60 + int(end_m)
        assert (end - start) % (24 * 60) > 0  # a range runs forward (23:00-00:00 wraps midnight)
    assert re.search(r"\*\*(\d\d):00-\1:00\*\*", overview) is None
    assert any(
        (int(end_h) * 60 + int(end_m) - int(start_h) * 60 - int(start_m)) % (24 * 60) == 60 for start_h, start_m, end_h, end_m in ranges
    )  # a single whole hour ends at the next hour, not at HH:59


def test_overview_has_the_key_figures_hour_squares_and_levels() -> None:
    import re

    engine, _ = planned(0, 30, {}, solar=True, negative=True, days=4)
    overview = engine.last_overview
    summary = engine.optimizer_shadow["summary_optimal"]
    assert f"<summary><b>💰 I alt: {summary['profit']:.2f} kr ({summary['profit_per_kwh']:.2f} kr/kWh)</b><br>" in overview  # the key figures, the bill behind them
    assert f"📈 {summary['charged_grid_kwh']:.1f} kWh · 📤 {summary['with']['sold_kwh']:.1f} kWh · ☀️ {summary['charged_solar_kwh']:.1f} kWh</summary>" in overview
    expenses = {"kwh": 5.0, "percentage": 50.0, "solar_percentage": 0.0, "unit": 1.0}
    engine.config["solar"]["entity_ids"]["production_entity_id"] = "sensor.solar_production"  # solar installed
    battery_costs = "\n".join(engine._build_overview(engine.plan, {}, expenses, {}))
    assert "**☀️ Solcelle andel** | **0% 0.0 kWh**" in battery_costs  # also with 0% from solar
    assert re.search(r"Sælges kl\. \*\*[^<]*\*\* · Fortjeneste pr\. kWh: \*\*[^<]*\*\*<br>", overview)  # the target and the profit on one line
    assert "Salgspris: **" in overview and "<br>Fortjeneste pr. kWh" not in overview
    legend = "🟩 Ladning · 🟧 Salg · 🟨 Sol blokeret · 🟦 Holder tilbage · ⬜ Normal drift · ⬛ Forbi"
    assert overview.count(legend) == 1
    strips = re.findall(r"\*\*00:00-11:59\*\* ([🟩🟧🟨🟦⬜⬛]{12})<br>\*\*12:00-23:59\*\* ([🟩🟧🟨🟦⬜⬛]{12})<br>📈 [\d.]+ kWh · 📤 [\d.]+ kWh", overview)
    assert len(strips) == overview.count("\n#### ")  # a strip for each day shown (the later days too)
    # the battery level at the end of each row, from the plan that controls the battery
    rows = re.findall(r"\| \*\*\d\d:\d\d-\d\d:\d\d\*\*\ \| .* \| (\d+%|–) \|\n", overview)
    assert rows and all(level != "–" for level in rows)
    hours = {hour["hour"].replace(tzinfo=None): hour["battery_level"] for hour in engine.optimizer_shadow["hours"]}
    first_row = re.search(r"\| \*\*00:00-(\d\d):00\*\* \| .* \| (\d+)% \|", overview)
    assert first_row
    last_hour = int(first_row.group(1)) - 1  # the row ends at the level after its last hour
    assert int(first_row.group(2)) == round(hours[min(hours) + datetime.timedelta(hours=last_hour)])


def test_day_strip_squares_follow_the_plan_and_the_clock() -> None:
    from custom_components.battmind.engine.planner.overview import _day_strip

    day = datetime.datetime.combine(DAY, datetime.time(0))

    def at(hour: int, minute: int = 0) -> datetime.datetime:
        return day + datetime.timedelta(hours=hour, minutes=minute)

    data = {
        "start_of_day": day,
        "charging_sessions": {at(3): {}, at(5): {}},
        "force_discharge_timestamps": {at(5, 30): {}, at(7, 45).isoformat(): {}},  # a sale in a charge hour: the charge wins
        "blocked_solar_charge_timestamps": {at(9): {}},
        "blocked_discharge_timestamps": {at(9): "x", at(10): "x"},  # held back in a solar block hour: the block wins
    }
    first, second = _day_strip(data, at(2, 10)).split("<br>")
    assert first == "**00:00-11:59** ⬛⬛⬜🟩⬜🟩⬜🟧⬜🟨🟦⬜"  # 00 and 01 have passed, 02 is now
    assert second == "**12:00-23:59** " + "⬜" * 12
    assert _day_strip(data, at(23, 59)).count("⬛") == 23  # the last hour is not over yet
    assert "⬛" not in _day_strip(data, day)


def test_overview_with_the_rule_plan_shows_the_rule_levels() -> None:
    import re

    engine, _ = planned(0, 30, {"optimizer_control": False}, days=4)
    overview = engine.last_overview
    shadow = engine.optimizer_shadow
    assert not shadow.get("controls", False)
    levels = re.findall(r"\| (\d+%|–) \|\n", overview)
    assert levels and "–" not in levels
    first_row = re.search(r"\| \*\*00:00-(\d\d):00\*\* \| .* \| (\d+)% \|", overview)
    hours = {hour["hour"]: hour["rules_battery_level"] for hour in shadow["hours"]}
    assert first_row and int(first_row.group(2)) == round(hours[min(hours) + datetime.timedelta(hours=int(first_row.group(1)) - 1)])


def test_overview_without_a_plan_or_summary_shows_no_key_figures() -> None:
    engine, _ = planned(0, 30, {})
    engine.optimizer_shadow = None
    assert engine._plan_summary_lines("kr", "kr/kWh") == []
    lines: list[str] = []
    engine._plan_overview(lines, {}, [])
    text = "\n".join(lines)
    assert "Ingen" in text and "💰" not in text and "🟩" not in text


def test_overview_folds_the_days_after_tomorrow_into_read_more() -> None:
    engine, _ = planned(0, 30, {}, days=4)
    overview = engine.last_overview
    marker = "<details><summary><b>Se alle dage</b></summary>"
    assert overview.count(marker) <= 1
    if marker in overview:
        before, folded = overview.split(marker, 1)
        folded = folded.split("</details>\n", 1)[0] if "<details>" not in folded.split("</details>")[0] else folded
        assert before.count("#### ") <= 2  # today + tomorrow stay visible
        assert "#### " in folded


def _planned_with_cars(ev_charging) -> tuple[BattMindEngine, dict]:
    """planned(), then planned again with the Cable Juice Planner cars (ev_charging(start, end) of the first plan)."""
    engine, _ = planned(0, 30, {})
    summary = engine.optimizer_shadow["summary_optimal"]
    snap = realistic_snapshot(datetime.datetime.combine(DAY, datetime.time(0, 30)), 30, {})
    snap.ev_charging = ev_charging(summary["start"], summary["end"])
    assert engine.run_job(snap, engine.charge_if_needed).error is None  # replans: the cars are in the plan inputs
    return engine, engine.optimizer_shadow["summary_optimal"]


def test_overview_shows_the_planned_charging_of_the_cars() -> None:
    hour = datetime.timedelta(hours=1)

    def cars(start: datetime.datetime, end: datetime.datetime) -> list[dict]:
        aware = (start + 2 * hour).replace(tzinfo=TZ).astimezone(datetime.UTC).isoformat()
        return [
            {"name": "Tesla", "charge_plan": [
                {"time": (start + hour).isoformat(), "kwh": 10.0, "price": 1.99, "cost": 20.0},
                {"time": aware, "kwh": 2.5, "price": 2.0, "cost": 5.0},  # UTC -> local
                {"time": end.replace(minute=20).isoformat(), "kwh": 2.5, "price": 2.0, "cost": 5.0},  # the last hour
                {"time": (end + hour).isoformat(), "kwh": 50.0, "price": 2.0, "cost": 100.0},  # after the period
            ]},
            {"name": "Zoe", "charge_plan": [{"time": start.isoformat(), "kwh": 5.0, "price": 1.6}]},  # no cost: kwh x price
        ]

    engine, summary = _planned_with_cars(cars)
    overview = engine.last_overview
    assert "**🚗 Elbiler i perioden**" in overview
    assert "| Bil | Ladetimer | kWh | Pris | kr/kWh |" in overview
    assert "| Tesla | 3 | 15.0 | 30.00 kr | 2.00 |" in overview
    assert "| Zoe | 1 | 5.0 | 8.00 kr | 1.60 |" in overview
    assert "| **I alt** | **4** | **20.0** | **38.00 kr** | **1.90** |" in overview
    assert f"| **= Elregning inkl. elbiler** | **{summary['with']['bill'] + 38.0:.2f} kr** |" in overview
    # inside the totals, before their note
    assert overview.index("<summary><b>💰 ") < overview.index("🚗 Elbiler") < overview.index("trækkes fra regningen")


def test_cars_without_planned_charging_are_one_line_without_a_bill() -> None:
    def cars(_start: datetime.datetime, _end: datetime.datetime) -> list[dict]:
        return [{"name": "Tesla", "charge_plan": []}, {"name": "Zoe", "charge_plan": []}]

    overview = _planned_with_cars(cars)[0].last_overview
    assert "**🚗 Elbiler i perioden:** Ingen planlagt ladning" in overview
    assert "| Tesla |" not in overview and "| Zoe |" not in overview and "Elregning inkl. elbiler" not in overview


def test_a_car_without_a_plan_is_not_counted_as_no_charging() -> None:
    def cars(start: datetime.datetime, _end: datetime.datetime) -> list[dict]:
        return [
            {"name": "Tesla", "charge_plan": None},
            {"name": "Zoe", "charge_plan": [{"time": start.isoformat(), "kwh": "unknown", "price": 1.6, "cost": 8.0}]},
            {"name": "Leaf", "charge_plan": []},
        ]

    overview = _planned_with_cars(cars)[0].last_overview
    assert "| Tesla | – | – | ikke tilgængelig | – |" in overview
    assert "| Zoe | – | – | ikke tilgængelig | – |" in overview
    assert "| Leaf |" not in overview  # a car without planned charging adds nothing
    # too low with cars missing
    assert "| **I alt** |" not in overview.split("🚗")[1] and "Elregning inkl. elbiler" not in overview


def test_overview_without_cable_juice_planner_has_no_cars() -> None:
    engine, _ = planned(0, 30, {})
    assert "🚗" not in engine.last_overview and "Elregning inkl. elbiler" not in engine.last_overview


def test_bill_with_the_battery_is_the_simulated_cost() -> None:
    """breakdown()'s bill with the battery is the same as simulate() of the plan, without = no battery at all."""
    from custom_components.battmind.engine.optimizer import breakdown

    hours = [OptimizerHour(0, 0.5, 0.2, 1.0, 0.0), OptimizerHour(1, 3.0, 2.0, 2.0, 0.0), OptimizerHour(2, 1.0, 0.5, 0.0, 3.0)]
    actions = ["grid_charging", "force_discharge", "discharge_allowed"]
    result = breakdown(hours, 50.0, CFG, actions, 1.0)
    assert result["with"]["bill"] == pytest.approx(simulate(hours, 50.0, CFG, actions, 1.0).total, abs=0.01)
    assert result["without"]["bill"] == pytest.approx(simulate(hours, 50.0, CFG, ["blocked_solar_charge"] * 3, 1.0).total, abs=0.01)
    assert result["without"]["house_kwh"] == pytest.approx(3.0)
    assert result["without"]["solar_kwh"] == pytest.approx(3.0)


def test_battery_price_is_a_running_average() -> None:
    """Charged kWh come in at their price, used/sold kWh leave at the average (the price of the rest stays)."""
    from custom_components.battmind.engine.planner._helpers import _running_battery_price

    # 5 kWh at 2.00, 5 kWh charged at 1.00 -> 1.50
    assert _running_battery_price(5.0, 2.0, [(5.0, 5.0, 1.0, 0.5)]) == pytest.approx(1.5)
    # using 4 kWh doesn't change the price of the rest
    assert _running_battery_price(5.0, 2.0, [(5.0, 5.0, 1.0, 0.5), (-4.0, 0.0, 0.0, 0.0)]) == pytest.approx(1.5)
    # emptied and refilled with solar (sold for 0.50 otherwise): the old price is gone
    assert _running_battery_price(5.0, 2.0, [(-5.0, 0.0, 0.0, 0.0), (4.0, 0.0, 0.0, 0.5)]) == pytest.approx(0.5)
    # a charge hour with solar on top: the grid part at the grid price, the rest at the solar price
    assert _running_battery_price(0.0, 2.0, [(4.0, 1.0, 1.0, 0.5)]) == pytest.approx((1.0 + 3 * 0.5) / 4)
    # nothing in the battery: the start price
    assert _running_battery_price(0.0, 2.0, []) == 2.0


def test_midnight_battery_level_optimises_every_day_on_its_own() -> None:
    """use_midnight_battery_level: every day of days_to_predict is optimised until midnight (max 24 hours)."""
    from custom_components.battmind.engine.optimizer import optimize_per_day, optimize_with_rules

    engine, _ = planned(18, 60, {"use_midnight_battery_level": True}, solar=True, days=3)
    shadow = engine.optimizer_shadow
    assert shadow["controls"] is True
    assert len({row["hour"].date() for row in shadow["hours"]}) == 3

    # the same as each day optimised alone, starting at the level the day before ends with
    def hour(day: int, h: int, buy: float, sell: float) -> OptimizerHour:
        return OptimizerHour(datetime.datetime.combine(DAY + datetime.timedelta(days=day), datetime.time(h)), buy, sell, 1.0, 0.0)

    hours = [hour(0, 22, 0.5, 0.2), hour(0, 23, 3.0, 2.0), hour(1, 0, 0.4, 0.1), hour(1, 1, 2.5, 1.5)]
    merged, explanations = optimize_per_day(hours, 30.0, CFG, 1.0, lambda _switch: True)
    first, _ = optimize_with_rules(hours[:2], 30.0, CFG, 1.0, lambda _switch: True)
    second, _ = optimize_with_rules(hours[2:], first.levels[-1], CFG, 1.0, lambda _switch: True)
    assert merged.actions == first.actions + second.actions
    assert merged.levels == first.levels + second.levels
    assert len(explanations) == 4


def _what_if_data(engine: BattMindEngine, days: int = 2) -> dict:
    """Hourly history: cheap nights, expensive days with a profitable sell price and midday sun."""
    ids = engine.config["home"]["entity_ids"]
    solar_id = engine.config["solar"]["entity_ids"]["production_entity_id"]
    start = datetime.datetime.combine(DAY - datetime.timedelta(days=days), datetime.time(), tzinfo=TZ)
    hours = [start + datetime.timedelta(hours=hour) for hour in range(24 * days)]
    return {
        "buy_entity_id": "sensor.historical_buy",
        "sell_entity_id": "sensor.historical_sell",
        "stats": {
            ids["power_consumption_entity_id"]: [(hour.timestamp(), 1000.0) for hour in hours],
            ids["powerwall_watt_flow_entity_id"]: [(hour.timestamp(), 0.0) for hour in hours],
            solar_id: [(hour.timestamp(), 2500.0 if 10 <= hour.hour <= 14 else 0.0) for hour in hours],
            "sensor.historical_buy": [(hour.timestamp(), 0.5 if hour.hour < 6 else 2.5) for hour in hours],
            "sensor.historical_sell": [(hour.timestamp(), 0.3 if hour.hour < 6 else 3.0) for hour in hours],
        },
    }


@pytest.mark.parametrize("optimizer_control", [True, False])
def test_what_if_replays_the_planner_without_touching_the_live_engine(optimizer_control: bool) -> None:
    now = datetime.datetime.combine(DAY, datetime.time(3, 30))
    settings = {"optimizer_control": optimizer_control}
    engine, _ = planned(3, 40, settings, solar=True)
    live = deepcopy((engine.plan, engine.charge_hours, engine.optimizer_shadow, engine.shadow_plan_actions))
    history = deepcopy(engine.charging_history_db)
    result = engine.run_job(
        realistic_snapshot(now, 40, settings), engine.what_if, _what_if_data(engine), {"optimizer_control": not optimizer_control}
    )

    assert result.error is None, result.error
    simulation = result.value
    current_mode, scenario_mode = ("optimizer", "rules") if optimizer_control else ("rules", "optimizer")
    assert simulation["current_assumptions"]["control_mode"] == current_mode
    assert simulation["assumptions"]["control_mode"] == scenario_mode
    assert simulation["assumptions"]["rules"]["optimizer_control"] is not optimizer_control
    for column in ("current", "scenario"):
        # the cheap nights are used for the expensive days
        assert simulation[column]["charged_grid_kwh"] > 0.0
        assert simulation[column]["profit"] > 0.0
    assert (engine.plan, engine.charge_hours, engine.optimizer_shadow, engine.shadow_plan_actions) == live
    assert engine.charging_history_db == history


@pytest.mark.parametrize("optimizer_control", [True, False])
def test_what_if_excluded_sell_hours_stop_the_sales(optimizer_control: bool) -> None:
    now = datetime.datetime.combine(DAY, datetime.time(3, 30))
    settings = {"optimizer_control": optimizer_control}
    engine, _ = planned(3, 40, settings, solar=True)
    result = engine.run_job(
        realistic_snapshot(now, 40, settings),
        engine.what_if,
        _what_if_data(engine),
        {"exclude_sell_hours": ",".join(str(hour) for hour in range(24))},
    )

    assert result.error is None, result.error
    simulation = result.value
    assert simulation["current"]["with"]["sold_kwh"] > 0.0
    assert simulation["scenario"]["with"]["sold_kwh"] == 0.0
    assert simulation["assumptions"]["exclude_sell_hours"] == ",".join(str(hour) for hour in range(24))



def test_what_if_reports_the_progress_at_most_every_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.datetime.combine(DAY, datetime.time(3, 30))
    settings = {"optimizer_control": False}
    engine, _ = planned(3, 40, settings, solar=True)
    data = _what_if_data(engine)

    reports: list[dict] = []
    monkeypatch.setattr(what_if_module, "PROGRESS_INTERVAL", 0.0)
    result = engine.run_job(realistic_snapshot(now, 40, settings), engine.what_if, data, {"most_profit": False}, progress=reports.append)
    assert result.error is None, result.error
    assert reports and {report["steps"] for report in reports} == {2}
    assert [report["percent"] for report in reports] == sorted(report["percent"] for report in reports)
    assert reports[-1] == {"percent": 100, "step": 2, "steps": 2, "hours": 48, "total_hours": 48}

    reports.clear()
    monkeypatch.setattr(what_if_module, "PROGRESS_INTERVAL", 3600.0)
    result = engine.run_job(realistic_snapshot(now, 40, settings), engine.what_if, data, {}, progress=reports.append)
    assert result.error is None, result.error
    # only the first plan: the simulation has started
    assert len(reports) == 1 and reports[0]["step"] == 1 and reports[0]["steps"] == 1


def _requested_period(days_before: int, days: int) -> dict:
    """A requested period ending with the history of _what_if_data() and starting days_before days earlier."""
    end = datetime.datetime.combine(DAY, datetime.time(), tzinfo=TZ)
    return {"start": (end - datetime.timedelta(days=days_before)).timestamp(), "end": end.timestamp(), "days": days}


def test_what_if_estimates_the_requested_period_apart_from_the_simulated_result() -> None:
    now = datetime.datetime.combine(DAY, datetime.time(3, 30))
    settings = {"optimizer_control": False}
    engine, _ = planned(3, 40, settings, solar=True)
    data = _what_if_data(engine)  # 2 days of history
    complete = engine.run_job(realistic_snapshot(now, 40, settings), engine.what_if, data, {"battery_size": 20}).value

    # the whole requested period has data: nothing is missing, nothing is estimated
    data["requested_period"] = _requested_period(2, 2)
    result = engine.run_job(realistic_snapshot(now, 40, settings), engine.what_if, data, {"battery_size": 20})
    assert result.error is None, result.error
    assert result.value["coverage"] is None and result.value["estimate"] is None

    data["requested_period"] = _requested_period(3, 3)
    result = engine.run_job(realistic_snapshot(now, 40, settings), engine.what_if, data, {"battery_size": 20})
    assert result.error is None, result.error
    simulation = result.value
    # the simulated (reliable) results don't change
    for column in ("current", "scenario"):
        assert simulation[column] == complete[column]
        # the months add up to the profit and the bill with the battery
        assert simulation[column]["months"] == {
            "2026-10": {
                "hours": 48,
                "profit": pytest.approx(simulation[column]["profit"], abs=0.02),
                "bill": pytest.approx(simulation[column]["with"]["bill"], abs=0.02),
            }
        }
    coverage = simulation["coverage"]
    assert coverage["requested_days"] == 3 and coverage["requested_hours"] == 72
    assert coverage["missing_hours"] == 24 and coverage["missing_before_hours"] == 24 and coverage["gap_hours"] == 0
    assert coverage["limited_by"]["first"] == "2026-10-20T00:00:00"
    assert coverage["months"] == {"2026-10": {"requested_hours": 72, "simulated_hours": 48}}
    estimate = simulation["estimate"]
    assert estimate["estimated_hours"] == 24 and estimate["estimated_from_other_months"] == []
    for column in ("current", "scenario"):
        bill = simulation[column]["with"]["bill"]
        assert estimate[column]["bill"] == bill
        # the missing day gets the average bill per hour of the same month
        assert estimate[column]["estimated"] == pytest.approx(simulation[column]["months"]["2026-10"]["bill"] / 2, abs=0.01)
        assert estimate[column]["total"] == pytest.approx(bill + estimate[column]["estimated"], abs=0.01)
    assert estimate["difference"] == pytest.approx(estimate["scenario"]["total"] - estimate["current"]["total"], abs=0.01)

    # less than half of the requested hours: the missing data is reported but not estimated
    data["requested_period"] = _requested_period(5, 5)
    simulation = engine.run_job(realistic_snapshot(now, 40, settings), engine.what_if, data, {}).value
    assert simulation["coverage"]["missing_hours"] == 72
    assert simulation["estimate"] is None


def test_what_if_months_add_up_with_a_starting_level_and_a_gap() -> None:
    now = datetime.datetime.combine(DAY, datetime.time(3, 30))
    settings = {"optimizer_control": False}
    engine, _ = planned(3, 40, settings, solar=True)
    data = _what_if_data(engine, days=3)
    consumption_id = engine.config["home"]["entity_ids"]["power_consumption_entity_id"]
    # a 4 hour gap: the second run starts again at the minimum level
    data["stats"][consumption_id] = [row for index, row in enumerate(data["stats"][consumption_id]) if not 30 <= index < 34]
    result = engine.run_job(realistic_snapshot(now, 40, settings), engine.what_if, data, {"starting_level": 60})
    assert result.error is None, result.error
    for column in ("current", "scenario"):
        months = result.value[column]["months"].values()
        assert sum(month["hours"] for month in months) == 68
        assert sum(month["profit"] for month in months) == pytest.approx(result.value[column]["profit"], abs=0.05)
        assert sum(month["bill"] for month in months) == pytest.approx(result.value[column]["with"]["bill"], abs=0.05)


def test_what_if_estimates_months_without_data_from_the_nearest_month() -> None:
    requested = {"2025-11": 720, "2025-12": 744, "2026-01": 744, "2026-02": 672}
    simulated = {"2025-11": 720, "2026-02": 600}
    references, from_other_months = _reference_months(requested, simulated, 168)
    # December is nearest to November, January to February; February has enough data of its own
    assert references == {"2025-12": [11], "2026-01": [2], "2026-02": [2]}
    assert from_other_months == ["2025-12", "2026-01"]
    # a tie uses both months
    references, _ = _reference_months({"2026-01": 744}, {"2025-11": 720, "2026-03": 744}, 168)
    assert references == {"2026-01": [3, 11]}
    # a month with too little data is no reference
    assert _reference_months({"2026-01": 744}, {"2025-12": 100}, 168) is None


def test_replay_plans_when_the_prices_of_the_next_day_are_published() -> None:
    morning = datetime.datetime(2026, 10, 22, 5)
    assert _known_until(morning) == datetime.datetime(2026, 10, 23)
    assert _next_planning(morning) == datetime.datetime(2026, 10, 22, 13)
    afternoon = datetime.datetime(2026, 10, 22, 13)
    assert _known_until(afternoon) == datetime.datetime(2026, 10, 24)
    assert _next_planning(afternoon) == datetime.datetime(2026, 10, 23, 13)
