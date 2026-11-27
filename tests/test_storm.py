"""Storm warning: the window of the warning entity, the level to keep and how the optimiser and the rules keep it."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.battmind.engine.optimizer import (
    OptimizerConfig,
    OptimizerHour,
    carry_floor,
    enforce_max_age,
    enforce_storm_floor,
    storm_holds,
    optimize,
    optimize_per_day,
    optimize_with_rules,
    simulate,
)
from custom_components.battmind.engine.storm import (
    STORM_MAX_BUFFER,
    STORM_MIN_BUFFER,
    STORM_RULE,
    STATUS_ACTIVE,
    STATUS_COMING,
    STATUS_EXPIRED,
    STATUS_INVALID,
    STATUS_NONE,
    STATUS_NO_START,
    STATUS_OTHER,
    STATUS_UNAVAILABLE,
    STATUS_UNREADABLE,
    STATUS_WEAK,
    StormWindow,
    forecast_buffer,
    parse_alert,
    storm_plan,
)

from .test_engine import DAY

TZ = ZoneInfo("Europe/Copenhagen")
NOW = datetime.datetime.combine(DAY, datetime.time(10, 0))  # naive local, like the engine's clock


def at(hours: int) -> datetime.datetime:
    return datetime.datetime.combine(DAY, datetime.time(0)) + datetime.timedelta(hours=hours)


def local_iso(moment: datetime.datetime) -> str:
    """ISO 8601 with the offset of Europe/Copenhagen, like MeteoAlarm's attributes."""
    return moment.replace(tzinfo=TZ).isoformat()


def alert(onset=None, expires=None, level="3; orange; Severe", kind="1; wind", **extra) -> dict:
    attributes = {"awareness_level": level, "awareness_type": kind, **extra}
    if onset is not None:
        attributes["onset"] = onset
    if expires is not None:
        attributes["expires"] = expires
    return attributes


def parse(attributes, state="on", **kwargs) -> StormWindow:
    return parse_alert(state, attributes, NOW, TZ, **kwargs)


# --- the warning entity ---------------------------------------------------------------------------


def test_a_warning_that_starts_later_is_a_window_ahead() -> None:
    window = parse(alert(local_iso(at(18)), local_iso(at(30))))
    assert (window.status, window.start, window.end, window.level, window.kind, window.end_from) == (STATUS_COMING, at(18), at(30), 3, 1, "entity")
    assert window.planned


def test_a_warning_that_has_started_is_active() -> None:
    window = parse(alert(local_iso(at(8)), local_iso(at(20))))
    assert window.status == STATUS_ACTIVE and window.start == at(8) and window.planned


def test_a_warning_without_a_start_time_does_nothing() -> None:
    """Energy Pilot's bug: a warning without an onset charged to 100 % at once. Here it is only shown in the status."""
    for attributes in (alert(expires=local_iso(at(30))), alert(None, None), alert("", local_iso(at(30)))):
        window = parse(attributes)
        assert window.status == STATUS_NO_START and not window.planned and window.start is None


def test_a_start_time_that_is_not_a_time_is_no_start_time() -> None:
    for onset in ("tomorrow", "2026-13-45T99:00", 12345, True, ["x"]):
        assert parse(alert(onset, local_iso(at(30)))).status == STATUS_NO_START


def test_effective_is_not_a_start_time() -> None:
    """effective is when the message was issued, not when the weather begins."""
    window = parse(alert(None, local_iso(at(30)), effective=local_iso(at(11))))
    assert window.status == STATUS_NO_START


def test_the_offset_of_the_attribute_is_converted_to_local_time() -> None:
    utc = datetime.datetime(DAY.year, DAY.month, DAY.day, 16, 0, tzinfo=datetime.timezone.utc)
    window = parse(alert(utc.isoformat().replace("+00:00", "Z"), (utc + datetime.timedelta(hours=6)).isoformat()))
    offset = TZ.utcoffset(datetime.datetime.combine(DAY, datetime.time(18)))
    assert window.start == datetime.datetime(DAY.year, DAY.month, DAY.day, 16, 0) + offset
    assert window.end == window.start + datetime.timedelta(hours=6)


def test_a_warning_without_an_end_time_covers_the_set_number_of_hours() -> None:
    window = parse(alert(local_iso(at(18))), cover_hours=12.0)
    assert window.status == STATUS_COMING and window.end == at(30) and window.end_from == "hours"
    assert parse(alert(local_iso(at(18))), cover_hours=6.0).end == at(24)
    # the number entity is limited to 1-72 h, and a number that is no number is the default of 12 h
    assert parse(alert(local_iso(at(18))), cover_hours=500.0).end == at(18) + datetime.timedelta(hours=72)
    assert parse(alert(local_iso(at(18))), cover_hours=float("nan")).end == at(30)


def test_a_warning_that_has_ended_or_ends_before_it_starts_is_ignored() -> None:
    assert parse(alert(local_iso(at(2)), local_iso(at(9)))).status == STATUS_EXPIRED
    assert parse(alert(local_iso(at(20)), local_iso(at(14)))).status == STATUS_INVALID


def test_only_a_storm_at_or_above_the_chosen_level_counts() -> None:
    onset, expires = local_iso(at(18)), local_iso(at(30))
    assert parse(alert(onset, expires, level="2; yellow; Moderate")).status == STATUS_WEAK
    assert parse(alert(onset, expires, level="2; yellow; Moderate"), min_level=2).status == STATUS_COMING
    assert parse(alert(onset, expires, level="4; red; Extreme")).status == STATUS_COMING
    # forest fire (8), rain (10) and others are not a storm for the battery
    assert parse(alert(onset, expires, kind="8; forest-fire")).status == STATUS_OTHER
    assert parse(alert(onset, expires, kind="3; thunderstorm")).status == STATUS_COMING
    assert parse(alert(onset, expires, kind="2; snow-ice")).status == STATUS_COMING


def test_a_level_or_a_type_that_cannot_be_read_is_ignored_not_taken_for_a_storm() -> None:
    onset, expires = local_iso(at(18)), local_iso(at(30))
    assert parse(alert(onset, expires, level="orange")).status == STATUS_UNREADABLE
    assert parse(alert(onset, expires, kind="wind")).status == STATUS_UNREADABLE
    assert parse({"onset": onset, "expires": expires}).status == STATUS_UNREADABLE


def test_the_severity_word_is_the_level_without_an_awareness_level() -> None:
    onset, expires = local_iso(at(18)), local_iso(at(30))
    attributes = {"severity": "Severe", "awareness_type": "1; wind", "onset": onset, "expires": expires}
    assert parse(attributes).status == STATUS_COMING and parse(attributes).level == 3
    assert parse({**attributes, "severity": "Minor"}).status == STATUS_WEAK


def test_no_warning_and_an_unavailable_entity_do_nothing() -> None:
    assert parse(alert(local_iso(at(18)), local_iso(at(30))), state="off").status == STATUS_NONE
    for state in (None, "unavailable", "unknown"):
        assert parse(alert(local_iso(at(18)), local_iso(at(30))), state=state).status == STATUS_UNAVAILABLE
    assert parse(None).status == STATUS_UNREADABLE  # on, but nothing to read


# --- the buffer -----------------------------------------------------------------------------------


def errors(days: int, relative) -> dict:
    return {"days": days, "ema": {"relative": relative}}


def test_the_buffer_is_the_measured_error_and_at_least_twenty_percent() -> None:
    assert forecast_buffer(errors(30, 0.35), "ema", 7) == (0.35, "measured")
    assert forecast_buffer(errors(30, 0.05), "ema", 7) == (STORM_MIN_BUFFER, "measured")
    assert forecast_buffer(errors(30, 3.0), "ema", 7) == (STORM_MAX_BUFFER, "measured")


def test_an_error_that_is_not_known_is_never_taken_as_none() -> None:
    """Too few days, no errors, or a type without a relative error: the minimum, said to be the default."""
    for known in (errors(3, 0.35), None, {}, errors(30, None), errors(30, float("nan")), errors(30, -0.2), {"days": 30}):
        assert forecast_buffer(known, "ema", 7) == (STORM_MIN_BUFFER, "default")
    assert forecast_buffer(errors(30, 0.35), "trend", 7) == (STORM_MIN_BUFFER, "default")


# --- the level to keep ----------------------------------------------------------------------------


def plan_hours(count: int = 24, **changes) -> list[OptimizerHour]:
    return [OptimizerHour(at(hour), 2.0, 1.0, 1.0, 0.0, **changes) for hour in range(count)]


def make_plan(window, hours=None, consumption=None, buffer=0.2, efficiency=0.9, size=10.0, **kwargs):
    hours = hours if hours is not None else plan_hours()
    consumption = consumption if consumption is not None else {hour.timestamp: 1.0 for hour in hours}
    return storm_plan(window, hours, consumption, efficiency, size, 5.0, 100.0, buffer, **kwargs)


def coming(start: int, end: int) -> StormWindow:
    return StormWindow(STATUS_COMING, at(start), at(end), 3, 1, "entity")


def test_the_level_falls_by_the_hour_and_the_hour_before_keeps_all_of_it() -> None:
    plan = make_plan(coming(10, 14))
    # 4 kWh * 1.2 / 0.9 = 5.33 kWh = 53.3 % of a 10 kWh battery, above the minimum 5 %
    assert plan.need_kwh == pytest.approx(4.0) and plan.need_percent == pytest.approx(53.33, abs=0.01)
    assert plan.floors[at(9)] == pytest.approx(5.0 + 53.33, abs=0.01)
    assert plan.floors[at(10)] == pytest.approx(5.0 + 3 * 13.333, abs=0.02)
    assert plan.floors[at(11)] == pytest.approx(5.0 + 2 * 13.333, abs=0.02)
    assert plan.floors[at(12)] == pytest.approx(5.0 + 13.333, abs=0.02)
    assert plan.floors[at(13)] == pytest.approx(5.0)
    assert at(8) not in plan.floors and at(14) not in plan.floors
    assert not plan.capped and plan.hours == 5


def test_the_level_is_never_more_than_the_maximum() -> None:
    plan = make_plan(coming(10, 22), consumption={hour.timestamp: 3.0 for hour in plan_hours()})
    assert plan.capped and max(plan.floors.values()) == pytest.approx(100.0)


def test_the_buffer_and_the_loss_are_in_the_need() -> None:
    low, high = make_plan(coming(10, 12), buffer=0.2), make_plan(coming(10, 12), buffer=0.6)
    assert high.need_percent == pytest.approx(low.need_percent * 1.6 / 1.2)
    assert make_plan(coming(10, 12), efficiency=0.8).need_percent == pytest.approx(low.need_percent * 0.9 / 0.8)


def test_an_hour_the_window_covers_a_part_of_counts_that_part() -> None:
    window = StormWindow(STATUS_COMING, at(10) + datetime.timedelta(minutes=30), at(12), 3, 1, "entity")
    plan = make_plan(window)
    assert plan.need_kwh == pytest.approx(1.5)  # half of 10:00, all of 11:00
    assert at(9) in plan.floors  # the hour before the first covered hour keeps all of it


def test_an_active_window_has_no_hour_before_and_keeps_what_is_left() -> None:
    window = StormWindow(STATUS_ACTIVE, at(8), at(13), 3, 1, "entity")
    plan = make_plan(window, hours=plan_hours()[10:])  # the plan starts at 10:00
    assert at(9) not in plan.floors and at(10) in plan.floors
    assert plan.need_kwh == pytest.approx(3.0)  # only 10:00-12:59 is in the plan


def test_hours_without_a_consumption_forecast_count_nothing_but_say_the_need_is_not_complete() -> None:
    hours = plan_hours()
    consumption = {hour.timestamp: 1.0 for hour in hours if hour.timestamp != at(11)}
    consumption[at(12)] = float("nan")
    plan = make_plan(coming(10, 14), consumption=consumption)
    assert plan.need_kwh == pytest.approx(2.0)  # 10:00 and 13:00 only
    assert not plan.complete  # the need is at least this much: missing data is shown, never taken as no need
    assert make_plan(coming(10, 14), consumption={}).floors == {}  # no forecast at all: no level to keep
    assert not make_plan(coming(10, 14), consumption={}).complete
    assert make_plan(coming(10, 14)).complete


def test_hours_of_the_window_without_a_price_still_count_in_the_need() -> None:
    """The plan's hours are the priced ones; the forecast has every hour. A window that runs past the last priced hour keeps
    the energy of those hours in the level of the hours before them."""
    hours = plan_hours(count=12)  # the plan ends at 11:59
    consumption = {at(hour): 1.0 for hour in range(24)}  # but the forecast goes on
    plan = make_plan(coming(10, 16), hours=hours, consumption=consumption)
    assert plan.need_kwh == pytest.approx(6.0) and plan.complete
    per_kwh = 1.2 / 0.9 / 10.0 * 100.0  # buffer and loss, % of a 10 kWh battery
    assert plan.floors[at(9)] == pytest.approx(5.0 + 6.0 * per_kwh, abs=0.01)
    assert plan.floors[at(11)] == pytest.approx(5.0 + 4.0 * per_kwh, abs=0.01)  # 12:00-15:59 is still to come after the last priced hour
    # a forecast that ends too: the rest is missing and said so
    assert not make_plan(coming(10, 40), hours=hours, consumption={at(hour): 1.0 for hour in range(24)}).complete


def test_a_window_that_is_not_planned_or_a_battery_that_is_not_there_keep_nothing() -> None:
    assert make_plan(StormWindow(STATUS_NO_START)).floors == {}
    assert make_plan(coming(10, 14), size=0.0).floors == {}
    assert make_plan(coming(10, 14), hours=[]).floors == {}
    assert make_plan(coming(40, 50)).floors == {}  # starts after the plan


# --- the optimiser --------------------------------------------------------------------------------

CFG = OptimizerConfig(battery_size=10.0, level_min=5.0, level_max=100.0, max_charge=3.0, max_discharge=3.0, efficiency=0.95, wear=0.0)


def cfg_with(**changes) -> OptimizerConfig:
    return OptimizerConfig(**{**CFG.__dict__, **changes})


def cheap_night(count: int = 12) -> list[OptimizerHour]:
    """Hours 0-1 cost 0.5, then 3.0: the plan charges at night when it has something to keep the battery for."""
    return [OptimizerHour(at(hour), 0.5 if hour < 2 else 3.0, 0.4 if hour < 2 else 2.0, 0.5, 0.0) for hour in range(count)]


def with_floor(hours: list[OptimizerHour], floors: dict[int, float]) -> list[OptimizerHour]:
    for hour, floor in floors.items():
        hours[hour].floor = floor
    return hours


def test_the_battery_is_charged_up_to_the_level_before_the_storm() -> None:
    plain = optimize(cheap_night(), 20.0, CFG, 0.0)
    hours = with_floor(cheap_night(), {7: 70.0})
    kept = optimize(hours, 20.0, CFG, 0.0)
    assert plain.levels[7] < 70.0
    assert kept.levels[7] >= 70.0 - 0.5
    assert "grid_charging" in kept.actions[:2]  # the cheap hours


def test_the_penalty_is_steering_only_and_not_in_the_costs() -> None:
    hours = with_floor(cheap_night(), {7: 99.0})  # more than two cheap hours can charge: a shortfall remains
    result = optimize(hours, 20.0, CFG, 0.0)
    again = simulate(hours, 20.0, CFG, result.actions, 0.0, [None] * len(hours))
    assert result.total == pytest.approx(sum(result.costs))
    assert result.total < 50.0  # real money: the penalty of 1000 per kWh is not in it
    assert again.total == pytest.approx(result.total, abs=0.5)


def test_a_level_that_cannot_be_reached_gives_as_high_a_level_as_possible_and_does_not_fail() -> None:
    hours = with_floor(cheap_night(), {3: 100.0})  # 2 h * 3 kWh at most = 60 % of the battery
    result = optimize(hours, 20.0, CFG, 0.0)
    assert result.actions[0] == "grid_charging" and result.actions[1] == "grid_charging"
    assert result.levels[3] > 70.0  # as high as the two cheap hours can make it
    assert result.converged


def test_nothing_is_sold_under_the_level_to_keep() -> None:
    hours = [OptimizerHour(at(hour), 2.0, 5.0, 0.5, 0.0) for hour in range(6)]  # a sell price that makes selling pay
    cfg = cfg_with(unsupported=frozenset({"grid_charging"}))  # nothing can refill what is sold
    sells = optimize(hours, 80.0, cfg, 0.0)
    assert sum(sells.sold_kwh) > 1.0
    for hour in hours:
        hour.floor = 80.0
    kept = optimize(hours, 80.0, cfg, 0.0)
    # the battery keeps 80 % for the storm: no sale takes it under the level (the house is not served from it either)
    assert sum(kept.sold_kwh) == pytest.approx(0.0, abs=0.01)
    assert min(kept.levels) >= 80.0 - 0.01


def test_the_level_to_keep_goes_before_the_max_age_of_the_energy() -> None:
    hours = with_floor([OptimizerHour(at(hour), 3.0, 1.0, 0.5, 0.0) for hour in range(6)], {2: 60.0})
    for hour in hours[:3]:
        hour.floor_ahead = 60.0  # the storm level is still to come
    cfg = cfg_with(max_age_hours=12.0, unaged=0.0, charged_before=((at(-30), 40.0),))  # 40 % of old energy
    result = optimize(hours, 45.0, cfg, 0.0)
    assert result.levels[2] >= 60.0 - 0.5  # the old energy was held for the storm, not used
    plain = optimize([OptimizerHour(at(hour), 3.0, 1.0, 0.5, 0.0) for hour in range(6)], 45.0, cfg, 0.0)
    assert plain.held_old > 0 or plain.levels[2] < 45.0  # without a storm the old energy is used
    fixed, _, _ = enforce_max_age(hours, 45.0, cfg, ["force_solar_only_charging"] * 6, [None] * 6)
    assert fixed[:3] == ["force_solar_only_charging"] * 3  # the rules' plan holds it as well


def test_old_energy_is_used_as_before_where_the_storm_does_not_need_it() -> None:
    """The storm level goes first only where the storm needs the energy: with more in the battery than the level ahead asks for,
    holding old energy is still not allowed (the user's max. age rule is not switched off for days before a warning)."""
    hours = with_floor([OptimizerHour(at(hour), 3.0, 1.0, 0.5, 0.0) for hour in range(6)], {2: 20.0})
    for hour in hours[:3]:
        hour.floor_ahead = 20.0  # 45 % minus an hour of use is far above it
    cfg = cfg_with(max_age_hours=12.0, unaged=0.0, charged_before=((at(-30), 40.0),))
    fixed, _, _ = enforce_max_age(hours, 45.0, cfg, ["force_solar_only_charging"] * 6, [None] * 6)
    assert fixed[0] == "discharge_allowed"
    assert storm_holds(hours[0], 45.0, cfg) is False
    assert storm_holds(hours[0], 22.0, cfg) is True  # an hour of use would take it under 20 %
    assert storm_holds(OptimizerHour(at(0), 3.0, 1.0, 0.5, 0.0), 22.0, cfg) is False  # no storm level ahead


def test_a_charge_for_the_storm_stays_when_every_rule_is_switched_off() -> None:
    hours = with_floor(cheap_night(), {7: 70.0})
    result, explanations = optimize_with_rules(hours, 20.0, CFG, 0.0, lambda switch: False)
    assert result.converged
    charged = [(action, explanation) for action, explanation in zip(result.actions, explanations) if action == "grid_charging"]
    assert charged and all(explanation["rule"] == STORM_RULE for _, explanation in charged)
    assert result.levels[7] >= 70.0 - 0.5


def test_a_charge_that_is_not_for_the_storm_is_still_forbidden_by_its_switch() -> None:
    hours = cheap_night()  # no storm: the cheap hours are charged for use at 3.0
    result, explanations = optimize_with_rules(hours, 20.0, CFG, 0.0, lambda switch: False)
    assert "grid_charging" not in result.actions
    assert all(explanation is None or explanation["rule"] != STORM_RULE for explanation in explanations)


# --- the rules ------------------------------------------------------------------------------------


def test_the_rules_plan_is_charged_in_the_cheapest_hour_before_the_level_is_missed() -> None:
    hours = with_floor(cheap_night(), {7: 70.0})
    actions, limits = ["force_solar_only_charging"] * 12, [None] * 12
    fixed, fixed_limits, changed = enforce_storm_floor(hours, 20.0, CFG, actions, limits)
    after = simulate(hours, 20.0, CFG, fixed, 0.0, fixed_limits)
    assert after.levels[7] >= 70.0 - 0.01
    assert set(changed) <= {0, 1} and all(now == "grid_charging" for _, now in changed.values())  # the cheap hours
    assert fixed[2:] == actions[2:]


def test_a_part_of_an_hour_is_charged_when_that_is_enough() -> None:
    hours = with_floor(cheap_night(), {7: 28.0})
    actions, limits = ["force_solar_only_charging"] * 12, [None] * 12
    fixed, fixed_limits, changed = enforce_storm_floor(hours, 20.0, CFG, actions, limits)
    assert list(changed) == [0] and fixed_limits[0] is not None and fixed_limits[0] < CFG.max_charge  # not the full rate


def test_a_sale_is_cancelled_when_it_takes_the_battery_under_the_level() -> None:
    hours = [OptimizerHour(at(hour), 2.0, 5.0, 0.5, 0.0) for hour in range(6)]
    hours[3].floor = 60.0
    actions, limits = ["force_solar_only_charging", "force_solar_only_charging", "force_discharge", "force_solar_only_charging", "force_solar_only_charging", "force_solar_only_charging"], [None, None, 3.0, None, None, None]
    cfg = cfg_with(unsupported=frozenset({"grid_charging"}))  # the battery can't be charged, so the sale is what can change
    assert simulate(hours, 62.0, cfg, actions, 0.0, limits).levels[3] < 60.0
    fixed, fixed_limits, changed = enforce_storm_floor(hours, 62.0, cfg, actions, limits)
    assert fixed[2] == "force_solar_only_charging" and changed == {2: ("force_discharge", "force_solar_only_charging")}
    assert simulate(hours, 62.0, cfg, fixed, 0.0, fixed_limits).levels[3] >= 60.0 - 0.01


def test_a_sale_is_kept_when_charging_is_cheaper_than_the_sale_earns() -> None:
    hours = [OptimizerHour(at(hour), 2.0, 5.0, 0.5, 0.0) for hour in range(6)]
    hours[3].floor = 60.0
    actions = ["force_solar_only_charging", "force_solar_only_charging", "force_discharge", "force_solar_only_charging", "force_solar_only_charging", "force_solar_only_charging"]
    fixed, _, changed = enforce_storm_floor(hours, 62.0, CFG, actions, [None, None, 3.0, None, None, None])
    assert fixed[2] == "force_discharge" and 2 not in changed and "grid_charging" in [fixed[0], fixed[1]]


def test_a_level_the_rules_cannot_reach_changes_what_it_can_and_stops() -> None:
    hours = with_floor(cheap_night(count=4), {1: 100.0})
    fixed, fixed_limits, changed = enforce_storm_floor(hours, 20.0, CFG, ["force_solar_only_charging"] * 4, [None] * 4)
    assert fixed[:2] == ["grid_charging"] * 2 and set(changed) == {0, 1}  # both hours charge at the full rate and it is still short
    assert fixed_limits[:2] == [None, None] and simulate(hours, 20.0, CFG, fixed, 0.0, fixed_limits).levels[1] < 100.0


def test_a_plan_that_keeps_the_level_is_not_changed() -> None:
    hours = with_floor(cheap_night(), {5: 30.0})
    actions, limits = ["force_solar_only_charging"] * 12, [None] * 12
    assert enforce_storm_floor(hours, 50.0, CFG, actions, limits) == (actions, limits, {})
    # without any level to keep too
    assert enforce_storm_floor(cheap_night(), 20.0, CFG, actions, limits) == (actions, limits, {})


# --- every day on its own (use_midnight_battery_level) ------------------------------------------------------------


def two_days() -> list[OptimizerHour]:
    return [OptimizerHour(at(hour), 1.0, 0.5, 0.5, 0.0) for hour in range(48)]


def test_a_day_ends_high_enough_for_a_storm_just_after_midnight() -> None:
    """Each day is optimised on its own from the level the day before ends with: without a carry the storm at 01:00 of the second
    day could not be charged for (two hours at 9.5 % are not enough to get to 90 %)."""
    cfg = cfg_with(max_charge=1.0, max_discharge=1.0)
    hours = two_days()
    hours[25].floor = 90.0
    for hour in hours[24:26]:
        hour.storm_until = True
    assert carry_floor(hours[24:], cfg) == pytest.approx(90.0 - 2 * 9.5)
    result, _ = optimize_per_day(hours, 20.0, cfg, 0.0, lambda switch: True)
    assert result.levels[23] >= 71.0 - 0.5
    assert result.levels[25] >= 90.0 - 0.5
    assert result.converged
    assert all(hour.floor is None for hour in hours[:24]), "the carry is only for the optimisation: the day's own hours are left as they were"


def test_a_storm_that_the_next_day_can_reach_on_its_own_asks_nothing_of_the_day_before() -> None:
    cfg = cfg_with(max_charge=1.0, max_discharge=1.0)
    hours = two_days()
    hours[30].floor = 40.0  # six hours of charging are plenty
    assert carry_floor(hours[24:], cfg) is None
    assert carry_floor(two_days()[24:], cfg) is None  # no level to keep at all
    assert carry_floor([], cfg) is None
    assert carry_floor(hours[24:], cfg_with(battery_size=0.0)) is None


# --- what the level caps and what the house takes ---------------------------------------------------------------------


def test_a_sale_that_the_level_caps_is_cut_to_what_may_be_sold() -> None:
    """The simulation caps the export at the level, so the level looks kept: the plan has to list only what may be sold, because the
    plan is what runs (a sale runs at full rate until the next replan)."""
    hours = [OptimizerHour(at(hour), 2.0, 5.0, 0.5, 0.0) for hour in range(4)]
    hours[1].floor = 55.0
    cfg = cfg_with(unsupported=frozenset({"grid_charging"}))
    actions = ["force_solar_only_charging", "force_discharge", "force_solar_only_charging", "force_solar_only_charging"]
    fixed, limits, changed = enforce_storm_floor(hours, 62.0, cfg, actions, [None, 3.0, None, None])
    assert simulate(hours, 62.0, cfg, actions, 0.0, [None, 3.0, None, None]).levels[1] >= 55.0 - 0.2  # the simulation looks kept
    assert changed == {1: ("force_discharge", "force_discharge")}
    assert fixed[1] == "force_discharge" and 0.0 < limits[1] < 0.3  # the plan lists what may be sold, not the 3 kWh
    assert simulate(hours, 62.0, cfg, fixed, 0.0, limits).levels[1] == pytest.approx(55.0, abs=0.3)


def test_house_use_becomes_a_hold_when_the_battery_cannot_be_charged() -> None:
    hours = [OptimizerHour(at(hour), 2.0, 0.5, 0.5, 0.0) for hour in range(6)]
    hours[3].floor = 40.0
    cfg = cfg_with(unsupported=frozenset({"grid_charging"}))
    actions = ["discharge_allowed"] * 6
    assert simulate(hours, 45.0, cfg, actions, 0.0).levels[3] < 40.0
    fixed, limits, changed = enforce_storm_floor(hours, 45.0, cfg, actions, [None] * 6)
    assert set(changed) <= {0, 1, 2, 3} and all(now == "force_solar_only_charging" for _, now in changed.values())
    assert simulate(hours, 45.0, cfg, fixed, 0.0, limits).levels[3] >= 40.0 - 0.01


def test_a_far_away_end_time_is_not_walked_hour_by_hour() -> None:
    from custom_components.battmind.engine.storm import STORM_MAX_HOURS

    window = StormWindow(STATUS_COMING, at(10), at(10) + datetime.timedelta(days=365 * 70), 3, 1, "entity")
    plan = make_plan(window, consumption={hour.timestamp: 1.0 for hour in plan_hours()})
    assert not plan.complete  # the hours after the last one the forecast has are said to be missing
    assert plan.need_kwh <= STORM_MAX_HOURS


def test_a_forecast_of_nothing_is_not_taken_for_no_need() -> None:
    plan = make_plan(coming(10, 14), consumption={hour.timestamp: 0.0 for hour in plan_hours()})
    assert plan.floors == {} and not plan.complete
