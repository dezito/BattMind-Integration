"""Storm warning: the hours a warning covers, the energy the house needs in them and the battery level that has to be kept.

Pure functions, no Home Assistant objects. The warning is an entity the user chose (MeteoAlarm's binary sensor, see
parse_alert()): its state and attributes are read on the event loop and handed to the engine as a dict.

Rules (decided with the user):
 * Nothing happens without a start time: a warning whose onset is missing or unreadable is shown in the status and ignored.
 * The end is the warning's own end time; without one the window is STORM_DEFAULT_HOURS long (the number entity
   storm_cover_hours), counted from the start.
 * The need is the forecast house consumption of the window with no solar, plus a buffer from how far the forecast
   was off over the last days (at least STORM_MIN_BUFFER), plus the loss of discharging.
 * The level to keep at the end of an hour is the minimum level plus what is still needed after that hour, so it
   falls as the storm goes on. The hour before the window keeps all of it.
"""
from __future__ import annotations

import datetime
import math
import re
from dataclasses import dataclass, field

STORM_MIN_BUFFER = 0.20  # the buffer is at least this part of the need
STORM_MAX_BUFFER = 1.0  # and at most this part (a forecast that was off by more is not a reason to plan for double)
STORM_DEFAULT_HOURS = 12.0  # a warning with an onset and no end time covers this many hours
STORM_MAX_HOURS = 72.0  # the number entity's upper limit
STORM_MIN_LEVELS = (2, 3, 4)  # MeteoAlarm awareness level: yellow, orange, red
STORM_DEFAULT_LEVEL = 3
# MeteoAlarm awareness types that make a storm for the battery: wind, snow/ice, thunderstorms
STORM_TYPES = (1, 2, 3)
# what the DP is charged per kWh the battery is below the level to keep: steering only (not money, never in a total), so large
# that no price difference makes the plan accept a shortfall it can avoid
STORM_SHORTFALL_PENALTY = 1000.0
# the rule key of a charge that is made for the storm: it belongs to no rule switch, so it can't be switched off
STORM_RULE = "storm_warning"
# the attributes of the warning entity the snapshot keeps (the others, such as the headline and the area, name places)
STORM_ATTRIBUTES = ("onset", "start", "expires", "end", "awareness_level", "awareness_type", "severity")

# status values of sensor.battmind_storm_warning
STATUS_OFF = "off"  # no warning entity is chosen (the sensor is not made then; kept for the helper's sake)
STATUS_NONE = "no_warning"  # the entity says there is no warning
STATUS_UNAVAILABLE = "entity_unavailable"  # the entity is unavailable, unknown or missing (not "unavailable": that is HA's own state)
STATUS_WEAK = "below_level"  # a warning below the chosen level
STATUS_OTHER = "other_type"  # a warning of a type that is not a storm
STATUS_NO_START = "no_start"  # a warning without a readable start time: ignored
STATUS_UNREADABLE = "unreadable"  # the level or the type can't be read: ignored
STATUS_EXPIRED = "expired"  # the end is in the past
STATUS_INVALID = "invalid"  # the end is not after the start
STATUS_LATER = "later"  # the warning starts after the hours the plan covers
STATUS_COMING = "coming"  # a window ahead is planned for
STATUS_ACTIVE = "active"  # the warning is going on now

_FIRST_NUMBER = re.compile(r"^\s*(\d+)")
_SEVERITY_LEVEL = {"minor": 1, "moderate": 2, "severe": 3, "extreme": 4}
_START_KEYS = ("onset", "start")
_END_KEYS = ("expires", "end")


@dataclass
class StormWindow:
    """What the warning entity says, read with the rules above. start/end are naive local time."""

    status: str
    start: datetime.datetime | None = None
    end: datetime.datetime | None = None
    level: int | None = None
    kind: int | None = None
    end_from: str | None = None  # "entity" (the warning's own end) or "hours" (counted from the start)

    @property
    def planned(self) -> bool:
        """The plan keeps a level for this window."""
        return self.status in (STATUS_COMING, STATUS_ACTIVE)


@dataclass
class StormPlan:
    """The window with the energy it needs and the level to keep at the end of each planned hour."""

    window: StormWindow
    need_kwh: float = 0.0  # house consumption of the window (forecast, no solar), before the buffer and the loss
    buffer: float = 0.0  # part added for a forecast that is off
    buffer_from: str = "default"  # "measured" (from the days of the analysis) or "default" (the minimum)
    need_percent: float = 0.0  # battery level needed at the start (buffer and loss included), before the cap
    capped: bool = False  # the need is more than the battery holds above its minimum
    complete: bool = True  # False: an hour of the window has no consumption forecast, so the need is at least this much
    hours: int = 0  # plan hours that carry a level to keep
    floors: dict = field(default_factory=dict)  # hour timestamp -> % to have at the end of the hour


def _number(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _time(value, tz) -> datetime.datetime | None:
    """A time attribute (ISO 8601 text, or a datetime) as naive local time; None when it isn't a time."""
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        try:
            moment = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is None:
        return moment  # no offset: it is local time already (the entity's own clock)
    return moment.astimezone(tz).replace(tzinfo=None)


def _first(attributes: dict, keys: tuple):
    for key in keys:
        if attributes.get(key) not in (None, ""):
            return attributes[key]
    return None


def _awareness_level(attributes: dict) -> int | None:
    """MeteoAlarm "3; orange; Severe" -> 3. Without that attribute the CAP severity word (Moderate = 2, Severe = 3, Extreme = 4)."""
    value = attributes.get("awareness_level")
    if value not in (None, ""):
        match = _FIRST_NUMBER.match(str(value))
        return int(match.group(1)) if match else None
    severity = attributes.get("severity")
    if isinstance(severity, str):
        return _SEVERITY_LEVEL.get(severity.strip().lower())
    return None


def _awareness_type(attributes: dict) -> int | None:
    """MeteoAlarm "1; wind" -> 1."""
    value = attributes.get("awareness_type")
    if value in (None, ""):
        return None
    match = _FIRST_NUMBER.match(str(value))
    return int(match.group(1)) if match else None


def parse_alert(
    state: str | None,
    attributes: dict | None,
    now: datetime.datetime,
    tz,
    min_level: int = STORM_DEFAULT_LEVEL,
    cover_hours: float = STORM_DEFAULT_HOURS,
) -> StormWindow:
    """The window of the warning entity (MeteoAlarm's binary sensor), or why it is ignored.

    MeteoAlarm: state "on" = an alert exists and has not expired (also one that has not started yet: onset is never checked
    by the integration), the attributes are the alert's own: onset, expires (ISO 8601 with an offset), awareness_level
    ("3; orange; Severe") and awareness_type ("1; wind"). Only the onset starts a window: effective is when the message
    was issued, not when the weather begins. now: naive local time.
    """
    if state in (None, "unavailable", "unknown"):
        return StormWindow(STATUS_UNAVAILABLE)
    if state != "on":
        return StormWindow(STATUS_NONE)
    attributes = attributes if isinstance(attributes, dict) else {}
    level = _awareness_level(attributes)
    kind = _awareness_type(attributes)
    if level is None or kind is None:
        # a warning that can't be classified is never taken for a storm
        return StormWindow(STATUS_UNREADABLE, level=level, kind=kind)
    if kind not in STORM_TYPES:
        return StormWindow(STATUS_OTHER, level=level, kind=kind)
    if level < min_level:
        return StormWindow(STATUS_WEAK, level=level, kind=kind)
    start = _time(_first(attributes, _START_KEYS), tz)
    if start is None:
        return StormWindow(STATUS_NO_START, level=level, kind=kind)
    end = _time(_first(attributes, _END_KEYS), tz)
    end_from = "entity"
    if end is None:
        end_from = "hours"
        end = start + datetime.timedelta(hours=min(max(_number(cover_hours) or STORM_DEFAULT_HOURS, 1.0), STORM_MAX_HOURS))
    if end <= now:
        return StormWindow(STATUS_EXPIRED, start, end, level, kind, end_from)
    if end <= start:
        return StormWindow(STATUS_INVALID, start, end, level, kind, end_from)
    status = STATUS_ACTIVE if start <= now else STATUS_COMING
    return StormWindow(status, start, end, level, kind, end_from)


def forecast_buffer(errors: dict | None, forecast_type: str, min_days: int) -> tuple[float, str]:
    """(buffer, source): the part added to the need for a forecast that is off.

    errors: forecast_errors() of the last days. The buffer is the forecast type's mean absolute error as a part of the
    measured consumption, at least STORM_MIN_BUFFER and at most STORM_MAX_BUFFER. Fewer than min_days days with a recorded
    forecast, or a type without a relative error, gives the minimum: an error that is not known is never taken as none.
    """
    if isinstance(errors, dict) and (_number(errors.get("days")) or 0) >= min_days:
        kind = errors.get(forecast_type)
        relative = _number(kind.get("relative")) if isinstance(kind, dict) else None
        if relative is not None and relative >= 0.0:
            return min(max(relative, STORM_MIN_BUFFER), STORM_MAX_BUFFER), "measured"
    return STORM_MIN_BUFFER, "default"


def storm_plan(
    window: StormWindow,
    hours: list,
    consumption: dict,
    efficiency: float,
    battery_size: float,
    level_min: float,
    level_max: float,
    buffer: float,
    buffer_from: str = "default",
) -> StormPlan:
    """The level to keep at the end of each plan hour for the window.

    hours: the plan's hours (anything with .timestamp, naive local, on the hour: the hours that have a price); consumption: hour
    timestamp -> forecast house kWh in that hour with no solar (the whole hour), for every hour of the forecast, priced or not.
    An hour of the window counts the part of it the window covers. An hour of the window (from the plan's first hour) that has
    no forecast adds nothing and makes plan.complete False: a need that is not known is never taken as none, it is shown as
    at least this much. No battery, a window that is not planned or no hours: no floors.
    """
    plan = StormPlan(window, buffer=buffer, buffer_from=buffer_from)
    if not window.planned or not hours or battery_size <= 0.0 or efficiency <= 0.0 or level_max <= level_min:
        return plan
    one_hour = datetime.timedelta(hours=1)
    covered = []  # (hour timestamp, kWh of the hour inside the window)
    hour = max(window.start.replace(minute=0, second=0, microsecond=0), hours[0].timestamp)
    # an end time far away (a wrong expires) is not walked hour by hour: the part past STORM_MAX_HOURS is not counted
    end = min(window.end, hour + datetime.timedelta(hours=STORM_MAX_HOURS))
    if end < window.end:
        plan.complete = False
    while hour < end:
        overlap = (min(hour + one_hour, window.end) - max(hour, window.start)).total_seconds() / 3600.0
        kwh = _number(consumption.get(hour))
        # a forecast of 0 kWh is no forecast: the planner gives 0 to an hour it has no history for (a new installation)
        if kwh is None or kwh <= 0.0:
            plan.complete = False
        elif overlap > 0.0:
            covered.append((hour, kwh * min(overlap, 1.0)))
        hour += one_hour
    if not covered:
        return plan
    plan.need_kwh = sum(kwh for _, kwh in covered)
    span = level_max - level_min

    def percent(kwh: float) -> float:
        # house kWh -> battery level: the buffer on top, and the battery gives 1/efficiency kWh for every kWh it delivers
        return kwh * (1.0 + buffer) / efficiency / battery_size * 100.0

    plan.need_percent = percent(plan.need_kwh)
    plan.capped = plan.need_percent > span + 1e-9
    first = covered[0][0]
    before = [hour for hour in hours if hour.timestamp < first]
    if before:
        plan.floors[before[-1].timestamp] = level_min + min(percent(plan.need_kwh), span)  # all of it before the first hour
    for hour in hours:
        if any(timestamp == hour.timestamp for timestamp, _ in covered):
            remaining = sum(kwh for timestamp, kwh in covered if timestamp > hour.timestamp)
            plan.floors[hour.timestamp] = level_min + min(percent(remaining), span)
    plan.hours = len(plan.floors)
    return plan
