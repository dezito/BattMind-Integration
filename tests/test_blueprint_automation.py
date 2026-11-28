"""The bundled battery control blueprint runs the right actions on the right triggers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import shutil
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.battmind.management.blueprint_install import SOURCE
from custom_components.battmind.const import DOMAIN

ACTION_SENSOR = "sensor.battmind_powerwall_action"
ACTIONS = ["battery_stopped", "grid_charging", "force_discharge", "discharge_allowed", "force_solar_only_charging", "blocked_solar_charge"]


@pytest.fixture(autouse=True)
def config_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    hass.config.config_dir = str(tmp_path)
    target = tmp_path / "blueprints" / "automation" / DOMAIN
    target.mkdir(parents=True)
    shutil.copy(SOURCE / "battery_control.yaml", target / "battery_control.yaml")


@pytest.fixture
def ran(hass: HomeAssistant) -> list[str]:
    events: list[str] = []

    @callback
    def _record(event: Event) -> None:
        events.append(event.data["action"])

    hass.bus.async_listen("battmind_test", _record)
    return events


async def _setup(hass: HomeAssistant, freezer: FrozenDateTimeFactory, default_repeat: bool = False, **inputs: Any) -> None:
    if not default_repeat:
        # the tests choose the repeat themselves (the blueprint repeats every 5 minutes by default)
        inputs.setdefault("repeat_interval", {"hours": 0, "minutes": 0, "seconds": 0})
    freezer.move_to(datetime(2026, 10, 23, 10, 13, 30, tzinfo=dt_util.get_default_time_zone()))
    hass.states.async_set(ACTION_SENSOR, "discharge_allowed")
    actions = {
        name: [{"event": "battmind_test", "event_data": {"action": name}}] for name in ACTIONS
    }
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "use_blueprint": {
                    "path": f"{DOMAIN}/battery_control.yaml",
                    "input": {"battmind_action": ACTION_SENSOR, **actions, **inputs},
                }
            }
        },
    )
    await hass.async_block_till_done()


async def _settle() -> None:
    """Let the automation run until it waits. async_block_till_done would wait for the repeat loop."""
    for _ in range(20):
        await asyncio.sleep(0)


async def _wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: int, step: int = 1) -> None:
    """Let the time pass in small steps, so every delay finishes on time."""
    for _ in range(seconds // step):
        freezer.tick(step)
        async_fire_time_changed(hass)
        await _settle()


async def _stop(hass: HomeAssistant) -> None:
    """End a running repeat loop, so it does not linger after the test."""
    await hass.services.async_call("automation", "turn_off", {"entity_id": "all"}, blocking=True)
    await hass.async_block_till_done()


async def test_action_change_runs_action(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer)
    hass.states.async_set(ACTION_SENSOR, "grid_charging")
    await _settle()
    assert ran == ["grid_charging"]


async def test_temperature_stop_runs_stop_action(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer)
    hass.states.async_set(ACTION_SENSOR, "battery_stopped")
    await _settle()
    assert ran == ["battery_stopped"]


async def test_repeat_off(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer)
    hass.states.async_set(ACTION_SENSOR, "grid_charging")
    await _settle()
    await _wait(hass, freezer, 3600, step=60)
    assert ran == ["grid_charging"]


async def test_repeat_every_5_minutes_by_default(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    """A lost command or an inverter that changed its mode is put right within 5 minutes."""
    await _setup(hass, freezer, default_repeat=True)
    hass.states.async_set(ACTION_SENSOR, "grid_charging")
    await _settle()
    await _wait(hass, freezer, 900, step=30)
    assert ran == ["grid_charging"] * 4

    await _stop(hass)


async def test_runs_on_reload_by_default(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, default_repeat=True)
    hass.bus.async_fire("automation_reloaded")
    await _settle()
    assert ran == ["discharge_allowed"]

    await _stop(hass)


async def test_repeat_interval_seconds(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"hours": 0, "minutes": 0, "seconds": 10})
    hass.states.async_set(ACTION_SENSOR, "force_discharge")
    await _settle()
    await _wait(hass, freezer, 35)
    # at once and after 10, 20 and 30 seconds
    assert ran == ["force_discharge"] * 4
    await _stop(hass)


async def test_repeat_interval_minutes_and_hours(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"hours": 1, "minutes": 30, "seconds": 0})
    hass.states.async_set(ACTION_SENSOR, "force_discharge")
    await _settle()
    await _wait(hass, freezer, 3 * 3600 + 60, step=60)
    # at once and after 1:30 and 3:00
    assert ran == ["force_discharge"] * 3
    await _stop(hass)


async def test_new_action_restarts_the_repeat(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"seconds": 10})
    hass.states.async_set(ACTION_SENSOR, "force_discharge")
    await _settle()
    await _wait(hass, freezer, 5)
    hass.states.async_set(ACTION_SENSOR, "grid_charging")
    await _settle()
    await _wait(hass, freezer, 12)
    assert ran == ["force_discharge", "grid_charging", "grid_charging"]
    await _stop(hass)


async def test_repeat_stops_when_sensor_unavailable(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"seconds": 10})
    hass.states.async_set(ACTION_SENSOR, "force_discharge")
    await _settle()
    hass.states.async_set(ACTION_SENSOR, "unavailable")
    await _settle()
    await _wait(hass, freezer, 60)
    assert ran == ["force_discharge"]


async def test_repeat_starts_on_reload(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"seconds": 10})
    hass.bus.async_fire("automation_reloaded")
    await _settle()
    await _wait(hass, freezer, 15)
    assert ran == ["discharge_allowed"] * 2
    await _stop(hass)


async def test_no_run_on_reload_without_repeat(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer)
    hass.bus.async_fire("automation_reloaded")
    await _settle()
    assert ran == []


async def test_custom_trigger(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, extra_triggers=[{"trigger": "event", "event_type": "modbus_tick"}])
    hass.bus.async_fire("modbus_tick")
    await _settle()
    assert ran == ["discharge_allowed"]


EV_POWER = "sensor.ev_charger_power"
EV_ALLOWED = "switch.cjp_tesla_powerwall_discharge_above_needed"
EV_MIN_LEVEL = "number.cjp_tesla_ev_charge_after_powerwall_battery_level"
HOME_BATTERY = "sensor.powerwall_battery_level"
EV_INPUTS = {"ev_chargers": [EV_POWER], "ev_charging": [{"event": "battmind_test", "event_data": {"action": "ev_charging"}}]}


async def _car_charging(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set(EV_POWER, "7000")
    await _wait(hass, freezer, 31)


async def test_car_charging_blocks_discharge(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    hass.states.async_set(EV_POWER, "0")
    await _setup(hass, freezer, **EV_INPUTS)
    await _car_charging(hass, freezer)
    assert ran == ["ev_charging"]


async def test_discharge_into_car_allowed_by_entity(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    hass.states.async_set(EV_POWER, "0")
    hass.states.async_set(EV_ALLOWED, "on")
    await _setup(hass, freezer, **EV_INPUTS, ev_discharge_allowed=EV_ALLOWED)
    await _car_charging(hass, freezer)
    assert ran == ["discharge_allowed"]
    # turned off while the car charges: back to the car action
    hass.states.async_set(EV_ALLOWED, "off")
    await _settle()
    assert ran == ["discharge_allowed", "ev_charging"]


KW = {"unit_of_measurement": "kW", "device_class": "power"}


async def test_car_charging_in_kw_blocks_discharge(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    """A charger power sensor in kW (e.g. Easee) is compared with the threshold in W."""
    hass.states.async_set(EV_POWER, "0.0", KW)
    await _setup(hass, freezer, **EV_INPUTS)
    hass.states.async_set(EV_POWER, "10.0", KW)
    await _wait(hass, freezer, 31)
    assert ran == ["ev_charging"]
    # the car stops: back to BattMind's action
    hass.states.async_set(EV_POWER, "0.0", KW)
    await _wait(hass, freezer, 31)
    assert ran == ["ev_charging", "discharge_allowed"]


async def test_car_charging_in_kw_below_threshold(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    """0.5 kW is 500 W: under the default 1000 W the car does not count as charging."""
    hass.states.async_set(EV_POWER, "0.0", KW)
    await _setup(hass, freezer, **EV_INPUTS)
    hass.states.async_set(EV_POWER, "0.5", KW)
    await _wait(hass, freezer, 31)
    hass.states.async_set(ACTION_SENSOR, "blocked_solar_charge")
    await _settle()
    assert ran == ["blocked_solar_charge"]


async def test_discharge_into_car_switch_with_kw_charger(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    hass.states.async_set(EV_POWER, "0.0", KW)
    hass.states.async_set(EV_ALLOWED, "on")
    await _setup(hass, freezer, **EV_INPUTS, ev_discharge_allowed=EV_ALLOWED)
    hass.states.async_set(EV_POWER, "7.4", KW)
    await _wait(hass, freezer, 31)
    assert ran == ["discharge_allowed"]
    hass.states.async_set(EV_ALLOWED, "off")
    await _settle()
    assert ran == ["discharge_allowed", "ev_charging"]


async def _ramp(hass: HomeAssistant, freezer: FrozenDateTimeFactory, values: list[str], attributes: dict[str, str] | None = None, seconds: int = 7) -> None:
    """A charger that ramps up through several values, each one shorter than 30 seconds."""
    for value in values:
        hass.states.async_set(EV_POWER, value, attributes)
        await _wait(hass, freezer, seconds)


@pytest.mark.parametrize(
    ("values", "attributes"),
    [
        (["0.45", "9.87", "10.07", "10.17"], KW),
        (["450", "9866", "10068", "10173"], None),
    ],
    ids=["kW", "W"],
)
async def test_car_charging_detected_when_power_ramps_up(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str], values: list[str], attributes: dict[str, str] | None
) -> None:
    """Seen with an Easee charger: the power steps up within 30 seconds, then holds above the threshold."""
    hass.states.async_set(EV_POWER, "0", attributes)
    await _setup(hass, freezer, **EV_INPUTS)
    await _ramp(hass, freezer, values, attributes)
    await _wait(hass, freezer, 31)
    assert ran == ["ev_charging"]


async def test_power_wobble_while_charging_runs_nothing_new(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    hass.states.async_set(EV_POWER, "0.0", KW)
    await _setup(hass, freezer, **EV_INPUTS)
    hass.states.async_set(EV_POWER, "10.0", KW)
    await _wait(hass, freezer, 31)
    assert ran == ["ev_charging"]
    # the reading moves a little, sometimes held longer than 30 seconds: still charging
    await _ramp(hass, freezer, ["10.07", "9.95", "10.05", "9.96"], KW, seconds=40)
    assert ran == ["ev_charging"]


async def test_single_charger_entity_as_string(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    """An automation that holds one entity instead of a list (as an older editor or YAML may write it)."""
    hass.states.async_set(EV_POWER, "0.0", KW)
    await _setup(hass, freezer, ev_chargers=EV_POWER, ev_charging=EV_INPUTS["ev_charging"])
    hass.states.async_set(EV_POWER, "10.0", KW)
    await _wait(hass, freezer, 31)
    assert ran == ["ev_charging"]


async def test_discharge_into_car_only_above_level(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    hass.states.async_set(EV_POWER, "0")
    hass.states.async_set(EV_ALLOWED, "on")
    hass.states.async_set(EV_MIN_LEVEL, "40")
    hass.states.async_set(HOME_BATTERY, "35")
    await _setup(hass, freezer, **EV_INPUTS, ev_discharge_allowed=EV_ALLOWED, ev_discharge_min_level=EV_MIN_LEVEL, home_battery_level=HOME_BATTERY)
    await _car_charging(hass, freezer)
    assert ran == ["ev_charging"]
    hass.states.async_set(HOME_BATTERY, "60")
    await _settle()
    assert ran == ["ev_charging", "discharge_allowed"]
    # changes while the car is not charging do not run anything
    hass.states.async_set(EV_POWER, "0")
    await _wait(hass, freezer, 31)
    hass.states.async_set(HOME_BATTERY, "20")
    await _settle()
    assert ran == ["ev_charging", "discharge_allowed", "discharge_allowed"]


# --- fail-safe: BattMind has stopped or hangs ---------------------------------------------------------------------

FORCED = ["grid_charging", "force_discharge", "force_solar_only_charging", "blocked_solar_charge"]
REPEAT_1_MIN = {"minutes": 1}


def _publish(hass: HomeAssistant, action: str, valid_minutes: float = 15) -> None:
    """The action sensor as BattMind publishes it: valid_until is renewed by every calculation."""
    valid_until = (dt_util.now() + timedelta(minutes=valid_minutes)).isoformat(timespec="seconds")
    hass.states.async_set(ACTION_SENSOR, action, {"valid_until": valid_until})


@pytest.mark.parametrize("action", FORCED)
async def test_an_action_that_is_no_longer_renewed_becomes_normal_operation(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str], action: str
) -> None:
    """BattMind is there but calculates nothing: a forced mode is not renewed for ever."""
    await _setup(hass, freezer, repeat_interval=REPEAT_1_MIN)
    _publish(hass, action)
    await _settle()
    await _wait(hass, freezer, 14 * 60, step=30)
    assert set(ran) == {action}
    ran.clear()
    await _wait(hass, freezer, 5 * 60, step=30)
    first = ran.index("discharge_allowed")
    assert first <= 1  # at most the repeat that falls on the last valid minute
    assert set(ran[first:]) == {"discharge_allowed"}
    await _stop(hass)


async def test_a_long_repeat_does_not_delay_the_fail_safe(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    """The wait looks at the time every minute: the repeat interval does not decide how soon an old action is noticed."""
    await _setup(hass, freezer, repeat_interval={"hours": 1})
    _publish(hass, "force_discharge")
    await _settle()
    await _wait(hass, freezer, 17 * 60, step=30)
    assert ran == ["force_discharge", "discharge_allowed"]
    await _stop(hass)


async def test_a_car_that_starts_while_the_action_is_gone_is_kept_out_of_the_battery(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]
) -> None:
    """No trigger restarts the waiting run then: the car is looked at again when the fail-safe runs."""
    hass.states.async_set(EV_POWER, "0")
    await _setup(hass, freezer, **EV_INPUTS)
    _publish(hass, "grid_charging", 60)
    await _settle()
    hass.states.async_set(ACTION_SENSOR, "unavailable")
    await _settle()
    ran.clear()
    hass.states.async_set(EV_POWER, "7000")
    await _wait(hass, freezer, 18 * 60, step=30)
    assert ran == ["ev_charging"]
    await _stop(hass)


async def test_the_real_action_is_back_when_battmind_calculates_again(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval=REPEAT_1_MIN)
    _publish(hass, "force_discharge")
    await _settle()
    await _wait(hass, freezer, 18 * 60, step=30)
    assert ran[-1] == "discharge_allowed"
    ran.clear()
    _publish(hass, "force_discharge")  # only the attribute changes: no new trigger
    await _wait(hass, freezer, 3 * 60, step=30)
    assert ran and set(ran) == {"force_discharge"}
    await _stop(hass)


async def test_a_renewed_action_never_runs_the_fail_safe(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"minutes": 5})
    _publish(hass, "grid_charging")
    await _settle()
    for _ in range(8):  # a calculation every 5 minutes for 40 minutes
        await _wait(hass, freezer, 5 * 60, step=30)
        _publish(hass, "grid_charging")
        await _settle()
    assert set(ran) == {"grid_charging"}
    await _stop(hass)


async def test_without_valid_until_nothing_is_ever_too_old(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"minutes": 5})
    hass.states.async_set(ACTION_SENSOR, "force_discharge")
    await _settle()
    await _wait(hass, freezer, 3600, step=60)
    assert set(ran) == {"force_discharge"}
    await _stop(hass)


async def test_a_temperature_stop_is_never_lifted_by_the_fail_safe(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"minutes": 5})
    _publish(hass, "battery_stopped")
    await _settle()
    await _wait(hass, freezer, 3600, step=60)
    assert set(ran) == {"battery_stopped"}
    ran.clear()
    hass.states.async_set(ACTION_SENSOR, "unavailable")
    await _settle()
    await _wait(hass, freezer, 3600, step=60)
    assert ran == []
    await _stop(hass)


async def test_fail_safe_off_keeps_the_old_action(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"minutes": 5}, fail_safe=False)
    _publish(hass, "force_discharge")
    await _settle()
    await _wait(hass, freezer, 3600, step=60)
    assert set(ran) == {"force_discharge"}
    ran.clear()
    hass.states.async_set(ACTION_SENSOR, "unavailable")
    await _settle()
    await _wait(hass, freezer, 3600, step=60)
    assert ran == []
    await _stop(hass)


async def test_without_a_repeat_the_fail_safe_still_works_once_each_way(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer)  # repeat off
    _publish(hass, "grid_charging")
    await _settle()
    await _wait(hass, freezer, 14 * 60, step=30)
    assert ran == ["grid_charging"]
    await _wait(hass, freezer, 30 * 60, step=30)
    assert ran == ["grid_charging", "discharge_allowed"]
    _publish(hass, "grid_charging")
    await _wait(hass, freezer, 3 * 60, step=30)
    assert ran == ["grid_charging", "discharge_allowed", "grid_charging"]
    await _stop(hass)


async def test_without_a_repeat_a_renewed_action_is_sent_only_once(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer)
    _publish(hass, "force_discharge")
    await _settle()
    for _ in range(8):
        await _wait(hass, freezer, 5 * 60, step=30)
        _publish(hass, "force_discharge")
        await _settle()
    assert ran == ["force_discharge"]
    await _stop(hass)


@pytest.mark.parametrize("repeat", [{"seconds": 0}, {"minutes": 5}], ids=["no repeat", "repeat"])
@pytest.mark.parametrize("lost", [["unavailable"], ["unknown"], ["unavailable", "unknown"]], ids=["unavailable", "unknown", "reload"])
async def test_a_lost_action_sensor_ends_a_forced_mode_after_the_wait(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str], repeat: dict, lost: list[str]
) -> None:
    await _setup(hass, freezer, repeat_interval=repeat)
    _publish(hass, "force_discharge", 60)
    await _settle()
    ran.clear()
    for state in lost:
        hass.states.async_set(ACTION_SENSOR, state)
        await _settle()
        await _wait(hass, freezer, 4 * 60, step=30)
    assert ran == []  # not before the 15 minutes (a restart or a reload is over by then)
    await _wait(hass, freezer, 14 * 60, step=30)
    assert ran == ["discharge_allowed"]
    await _wait(hass, freezer, 30 * 60, step=30)
    assert ran == ["discharge_allowed"]
    await _stop(hass)


async def test_a_sensor_that_comes_back_in_time_keeps_the_plan(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, fail_safe_minutes=10)
    _publish(hass, "grid_charging", 60)
    await _settle()
    hass.states.async_set(ACTION_SENSOR, "unavailable")
    await _settle()
    await _wait(hass, freezer, 5 * 60, step=30)
    _publish(hass, "grid_charging", 60)
    await _settle()
    await _wait(hass, freezer, 20 * 60, step=30)
    assert ran == ["grid_charging", "grid_charging"]
    await _stop(hass)


async def test_normal_operation_needs_no_fail_safe_when_the_sensor_is_lost(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer)
    _publish(hass, "grid_charging")
    await _settle()
    _publish(hass, "discharge_allowed")
    await _settle()
    ran.clear()
    hass.states.async_set(ACTION_SENSOR, "unavailable")
    await _settle()
    await _wait(hass, freezer, 3600, step=60)
    assert ran == []
    await _stop(hass)


async def test_a_start_with_an_old_action_sends_normal_operation(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    await _setup(hass, freezer, repeat_interval={"minutes": 5})
    _publish(hass, "force_discharge", -1)
    await _settle()
    assert ran == ["discharge_allowed"]
    await _stop(hass)


async def test_the_fail_safe_keeps_the_car_out_of_the_battery(hass: HomeAssistant, freezer: FrozenDateTimeFactory, ran: list[str]) -> None:
    """Normal operation in the fail-safe is the same normal operation: while the car charges, the EV action runs."""
    hass.states.async_set(EV_POWER, "7000")
    await _setup(hass, freezer, **EV_INPUTS)
    _publish(hass, "grid_charging", -1)
    await _settle()
    assert ran == ["ev_charging"]
    await _stop(hass)
