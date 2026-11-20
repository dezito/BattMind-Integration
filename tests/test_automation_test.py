"""The test of the user's automation: the verdict of a step, the held action, the measuring, the runner and its texts."""
from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from custom_components.battmind.const import AUTOMATION_TEST_STATUSES, build_config
from custom_components.battmind.engine import BattMindEngine
from custom_components.battmind.engine.analysis.plan_check import judge_hour
from custom_components.battmind.engine.automation_test import (
    AUTOMATION_TEST_ACTIONS,
    LOAD_W,
    MOVE_W,
    SETTLE_SECONDS,
    STOPPED_W,
    WINDOW_SECONDS,
    build_automation_test_markdown,
    judge_step,
    mean_or_none,
)
from custom_components.battmind.management.automation_inputs import actions_without_steps, automations_using_blueprint
from custom_components.battmind.management.dashboard_build import render_dashboard
from custom_components.battmind.utils.formatting import translate

from .conftest import PREFIX
from .test_engine import DAY, OPTIONS, TEXTS, TZ, snapshot, started_engine

LEVEL_MIN, LEVEL_MAX, MARGIN, DISCHARGE_LIMIT = 10.0, 100.0, 2.0, 5000.0
TEXT_DIR = Path(__file__).parents[1] / "custom_components" / "battmind" / "texts"


def _sample(charge=0.0, discharge=0.0, load=1000.0, solar=0.0, ignored=0.0, level=50.0, use=None) -> dict:
    return {"charge": charge, "discharge": discharge, "load": load, "use": load - ignored if use is None else use,
            "ignored": ignored, "solar": solar, "level": level}


IDLE = _sample()  # what the battery did before the step: nothing
CHARGING, DISCHARGING = _sample(charge=3000.0), _sample(discharge=3000.0)


def _judge(action: str, sample: dict, before: dict | None = None) -> dict:
    """before: what the battery did just before the step (the opposite of what the step asks, unless a test says else)."""
    if before is None:
        before = {"battery_stopped": DISCHARGING, "force_solar_only_charging": DISCHARGING, "blocked_solar_charge": CHARGING}.get(action, IDLE)
    return judge_step(action, sample, LEVEL_MIN, LEVEL_MAX, MARGIN, before, DISCHARGE_LIMIT)


def _skip(verdict: dict, reason: str) -> None:
    assert verdict["verdict"] == "skip" and verdict["reason"] == reason, verdict


# --- the verdict of one measurement -------------------------------------------------------------------------------


def test_grid_charging_passes_when_the_battery_charges_more_than_the_sun_could() -> None:
    assert _judge("grid_charging", _sample(charge=3000.0))["verdict"] == "ok"
    # 2 kW surplus from the sun, 3 kW charged: 1 kW can only be from the grid
    assert _judge("grid_charging", _sample(charge=3000.0, load=500.0, solar=2500.0))["verdict"] == "ok"


def test_grid_charging_that_the_sun_alone_explains_proves_nothing() -> None:
    _skip(_judge("grid_charging", _sample(charge=2000.0, load=500.0, solar=2500.0)), "unproven")


def test_grid_charging_that_did_not_charge_fails_and_a_full_battery_is_not_tested() -> None:
    assert _judge("grid_charging", _sample(charge=MOVE_W - 1))["verdict"] == "fail"
    _skip(_judge("grid_charging", _sample(level=LEVEL_MAX - MARGIN)), "limit")


def test_forced_discharge_must_discharge_more_than_the_house_uses() -> None:
    assert _judge("force_discharge", _sample(discharge=4000.0, load=800.0))["verdict"] == "ok"
    assert _judge("force_discharge", _sample(discharge=100.0, load=800.0))["verdict"] == "fail"
    # the house load alone explains it
    _skip(_judge("force_discharge", _sample(discharge=900.0, load=800.0)), "unproven")
    _skip(_judge("force_discharge", _sample(level=LEVEL_MIN + MARGIN)), "limit")


def test_standing_still_only_counts_when_the_battery_moved_just_before() -> None:
    _skip(_judge("battery_stopped", _sample(), before=IDLE), "no_baseline")
    _skip(_judge("battery_stopped", _sample(), before=_sample(charge=MOVE_W - 1)), "no_baseline")
    assert _judge("battery_stopped", _sample(), before=CHARGING)["verdict"] == "ok"
    assert _judge("battery_stopped", _sample())["verdict"] == "ok"
    assert _judge("battery_stopped", _sample(discharge=STOPPED_W))["verdict"] == "fail"
    assert _judge("battery_stopped", _sample(charge=3000.0))["verdict"] == "fail"
    _skip(_judge("battery_stopped", _sample(level=LEVEL_MAX)), "limit")
    _skip(_judge("battery_stopped", _sample(level=LEVEL_MIN)), "limit")


def test_standing_still_needs_a_load_or_a_surplus_that_normal_operation_would_move_the_battery_for() -> None:
    """Steps that only cancel the forced mode would leave a battery idle too when the sun covers the house."""
    _skip(_judge("battery_stopped", _sample(load=1000.0, solar=1000.0)), "no_load")
    _skip(_judge("battery_stopped", _sample(load=1000.0, solar=1200.0)), "no_load")  # 200 W surplus is not enough
    assert _judge("battery_stopped", _sample(load=1000.0, solar=1000.0 + LOAD_W))["verdict"] == "ok"  # a surplus to charge from
    assert _judge("battery_stopped", _sample(load=LOAD_W, solar=0.0))["verdict"] == "ok"  # a load to discharge for


def test_a_battery_sensor_that_is_quiet_at_0_watt_proves_no_stop_but_still_shows_no_charging() -> None:
    quiet = {**_sample(load=1000.0), "battery_stale": True}
    _skip(_judge("battery_stopped", quiet), "no_data")
    _skip(_judge("force_solar_only_charging", quiet), "no_data")
    _skip(_judge("blocked_solar_charge", {**_sample(load=500.0, solar=2000.0), "battery_stale": True}), "no_data")
    assert _judge("grid_charging", quiet)["verdict"] == "fail"  # it would have reported a change


def test_discharge_allowed_needs_a_house_load_above_the_sun_and_a_battery_that_did_not_discharge_before() -> None:
    _skip(_judge("discharge_allowed", _sample(load=LOAD_W - 1)), "no_load")
    _skip(_judge("discharge_allowed", _sample(load=1000.0, solar=900.0)), "no_load")
    assert _judge("discharge_allowed", _sample(load=1000.0, discharge=900.0))["verdict"] == "ok"
    assert _judge("discharge_allowed", _sample(load=1000.0, discharge=0.0))["verdict"] == "fail"
    _skip(_judge("discharge_allowed", _sample(load=1000.0, level=LEVEL_MIN)), "limit")
    # a forced discharge that is still running: it passes only if the automation did nothing
    _skip(_judge("discharge_allowed", _sample(load=1000.0, discharge=900.0), before=DISCHARGING), "no_baseline")
    # discharging more than the house uses is not normal operation
    _skip(_judge("discharge_allowed", _sample(load=1000.0, discharge=3000.0)), "unproven")
    # the blueprint runs the action for a charging car instead
    _skip(_judge("discharge_allowed", _sample(load=2500.0, ignored=2000.0, discharge=900.0)), "ev")


def test_a_battery_that_can_only_discharge_a_little_is_judged_on_what_it_can() -> None:
    # 4 kW load but the inverter discharges 500 W at most: 300 W is what half of its limit asks for
    verdict = judge_step("discharge_allowed", _sample(load=4000.0, discharge=300.0), LEVEL_MIN, LEVEL_MAX, MARGIN, IDLE, 500.0)
    assert verdict["verdict"] == "ok"
    assert judge_step("discharge_allowed", _sample(load=4000.0, discharge=50.0), LEVEL_MIN, LEVEL_MAX, MARGIN, IDLE, 500.0)["verdict"] == "fail"


def test_solar_charging_only_checks_that_a_battery_that_discharged_stops() -> None:
    assert _judge("force_solar_only_charging", _sample(load=1000.0, discharge=0.0))["verdict"] == "ok"
    assert _judge("force_solar_only_charging", _sample(load=1000.0, discharge=700.0))["verdict"] == "fail"
    _skip(_judge("force_solar_only_charging", _sample(load=1000.0, solar=1000.0)), "no_load")  # no load to see it not discharge
    _skip(_judge("force_solar_only_charging", _sample(load=1000.0, level=LEVEL_MIN)), "limit")
    # it did not discharge before: standing still proves nothing
    _skip(_judge("force_solar_only_charging", _sample(load=1000.0), before=IDLE), "no_baseline")


def test_blocked_solar_charging_needs_a_surplus_no_car_and_a_battery_that_charged_before() -> None:
    assert _judge("blocked_solar_charge", _sample(load=500.0, solar=3000.0, charge=0.0))["verdict"] == "ok"
    assert _judge("blocked_solar_charge", _sample(load=500.0, solar=3000.0, charge=1500.0))["verdict"] == "fail"
    _skip(_judge("blocked_solar_charge", _sample(load=1000.0, solar=0.0)), "no_surplus")
    _skip(_judge("blocked_solar_charge", _sample(load=500.0, solar=3000.0, level=LEVEL_MAX)), "limit")
    # the blueprint runs its EV action instead while the car charges
    _skip(_judge("blocked_solar_charge", _sample(load=2500.0, solar=3000.0, ignored=2000.0)), "ev")
    # the larger load counts: the surplus is smaller than it looks
    _skip(_judge("blocked_solar_charge", _sample(load=500.0, use=2800.0, solar=3000.0)), "no_surplus")
    # an automation that does nothing leaves a stopped battery stopped, which looks like "does not charge"
    _skip(_judge("blocked_solar_charge", _sample(load=500.0, solar=3000.0), before=IDLE), "no_baseline")


def test_a_step_that_follows_a_step_that_left_the_battery_as_it_was_proves_nothing() -> None:
    """The chain the reviewer found: stop did nothing, so discharge-allowed, solar-only and blocked all 'pass' on the old state."""
    standing = _sample(load=1000.0, solar=0.0)
    for action in ("battery_stopped", "force_solar_only_charging", "blocked_solar_charge"):
        assert _judge(action, standing, before=IDLE)["verdict"] == "skip", action


def test_an_action_without_a_test_is_an_error() -> None:
    with pytest.raises(ValueError):
        _judge("something_else", _sample())


def test_a_window_without_enough_numbers_has_no_mean() -> None:
    assert mean_or_none([10.0] * 100) == 10.0
    assert mean_or_none([10.0] * 89) is None
    assert mean_or_none([]) is None


def test_the_hour_of_a_test_is_never_judged_by_the_plan_check() -> None:
    # 40 minutes of grid charging in the plan and 15 minutes of test: the battery moved because of the test
    entry = judge_hour({"grid_charging": 2400.0, "test": 900.0}, {"charge": 0.0, "discharge": 0.0, "home": 1.0, "solar": 0.0}, 50.0, 10.0, 100.0)
    assert entry == {"a": "grid_charging", "ok": None, "skip": "test"}
    assert judge_hour({"test": 3600.0}, {}, 50.0, 10.0, 100.0)["skip"] == "test"


# --- the held action ----------------------------------------------------------------------------------------------


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(DAY, datetime.time(hour, minute, second))


def _ts(moment: datetime.datetime) -> float:
    return moment.replace(tzinfo=TZ).timestamp()


def _snap(now: datetime.datetime, battery_w: float | None = 0.0, consumption_w: float | None = 1000.0, level: float = 50.0, **extra):
    """A snapshot where the sensors have shown the same number for the last hour (None: the sensor has no number)."""
    snap = snapshot(now, level)
    before = _ts(now) - 3600
    snap.entity_states.update({"sensor.battery_power": str(battery_w), "sensor.consumption": str(consumption_w), **extra.pop("states", {})})
    snap.buffers = {
        "sensor.battery_power": [(before, battery_w)],
        "sensor.consumption": [(before, consumption_w)],
        **extra.pop("buffers", {}),
    }
    snap.reported = {"sensor.battery_power": _ts(now) - 5, **extra.pop("reported", {})}
    return snap


def _run(engine, now, func, *args, **snap_kwargs):
    result = engine.run_job(_snap(now, **snap_kwargs), func, *args)
    assert result.error is None, result.error
    return result


def _engine():
    engine = started_engine(_at(12, 30), 50.0)
    _run(engine, _at(12, 30), engine.charge_if_needed, True)
    return engine


def test_a_test_can_start_when_battmind_has_planned_and_the_sensors_have_numbers() -> None:
    engine = started_engine(_at(12, 30), 50.0)
    assert _run(engine, _at(12, 30), engine.automation_test_begin).value == {"ok": False, "reason": "not_ready", "unsupported": []}
    _run(engine, _at(12, 30), engine.charge_if_needed, True)
    assert _run(engine, _at(12, 31), engine.automation_test_begin).value["ok"] is True
    # a battery level sensor without a number is not read as a full battery
    assert _run(engine, _at(12, 31), engine.automation_test_begin, states={"sensor.battery_level": "unavailable"}).value["reason"] == "no_level"


def test_a_test_lists_the_actions_the_inverter_cannot_run() -> None:
    engine = BattMindEngine(build_config({**OPTIONS, "unsupported_actions": ["blocked_solar_charge"]}), "test", TEXTS)
    _run(engine, _at(12, 30), engine.startup, {})
    _run(engine, _at(12, 30), engine.charge_if_needed, True)
    assert _run(engine, _at(12, 31), engine.automation_test_begin).value["unsupported"] == ["blocked_solar_charge"]


def test_the_held_action_is_published_instead_of_the_plan_and_survives_every_calculation() -> None:
    engine = _engine()
    real = engine.last_powerwall_action
    held = "force_discharge" if real != "force_discharge" else "grid_charging"
    assert _run(engine, _at(12, 40), engine.automation_test_hold, held, 900).value == "ok"
    for minute in (45, 50):  # the cron, a recalculation: they all publish the held action
        result = _run(engine, _at(12, minute), engine.charge_if_needed, minute == 50)
        assert result.states["powerwall_action"] == held
        assert "🧪" in result.states["current_charging_rule"]
    assert engine.last_powerwall_action == real  # what the plan decided is kept
    released = _run(engine, _at(12, 55), engine.automation_test_release)
    assert released.states["powerwall_action"] == real
    assert engine._automation_hold is None


def test_the_held_action_ends_by_itself_and_never_outlives_its_time() -> None:
    engine = _engine()
    real = engine.last_powerwall_action
    held = "force_discharge" if real != "force_discharge" else "grid_charging"
    _run(engine, _at(12, 40), engine.automation_test_hold, held, 60)
    assert _run(engine, _at(12, 40, 59), engine.charge_if_needed, False).states["powerwall_action"] == held
    result = _run(engine, _at(12, 41, 1), engine.charge_if_needed, False)
    assert result.states["powerwall_action"] == real
    assert engine._automation_hold is None and engine._automation_abort == "expired"


def test_the_rules_of_the_planner_are_not_credited_with_what_the_test_moves() -> None:
    engine = _engine()
    engine.session_rules = {}
    _run(engine, _at(12, 40), engine.automation_test_hold, "grid_charging", 900)
    _run(engine, _at(12, 45), engine.charge_if_needed, False)
    assert engine.session_rules == {}


def test_the_time_of_the_test_counts_as_test_time_in_the_plan_check() -> None:
    engine = _engine()
    _run(engine, _at(13, 0), engine.plan_check_note, "discharge_allowed")
    _run(engine, _at(13, 5), engine.automation_test_hold, "grid_charging", 600)
    _run(engine, _at(13, 10), engine.automation_test_release)
    _run(engine, _at(13, 15), engine.charge_if_needed, False)
    assert engine._plan_tally["hours"][f"{DAY.isoformat()}T13"].get("test", 0) >= 300.0


def _temperature_engine():
    options = {**OPTIONS, "battery_temperature_entity_id": "sensor.battery_temperature", "battery_temperature_min": 0, "battery_temperature_max": 50}
    engine = BattMindEngine(build_config(options), "test", TEXTS)
    engine.run_job(_snap(_at(12, 30), states={"sensor.battery_temperature": "20"}), engine.startup, {})
    return engine


def test_a_battery_that_is_stopped_for_temperature_is_never_tested() -> None:
    engine = _temperature_engine()
    _run(engine, _at(12, 30), engine.charge_if_needed, True, states={"sensor.battery_temperature": "60"})
    assert engine.last_powerwall_action == "battery_stopped"
    assert _run(engine, _at(12, 31), engine.automation_test_begin, states={"sensor.battery_temperature": "60"}).value["reason"] == "battery_stopped"
    assert _run(engine, _at(12, 31), engine.automation_test_hold, "grid_charging", 300, states={"sensor.battery_temperature": "60"}).value == "battery_stopped"
    assert engine._automation_hold is None


def test_a_battery_that_gets_too_hot_during_the_test_ends_the_hold_at_once() -> None:
    engine = _temperature_engine()
    _run(engine, _at(12, 30), engine.charge_if_needed, True, states={"sensor.battery_temperature": "20"})
    assert _run(engine, _at(12, 31), engine.automation_test_hold, "grid_charging", 300, states={"sensor.battery_temperature": "20"}).value == "ok"
    result = _run(engine, _at(12, 35), engine.charge_if_needed, False, states={"sensor.battery_temperature": "60"})
    assert result.states["powerwall_action"] == "battery_stopped"
    assert engine._automation_hold is None and engine._automation_abort == "battery_stopped"
    out = _run(engine, _at(12, 36), engine.automation_test_sample, states={"sensor.battery_temperature": "60"}).value
    assert out["held"] is False and out["abort"] == "battery_stopped" and out["sample"] is None


# --- the measuring ------------------------------------------------------------------------------------------------


def _hold(engine, action="grid_charging", at=_at(12, 40)):
    _run(engine, at, engine.automation_test_hold, action, 300)
    return at


def test_nothing_is_measured_until_the_action_has_settled() -> None:
    engine = _engine()
    started = _hold(engine)
    for seconds in (5, SETTLE_SECONDS + WINDOW_SECONDS - 1):
        out = _run(engine, started + datetime.timedelta(seconds=seconds), engine.automation_test_sample).value
        assert out["sample"] is None and out["why"] == "settling" and out["held"] is True
    out = _run(engine, started + datetime.timedelta(seconds=SETTLE_SECONDS + WINDOW_SECONDS), engine.automation_test_sample).value
    assert out["sample"] is not None


def test_the_battery_power_is_read_with_its_sign_and_the_house_load_without_the_battery() -> None:
    # positive is discharging (the default): 2 kW out of the battery
    engine = _engine()
    started = _hold(engine)
    later = started + datetime.timedelta(seconds=60)
    sample = _run(engine, later, engine.automation_test_sample, battery_w=2000.0, consumption_w=2500.0).value["sample"]
    assert sample["discharge"] == 2000.0 and sample["charge"] == 0.0
    # the consumption sensor does not include the battery discharging: the house used 2.5 kW + 2 kW
    assert sample["load"] == 2500.0 + 2000.0
    sample = _run(engine, later, engine.automation_test_sample, battery_w=-3000.0).value["sample"]
    assert sample["charge"] == 3000.0 and sample["discharge"] == 0.0


def test_an_inverted_battery_power_sensor_is_read_the_other_way() -> None:
    engine = BattMindEngine(build_config({**OPTIONS, "invert_powerwall_watt_flow_entity_id": True}), "test", TEXTS)
    engine.run_job(snapshot(_at(12, 30), 50.0), engine.startup, {})
    _run(engine, _at(12, 30), engine.charge_if_needed, True)
    started = _hold(engine)
    sample = _run(engine, started + datetime.timedelta(seconds=60), engine.automation_test_sample, battery_w=3000.0).value["sample"]
    assert sample["charge"] == 3000.0 and sample["discharge"] == 0.0


@pytest.mark.parametrize("lost", ["sensor.battery_power", "sensor.consumption"])
def test_a_sensor_without_numbers_gives_no_measurement_never_a_standing_still_battery(lost) -> None:
    engine = _engine()
    started = _hold(engine)
    later = started + datetime.timedelta(seconds=60)
    kwargs = {"battery_w": None} if lost == "sensor.battery_power" else {"consumption_w": None}
    out = _run(engine, later, engine.automation_test_sample, **kwargs).value
    assert out["sample"] is None and out["why"] == "no_data"


def test_a_sensor_that_was_lost_in_the_middle_of_the_window_is_not_enough() -> None:
    engine = _engine()
    started = _hold(engine)
    later = started + datetime.timedelta(seconds=60)
    rows = [(_ts(later) - 3600, 0.0), (_ts(later) - 25, None)]  # lost for the last 25 of the 30 seconds
    out = _run(engine, later, engine.automation_test_sample, buffers={"sensor.battery_power": rows}).value
    assert out["sample"] is None and out["why"] == "no_data"


@pytest.mark.parametrize(("sensor", "watt"), [("sensor.battery_power", -3000.0), ("sensor.consumption", 1000.0)])
def test_a_sensor_that_stopped_reporting_is_not_believed(sensor, watt) -> None:
    """A frozen 3 kW battery or a frozen house load would make any verdict up."""
    engine = _engine()
    started = _hold(engine)
    later = started + datetime.timedelta(seconds=60)
    kwargs = {"battery_w": watt} if sensor == "sensor.battery_power" else {"consumption_w": watt}
    out = _run(engine, later, engine.automation_test_sample, reported={sensor: _ts(later) - 600}, **kwargs).value
    assert out["sample"] is None and out["why"] == "no_data"
    out = _run(engine, later, engine.automation_test_sample, reported={sensor: _ts(later) - 20}, **kwargs).value
    assert out["sample"] is not None and not out["sample"]["battery_stale"]


def test_a_battery_sensor_that_is_quiet_at_0_watt_is_measured_but_marked() -> None:
    """A template sensor reports only changes: a battery that idles for an hour must not make the whole test 🔧."""
    engine = _engine()
    started = _hold(engine)
    later = started + datetime.timedelta(seconds=60)
    out = _run(engine, later, engine.automation_test_sample, battery_w=0.0, reported={"sensor.battery_power": _ts(later) - 3000}).value
    assert out["sample"] is not None and out["sample"]["battery_stale"] is True


def test_a_solar_sensor_that_stopped_reporting_is_not_read_as_no_sun() -> None:
    engine = _engine()
    engine.config["solar"]["entity_ids"]["production_entity_id"] = "sensor.solar"
    started = _hold(engine)
    later = started + datetime.timedelta(seconds=60)
    buffers = {"sensor.solar": [(_ts(later) - 3600, 0.0)]}
    out = _run(engine, later, engine.automation_test_sample, buffers=buffers, reported={"sensor.solar": _ts(later) - 900}).value
    assert out["sample"] is None and out["why"] == "no_data"
    out = _run(engine, later, engine.automation_test_sample, buffers=buffers, reported={"sensor.solar": _ts(later) - 30}).value
    assert out["sample"]["solar"] == 0.0
    # in the dark a solar sensor at 0 does not report for hours, and that is right
    with patch.object(engine, "_automation_dark", return_value=True):
        out = _run(engine, later, engine.automation_test_sample, buffers=buffers, reported={"sensor.solar": _ts(later) - 9000}).value
    assert out["sample"]["solar"] == 0.0
    buffers = {"sensor.solar": [(_ts(later) - 3600, 2000.0)]}  # but not one that shows the sun at night
    with patch.object(engine, "_automation_dark", return_value=True):
        out = _run(engine, later, engine.automation_test_sample, buffers=buffers, reported={"sensor.solar": _ts(later) - 9000}).value
    assert out["sample"] is None


def test_the_dark_is_from_the_sun_events() -> None:
    engine = _engine()
    assert _run(engine, _at(2), engine._automation_dark, _at(2)).value is True
    assert _run(engine, _at(12), engine._automation_dark, _at(12)).value is False


def test_the_level_of_a_lost_level_sensor_is_not_a_full_battery() -> None:
    engine = _engine()
    started = _hold(engine)
    out = _run(engine, started + datetime.timedelta(seconds=60), engine.automation_test_sample, states={"sensor.battery_level": "unavailable"}).value
    assert out["sample"] is None and out["why"] == "no_data"


def test_a_measurement_before_any_step_needs_no_hold() -> None:
    engine = _engine()
    out = _run(engine, _at(12, 40), engine.automation_test_sample, False, battery_w=-1500.0).value
    assert out["held"] is False and out["sample"]["charge"] == 1500.0
    assert _run(engine, _at(12, 40), engine.automation_test_sample).value["sample"] is None  # held=True needs a hold


# --- the texts ----------------------------------------------------------------------------------------------------


def _texts(language: str) -> dict[str, str]:
    texts: dict[str, str] = {}

    def flatten(data: dict, prefix: str = "") -> None:
        for key, value in data.items():
            if isinstance(value, dict):
                flatten(value, f"{prefix}{key}.")
            else:
                texts[f"{prefix}{key}"] = value

    flatten(json.loads((TEXT_DIR / f"{language}.json").read_text(encoding="utf-8")))
    return texts


STEP_RESULTS = (
    [{"action": a, "status": "ok", "reason": None, "seconds": 70, "charge": 3000, "discharge": 4000, "deficit": 650, "surplus": 900} for a in AUTOMATION_TEST_ACTIONS]
    + [{"action": a, "status": "fail", "reason": None, "seconds": 300, "charge": 0, "discharge": 0, "deficit": 650, "surplus": 900} for a in AUTOMATION_TEST_ACTIONS]
    + [{"action": a, "status": "skip", "reason": r, "seconds": 0} for a in AUTOMATION_TEST_ACTIONS
       for r in ("unsupported", "limit", "no_load", "no_surplus", "unproven", "ev", "no_baseline", "no_data", "not_reached", "battery_stopped", "expired", "not_published", "no_steps")]
)


@pytest.mark.parametrize("language", ["da", "de", "en", "pl", "uk"])
def test_every_text_of_the_result_exists_in_every_language(language) -> None:
    texts = _texts(language)

    def t(key, **fmt):
        assert key in texts, f"{language}: {key}"
        return translate(texts, key, **fmt)

    for status in AUTOMATION_TEST_STATUSES:
        for reason in (None, "battery_stopped", "expired", "stopped", "internal_error", "not_configured", "not_ready", "no_level"):
            markdown = build_automation_test_markdown(t, {"status": status, "steps": STEP_RESULTS, "reason": reason})
            assert "{" not in markdown and "ui.automation_test" not in markdown
    assert "ui.automation_test" not in build_automation_test_markdown(t, {"status": "idle", "steps": [], "reason": None})


def test_the_placeholders_of_a_text_are_the_same_in_every_language() -> None:
    import re

    english = {key: value for key, value in _texts("en").items() if key.startswith("ui.automation_test.")}
    assert len(english) > 40
    for language in ("da", "de", "pl", "uk"):
        texts = _texts(language)
        for key, value in english.items():
            assert key in texts, f"{language}: {key}"
            assert sorted(re.findall(r"{(\w+)}", texts[key])) == sorted(re.findall(r"{(\w+)}", value)), f"{language}: {key}"


def test_the_markdown_has_a_row_per_action_and_tells_what_was_not_tested() -> None:
    steps = [
        {"action": "grid_charging", "status": "ok", "reason": None, "seconds": 70, "charge": 3100, "discharge": 0, "deficit": 0, "surplus": 0},
        {"action": "discharge_allowed", "status": "fail", "reason": None, "seconds": 300, "charge": 0, "discharge": 0, "deficit": 800, "surplus": 0},
        {"action": "blocked_solar_charge", "status": "skip", "reason": "no_surplus", "seconds": 0},
    ]
    markdown = build_automation_test_markdown(translate_with(TEXTS), {"status": "done", "steps": steps, "reason": None})
    rows = [line for line in markdown.splitlines() if line.startswith("| ") and "---" not in line][1:]
    assert [row.split(" | ")[0] for row in rows] == ["| ✅", "| ❌", "| 🔧"]
    assert "3100" in rows[0] and "solcelleoverskud" in rows[2]
    assert "1 af 3" in markdown  # one of three worked (the Danish summary)
    assert "EV" in rows[1]  # the car may have held the battery


def translate_with(texts):
    return lambda key, **fmt: translate(texts, key, **fmt)


# --- the runner, with the real entities ----------------------------------------------------------------------------


@pytest.fixture
def fast_runner():
    """No waiting between measurements and a step that gives up after a moment."""
    with patch("custom_components.battmind.coordinator.automation_test.SAMPLE_SECONDS", 0), \
            patch("custom_components.battmind.coordinator.automation_test.STEP_TIMEOUT", 0.3):
        yield


def _battery(responses: dict | None = None, idle: dict | None = None, calls: list | None = None):
    """A stand-in for automation_test_sample: what the battery does for the action that is held."""
    base = {"charge": 0.0, "discharge": 0.0, "load": 650.0, "use": 650.0, "ignored": 0.0, "solar": 0.0, "level": 50.0}
    answers = {
        "grid_charging": {"charge": 3000.0},
        "force_discharge": {"discharge": 4000.0},
        "battery_stopped": {},
        "discharge_allowed": {"discharge": 650.0},
        "force_solar_only_charging": {},
        "blocked_solar_charge": {},
        **(responses or {}),
    }

    def sample(self, held: bool = True) -> dict:
        hold = self._automation_hold
        out = {"held": hold is not None, "abort": self._automation_abort, "sample": None, "why": None}
        if held and hold is None:
            return out
        action = hold["action"] if hold else None
        if calls is not None and held and action:
            calls.append(action)
        out["sample"] = {**base, **(idle or {}), **answers.get(action, {})}
        return out

    return sample


async def _finished(hass) -> None:
    """Wait until the test has ended (the release of the action runs on the worker thread, which the HA waits do not see)."""
    for _ in range(1000):
        await hass.async_block_till_done(wait_background_tasks=True)
        if hass.states.get(f"sensor.{PREFIX}_automation_test").state != "running":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the test did not end")


async def _start(hass, entry) -> None:
    await hass.services.async_call("switch", "turn_on", {"entity_id": f"switch.{PREFIX}_automation_test_run"}, blocking=True)
    await _finished(hass)


def _sensor(hass):
    return hass.states.get(f"sensor.{PREFIX}_automation_test")


async def test_a_test_runs_every_step_and_puts_the_real_action_back(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data
    real = hass.states.get(f"sensor.{PREFIX}_powerwall_action").state
    held: list[str] = []
    with patch.object(type(coordinator.engine), "automation_test_sample", _battery(calls=held)):
        await _start(hass, loaded_entry)
    state = _sensor(hass)
    assert state.state == "done"
    steps = {step["action"]: step for step in state.attributes["steps"]}
    assert [step["status"] for step in steps.values()] == ["ok", "ok", "ok", "ok", "ok", "skip"]
    assert steps["blocked_solar_charge"]["reason"] == "no_surplus"  # no solar: it can not be proven
    assert "✅" in state.attributes["markdown"] and "🔧" in state.attributes["markdown"]
    # every action that could be proven was held, in the order of the test, and the real one is back
    assert [action for i, action in enumerate(held) if i == 0 or action != held[i - 1]] == list(AUTOMATION_TEST_ACTIONS[:5])
    assert hass.states.get(f"sensor.{PREFIX}_powerwall_action").state == real
    assert coordinator.engine._automation_hold is None
    assert hass.states.get(f"switch.{PREFIX}_automation_test_run").state == "off"


async def test_a_battery_that_does_not_react_is_a_failed_step_with_what_was_seen(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data
    with patch.object(type(coordinator.engine), "automation_test_sample", _battery({"grid_charging": {"charge": 0.0}})):
        await _start(hass, loaded_entry)
    state = _sensor(hass)
    assert state.state == "done"
    first = state.attributes["steps"][0]
    assert first["action"] == "grid_charging" and first["status"] == "fail" and first["charge"] == 0
    assert "❌" in state.attributes["markdown"]


async def test_a_battery_that_never_moved_proves_nothing_by_standing_still(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data
    dead = {"grid_charging": {}, "force_discharge": {}}
    with patch.object(type(coordinator.engine), "automation_test_sample", _battery(dead)):
        await _start(hass, loaded_entry)
    steps = {step["action"]: step for step in _sensor(hass).attributes["steps"]}
    assert steps["grid_charging"]["status"] == "fail" and steps["force_discharge"]["status"] == "fail"
    assert steps["battery_stopped"]["status"] == "skip" and steps["battery_stopped"]["reason"] == "no_baseline"


async def test_a_lost_sensor_is_a_step_that_was_not_tested(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data

    def lost(self, held: bool = True) -> dict:
        return {"held": self._automation_hold is not None, "abort": self._automation_abort, "sample": None, "why": "no_data"}

    with patch.object(type(coordinator.engine), "automation_test_sample", lost):
        await _start(hass, loaded_entry)
    steps = _sensor(hass).attributes["steps"]
    assert all(step["status"] == "skip" and step["reason"] == "no_data" for step in steps)


async def test_the_test_is_stopped_with_the_switch_and_the_real_action_comes_back(hass, loaded_entry) -> None:
    coordinator = loaded_entry.runtime_data
    real = hass.states.get(f"sensor.{PREFIX}_powerwall_action").state
    with patch("custom_components.battmind.coordinator.automation_test.SAMPLE_SECONDS", 0), \
            patch.object(type(coordinator.engine), "automation_test_sample", _battery({"grid_charging": {}})):
        await hass.services.async_call("switch", "turn_on", {"entity_id": f"switch.{PREFIX}_automation_test_run"}, blocking=True)
        for _ in range(500):
            await asyncio.sleep(0.01)
            if coordinator.engine._automation_hold is not None:
                break
        assert coordinator.engine._automation_hold is not None
        assert hass.states.get(f"switch.{PREFIX}_automation_test_run").state == "on"
        await hass.services.async_call("switch", "turn_off", {"entity_id": f"switch.{PREFIX}_automation_test_run"}, blocking=True)
        await _finished(hass)
    state = _sensor(hass)
    assert state.state == "stopped" and state.attributes["reason"] == "stopped"
    assert coordinator.engine._automation_hold is None
    assert hass.states.get(f"sensor.{PREFIX}_powerwall_action").state == real
    assert hass.states.get(f"switch.{PREFIX}_automation_test_run").state == "off"
    assert [step["reason"] for step in state.attributes["steps"]][-1] == "not_reached"


async def test_a_battery_stopped_for_temperature_ends_the_test(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data

    def stopped(self, held: bool = True) -> dict:
        if self._automation_hold is not None:
            self._automation_hold, self._automation_abort = None, "battery_stopped"
        return {"held": False, "abort": "battery_stopped", "sample": None, "why": None}

    sample = _battery()

    def first_then_stopped(self, held: bool = True) -> dict:
        return sample(self, held) if not held else stopped(self, held)

    with patch.object(type(coordinator.engine), "automation_test_sample", first_then_stopped):
        await _start(hass, loaded_entry)
    state = _sensor(hass)
    assert state.state == "stopped" and state.attributes["reason"] == "battery_stopped"
    assert coordinator.engine._automation_hold is None
    assert [step["reason"] for step in state.attributes["steps"]][-1] == "not_reached"


async def test_a_test_does_not_start_while_the_battery_is_stopped_for_temperature(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data
    coordinator.engine.last_powerwall_action = "battery_stopped"
    await _start(hass, loaded_entry)
    state = _sensor(hass)
    assert state.state == "failed" and state.attributes["reason"] == "battery_stopped"
    assert state.attributes["steps"] == []
    assert coordinator.engine._automation_hold is None


async def test_a_second_start_while_a_test_runs_does_nothing(hass, loaded_entry) -> None:
    coordinator = loaded_entry.runtime_data
    with patch("custom_components.battmind.coordinator.automation_test.SAMPLE_SECONDS", 0), \
            patch.object(type(coordinator.engine), "automation_test_sample", _battery({"grid_charging": {}})):
        coordinator.async_run_automation_test()
        task = coordinator._automation_test_task
        coordinator.async_run_automation_test()
        assert coordinator._automation_test_task is task
        coordinator.async_stop_automation_test()
        await _finished(hass)
    assert _sensor(hass).state == "stopped"


async def test_unloading_during_a_test_puts_the_real_action_back_first(hass, loaded_entry) -> None:
    coordinator = loaded_entry.runtime_data
    with patch("custom_components.battmind.coordinator.automation_test.SAMPLE_SECONDS", 0), \
            patch.object(type(coordinator.engine), "automation_test_sample", _battery({"grid_charging": {}})):
        coordinator.async_run_automation_test()
        for _ in range(500):
            await asyncio.sleep(0.01)
            if coordinator.engine._automation_hold is not None:
                break
        engine = coordinator.engine
        assert engine._automation_hold is not None
        from .conftest import async_unload_and_join

        await async_unload_and_join(hass, loaded_entry)
    assert engine._automation_hold is None


async def test_the_last_result_is_restored_after_a_restart_but_a_running_test_is_not(hass, loaded_entry) -> None:
    coordinator = loaded_entry.runtime_data
    steps = [{"action": "grid_charging", "status": "ok", "reason": None, "seconds": 70, "charge": 3000, "discharge": 0, "deficit": 0, "surplus": 0}]
    coordinator.async_restore_automation_test({"status": "running", "steps": steps})
    assert coordinator.automation_test_state["status"] == "idle"
    coordinator.async_restore_automation_test({"status": "done", "steps": steps, "reason": None, "finished": "2026-11-20T10:00:00+01:00"})
    assert coordinator.automation_test_state["status"] == "done"
    assert "✅" in coordinator.automation_test_state["markdown"]
    coordinator.async_restore_automation_test({"status": "done", "steps": "garbage"})
    assert coordinator.automation_test_state["steps"] == steps


@pytest.mark.parametrize("language", ["da", "de", "en", "pl", "uk"])
def test_the_dashboard_shows_the_switch_the_status_and_the_result(language) -> None:
    content = render_dashboard(build_config({**OPTIONS, "language": language}), "mybatt")
    for entity in ("switch.mybatt_automation_test_run", "sensor.mybatt_automation_test"):
        assert entity in content
    assert content.count("sensor.mybatt_automation_test") >= 2  # the status row and the markdown card
    assert "@@" not in content and "battmind_automation_test" not in content
    assert _texts(language)["dashboard.section_automation_test"] in content


async def test_the_entities_exist_with_a_name_in_the_language_of_the_entry(hass, loaded_entry) -> None:
    for entity in (f"switch.{PREFIX}_automation_test_run", f"sensor.{PREFIX}_automation_test"):
        state = hass.states.get(entity)
        assert state is not None and state.state != "unavailable", entity
    assert _sensor(hass).state == "idle" and "markdown" in _sensor(hass).attributes


# --- a calculation that fails, and what the runner asks for -----------------------------------------------------------


def test_the_real_action_comes_back_when_the_calculation_fails_after_the_test() -> None:
    engine = _engine()
    real = engine.last_powerwall_action
    held = "force_discharge" if real != "force_discharge" else "grid_charging"
    assert _run(engine, _at(12, 40), engine.automation_test_hold, held, 900).value == "ok"
    with patch.object(engine, "cheap_grid_charge_hours", side_effect=RuntimeError("prices")):
        result = _run(engine, _at(12, 45), engine.automation_test_release)
    assert result.states["powerwall_action"] == real
    assert engine._automation_hold is None and engine._published_action == real


def test_an_expired_hold_does_not_stay_on_the_sensor_when_the_calculation_fails() -> None:
    engine = _engine()
    real = engine.last_powerwall_action
    held = "force_discharge" if real != "force_discharge" else "grid_charging"
    _run(engine, _at(12, 40), engine.automation_test_hold, held, 60)
    with patch.object(engine, "cheap_grid_charge_hours", side_effect=RuntimeError("prices")):
        result = _run(engine, _at(12, 50), engine.charge_if_needed, False)
    assert result.states["powerwall_action"] == real
    assert engine._automation_hold is None


def test_a_hold_that_did_not_reach_the_sensor_is_reported_and_dropped() -> None:
    engine = _engine()
    real = engine.last_powerwall_action
    held = "force_discharge" if real != "force_discharge" else "grid_charging"
    with patch.object(engine, "cheap_grid_charge_hours", side_effect=RuntimeError("prices")):
        result = _run(engine, _at(12, 40), engine.automation_test_hold, held, 900)
    assert result.value == "not_published"
    assert engine._automation_hold is None


async def test_the_jobs_that_plan_are_run_with_the_forecast(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data
    calls: list[tuple] = []
    original = coordinator.async_run_job

    async def spy(job_name, *args, **kwargs):
        calls.append((job_name, kwargs.get("include_forecast", False)))
        return await original(job_name, *args, **kwargs)

    with patch.object(coordinator, "async_run_job", spy), patch.object(type(coordinator.engine), "automation_test_sample", _battery()):
        await _start(hass, loaded_entry)
    planning = [call for call in calls if call[0] in ("automation_test_hold", "automation_test_release")]
    assert planning and all(forecast for _, forecast in planning), planning


async def test_a_battery_that_reaches_a_limit_while_forced_stops_the_forcing_at_once(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data
    base = _battery()

    def full_when_charging(self, held: bool = True) -> dict:
        out = base(self, held)
        if out["sample"] and self._automation_hold and self._automation_hold["action"] == "grid_charging":
            out["sample"] = {**out["sample"], "level": 100.0}
        return out

    with patch.object(type(coordinator.engine), "automation_test_sample", full_when_charging):
        await _start(hass, loaded_entry)
    first = _sensor(hass).attributes["steps"][0]
    assert first["action"] == "grid_charging" and first["status"] == "skip" and first["reason"] == "limit"
    assert coordinator.engine._automation_hold is None


async def test_a_calculation_that_cannot_publish_the_action_ends_the_test(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data
    with patch.object(type(coordinator.engine), "automation_test_sample", _battery()), \
            patch.object(coordinator.engine, "cheap_grid_charge_hours", side_effect=RuntimeError("prices")):
        await _start(hass, loaded_entry)
    state = _sensor(hass)
    assert state.state == "stopped" and state.attributes["reason"] == "internal_error"
    assert state.attributes["steps"][0]["reason"] == "not_published"
    assert coordinator.engine._automation_hold is None


async def test_a_restored_result_with_a_broken_step_is_not_shown(hass, loaded_entry) -> None:
    coordinator = loaded_entry.runtime_data
    coordinator.async_restore_automation_test({"status": "done", "steps": [{"action": "grid_charging"}]})
    assert coordinator.automation_test_state["status"] == "idle"
    coordinator.async_restore_automation_test({"status": "done", "steps": ["garbage"]})
    assert coordinator.automation_test_state["status"] == "idle"
    # a number that is not one (a state set by hand) would raise in the markdown while the entity is added
    for key in ("charge", "discharge", "deficit", "surplus", "seconds"):
        step = {"action": "grid_charging", "status": "ok", "reason": None, "seconds": 70, "charge": 3000, key: "lots"}
        coordinator.async_restore_automation_test({"status": "done", "steps": [step]})
        assert coordinator.automation_test_state["status"] == "idle", key


async def test_the_automation_not_found_note_survives_a_restart(hass, loaded_entry) -> None:
    coordinator = loaded_entry.runtime_data
    steps = [{"action": "grid_charging", "status": "ok", "reason": None, "seconds": 70, "charge": 3000, "discharge": 0}]
    coordinator.async_restore_automation_test({"status": "done", "steps": steps, "automation": "unknown"})
    assert coordinator.automation_test_state["automation"] == "unknown"
    assert "automations.yaml" in coordinator.automation_test_state["markdown"]
    coordinator.async_restore_automation_test({"status": "done", "steps": steps, "automation": "made up"})
    assert coordinator.automation_test_state["automation"] is None


def test_a_test_that_ended_before_its_first_step_shows_no_table_and_no_count() -> None:
    markdown = build_automation_test_markdown(translate_with(TEXTS), {"status": "failed", "steps": [], "reason": "battery_stopped"})
    assert "|" not in markdown and "0 af 0" not in markdown
    assert "temperaturen" in markdown  # why it did not start


# --- which actions the automation has steps for ----------------------------------------------------------------------


SENSOR = f"sensor.{PREFIX}_powerwall_action"
STEPS = [{"action": "light.turn_on"}]


def _automations_file(tmp_path, automations) -> str:
    import yaml

    path = tmp_path / "automations.yaml"
    path.write_text(yaml.safe_dump(automations), encoding="utf-8")
    return str(path)


def _use(inputs: dict, path: str = "battmind/battery_control.yaml") -> dict:
    return {"alias": "battery", "use_blueprint": {"path": path, "input": inputs}}


def test_the_inputs_of_the_automation_of_this_action_sensor_are_found(tmp_path) -> None:
    file = _automations_file(tmp_path, [
        _use({"battmind_action": SENSOR, "grid_charging": STEPS}),
        _use({"battmind_action": "sensor.other_powerwall_action", "force_discharge": STEPS}),
        _use({"battmind_action": SENSOR, "force_discharge": STEPS}, path="someone/else.yaml"),
        {"alias": "not a blueprint", "actions": []},
    ])
    found = automations_using_blueprint(file, SENSOR)
    assert found == [{"battmind_action": SENSOR, "grid_charging": STEPS}]
    assert actions_without_steps(found, AUTOMATION_TEST_ACTIONS) == set(AUTOMATION_TEST_ACTIONS) - {"grid_charging"}


def test_an_automation_that_is_not_found_means_nothing_is_known_not_that_nothing_has_steps(tmp_path) -> None:
    assert automations_using_blueprint(str(tmp_path / "missing.yaml"), SENSOR) == []
    assert automations_using_blueprint(_automations_file(tmp_path, [{"alias": "x"}]), SENSOR) == []
    broken = tmp_path / "broken.yaml"
    broken.write_text("- [unclosed", encoding="utf-8")
    assert automations_using_blueprint(str(broken), SENSOR) == []
    assert actions_without_steps([], AUTOMATION_TEST_ACTIONS) == set()


def test_an_action_counts_as_having_steps_when_any_automation_of_the_sensor_has_them() -> None:
    inputs = [{"grid_charging": STEPS}, {"force_discharge": STEPS, "grid_charging": []}]
    assert actions_without_steps(inputs, AUTOMATION_TEST_ACTIONS) == set(AUTOMATION_TEST_ACTIONS) - {"grid_charging", "force_discharge"}


async def test_an_action_without_steps_is_not_held_and_a_missing_automation_is_said(hass, loaded_entry, fast_runner) -> None:
    coordinator = loaded_entry.runtime_data
    held: list[str] = []
    found = [{"grid_charging": STEPS, "force_discharge": STEPS, "battery_stopped": STEPS}]
    with patch("custom_components.battmind.coordinator.automation_test.automations_using_blueprint", return_value=found),             patch.object(type(coordinator.engine), "automation_test_sample", _battery(calls=held)):
        await _start(hass, loaded_entry)
    state = _sensor(hass)
    steps = {step["action"]: step for step in state.attributes["steps"]}
    assert [steps[a]["status"] for a in AUTOMATION_TEST_ACTIONS[:3]] == ["ok", "ok", "ok"]
    assert all(steps[a]["reason"] == "no_steps" for a in AUTOMATION_TEST_ACTIONS[3:])
    assert "discharge_allowed" not in held and "blocked_solar_charge" not in held
    assert "automations.yaml" not in state.attributes["markdown"]  # the automation was found
    with patch("custom_components.battmind.coordinator.automation_test.automations_using_blueprint", return_value=[]),             patch.object(type(coordinator.engine), "automation_test_sample", _battery()):
        await _start(hass, loaded_entry)
    assert "automations.yaml" in _sensor(hass).attributes["markdown"]
    assert [step["status"] for step in _sensor(hass).attributes["steps"]][:5] == ["ok"] * 5  # unknown: tested as usual
