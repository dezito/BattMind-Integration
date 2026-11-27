"""The data handed to and returned from the engine worker."""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Snapshot:
    """Plain data collected on the event loop for one worker job."""

    now: datetime.datetime
    tz: Any
    settings: dict[str, Any] = field(default_factory=dict)
    entity_states: dict[str, Any] = field(default_factory=dict)
    entity_platforms: dict[str, str | None] = field(default_factory=dict)
    units: dict[str, str | None] = field(default_factory=dict)
    # entity_id -> epoch seconds of the state's last_reported (None: unknown); the sensors that stopped reporting
    reported: dict[str, float | None] = field(default_factory=dict)
    buffers: dict[str, list] = field(default_factory=dict)
    statistics: dict[str, list] = field(default_factory=dict)
    price_data: dict | None = None
    forecast_dict: dict = field(default_factory=lambda: {"hourly": [], "daily": []})
    # normalised hourly solar forecast rows by entity id: Solcast's detailedHourly, or the rows of Forecast.Solar or Helios
    # Forecast in the same shape (utils/solar_forecast.py); the name stays for the debug exports that hold it
    solcast: dict[str, list] = field(default_factory=dict)
    sun_events: dict = field(default_factory=dict)
    # Cable Juice Planner cars: {"name", "charge_plan"}; charge_plan None = no plan available
    ev_charging: list[dict] = field(default_factory=list)
    # the storm warning entity (option storm_warning_entity_id): {"entity_id", "state", "attributes"} with only the attributes
    # engine/storm.py reads; None without the option
    storm_alert: dict | None = None


@dataclass
class JobResult:
    """Everything a worker job wants the event loop to publish."""

    states: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, dict[str, Any]] = field(default_factory=dict)
    notifications: list[tuple[str, str, str]] = field(default_factory=list)
    # push notifications (management/notifier.py): kind -> (True active / False over / None not evaluated, text parameters)
    conditions: dict[str, tuple[bool | None, dict[str, Any]]] = field(default_factory=dict)
    # something learned: kind -> whether it is ready now
    ready: dict[str, bool | None] = field(default_factory=dict)
    # the daily summary of a date: {"date": iso date, ...text parameters}, None when there is none to send
    summary: dict[str, Any] | None = None
    db_saves: dict[str, Any] = field(default_factory=dict)
    solar_kwh_increment: float = 0.0
    value: Any = None
    error: BaseException | None = None
    request_reload: bool = False
    worker_thread: str = ""
    duration: float = 0.0
