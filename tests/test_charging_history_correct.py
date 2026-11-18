"""The charging history of the last hours measured again from the buffers (engine/history/storage.py).

An hour recorded when the battery power was drawn as lines between its rows can have charging that never
happened. Only the hours the buffers can measure are corrected; the rules and the price of the battery energy
of the hour stay as recorded.
"""
from __future__ import annotations

import datetime

import pytest

from .test_engine import DAY, TZ, snapshot, started_engine

NOW = datetime.datetime.combine(DAY, datetime.time(12, 5))


def hour(h: int) -> datetime.datetime:
    return datetime.datetime.combine(DAY, datetime.time(h))


def ts(h: int, minute: int = 0) -> float:
    return datetime.datetime.combine(DAY, datetime.time(h, minute), tzinfo=TZ).timestamp()


def entry(charged: float = 0.0, discharged: float = 0.0, rules: dict | None = None, battery_price: float = 1.7) -> dict:
    """An hour as the history recorded it."""
    return {
        "rules": rules if rules is not None else {},
        "energy_summary": {"home_consumption_kwh": 0.6, "solar_production_kwh": 0.0, "home_consumption_without_solar_kwh": 0.6, "grid_consumption_kwh": 0.6},
        "prices": {"buy_price": 1.5, "sell_price": 1.0, "powerwall_kwh_price": battery_price, "kwh_savings": None, "charge_price": 1.5, "discharge_price": None},
        "charged": {"percentage": charged * 10, "kWh": charged, "kWh_from_grid": charged, "kWh_from_local_energy": 0.0, "cost": charged * 1.5, "price": 1.5},
        "discharged": {"percentage": discharged * 10, "kWh": discharged, "cost": discharged * battery_price, "price": battery_price, "savings": 0.1},
    }


def buffers() -> dict:
    """600 W in the house; the battery (sensor.battery_power, - = charging) charges 1 kW for 20 minutes from 05:10 and
    500 W the whole hour from 07:00; the sensor with the battery power has no states before 04:00."""
    return {
        "sensor.consumption": [(ts(0) - 3 * 86400, 600.0)],
        "sensor.battery_level": [(ts(0) - 3 * 86400, 50.0)],
        "sensor.battery_power": [
            (ts(4), 0.0), (ts(5, 10), -1000.0), (ts(5, 30), 0.0), (ts(7), -500.0), (ts(8), 0.0),
        ],
    }


def run(engine, level: float = 50.0, bufs: dict | None = None):
    snap = snapshot(NOW, level)
    snap.buffers = buffers() if bufs is None else bufs
    result = engine.run_job(snap, engine.correct_recent_charging_history)
    assert result.error is None, result.error
    return result


def test_the_hours_measured_again_are_kept_hourly_by_the_combining_of_old_days() -> None:
    from custom_components.battmind.const import CHARGING_HISTORY_COMBINE_AFTER, CHARGING_HISTORY_CORRECT_HOURS, HISTORY_BUFFER_HOURS

    assert CHARGING_HISTORY_CORRECT_HOURS < CHARGING_HISTORY_COMBINE_AFTER  # no entry of these hours is a combined day
    assert CHARGING_HISTORY_CORRECT_HOURS < HISTORY_BUFFER_HOURS  # the buffers hold the states of all of them


def test_an_hour_of_yesterday_is_measured_again_with_its_recorded_prices() -> None:
    """The price lists only hold today and later: the buy and sell price of an older hour are the recorded ones."""
    engine = started_engine(NOW, 50.0)
    yesterday = DAY - datetime.timedelta(days=1)
    key = datetime.datetime.combine(yesterday, datetime.time(5))
    recorded = entry(charged=2.3, rules={"cheapest_hour_fill_planner": True})
    recorded["prices"].update(buy_price=2.0, sell_price=1.2)
    engine.charging_history_db[key] = recorded
    at = lambda h, m=0: datetime.datetime.combine(yesterday, datetime.time(h, m), tzinfo=TZ).timestamp()  # noqa: E731
    bufs = {
        "sensor.consumption": [(at(0) - 86400, 600.0)],
        "sensor.battery_level": [(at(0) - 86400, 50.0)],
        "sensor.battery_power": [(at(0) - 86400, 0.0), (at(5, 10), -1000.0), (at(5, 30), 0.0)],
    }
    run(engine, bufs=bufs)
    fixed = engine.charging_history_db[key]
    assert fixed["charged"]["kWh"] == pytest.approx(0.33, abs=0.02)
    assert fixed["prices"]["buy_price"] == 2.0 and fixed["prices"]["sell_price"] == 1.2  # not converted again
    assert fixed["charged"]["price"] == pytest.approx(2.0)


def test_a_sensor_without_a_number_in_the_hour_is_not_read_as_0_w() -> None:
    engine = started_engine(NOW, 50.0)
    recorded = entry(charged=2.3)
    engine.charging_history_db[hour(5)] = recorded
    bufs = buffers()
    bufs["sensor.battery_power"] = [(ts(4), None)]  # unavailable all the time
    run(engine, bufs=bufs)
    assert engine.charging_history_db[hour(5)] is recorded


def test_a_solar_sensor_that_is_unavailable_at_night_does_not_stop_the_correction() -> None:
    """Solar and the ignored consumers are 0 W when lost, in the recording as well: the night hours are still measured."""
    engine = started_engine(NOW, 50.0, options={"production_entity_id": "sensor.solar"})
    engine.charging_history_db[hour(5)] = entry(charged=2.3)
    bufs = buffers()
    bufs["sensor.solar"] = [(ts(0) - 3 * 86400, None)]
    run(engine, bufs=bufs)
    assert engine.charging_history_db[hour(5)]["charged"]["kWh"] == pytest.approx(0.33, abs=0.02)
    # a solar sensor without any state in the buffers is not 0 W: the hour is left
    engine.charging_history_db[hour(5)] = recorded = entry(charged=2.3)
    bufs["sensor.solar"] = []
    run(engine, bufs=bufs)
    assert engine.charging_history_db[hour(5)] is recorded


def test_an_entry_that_cannot_be_read_is_left_and_the_others_are_corrected() -> None:
    engine = started_engine(NOW, 50.0)
    broken = entry(charged=1.0)
    broken["charged"]["kWh"] = "x"
    engine.charging_history_db[hour(6)] = broken
    engine.charging_history_db[hour(5)] = entry(charged=2.3)
    result = run(engine)
    # the unreadable hour does not stop the others (it is not measured: the failure is logged and the entry put back)
    assert engine.charging_history_db[hour(5)]["charged"]["kWh"] == pytest.approx(0.33, abs=0.02)
    assert result.error is None


def test_the_rules_table_of_the_day_is_made_again_from_the_corrected_hours() -> None:
    engine = started_engine(NOW, 50.0)
    engine.charging_history_db[hour(5)] = entry(charged=2.3, rules={"cheapest_hour_fill_planner": True})
    engine.analysis_db["rule_days"][DAY.isoformat()] = {"cheapest_hour_fill_planner": {"c": 2.3, "cc": 3.5, "d": 0.0, "v": 0.0, "s": 0.0}}
    run(engine)
    rules = engine.analysis_db["rule_days"][DAY.isoformat()]
    assert rules["cheapest_hour_fill_planner"]["c"] == pytest.approx(0.33, abs=0.02)


def test_it_runs_once_with_the_analysis_update() -> None:
    engine = started_engine(NOW, 50.0)
    engine.charging_history_db[hour(5)] = entry(charged=2.3)
    snap = snapshot(NOW, 50.0)
    snap.buffers = buffers()
    result = engine.run_job(snap, engine.analysis_update, {"stats": {}})
    assert result.error is None, result.error
    assert engine.charging_history_db[hour(5)]["charged"]["kWh"] == pytest.approx(0.33, abs=0.02)
    # a later version of the backfill: the history is not measured again
    phantom = entry(charged=2.3)
    engine.charging_history_db[hour(5)] = phantom
    engine.analysis_db["backfilled"] = 0
    result = engine.run_job(snap, engine.analysis_update, {"stats": {}})
    assert result.error is None, result.error
    assert engine.charging_history_db[hour(5)] is phantom


def test_an_hour_with_charging_that_never_happened_is_measured_again_and_keeps_its_rules() -> None:
    engine = started_engine(NOW, 50.0)
    rules = {"cheapest_hour_fill_planner": True}
    engine.charging_history_db[hour(5)] = entry(charged=2.3, rules=rules, battery_price=1.7)  # recorded: 2.3 kWh
    run(engine)
    fixed = engine.charging_history_db[hour(5)]
    assert fixed["charged"]["kWh"] == pytest.approx(0.33, abs=0.02)  # 1 kW for 20 of the 60 minutes
    assert fixed["rules"] == rules
    assert fixed["prices"]["powerwall_kwh_price"] == 1.7  # the price of the battery energy then, not now
    assert fixed["charged"]["cost"] == pytest.approx(fixed["charged"]["kWh"] * fixed["charged"]["price"], abs=0.005)  # the stored numbers are rounded


def test_an_hour_with_the_same_energy_is_left_exactly_as_recorded() -> None:
    engine = started_engine(NOW, 50.0)
    recorded = entry(charged=0.5, rules={"solar": {}}, battery_price=2.2)
    recorded["prices"]["buy_price"] = 9.99  # a price that is not the one of the hour now: it must not be touched
    engine.charging_history_db[hour(7)] = recorded
    run(engine)
    assert engine.charging_history_db[hour(7)] is recorded
    assert recorded["prices"]["buy_price"] == 9.99


def test_an_hour_without_any_energy_now_is_removed() -> None:
    engine = started_engine(NOW, 50.0)
    engine.charging_history_db[hour(9)] = entry(charged=1.0)
    run(engine)
    assert hour(9) not in engine.charging_history_db


def test_an_hour_that_was_not_recorded_is_not_added() -> None:
    engine = started_engine(NOW, 50.0)
    run(engine)
    assert hour(5) not in engine.charging_history_db and hour(7) not in engine.charging_history_db


def test_an_hour_the_buffers_cannot_measure_is_not_touched() -> None:
    """No states of the battery power before 04:00: it would count as 0 W and remove the hour."""
    engine = started_engine(NOW, 50.0)
    recorded = entry(charged=5.0)
    engine.charging_history_db[hour(2)] = recorded
    run(engine)
    assert engine.charging_history_db[hour(2)] is recorded


def test_nothing_is_touched_without_buffers() -> None:
    engine = started_engine(NOW, 50.0)
    recorded = entry(charged=5.0)
    engine.charging_history_db[hour(5)] = recorded
    result = engine.run_job(snapshot(NOW, 50.0), engine.correct_recent_charging_history)  # the snapshot has no buffers
    assert result.error is None, result.error
    assert engine.charging_history_db[hour(5)] is recorded


def test_the_current_and_older_hours_are_not_part_of_it() -> None:
    engine = started_engine(NOW, 50.0)
    current = entry(charged=1.0)
    older = datetime.datetime.combine(DAY - datetime.timedelta(days=4), datetime.time(5))
    engine.charging_history_db[hour(12)] = current  # the hour that is not finished
    engine.charging_history_db[older] = entry(charged=1.0)
    run(engine)
    assert engine.charging_history_db[hour(12)] is current
    assert older in engine.charging_history_db
