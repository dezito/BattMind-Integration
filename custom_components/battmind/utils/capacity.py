"""Battery capacity bounds, measured from the battery's own power and level (always measured; the plan counts on them only through
used_capacity(), with the switch learn_battery_capacity on).

The battery power sensor gives the energy that went through it while the battery level moved a number of points (the sensor of a
usual setup sees the inverter's AC side; one on the battery's DC side gives the same bounds, closer together). Per 100 % of battery
level that is:

- charging: energy in / points = capacity / charging efficiency, an UPPER bound of the usable capacity (the battery got at most
  that much; the inverter's own use pushes the figure up too);
- discharging: energy out / points = capacity x discharging efficiency, a LOWER bound.

The efficiencies are not known, so the capacity itself can not be separated from them (only the round trip, discharging /
charging, is free of it). The two sides are therefore kept and shown apart, never blended. This assumes that the battery level
follows the stored energy evenly over a run; a battery management system that does not (or recalibrates) moves the figures, which
is part of why a bound has to be clearly (CAPACITY_NOISE) away from the configured capacity before it says anything, and why the
status only suggests. Energy the other way (up to 3 %) and idle time (up to 10 %) that measure_efficiency still accepts are not netted.

An episode is an unbroken run of measurements (utils/efficiency.measure_efficiency) in one direction that covers at least
CAPACITY_MIN_POINTS of battery level. Both ends are the times the level changed, so the rounding of the level is the same at both
ends and cancels, as in the efficiency. A measurement that does not continue the run (a gap, the other direction, a rejected
measurement) ends it; a jump of the level (calibration) ends it too and is itself in no run. The run need not be complete: any
unbroken stretch of enough points measures the same. Timestamps are epoch seconds, so time zones and daylight saving time do not
matter.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from typing import Any

CAPACITY_MIN_POINTS = 40.0     # battery level points an episode must cover
CAPACITY_MAX_STEP = 10.0       # a single step of the level above this is a jump (calibration): the run ends before it
CAPACITY_MAX_GAP_S = 1.0       # seconds between two measurements that still continue one run
CAPACITY_MAX_SECONDS = 86400   # an episode this long is closed and a new one started
CAPACITY_OPEN_MAX_AGE = 7200   # seconds without a continuing measurement after which an open run is closed (BATTERY_LEVEL_MAX_AGE)
CAPACITY_KEEP = 20             # episodes kept per direction
CAPACITY_MIN_EPISODES = 3      # episodes per direction before a bound is shown
CAPACITY_NOISE = 0.05          # share of the configured capacity a bound may differ without it meaning anything (what the measurement can not separate)
CAPACITY_USED_STEP = 0.1       # kWh: the capacity the plan counts on is rounded down to this, so a new run does not change the plan every time
CAPACITY_USED_MIN_SHARE = 0.5  # a measured capacity under this share of the configured one is not believed
DIRECTIONS = ("charging", "discharging")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)  # (a huge stored integer does not fit)
    except OverflowError:
        return None
    return number if number == number and abs(number) != float("inf") else None


def _episode(value: Any, direction: str | None = None) -> dict | None:
    """A stored episode {"l0", "l1", "t0", "t1", "ac"} with numbers only, or None. With a direction it is a closed one: it covers
    CAPACITY_MIN_POINTS and the level moved the way of the direction."""
    if not isinstance(value, Mapping):
        return None
    result = {key: _number(value.get(key)) for key in ("l0", "l1", "t0", "t1", "ac")}
    if any(number is None for number in result.values()) or result["ac"] <= 0 or result["t1"] < result["t0"]:
        return None
    if direction is not None:
        moved = result["l1"] - result["l0"]
        if abs(moved) < CAPACITY_MIN_POINTS or (moved > 0) != (direction == "charging"):
            return None
    return result  # type: ignore[return-value]


def clean_capacity(value: Any) -> dict:
    """The stored capacity measurements as {"episodes": {direction: [...]}, "open": episode + "d" | None, "until": seconds}; whatever is
    malformed is dropped (a broken entry never stops the analysis)."""
    value = value if isinstance(value, Mapping) else {}
    stored = value.get("episodes") if isinstance(value.get("episodes"), Mapping) else {}
    episodes = {
        direction: [episode for episode in (_episode(row, direction) for row in (stored.get(direction) if isinstance(stored.get(direction), list) else [])) if episode][-CAPACITY_KEEP:]
        for direction in DIRECTIONS
    }
    run = _episode(value.get("open"))
    if run is not None and value["open"].get("d") in DIRECTIONS:
        run["d"] = value["open"]["d"]
    else:
        run = None
    result: dict = {"episodes": episodes, "open": run}
    until = _number(value.get("until"))
    if until is not None:
        result["until"] = until
    # what they were measured with (recording.py): the power sensor, the level sensor, the sign and the configured size
    sensors = value.get("sensors")
    if (
        isinstance(sensors, list) and len(sensors) == 4 and all(isinstance(item, str) for item in sensors[:2])
        and isinstance(sensors[2], bool) and _number(sensors[3]) is not None
    ):
        result["sensors"] = list(sensors)
    return result


def _close(database: dict, run: dict | None) -> None:
    """Keep the run as an episode when it covers enough points."""
    if run is None or abs(run["l1"] - run["l0"]) < CAPACITY_MIN_POINTS:
        return
    episodes = database["episodes"][run["d"]]
    episodes.append({key: round(run[key], 6) for key in ("l0", "l1", "t0", "t1", "ac")})
    del episodes[:-CAPACITY_KEEP]


def add_capacity_measurements(database: dict, details: list, now: float) -> None:
    """Add the measurements (utils/efficiency.measure_efficiency details: direction, level at the start %, level at the end %, AC kWh,
    seconds, time it ended) in time order to the run that is open in database (clean_capacity), and close a run no measurement has
    continued for CAPACITY_OPEN_MAX_AGE before now."""
    for direction, start_level, end_level, ac_kwh, seconds, ended in details:
        started = ended - seconds
        run = database.get("open")
        continues = (
            run is not None
            and run["d"] == direction
            and abs(run["t1"] - started) <= CAPACITY_MAX_GAP_S
            and abs(run["l1"] - start_level) < 1e-6
            and ended - run["t0"] <= CAPACITY_MAX_SECONDS
        )
        if not continues:
            _close(database, run)
            run = {"d": direction, "l0": float(start_level), "l1": float(start_level), "t0": started, "t1": started, "ac": 0.0}
        if abs(end_level - start_level) > CAPACITY_MAX_STEP or ac_kwh <= 0:
            # a jump of the level (calibration) is no measurement of the battery: the run before it ends there (kept when long
            # enough), and the jump's own energy is in no run
            if continues:
                _close(database, run)
            database["open"] = None
            continue
        run["l1"], run["t1"], run["ac"] = float(end_level), float(ended), run["ac"] + float(ac_kwh)
        database["open"] = run
    run = database.get("open")
    if run is not None and now - run["t1"] > CAPACITY_OPEN_MAX_AGE:
        _close(database, run)
        database["open"] = None


def _per_100(episodes: list[dict]) -> list[float]:
    return [episode["ac"] / abs(episode["l1"] - episode["l0"]) * 100.0 for episode in episodes if abs(episode["l1"] - episode["l0"]) > 0]


def capacity_summary(database: Any, configured_kwh: float) -> dict:
    """What the measured episodes say, per side, for the analysis sensor. A side is shown from CAPACITY_MIN_EPISODES episodes.

    charging.kwh is the upper bound and discharging.kwh the lower bound of the usable capacity (kWh per 100 % of level, median of the
    episodes); round_trip is discharging / charging. status: too_few (not enough to judge), below (the charging bound is under the
    configured capacity by more than CAPACITY_NOISE: the usable capacity is smaller), above (the discharging bound is over it: it
    is larger), inconsistent (more energy came out than went in per point: the sensor measures the two directions differently, or
    the level does not follow the energy evenly) or within (neither bound contradicts the configured capacity).
    """
    episodes = clean_capacity(database)["episodes"]
    sides: dict[str, dict] = {}
    for direction in DIRECTIONS:
        values = _per_100(episodes[direction])
        sides[direction] = {
            "episodes": len(values),
            "kwh": round(statistics.median(values), 2) if len(values) >= CAPACITY_MIN_EPISODES else None,
            "min": round(min(values), 2) if values else None,
            "max": round(max(values), 2) if values else None,
        }
    upper, lower = sides["charging"]["kwh"], sides["discharging"]["kwh"]
    configured = float(configured_kwh) if configured_kwh and configured_kwh > 0 else None
    status = "too_few"
    if upper is not None and lower is not None and lower > upper * (1 + CAPACITY_NOISE):
        status = "inconsistent"
    elif configured is not None:
        if upper is not None and upper < configured * (1 - CAPACITY_NOISE):
            status = "below"
        elif lower is not None and lower > configured * (1 + CAPACITY_NOISE):
            status = "above"
        elif upper is not None and lower is not None:
            status = "within"
    return {
        "configured": configured,
        "charging": sides["charging"],
        "discharging": sides["discharging"],
        "round_trip": round(lower / upper, 3) if upper and lower is not None else None,
        "status": status,
    }


def used_capacity(summary: dict, configured_kwh: float) -> float | None:
    """The usable capacity (kWh per 100 % of level) the plan may count on, or None to keep the configured one.

    Only when capacity_summary says "below": the charging bound is an upper bound of the usable capacity, so the capacity is taken
    as that bound rounded DOWN to CAPACITY_USED_STEP (never past what the runs show) and never above the configured capacity. A
    bound under CAPACITY_USED_MIN_SHARE of the configured capacity is not believed (a sensor or level problem, not a battery that
    lost half of itself): None. Both directions need their runs. The efficiency is not known, so this is the least that moves the plan
    toward the measurement: it stays on the cautious side of the configured size, and with a configured efficiency of about 98 % or
    better, or a discharging efficiency below the measured round trip, it can land a little on the pessimistic side of the runs (the
    plan then keeps more loss than measured, never less). Where a measured efficiency curve is in use the caller keeps the
    configured capacity (engine/battery.py)."""
    upper = summary.get("charging", {}).get("kwh") if summary.get("status") == "below" else None
    # both directions need their runs: the charging bound alone could not be checked against the discharging one (inconsistent)
    if upper is None or summary.get("discharging", {}).get("kwh") is None or not configured_kwh or configured_kwh <= 0:
        return None
    if upper < configured_kwh * CAPACITY_USED_MIN_SHARE:
        return None
    size = math.floor(upper / CAPACITY_USED_STEP + 1e-9) * CAPACITY_USED_STEP
    return round(min(size, configured_kwh), 3)
