"""The hindsight score: the best possible plan, the baseline, the days that are not scored and the real hourly prices."""
from __future__ import annotations

import datetime

import pytest

from custom_components.battmind.engine.analysis.helpers import SCORE_MIN_POSSIBLE, SCORE_VERSION, day_score, hindsight_costs, score_figures

from .test_analysis import _buffers
from .test_engine import DAY, TZ, snapshot, started_engine

ONE = lambda _: 1.0  # noqa: E731  (a battery without losses)
CHEAP_HOURS = (0, 1, 2)


def _hours(price=lambda hour: 1.0 if hour in CHEAP_HOURS else 3.0, load: float = 0.5, sell: float = 0.5, charge=None, discharge=None, **hours) -> list[dict]:
    """24 hours of a house with `load` kWh an hour at the price of the hour. charge/discharge: {hour: kWh}; hours: {"h12": {"solar": 2.0}}."""
    result = [
        {"use": load, "solar": 0.0, "ignored": 0.0, "charge": (charge or {}).get(h, 0.0), "discharge": (discharge or {}).get(h, 0.0), "buy": price(h), "sell": sell}
        for h in range(24)
    ]
    for key, values in hours.items():
        result[int(key[1:])].update(values)
    return result


def _score(hours: list[dict], start: float = 1.0, end: float = 1.0, **kwargs) -> tuple[dict | None, str | None]:
    """A 10 kWh battery between 1 and 9 kWh, 3 kW both ways, without losses."""
    arguments = {"levels": [start, end], "low": 1.0, "high": 9.0, "max_charge": 3.0, "max_discharge": 3.0, "wear": 0.0, **kwargs}
    return day_score(hours, start, end, charge_efficiency=ONE, discharge_efficiency=ONE, **arguments)


def _ratio(score: dict) -> float:
    saved, possible = score_figures(score)
    assert possible > SCORE_MIN_POSSIBLE
    return saved / possible


FILL = {0: 3.0, 1: 3.0, 2: 2.0}  # 8 kWh in the cheap hours: the battery is full


def _cheap_night_and_evening(hour: int) -> float:
    return 1.0 if hour in (0, 1, 2, 21, 22, 23) else 3.0


@pytest.mark.parametrize(
    ("label", "start", "end", "charge", "discharge", "price"),
    [
        ("level neutral", 1.0, 1.0, FILL, dict.fromkeys(range(3, 19), 0.5), None),
        # 2 kWh more in the battery at the end: charged for tomorrow, not a loss
        ("ends higher", 1.0, 3.0, FILL, dict.fromkeys(range(3, 15), 0.5), None),
        # 8 kWh less: the battery was used up (it was charged the day before), not a saving made for free
        ("ends lower", 9.0, 1.0, {}, dict.fromkeys(range(3, 19), 0.5), _cheap_night_and_evening),
        # 1 kWh gone in standby: the level ends 1 kWh under what the flows say
        ("standby", 1.0, 1.0, FILL, dict.fromkeys(range(3, 17), 0.5), None),
    ],
)
def test_the_best_possible_plan_scores_100_percent(label: str, start: float, end: float, charge: dict, discharge: dict, price) -> None:
    hours = _hours(charge=charge, discharge=discharge) if price is None else _hours(price=price, charge=charge, discharge=discharge)
    score, reason = _score(hours, start, end)
    assert reason is None, label
    assert score["v"] == SCORE_VERSION
    assert score["optimal_raw"] <= score["actual"] + 0.01, label  # the model can make the plan the day made
    assert _ratio(score) == pytest.approx(1.0, abs=0.01), label


def test_an_idle_battery_scores_zero_and_a_bad_plan_below() -> None:
    idle, _ = _score(_hours())
    assert _ratio(idle) == pytest.approx(0.0, abs=0.01)
    # charged in an expensive hour, used in the cheap hours
    bad, _ = _score(_hours(charge={18: 2.0}, discharge={0: 0.5, 1: 0.5, 2: 0.5}), start=3.0, end=3.5)
    assert _ratio(bad) < 0.0


def test_the_baseline_is_the_battery_at_its_start_level() -> None:
    """The old baseline (without battery) made a day that ends higher a loss: possible went negative though the plan was the best."""
    score, _ = _score(_hours(charge=FILL, discharge=dict.fromkeys(range(3, 15), 0.5)), 1.0, 3.0)
    saved, possible = score_figures(score)
    assert possible == pytest.approx(16.0, abs=0.1)  # 8 kWh moved from the price 1 to the price 3
    assert saved == pytest.approx(possible, abs=0.01)
    # ending 2 kWh higher costs the best plan 2 kWh it could not use; the cost without the battery knows nothing of that
    assert score["optimal"] > score["optimal_neutral"]
    assert score["no_battery"] - score["actual"] < possible


def test_ignored_consumers_are_not_part_of_the_score() -> None:
    # solar surplus that a car took: the same day as one where the sun never made it
    with_car, _ = _score(_hours(charge=FILL, discharge=dict.fromkeys(range(3, 19), 0.5), h12={"solar": 2.0, "ignored": 1.0}))
    without, _ = _score(_hours(charge=FILL, discharge=dict.fromkeys(range(3, 19), 0.5), h12={"solar": 1.0}))
    assert with_car == without
    # battery energy that a car took (1 kWh more out of the battery, 1 kWh less at the end)
    with_car, _ = _score(_hours(charge=FILL, discharge={**dict.fromkeys(range(3, 15), 0.5), 10: 1.5}, h10={"ignored": 1.0}), 1.0, 2.0)
    without, _ = _score(_hours(charge=FILL, discharge=dict.fromkeys(range(3, 15), 0.5)), 1.0, 3.0)
    assert with_car == without
    assert _ratio(with_car) == pytest.approx(1.0, abs=0.01)
    # and what the battery gave a car is no export at the sell price
    assert with_car["actual"] == pytest.approx(without["actual"])


def test_a_load_and_a_surplus_smaller_than_a_level_step_are_exact() -> None:
    """A battery that covers a 0.03 kWh load every hour, and one that stores a 0.03 kWh surplus every hour: no whole step is needed."""
    rows = [{"load": 0.03, "buy": 3.0, "sell": 0.0}] * 24
    # 0.72 kWh used from a half full battery: the house is covered, no hour is bought
    assert hindsight_costs(rows, 5.0, [4.28], 1.0, 9.0, 3.0, 3.0, ONE, ONE, 0.0) == [pytest.approx(0.0, abs=1e-6)]
    rows = [{"load": -0.03, "buy": 3.0, "sell": 0.0}] * 24
    # an empty battery that has to end 0.72 kWh higher: the sun gives it, nothing is bought
    assert hindsight_costs(rows, 1.0, [1.72], 1.0, 9.0, 3.0, 3.0, ONE, ONE, 0.0) == [pytest.approx(0.0, abs=1e-6)]


def test_a_sale_below_the_minimum_does_not_stop_the_battery_covering_the_house() -> None:
    """min_sell_kwh keeps a step that would sell 0.05 kWh from being planned, but the battery still covers a 0.1 kWh load."""
    rows = [{"load": 0.1, "buy": 3.0, "sell": 0.0}] * 10
    assert hindsight_costs(rows, 5.0, [4.0], 1.0, 9.0, 3.0, 3.0, ONE, ONE, 0.0, min_sell_kwh=0.2) == [pytest.approx(0.0, abs=1e-6)]


def test_a_higher_end_level_costs_the_best_plan_the_energy_it_could_not_use() -> None:
    rows = [{"load": 0.5, "buy": 1.0 if h < 3 else 3.0, "sell": 0.5} for h in range(24)]
    level, higher = hindsight_costs(rows, 1.0, [1.0, 3.0], 1.0, 9.0, 3.0, 3.0, ONE, ONE, 0.0)
    assert higher == pytest.approx(level + 2.0 * 3.0, abs=0.05)  # 2 kWh more bought at the price 3


@pytest.mark.parametrize(
    ("hours", "start", "end", "reason"),
    [
        (_hours(), 1.0, 6.0, "drift"),  # the level jumped by 5 kWh with no flow behind it: a missed hour or a bad sensor
        (_hours(), 1.0, 1.0, None),
    ],
)
def test_a_day_whose_energy_balance_is_off_is_not_scored(hours: list[dict], start: float, end: float, reason: str | None) -> None:
    score, why = _score(hours, start, end)
    assert why == reason
    assert (score is None) == (reason is not None)


def test_a_battery_without_room_is_not_scored() -> None:
    score, why = _score(_hours(), 1.0, 1.0, low=5.0, high=5.0, levels=[5.0])
    assert (score, why) == (None, "battery")


def test_a_big_battery_is_calculated_too() -> None:
    rows = [{"load": 0.5, "buy": 1.0 if h < 6 else 3.0, "sell": 0.5} for h in range(24)]
    assert all(value is not None for value in hindsight_costs(rows, 3.0, [3.0, 6.0], 1.0, 41.0, 5.0, 5.0, ONE, ONE, 0.1))


def test_a_start_level_off_the_step_grid_can_still_reach_the_top() -> None:
    """The level 1.03 is not a whole number of steps above the bottom: the battery is filled exactly, so a day that ends full has a plan."""
    rows = [{"load": 0.5, "buy": 1.0 if h >= 21 else 3.0, "sell": 0.5} for h in range(24)]
    cost = hindsight_costs(rows, 1.03, [9.0], 1.0, 9.0, 3.0, 3.0, ONE, ONE, 0.0)[0]
    assert cost is not None
    # 8 kWh charged at the price 1.0, the house bought at 3.0 for 21 hours of 0.5 and at 1.0 for 3 hours,
    # but the 0.03 kWh the battery starts with above its bottom covers 0.03 kWh of the house at 3.0
    assert cost == pytest.approx(8.0 * 1.0 + 21 * 0.5 * 3.0 + 3 * 0.5 * 1.0 - 0.03 * 3.0, abs=0.01)


def test_a_surplus_that_costs_to_sell_is_stored_and_a_lossy_battery_with_wear_is_scored_fairly() -> None:
    # a fixed production price makes a sale worth less than nothing: the surplus is better in the battery
    rows = [{"load": -0.5, "buy": 3.0, "sell": -1.0}] * 4
    assert hindsight_costs(rows, 1.0, [1.0], 1.0, 9.0, 3.0, 3.0, ONE, ONE, 0.0) == [pytest.approx(0.0, abs=1e-6)]
    # the best plan with losses and wear: 8 kWh stored (8 / 0.9 charged), 7.2 kWh delivered
    lossy = lambda _: 0.9  # noqa: E731
    charge = {0: 3.0, 1: 3.0, 2: 8 / 0.9 - 6.0}
    discharge = {**dict.fromkeys(range(3, 17), 0.5), 17: 0.2}
    score, reason = day_score(_hours(charge=charge, discharge=discharge), 1.0, 1.0, 1.0, 9.0, [1.0], 3.0, 3.0, lossy, lossy, 0.1)
    assert reason is None
    assert score["optimal_raw"] <= score["actual"] + 0.05
    assert _ratio(score) >= 0.97


def test_energy_an_ignored_consumer_took_beyond_what_the_battery_can_refill_is_not_scored() -> None:
    """The battery feeds a car 5 kWh from full, the sun refills 4: the best plan can not net more than the battery holds."""
    hours = _hours(
        charge={9: 2.0, 10: 2.0}, discharge={7: 2.5, 8: 2.5},
        h7={"ignored": 2.0}, h8={"ignored": 2.0}, h9={"solar": 2.5}, h10={"solar": 2.5},
    )
    score, reason = _score(hours, 9.0, 8.0)
    assert (score, reason) == (None, "drift")


def test_the_noise_of_a_day_is_stored_with_its_score() -> None:
    score, _ = _score(_hours(charge=FILL, discharge=dict.fromkeys(range(3, 19), 0.5)))
    # two level steps (8 kWh in 160 steps of 0.05) at the dearest price 3.0
    assert score["noise"] == pytest.approx(2 * 0.05 * 3.0, abs=0.001)


# the recording ---------------------------------------------------------------------------------------------------------


def _record_day(engine, buffers: dict | None = None, skip=(), seed: bool = True, patch=None, before=None) -> dict:
    """Record every hour of DAY like the coordinator does at minute 59 (the day before ended at 20 %) and return the closed day."""
    buffers = buffers or _buffers()
    if seed:
        engine.analysis_db["days"].setdefault((DAY - datetime.timedelta(days=1)).isoformat(), {"final": True, "soc_end": 20.0, "soc_exact": True})
    if patch:
        patch(engine)
    from .test_analysis import _level

    for hour in range(24):
        if hour in skip:
            continue
        if before:
            before(engine, hour)
        now = datetime.datetime.combine(DAY, datetime.time(hour, 59))
        snap = snapshot(now, _level(hour))
        snap.buffers = buffers
        assert engine.run_job(snap, engine.charge_if_needed, False).error is None
        assert engine.run_job(snap, engine.hour_end, now.replace(minute=0)).error is None
    return engine.analysis_db["days"][DAY.isoformat()]


def _engine():
    return started_engine(datetime.datetime.combine(DAY, datetime.time(0, 5)), 20.0)


def test_a_complete_day_is_scored_and_published_with_the_level_neutral_figures() -> None:
    engine = _engine()
    day = _record_day(engine)
    assert "no_score" not in day, day.get("no_score")
    score = day["score"]
    assert score["v"] == SCORE_VERSION and score["optimal_neutral"] <= score["no_battery"] and score["noise"] > 0
    assert engine.analysis_db["hours"][f"{DAY.isoformat()}T05"]["sv"] == SCORE_VERSION
    assert day["soc_exact"] is True


@pytest.mark.parametrize(
    ("label", "kwargs", "reason"),
    [
        ("an hour that was not recorded", {"skip": (5,)}, "hours"),
        ("no level at the start of the day", {"seed": False}, "soc"),
    ],
)
def test_a_day_without_every_hour_or_a_start_level_is_not_scored(label: str, kwargs: dict, reason: str) -> None:
    day = _record_day(_engine(), **kwargs)
    assert "score" not in day, label
    assert day["no_score"] == reason, label


def test_a_lost_consumption_sensor_hour_is_not_a_measurement() -> None:
    buffers = _buffers()
    # the sensor is unavailable from the start of hour 7 until hour 8
    start = datetime.datetime.combine(DAY, datetime.time(0), tzinfo=TZ)
    lost = (start + datetime.timedelta(hours=7)).timestamp()
    buffers["sensor.consumption"] = [(ts, None if ts == lost else value) for ts, value in buffers["sensor.consumption"]]
    engine = _engine()
    day = _record_day(engine, buffers)
    assert engine.analysis_db["hours"][f"{DAY.isoformat()}T07"]["gap"] is True
    assert "gap" not in engine.analysis_db["hours"][f"{DAY.isoformat()}T08"]
    assert day["no_score"] == "gap" and "score" not in day


def test_an_estimated_price_is_not_recorded_as_the_price_of_the_hour() -> None:
    engine = _engine()

    def patch(engine) -> None:
        real = engine.real_hour_price
        engine.real_hour_price = lambda hour, sell_prices=False: None if hour.hour == 9 else real(hour, sell_prices)

    day = _record_day(engine, patch=patch)
    assert engine.analysis_db["hours"][f"{DAY.isoformat()}T09"]["buy"] is None
    assert day["no_score"] == "prices"


def test_the_battery_level_is_never_the_fail_safe_value() -> None:
    engine = _engine()
    original = engine.get_battery_level_or_none

    def patch(engine) -> None:
        engine.get_battery_level_or_none = lambda stale_ok=True: None if engine.now().hour == 23 else original(stale_ok)

    day = _record_day(engine, patch=patch)
    assert engine.analysis_db["hours"][f"{DAY.isoformat()}T23"]["soc"] is None  # not 100
    assert day["soc_exact"] is False and day["no_score"] == "soc"


def test_a_charger_on_its_own_meter_is_recorded_for_the_score() -> None:
    engine = _engine()
    engine.config["home"]["own_meter_charger_entity_ids"] = ["sensor.charger"]  # what the coordinator finds from the CJP cars
    engine.config["home"]["entity_ids"]["ignore_consumption_from_entity_ids"] = ["sensor.ignored"]
    buffers = _buffers()
    start = datetime.datetime.combine(DAY, datetime.time(0), tzinfo=TZ)
    buffers["sensor.charger"] = [((start + datetime.timedelta(hours=h)).timestamp(), 1000.0 if h == 12 else 0.0) for h in range(-1, 24)]
    buffers["sensor.ignored"] = [((start + datetime.timedelta(hours=h)).timestamp(), 500.0) for h in range(-1, 24)]
    _record_day(engine, buffers)
    hours = engine.analysis_db["hours"]
    assert hours[f"{DAY.isoformat()}T12"]["own"] == pytest.approx(1.0, abs=0.01)  # 1 kW for the hour
    assert "own" not in hours[f"{DAY.isoformat()}T05"]  # an ignored consumer is in "ignored", not "own"
    assert hours[f"{DAY.isoformat()}T05"]["ignored"] == pytest.approx(0.5, abs=0.01)


def test_the_days_of_the_clock_change_have_23_and_25_hours() -> None:
    engine = _engine()
    hours = engine._local_day_hours
    assert (hours("2026-03-29", TZ), hours("2026-10-25", TZ), hours(DAY.isoformat(), TZ)) == (23, 25, 24)
    assert len(engine._hour_keys("2026-03-29", TZ)) == 23
    # the repeated hour is one key: the day is not scored, since one hour of energy is lost
    assert len(engine._hour_keys("2026-10-25", TZ)) == 24


def test_an_hour_recorded_without_the_score_stamp_makes_the_day_unscored() -> None:
    """Hours recorded by an older version (prices of the :00 slot of any source, the fail-safe level) are not trusted."""
    engine = _engine()
    day = _record_day(engine, before=lambda engine, hour: engine.analysis_db["hours"][f"{DAY.isoformat()}T05"].pop("sv") if hour == 23 else None)
    assert day["no_score"] == "version" and "score" not in day


def test_an_hour_before_the_first_state_of_a_sensor_is_not_a_measurement() -> None:
    """A buffer that starts inside the hour holds its first state backwards in time: 100 points, but the hour is not measured."""
    buffers = _buffers()
    start = datetime.datetime.combine(DAY, datetime.time(0), tzinfo=TZ)
    first = (start + datetime.timedelta(hours=7, minutes=40)).timestamp()
    # the first state of the battery power is at 07:40; nothing before it
    buffers["sensor.battery_power"] = [(first, 0.0), *[(ts, value) for ts, value in buffers["sensor.battery_power"] if ts > first]]
    engine = _engine()
    day = _record_day(engine, buffers)
    assert engine.analysis_db["hours"][f"{DAY.isoformat()}T07"]["gap"] is True
    assert day["no_score"] == "gap"


def test_a_battery_level_held_from_before_a_lost_sensor_is_not_a_measurement() -> None:
    engine = _engine()
    now = datetime.datetime.combine(DAY, datetime.time(10, 59))
    snap = snapshot(now, "unavailable")
    stamp = lambda minutes: (datetime.datetime.combine(DAY, datetime.time(10, 59), tzinfo=TZ) - datetime.timedelta(minutes=minutes)).timestamp()  # noqa: E731
    snap.buffers = {"sensor.battery_level": [(stamp(30), 50.0), (stamp(10), None)]}
    assert engine.run_job(snap, engine.get_battery_level_or_none).value == 50.0  # the plan may use the held level
    assert engine.run_job(snap, engine.get_battery_level_or_none, False).value is None  # the score may not


# the real hourly price ----------------------------------------------------------------------------------------------------


def test_the_hourly_price_is_the_mean_of_its_slots_and_only_real_prices() -> None:
    engine = _engine()
    hour = datetime.datetime.combine(DAY, datetime.time(10))
    slots = [hour + datetime.timedelta(minutes=15 * i) for i in range(4)]
    prices = {**engine.sell_price_cache["prices"], **dict(zip(slots, (1.0, 2.0, 3.0, 4.0)))}
    engine.get_sell_prices = lambda update_prices=False: prices  # the cached prices, as the hour end reads them
    cache = engine.sell_price_cache
    cache.update(sources={**cache["sources"], **dict.fromkeys(slots, "energidataservice:real")}, using_offline_prices=False)
    snap = snapshot(datetime.datetime.combine(DAY, datetime.time(10, 59)), 20.0)

    def price() -> float | None:
        return engine.run_job(snap, engine.real_hour_price, hour, True).value

    assert price() == pytest.approx(2.5)  # the mean of the four quarters, not the :00 one (1.0)
    # one slot that is a forecast: the hour is not a measured price
    cache["sources"] = {**cache["sources"], slots[2]: "energidataservice:forecast"}
    assert price() is None
    # a price filled from the history
    cache["sources"] = {**cache["sources"], slots[2]: "database"}
    assert price() is None
    # a slot without a price
    cache["sources"] = {**cache["sources"], slots[2]: "energidataservice:real"}
    del prices[slots[2]]
    assert price() is None
    prices[slots[2]] = 3.0
    assert price() == pytest.approx(2.5)
    # the offline prices are an estimate
    cache["using_offline_prices"] = True
    assert price() is None


# the published score ---------------------------------------------------------------------------------------------------


def test_the_published_score_uses_possible_at_the_start_level_and_counts_the_days_without_one() -> None:
    engine = _engine()
    days = engine.analysis_db["days"]
    day = lambda back: (DAY - datetime.timedelta(days=back)).isoformat()  # noqa: E731
    # 8 possible, the plan lost 2 against the best plan that ends where the day ended: 6 saved
    days[day(1)] = {"final": True, "score": {"actual": 10.0, "no_battery": 18.0, "optimal": 8.0, "optimal_neutral": 10.0, "v": SCORE_VERSION}}
    # no more possible than the noise of the model: no score of its own, but it counts in the 30 days
    days[day(2)] = {"final": True, "score": {"actual": 5.0, "no_battery": 5.2, "optimal": 4.9, "optimal_neutral": 4.9, "noise": 0.5, "v": SCORE_VERSION}}
    days[day(3)] = {"final": True, "score": {"actual": 9.0, "no_battery": 9.0, "optimal": 9.0, "v": 2}}
    days[day(4)] = {"final": True, "no_score": "gap"}
    days[day(5)] = {"final": True, "no_score": "gap"}
    days[day(6)] = {"final": True}
    result = engine.run_job(snapshot(datetime.datetime.combine(DAY, datetime.time(0, 5)), 50.0), engine.publish_analysis)
    analysis = result.attributes["analysis"]
    rows = {row["date"]: row for row in analysis["scores"]}
    assert (rows[day(1)]["saved"], rows[day(1)]["possible"], rows[day(1)]["score"]) == (6.0, 8.0, 0.75)
    assert rows[day(2)]["score"] is None
    # an older model: shown by its own formula and marked, not a day without a score, not in the 30 days
    assert rows[day(3)] == {"date": day(3), "saved": 0.0, "possible": 0.0, "score": None, "old": True}
    assert "old" not in rows[day(1)]
    assert analysis["score_missing"] == {"gap": 2, "history": 1}
    assert analysis["score_30"] == pytest.approx((6.0 + (0.3 - 0.1)) / (8.0 + 0.3), abs=0.01)
    assert analysis["score_days"] == 2


def test_a_score_of_an_older_model_is_shown_marked_by_its_own_formula_outside_the_30_days() -> None:
    engine = _engine()
    days = engine.analysis_db["days"]
    day = lambda back: (DAY - datetime.timedelta(days=back)).isoformat()  # noqa: E731
    days[day(1)] = {"final": True, "score": {"actual": 10.0, "no_battery": 18.0, "optimal": 8.0, "optimal_neutral": 10.0, "v": SCORE_VERSION}}
    days[day(2)] = {"final": True, "score": {"actual": 12.0, "no_battery": 20.0, "optimal": 10.0, "v": 2}}  # (20-12)/(20-10)
    days[day(3)] = {"final": True, "score": {"actual": 7.0, "no_battery": 9.0, "optimal": 5.0}}  # version 1 had no "v"
    days[day(4)] = {"final": True, "score": {"actual": 7.0, "no_battery": None, "optimal": 5.0, "v": 2}}  # not its numbers
    days[day(5)] = {"final": True, "no_score": "version"}  # hours of an older version: no score at all
    # a model with optimal_neutral that is no longer the current one: its own formula (8 possible, 2 lost: 6 saved)
    days[day(6)] = {"final": True, "score": {"actual": 10.0, "no_battery": 18.0, "optimal": 8.0, "optimal_neutral": 10.0, "noise": 0.1, "v": SCORE_VERSION - 1}}
    days[day(7)] = {"final": True, "score": {"actual": 10.0, "no_battery": 18.0, "optimal": 8.0, "v": SCORE_VERSION}}  # current without its numbers
    result =engine.run_job(snapshot(datetime.datetime.combine(DAY, datetime.time(0, 5)), 50.0), engine.publish_analysis)
    analysis = result.attributes["analysis"]
    rows = {row["date"]: row for row in analysis["scores"]}
    assert rows[day(2)] == {"date": day(2), "saved": 8.0, "possible": 10.0, "score": 0.8, "old": True}
    assert rows[day(3)] == {"date": day(3), "saved": 2.0, "possible": 4.0, "score": 0.5, "old": True}
    assert rows[day(6)] == {"date": day(6), "saved": 6.0, "possible": 8.0, "score": 0.75, "old": True}
    assert day(4) not in rows and day(5) not in rows and day(7) not in rows
    assert analysis["score_missing"] == {"version": 3}
    # only the current model counts: 6 saved of 8 possible
    assert analysis["score_30"] == pytest.approx(0.75)
    assert analysis["score_days"] == 1
