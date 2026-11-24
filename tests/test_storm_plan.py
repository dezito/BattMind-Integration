"""Storm warning in the whole plan: the optimiser and the rules keep the level, the status is published and nothing happens without
a start time or without the option."""

from __future__ import annotations

import datetime

import pytest

from unittest.mock import patch

from custom_components.battmind.const import build_config
from custom_components.battmind.engine import BattMindEngine
from custom_components.battmind.engine.storm import STORM_RULE

from .test_engine import DAY, OPTIONS, TEXTS, TZ, _power_values_db
from .test_planner_rules import realistic_snapshot

ENTITY = "binary_sensor.meteoalarm"
NOW = datetime.datetime.combine(DAY, datetime.time(10, 30))
LEVEL = 20.0


def at(hour: int, days: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(DAY, datetime.time(0)) + datetime.timedelta(days=days, hours=hour)


def meteoalarm(start, end=None, level="3; orange; Severe", kind="1; wind", tz=None) -> dict:
    """The attributes of MeteoAlarm's binary sensor (ISO 8601 with an offset)."""
    attributes = {"awareness_level": level, "awareness_type": kind}
    if start is not None:
        attributes["onset"] = start.replace(tzinfo=tz).isoformat()
    if end is not None:
        attributes["expires"] = end.replace(tzinfo=tz).isoformat()
    return attributes


def storm_snapshot(settings=None, alert="coming", **kwargs):
    """The shared plan snapshot with a storm warning: "coming" starts 20:00 and ends 23:00 today."""
    snap = realistic_snapshot(NOW, LEVEL, settings)
    if alert == "coming":
        snap.storm_alert = {"entity_id": ENTITY, "state": "on", "attributes": meteoalarm(at(20), at(23), tz=snap.tz, **kwargs)}
    elif alert is not None:
        snap.storm_alert = alert(snap) if callable(alert) else alert
    return snap


def engine_with_option(option: bool = True) -> BattMindEngine:
    options = {**OPTIONS, "energidataservice_sell_entity_id": "sensor.sell", **({"storm_warning_entity_id": ENTITY} if option else {})}
    engine = BattMindEngine(build_config(options), "test", TEXTS)
    assert engine.run_job(realistic_snapshot(NOW, LEVEL), engine.startup, {"power_values": _power_values_db()}).error is None
    return engine


def planned(snapshot, option: bool = True):
    engine = engine_with_option(option)
    result = engine.run_job(snapshot, engine.charge_if_needed, True)
    assert result.error is None, result.error
    return engine, result


def storm_sessions(engine: BattMindEngine) -> list[dict]:
    return [session for session in engine.charge_hours.values() if isinstance(session, dict) and session.get(STORM_RULE)]


def end_level(engine: BattMindEngine, hour: int) -> float:
    """The plan's level at the end of an hour today (what controls the battery: the optimiser's)."""
    return sum(engine.plan[0]["battery_level_flow"][hour])


@pytest.fixture(scope="module")
def baseline():
    """The same day without a warning."""
    return planned(storm_snapshot(alert=None))


@pytest.fixture(scope="module")
def storm():
    return planned(storm_snapshot())


def test_a_coming_warning_is_published_with_the_level_to_keep(storm) -> None:
    engine, result = storm
    assert result.states["storm_warning"] == "coming"
    attributes = result.attributes["storm_warning"]
    assert attributes["awareness_level"] == 3 and attributes["awareness_type"] == 1
    # timezone aware, like the other sensors that publish a time
    assert attributes["start"] == at(20).replace(tzinfo=TZ).isoformat() and attributes["end"] == at(23).replace(tzinfo=TZ).isoformat()
    assert attributes["end_from"] == "entity" and attributes["need_complete"] is True
    assert attributes["buffer_percent"] == 20 and attributes["buffer_from"] == "default"  # no recorded forecast days in the test
    assert attributes["expected_use_kwh"] > 0.0 and attributes["keep_percent"] > engine.config["solar"]["powerwall_battery_level_min"]
    assert attributes["keep_from"] == at(19).replace(tzinfo=TZ).isoformat()  # the hour before the window keeps all of it
    assert attributes["plan"] == "optimal" and attributes["short_percent"] == 0.0


def test_the_plan_charges_for_the_storm_and_says_so(storm, baseline) -> None:
    engine, result = storm
    keep = result.attributes["storm_warning"]["keep_percent"]
    base_engine, _ = baseline
    assert end_level(base_engine, 19) < keep - 5.0  # without the warning the battery is not that high
    assert end_level(engine, 19) >= keep - 1.0
    assert storm_sessions(engine), "a charge for the storm is marked, so no rule switch can forbid it"
    reasons = [item["reason"] for day in engine.plan.values() if isinstance(day, dict) for item in day["charging_sessions"].values()]
    assert any("🌪️" in reason and "Stormvarsel" in reason for reason in reasons)


def test_the_level_falls_during_the_storm_and_every_hour_keeps_its_own_level(storm) -> None:
    engine, result = storm
    floors = engine.storm_state["plan"].floors
    assert sorted(floors) == [at(19), at(20), at(21), at(22)]
    assert floors[at(19)] > floors[at(20)] > floors[at(21)] > floors[at(22)]  # what is still needed after the hour
    rows = {row["hour"]: row for row in engine.optimizer_shadow["hours"]}
    for hour, floor in floors.items():
        assert rows[hour]["battery_level"] >= floor - 0.1, hour  # a sale above the level is fine, one under it is not


def test_a_warning_without_a_start_time_changes_nothing(baseline) -> None:
    engine, result = planned(storm_snapshot(alert=lambda snap: {"entity_id": ENTITY, "state": "on", "attributes": meteoalarm(None, at(23), tz=snap.tz)}))
    base_engine, _ = baseline
    assert result.states["storm_warning"] == "no_start"
    assert result.attributes["storm_warning"]["keep_percent"] is None
    assert engine.charge_hours == base_engine.charge_hours
    assert [hour["action"] for hour in engine.optimizer_shadow["hours"]] == [hour["action"] for hour in base_engine.optimizer_shadow["hours"]]


@pytest.mark.parametrize(
    ("state", "attributes", "status"),
    [
        ("off", {}, "no_warning"),
        ("unavailable", {}, "entity_unavailable"),  # not "unavailable": that is Home Assistant's own state
        ("on", {"awareness_level": "2; yellow; Moderate", "awareness_type": "1; wind", "onset": at(20).isoformat()}, "below_level"),
        ("on", {"awareness_level": "3; orange; Severe", "awareness_type": "8; forest-fire", "onset": at(20).isoformat()}, "other_type"),
        ("on", {"onset": at(20).isoformat()}, "unreadable"),
    ],
)
def test_a_warning_that_does_not_count_is_shown_and_ignored(state, attributes, status, baseline) -> None:
    engine, result = planned(storm_snapshot(alert={"entity_id": ENTITY, "state": state, "attributes": attributes}))
    assert result.states["storm_warning"] == status
    assert engine.charge_hours == baseline[0].charge_hours


def test_a_warning_after_the_plan_is_later() -> None:
    far = at(20, days=20)
    engine, result = planned(storm_snapshot(alert=lambda snap: {"entity_id": ENTITY, "state": "on", "attributes": meteoalarm(far, far + datetime.timedelta(hours=3), tz=snap.tz)}))
    assert result.states["storm_warning"] == "later"


def test_without_the_option_nothing_is_made_or_published(baseline) -> None:
    engine, result = planned(storm_snapshot(alert=None), option=False)
    assert "storm_warning" not in result.states and "storm_warning" not in result.attributes
    assert engine.charge_hours == baseline[0].charge_hours
    # a snapshot that has an alert but an engine without the option reads none of it (the snapshot only has one with the option)
    assert getattr(engine, "storm_state", None) is None


def test_the_rules_keep_the_level_when_the_optimiser_does_not_control_the_battery(baseline) -> None:
    rules_baseline, _ = planned(storm_snapshot({"optimizer_control": False}, alert=None))
    engine, result = planned(storm_snapshot({"optimizer_control": False}))
    keep = result.attributes["storm_warning"]["keep_percent"]
    assert result.attributes["storm_warning"]["plan"] == "rules"
    assert end_level(rules_baseline, 19) < keep - 5.0
    assert end_level(engine, 19) >= keep - 1.0
    assert storm_sessions(engine)
    assert result.attributes["storm_warning"]["short_percent"] == 0.0


def test_the_warning_is_part_of_what_replans_the_plan() -> None:
    engine = engine_with_option()
    hashes = {
        name: engine.run_job(snapshot, engine.plan_inputs_hash).value
        for name, snapshot in (("none", storm_snapshot(alert=None)), ("coming", storm_snapshot()), ("stronger", storm_snapshot(level="4; red; Extreme")))
    }
    assert len(set(hashes.values())) == 3


def test_a_level_that_cannot_be_reached_is_charged_as_high_as_possible_and_says_how_far_short() -> None:
    """A storm one hour from now that needs more than the battery can charge in that time."""
    snap = realistic_snapshot(NOW, 5.0)
    soon = at(12)
    snap.storm_alert = {"entity_id": ENTITY, "state": "on", "attributes": meteoalarm(soon, soon + datetime.timedelta(hours=20), tz=snap.tz)}
    engine, result = planned(snap)
    attributes = result.attributes["storm_warning"]
    assert result.states["storm_warning"] == "coming"
    assert attributes["short_percent"] > 0.0 and attributes["keep_percent"] is not None
    assert result.error is None
    assert end_level(engine, 11) > 5.0  # it charges what it can in the one hour there is


def test_the_rules_replace_what_an_hour_already_charges_and_the_totals_follow() -> None:
    """With 15 minute prices an hour has one session per period: the storm's charge of the hour replaces them, it is not added to them."""
    snap = storm_snapshot({"optimizer_control": False})
    quarters = realistic_snapshot(NOW, LEVEL, {"optimizer_control": False}, quarters=True)
    quarters.storm_alert = snap.storm_alert
    engine, result = planned(quarters)
    assert result.states["storm_warning"] == "coming"
    sessions = {ts: session for ts, session in engine.charge_hours.items() if isinstance(ts, datetime.datetime)}
    per_hour: dict = {}
    for ts, session in sessions.items():
        per_hour.setdefault(ts.replace(minute=0), []).append(session)
    storm_hours = [hour for hour, items in per_hour.items() if any(item.get(STORM_RULE) for item in items)]
    assert storm_hours
    for hour in storm_hours:
        assert len(per_hour[hour]) == 1, "the storm's session is the hour's only one"
    plan_cost = sum(day["total_cost"] for day in engine.plan.values() if isinstance(day, dict))
    assert plan_cost == pytest.approx(sum(session["Cost"] for session in sessions.values()), abs=0.05)
    # the charged kWh in the plan's sessions are the ones the battery levels are built from
    assert end_level(engine, 19) >= result.attributes["storm_warning"]["keep_percent"] - 1.0


def test_hours_after_the_last_price_are_in_the_need_and_a_missing_forecast_is_said() -> None:
    """The window runs past the days the plan covers: what the forecast has counts, and the status says the need is not complete."""
    days = 2
    options = {**OPTIONS, "energidataservice_sell_entity_id": "sensor.sell", "storm_warning_entity_id": ENTITY, "days_to_predict": days}
    engine = BattMindEngine(build_config(options), "test", TEXTS)
    assert engine.run_job(realistic_snapshot(NOW, LEVEL), engine.startup, {"power_values": _power_values_db()}).error is None
    snap = realistic_snapshot(NOW, LEVEL)
    start = at(20)
    snap.storm_alert = {"entity_id": ENTITY, "state": "on", "attributes": meteoalarm(start, start + datetime.timedelta(hours=60), tz=snap.tz)}
    result = engine.run_job(snap, engine.charge_if_needed, True)
    assert result.error is None, result.error
    assert result.attributes["storm_warning"]["need_complete"] is False
    assert result.attributes["storm_warning"]["expected_use_kwh"] > 0.0


def test_a_new_installation_without_consumption_history_does_not_take_the_missing_forecast_for_no_need() -> None:
    """The planner gives an hour it has no history for 0 kWh. A level of 0 % needed would be a favourable default: it is said not to be complete."""
    engine = BattMindEngine(build_config({**OPTIONS, "energidataservice_sell_entity_id": "sensor.sell", "storm_warning_entity_id": ENTITY}), "test", TEXTS)
    assert engine.run_job(realistic_snapshot(NOW, LEVEL), engine.startup, {}).error is None  # no power_values database at all
    result = engine.run_job(storm_snapshot(), engine.charge_if_needed, True)
    assert result.error is None, result.error
    attributes = result.attributes["storm_warning"]
    assert result.states["storm_warning"] == "coming"
    assert attributes["need_complete"] is False


def test_the_rules_keep_the_level_when_the_optimiser_fails() -> None:
    """If the whole period optimisation raises, the rules control the battery - and they still keep the storm level."""
    rules_baseline, _ = planned(storm_snapshot({"optimizer_control": False}, alert=None))
    engine = engine_with_option()
    with patch.object(BattMindEngine, "apply_optimizer_plan", side_effect=RuntimeError("boom")):
        result = engine.run_job(storm_snapshot(), engine.charge_if_needed, True)
    assert result.error is None, result.error
    keep = result.attributes["storm_warning"]["keep_percent"]
    assert end_level(rules_baseline, 19) < keep - 5.0
    assert end_level(engine, 19) >= keep - 1.0
    assert storm_sessions(engine)


def test_the_hour_that_is_going_on_may_not_export_under_its_level() -> None:
    """A planned sale runs at full rate until the next replan: charge_if_needed stops it at the level of the hour."""
    now = datetime.datetime.combine(DAY, datetime.time(21, 30))
    engine = engine_with_option()
    snap = realistic_snapshot(now, 60.0)
    snap.storm_alert = {"entity_id": ENTITY, "state": "on", "attributes": meteoalarm(at(20), at(23), tz=snap.tz)}
    assert engine.run_job(snap, engine.charge_if_needed, True).error is None
    floor = engine.run_job(snap, engine.storm_floor_now).value
    assert floor > engine.config["solar"]["powerwall_battery_level_min"]  # 22:00 still needs energy after the hour
    # outside the window nothing is asked
    early = realistic_snapshot(NOW, 60.0)
    early.storm_alert = snap.storm_alert
    assert engine.run_job(early, engine.charge_if_needed, True).error is None
    assert engine.run_job(early, engine.storm_floor_now).value == 0.0
