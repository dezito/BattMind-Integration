"""An ignored consumer that stopped reporting is not subtracted, and the house consumption is never negative as a surplus."""
from __future__ import annotations

import datetime
import logging

import pytest
from homeassistant.core import HomeAssistant

from custom_components.battmind.const import build_config
from custom_components.battmind.engine import BattMindEngine

from .conftest import async_unload_and_join, set_source_states
from .test_cjp_chargers import _settle, cjp_entry
from .test_engine import DAY, OPTIONS, TEXTS, TZ, snapshot

NOW = datetime.datetime.combine(DAY, datetime.time(10, 30))
NOW_TS = NOW.replace(tzinfo=TZ).timestamp()
START_TS = NOW_TS - 7200
LOGGER = "custom_components.battmind.engine.energy.energy"


def _engine(ignored=("sensor.charger",), chargers=("sensor.charger",), **options) -> BattMindEngine:
    engine = BattMindEngine(build_config({**OPTIONS, "ignore_consumption_from_entity_ids": list(ignored), **options}), "test", TEXTS)
    engine.config["home"]["charger_entity_ids"] = list(chargers)  # what the coordinator sets from the CJP cars
    return engine


def _snapshot(charger_watt: float, reported: float | None, now: datetime.datetime = NOW):
    snap = snapshot(now, 50)
    snap.buffers = {
        "sensor.consumption": [(START_TS, 2000.0)],
        "sensor.battery_power": [(START_TS, 0.0)],
        "sensor.charger": [(START_TS, charger_watt)],
        "sensor.solar": [(START_TS, 1500.0)],
    }
    snap.units["sensor.charger"] = "W"
    snap.entity_states["sensor.charger"] = str(charger_watt)  # what it shows now
    snap.reported = {"sensor.charger": reported}
    return snap


def _power(engine: BattMindEngine, snap, **kwargs) -> dict:
    result = engine.run_job(snap, engine.power_values, **{"period": 60, **kwargs})
    assert result.error is None, result.error
    return result.value


def _warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.name == LOGGER and record.levelno == logging.WARNING]


def test_a_charger_that_keeps_reporting_is_subtracted_even_when_its_value_never_changes(caplog) -> None:
    """A steady 11 kW charge: the state does not change for hours, but the sensor reports every minute."""
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        values = _power(_engine(), _snapshot(700.0, NOW_TS - 60))
    assert values["ignored_consumption"] == 700.0
    assert values["power_consumption_without_ignored"] == 1300.0
    assert _warnings(caplog) == []


def test_a_charger_that_stopped_reporting_is_not_subtracted(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        values = _power(_engine(), _snapshot(700.0, NOW_TS - 3600))
    assert values["ignored_consumption"] == 0.0
    assert values["power_consumption_without_ignored"] == 2000.0  # the higher consumption: the conservative one
    assert values["power_consumption_without_all_exclusion"] == 2000.0
    assert len(_warnings(caplog)) == 1 and "sensor.charger" in _warnings(caplog)[0]


def test_the_limit_is_thirty_minutes() -> None:
    assert _power(_engine(), _snapshot(700.0, NOW_TS - 29 * 60))["ignored_consumption"] == 700.0
    assert _power(_engine(), _snapshot(700.0, NOW_TS - 31 * 60))["ignored_consumption"] == 0.0


def test_the_warning_comes_once_per_stale_episode(caplog) -> None:
    engine = _engine()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for minutes in (0, 5, 10):
            now = NOW + datetime.timedelta(minutes=minutes)
            _power(engine, _snapshot(700.0, NOW_TS - 3600, now))
        assert len(_warnings(caplog)) == 1  # not at every job

        # it reports again, so it is subtracted again ...
        assert _power(engine, _snapshot(700.0, NOW_TS + 15 * 60 - 30, NOW + datetime.timedelta(minutes=15)))["ignored_consumption"] == 700.0
        # ... and a new stop is a new episode
        _power(engine, _snapshot(700.0, NOW_TS + 15 * 60 - 30, NOW + datetime.timedelta(minutes=120)))
    assert len(_warnings(caplog)) == 2


def test_a_stale_charger_that_shows_zero_changes_nothing(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        values = _power(_engine(), _snapshot(0.0, NOW_TS - 3600))
    assert values["ignored_consumption"] == 0.0 and values["power_consumption_without_ignored"] == 2000.0
    assert _warnings(caplog) == []


def test_an_unknown_last_reported_counts_as_fresh() -> None:
    assert _power(_engine(), _snapshot(700.0, None))["ignored_consumption"] == 700.0
    snap = _snapshot(700.0, None)
    snap.reported = {}  # a snapshot made before the field existed
    assert _power(_engine(), snap)["ignored_consumption"] == 700.0


def test_a_window_that_ended_before_it_got_stuck_is_read_as_recorded() -> None:
    """What happened hours ago was recorded when it reported."""
    engine = _engine()
    snap = _snapshot(700.0, NOW_TS - 3600)
    snap.buffers["sensor.charger"] = [(NOW_TS - 36000, 700.0)]
    past = _power(engine, snap, period=None, from_timestamp=NOW - datetime.timedelta(hours=5), to_timestamp=NOW - datetime.timedelta(hours=4))
    assert past["ignored_consumption"] == 700.0
    # it got stuck 30 minutes after its last report, at NOW - 30 min: a window that ended then is as recorded too
    edge = _power(engine, snap, period=None, from_timestamp=NOW - datetime.timedelta(hours=2), to_timestamp=NOW - datetime.timedelta(minutes=30))
    assert edge["ignored_consumption"] == 700.0


def test_the_hours_since_it_got_stuck_are_corrected_too_not_only_the_live_window() -> None:
    """A pump sensor hangs at 700 W: the hours that are recomputed until they are cached must not keep subtracting it."""
    engine = _engine()
    snap = _snapshot(700.0, NOW_TS - 3 * 3600)  # last reported three hours ago, stuck since 2.5 hours ago
    snap.buffers["sensor.charger"] = [(NOW_TS - 4 * 3600, 700.0)]
    for end_minutes_ago in (120, 90, 10):
        values = _power(engine, snap, period=None, from_timestamp=NOW - datetime.timedelta(minutes=end_minutes_ago + 60), to_timestamp=NOW - datetime.timedelta(minutes=end_minutes_ago))
        assert values["ignored_consumption"] == 0.0, end_minutes_ago
    # before it got stuck (2.5 h ago) the window is as recorded
    assert _power(engine, snap, period=None, from_timestamp=NOW - datetime.timedelta(hours=4), to_timestamp=NOW - datetime.timedelta(hours=3))["ignored_consumption"] == 700.0


def test_a_charger_that_stopped_and_shows_zero_now_is_not_stuck(caplog) -> None:
    """It charged until 20 minutes ago and does not write the unchanged 0 again: the window's average is not what it shows now."""
    snap = _snapshot(0.0, NOW_TS - 3600)
    snap.buffers["sensor.charger"] = [(START_TS, 700.0), (NOW_TS - 1200, 0.0)]
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        values = _power(_engine(), snap)
    assert values["ignored_consumption"] > 0.0  # what it used in the window is still subtracted
    assert _warnings(caplog) == []


def test_a_listed_and_a_found_charger_are_treated_alike() -> None:
    """A CJP charger is checked whether BattMind added it itself or the user listed it too."""
    engine = _engine(("sensor.charger", "sensor.pool_pump"))
    snap = _snapshot(700.0, NOW_TS - 3600)
    snap.buffers["sensor.pool_pump"] = [(START_TS, 400.0)]
    snap.units["sensor.pool_pump"] = "W"
    snap.entity_states["sensor.pool_pump"] = "400.0"
    snap.reported["sensor.pool_pump"] = NOW_TS - 10
    values = _power(engine, snap)
    assert values["ignored_consumption"] == 400.0  # the frozen charger is left out, the one that reports is subtracted


def test_a_listed_consumer_that_is_not_a_charger_is_always_subtracted(caplog) -> None:
    """A plug or a template sensor of a steady load may only report when its value changes: it is not 'stuck' after 30 minutes."""
    engine = _engine(("sensor.charger", "sensor.pool_pump"))
    snap = _snapshot(700.0, NOW_TS - 10)
    snap.buffers["sensor.pool_pump"] = [(START_TS, 400.0)]
    snap.units["sensor.pool_pump"] = "W"
    snap.entity_states["sensor.pool_pump"] = "400.0"
    snap.reported["sensor.pool_pump"] = NOW_TS - 3 * 3600
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        values = _power(engine, snap)
    assert values["ignored_consumption"] == 1100.0 and _warnings(caplog) == []


def test_an_ignored_consumer_above_the_house_sensor_gives_no_extra_surplus() -> None:
    """The charger reads 3 kW but the house sensor 2 kW: the house is 0 W, not -1 kW, so 1.5 kW of solar is a surplus of 1.5 kW."""
    engine = _engine(production_entity_id="sensor.solar")
    snap = _snapshot(3000.0, NOW_TS - 10)
    for solar_only in (False, True):
        result = engine.run_job(snap, engine.local_energy_available, period=60, solar_only=solar_only)
        assert result.error is None, result.error
        assert result.value == 1500.0
    # the stored value itself is as measured
    assert _power(engine, snap)["power_consumption_without_ignored"] == -1000.0
    # and the forecast is never fed a negative consumption
    assert _power(engine, snap)["power_consumption_without_all_exclusion"] == 0.0


@pytest.mark.parametrize("without_all_exclusion", [True, False])
def test_the_surplus_is_never_above_the_solar_power(without_all_exclusion: bool) -> None:
    engine = _engine(production_entity_id="sensor.solar")
    result = engine.run_job(
        _snapshot(4500.0, NOW_TS - 10), engine.local_energy_available, period=60, solar_only=True, without_all_exclusion=without_all_exclusion
    )
    assert result.value <= 1500.0


async def test_the_snapshot_carries_when_the_sensors_last_reported(hass: HomeAssistant, mock_entry) -> None:
    cjp_entry(hass, "sensor.zoe_power")
    hass.states.async_set("sensor.zoe_power", "7.0", {"unit_of_measurement": "kW", "device_class": "power"})
    set_source_states(hass)
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await _settle(hass)

    coordinator = mock_entry.runtime_data
    snap = await coordinator._async_snapshot()
    assert snap.reported["sensor.zoe_power"] == pytest.approx(hass.states.get("sensor.zoe_power").last_reported.timestamp())
    assert all(isinstance(value, float) for value in snap.reported.values())
    await async_unload_and_join(hass, mock_entry)
