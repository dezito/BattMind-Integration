"""The states of a window are held until the next change (utils/power.py), not drawn as lines between rows.

A sensor writes a row only when its state changes. The battery flow of a Huawei battery that is full
jumps between 0 and 5 kW for a few seconds with minutes between the rows: the lines between the rows
made a 0.13 kWh hour a 2.3 kWh hour.
"""
from __future__ import annotations

import datetime
import zoneinfo

import pytest

from custom_components.battmind.utils.efficiency import _flow_energy
from custom_components.battmind.utils.power import WindowCalculator

TZ = zoneinfo.ZoneInfo("Europe/Copenhagen")
DAY = datetime.date(2026, 11, 18)


def at(hour: int, minute: int = 0, second: int = 0) -> float:
    return datetime.datetime.combine(DAY, datetime.time(hour, minute, second), tzinfo=TZ).timestamp()


def naive(hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(DAY, datetime.time(hour, minute, second))


def calculator(rows, statistics=None, unit="W") -> WindowCalculator:
    return WindowCalculator({"sensor.flow": rows}, statistics or {}, {"sensor.flow": unit}, TZ)


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def charging(values: list[float]) -> float:
    """What the engine does: the positive values count as charging, the others as 0 W."""
    return mean([max(value, 0.0) for value in values])


# the rows of a night in which a hybrid inverter's battery was full (battery power in W, positive = charging)
FULL_BATTERY = [
    (at(4, 58, 42), 2641.0), (at(4, 59, 13), 49.0), (at(4, 59, 44), -539.0),
    (at(5, 0, 15), 0.0),
    (at(5, 5, 25), 4998.0), (at(5, 5, 56), 0.0),
    (at(5, 35, 24), 5000.0), (at(5, 35, 55), 0.0),
    (at(5, 55, 35), 5000.0), (at(5, 56, 6), 0.0),
    (at(6, 45, 44), 5000.0), (at(6, 46, 15), 4105.0), (at(6, 50, 23), 0.0),
]


def test_a_full_battery_that_blinks_does_not_charge_for_an_hour() -> None:
    """05:00-06:00: three bursts of about 30 s at 5 kW is 0.13 kWh. Lines between the rows gave 2.3 kWh."""
    start, end = naive(5), naive(5, 59, 59)
    values = calculator(FULL_BATTERY).short_values("sensor.flow", start, end)
    assert len(values) == 100
    exact_wh = _flow_energy(FULL_BATTERY, [row[0] for row in FULL_BATTERY], at(5), at(5, 59, 59), 1.0)[0]
    assert exact_wh == pytest.approx(130, abs=10)
    assert charging(values) == pytest.approx(exact_wh, abs=100)  # watt average of the hour = Wh; a burst is 1-2 of 100 points
    assert charging(values) < 300


def test_the_next_hour_has_a_real_stretch_of_charging_and_it_counts() -> None:
    """06:45:44-06:50:23: 5 kW for 31 s, then about 4.1 kW for four minutes: 0.33 kWh, found as it is."""
    values = calculator(FULL_BATTERY).short_values("sensor.flow", naive(6), naive(6, 59, 59))
    exact_wh = _flow_energy(FULL_BATTERY, [row[0] for row in FULL_BATTERY], at(6), at(6, 59, 59), 1.0)[0]
    assert exact_wh == pytest.approx(326, abs=5)
    assert charging(values) == pytest.approx(exact_wh, abs=100)


def test_a_state_is_held_until_the_next_row() -> None:
    """100 W from before the window until 30 minutes in, then 0 W: the mean is 50 W."""
    rows = [(at(4), 100.0), (at(5, 30), 0.0)]
    values = calculator(rows).short_values("sensor.flow", naive(5), naive(6))
    assert set(values) == {100.0, 0.0}  # every value is a real state, nothing in between
    assert mean(values) == pytest.approx(50, abs=1.5)


def test_a_short_burst_after_a_quiet_stretch_is_not_a_ramp() -> None:
    """0 W for 30 minutes, then 5 kW for 30 s: 42 W on average (a line from the 0 to the 5 kW row gave 2500 W)."""
    rows = [(at(5), 0.0), (at(5, 30), 5000.0), (at(5, 30, 30), 0.0)]
    values = calculator(rows).short_values("sensor.flow", naive(5), naive(6))
    assert mean(values) == pytest.approx(42, abs=60)
    assert mean(values) < 150


def test_the_state_at_the_start_of_the_window_counts() -> None:
    values = calculator([(at(3), 700.0)]).short_values("sensor.flow", naive(5), naive(6))
    assert values == [700.0] * 100  # not a single row inside the window: it still shows that state


def test_a_window_before_the_first_row_holds_the_first_value_back() -> None:
    values = calculator([(at(5, 30), 400.0)]).short_values("sensor.flow", naive(5), naive(6))
    assert values == [400.0] * 100


def test_the_last_value_is_the_newest_state() -> None:
    """The last of the 100 values is the state at the end of the window (the newest row)."""
    rows = [(at(5), 10.0), (at(5, 59, 40), 99.0)]
    assert calculator(rows).short_values("sensor.flow", naive(5), naive(6))[-1] == 99.0


def test_charging_and_discharging_in_the_same_window_are_weighed_by_time() -> None:
    """+2000 W for 15 minutes, -1000 W for 15 minutes, 0 W for the rest: 500 W charging, 250 W discharging."""
    rows = [(at(5), 2000.0), (at(5, 15), -1000.0), (at(5, 30), 0.0)]
    values = calculator(rows).short_values("sensor.flow", naive(5), naive(6))
    assert charging(values) == pytest.approx(500, abs=15)
    assert mean([max(-value, 0.0) for value in values]) == pytest.approx(250, abs=15)


def test_the_unit_is_converted_to_watt() -> None:
    values = calculator([(at(4), 2.0)], unit="kW").short_values("sensor.flow", naive(5), naive(6))
    assert values == [2000.0] * 100


@pytest.mark.parametrize("gap", [None, float("nan"), float("inf")])
def test_a_sensor_that_is_lost_does_not_keep_its_last_value(gap) -> None:
    """5 kW, then unavailable for 20 minutes, then 100 W: the lost time gives no value (it is not 5 kW for 20 more minutes)."""
    rows = [(at(4, 59), 5000.0), (at(5, 10), gap), (at(5, 30), 100.0)]
    values = calculator(rows).short_values("sensor.flow", naive(5), naive(6))
    assert set(values) == {5000.0, 100.0}
    assert values.count(5000.0) == pytest.approx(100 * 10 / 60, abs=2)  # 05:00-05:10
    assert values.count(100.0) == pytest.approx(100 * 30 / 60, abs=2)  # 05:30-06:00
    assert len(values) == pytest.approx(100 * 40 / 60, abs=3)  # only the known time
    assert mean(values) == pytest.approx((5000 * 10 + 100 * 30) / 40, rel=0.05)


def test_a_window_that_starts_while_the_sensor_is_lost_has_no_values_until_it_is_back() -> None:
    rows = [(at(4), None), (at(5, 30), 100.0)]
    values = calculator(rows).short_values("sensor.flow", naive(5), naive(6))
    assert set(values) == {100.0}
    assert len(values) == pytest.approx(50, abs=2)


@pytest.mark.parametrize("rows", [[], [(at(5), None)], [(at(4), float("nan")), (at(5), None)]])
def test_no_state_that_is_a_number_gives_no_values(rows) -> None:
    calc = calculator(rows)
    assert calc.short_values("sensor.flow", naive(5), naive(6)) == []
    assert calc.average("sensor.flow", naive(5), naive(6), error_state=-1.0) == -1.0


def test_a_missing_entity_gives_no_values() -> None:
    assert calculator([(at(4), 1.0)]).short_values("", naive(5), naive(6)) == []
    assert calculator([(at(4), 1.0)]).short_values("sensor.other", naive(5), naive(6)) == []


def test_the_average_is_the_time_weighted_mean() -> None:
    rows = [(at(4), 0.0), (at(5, 45), 1000.0)]
    assert calculator(rows).average("sensor.flow", naive(5), naive(6)) == pytest.approx(250, abs=8)


def test_the_hourly_statistics_are_still_interpolated_as_before() -> None:
    """Only the states are held; the long term statistics of the hours are not part of this change."""
    stats = {"sensor.flow": [(at(5), 100.0), (at(6), 300.0)]}
    values = calculator([], stats).long_values("sensor.flow", naive(5), naive(7))
    assert len(values) == 100
    assert values[0] == 100.0
    assert 100.0 < values[25] < 300.0  # a line between the two hours
    assert values[-1] == 300.0
