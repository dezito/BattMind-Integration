"""Full charge (number full_charge_recommended): the optimiser requirement, the rule, the detection and the sensor."""
from __future__ import annotations

import datetime
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant

from custom_components.battmind.const import build_config
from custom_components.battmind.engine import BattMindEngine
from custom_components.battmind.engine.optimizer import (
    OptimizerConfig,
    OptimizerHour,
    explain,
    optimize,
    optimize_per_day,
    optimize_with_rules,
)
from custom_components.battmind.engine.planner.replay import ReplayInputs
from custom_components.battmind.utils.codec import decode, encode

from .conftest import BATTERY_LEVEL, PREFIX, async_unload_and_join, set_source_states
from .test_engine import NO_SELLING, OPTIONS, TEXTS, TZ, _power_values_db, _price_history, snapshot, solar_engine, started_engine
from .test_planner_rules import DAY as PLANNER_DAY, _what_if_data, planned, realistic_snapshot

DAY = datetime.date(2026, 10, 22)
CFG = OptimizerConfig(
    battery_size=10.0, level_min=10.0, level_max=100.0, max_charge=5.0, max_discharge=5.0, efficiency=0.95, wear=0.0, step=0.5
)
FULL = 99.0  # level_max - FULL_CHARGE_MARGIN


def _hours(days: int = 1, cheap=(2, 3, 4), solar: dict | None = None, consumption: float = 0.5) -> list[OptimizerHour]:
    hours = []
    for day in range(days):
        for hour in range(24):
            timestamp = datetime.datetime.combine(DAY + datetime.timedelta(days=day), datetime.time(hour))
            sun = (solar or {}).get(hour, 0.0)
            hours.append(OptimizerHour(
                timestamp, 0.9 if hour in cheap else 1.0, 0.1, 0.0 if sun > 0.0 else consumption, sun,
            ))
    return hours


def _deadline(day: int = 0, hour: int = 23) -> datetime.datetime:
    return datetime.datetime.combine(DAY + datetime.timedelta(days=day), datetime.time(hour))


def _required(cfg: OptimizerConfig = CFG, day: int = 0, hour: int = 23) -> OptimizerConfig:
    return replace(cfg, full_deadline=_deadline(day, hour), full_level=FULL)


def _first_full(hours, result, level=FULL):
    return next((i for i, value in enumerate(result.levels) if value >= level - 1e-6), None)


def _all_on(_switch: str) -> bool:
    return True


# --------------------------------------------------------------------------- #
# Optimiser
# --------------------------------------------------------------------------- #


def test_no_requirement_gives_the_same_plan_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: without a requirement (number 0 / not due), or with a deadline before the first hour, the full charge
    layer never runs, so the plan is exactly the plan of today."""
    from custom_components.battmind.engine import optimizer as optimizer_module

    def never(*args, **kwargs):
        raise AssertionError("the full charge layer ran without a requirement")

    monkeypatch.setattr(optimizer_module, "_full_charge_layer", never)
    hours = _hours(2)
    plain = optimize(hours, 20.0, CFG, 0.0)
    for cfg in (replace(CFG, full_deadline=None, full_level=None), replace(CFG, full_deadline=_deadline(-1), full_level=FULL)):
        none = optimize(hours, 20.0, cfg, 0.0)
        assert (none.actions, none.levels, none.grid_kwh, none.costs) == (plain.actions, plain.levels, plain.grid_kwh, plain.costs)
        assert none.full_reached is None and none.full_dropped is None


def test_due_full_charge_reaches_full_in_the_cheapest_hours_and_is_not_held() -> None:
    hours = _hours()
    plain = optimize(hours, 20.0, CFG, 0.0)
    assert max(plain.levels) < FULL  # without the requirement the battery isn't filled
    result = optimize(hours, 20.0, _required(), 0.0)
    assert result.full_dropped is None
    reached = _first_full(hours, result)
    assert reached is not None and result.full_reached == reached
    charged = [i for i, kwh in enumerate(result.grid_kwh) if kwh > 0.01]
    assert charged and all(hours[i].buy == 0.9 for i in charged)  # only the cheap hours
    # reaching full once is enough: the energy is used afterwards, not held until the deadline
    assert result.levels[-1] < FULL
    explanations = explain(hours, result, _required())
    assert {explanations[i]["rule"] for i in charged} == {"full_charge_planner"}


def test_full_battery_at_the_start_needs_nothing() -> None:
    hours = _hours()
    plain = optimize(hours, 100.0, CFG, 0.0)
    result = optimize(hours, 100.0, _required(), 0.0)
    assert result.actions == plain.actions and result.levels == plain.levels
    assert result.full_dropped is None


def test_solar_that_fills_the_battery_before_the_deadline_means_no_grid_charge() -> None:
    hours = _hours(solar={hour: 3.0 for hour in range(9, 16)})
    result = optimize(hours, 20.0, _required(), 0.0)
    assert result.full_dropped is None
    assert _first_full(hours, result) is not None
    assert sum(result.grid_kwh) == pytest.approx(0.0)


@pytest.mark.parametrize("case", ["slow_charger", "grid_unsupported"])
def test_unreachable_requirement_is_dropped_and_the_plan_stays_the_unconstrained_one(case: str) -> None:
    hours = _hours()
    cfg = replace(CFG, max_charge=0.2) if case == "slow_charger" else replace(CFG, unsupported=frozenset({"grid_charging"}))
    plain = optimize(hours, 20.0, cfg, 0.0)
    result = optimize(hours, 20.0, _required(cfg), 0.0)
    assert result.full_dropped == "unreachable"
    assert result.full_reached is None
    assert result.actions == plain.actions and result.levels == plain.levels


def test_solar_charging_is_not_blocked_before_the_battery_has_been_full() -> None:
    """Without the requirement the sun's early surplus is sold (blocked) because the later sun fills the battery anyway."""
    solar = {hour: 3.0 for hour in range(8, 17)}
    hours = _hours(solar=solar)
    for hour in hours:
        if hour.timestamp.hour in (8, 9):
            hour.sell = 2.0  # sold for more than any later use is worth
    plain = optimize(hours, 60.0, CFG, 0.0)
    assert "blocked_solar_charge" in plain.actions
    result = optimize(hours, 60.0, _required(hour=11), 0.0)
    assert result.full_dropped is None
    reached = result.full_reached
    assert reached is not None and reached <= 11
    assert "blocked_solar_charge" not in result.actions[: reached + 1]


def test_rules_switched_off_never_forbid_the_full_charge() -> None:
    """With the charge rules off the requirement still converges: its charges belong to no switch."""
    hours = _hours()
    off = {"cheapest_hour_fill_planner", "most_expensive_planner", "needed_before_max_level_planner", "negative_price_planner"}
    result, explanations = optimize_with_rules(hours, 20.0, _required(), 0.0, lambda switch: switch not in off)
    assert result.converged
    assert result.full_reached is not None
    charged = [i for i, kwh in enumerate(result.grid_kwh) if kwh > 0.01]
    assert charged and all(explanations[i]["rule"] == "full_charge_planner" for i in charged)
    # without the requirement the same switches allow no grid charging at all
    plain, _ = optimize_with_rules(_hours(), 20.0, CFG, 0.0, lambda switch: switch not in off)
    assert sum(plain.grid_kwh) == pytest.approx(0.0)


def test_per_day_the_deadline_day_has_its_own_requirement() -> None:
    hours = _hours(2)
    result, explanations = optimize_per_day(hours, 20.0, _required(day=1), 0.0, _all_on)
    assert result.converged and result.full_dropped is None
    reached = _first_full(hours, result)
    assert reached is not None and 24 <= reached <= 47  # day 0 doesn't know it, day 1 has to reach full
    assert result.full_reached == reached
    assert all(explanations[i]["rule"] == "full_charge_planner" for i in range(24, reached + 1) if result.grid_kwh[i] > 0.01)


def test_per_day_a_full_battery_on_an_earlier_day_ends_the_requirement() -> None:
    hours = _hours(2, solar={hour: 3.0 for hour in range(9, 16)})
    for hour in hours[24:]:
        hour.solar, hour.consumption = 0.0, 0.5  # no sun on the deadline day
    # the energy left at midnight is worth more than the solar sell price, so the sun fills the battery on day 0
    plain, _ = optimize_per_day(hours, 20.0, CFG, 0.5, _all_on)
    result, _ = optimize_per_day(hours, 20.0, _required(day=1), 0.5, _all_on)
    assert _first_full(hours, plain) is not None and _first_full(hours, plain) < 24  # the sun filled it on day 0
    assert result.actions == plain.actions and result.levels == plain.levels
    assert result.full_dropped is None


# --------------------------------------------------------------------------- #
# Engine: detection, due state, the plan
# --------------------------------------------------------------------------- #

NOW = datetime.datetime.combine(DAY, datetime.time(10, 30))
# no charge rule of their own: without a full charge nothing is charged from the grid
NO_CHARGE_RULES = {
    key: False
    for key in (
        "cheapest_hour_fill_planner", "cheapest_hour_fill_up", "most_expensive_planner", "needed_before_max_level_planner",
        "negative_price_planner", "grid_charge_to_sell",
    )
}


def _aware(naive: datetime.datetime) -> datetime.datetime:
    return naive.replace(tzinfo=TZ)


def _ts(naive: datetime.datetime) -> float:
    return _aware(naive).timestamp()


def _run(engine: BattMindEngine, snap, func, *args):
    result = engine.run_job(snap, func, *args)
    assert result.error is None, result.error
    return result


def _plan(engine: BattMindEngine, now: datetime.datetime, level, settings: dict, snap=None):
    snap = snap or snapshot(now, level, settings)
    return _run(engine, snap, engine.charge_if_needed, True)


def _full_charge_sessions(engine: BattMindEngine) -> list[datetime.datetime]:
    return sorted(ts for ts, session in engine.charge_hours.items() if isinstance(ts, datetime.datetime) and session.get("full_charge_planner"))


def _highest_level(result, until: datetime.datetime) -> float:
    flow = result.attributes["powerwall_action"]["battery_level_flow"]
    return max(row["battery_level"] for row in flow if row["hour"] <= until)


def _status(engine: BattMindEngine, now: datetime.datetime, level, settings: dict) -> dict:
    return _run(engine, snapshot(now, level, settings), engine.full_charge_status).value


@pytest.mark.parametrize("level", [99.0, 100.0])
def test_reaching_max_level_minus_the_margin_registers_a_full_charge_at_once(level: float) -> None:
    engine = started_engine(NOW, 50)
    assert engine.last_full_charge() is None
    now = NOW + datetime.timedelta(minutes=5)
    result = _plan(engine, now, level, {})
    assert result.states["last_full_charge"] == _aware(now)
    assert result.states["last_full_charge"].tzinfo is not None
    assert "analysis" in result.db_saves
    assert decode(json.loads(json.dumps(result.db_saves["analysis"])))["full_charge"]["last"] == _aware(now)
    # staying full is not a new full charge: no new time, no save every few minutes
    later = _plan(engine, now + datetime.timedelta(minutes=5), level, {})
    assert "last_full_charge" not in later.states
    assert engine.last_full_charge() == _aware(now)


def test_just_below_the_margin_is_not_full() -> None:
    engine = started_engine(NOW, 50)
    result = _plan(engine, NOW + datetime.timedelta(minutes=5), 98.9, {})
    assert "last_full_charge" not in result.states
    assert engine.last_full_charge() is None


@pytest.mark.parametrize("state", ["unavailable", "unknown", "nan", "inf", "not-a-number"])
def test_a_battery_level_that_is_no_number_never_registers_a_full_charge(state: str) -> None:
    """get_battery_level() uses 100 % as its fail-safe: that is never a full battery."""
    engine = started_engine(NOW, 50)
    snap = snapshot(NOW, state, {})
    snap.buffers = {"sensor.battery_level": [(_ts(NOW - datetime.timedelta(minutes=10)), float("nan")), (_ts(NOW), None)]}
    _run(engine, snap, engine.charge_if_needed, True)
    _run(engine, snap, engine.hour_end, NOW.replace(minute=0))
    assert engine.last_full_charge() is None


def test_a_short_peak_between_two_jobs_is_found_in_the_buffer() -> None:
    engine = started_engine(NOW, 50)
    peak = NOW + datetime.timedelta(minutes=2)
    snap = snapshot(NOW.replace(minute=59), 70, {})
    snap.buffers = {"sensor.battery_level": [(_ts(NOW), 96.0), (_ts(peak), 100.0), (_ts(peak + datetime.timedelta(minutes=1)), 97.0)]}
    _run(engine, snap, engine.hour_end, NOW.replace(minute=0))
    assert engine.last_full_charge() == _aware(peak)


def test_unknown_last_full_charge_is_looked_up_in_the_battery_level_history() -> None:
    engine = BattMindEngine(build_config(OPTIONS), "test", TEXTS)
    full_at = NOW - datetime.timedelta(hours=30)
    snap = snapshot(NOW, 60, {})
    snap.buffers = {"sensor.battery_level": [
        (_ts(full_at - datetime.timedelta(hours=1)), 80.0), (_ts(full_at), 100.0), (_ts(full_at + datetime.timedelta(hours=2)), 60.0),
    ]}
    result = _run(engine, snap, engine.startup, {"power_values": _power_values_db()})
    assert result.states["last_full_charge"] == _aware(full_at)


def test_unknown_last_full_charge_is_due_with_the_deadline_today() -> None:
    """Never seen full: due (never "just charged"), and overdue so the deadline can't slide every day."""
    engine = started_engine(NOW, 50)
    status = _status(engine, NOW, 50, {"full_charge_recommended": 7})
    assert status["due"] and status["overdue"] and status["active"]
    assert status["deadline"] == datetime.datetime.combine(DAY, datetime.time(23))
    assert not _status(engine, NOW, 50, {"full_charge_recommended": 0})["due"]


@pytest.mark.parametrize(
    ("days_ago", "due", "deadline_day"),
    [(6.9, False, None), (7.5, True, 1), (8.0, True, 0), (12.0, True, 0)],
)
def test_due_and_the_deadline(days_ago: float, due: bool, deadline_day: int | None) -> None:
    engine = started_engine(NOW, 50)
    engine.analysis_db["full_charge"] = {"last": _aware(NOW) - datetime.timedelta(days=days_ago)}
    status = _status(engine, NOW, 50, {"full_charge_recommended": 7})
    assert status["due"] is due
    if deadline_day is None:
        assert status["deadline"] is None and not status["active"]
    else:
        assert status["deadline"] == datetime.datetime.combine(DAY + datetime.timedelta(days=deadline_day), datetime.time(23))
        assert status["active"]


def test_days_are_counted_in_real_hours_over_a_daylight_saving_change() -> None:
    """25 October 2026 has 25 hours in Copenhagen: noon to noon two days later is more than 2 days."""
    now = datetime.datetime(2026, 10, 26, 12, 0)
    engine = started_engine(now, 50)
    # stored with a fixed offset, as the database gives it back
    last = decode(encode({"last": datetime.datetime(2026, 10, 24, 12, 0, tzinfo=TZ)}))["last"]
    assert last.utcoffset() == datetime.timedelta(hours=2)
    engine.analysis_db["full_charge"] = {"last": last}
    status = _status(engine, now, 50, {"full_charge_recommended": 2})
    assert status["elapsed_days"] == pytest.approx(49 / 24)
    assert status["due"] and not status["overdue"]
    # back in the real time zone (a fixed offset would get the day arithmetic over the change wrong)
    assert engine.run_job(snapshot(now, 50), engine.last_full_charge).value.tzinfo == TZ


def test_a_full_battery_now_is_never_due() -> None:
    engine = started_engine(NOW, 100)  # registers now
    engine.analysis_db["full_charge"] = {"last": _aware(NOW) - datetime.timedelta(days=30)}
    assert not _status(engine, NOW, 100, {"full_charge_recommended": 7})["due"]


def test_tomorrow_without_published_prices_has_no_requirement_yet() -> None:
    """Tomorrow's hours from the price history (no real price) don't count: no full charge is forced today."""
    engine = BattMindEngine(build_config({**OPTIONS, "days_to_predict": 3}), "test", TEXTS)
    snap = snapshot(NOW, 50, {**NO_CHARGE_RULES, "full_charge_recommended": 7})
    snap.price_data["attributes"]["sensor.price"].update(raw_tomorrow=[], tomorrow_valid=False, forecast=[])
    _run(engine, snap, engine.startup, {"power_values": _power_values_db(), "kwh_avg_prices": _price_history()})
    engine.analysis_db["full_charge"] = {"last": _aware(NOW) - datetime.timedelta(days=7.5)}
    _run(engine, snap, engine.charge_if_needed, True)
    assert engine.grid_price_cache["sources"][datetime.datetime.combine(DAY + datetime.timedelta(days=1), datetime.time(23))] == "database"
    status = _run(engine, snap, engine.full_charge_status).value
    assert status["due"] and not status["active"]
    assert not _full_charge_sessions(engine)
    assert engine.optimizer_shadow["full_charge"] is None


@pytest.mark.parametrize(("days_ago", "active"), [(7.5, False), (8.5, True)])
def test_one_day_to_plan_only_has_the_overdue_deadline_today(days_ago: float, active: bool) -> None:
    engine = started_engine(NOW, 50, options={"days_to_predict": 1})
    engine.analysis_db["full_charge"] = {"last": _aware(NOW) - datetime.timedelta(days=days_ago)}
    assert _status(engine, NOW, 50, {"full_charge_recommended": 7})["active"] is active


@pytest.mark.parametrize("optimizer_control", [True, False])
def test_due_full_charge_fills_the_battery_in_the_cheapest_hours(optimizer_control: bool) -> None:
    """Rules and optimiser: with every charge rule off, a due full charge still fills the battery before the deadline."""
    settings = {**NO_CHARGE_RULES, "optimizer_control": optimizer_control}
    plain = started_engine(NOW, 50)
    _plan(plain, NOW, 50, settings)
    assert not [ts for ts in plain.charge_hours if isinstance(ts, datetime.datetime)]

    engine = started_engine(NOW, 50)
    engine.analysis_db["full_charge"] = {"last": _aware(NOW) - datetime.timedelta(days=7.5)}
    result = _plan(engine, NOW, 50, {**settings, "full_charge_recommended": 7})
    deadline = datetime.datetime.combine(DAY + datetime.timedelta(days=1), datetime.time(23))
    sessions = _full_charge_sessions(engine)
    assert sessions and all(ts <= deadline for ts in sessions)
    # tomorrow night is the cheapest time before the deadline
    assert all(ts.date() == DAY + datetime.timedelta(days=1) and ts.hour <= 6 for ts in sessions)
    assert _highest_level(result, deadline) >= 99.0
    assert engine.optimizer_shadow["converged"]
    plan = engine.full_charge_plan
    assert plan["optimal_dropped"] is None and plan["optimal_reached"] is not None
    if not optimizer_control:
        assert plan["rules_reached"] is True
    assert "💯" in result.attributes["overview"]["overview"]  # the reason of the charge
    reasons = [engine.plan[1]["charging_sessions"][ts]["reason"] for ts in sessions]
    assert all(TEXTS["ui.reason.title.full_charge"] in reason for reason in reasons)


def test_number_0_or_not_due_gives_the_plan_of_today() -> None:
    """Regression: with the number 0 or a recent full charge the plan is exactly the plan without the feature."""
    plans = []
    for settings, days_ago in (({}, None), ({"full_charge_recommended": 0}, 30.0), ({"full_charge_recommended": 7}, 1.0)):
        engine = started_engine(NOW, 50)
        if days_ago is not None:
            engine.analysis_db["full_charge"] = {"last": _aware(NOW) - datetime.timedelta(days=days_ago)}
        _plan(engine, NOW, 50, settings)
        assert engine.full_charge_plan is None
        plans.append((
            {ts: dict(session) if isinstance(session, dict) else session for ts, session in engine.charge_hours.items()},
            [dict(row) for row in engine.optimizer_shadow["hours"]],
        ))
    assert plans[0] == plans[1] == plans[2]


@pytest.mark.parametrize(("maximum", "target", "not_shown"), [(100.0, "99", "100"), (95.5, "94.5", "94")])
def test_a_full_charge_that_cant_be_reached_says_why_with_the_level_it_aims_at(maximum: float, target: str, not_shown: str) -> None:
    """Overdue (never seen full) at 23:30 from 10 %: the last hour can't fill the battery, the plan says so (the target is
    the max. level minus the margin, a fractional one is not rounded away)."""
    engine = started_engine(NOW, 50, options={"powerwall_battery_level_max": maximum})
    assert engine.config["solar"]["powerwall_battery_level_max"] == maximum
    late = datetime.datetime.combine(DAY, datetime.time(23, 30))
    result = _plan(engine, late, 10, {"full_charge_recommended": 7})
    plan = engine.full_charge_plan
    assert plan is not None and plan["optimal_dropped"] == "unreachable" and plan["rules_reached"] is False
    overview = result.attributes["overview"]["overview"]
    assert TEXTS["ui.cheap_grid_charge_hours.full_charge_dropped"].format(level=target) in overview
    assert TEXTS["ui.cheap_grid_charge_hours.full_charge_dropped"].format(level=not_shown) not in overview


def _solar_plan(full_charge_days: int, extra_options: dict | None = None, sell_hours=(10,), sell_day: int = 0):
    """Rules control, 10:10 at 60 %, the sun fills the battery 09-15 every day (test_engine.solar_engine)."""
    now = datetime.datetime.combine(DAY, datetime.time(10, 10))
    engine, snap = solar_engine(now, 60, True, extra_options)
    # a copy: the snapshot's settings are test_engine.ALL_RULES itself (updating them would leak into later tests)
    snap.settings = {**snap.settings, **NO_SELLING, "optimizer_control": False, "full_charge_recommended": full_charge_days}
    engine.analysis_db["full_charge"] = {"last": _aware(now) - datetime.timedelta(days=7.5)}
    prices = engine.sell_price_cache["prices"]
    for ts in [ts for ts in prices if ts.date() == DAY + datetime.timedelta(days=sell_day) and ts.hour in sell_hours]:
        prices[ts] = 2.0
    result = _run(engine, snap, engine.charge_if_needed, True)
    blocked = sorted(ts for day in engine.plan if isinstance(day, int) for ts in engine.plan[day]["blocked_solar_charge_timestamps"])
    return engine, result, blocked


def test_unsupported_grid_charging_means_no_full_charge_requirement_and_the_same_plan() -> None:
    """Grid charging unsupported: the rule counts as off (docs/actions.md), solar blocking is not changed by the number."""
    options = {"unsupported_actions": ["grid_charging"]}
    off_engine, off_result, off_blocked = _solar_plan(0, options)
    engine, result, blocked = _solar_plan(7, options)
    assert off_blocked  # the sell price at 10:00 blocks solar charging without a full charge
    assert engine.full_charge_plan is None
    assert blocked == off_blocked
    assert result.states["powerwall_action"] == off_result.states["powerwall_action"]
    assert [dict(row) for row in engine.optimizer_shadow["hours"]] == [dict(row) for row in off_engine.optimizer_shadow["hours"]]
    assert "💯" not in result.attributes["overview"]["overview"]


def test_solar_charging_may_be_blocked_after_the_hour_the_plan_is_full() -> None:
    """The sun fills the battery today (deadline tomorrow 23:00): tomorrow's solar charging may be blocked as without the number."""
    tomorrow = DAY + datetime.timedelta(days=1)
    _, _, off_blocked = _solar_plan(0, sell_day=1)
    engine, _, blocked = _solar_plan(7, sell_day=1)
    plan = engine.full_charge_plan
    assert plan is not None and plan["rules_reached"] is True
    assert plan["deadline"] == datetime.datetime.combine(tomorrow, datetime.time(23))
    late = [ts for ts in off_blocked if ts.date() == tomorrow]
    assert late  # blocked without the full charge
    assert late == [ts for ts in blocked if ts.date() == tomorrow]


def test_the_rule_charges_at_most_the_room_the_sun_leaves_also_a_small_top_up() -> None:
    """The amount is never raised above the room (solar first) or the hour's charge power, and a top-up smaller than the
    smallest charge still closes the last gap (add_to_charge_hours checks the hour's capacity, not the amount)."""
    engine = started_engine(NOW, 50)
    hour = NOW.replace(minute=0)
    calls = []
    engine.full_charge_plan = {"deadline": hour + datetime.timedelta(hours=5), "elapsed_days": 8.0, "full_level": 99.0, "level": 100.0}
    engine.full_charge_reached = lambda ctx, deadline: False
    engine.kwh_available_in_hour = lambda *args: (False, 3.0)
    engine.charge_periods_in_hour = lambda timestamp: 1
    # (kWh needed, kWh available in the hour)
    engine.add_to_charge_hours = lambda *args, **kwargs: calls.append((args[8], args[13])) or (None, 0.0, 0.0, 0.0, 0.0)
    ctx = SimpleNamespace(
        totals=[0.0, 0.0], config=engine.config, max_kwh_charging=5.0, sorted_by_cheapest_price=[(hour + datetime.timedelta(hours=1), 0.5)],
        current_hour=hour, charging_plan={0: {}}, charge_hours={}, efficiency=0.95, kwh_to_percentage=None, name="test",
        percentage_to_kwh=None, valuta_kwh="kr/kWh",
    )
    for room, expected in ((0.0, []), (0.05, [(0.05, 3.0)]), (2.0, [(2.0, 3.0)]), (4.0, [(3.0, 3.0)])):
        calls.clear()
        engine.grid_headroom_kwh = lambda ctx, timestamp, price=None, until=None, room=room: room
        engine.full_charge_planner(ctx, 0)
        assert calls == expected


def test_add_to_charge_hours_takes_a_small_need_in_an_hour_with_capacity() -> None:
    """What the rule relies on: the minimum of add_to_charge_hours is on the hour's capacity, not on the amount needed."""
    engine = started_engine(NOW, 50)
    hour = NOW.replace(minute=0) + datetime.timedelta(hours=1)
    charge_hours: dict = {}
    engine.add_to_charge_hours(
        charge_hours, engine.config, 0.95, lambda kwh: kwh * 10.0, 5.0, "test", lambda percent: percent / 10.0, "kr/kWh",
        0.05, 0.0, 0.0, hour, 0.5, 5.0, rules=["full_charge_planner"],
    )
    assert charge_hours[hour]["kWh"] == pytest.approx(0.05)


# --------------------------------------------------------------------------- #
# Review fixes: off-grid levels, forward-pass misses, explain(), per day
# --------------------------------------------------------------------------- #

CHARGE_SWITCHES_OFF = {"cheapest_hour_fill_planner", "most_expensive_planner", "needed_before_max_level_planner", "negative_price_planner"}
DISCHARGE_SWITCHES_OFF = {
    "only_discharge_on_profit", "prioritize_discharge_hours_by_energy_cost", "most_profit", "sell_excess_kwh_available",
    "grid_charge_to_sell", "discharge_before_solar", "force_powerwall_max_hours_old_discharge",
}


@pytest.mark.parametrize("start", [98.9, 98.8, 50.3, 20.0, 73.7])
def test_a_start_level_between_the_steps_of_the_dp_still_reaches_full(start: float) -> None:
    """The DP decides on a 0.5 % grid, the plan follows the exact level: 98.9 rounds to 99.0 but is not full."""
    hours = _hours()
    result = optimize(hours, start, _required(), 0.0)
    assert result.full_dropped is None
    assert result.full_reached is not None and result.levels[result.full_reached] >= FULL - 1e-6
    explanations = explain(hours, result, _required())
    charged = [i for i, kwh in enumerate(result.grid_kwh) if kwh > 0.01 and i <= result.full_reached]
    assert charged and all(explanations[i]["rule"] == "full_charge_planner" for i in charged)


@pytest.mark.parametrize("with_rules", [False, True])
def test_98_9_rounds_to_99_on_the_grid_but_is_charged_to_full(with_rules: bool) -> None:
    """No consumption: every action keeps the grid level 99.0, which is not full at the exact 98.9 (review: HIGH)."""
    hours = _hours(consumption=0.0)
    off = CHARGE_SWITCHES_OFF | DISCHARGE_SWITCHES_OFF
    if with_rules:
        result, _ = optimize_with_rules(hours, 98.9, _required(hour=5), 0.0, lambda switch: switch not in off)
        assert result.converged
    else:
        result = optimize(hours, 98.9, _required(hour=5), 0.0)
    assert result.full_dropped is None
    assert result.full_reached is not None and result.full_reached <= 5 and result.levels[result.full_reached] >= FULL - 1e-6


def test_a_plan_that_misses_full_in_the_forward_pass_is_the_plan_without_the_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the forward pass doesn't reach full the layer's holds and charges are not kept: the plain plan is returned."""
    from custom_components.battmind.engine import optimizer as optimizer_module

    original = optimizer_module._full_charge_layer

    def too_low(hour, levels, index, options, full, not_full, cfg, later, to_kwh):
        # the layer believes 79 % is full: the forward pass (exact level, 99 %) never gets there
        return original(hour, levels, index, options, full, not_full, replace(cfg, full_level=cfg.full_level - 20.0), later, to_kwh)

    monkeypatch.setattr(optimizer_module, "_full_charge_layer", too_low)
    hours = _hours(cheap=(2,))
    plain = optimize(hours, 20.0, CFG, 0.0)
    result = optimize(hours, 20.0, _required(), 0.0)
    assert result.full_dropped == "unreachable" and result.full_reached is None
    assert result.actions == plain.actions and result.levels == plain.levels and result.grid_kwh == plain.grid_kwh
    assert not [e for e in explain(hours, result, _required()) if e and e["rule"] == "full_charge_planner"]


@pytest.mark.parametrize("start", [98.9, 50.3])
@pytest.mark.parametrize("off", [CHARGE_SWITCHES_OFF, DISCHARGE_SWITCHES_OFF, CHARGE_SWITCHES_OFF | DISCHARGE_SWITCHES_OFF])
def test_switches_off_with_an_off_grid_start_still_converge(start: float, off: set) -> None:
    hours = _hours()
    result, explanations = optimize_with_rules(hours, start, _required(), 0.0, lambda switch: switch not in off)
    assert result.converged
    assert result.full_dropped is None
    assert result.full_reached is not None and result.levels[result.full_reached] >= FULL - 1e-6
    charged = [i for i, kwh in enumerate(result.grid_kwh) if kwh > 0.01 and i <= result.full_reached]
    assert all(explanations[i]["rule"] == "full_charge_planner" for i in charged)


def test_a_full_charge_in_a_negative_price_hour_is_labelled_full_charge_and_never_forbidden() -> None:
    hours = _hours()
    for hour in hours:
        if hour.timestamp.hour in (2, 3, 4):
            hour.buy = -0.5
    result, explanations = optimize_with_rules(hours, 20.0, _required(), 0.0, lambda switch: switch not in CHARGE_SWITCHES_OFF)
    assert result.converged and result.full_dropped is None and result.full_reached is not None
    charged = [i for i, kwh in enumerate(result.grid_kwh) if kwh > 0.01 and i <= result.full_reached]
    assert charged and all(hours[i].buy < 0 for i in charged)  # the negative hours are still used
    assert all(explanations[i]["rule"] == "full_charge_planner" for i in charged)


def test_per_day_a_deadline_after_the_last_hour_of_its_day_still_applies() -> None:
    """The deadline hour itself is missing (the plan ends at 20:00 that day): the day still has the requirement."""
    hours = [hour for hour in _hours(2) if not (hour.timestamp.date() == DAY + datetime.timedelta(days=1) and hour.timestamp.hour > 20)]
    result, _ = optimize_per_day(hours, 20.0, _required(day=1), 0.0, _all_on)
    assert result.converged and result.full_dropped is None
    reached = _first_full(hours, result)
    assert reached is not None and 24 <= reached and result.full_reached == reached


def test_per_day_hours_that_end_before_the_deadline_day_fill_on_the_last_day_present() -> None:
    """The deadline is on a day after the hours (the plan has two days): the last day present gets the requirement."""
    hours = _hours(2)
    result, _ = optimize_per_day(hours, 20.0, _required(day=2), 0.0, _all_on)
    assert result.converged and result.full_dropped is None
    reached = _first_full(hours, result)
    assert reached is not None and 24 <= reached and result.full_reached == reached


def test_max_age_holds_replaced_in_the_forward_pass_teach_the_limits_instead_of_dropping_the_full_charge() -> None:
    """All energy is old (max. age, nothing charged within it): the table's holds before the cheap hour are used instead in
    the forward pass, which then misses full. The limits of that failed plan are learned and the next round reaches full."""
    cfg = replace(CFG, max_age_hours=24.0)
    hours = _hours(cheap=(2,))
    for hour in hours:
        if hour.timestamp.hour in (0, 1):
            hour.buy = 0.95  # for the full charge holding pays: a kWh used now costs 0.9 / 0.95² to charge again
        elif hour.timestamp.hour > 2:
            hour.buy = 0.88  # without it nothing is held (later hours are cheaper): the plain plan has no held old energy
    required = _required(cfg, hour=2)
    first = optimize(hours, 60.0, required, 0.0)
    assert first.full_dropped == "unreachable"  # the case: the forward pass missed full
    assert optimize(hours, 60.0, cfg, 0.0).held_old == 0
    result, _ = optimize_with_rules(hours, 60.0, required, 0.0, _all_on)
    assert result.converged
    assert result.full_dropped is None
    assert result.full_reached is not None and result.full_reached <= 2 and result.levels[result.full_reached] >= FULL - 1e-6


def test_only_the_charge_that_crosses_into_full_is_the_full_charge() -> None:
    """A cheap night charge for an expensive morning is the cheapest-hour rule's, not the full charge's (the battery never
    empties in between): with the charge rules off it goes, and the later window still fills the battery."""
    hours = _hours(cheap=(), consumption=0.3)
    for hour in hours:
        if hour.timestamp.hour in (2, 12, 13, 14):
            hour.buy = 0.5
        elif hour.timestamp.hour < 9:
            hour.buy = 3.0  # one cheap night hour can't fill the battery (40 % - use + 47.5 %), the midday window can
    required = _required()
    on = optimize(hours, 40.0, required, 0.0)
    assert on.grid_kwh[2] > 0.01 and on.levels[2] < FULL  # the night charge for the morning (it doesn't fill the battery)
    assert on.full_reached is not None and on.full_reached >= 12
    assert min(on.levels[:12]) > CFG.level_min + 0.5  # the battery never empties before the full charge
    labels = explain(hours, on, required, 40.0)
    assert labels[2]["rule"] != "full_charge_planner"
    result, explanations = optimize_with_rules(hours, 40.0, required, 0.0, lambda switch: switch not in CHARGE_SWITCHES_OFF)
    assert result.converged
    assert result.grid_kwh[2] == 0.0
    assert result.full_dropped is None and result.full_reached is not None and 12 <= result.full_reached
    assert all(explanations[i]["rule"] == "full_charge_planner" for i, kwh in enumerate(result.grid_kwh) if kwh > 0.01)


def test_a_plan_that_reaches_full_between_the_dp_grid_and_the_level_is_not_reported_unreachable() -> None:
    """One hour that can charge to 99.1 % exactly: the table (full at 99.25 on its grid) can't promise it, but the plan
    that charges it (energy worth more at the end than its price) does reach full: reached, not dropped."""
    cfg = replace(CFG, max_charge=79.1 * CFG.battery_size / 100.0 / CFG.efficiency)
    hours = _hours(cheap=(0,), consumption=0.0)
    result = optimize(hours, 20.0, _required(cfg, hour=0), 2.0)
    assert result.levels[0] >= FULL
    assert result.full_dropped is None and result.full_reached == 0


def test_a_last_full_charge_in_the_future_is_ignored_and_a_new_one_registered() -> None:
    """A clock jump or an imported backup: a time after now is unknown (never a negative age) and doesn't block a new one."""
    engine = started_engine(NOW, 50)
    engine.analysis_db["full_charge"] = {"last": _aware(NOW) + datetime.timedelta(days=2)}
    status = _status(engine, NOW, 50, {"full_charge_recommended": 7})
    assert status["elapsed_days"] is None and status["due"] and status["overdue"]
    # the next job drops it from the database, saves that and publishes the sensor as unknown
    dropped = _plan(engine, NOW + datetime.timedelta(minutes=1), 50, {})
    assert "full_charge" not in engine.analysis_db
    assert "last_full_charge" in dropped.states and dropped.states["last_full_charge"] is None
    assert "analysis" in dropped.db_saves
    now = NOW + datetime.timedelta(minutes=5)
    result = _plan(engine, now, 100, {})
    assert result.states["last_full_charge"] == _aware(now)
    assert engine.last_full_charge() == _aware(now)


def _full_buffer(now: datetime.datetime) -> list:
    return [(_ts(now - datetime.timedelta(hours=hours)), 100.0) for hours in range(40, -1, -1)]


def test_a_restart_while_the_battery_sits_full_keeps_the_stored_time() -> None:
    stored = _aware(NOW) - datetime.timedelta(days=3)
    engine = BattMindEngine(build_config(OPTIONS), "test", TEXTS)
    snap = snapshot(NOW, 100, {})
    snap.buffers = {"sensor.battery_level": _full_buffer(NOW)}
    result = _run(engine, snap, engine.startup, {"power_values": _power_values_db(), "analysis": {"full_charge": {"last": stored}}})
    assert result.states["last_full_charge"] == stored
    # a later drop and a new reach is registered as usual
    later = NOW + datetime.timedelta(hours=1)
    _plan(engine, later, 95, {})
    again = later + datetime.timedelta(minutes=10)
    assert _plan(engine, again, 100, {}).states["last_full_charge"] == _aware(again)


def test_a_restart_while_full_without_a_stored_time_registers_the_first_full_row() -> None:
    engine = BattMindEngine(build_config(OPTIONS), "test", TEXTS)
    snap = snapshot(NOW, 100, {})
    snap.buffers = {"sensor.battery_level": _full_buffer(NOW)}
    result = _run(engine, snap, engine.startup, {"power_values": _power_values_db()})
    assert result.states["last_full_charge"] == _aware(NOW - datetime.timedelta(hours=40))


def test_the_repeated_hour_of_the_daylight_saving_change_is_stamped_in_real_time() -> None:
    """25 October 2026 02:30 happens twice in Copenhagen; the snapshot's naive now keeps fold=1 for the second one."""
    second = datetime.datetime(2026, 10, 25, 2, 30, fold=1)
    engine = started_engine(second - datetime.timedelta(minutes=30), 50)
    result = _plan(engine, second, 100, {})
    assert result.states["last_full_charge"].timestamp() == second.replace(tzinfo=TZ).timestamp()
    assert result.states["last_full_charge"].timestamp() - datetime.datetime(2026, 10, 25, 2, 30, tzinfo=TZ).timestamp() == 3600


def test_a_what_if_replay_with_the_number_on_is_the_replay_with_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The replay never reads the live last full charge: the whole simulation is the same with the number 7 and 0.

    The replay's prices have no real price list, so every deadline counts as priced here: without
    ReplayInputs.full_charge_status the requirement would be active in the replay (checked when the test was written)."""
    from custom_components.battmind.engine.full_charge import FullCharge

    monkeypatch.setattr(FullCharge, "_full_charge_priced", lambda self, deadline: True)
    now = datetime.datetime.combine(PLANNER_DAY, datetime.time(3, 30))
    simulations = []
    for days in (0, 7):
        settings = {"optimizer_control": True, "full_charge_recommended": days}
        engine, _ = planned(3, 40, settings, solar=True)
        engine.analysis_db["full_charge"] = {"last": datetime.datetime.combine(PLANNER_DAY, datetime.time(), tzinfo=TZ) - datetime.timedelta(days=30)}
        result = engine.run_job(realistic_snapshot(now, 40, settings), engine.what_if, _what_if_data(engine), {})
        assert result.error is None, result.error
        simulations.append(result.value["scenario"])
    assert simulations[0] == simulations[1]


def test_the_plan_is_made_again_when_the_full_charge_becomes_due_or_is_done() -> None:
    engine = started_engine(NOW, 50)
    engine.analysis_db["full_charge"] = {"last": _aware(NOW) - datetime.timedelta(days=6.99)}
    settings = {"full_charge_recommended": 7}
    first = _run(engine, snapshot(NOW, 50, settings), engine.plan_inputs_hash).value
    due = _run(engine, snapshot(NOW + datetime.timedelta(minutes=20), 50, settings), engine.plan_inputs_hash).value
    assert first != due  # same hour, now due
    engine.analysis_db["full_charge"] = {"last": _aware(NOW + datetime.timedelta(minutes=20))}
    done = _run(engine, snapshot(NOW + datetime.timedelta(minutes=20), 50, settings), engine.plan_inputs_hash).value
    assert done not in (first, due)


def test_the_what_if_replay_has_the_full_charge_off() -> None:
    """The replay copies the number, but never the live last full charge: the requirement is off there."""
    replay_class = type("ReplayEngine", (ReplayInputs, BattMindEngine), {})
    engine = replay_class(build_config(OPTIONS), "test_what_if", TEXTS)
    engine.replay_setup()
    snap = snapshot(NOW, 50, {"full_charge_recommended": 7})
    status = _run(engine, snap, engine.full_charge_status).value
    assert not status["due"] and not status["active"]
    assert _run(engine, snap, engine.full_charge_requirement).value is None
    snap.entity_states["sensor.battery_level"] = "100"
    _run(engine, snap, engine.full_charge_observe)
    assert engine.last_full_charge() is None


def test_the_last_full_charge_survives_a_restart_and_an_old_backup_without_it() -> None:
    engine = started_engine(NOW, 100)
    saved = json.loads(json.dumps(encode(engine.analysis_db)))  # what the store and a backup hold
    restarted = BattMindEngine(build_config(OPTIONS), "test", TEXTS)
    result = _run(restarted, snapshot(NOW + datetime.timedelta(hours=1), 50), restarted.startup, {"analysis": decode(saved)})
    assert result.states["last_full_charge"] == _aware(NOW)
    # an old backup has no full charge, a malformed one is dropped: unknown, never "just charged"
    for analysis in ({"hours": {}}, {"full_charge": {"last": "yesterday"}}, {"full_charge": {"last": datetime.datetime(2026, 1, 1)}}):
        old = BattMindEngine(build_config(OPTIONS), "test", TEXTS)
        result = _run(old, snapshot(NOW, 50), old.startup, {"analysis": analysis})
        assert result.states["last_full_charge"] is None
        assert "full_charge" not in old.analysis_db


async def test_last_full_charge_sensor_is_a_timestamp(hass: HomeAssistant, mock_entry) -> None:
    set_source_states(hass, battery_level=100)
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get(f"sensor.{PREFIX}_last_full_charge")
    assert state is not None
    assert state.attributes.get("device_class") == SensorDeviceClass.TIMESTAMP
    assert datetime.datetime.fromisoformat(state.state).tzinfo is not None
    assert hass.states.get(BATTERY_LEVEL).state == "100"
    await async_unload_and_join(hass, mock_entry)
