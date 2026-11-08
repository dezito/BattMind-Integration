"""powerwall_max_hours_old in the optimiser: a FIFO battery may not hold energy older than max_age_hours."""

from __future__ import annotations

import datetime

import pytest

from custom_components.battmind.const import build_config
from custom_components.battmind.engine import BattMindEngine
from custom_components.battmind.engine.optimizer import (
    OptimizerConfig,
    OptimizerHour,
    enforce_max_age,
    optimize,
    optimize_per_day,
    optimize_with_rules,
)

from .test_engine import DAY, OPTIONS, TEXTS, _power_values_db
from .test_planner_rules import realistic_snapshot

CFG = OptimizerConfig(battery_size=10.0, level_min=5.0, level_max=100.0, max_charge=5.0, max_discharge=5.0, efficiency=0.95, wear=0.1)
HOLD_ONLY = frozenset({"grid_charging"})  # only hold or use the battery


def at(hours: int) -> datetime.datetime:
    return datetime.datetime.combine(DAY, datetime.time(0)) + datetime.timedelta(hours=hours)


def cfg_with(**changes) -> OptimizerConfig:
    return OptimizerConfig(**{**CFG.__dict__, **changes})


def _all_rules(switch: str) -> bool:
    return True


def old_percent_at_start(hours, start_level, result, cfg) -> list[float]:
    """Independent FIFO check: % of the usable level at each hour's start that was charged more than max_age_hours ago.

    Charges are the level increases of the plan plus cfg.charged_before; cfg.unaged never gets old.
    """
    charges = list(cfg.charged_before)
    old = []
    level, unaged = start_level, cfg.unaged
    for index, hour in enumerate(hours):
        earliest = hour.timestamp - datetime.timedelta(hours=cfg.max_age_hours)
        unaged = min(unaged, max(level - cfg.level_min, 0.0))  # unknown age is the oldest: it leaves first
        fresh = unaged + sum(pct for when, pct in charges if earliest < when < hour.timestamp)
        old.append(max(level - cfg.level_min - fresh, 0.0))
        after = result.levels[index]
        if after > level:
            charges.append((hour.timestamp, after - level))
        level = after
    return old


def held_with_old_energy(hours, start_level, result, cfg) -> list[int]:
    """Hours that hold (house buys from the grid) while more than one step of the level is older than max_age_hours."""
    old = old_percent_at_start(hours, start_level, result, cfg)
    return [
        index
        for index, (hour, action) in enumerate(zip(hours, result.actions))
        if action == "force_solar_only_charging" and hour.consumption > 0.0 and old[index] > cfg.step + 0.1
    ]


def _holding_hours() -> list[OptimizerHour]:
    """The battery holds 1 kWh: the house uses it at 4.0 instead of buying at 2.5 now."""
    return [OptimizerHour(at(0), 2.5, 0.0, 1.0, 0.0), OptimizerHour(at(1), 4.0, 0.0, 1.0, 0.0)]


@pytest.mark.parametrize(
    ("max_age", "charged_before", "unaged", "held"),
    [
        (48.0, ((at(-100), 100.0),), 0.0, False),  # charged 100 h ago: the whole level is old
        (48.0, ((at(-10), 10.0),), 0.0, True),  # charged 10 h ago: fresh
        (None, ((at(-100), 100.0),), 0.0, True),  # no limit
        (48.0, (), 10.0, True),  # history unknown: counted as never old
    ],
)
def test_old_energy_is_not_held_for_a_more_expensive_hour(max_age, charged_before, unaged, held: bool) -> None:
    cfg = cfg_with(unsupported=HOLD_ONLY, max_age_hours=max_age, charged_before=charged_before, unaged=unaged)
    plain = optimize(_holding_hours(), 15.0, cfg, 0.0)
    ruled, _ = optimize_with_rules(_holding_hours(), 15.0, cfg, 0.0, _all_rules)
    for result in (plain, ruled):
        if held:
            assert result.actions == ["force_solar_only_charging", "discharge_allowed"]
        else:
            assert result.actions[0] == "discharge_allowed"


def _forward_hours() -> list[OptimizerHour]:
    """A cheap hour, 24 hours at 1.5 with house use, then an expensive hour: the plan holds the cheap energy."""
    hours = [OptimizerHour(at(0), 0.5, 0.0, 0.3, 0.0)]
    hours += [OptimizerHour(at(hour), 1.5, 0.0, 0.3, 0.0) for hour in range(1, 25)]
    hours.append(OptimizerHour(at(25), 4.0, 0.0, 4.0, 0.0))
    return hours


def test_planned_charges_are_not_held_past_max_age() -> None:
    cfg = cfg_with(max_age_hours=12.0)
    result, _ = optimize_with_rules(_forward_hours(), 5.0, cfg, 0.0, _all_rules)
    assert held_with_old_energy(_forward_hours(), 5.0, result, cfg) == []
    # the scenario bites: without the limit the plan holds the cheap energy past 12 h
    unlimited, _ = optimize_with_rules(_forward_hours(), 5.0, cfg_with(), 0.0, _all_rules)
    assert held_with_old_energy(_forward_hours(), 5.0, unlimited, cfg)


def _two_day_hours() -> list[OptimizerHour]:
    """24 hours from 12:00 to 11:00 the next day: charge cheap at noon, hold through midnight, use at 4.0 in the morning."""
    hours = [OptimizerHour(at(12), 0.5, 0.0, 0.3, 0.0)]
    hours += [OptimizerHour(at(hour), 1.5, 0.0, 0.3, 0.0) for hour in range(13, 35)]
    hours.append(OptimizerHour(at(35), 4.0, 0.0, 4.0, 0.0))
    return hours


def test_per_day_plan_is_not_held_past_max_age() -> None:
    """optimize_per_day() splits the hours at midnight; the energy held over midnight is old on the second day."""
    cfg = cfg_with(max_age_hours=12.0)
    result, _ = optimize_per_day(_two_day_hours(), 5.0, cfg, 3.0, _all_rules)
    assert len(result.actions) == len(_two_day_hours())
    assert held_with_old_energy(_two_day_hours(), 5.0, result, cfg) == []
    unlimited, _ = optimize_per_day(_two_day_hours(), 5.0, cfg_with(), 3.0, _all_rules)
    assert held_with_old_energy(_two_day_hours(), 5.0, unlimited, cfg)


def test_result_reports_old_energy() -> None:
    cfg = cfg_with(unsupported=HOLD_ONLY, max_age_hours=48.0, charged_before=((at(-100), 100.0),))
    result = optimize(_holding_hours(), 15.0, cfg, 0.0)
    assert len(result.old) == 2
    assert result.old[0] > 0.0
    plain = optimize(_holding_hours(), 15.0, cfg_with(unsupported=HOLD_ONLY), 0.0)
    assert all(value < 0.01 for value in plain.old)  # no limit: nothing is old (empty or zeros)


def test_unknown_age_energy_stops_covering_for_later_charges() -> None:
    """The energy of unknown age leaves first: once the battery is used up and charged again, what is
    held back for 12 h is old like any other (no history = the whole start level has an unknown age)."""
    hours = [OptimizerHour(at(0), 5.0, 0.0, 4.5, 0.0), OptimizerHour(at(1), 0.1, 0.0, 0.3, 0.0)]
    hours += [OptimizerHour(at(hour), 1.5, 0.0, 0.3, 0.0) for hour in range(2, 30)]
    hours.append(OptimizerHour(at(30), 6.0, 0.0, 4.0, 0.0))
    cfg = cfg_with(max_age_hours=12.0, unaged=55.0)
    for run in (
        lambda: optimize(hours, 60.0, cfg, 0.0),
        lambda: optimize_with_rules(hours, 60.0, cfg, 0.0, _all_rules)[0],
        lambda: optimize_per_day(hours, 60.0, cfg, 0.0, _all_rules)[0],
    ):
        result = run()
        assert held_with_old_energy(hours, 60.0, result, cfg) == []
    # the scenario bites: without the limit the cheap charge is held for the expensive hour
    unlimited = optimize(hours, 60.0, cfg_with(unaged=55.0), 0.0)
    assert held_with_old_energy(hours, 60.0, unlimited, cfg)


def test_enforce_max_age_changes_only_holds_with_old_energy() -> None:
    """The rule plan: a hold with old energy in the battery uses it, everything else stays."""
    actions, limits = ["force_solar_only_charging", "force_solar_only_charging"], [None, None]
    old = cfg_with(unsupported=HOLD_ONLY, max_age_hours=48.0, charged_before=((at(-100), 100.0),))
    fixed, fixed_limits, old_levels = enforce_max_age(_holding_hours(), 15.0, old, actions, limits)
    assert fixed[0] == "discharge_allowed" and fixed_limits[0] is None and old_levels[0] > 0.0
    fresh = cfg_with(unsupported=HOLD_ONLY, max_age_hours=48.0, charged_before=((at(-10), 10.0),))
    assert enforce_max_age(_holding_hours(), 15.0, fresh, actions, limits)[0][0] == "force_solar_only_charging"
    # not a hold in a sale hour: selling is not limited
    assert enforce_max_age(_holding_hours(), 15.0, old, ["force_discharge", "grid_charging"], [1.0, 1.0])[0] == ["force_discharge", "grid_charging"]


# --- the whole planner: charging history -> age of the energy -> plan -------------------------------------------------

LEVEL = 50.0


def plan_with_history(controls: bool, hours_old: int, switch: bool, charged_at: datetime.datetime) -> BattMindEngine:
    """Planned at 17:30 with 50 % in the battery, which is the latest charge (60 % charged at charged_at)."""
    now = datetime.datetime.combine(DAY, datetime.time(17, 30))
    engine = BattMindEngine(build_config({**OPTIONS, "energidataservice_sell_entity_id": "sensor.sell"}), "test", TEXTS)
    assert engine.run_job(realistic_snapshot(now, LEVEL), engine.startup, {"power_values": _power_values_db()}).error is None
    engine.charging_history_db[charged_at] = {
        "charged": {"percentage": 60.0, "kWh": 6.0, "kWh_from_grid": 6.0, "kWh_from_local_energy": 0.0, "cost": 4.8, "price": 0.8}
    }
    settings = {"optimizer_control": controls, "powerwall_max_hours_old": hours_old, "force_powerwall_max_hours_old_discharge": switch}
    result = engine.run_job(realistic_snapshot(now, LEVEL, settings), engine.charge_if_needed, True)
    assert result.error is None, result.error
    return engine


def holds_with_old_energy(engine: BattMindEngine, controls: bool, hours_old: int, charged_at: datetime.datetime) -> list[datetime.datetime]:
    """Independent FIFO check of the plan that controls the battery: hours that hold (house consumption, no discharge)
    while more than 1 % of the level is older than hours_old (the levels of the plan are rounded to 0.1)."""
    action_key, level_key = ("action", "battery_level") if controls else ("rules_action", "rules_battery_level")
    minimum = engine.config["solar"]["powerwall_battery_level_min"]
    charges = [(charged_at, LEVEL)]  # the 50 % in the battery are the latest charge
    level, held = LEVEL, []
    for row in engine.optimizer_shadow["hours"]:
        when = row["hour"]
        fresh = sum(pct for charged, pct in charges if when - datetime.timedelta(hours=hours_old) < charged < when)
        consumption = engine.plan[(when.date() - DAY).days]["hour_cost_prediction"][engine.forecast_type][when.hour]["kwh"]
        if row[action_key] == "force_solar_only_charging" and consumption > 0.0 and level - minimum - fresh > 1.0:
            held.append(when)
        if row[level_key] > level + 0.05:
            charges.append((when, row[level_key] - level))
        level = row[level_key]
    return held


def old_energy_hours(engine: BattMindEngine) -> set:
    return {ts for day in engine.plan if isinstance(day, int) for ts in engine.plan[day]["force_powerwall_max_hours_old_discharge_timestamps"]}


@pytest.mark.parametrize("controls", [True, False], ids=["optimizer", "rules"])
def test_plan_does_not_hold_energy_past_max_age(controls: bool) -> None:
    """Optimizer on or off: with 6 h the energy charged tonight can't be kept for tomorrow's expensive hours."""
    charged_at = datetime.datetime.combine(DAY, datetime.time(16))
    limited = plan_with_history(controls, 6, True, charged_at)
    assert holds_with_old_energy(limited, controls, 6, charged_at) == []
    assert old_energy_hours(limited)
    # the check bites: the same plan without the limit does hold old energy, and lists no old energy hours
    unlimited = plan_with_history(controls, 6, False, charged_at)
    assert holds_with_old_energy(unlimited, controls, 6, charged_at)
    assert not old_energy_hours(unlimited)


def test_old_energy_in_the_battery_is_used_at_once() -> None:
    """Backward: the latest charge was 3 days ago, the optimiser doesn't hold the energy (fresh: it does)."""
    old, fresh = datetime.datetime.combine(DAY, datetime.time(2)) - datetime.timedelta(days=3), datetime.datetime.combine(DAY, datetime.time(16))
    first_hour = datetime.datetime.combine(DAY, datetime.time(17))
    assert first_hour in old_energy_hours(plan_with_history(True, 48, True, old))
    hours = {row["hour"]: row["action"] for row in plan_with_history(True, 48, True, old).optimizer_shadow["hours"]}
    assert hours[first_hour] != "force_solar_only_charging"
    hours = {row["hour"]: row["action"] for row in plan_with_history(True, 48, True, fresh).optimizer_shadow["hours"]}
    assert hours[first_hour] == "force_solar_only_charging"


def test_rule_plan_uses_energy_that_becomes_old_on_the_next_day() -> None:
    """The rule plan (optimiser off) checks the max. age on every planned day, also with a limit of 12 h.

    Planned at 20:30 with 80 %: 30 % charged 60 h ago, 30 % charged 2 h ago (it is 12 h old at 06:30 tomorrow) and
    the rest of unknown age. Profit only holds the battery in the day hours tomorrow, but old energy is never held."""
    now = datetime.datetime.combine(DAY, datetime.time(20, 30))
    engine = BattMindEngine(build_config({**OPTIONS, "energidataservice_sell_entity_id": "sensor.sell"}), "test", TEXTS)
    assert engine.run_job(realistic_snapshot(now, 80), engine.startup, {"power_values": _power_values_db()}).error is None
    for ago, price in ((60, 0.2), (2, 0.6)):
        engine.charging_history_db[now.replace(minute=0) - datetime.timedelta(hours=ago)] = {
            "charged": {"percentage": 30.0, "kWh": 3.0, "kWh_from_grid": 3.0, "kWh_from_local_energy": 0.0, "cost": 3.0 * price, "price": price}
        }
    settings = {"optimizer_control": False, "force_powerwall_max_hours_old_discharge": True, "powerwall_max_hours_old": 12, "only_discharge_on_profit": True}
    result = engine.run_job(realistic_snapshot(now, 80, settings), engine.charge_if_needed, True)
    assert result.error is None, result.error
    tomorrow = engine.plan[1]
    old_from = now.replace(minute=0) - datetime.timedelta(hours=2) + datetime.timedelta(hours=12)
    day_hours = [ts for ts in tomorrow["force_powerwall_max_hours_old_discharge_timestamps"] if ts >= old_from]
    assert day_hours, "the energy that turns 12 h old tomorrow is used"
    assert not [ts for ts in tomorrow["blocked_discharge_timestamps"] if ts >= old_from and ts < old_from.replace(hour=22)]


def test_max_age_of_kwh_has_a_minimum_of_12_hours() -> None:
    from custom_components.battmind.const import NUMBER_SETTINGS

    assert NUMBER_SETTINGS["powerwall_max_hours_old"]["min"] == 12
    assert NUMBER_SETTINGS["powerwall_max_hours_old"]["default"] >= 12


def test_result_lists_the_planned_grid_charges() -> None:
    hours = _forward_hours()
    result = optimize(hours, 5.0, cfg_with(max_age_hours=12.0), 0.0)
    grid_hours = {hour.timestamp for hour, action in zip(hours, result.actions) if action == "grid_charging"}
    charged_hours = {when for when, _ in result.charged}
    assert charged_hours
    assert charged_hours <= grid_hours
    assert all(pct > 0.0 for _, pct in result.charged)
