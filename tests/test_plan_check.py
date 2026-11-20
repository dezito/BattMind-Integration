"""Plan check (engine/analysis/plan_check.py): did the battery do what the published action asked, hour by hour."""
from __future__ import annotations

import datetime

import pytest

from custom_components.battmind.engine.analysis.plan_check import (
    PLAN_CHECK_DAYS,
    PLAN_CHECK_LATEST,
    judge_hour,
    level_margin,
    summarize_plan_check,
)
from custom_components.battmind.engine.energy.energy import derive_power_values
from custom_components.battmind.utils.codec import decode, encode

from .test_analysis import _buffers, _level
from .test_engine import DAY, TZ, snapshot, started_engine

LEVEL_MIN, LEVEL_MAX = 10.0, 100.0
FULL_HOUR = 3600.0


def _row(charge=0.0, discharge=0.0, home=1.0, solar=0.0, **extra) -> dict:
    return {"charge": charge, "discharge": discharge, "home": home, "solar": solar, **extra}


def _judge(action, row, seconds=None, level=50.0, ambiguous=False) -> dict | None:
    return judge_hour(seconds if seconds is not None else {action: FULL_HOUR}, row, level, LEVEL_MIN, LEVEL_MAX, ambiguous)


def _skip(entry: dict, reason: str) -> None:
    assert entry["ok"] is None and entry["skip"] == reason, entry


# --- the verdict of one hour -------------------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["discharge_allowed", "something_else"])
def test_normal_operation_is_not_judged(action) -> None:
    assert _judge(action, _row(charge=2.0)) is None


def test_no_action_known_is_not_judged() -> None:
    assert judge_hour({}, _row(), 50.0, LEVEL_MIN, LEVEL_MAX) is None
    assert judge_hour({"grid_charging": 0.0}, _row(), 50.0, LEVEL_MIN, LEVEL_MAX) is None


def test_grid_charging_matches_when_the_battery_charged_more_than_the_sun_could() -> None:
    entry = _judge("grid_charging", _row(charge=2.5))
    assert entry == {"a": "grid_charging", "ok": True, "c": 2.5, "d": 0.0}
    # 2.5 kWh surplus from the sun, 3.0 kWh charged: 0.5 kWh can only be from the grid
    assert _judge("grid_charging", _row(charge=3.0, home=0.5, solar=3.0))["ok"] is True


def test_grid_charging_that_normal_operation_explains_is_not_a_match() -> None:
    # the automation is not connected: the battery charges from a solar surplus anyway
    _skip(_judge("grid_charging", _row(charge=2.0, home=0.5, solar=3.0)), "unproven")


def test_a_battery_that_moves_both_ways_in_the_hour_is_judged_on_its_net_flow() -> None:
    # clouds: the first half charges 2.5 kWh from a surplus, the second half discharges 0.5 kWh into the house.
    # Net 2.0 kWh is what the sun's surplus (3.0 - 1.0) explains: not a grid charge
    _skip(_judge("grid_charging", _row(charge=2.5, discharge=0.5, home=1.0, solar=3.0)), "unproven")
    # the same for a forced discharge: 1.0 kWh net is the house's own load
    _skip(_judge("force_discharge", _row(charge=1.0, discharge=2.0, home=1.0, solar=0.0)), "unproven")
    # a real grid charge still shows in the net flow
    assert _judge("grid_charging", _row(charge=3.5, discharge=0.5, home=1.0, solar=1.0))["ok"] is True


def test_the_level_margin_is_larger_on_a_small_battery() -> None:
    assert level_margin(10.0) == 2.0 and level_margin(5.0) == 4.0 and level_margin(0.0) == 2.0
    # 97 % on a 5 kWh battery leaves 0.15 kWh: grid charging can't be expected to show 0.1 kWh
    _skip(judge_hour({"grid_charging": FULL_HOUR}, _row(charge=0.0), 97.0, LEVEL_MIN, LEVEL_MAX, margin=level_margin(5.0)), "limit")
    assert judge_hour({"grid_charging": FULL_HOUR}, _row(charge=0.0), 97.0, LEVEL_MIN, LEVEL_MAX)["ok"] is False


def test_grid_charging_without_charging_is_a_mismatch() -> None:
    assert _judge("grid_charging", _row(charge=0.02))["ok"] is False
    assert _judge("grid_charging", _row(charge=0.02, home=0.5, solar=3.0))["ok"] is False


def test_grid_charging_with_a_full_battery_at_the_start_is_skipped() -> None:
    _skip(_judge("grid_charging", _row(charge=0.0), level=99.0), "limit")


def test_force_discharge_must_discharge_more_than_the_house_used() -> None:
    assert _judge("force_discharge", _row(discharge=1.8, home=1.0))["ok"] is True
    assert _judge("force_discharge", _row(discharge=0.0))["ok"] is False
    # the evening house load, discharged by normal operation too: the automation may not be connected
    _skip(_judge("force_discharge", _row(discharge=1.0, home=1.0)), "unproven")
    _skip(_judge("force_discharge", _row(discharge=0.0), level=LEVEL_MIN + 1), "limit")


def test_solar_charging_only_checks_no_discharge_never_that_it_charges() -> None:
    assert _judge("force_solar_only_charging", _row(home=1.0, solar=0.0, discharge=0.0, charge=0.0))["ok"] is True
    assert _judge("force_solar_only_charging", _row(home=1.0, solar=0.0, discharge=0.6))["ok"] is False
    # the house used almost nothing the battery could have discharged: proves nothing
    _skip(_judge("force_solar_only_charging", _row(home=0.1, solar=0.0, discharge=0.0)), "no_load")
    _skip(_judge("force_solar_only_charging", _row(home=1.0, solar=1.0)), "no_load")


def test_a_battery_that_drains_to_the_reserve_is_a_mismatch_not_a_skip() -> None:
    # the level at the start counts, not the level at the end, which is low because it discharged
    assert _judge("force_solar_only_charging", _row(home=3.0, discharge=3.0), level=40.0)["ok"] is False
    _skip(_judge("force_solar_only_charging", _row(home=3.0, discharge=0.0), level=LEVEL_MIN), "limit")


def test_solar_charging_blocked_needs_a_surplus() -> None:
    assert _judge("blocked_solar_charge", _row(home=0.5, solar=2.0, charge=0.0))["ok"] is True
    assert _judge("blocked_solar_charge", _row(home=0.5, solar=2.0, charge=0.8))["ok"] is False
    _skip(_judge("blocked_solar_charge", _row(home=1.0, solar=0.0, charge=0.0)), "no_surplus")
    _skip(_judge("blocked_solar_charge", _row(home=0.5, solar=2.0), level=100.0), "limit")
    # the consumption without any exclusion is the larger load: the surplus is smaller than it looks
    _skip(_judge("blocked_solar_charge", _row(home=0.5, use=2.5, solar=2.0)), "no_surplus")


def test_a_charging_car_leaves_solar_charging_blocked_unjudged() -> None:
    # the blueprint runs the actions for "while the car is charging" instead
    _skip(_judge("blocked_solar_charge", _row(home=2.5, ignored=2.0, solar=4.0, charge=1.0)), "ev")


def test_battery_stopped_means_neither_direction_when_normal_operation_would_move_it() -> None:
    assert _judge("battery_stopped", _row(home=1.0, solar=0.0))["ok"] is True
    assert _judge("battery_stopped", _row(home=1.0, solar=0.0, discharge=0.4))["ok"] is False
    assert _judge("battery_stopped", _row(home=0.5, solar=2.0, charge=0.4))["ok"] is False
    # an idle hour proves nothing
    _skip(_judge("battery_stopped", _row(home=0.1, solar=0.0)), "no_load")
    # a full battery would not have charged from the surplus either
    _skip(_judge("battery_stopped", _row(home=0.5, solar=2.0), level=100.0), "no_load")


def test_the_action_that_held_most_of_the_hour_counts() -> None:
    seconds = {"grid_charging": 3000.0, "discharge_allowed": 600.0}
    assert _judge("grid_charging", _row(charge=2.0), seconds)["ok"] is True


def test_an_hour_where_the_action_changed_is_skipped() -> None:
    # 2400 of 3600 seconds is less than three quarters
    _skip(_judge("grid_charging", _row(charge=0.0), {"grid_charging": 2400.0, "force_discharge": 1200.0}), "changed")


def test_an_incomplete_hour_is_skipped() -> None:
    _skip(_judge("grid_charging", _row(charge=0.0), {"grid_charging": 1500.0}), "partial")


def test_an_hour_counted_twice_is_skipped() -> None:
    _skip(_judge("grid_charging", _row(charge=0.0), {"grid_charging": 7200.0}), "twice")
    _skip(_judge("grid_charging", _row(charge=0.0), ambiguous=True), "twice")


@pytest.mark.parametrize("bad", [None, "unavailable", float("nan"), float("inf")])
def test_missing_measurements_are_never_a_match(bad) -> None:
    for key in ("charge", "discharge", "home", "solar", "use", "ignored"):
        row = _row(charge=2.0)
        row[key] = bad
        _skip(_judge("grid_charging", row), "no_data")
    _skip(_judge("grid_charging", _row(charge=2.0), level=None), "no_data")
    _skip(_judge("grid_charging", _row(charge=2.0), level=float("nan")), "no_data")


def test_the_consumption_of_the_row_is_the_house_load_without_the_battery() -> None:
    """The "unproven" limits assume it: the meter's consumption is corrected for the battery like configured."""
    both = {"power_consumption_entity_id_include_powerwall_discharging": True, "power_consumption_entity_id_include_powerwall_charging": True}
    values = derive_power_values(both, consumption=3600.0, ignored=0.0, charging=3000.0, discharging=0.0, solar=0.0)
    assert values["power_consumption"] == 600.0  # a meter that includes the charging: the 3 kW of it is taken off
    neither = {"power_consumption_entity_id_include_powerwall_discharging": False, "power_consumption_entity_id_include_powerwall_charging": False}
    values = derive_power_values(neither, consumption=100.0, ignored=0.0, charging=0.0, discharging=900.0, solar=0.0)
    assert values["power_consumption"] == 1000.0  # a meter that doesn't include the discharging: the 900 W is added


def test_an_old_row_without_use_and_ignored_is_judged_with_the_house_consumption() -> None:
    assert "use" not in _row() and "ignored" not in _row()
    assert _judge("grid_charging", _row(charge=2.5))["ok"] is True


# --- what is published -------------------------------------------------------------------------------------------


def _table(*items) -> dict:
    return {key: {"a": "grid_charging", "c": 0.0, "d": 0.0, "ok": ok} for key, ok in items}


def test_summary_counts_matches_skips_and_the_latest_mismatches() -> None:
    today = datetime.date(2026, 10, 22)
    table = _table(
        ("2026-10-22T01", True), ("2026-10-22T02", True), ("2026-10-22T03", False), ("2026-10-21T04", False),
        ("2026-10-21T05", False), ("2026-10-21T06", False), ("2026-10-21T07", None),
    )
    summary = summarize_plan_check(table, today)
    assert summary["checked_hours"] == 6 and summary["matching_hours"] == 2 and summary["skipped_hours"] == 1
    assert summary["match_percent"] == 33
    assert summary["days"] == PLAN_CHECK_DAYS
    mismatches = summary["latest_mismatches"]
    assert len(mismatches) == PLAN_CHECK_LATEST
    assert [item["hour"] for item in mismatches] == ["2026-10-22T03", "2026-10-21T06", "2026-10-21T05"]
    assert set(mismatches[0]) == {"hour", "action", "charged_kwh", "discharged_kwh"}


def test_summary_without_judged_hours_has_no_percentage() -> None:
    summary = summarize_plan_check({}, datetime.date(2026, 10, 22))
    assert summary["match_percent"] is None and summary["checked_hours"] == 0 and summary["latest_mismatches"] == []
    only_skipped = summarize_plan_check(_table(("2026-10-22T01", None)), datetime.date(2026, 10, 22))
    assert only_skipped["match_percent"] is None and only_skipped["skipped_hours"] == 1


def test_summary_counts_exactly_the_days_of_the_window_and_ignores_garbage() -> None:
    today = datetime.date(2026, 10, 22)
    oldest = (today - datetime.timedelta(days=PLAN_CHECK_DAYS - 1)).isoformat()
    too_old = (today - datetime.timedelta(days=PLAN_CHECK_DAYS)).isoformat()
    table = {**_table((f"{too_old}T01", False), (f"{oldest}T01", True), ("2026-10-22T01", True)), "garbage": "text", 5: {"ok": True}}
    summary = summarize_plan_check(table, today)
    assert summary["checked_hours"] == 2 and summary["match_percent"] == 100


# --- counting the time of each action and judging the hour on the engine ------------------------------------------


def _at(hour: int, minute: int, day: datetime.date = DAY) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(hour, minute))


def _note(engine, hour: int, minute: int, action: str, day: datetime.date = DAY) -> None:
    result = engine.run_job(snapshot(_at(hour, minute, day), 50.0), engine.plan_check_note, action)
    assert result.error is None, result.error


def _record(engine, hour: int, row: dict | None = None, buffers: dict | None = None, day: datetime.date = DAY) -> None:
    if row is not None:
        engine.analysis_db["hours"][f"{day.isoformat()}T{hour:02d}"] = row
    snap = snapshot(_at(hour, 59, day), 50.0)
    snap.buffers = buffers if buffers is not None else _buffers()
    result = engine.run_job(snap, engine._plan_check_record_hour, _at(hour, 0, day))
    assert result.error is None, result.error


def _every_five_minutes(engine, hour: int, action: str, first=0, last=55, day: datetime.date = DAY) -> None:
    for minute in range(first, last + 1, 5):
        _note(engine, hour, minute, action, day)


def _engine():
    return started_engine(_at(12, 30), 50.0)


def _summary(engine) -> dict:
    """The summary as the jobs publish it: inside a job, where now() is the snapshot's time."""
    result = engine.run_job(snapshot(_at(12, 30), 50.0), engine.plan_check_summary)
    assert result.error is None, result.error
    return result.value


def _entry(engine, hour: int, day: datetime.date = DAY) -> dict:
    return engine.analysis_db["plan_check"][f"{day.isoformat()}T{hour:02d}"]


def test_a_whole_hour_of_grid_charging_is_judged_a_match() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging")
    _record(engine, 1, _row(charge=3.0))
    assert _entry(engine, 1)["ok"] is True and _entry(engine, 1)["a"] == "grid_charging"
    assert _summary(engine)["match_percent"] == 100


def test_a_battery_that_did_not_charge_is_a_mismatch_in_the_summary() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging")
    _record(engine, 1, _row(charge=0.0))
    summary = _summary(engine)
    assert summary["match_percent"] == 0
    assert summary["latest_mismatches"] == [{"hour": f"{DAY.isoformat()}T01", "action": "grid_charging", "charged_kwh": 0.0, "discharged_kwh": 0.0}]


def test_the_time_is_split_at_the_hour() -> None:
    engine = _engine()
    _note(engine, 0, 55, "grid_charging")
    _note(engine, 1, 5, "grid_charging")
    hours = engine._plan_tally["hours"]
    assert hours[f"{DAY.isoformat()}T00"] == {"grid_charging": 300.0}
    assert hours[f"{DAY.isoformat()}T01"] == {"grid_charging": 300.0}


def test_the_action_published_first_gets_the_time_until_the_next_call() -> None:
    engine = _engine()
    _note(engine, 1, 0, "grid_charging")
    _note(engine, 1, 5, "force_discharge")
    _note(engine, 1, 10, "force_discharge")
    assert engine._plan_tally["hours"][f"{DAY.isoformat()}T01"] == {"grid_charging": 300.0, "force_discharge": 300.0}


def test_an_action_that_changed_in_the_hour_is_skipped_not_a_mismatch() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging", 0, 35)
    _every_five_minutes(engine, 1, "force_discharge", 40, 55)
    _record(engine, 1, _row(charge=0.0))
    _skip(_entry(engine, 1), "changed")
    assert _summary(engine)["checked_hours"] == 0


def test_a_gap_after_a_restart_leaves_the_hour_incomplete() -> None:
    engine = _engine()
    _note(engine, 1, 0, "grid_charging")
    _note(engine, 1, 5, "grid_charging")
    # nothing for 45 minutes: more than two calculation intervals of it is not counted
    _every_five_minutes(engine, 1, "grid_charging", 50, 55)
    _record(engine, 1, _row(charge=0.0))
    _skip(_entry(engine, 1), "partial")


def test_the_first_hour_after_a_start_is_not_judged() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging", 30, 55)  # started at half past
    _record(engine, 1, _row(charge=0.0))
    _skip(_entry(engine, 1), "partial")


def test_a_lost_level_sensor_is_never_read_as_a_battery_level() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging")
    buffers = {key: rows for key, rows in _buffers().items() if key != "sensor.battery_level"}
    _record(engine, 1, _row(charge=0.0), buffers=buffers)
    _skip(_entry(engine, 1), "no_data")


def test_a_battery_power_sensor_lost_for_most_of_the_hour_is_not_a_mismatch() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging")
    buffers = _buffers()
    zone = snapshot(_at(1, 0), 50.0).tz
    # 10 minutes known, then lost until the next row at 02:00
    lost = datetime.datetime.combine(DAY, datetime.time(1, 10), tzinfo=zone).timestamp()
    buffers["sensor.battery_power"] = sorted([*buffers["sensor.battery_power"], (lost, None)])
    _record(engine, 1, _row(charge=0.0), buffers=buffers)
    _skip(_entry(engine, 1), "no_data")


def _ts(hour: int, minute: int = 0) -> float:
    return datetime.datetime.combine(DAY, datetime.time(hour, minute), tzinfo=TZ).timestamp()


def _constant(watt: float) -> list[tuple[float, float | None]]:
    return [(_ts(0) - 3600, watt)]  # one state before the day, held all day


def _lost(rows: list, start: tuple[int, int], back: tuple[int, int]) -> list:
    """rows with the sensor lost from start until it reports again at back."""
    return sorted([*rows, (_ts(*start), None), (_ts(*back), rows[0][1])])


def _daylight_engine():
    engine = _engine()
    engine.config["solar"]["entity_ids"]["production_entity_id"] = "sensor.solar"
    return engine


def test_a_solar_sensor_that_was_lost_in_daylight_is_not_read_as_no_sun() -> None:
    row = _row(charge=3.0, home=1.0, solar=3.0)
    # 18:00 is daylight (before the fallback sunset at 19:00), and the battery is at 95 % then
    for solar_rows, expected in ((_constant(3000.0), True), (_lost(_constant(3000.0), (18, 10), (19, 0)), None)):
        engine = _daylight_engine()
        _every_five_minutes(engine, 18, "grid_charging")
        _record(engine, 18, row, buffers={**_buffers(), "sensor.solar": solar_rows})
        entry = _entry(engine, 18)
        if expected:
            assert entry["ok"] is True
        else:
            _skip(entry, "no_data")


def test_a_solar_sensor_that_is_lost_in_the_dark_counts_as_zero() -> None:
    engine = _daylight_engine()
    _every_five_minutes(engine, 1, "grid_charging")  # 01:00 is before sunrise
    _record(engine, 1, _row(charge=3.0), buffers={**_buffers(), "sensor.solar": _lost(_constant(0.0), (1, 10), (2, 0))})
    assert _entry(engine, 1)["ok"] is True


def test_an_ignored_consumer_that_was_lost_leaves_the_hour_unjudged() -> None:
    for car_rows, expected in ((_constant(0.0), True), (_lost(_constant(0.0), (1, 10), (2, 0)), None)):
        engine = _engine()
        engine.config["home"]["entity_ids"]["ignore_consumption_from_entity_ids"] = ["sensor.car"]
        _every_five_minutes(engine, 1, "grid_charging")
        _record(engine, 1, _row(charge=3.0), buffers={**_buffers(), "sensor.car": car_rows})
        entry = _entry(engine, 1)
        if expected:
            assert entry["ok"] is True
        else:
            _skip(entry, "no_data")


def test_an_hour_without_any_measuring_is_skipped() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging")
    _record(engine, 1, _row(charge=0.0), buffers={})
    _skip(_entry(engine, 1), "no_data")


def test_normal_operation_leaves_no_entry() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "discharge_allowed")
    _record(engine, 1, _row(charge=1.0))
    assert engine.analysis_db["plan_check"] == {}


def test_an_hour_without_a_row_is_left_alone() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging")
    _record(engine, 1, None)
    assert engine.analysis_db["plan_check"] == {}


def test_the_hour_that_comes_twice_when_summer_time_ends_is_not_judged() -> None:
    day = datetime.date(2026, 10, 25)  # the clocks go back from 03:00 to 02:00 in Europe/Copenhagen
    engine = _engine()
    engine._plan_check_level = lambda start: 50.0  # the sensors are fine: only the repeated hour is under test
    for hour, ambiguous in ((1, False), (2, True), (3, False)):
        _every_five_minutes(engine, hour, "grid_charging", day=day)
        _record(engine, hour, _row(charge=3.0), day=day)
        entry = _entry(engine, hour, day)
        if ambiguous:
            _skip(entry, "twice")
        else:
            assert entry["ok"] is True, hour
    # the second pass of the hour does not turn the skip into a match
    _every_five_minutes(engine, 2, "grid_charging", day=day)
    _record(engine, 2, _row(charge=3.0), day=day)
    _skip(_entry(engine, 2, day), "twice")


def test_old_hours_and_broken_keys_are_removed() -> None:
    engine = _engine()
    old = (DAY - datetime.timedelta(days=PLAN_CHECK_DAYS + 2)).isoformat()
    engine.analysis_db["plan_check"].update({f"{old}T05": {"a": "grid_charging", "ok": True}, 5: {"ok": True}, ("a", 1): {"ok": False}})
    _every_five_minutes(engine, 1, "grid_charging")
    _record(engine, 1, _row(charge=3.0))
    assert list(engine.analysis_db["plan_check"]) == [f"{DAY.isoformat()}T01"]


def test_the_tally_does_not_keep_old_hours() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging")
    _every_five_minutes(engine, 2, "grid_charging")
    _record(engine, 2, _row(charge=3.0))
    assert all(key >= f"{DAY.isoformat()}T02" for key in engine._plan_tally["hours"])


def test_a_failure_in_the_note_never_fails_the_calculation() -> None:
    engine = _engine()
    engine._plan_tally = None
    result = engine.run_job(snapshot(_at(1, 0), 50.0), engine.charge_if_needed, True)
    assert result.error is None
    assert result.states["powerwall_action"]


def test_the_summary_is_published_on_the_action_sensor() -> None:
    engine = _engine()
    _every_five_minutes(engine, 1, "grid_charging")
    _record(engine, 1, _row(charge=3.0))
    result = engine.run_job(snapshot(_at(2, 0), 50.0), engine.publish_analysis)
    assert result.error is None
    summary = result.attributes["powerwall_action"]["plan_check"]
    assert summary["checked_hours"] == 1 and summary["match_percent"] == 100


def test_an_old_database_without_the_key_is_loaded() -> None:
    engine = _engine()
    engine.load_analysis_db({"hours": {}, "days": {}, "plans": {}})
    assert engine.analysis_db["plan_check"] == {}
    engine.load_analysis_db({"plan_check": "garbage"})
    assert engine.analysis_db["plan_check"] == {}
    engine.load_analysis_db(None)
    assert engine.analysis_db["plan_check"] == {}


def _hour_end(engine, hour: int, buffers: dict):
    now = _at(hour, 59)
    snap = snapshot(now, _level(hour))
    snap.buffers = buffers
    return engine.run_job(snap, engine.hour_end, now.replace(minute=0))


def test_hour_end_judges_the_hour_from_the_measured_energy() -> None:
    """The whole path: the actions of the hour, the measured row of hour_end() and the verdict, stored and decoded again."""
    engine = _engine()
    buffers = _buffers()  # the battery charges 3 kW in hours 1 to 3
    _every_five_minutes(engine, 0, "discharge_allowed")
    assert _hour_end(engine, 0, buffers).error is None
    _every_five_minutes(engine, 1, "grid_charging")
    result = _hour_end(engine, 1, buffers)
    assert result.error is None, result.error
    entry = _entry(engine, 1)
    assert entry["ok"] is True and entry["c"] == pytest.approx(3.0, abs=0.05)
    assert decode(encode(engine.analysis_db))["plan_check"] == engine.analysis_db["plan_check"]
    # the same hours, but the battery did not charge although grid charging was published
    quiet = {key: ([(ts, 0.0) for ts, _ in rows] if key == "sensor.battery_power" else rows) for key, rows in buffers.items()}
    engine = _engine()
    _every_five_minutes(engine, 0, "discharge_allowed")
    _hour_end(engine, 0, quiet)
    _every_five_minutes(engine, 1, "grid_charging")
    assert _hour_end(engine, 1, quiet).error is None
    assert _entry(engine, 1)["ok"] is False


def test_a_failing_plan_check_does_not_stop_the_hour_from_being_recorded() -> None:
    engine = _engine()

    def broken(start):
        raise RuntimeError("boom")

    engine._plan_check_record_hour = broken
    result = _hour_end(engine, 1, _buffers())
    assert result.error is None, result.error
    assert f"{DAY.isoformat()}T01" in engine.analysis_db["hours"]
