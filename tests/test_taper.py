"""Charging power that falls with the battery level: the measurement, the caps, and the plan that counts on them."""

from __future__ import annotations

import datetime

import pytest

from custom_components.battmind.const import build_config
from custom_components.battmind.engine import BattMindEngine
from custom_components.battmind.engine.optimizer import OptimizerConfig, OptimizerHour, optimize, step_hour
from custom_components.battmind.utils.efficiency import measure_efficiency
from custom_components.battmind.utils.taper import (
    TAPER_KEEP,
    TAPER_MIN_DAYS,
    TAPER_MIN_MEASUREMENTS,
    add_taper_measurement,
    band_of,
    band_powers,
    charge_energy_kwh,
    level_before_charge,
    taper_caps,
)

from .test_engine import DAY, OPTIONS, TEXTS, TZ, _power_values_db, snapshot
from .test_planner_rules import realistic_snapshot


def fill(database: dict, band: int, watts: float, count: int = 12, days: int = 4, spread: float = 0.0) -> None:
    for index in range(count):
        # ac_kwh / seconds -> watts: one hour at `watts`
        add_taper_measurement(database, band + 1, band + 3, watts * (1.0 + spread * (index % 3 - 1)) / 1000.0, 3600.0, index % days)


# --- measuring -------------------------------------------------------------------------------------------------


def test_a_measurement_is_the_average_power_in_the_band_of_its_middle_level() -> None:
    database: dict = {}
    add_taper_measurement(database, 78.0, 83.0, 0.5, 600.0, 7)  # 0.5 kWh in 10 minutes = 3 kW, middle 80.5 %
    assert database == {"charging": {"80": [[3000, 7]]}}
    assert band_of(100.0) == 90 and band_of(0.0) == 0 and band_of(59.9) == 50


def test_bad_measurements_are_left_out_and_only_the_newest_are_kept() -> None:
    database: dict = {}
    for ac, seconds in ((0.0, 600.0), (-1.0, 600.0), (1.0, 0.0), (float("nan"), 600.0), (1.0, float("inf"))):
        add_taper_measurement(database, 10.0, 12.0, ac, seconds, 1)
    assert database == {}
    for index in range(TAPER_KEEP + 25):
        add_taper_measurement(database, 10.0, 12.0, 1.0, 3600.0, index)
    assert len(database["charging"]["10"]) == TAPER_KEEP and database["charging"]["10"][-1][1] == TAPER_KEEP + 24


def test_measure_efficiency_hands_over_the_level_span_of_each_measurement() -> None:
    """2 % per 4 minutes at 3 kW (a 10 kWh battery): the details carry the levels and the AC kWh the taper is measured from."""
    flow, levels, ts = [(0.0, -3000.0)], [(0.0, 50.0)], 0.0
    for level in (52.0, 54.0, 56.0):
        ts += 240.0
        levels.append((ts, level))
    details: list = []
    measurements, _ = measure_efficiency(flow, levels, 0.0, ts, 1.0, False, 10.0, details)
    assert measurements and len(details) == len(measurements)
    direction, start_level, end_level, ac_kwh, seconds, ended = details[0]
    assert (direction, start_level, end_level, seconds) == ("charging", 52.0, 54.0, 240.0)
    assert ac_kwh == pytest.approx(3.0 * 240.0 / 3600.0) and ended == 480.0
    assert measure_efficiency(flow, levels, 0.0, ts, 1.0, False, 10.0)[0] == measurements  # without details the result is the same


# --- what is learned ---------------------------------------------------------------------------------------------


def test_a_band_is_judged_from_enough_measurements_on_enough_days() -> None:
    database: dict = {}
    fill(database, 50, 4000.0, count=TAPER_MIN_MEASUREMENTS - 1, days=TAPER_MIN_DAYS)
    assert band_powers(database)[50]["judged"] is False
    fill(database, 60, 4000.0, count=TAPER_MIN_MEASUREMENTS, days=TAPER_MIN_DAYS - 1)
    assert band_powers(database)[60]["judged"] is False  # one slow evening is not a pattern
    fill(database, 70, 4000.0, count=TAPER_MIN_MEASUREMENTS, days=TAPER_MIN_DAYS)
    assert band_powers(database)[70]["judged"] is True


def test_a_band_that_charges_clearly_slower_than_the_best_has_a_cap() -> None:
    database: dict = {}
    fill(database, 50, 4000.0)
    fill(database, 80, 3900.0)
    fill(database, 90, 2000.0)
    assert taper_caps(database, 5.0) == ((90, 2.0),)
    # the cap is never above the configured limit
    assert taper_caps(database, 1.5) == ((90, 1.5),)


def test_a_battery_that_is_only_ever_charged_slowly_keeps_the_configured_rate() -> None:
    """The caps are relative to what the same battery showed it can do: slow everywhere is not a taper."""
    database: dict = {}
    for band in (30, 50, 70, 90):
        fill(database, band, 1500.0)
    assert taper_caps(database, 5.0) == ()


def test_one_judged_band_is_not_enough_to_say_anything() -> None:
    database: dict = {}
    fill(database, 90, 2000.0)
    assert taper_caps(database, 5.0) == ()
    assert taper_caps({}, 5.0) == () and taper_caps(None, 5.0) == () and taper_caps({"charging": {"x": "y"}}, 5.0) == ()


def test_slow_sessions_that_are_not_the_battery_do_not_teach_a_cap() -> None:
    """The cap is the 90th percentile of the band: a few solar limited hours don't pull it down."""
    database: dict = {}
    fill(database, 50, 4000.0)
    fill(database, 90, 4000.0, count=18)
    fill(database, 90, 800.0, count=2)  # two hours the sun limited
    assert taper_caps(database, 5.0) == ()


def test_an_hour_is_walked_through_the_bands_at_the_pace_of_each() -> None:
    """A 10 kWh battery (0.1 kWh per %), 4 kW, and the 80 band at 2 kW and the 90 band at 1 kW, no loss."""
    caps = ((80, 2.0), (90, 1.0))

    def energy(level: float, hours: float = 1.0) -> float:
        return charge_energy_kwh(caps, level, hours, 4.0, 0.1, 1.0)

    assert energy(40.0) == pytest.approx(4.0)  # 40 -> 80 at the full rate takes exactly the hour
    assert energy(70.0) == pytest.approx(1.0 + 1.0 + 0.25)  # 1 kWh at 4 kW (15 min), 1 kWh at 2 kW (30 min), 15 min at 1 kW
    assert energy(85.0) == pytest.approx(0.5 + 0.75)  # 5 % at 2 kW (15 min), 45 min at 1 kW
    assert energy(92.0) == pytest.approx(0.8)  # the 8 % left are less than the hour at 1 kW
    assert energy(70.0, 0.5) == pytest.approx(1.5) and energy(100.0) == 0.0
    # never more than the limit, and exactly what it was without caps
    assert charge_energy_kwh(((90, 9.0),), 92.0, 1.0, 4.0, 0.1, 1.0) == pytest.approx(0.8)
    assert charge_energy_kwh((), 95.0, 1.0, 4.0, 0.1, 1.0) == 4.0 and charge_energy_kwh((), 95.0, 0.5, 4.0, 0.1, 1.0) == 2.0
    # a loss means more AC energy per % of level: each band lasts longer, so more of the hour is at the faster bands
    assert charge_energy_kwh(caps, 85.0, 1.0, 4.0, 0.1, 0.5) == pytest.approx(1.0 + 0.5)  # 30 min at 2 kW, 30 min at 1 kW


def test_the_level_an_hour_can_start_at_to_end_at_a_level() -> None:
    caps = ((80, 2.0), (90, 1.0))
    start = level_before_charge(caps, 95.0, 1.0, 4.0, 0.1, 1.0)
    assert start + charge_energy_kwh(caps, start, 1.0, 4.0, 0.1, 1.0) / 0.1 == pytest.approx(95.0, abs=0.01)
    assert start > 95.0 - 40.0  # the taper takes more than the full rate would: it has to start higher
    assert level_before_charge(caps, 30.0, 1.0, 4.0, 0.1, 1.0) == 0.0  # reachable from empty
    assert level_before_charge((), 95.0, 1.0, 4.0, 0.1, 1.0) == pytest.approx(55.0, abs=0.01)


# --- the optimiser ---------------------------------------------------------------------------------------------

CFG = OptimizerConfig(battery_size=10.0, level_min=5.0, level_max=100.0, max_charge=4.0, max_discharge=4.0, efficiency=1.0, wear=0.0)
TAPER = ((80, 2.0), (90, 1.0))


def at(hour: int) -> datetime.datetime:
    return datetime.datetime.combine(DAY, datetime.time(0)) + datetime.timedelta(hours=hour)


def cfg_with(**changes) -> OptimizerConfig:
    return OptimizerConfig(**{**CFG.__dict__, **changes})


def test_an_hour_near_full_charges_less_than_the_charge_rate() -> None:
    hour = OptimizerHour(at(0), 1.0, 0.5, 0.0, 0.0)
    plain = step_hour(hour, 85.0, "grid_charging", CFG)
    tapered = step_hour(hour, 85.0, "grid_charging", cfg_with(charge_taper=TAPER))
    assert plain[3] == pytest.approx(1.5)  # 1.5 kWh fit in the 15 % that are left: the room, not the rate
    assert tapered[3] == pytest.approx(1.25)  # 15 minutes at 2 kW in the 80 band, 45 minutes at 1 kW in the 90 band
    low = step_hour(hour, 40.0, "grid_charging", cfg_with(charge_taper=TAPER))
    assert low[3] == pytest.approx(4.0)  # low in the battery: the full rate


def test_the_cap_follows_the_part_of_the_hour_that_is_left_and_the_solar_surplus() -> None:
    cfg = cfg_with(charge_taper=TAPER)
    half = OptimizerHour(at(0), 1.0, 0.5, 0.0, 0.0, fraction=0.5)
    assert step_hour(half, 70.0, "grid_charging", cfg)[3] == pytest.approx(1.5)  # 15 min at 4 kW to 80 %, then 15 min at 2 kW
    assert step_hour(half, 85.0, "grid_charging", cfg)[3] == pytest.approx(0.5 + 0.25)  # half an hour: 15 min at 2 kW, 15 min at 1 kW
    sun = OptimizerHour(at(0), 1.0, 0.5, 0.0, 6.0)
    details: dict = {}
    step_hour(sun, 85.0, "discharge_allowed", cfg, details=details)
    assert details["solar_in"] == pytest.approx(1.25)  # the sun charges at the battery's pace too: the rest is sold


def test_without_a_taper_the_step_is_what_it_was() -> None:
    hour = OptimizerHour(at(0), 1.0, 0.5, 0.5, 2.0)
    for level in (5.0, 50.0, 85.0, 99.0):
        for action in ("grid_charging", "discharge_allowed", "force_discharge", "force_solar_only_charging", "blocked_solar_charge"):
            assert step_hour(hour, level, action, cfg_with(charge_taper=())) == step_hour(hour, level, action, CFG)


def test_a_plan_that_needs_the_last_percent_charges_in_more_hours_with_a_taper() -> None:
    hours = [OptimizerHour(at(h), 0.5 if h < 5 else 3.0, 0.2, 1.0 if h >= 5 else 0.0, 0.0) for h in range(10)]
    plain = optimize(hours, 60.0, CFG, 2.0)
    capped = optimize([OptimizerHour(at(h), 0.5 if h < 5 else 3.0, 0.2, 1.0 if h >= 5 else 0.0, 0.0) for h in range(10)], 60.0, cfg_with(charge_taper=TAPER), 2.0)
    assert sum(1 for action in capped.actions[:5] if action == "grid_charging") > sum(1 for action in plain.actions[:5] if action == "grid_charging")
    # the plan never counts on more than the battery takes in an hour: what it charges is within the walk through the bands
    previous = 60.0
    for level, grid in zip(capped.levels, capped.grid_kwh):
        assert grid <= charge_energy_kwh(TAPER, previous, 1.0, 4.0, 0.1, 1.0) + 0.01
        previous = level


# --- the engine: what is learned, and from which hours ----------------------------------------------------------

TAPERING = {50: 4000.0, 60: 4000.0, 70: 4000.0, 80: 3800.0, 90: 2000.0}  # W the battery takes in each band


def session(day: int, powers: dict[int, float], hour: int = 2) -> tuple[list, list, float, list[str]]:
    """A charge from 50 to 100 %, 2 % every 4 minutes at the power of the band (negative = charging), `day` days before DAY.

    Returns the rows, the time it ended and the plan check keys of the hours it ran in."""
    ts = datetime.datetime.combine(DAY - datetime.timedelta(days=day), datetime.time(hour), tzinfo=TZ).timestamp()
    first = ts
    flow, levels, level = [], [(ts, 50.0)], 50.0
    while level < 100.0:
        flow.append((ts, -powers[band_of(level)]))
        ts += 240.0
        level += 2.0
        levels.append((ts, level))
    flow.append((ts, 0.0))
    keys = sorted({datetime.datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%dT%H") for t in (first, ts - 1.0)})
    return flow, levels, ts, keys


def measured(engine: BattMindEngine, days: int, powers: dict[int, float], asked: bool = True) -> None:
    """Measure `days` charges (one a day); the hours are marked as hours where the plan asked for grid charging when asked."""
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    snap = snapshot(now, 50.0)
    for day in range(1, days + 1):
        flow, levels, end, keys = session(day, powers)
        for key in keys:
            engine.analysis_db.setdefault("plan_check", {})[key] = {"a": "grid_charging", "ok": True} if asked else {"a": "discharge_allowed", "ok": None}
        assert engine.run_job(snap, engine._taper_measure, flow, levels, flow[0][0], end + 60.0).error is None


def started(options: dict | None = None) -> BattMindEngine:
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    engine = BattMindEngine(build_config({**OPTIONS, **(options or {}), "powerwall_battery_size": 10.0}), "test", TEXTS)
    assert engine.run_job(snapshot(now, 50.0), engine.startup, {"power_values": _power_values_db()}).error is None
    return engine


def test_grid_charging_hours_teach_where_the_battery_tapers() -> None:
    engine = started()
    measured(engine, 5, TAPERING)
    bands = band_powers(engine.analysis_db["taper"])
    assert bands[90]["power"] == pytest.approx(2000.0, abs=20.0) and bands[50]["power"] == pytest.approx(4000.0, abs=40.0)
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    snap = snapshot(now, 50.0)
    assert engine.run_job(snap, engine.charge_taper).value == ((90, pytest.approx(2.0, abs=0.05)),)
    assert 0.7 < engine.run_job(snap, engine.charge_rate_kwh, 92.0).value < 1.0  # the 8 % that are left, at 2 kW
    assert engine.run_job(snap, engine.charge_rate_kwh, 40.0).value == pytest.approx(engine.max_kwh_charging)  # 40 -> 90 is the hour


def test_hours_where_the_plan_did_not_ask_for_a_grid_charge_teach_nothing() -> None:
    """A charge by the sun is as slow as the sun is. The same slow power in hours that were not a grid charge is no taper."""
    engine = started()
    measured(engine, 5, TAPERING, asked=False)
    assert "charging" not in engine.analysis_db.get("taper", {})
    # an hour that was never recorded (a restart) is not accepted either
    engine.analysis_db["plan_check"] = {}
    flow, levels, end, _ = session(2, TAPERING)
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    assert engine.run_job(snapshot(now, 50.0), engine._taper_measure, flow, levels, flow[0][0], end + 60.0).error is None
    assert "charging" not in engine.analysis_db.get("taper", {})


def test_a_battery_charged_by_the_grid_to_70_and_by_the_sun_above_does_not_get_the_suns_pace_as_a_taper() -> None:
    """The common summer day: grid charging at full rate to 80 %, then the sun fills the rest at whatever it gives."""
    engine = started()
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    snap = snapshot(now, 50.0)
    for day in range(1, 7):
        flow, levels, end, keys = session(day, {50: 4000.0, 60: 4000.0, 70: 4000.0, 80: 1200.0, 90: 1200.0})
        # the plan asked for grid charging only while the level was below 80 % (the first hour of the session)
        grid_hour = datetime.datetime.fromtimestamp(flow[0][0], TZ).strftime("%Y-%m-%dT%H")
        engine.analysis_db.setdefault("plan_check", {})[grid_hour] = {"a": "grid_charging", "ok": True}
        sun_keys = [key for key in keys if key != grid_hour]
        for key in sun_keys:
            engine.analysis_db["plan_check"][key] = {"a": "discharge_allowed", "ok": None}
        # one hour long: split the session at the hour boundary the way real hours are (02 = grid, 03 = sun)
        boundary = datetime.datetime.combine(DAY - datetime.timedelta(days=day), datetime.time(3), tzinfo=TZ).timestamp()
        grid_levels = [row for row in levels if row[0] < boundary]
        sun_levels = [row for row in levels if row[0] >= boundary]
        grid_flow = [row for row in flow if row[0] < boundary]
        sun_flow = [row for row in flow if row[0] >= boundary]
        assert engine.run_job(snap, engine._taper_measure, grid_flow, grid_levels, flow[0][0], boundary).error is None
        assert engine.run_job(snap, engine._taper_measure, sun_flow or flow[-1:], sun_levels, boundary, end + 60.0).error is None
    caps = engine.run_job(snap, engine.charge_taper).value
    assert caps == (), f"the sun's pace was learned as a taper: {caps}"


def test_the_hourly_recording_measures_once_from_the_buffers_and_not_again() -> None:
    """hour_end -> _analysis_record_taper: the buffers of the battery power and level, from where the last measuring ended."""
    engine = started()
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    snap = snapshot(now, 50.0)
    flow, levels, end, keys = session(1, TAPERING)
    snap.buffers = {"sensor.battery_power": flow, "sensor.battery_level": levels}
    for key in keys:
        engine.analysis_db.setdefault("plan_check", {})[key] = {"a": "grid_charging", "ok": True}
    hour = datetime.datetime.combine(DAY, datetime.time(10))
    assert engine.run_job(snap, engine._analysis_record_taper, hour).error is None
    first = {band: len(rows) for band, rows in engine.analysis_db["taper"]["charging"].items()}
    assert first and engine.analysis_db["taper"]["until"] > 0
    assert engine.run_job(snap, engine._analysis_record_taper, hour).error is None
    assert {band: len(rows) for band, rows in engine.analysis_db["taper"]["charging"].items()} == first


@pytest.mark.parametrize(
    "entry",
    [
        {"a": "grid_charging", "ok": None, "skip": "unproven"},  # the sun could have charged that much
        {"a": "grid_charging", "ok": None, "skip": "changed"},  # grid charging held less than 3/4 of the hour
        {"a": "grid_charging", "ok": None, "skip": "partial"},
        {"a": "grid_charging", "ok": None, "skip": "twice"},
        {"a": "grid_charging", "ok": None, "skip": "limit"},
        {"a": "grid_charging", "ok": False},  # the automation did not charge
        {"a": "grid_charging", "ok": None, "skip": "test"},
        {"a": "force_solar_only_charging", "ok": True},
    ],
)
def test_only_hours_that_proved_a_grid_charge_teach(entry) -> None:
    engine = started()
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    flow, levels, end, keys = session(1, TAPERING)
    for key in keys:
        engine.analysis_db.setdefault("plan_check", {})[key] = dict(entry)
    assert engine.run_job(snapshot(now, 50.0), engine._taper_measure, flow, levels, flow[0][0], end + 60.0).error is None
    assert "charging" not in engine.analysis_db.get("taper", {})


def test_a_measurement_that_began_in_an_hour_that_did_not_prove_a_grid_charge_is_not_taken() -> None:
    engine = started()
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    flow, levels, end, keys = session(1, TAPERING)
    assert len(keys) == 2
    engine.analysis_db["plan_check"] = {keys[1]: {"a": "grid_charging", "ok": True}, keys[0]: {"a": "discharge_allowed", "ok": None}}
    assert engine.run_job(snapshot(now, 50.0), engine._taper_measure, flow, levels, flow[0][0], end + 60.0).error is None
    bands = engine.analysis_db.get("taper", {}).get("charging", {})
    boundary = datetime.datetime.combine(DAY - datetime.timedelta(days=1), datetime.time(3), tzinfo=TZ).timestamp()
    # only what lies wholly in the second hour (from 03:00): levels 80 % and up in this session
    assert bands and all(int(band) >= 80 for band in bands), bands
    assert boundary > 0


def test_a_night_charge_across_midnight_utc_is_one_day() -> None:
    """The days are local days: 01:00-03:00 in Denmark is on both sides of 00:00 UTC in winter, and is one night."""
    engine = started()
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    flow, levels, end, keys = session(1, TAPERING, hour=0)  # 00:00 local = 22:00 UTC the day before (summer time)
    for key in keys:
        engine.analysis_db.setdefault("plan_check", {})[key] = {"a": "grid_charging", "ok": True}
    assert engine.run_job(snapshot(now, 50.0), engine._taper_measure, flow, levels, flow[0][0], end + 60.0).error is None
    days = {row[1] for rows in engine.analysis_db["taper"]["charging"].values() for row in rows}
    assert len(days) == 1


def test_a_cut_charge_hour_keeps_the_totals_right_also_when_a_session_goes_altogether() -> None:
    from types import SimpleNamespace

    engine = started()
    hour = datetime.datetime.combine(DAY, datetime.time(3))
    quarters = [hour + datetime.timedelta(minutes=15 * i) for i in range(4)]
    charge_hours = {ts: {"Cost": 1.0, "kWh": 1.0, "battery_level": 9.5, "Price": 1.0, "reason": "<details>x</details>"} for ts in quarters}
    day = {"charging_sessions": dict(charge_hours), "total_cost": -2.0}  # a negative total (negative prices) must stay what the sums say
    ctx = SimpleNamespace(
        charge_hours=charge_hours, charging_plan={0: day}, current_hour=datetime.datetime.combine(DAY, datetime.time(0)),
        totals=[4.0, 4.0], r=lambda key, **fmt: f"{key}:{fmt}",
    )
    engine.rule_taper = {hour: 0.2}  # 0.2 kWh of 4: 0.05 per period, under 0.1 each: all four go
    assert engine.apply_taper_to_rules(ctx) is True
    assert ctx.charge_hours == {} and day["charging_sessions"] == {}
    assert ctx.totals == [0.0, 0.0] and day["total_cost"] == pytest.approx(-6.0)
    ctx.charge_hours = {ts: {"Cost": 1.0, "kWh": 1.0, "battery_level": 9.5, "Price": 1.0, "reason": "<details>x</details>"} for ts in quarters}
    day.update(charging_sessions=dict(ctx.charge_hours), total_cost=4.0)
    ctx.totals[:] = [4.0, 4.0]
    engine.rule_taper = {hour: 2.0}  # half of it: 0.5 kWh per period
    assert engine.apply_taper_to_rules(ctx) is True
    assert [session["kWh"] for session in ctx.charge_hours.values()] == [0.5] * 4
    assert ctx.totals == [2.0, 2.0] and day["total_cost"] == pytest.approx(2.0)
    assert all("taper_limited" in session["reason"] for session in ctx.charge_hours.values())


def test_the_switch_decides_whether_the_plan_counts_on_it() -> None:
    engine = started()
    measured(engine, 5, TAPERING)
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    assert engine.run_job(snapshot(now, 50.0, {"learn_charge_taper": True}), engine.charge_taper).value
    assert engine.run_job(snapshot(now, 50.0, {"learn_charge_taper": False}), engine.charge_taper).value == ()  # the learning goes on, the plan ignores it
    assert engine.analysis_db["taper"]["charging"]  # (it is still there)


def test_the_caps_are_part_of_what_replans_the_plan_and_the_replay_never_has_them() -> None:
    engine = started()
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    snap = snapshot(now, 50.0)
    before = engine.run_job(snap, engine.plan_inputs_hash).value
    measured(engine, 5, TAPERING)
    assert engine.run_job(snap, engine.plan_inputs_hash).value != before
    # a replay engine (what-if) has empty databases: its optimiser config has no taper
    replay = started()
    assert replay.run_job(snap, lambda: replay.optimizer_config(5.0, 5.0, 0.95).charge_taper).value == ()


def plan_sessions(engine: BattMindEngine) -> list[tuple[datetime.datetime, float]]:
    return sorted((ts, session["kWh"]) for ts, session in engine.charge_hours.items() if isinstance(ts, datetime.datetime) and isinstance(session, dict))


@pytest.mark.parametrize("controls", [True, False], ids=["optimiser", "rules"])
def test_no_planned_charge_is_more_than_the_battery_takes_whoever_plans_it(controls: bool) -> None:
    """The optimiser and the rules count on the same energy per hour: what the plan that controls the battery asks of an hour is never
    more than the battery takes from the level that plan has when the hour starts (the levels of the shadow plan, one model)."""
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    engine = started()
    measured(engine, 5, TAPERING)
    assert engine.run_job(realistic_snapshot(now, 60.0, {"optimizer_control": controls}), engine.charge_if_needed, True).error is None
    snap = snapshot(now, 50.0)
    caps = engine.run_job(snap, engine.charge_taper).value
    efficiency = engine.run_job(snap, engine.battery_efficiency).value
    assert caps
    level_key = "battery_level" if controls else "rules_battery_level"
    planned: dict[datetime.datetime, float] = {}
    for ts, session in engine.charge_hours.items():
        if isinstance(ts, datetime.datetime) and isinstance(session, dict):
            planned[ts.replace(minute=0)] = planned.get(ts.replace(minute=0), 0.0) + session["kWh"]
    rows = engine.optimizer_shadow["hours"]
    previous, checked = 60.0, 0
    for row in rows:
        kwh = planned.get(row["hour"], 0.0)
        if kwh and controls and row["hour"] > now.replace(minute=0):  # the hour that is going on only has part of its hour left
            limit = charge_energy_kwh(caps, previous, 1.0, engine.max_kwh_charging, 0.1, efficiency)
            assert kwh <= limit + 0.03, (row["hour"], kwh, limit, previous)
            checked += 1
        previous = row[level_key]
    assert max(planned.values()) <= engine.max_kwh_charging + 1e-6
    reasons = [item["reason"] for day in engine.plan.values() if isinstance(day, dict) for item in day["charging_sessions"].values()]
    cut = [reason for reason in reasons if "Begrænset til" in reason]
    if controls:
        assert checked and not cut  # the optimiser plans within the taper from the start: nothing to cut
    else:
        # the rules plan in the order of the prices: hours planned at a level that a charge before them has since raised are cut to what
        # the battery takes, and the plan says so
        assert cut, "the rules' plan has hours that were cut to the taper"


def test_the_hours_a_taper_cuts_are_the_ones_the_battery_cannot_take_not_the_ones_that_are_full() -> None:
    from custom_components.battmind.engine.optimizer import simulate, taper_cuts

    cfg = cfg_with(charge_taper=TAPER, efficiency=1.0)
    hours = [OptimizerHour(at(h), 1.0, 0.5, 0.0, 0.0) for h in range(3)]
    actions, limits = ["grid_charging"] * 3, [None, None, None]
    result = simulate(hours, 85.0, cfg, actions, 0.0, limits)
    assert taper_cuts(hours, 85.0, cfg, actions, limits, result) == {0: pytest.approx(1.25)}  # the battery takes 1.25 of the 4 kWh asked for
    # without a taper nothing is cut, and an hour that only the room limits (the battery is nearly full anyway) is not a taper's doing
    plain = cfg_with(efficiency=1.0)
    assert taper_cuts(hours, 85.0, plain, actions, limits, simulate(hours, 85.0, plain, actions, 0.0, limits)) == {}
    assert 1 not in taper_cuts(hours, 85.0, cfg, actions, limits, result) and 2 not in taper_cuts(hours, 85.0, cfg, actions, limits, result)


def test_a_block_of_solar_charging_that_the_later_sun_can_refill_without_a_taper_is_refused_with_one() -> None:
    from custom_components.battmind.engine.optimizer import _allowed, _later

    hours = [OptimizerHour(at(h), 3.0 if h == 12 else 2.0, 2.0, 0.5, 3.0) for h in range(10, 13)]
    plain = cfg_with(efficiency=1.0)
    tapered = cfg_with(efficiency=1.0, charge_taper=((90, 1.0),))
    assert _later(hours, plain)[0].solar == pytest.approx(6.0)
    assert _later(hours, tapered)[0].solar == pytest.approx(2.0)  # the sun can't refill faster than the slowest band takes
    # 80 % in the battery: 2 kWh of room. The later sun (6 kWh) refills it, so selling the sun now is a safe bet - until the battery tapers
    assert _allowed(hours[0], 80.0, "blocked_solar_charge", plain, 0.0, _later(hours, plain)[0]) is True
    assert _allowed(hours[0], 80.0, "blocked_solar_charge", tapered, 0.0, _later(hours, tapered)[0]) is False
