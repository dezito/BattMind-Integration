"""The solar forecast sources of the coordinator's snapshot: Solcast's detailedHourly, and the energy platform of
Forecast.Solar and Helios Forecast normalised to the same rows (utils/solar_forecast.py), also through the debug export
and its replay."""
from __future__ import annotations

import datetime
import gzip
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockModule, mock_integration, mock_platform

from custom_components.battmind.const import CONF_ENTITY_PREFIX, DOMAIN
from custom_components.battmind.management.debug_export import DEBUG_DIR
from custom_components.battmind.utils import solar_forecast
from custom_components.battmind.utils.solcast import SOLCAST_UNIQUE_IDS

from . import debug_replay
from .conftest import MOCK_OPTIONS, PREFIX, async_unload_and_join, set_source_states

HELIOS_SENSOR = "sensor.helios_forecast_power_now"
SOLCAST_BASE = "sensor.solcast_pv_forecast_forecast"


@pytest.fixture
def config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    hass.config.config_dir = str(tmp_path)
    return tmp_path


def _wh_hours() -> dict[str, float]:
    """Two days of a Helios forecast from two hours ago: Wh per UTC hour, keyed by the start of the hour."""
    start = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=2)
    hours = [start + datetime.timedelta(hours=offset) for offset in range(48)]
    return {moment.isoformat(): float(max(0, 4000 - abs(moment.hour - 11) * 600)) for moment in hours}


def _expected_rows(wh_hours: dict[str, float]) -> list[dict]:
    """The rows written out by hand for whole UTC hours keyed by their start: each one is the local hour it falls in (the
    hour that occurs twice when the clocks go back gets both, so 48 UTC hours may give 47 rows)."""
    tz = dt_util.get_default_time_zone()
    wh: dict[datetime.datetime, float] = {}
    for key, value in wh_hours.items():
        local = datetime.datetime.fromisoformat(key).astimezone(tz).replace(tzinfo=None)
        wh[local] = wh.get(local, 0.0) + value
    return [{"period_start": local.replace(tzinfo=tz), "pv_estimate": round(value / 1000, 4)} for local, value in sorted(wh.items())]


def energy_platform(hass: HomeAssistant, domain: str, answer: dict) -> list[str]:
    """A fake energy platform of an integration; answer["value"] is what it returns (changeable by the test)."""
    calls: list[str] = []

    async def async_get_solar_forecast(hass: HomeAssistant, config_entry_id: str):
        calls.append(config_entry_id)
        return answer["value"]

    mock_integration(hass, MockModule(domain))
    mock_platform(hass, f"{domain}.energy", SimpleNamespace(async_get_solar_forecast=async_get_solar_forecast))
    return calls


def helios(hass: HomeAssistant, answer: dict) -> tuple[MockConfigEntry, list[str]]:
    entry = MockConfigEntry(domain="helios_forecast", title="Helios", state=ConfigEntryState.LOADED)
    entry.add_to_hass(hass)
    registered = er.async_get(hass).async_get_or_create(
        "sensor", "helios_forecast", f"{entry.entry_id}_power_now", config_entry=entry, suggested_object_id="helios_forecast_power_now"
    )
    assert registered.entity_id == HELIOS_SENSOR
    hass.states.async_set(HELIOS_SENSOR, "1200", {"unit_of_measurement": "W", "device_class": "power"})
    return entry, energy_platform(hass, "helios_forecast", answer)


async def _setup(hass: HomeAssistant, forecast_entity: str) -> MockConfigEntry:
    set_source_states(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"BattMind ({PREFIX})",
        data={CONF_ENTITY_PREFIX: PREFIX, "legacy_prefix": None, "initial_settings": {}},
        options={**MOCK_OPTIONS, "forecast_entity_id": forecast_entity},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


@pytest.fixture
async def unload(hass: HomeAssistant) -> AsyncGenerator[list[MockConfigEntry]]:
    entries: list[MockConfigEntry] = []
    yield entries
    for entry in entries:
        if entry.state is ConfigEntryState.LOADED:
            await async_unload_and_join(hass, entry)


async def test_solcast_rows_are_taken_as_they_are(hass: HomeAssistant, unload) -> None:
    """Regression: the Solcast path is unchanged, and the energy platform is not asked for a Solcast sensor."""
    tz = dt_util.get_default_time_zone()
    today = dt_util.now().date()
    attributes = {}
    for day, (ending, unique_id) in enumerate(SOLCAST_UNIQUE_IDS.items()):
        entity_id = er.async_get(hass).async_get_or_create("sensor", "solcast_solar", unique_id, suggested_object_id=f"solcast_pv_forecast_forecast_{ending}").entity_id
        date = today + datetime.timedelta(days=day)
        rows = [{"period_start": datetime.datetime.combine(date, datetime.time(hour), tzinfo=tz), "pv_estimate": hour / 10, "pv_estimate10": 0.0} for hour in range(24)]
        hass.states.async_set(entity_id, "12.3", {"detailedHourly": rows, "unit_of_measurement": "kWh"})
        attributes[entity_id] = rows
    calls = energy_platform(hass, "solcast_solar", {"value": {"wh_hours": _wh_hours()}})
    unload.append(entry := await _setup(hass, f"{SOLCAST_BASE}_today"))

    snapshot = await entry.runtime_data._async_snapshot(include_forecast=True)  # noqa: SLF001
    assert snapshot.solcast == attributes
    assert snapshot.solcast[f"{SOLCAST_BASE}_today"][0] is hass.states.get(f"{SOLCAST_BASE}_today").attributes["detailedHourly"][0]
    assert calls == []


async def test_helios_rows_come_from_the_energy_platform(hass: HomeAssistant, unload, caplog: pytest.LogCaptureFixture) -> None:
    wh_hours = _wh_hours()
    answer = {"value": {"wh_hours": wh_hours}}
    helios_entry, calls = helios(hass, answer)
    unload.append(entry := await _setup(hass, HELIOS_SENSOR))
    coordinator = entry.runtime_data

    snapshot = await coordinator._async_snapshot(include_forecast=True)  # noqa: SLF001
    expected = _expected_rows(wh_hours)
    assert 47 <= len(expected) <= 48 and expected[0]["pv_estimate"] == wh_hours[min(wh_hours)] / 1000
    assert snapshot.solcast == {HELIOS_SENSOR: expected}
    assert calls and set(calls) == {helios_entry.entry_id}
    # the plan reads them
    result = await coordinator.async_run_job("charge_if_needed", True, include_forecast=True)
    assert result.error is None
    assert any(day.get("total_forecast") for day in coordinator.engine.solar_prediction_db.values())

    # no answer: no rows (the history is used), a warning once until the reason changes
    answer["value"] = None
    caplog.clear()
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            assert (await coordinator._async_snapshot(include_forecast=True)).solcast == {}  # noqa: SLF001
        result = await coordinator.async_run_job("charge_if_needed", True, include_forecast=True)
        assert result.error is None
        warnings = [record for record in caplog.records if record.getMessage().startswith("No solar forecast from helios_forecast")]
        assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
        assert helios_entry.entry_id not in caplog.text
        answer["value"] = {"wh_hours": wh_hours}
        assert (await coordinator._async_snapshot(include_forecast=True)).solcast == {HELIOS_SENSOR: expected}  # noqa: SLF001
        assert "The solar forecast from helios_forecast is available again" in caplog.text


async def test_forecast_solar_rows_count_each_value_for_the_hour_before_its_key(hass: HomeAssistant, unload) -> None:
    """Forecast.Solar keys the end of each period: the value at 10:00 is the energy of 09:00-10:00 (literal rows)."""
    tz = dt_util.get_default_time_zone()
    tomorrow = dt_util.now().date() + datetime.timedelta(days=1)

    def at(hour: int) -> datetime.datetime:
        return datetime.datetime.combine(tomorrow, datetime.time(hour), tzinfo=tz)

    plane = MockConfigEntry(domain="forecast_solar", title="Roof", state=ConfigEntryState.LOADED)
    plane.add_to_hass(hass)
    sensor = er.async_get(hass).async_get_or_create(
        "sensor", "forecast_solar", f"{plane.entry_id}_energy_production_today", config_entry=plane, suggested_object_id="energy_production_today"
    ).entity_id
    assert sensor == "sensor.energy_production_today"
    hass.states.async_set(sensor, "12.5", {"unit_of_measurement": "kWh"})
    wh_hours = {at(9).isoformat(): 0, at(10).isoformat(): 1000, at(11).isoformat(): 2500, at(12).isoformat(): 1500, at(13).isoformat(): 500}
    calls = energy_platform(hass, "forecast_solar", {"value": {"wh_hours": wh_hours}})
    unload.append(entry := await _setup(hass, sensor))

    snapshot = await entry.runtime_data._async_snapshot(include_forecast=True)  # noqa: SLF001
    assert snapshot.solcast == {
        sensor: [
            {"period_start": at(9), "pv_estimate": 1.0},
            {"period_start": at(10), "pv_estimate": 2.5},
            {"period_start": at(11), "pv_estimate": 1.5},
            {"period_start": at(12), "pv_estimate": 0.5},
        ]
    }
    assert calls and set(calls) == {plane.entry_id}


async def test_forecast_solar_on_the_day_the_clocks_go_back(hass: HomeAssistant, unload, monkeypatch: pytest.MonkeyPatch) -> None:
    """End keys across the autumn clock change: the periods ending 01:00 and 02:00 UTC are both 02:00 local, summed."""
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    tz = dt_util.get_default_time_zone()
    day = next(
        date
        for offset in range(400)
        if (date := dt_util.now().date() + datetime.timedelta(days=offset))
        and datetime.datetime.combine(date, datetime.time(0), tzinfo=tz).utcoffset() > datetime.datetime.combine(date, datetime.time(12), tzinfo=tz).utcoffset()
    )
    monkeypatch.setattr(solar_forecast, "READ_AHEAD", datetime.timedelta(days=400))  # the next clock change may be months away
    instants = [datetime.datetime.combine(day, datetime.time(0), tzinfo=datetime.UTC) + datetime.timedelta(hours=hour) for hour in range(-1, 4)]
    # Forecast.Solar writes its keys with the local offset: 01:00+02:00, 02:00+02:00, 02:00+01:00, 03:00+01:00, 04:00+01:00
    wh_hours = {instant.astimezone(tz).isoformat(): wh for instant, wh in zip(instants, (0, 200, 300, 400, 500), strict=True)}
    assert len(wh_hours) == 5

    plane = MockConfigEntry(domain="forecast_solar", title="Roof", state=ConfigEntryState.LOADED)
    plane.add_to_hass(hass)
    sensor = er.async_get(hass).async_get_or_create(
        "sensor", "forecast_solar", f"{plane.entry_id}_energy_production_today", config_entry=plane, suggested_object_id="energy_production_today"
    ).entity_id
    hass.states.async_set(sensor, "12.5", {"unit_of_measurement": "kWh"})
    energy_platform(hass, "forecast_solar", {"value": {"wh_hours": wh_hours}})
    unload.append(entry := await _setup(hass, sensor))

    snapshot = await entry.runtime_data._async_snapshot(include_forecast=True)  # noqa: SLF001
    assert snapshot.solcast == {
        sensor: [
            {"period_start": datetime.datetime.combine(day, datetime.time(1), tzinfo=tz), "pv_estimate": 0.2},
            {"period_start": datetime.datetime.combine(day, datetime.time(2), tzinfo=tz), "pv_estimate": 0.7},
            {"period_start": datetime.datetime.combine(day, datetime.time(3), tzinfo=tz), "pv_estimate": 0.5},
        ]
    }
    assert snapshot.solcast[sensor][1]["period_start"].utcoffset() == datetime.timedelta(hours=2)  # the first 02:00


async def test_a_sensor_of_another_integration_gives_no_rows(hass: HomeAssistant, unload) -> None:
    """Only the integrations with known key semantics are read: another energy platform could key its hours otherwise."""
    other = MockConfigEntry(domain="open_meteo_solar_forecast", state=ConfigEntryState.LOADED)
    other.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("sensor", "open_meteo_solar_forecast", "x", config_entry=other, suggested_object_id="other_forecast")
    hass.states.async_set("sensor.other_forecast", "1.0")
    calls = energy_platform(hass, "open_meteo_solar_forecast", {"value": {"wh_hours": _wh_hours()}})
    unload.append(entry := await _setup(hass, "sensor.other_forecast"))
    assert (await entry.runtime_data._async_snapshot(include_forecast=True)).solcast == {}  # noqa: SLF001
    assert calls == []


async def test_the_debug_export_replays_the_normalised_rows(hass: HomeAssistant, config_dir: Path, unload) -> None:
    """There is no attribute to export for these sources: the snapshot keeps the rows under the configured sensor
    (named after its option by the Redactor), and the replay plans with them."""
    wh_hours = _wh_hours()
    helios_entry, _ = helios(hass, {"value": {"wh_hours": wh_hours}})
    unload.append(entry := await _setup(hass, HELIOS_SENSOR))
    coordinator = entry.runtime_data
    await coordinator.async_run_job("charge_if_needed", True, include_forecast=True)
    recorded = coordinator.data["current_charging_rule"]

    response = await hass.services.async_call(DOMAIN, "export_debug", {}, blocking=True, return_response=True)
    path = config_dir / response["files"][PREFIX]
    assert path.parent.name == DEBUG_DIR
    with gzip.open(path, "rt", encoding="utf-8") as file:
        text = file.read()
    assert HELIOS_SENSOR not in text and helios_entry.entry_id not in text
    export = debug_replay.load_export(str(path))
    rows = export["planning_job"]["snapshot"]["solcast"]["sensor.forecast"]
    assert len(rows) == len(_expected_rows(wh_hours)) and set(rows[0]) == {"period_start", "pv_estimate"}

    snapshot = debug_replay.build_snapshot(export["planning_job"])
    assert snapshot.solcast["sensor.forecast"][0]["period_start"].tzinfo is not None
    result = await hass.async_add_executor_job(debug_replay.replay, export)
    assert result.error is None
    assert result.states["current_charging_rule"] == recorded
