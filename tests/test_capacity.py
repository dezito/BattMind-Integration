"""The measured battery capacity bounds (utils/capacity.py), and the size the plan counts on (engine/battery.py)."""
from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest

from custom_components.battmind.utils.capacity import (
    CAPACITY_KEEP,
    CAPACITY_MIN_EPISODES,
    CAPACITY_OPEN_MAX_AGE,
    add_capacity_measurements,
    capacity_summary,
    clean_capacity,
    used_capacity,
)
from custom_components.battmind.utils.efficiency import add_efficiency_measurement, measure_efficiency

from .test_efficiency import _battery
from .test_engine import DAY, TZ, charge_hours, plan, snapshot, started_engine

START = 1_000_000.0


def _cycles(count: int, size: float = 10.0, charging: float = 0.9, discharging: float = 0.95) -> tuple[list, list]:
    """count cycles of 2 h charging at 3 kW AC and 2.5 h discharging at 2 kW AC with an idle pause between them (sensor + = charging)."""
    phases = []
    for _ in range(count):
        phases += [(2 * 3600, 3000, charging), (600, 0, 1.0), (int(2.5 * 3600), -2000, discharging), (600, 0, 1.0)]
    return _battery(phases, level=10.0, size=size, start=START)


def _measure(flow: list, levels: list) -> tuple[list, float]:
    details: list = []
    # the plausibility check of a measurement uses the configured size: 10 kWh
    _, until = measure_efficiency(flow, levels, 0.0, levels[-1][0] + 3600, 1.0, True, 10.0, details)
    return details, until


def _database(details: list, now: float | None = None) -> dict:
    database = clean_capacity(None)
    add_capacity_measurements(database, details, (details[-1][5] if details else 0.0) + 10 * CAPACITY_OPEN_MAX_AGE if now is None else now)
    return database


def _row(direction: str, start_level: float, end_level: float, started: float, seconds: float = 600.0, ac: float | None = None) -> tuple:
    """A measurement like measure_efficiency's details: 100 Wh AC per point unless ac is given."""
    return (direction, start_level, end_level, abs(end_level - start_level) * 0.1 if ac is None else ac, seconds, started + seconds)


def _run(direction: str, first: float, points: int, started: float, step: int = 5) -> tuple[list, float]:
    """Unbroken measurements of `step` points each: (rows, time it ended). Charging rises, discharging falls."""
    rows, level, time = [], first, started
    for _ in range(points // step):
        end = level + step if direction == "charging" else level - step
        rows.append(_row(direction, level, end, time))
        level, time = end, time + 600.0
    return rows, time


def test_both_bounds_come_from_the_battery_power_and_level_and_are_shown_apart() -> None:
    details, _ = _measure(*_cycles(3))
    summary = capacity_summary(_database(details), 10.0)

    # AC kWh per 100 %: size / charging efficiency (an upper bound) and size x discharging efficiency (a lower bound)
    assert summary["charging"]["episodes"] == summary["discharging"]["episodes"] == 3
    assert summary["charging"]["kwh"] == pytest.approx(10.0 / 0.9, rel=0.01)
    assert summary["discharging"]["kwh"] == pytest.approx(10.0 * 0.95, rel=0.01)
    assert summary["discharging"]["kwh"] <= summary["charging"]["kwh"]
    assert summary["round_trip"] == pytest.approx(0.9 * 0.95, rel=0.01)  # the one figure free of the capacity
    assert summary["status"] == "within"


def test_a_smaller_battery_is_seen_in_the_charging_bound_whatever_the_efficiency_is() -> None:
    details, _ = _measure(*_cycles(3, size=8.0))

    summary = capacity_summary(_database(details), 10.0)

    assert summary["charging"]["kwh"] == pytest.approx(8.0 / 0.9, rel=0.01)  # 8.9 kWh is below the configured 10 kWh
    assert summary["status"] == "below"


def test_a_larger_battery_is_seen_in_the_discharging_bound() -> None:
    details, _ = _measure(*_cycles(3, size=12.0))

    summary = capacity_summary(_database(details), 10.0)

    assert summary["discharging"]["kwh"] == pytest.approx(12.0 * 0.95, rel=0.01)
    assert summary["status"] == "above"


def test_a_difference_inside_the_noise_says_nothing() -> None:
    # 1 % level steps over 40 points: a bound less than 5 % from the configured capacity is no evidence
    details, _ = _measure(*_cycles(3, size=9.7, charging=1.0, discharging=1.0))

    assert capacity_summary(_database(details), 10.0)["status"] == "within"


def test_fewer_than_three_episodes_show_no_bound() -> None:
    details, _ = _measure(*_cycles(CAPACITY_MIN_EPISODES - 1))

    summary = capacity_summary(_database(details), 10.0)

    # the runs are counted and their spread is shown, but no bound before enough runs
    assert summary["charging"]["episodes"] == 2 and summary["charging"]["kwh"] is None
    assert summary["charging"]["min"] == pytest.approx(10.0 / 0.9, rel=0.01) == pytest.approx(summary["charging"]["max"], rel=0.01)
    assert summary["discharging"]["kwh"] is None
    assert summary["round_trip"] is None
    assert summary["status"] == "too_few"


def test_less_than_40_points_is_no_episode() -> None:
    rows, _ = _run("charging", 20.0, 35, START)  # 35 points

    assert capacity_summary(_database(rows), 10.0)["charging"]["episodes"] == 0
    rows, _ = _run("charging", 20.0, 40, START)
    assert capacity_summary(_database(rows), 10.0)["charging"]["episodes"] == 1


def test_a_reversal_ends_the_run() -> None:
    first, time = _run("charging", 20.0, 25, START)
    back, time = _run("discharging", 45.0, 5, time)
    second, _ = _run("charging", 40.0, 25, time)

    # 25 + 25 charging points would be an episode only if nothing came between them
    assert capacity_summary(_database(first + back + second), 10.0)["charging"]["episodes"] == 0
    joined, _ = _run("charging", 20.0, 50, START)
    assert capacity_summary(_database(joined), 10.0)["charging"]["episodes"] == 1


def test_a_gap_ends_the_run() -> None:
    first, time = _run("charging", 20.0, 25, START)
    second, _ = _run("charging", 45.0, 25, time + 60.0)  # the measurement in between was rejected: it does not continue the run

    assert capacity_summary(_database(first + second), 10.0)["charging"]["episodes"] == 0


def test_a_jump_of_the_level_is_not_a_measurement() -> None:
    first, time = _run("charging", 20.0, 25, START)
    jump = [_row("charging", 45.0, 65.0, time, ac=0.2)]  # a calibration of the battery level: 20 points for 0.2 kWh
    second, _ = _run("charging", 65.0, 25, time + 600.0)

    database = _database(first + jump + second)

    # neither side of the jump is 40 points, and the jump's own energy is in no episode
    assert capacity_summary(database, 10.0)["charging"]["episodes"] == 0
    long, _ = _run("charging", 20.0, 40, START + 10_000)
    database = _database(first + jump + second + long)
    assert database["episodes"]["charging"][0]["ac"] == pytest.approx(4.0)


def test_the_gap_between_two_measurements_may_be_a_second() -> None:
    first, time = _run("charging", 20.0, 25, START)
    touching, _ = _run("charging", 45.0, 25, time + 0.5)
    apart, _ = _run("charging", 45.0, 25, time + 1.5)

    assert capacity_summary(_database(first + touching), 10.0)["charging"]["episodes"] == 1
    assert capacity_summary(_database(first + apart), 10.0)["charging"]["episodes"] == 0


def test_a_run_longer_than_a_day_is_closed_and_a_new_one_started() -> None:
    rows, time = [], START
    level = 10.0
    for _ in range(50):  # 50 steps of 2 points, 1 hour each: 50 hours and 100 points
        rows.append(_row("charging", level, level + 2.0, time, seconds=3600.0))
        level, time = level + 2.0, time + 3600.0

    database = _database(rows)

    assert [round(episode["l1"] - episode["l0"]) for episode in database["episodes"]["charging"]] == [48, 48]  # 24 h each; the 4 points left are no episode


def test_a_long_run_before_a_jump_is_kept() -> None:
    run, time = _run("charging", 20.0, 45, START)
    jump = [_row("charging", 65.0, 95.0, time, ac=0.2)]

    database = _database(run + jump)

    assert len(database["episodes"]["charging"]) == 1
    assert database["episodes"]["charging"][0]["l1"] == 65.0 and database["episodes"]["charging"][0]["ac"] == pytest.approx(4.5)


def test_a_run_survives_a_restart_in_the_middle() -> None:
    rows, _ = _run("charging", 20.0, 50, START)
    database = clean_capacity(None)
    add_capacity_measurements(database, rows[:5], rows[4][5])
    assert database["open"] is not None and not database["episodes"]["charging"]

    database = clean_capacity(json.loads(json.dumps(database)))  # saved and loaded
    add_capacity_measurements(database, rows[5:], rows[-1][5] + 10 * CAPACITY_OPEN_MAX_AGE)

    assert len(database["episodes"]["charging"]) == 1
    assert database["episodes"]["charging"][0]["l0"] == 20.0 and database["episodes"]["charging"][0]["l1"] == 70.0


def test_an_open_run_is_closed_when_nothing_continues_it() -> None:
    rows, _ = _run("charging", 20.0, 45, START)
    database = clean_capacity(None)

    add_capacity_measurements(database, rows, rows[-1][5] + CAPACITY_OPEN_MAX_AGE - 1)
    assert database["open"] is not None  # may still be continued by the next hour
    assert capacity_summary(database, 10.0)["charging"]["episodes"] == 0

    add_capacity_measurements(database, [], rows[-1][5] + CAPACITY_OPEN_MAX_AGE + 1)
    assert database["open"] is None
    assert capacity_summary(database, 10.0)["charging"]["episodes"] == 1


def test_only_the_newest_episodes_are_kept() -> None:
    rows: list = []
    time = START
    for _ in range(CAPACITY_KEEP + 5):
        run, time = _run("charging", 20.0, 40, time)
        rows += run
        time += 3600.0

    assert len(_database(rows)["episodes"]["charging"]) == CAPACITY_KEEP


def test_more_energy_out_than_in_per_point_is_flagged() -> None:
    charge, time = _run("charging", 20.0, 40, START)
    discharge = [_row("discharging", 60.0 - 5 * i, 55.0 - 5 * i, time + 3600 + 600 * i, ac=1.0) for i in range(8)]
    database = clean_capacity(None)
    for _ in range(CAPACITY_MIN_EPISODES):
        add_capacity_measurements(database, charge, time)
        add_capacity_measurements(database, discharge, time + 10 * CAPACITY_OPEN_MAX_AGE)

    assert capacity_summary(database, 10.0)["status"] == "inconsistent"


def test_malformed_stored_data_is_dropped() -> None:
    good = {"l0": 10.0, "l1": 60.0, "t0": 1.0, "t1": 2.0, "ac": 5.0}
    stored = {
        "episodes": {"charging": [good, {"l0": "x"}, 5, {**good, "ac": -1.0}, {**good, "t1": 0.0}, {**good, "l0": float("nan")}], "discharging": "no"},
        "open": {**good, "d": "sideways"},
        "until": "never",
    }

    assert clean_capacity(stored) == {"episodes": {"charging": [good], "discharging": []}, "open": None}
    assert clean_capacity("garbage") == {"episodes": {"charging": [], "discharging": []}, "open": None}
    assert clean_capacity({"open": {**good, "d": "charging"}, "until": 5.0}) == {
        "episodes": {"charging": [], "discharging": []}, "open": {**good, "d": "charging"}, "until": 5.0,
    }
    assert capacity_summary(None, 10.0)["status"] == "too_few"
    assert capacity_summary({}, 0.0)["configured"] is None
    # a number that does not fit a float is no number
    huge = 10**400
    assert clean_capacity({"until": huge, "episodes": {"charging": [{**good, "ac": huge}]}}) == {"episodes": {"charging": [], "discharging": []}, "open": None}
    # a closed episode covers 40 points and moved the way of its direction
    short, wrong_way = {**good, "l1": 30.0}, {**good, "l0": 60.0, "l1": 10.0}
    assert clean_capacity({"episodes": {"charging": [short, wrong_way], "discharging": [good]}})["episodes"] == {"charging": [], "discharging": []}


# --- in the engine ------------------------------------------------------------------------------------------------------------


def _engine_with_cycles(count: int = 3, size: float = 10.0):
    start = datetime.datetime.combine(DAY, datetime.time(1, 0), tzinfo=TZ).timestamp()
    phases = []
    for _ in range(count):
        phases += [(2 * 3600, 3000, 0.9), (600, 0, 1.0), (int(2.5 * 3600), -2000, 0.95), (600, 0, 1.0)]
    flow, levels = _battery(phases, level=10.0, size=size, sign=-1.0, start=start)  # sensor.battery_power: + = discharging
    now = datetime.datetime.combine(DAY, datetime.time(20, 59))
    engine = started_engine(now, levels[-1][1])
    snap = snapshot(now, levels[-1][1])
    snap.buffers = {"sensor.battery_power": flow, "sensor.battery_level": levels}
    snap.units["sensor.battery_level"] = "%"
    return engine, now, snap


def test_the_capacity_is_measured_in_the_hour_end_and_published() -> None:
    engine, now, snap = _engine_with_cycles()

    result = engine.run_job(snap, engine.hour_end, now.replace(minute=0))

    assert result.error is None, result.error
    published = result.attributes["analysis"]["battery_capacity"]
    assert published["configured"] == 10.0
    assert published["charging"]["episodes"] == published["discharging"]["episodes"] == 3
    assert published["status"] == "within"
    # always measured: the switch (off by default) only decides whether the plan counts on it, and here it does not
    assert published["enabled"] is False and published["used"] == {"kwh": 10.0, "source": "configured"}
    assert published["charging"]["max"] == pytest.approx(10.0 / 0.9, rel=0.01)
    assert engine.analysis_db["capacity"]["until"] > 0

    # the next hour continues where the last one stopped: nothing is counted twice
    before = json.loads(json.dumps(engine.analysis_db["capacity"]["episodes"]))
    later = snapshot(now.replace(hour=21), 50)
    later.buffers = snap.buffers
    assert engine.run_job(later, engine._analysis_record_capacity, now.replace(hour=21, minute=0)).error is None
    assert json.loads(json.dumps(engine.analysis_db["capacity"]["episodes"])) == before


def test_the_published_capacity_is_empty_without_measurements() -> None:
    now = datetime.datetime.combine(DAY, datetime.time(0, 5))
    engine = started_engine(now, 50.0)

    result = engine.run_job(snapshot(now, 50.0), engine.publish_analysis)

    published = result.attributes["analysis"]["battery_capacity"]
    assert published["status"] == "too_few"
    assert published["charging"]["episodes"] == published["discharging"]["episodes"] == 0
    assert published["round_trip"] is None


def test_a_broken_capacity_entry_does_not_stop_the_hour() -> None:
    engine, now, snap = _engine_with_cycles()
    engine.load_analysis_db({"capacity": {"episodes": "broken", "open": 7, "until": None, "sensors": 3}})  # as a restored backup may hold it
    assert engine.analysis_db["capacity"] == {"episodes": {"charging": [], "discharging": []}, "open": None}

    result = engine.run_job(snap, engine.hour_end, now.replace(minute=0))

    assert result.error is None, result.error
    published = result.attributes["analysis"]["battery_capacity"]
    assert published["charging"]["episodes"] == published["discharging"]["episodes"] == 3
    assert published["status"] == "within"


def test_a_run_left_open_at_one_hour_end_is_continued_in_the_next() -> None:
    reference, now, snap = _engine_with_cycles(count=1)
    assert reference.run_job(snap, reference.hour_end, now.replace(minute=0)).error is None
    assert len(reference.analysis_db["capacity"]["episodes"]["charging"]) == len(reference.analysis_db["capacity"]["episodes"]["discharging"]) == 1

    engine, now, snap = _engine_with_cycles(count=1)
    for hour in range(1, 9):  # the 2 h charge from 01:00 is open at the end of hours 01 and 02 and continued by the next one
        at = snapshot(now.replace(hour=hour, minute=59), 50)
        at.buffers = snap.buffers
        at.units["sensor.battery_level"] = "%"
        assert engine.run_job(at, engine.hour_end, now.replace(hour=hour, minute=0)).error is None
        if hour in (1, 2):
            assert engine.analysis_db["capacity"]["open"] is not None
            assert engine.analysis_db["capacity"]["open"]["d"] == "charging"

    # the same runs as in one go, with the same energy: nothing lost at the hour ends and nothing counted twice
    assert engine.analysis_db["capacity"]["episodes"] == reference.analysis_db["capacity"]["episodes"]
    assert engine.analysis_db["capacity"]["open"] is None


def test_another_sign_of_the_battery_power_starts_the_measuring_again() -> None:
    engine, now, snap = _engine_with_cycles()
    assert engine.run_job(snap, engine.hour_end, now.replace(minute=0)).error is None
    assert len(engine.analysis_db["capacity"]["episodes"]["charging"]) == 3

    engine.config["home"]["invert_powerwall_watt_flow_entity_id"] = not engine.config["home"]["invert_powerwall_watt_flow_entity_id"]
    later = snapshot(now.replace(hour=21, minute=59), 50)
    later.buffers = snap.buffers
    assert engine.run_job(later, engine.hour_end, now.replace(hour=21, minute=0)).error is None

    # what was measured with the other sign is gone, and with this sign the same buffers give no measurement at all
    assert engine.analysis_db["capacity"]["episodes"] == {"charging": [], "discharging": []}


def test_a_time_measured_until_in_the_future_does_not_stop_the_measuring() -> None:
    engine, now, snap = _engine_with_cycles()
    assert engine.run_job(snap, engine.hour_end, now.replace(minute=0)).error is None
    engine.analysis_db["capacity"]["until"] = 9_999_999_999.0  # a clock that jumped, a restored backup

    later = snapshot(now.replace(hour=21, minute=59), 50)
    later.buffers = snap.buffers
    assert engine.run_job(later, engine.hour_end, now.replace(hour=21, minute=0)).error is None

    assert engine.analysis_db["capacity"]["until"] <= datetime.datetime.combine(DAY, datetime.time(22, 0), tzinfo=TZ).timestamp()


def test_the_plan_is_the_same_with_and_without_capacity_measurements_while_the_switch_is_off() -> None:
    """Off (the default): the measuring goes on, but the optimiser and the rules count on the configured size."""
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    plain = started_engine(now, 40.0)
    measured = started_engine(now, 40.0)
    # an episode that would say 6 kWh of 10 (a big degradation) if anything read it
    measured.analysis_db["capacity"] = clean_capacity({
        "episodes": {
            "charging": [{"l0": 10.0, "l1": 60.0, "t0": 1.0, "t1": 2.0, "ac": 3.0}] * 5,
            "discharging": [{"l0": 60.0, "l1": 10.0, "t0": 3.0, "t1": 4.0, "ac": 2.5}] * 5,
        },
    })
    assert capacity_summary(measured.analysis_db["capacity"], 10.0)["status"] == "below"

    first = plan(plain, now, 40.0)
    second = plan(measured, now, 40.0)
    assert charge_hours(plain) and first.states["powerwall_action"]  # a plan with something in it, not two empty ones

    assert second.states == first.states
    assert {key: value for key, value in second.attributes.items() if key != "analysis"} == {
        key: value for key, value in first.attributes.items() if key != "analysis"
    }
    assert measured.charge_hours == plain.charge_hours


# --- the size the plan counts on ----------------------------------------------------------------------------------------------


def _measured(upper: float, lower: float, count: int = 5) -> dict:
    """Runs of 50 points whose AC kWh per 100 % are upper (charging) and lower (discharging)."""
    return clean_capacity({
        "episodes": {
            "charging": [{"l0": 10.0, "l1": 60.0, "t0": 1.0 + i, "t1": 2.0 + i, "ac": upper / 2} for i in range(count)],
            "discharging": [{"l0": 60.0, "l1": 10.0, "t0": 1.0 + i, "t1": 2.0 + i, "ac": lower / 2} for i in range(count)],
        },
    })


def _sized(switch: bool, upper: float | None = 8.89, lower: float = 7.6, **solar):
    """An engine (10 kWh configured, a fixed round trip loss of 10 %) with measured bounds, and a snapshot with the switch."""
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    engine = started_engine(now, 40.0)
    engine.config["solar"]["powerwall_charge_discharge_loss"] = 0.1
    engine.config["solar"].update(solar)
    if upper is not None:
        engine.analysis_db["capacity"] = _measured(upper, lower)
    return engine, snapshot(now, 40.0, {"learn_battery_capacity": switch})


def _used(engine, snap) -> tuple:
    return engine.run_job(snap, engine.battery_size_used).value


def _model(engine, snap) -> tuple[float, float]:
    """(level points per AC kWh charged, AC kWh per level point discharged) as the plan counts them."""
    percent = engine.run_job(snap, engine.kwh_to_percentage, 1.0).value
    kwh = engine.run_job(snap, engine.percentage_to_kwh, 1.0).value
    return percent * engine.run_job(snap, engine.charging_efficiency).value, kwh * engine.run_job(snap, engine.discharging_efficiency).value


def _summary(upper: float, lower: float) -> dict:
    return capacity_summary(_measured(upper, lower), 10.0)


@pytest.mark.parametrize(
    ("upper", "lower", "expected"),
    [
        (8.89, 7.6, 8.8),     # below: the charging bound rounded DOWN to 0.1 kWh, never past it
        (8.8, 7.6, 8.8),
        (9.4, 7.6, 9.4),
        (9.5, 7.6, None),     # within 5 %: no change
        (11.0, 9.0, None),    # above or within: never more than the configured size
        (4.9, 4.0, None),     # under half of the configured size: not believed
        (5.0, 4.0, 5.0),
        (8.0, 9.0, None),     # inconsistent: more out than in
    ],
)
def test_used_capacity(upper: float, lower: float, expected: float | None) -> None:
    assert used_capacity(_summary(upper, lower), 10.0) == expected


def test_used_capacity_needs_enough_runs_and_a_configured_size() -> None:
    assert used_capacity(capacity_summary(_measured(8.0, 7.0, count=2), 10.0), 10.0) is None
    assert used_capacity(capacity_summary(None, 10.0), 10.0) is None
    assert used_capacity(_summary(8.0, 7.0), 0.0) is None


def test_the_plan_counts_on_the_configured_size_while_the_switch_is_off() -> None:
    engine, snap = _sized(False)

    assert _used(engine, snap) == (10.0, "configured")
    assert engine.run_job(snap, engine.percentage_to_kwh, 50.0).value == pytest.approx(5.0)


def test_the_plan_counts_on_the_measured_size_when_the_switch_is_on_and_the_runs_say_smaller() -> None:
    engine, snap = _sized(True)

    assert _used(engine, snap) == (pytest.approx(8.8), "measured")
    assert engine.run_job(snap, engine.percentage_to_kwh, 50.0).value == pytest.approx(4.4)
    assert engine.run_job(snap, engine.kwh_to_percentage, 4.4).value == pytest.approx(50.0)


@pytest.mark.parametrize(("upper", "lower"), [(9.6, 8.5), (11.0, 9.8), (8.0, 9.0)])
def test_the_plan_keeps_the_configured_size_unless_the_runs_say_smaller(upper: float, lower: float) -> None:
    engine, snap = _sized(True, upper, lower)  # within, above, inconsistent

    assert _used(engine, snap) == (10.0, "configured")
    engine, snap = _sized(True, None)  # nothing measured
    assert _used(engine, snap) == (10.0, "configured")


def test_a_measured_size_far_below_the_configured_one_is_not_believed() -> None:
    engine, snap = _sized(True, 4.0, 3.5)

    assert _used(engine, snap) == (10.0, "configured")


def _learned_efficiency(engine) -> None:
    """Enough measurements for a measured curve in both directions (3 kW, 90 % charging, 95 % discharging)."""
    steps = {"charging": {}, "discharging": {}}
    for _ in range(25):
        add_efficiency_measurement(steps["charging"], 0.2, 0.18, 240.0)
        add_efficiency_measurement(steps["discharging"], 0.19, 0.2, 240.0)
    engine.analysis_db["efficiency"] = steps


def test_with_a_measured_efficiency_curve_the_configured_size_stays() -> None:
    """The curve was measured with the configured size and already makes the plan agree with the battery: counting on a
    smaller size as well would correct twice."""
    engine, snap = _sized(True)
    _learned_efficiency(engine)

    assert _used(engine, snap) == (10.0, "efficiency")
    on = _model(engine, snap)
    engine, snap = _sized(False)
    _learned_efficiency(engine)
    assert _model(engine, snap) == pytest.approx(on)  # the plan's energy and level are exactly as with the switch off


@pytest.mark.parametrize(
    "solar",
    [
        {},  # the fixed round trip loss
        {"battery_charging_efficiency_curve": {"1000": 92, "5000": 92}, "battery_discharging_efficiency_curve": {"1000": 95, "5000": 95}},
    ],
    ids=["fixed loss", "configured curves"],
)
def test_the_measured_size_moves_the_plan_toward_the_runs_and_never_past_them(solar: dict) -> None:
    upper, lower = 8.89, 7.6
    off_engine, off_snap = _sized(False, upper, lower, **solar)
    on_engine, on_snap = _sized(True, upper, lower, **solar)
    charge_off, discharge_off = _model(off_engine, off_snap)
    charge_on, discharge_on = _model(on_engine, on_snap)

    # charging: level points per AC kWh. The runs say 100 / upper; the plan was below that and is nearer now, not above
    assert charge_off < charge_on <= 100.0 / upper
    # discharging: AC kWh per level point. The runs say lower / 100; the plan was above that and is nearer now, not below
    assert lower / 100.0 <= discharge_on < discharge_off


def test_the_size_in_use_is_published() -> None:
    engine, snap = _sized(True)

    result = engine.run_job(snap, engine.publish_analysis)

    published = result.attributes["analysis"]["battery_capacity"]
    assert published["enabled"] is True and published["configured"] == 10.0
    assert published["used"] == {"kwh": 8.8, "source": "measured"}
    assert result.attributes["analysis"]["battery_size"] == pytest.approx(8.8)  # cycles and the loss count on it as well


def test_a_new_size_makes_a_new_plan() -> None:
    engine, snap = _sized(True)
    same_engine, same_snap = _sized(True, None)  # same switch, nothing measured: the configured size

    assert engine.run_job(snap, engine.plan_inputs_hash).value != same_engine.run_job(same_snap, same_engine.plan_inputs_hash).value
    bigger, bigger_snap = _sized(True, 9.0, 8.0)
    assert engine.run_job(snap, engine.plan_inputs_hash).value != bigger.run_job(bigger_snap, bigger.plan_inputs_hash).value


def test_the_plan_changes_with_the_measured_size_and_not_with_the_switch_off() -> None:
    now = datetime.datetime.combine(DAY, datetime.time(10, 30))
    plain = started_engine(now, 40.0)
    off, on = started_engine(now, 40.0), started_engine(now, 40.0)
    for engine in (plain, off, on):
        engine.config["solar"]["powerwall_charge_discharge_loss"] = 0.1
    off.analysis_db["capacity"] = on.analysis_db["capacity"] = _measured(6.0, 5.0)  # a battery that holds a little over half

    base = plan(plain, now, 40.0)
    switched_off = plan(off, now, 40.0, {"learn_battery_capacity": False})
    plan(on, now, 40.0, {"learn_battery_capacity": True})

    assert charge_hours(plain) and base.states["powerwall_action"]
    assert switched_off.states == base.states and off.charge_hours == plain.charge_hours  # off: exactly the plan without data
    assert on.charge_hours != plain.charge_hours  # on: the plan counts on a smaller battery


def test_the_plan_needs_runs_in_both_directions() -> None:
    one_sided = clean_capacity({"episodes": {"charging": [{"l0": 10.0, "l1": 60.0, "t0": 1.0 + i, "t1": 2.0 + i, "ac": 4.0} for i in range(5)]}})
    summary = capacity_summary(one_sided, 10.0)

    assert summary["status"] == "below" and summary["discharging"]["episodes"] == 0  # the charging bound alone says smaller ...
    assert used_capacity(summary, 10.0) is None  # ... but it could not be checked against the discharging one


def test_a_changed_configured_size_starts_the_measuring_again() -> None:
    engine, now, snap = _engine_with_cycles()
    assert engine.run_job(snap, engine.hour_end, now.replace(minute=0)).error is None
    assert len(engine.analysis_db["capacity"]["episodes"]["charging"]) == 3

    engine.config["solar"]["powerwall_battery_size"] = 15.0  # a module was added: the old runs belong to the old battery
    engine.analysis_db["capacity"]["until"] = 0.0
    later = snapshot(now.replace(hour=21, minute=59), 50)
    later.buffers = snap.buffers
    later.units["sensor.battery_level"] = "%"
    assert engine.run_job(later, engine.hour_end, now.replace(hour=21, minute=0)).error is None

    # (the same buffers measured again with the new size's plausibility check: 15 kWh of 10 kWh runs are still within 0.25..4)
    assert engine.analysis_db["capacity"]["sensors"][3] == 15.0
    assert engine.analysis_db["capacity"]["episodes"]["charging"] != []  # measured again from the buffers, not carried over
    assert engine.analysis_db["capacity"]["open"] is None


def test_one_measured_efficiency_direction_is_enough_to_keep_the_configured_size() -> None:
    engine, snap = _sized(True)
    steps = {"charging": {}, "discharging": {}}
    for _ in range(25):
        add_efficiency_measurement(steps["charging"], 0.2, 0.18, 240.0)
    engine.analysis_db["efficiency"] = steps  # only the charging curve is learned, discharging uses the fixed loss

    assert _used(engine, snap) == (10.0, "efficiency")


def test_the_analysis_is_published_again_when_the_size_in_use_changes() -> None:
    engine, off = _sized(False)
    assert engine.run_job(off, engine.publish_analysis).error is None
    on = snapshot(off.now, 40.0, {"learn_battery_capacity": True})

    unchanged = engine.run_job(off, engine.republish_forecast_if_changed)
    changed = engine.run_job(on, engine.republish_forecast_if_changed)

    assert "analysis" not in unchanged.attributes
    assert changed.attributes["analysis"]["battery_capacity"]["used"] == {"kwh": 8.8, "source": "measured"}


def _history(now: datetime.datetime) -> dict:
    """Three charges of 2 kWh written when the size in use was the configured 10 kWh (20 % each)."""
    return {
        now - datetime.timedelta(hours=hours): {
            "charged": {"percentage": 20.0, "kWh": 2.0, "kWh_from_grid": 2.0, "kWh_from_local_energy": 0.0, "cost": 2.0, "price": 1.0}
        }
        for hours in (1, 2, 3)
    }


@pytest.mark.parametrize(("switch", "expected_kwh"), [(False, 4.0), (True, 3.52)])
def test_the_battery_cost_walk_follows_the_size_in_use_not_the_one_the_history_was_written_at(switch: bool, expected_kwh: float) -> None:
    """The battery holds 40 % of the size in use: 4.0 kWh of 10 kWh, 3.52 kWh of 8.8 kWh. The stored 20 % per charge belong to
    the old size, so the walk must count them from the stored kWh."""
    engine, snap = _sized(switch)
    engine.charging_history_db = _history(snap.now)

    expenses = engine.run_job(snap, engine.current_battery_level_expenses).value

    assert expenses["kwh"] == pytest.approx(expected_kwh, abs=0.01)
    assert expenses["percentage"] == pytest.approx(40.0, abs=0.1)


@pytest.mark.parametrize(("switch", "expected_percentage"), [(False, 20.0), (True, 2.0 / 8.8 * 100.0)])
def test_the_max_age_walk_follows_the_size_in_use(switch: bool, expected_percentage: float) -> None:
    engine, snap = _sized(switch)
    snap.settings["force_powerwall_max_hours_old_discharge"] = True
    entry = {"kWh": 2.0, "percentage": 20.0}  # (the percentage of the size at the time it was written)
    ctx = SimpleNamespace(battery_expenses={snap.now - datetime.timedelta(hours=1): entry}, efficiency=1.0, kwh_to_percentage=engine.kwh_to_percentage, config=engine.config)

    limits = engine.run_job(snap, engine.age_limits, ctx, 50.0).value

    assert limits["charged_before"][0][1] == pytest.approx(expected_percentage)


def test_the_max_age_walk_still_counts_the_efficiency_and_reads_bad_kwh_as_nothing() -> None:
    engine, snap = _sized(True)
    snap.settings["force_powerwall_max_hours_old_discharge"] = True
    first, second, third = (snap.now - datetime.timedelta(hours=hours) for hours in (1, 2, 3))
    ctx = SimpleNamespace(
        battery_expenses={first: {"kWh": 2.0, "percentage": 20.0}, second: {"kWh": None, "percentage": 20.0}, third: {"kWh": "x"}},
        efficiency=0.9, kwh_to_percentage=engine.kwh_to_percentage, config=engine.config,
    )

    limits = engine.run_job(snap, engine.age_limits, ctx, 50.0).value

    assert limits["charged_before"] == ((first.replace(minute=0, second=0, microsecond=0), pytest.approx(2.0 / 8.8 * 100.0 * 0.9)),)


def test_the_cost_walk_counts_a_row_without_a_stored_percentage() -> None:
    engine, snap = _sized(True)
    engine.charging_history_db = _history(snap.now)
    for entry in engine.charging_history_db.values():
        del entry["charged"]["percentage"]  # the kWh are all it needs

    assert engine.run_job(snap, engine.current_battery_level_expenses).value["kwh"] == pytest.approx(3.52, abs=0.01)


def test_the_history_table_keeps_a_row_it_can_only_read_a_percentage_from_and_survives_a_size_of_zero() -> None:
    engine, snap = _sized(False, None)
    now = snap.now - datetime.timedelta(hours=2)
    only_percentage = {"charged": {"percentage": 20.0, "kWh_from_local_energy": 0.0, "cost": 1.0}}  # no kWh
    with_kwh = {"charged": {"percentage": 20.0, "kWh": 2.0, "kWh_from_grid": 2.0, "kWh_from_local_energy": 0.0, "cost": 2.0, "price": 1.0}}
    engine.charging_history_db = {now: only_percentage, now - datetime.timedelta(hours=1): with_kwh}

    assert engine.run_job(snap, engine.charging_history_combine_and_set).error is None
    assert now in engine.charging_history_db  # not dropped as an empty row

    engine.config["solar"]["powerwall_battery_size"] = 0.0
    assert engine.run_job(snap, engine.charging_history_combine_and_set).error is None  # a size of 0 raised before only at hour end
    assert len(engine.charging_history_db) == 2
