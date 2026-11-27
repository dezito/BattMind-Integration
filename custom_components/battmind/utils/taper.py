"""Charging power that falls with the battery level (taper), measured from the battery's own power and level.

A battery charges at less than its limit when it is nearly full. The plan used to expect the full charge rate up to the maximum
level, which promises more energy than the battery takes. Here every measurement of the efficiency (utils/efficiency.py: between
two changes of the battery level, with the AC energy of the battery power sensor in that time) also tells the average AC power at
a battery level. Measurements are kept per band of TAPER_BAND % of the battery level; a band tapers when the power the battery
reached in it is clearly below the best band's.

Conservative on purpose:
 * only measurements from hours where the plan asked for grid charging are kept (engine/analysis/recording.py): there the
   battery, not the source, sets the power. A charge by the sun is as slow as the sun is, and would teach a taper that
   is not there. Only hours that proved a grid charge count (plan_check: grid charging held 3/4 of the hour and the
   battery took more than the sun could have given). The recorder history has no plan, so nothing is read from it;
 * the power of a band is the 90th percentile of its measurements, not the mean: a solar limited or partial charge is slow
   without the battery being the reason, and must not teach a cap that is too low;
 * a band needs TAPER_MIN_MEASUREMENTS measurements from TAPER_MIN_DAYS different days, and a cap is only learned relative to the
   best band that has enough, so a battery that is only ever charged slowly keeps the configured rate;
 * the cap of an hour is the lowest of the bands the hour can reach (charge_cap_kwh), and never above the configured limit.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

TaperCaps = tuple[tuple[int, float], ...]  # ((band start %, cap kW), ...) of the bands that taper, sorted

TAPER_BAND = 10  # % of battery level per band
TAPER_KEEP = 200  # measurements kept per band (the newest)
TAPER_MIN_MEASUREMENTS = 6  # measurements before a band is judged
TAPER_MIN_DAYS = 3  # different days they come from
TAPER_PERCENTILE = 0.9
TAPER_RATIO = 0.9  # a band tapers when its power is below this part of the best band's


def band_of(level: float) -> int:
    """Start (%) of the band a battery level is in; 100 % is in the top band."""
    return int(min(max(level, 0.0), 100.0 - 1e-9) // TAPER_BAND) * TAPER_BAND


def add_taper_measurement(database: dict, start_level: float, end_level: float, ac_kwh: float, seconds: float, day: int) -> None:
    """A charging measurement (the average AC power between two battery levels) in the band of its middle level.

    day: any whole number that changes with the calendar day (the epoch day).
    """
    if seconds <= 0 or ac_kwh <= 0 or not all(math.isfinite(value) for value in (start_level, end_level, ac_kwh, seconds)):
        return
    power = round(ac_kwh * 3_600_000.0 / seconds / 10.0) * 10  # W, to 10 W
    rows = database.setdefault("charging", {}).setdefault(str(band_of((start_level + end_level) / 2.0)), [])
    rows.append([power, int(day)])
    del rows[:-TAPER_KEEP]


def _percentile(values: list[float], share: float) -> float:
    values = sorted(values)
    position = share * (len(values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


def band_powers(database: Any) -> dict[int, dict]:
    """Every band with measurements: {band: {"power": W (90th percentile), "n": measurements, "days": distinct days, "judged": enough}}."""
    result: dict[int, dict] = {}
    for key, rows in ((database or {}).get("charging") or {}).items() if isinstance(database, Mapping) else ():
        try:
            band = int(key)
            powers = [float(row[0]) for row in rows if float(row[0]) > 0]
            days = {int(row[1]) for row in rows}
        except (TypeError, ValueError, IndexError):
            continue
        if not powers:
            continue
        result[band] = {
            "power": _percentile(powers, TAPER_PERCENTILE),
            "n": len(powers),
            "days": len(days),
            "judged": len(powers) >= TAPER_MIN_MEASUREMENTS and len(days) >= TAPER_MIN_DAYS,
        }
    return result


def taper_caps(database: Any, max_kw: float) -> TaperCaps:
    """The bands that taper with the cap (kW) the plan may count on there, at most max_kw (the configured limit).

    Empty without a judged band that reached a power well above the others': a cap is only ever relative to what the same
    battery showed it can do.
    """
    bands = {band: row for band, row in band_powers(database).items() if row["judged"]}
    if len(bands) < 2:
        return ()
    best = max(row["power"] for row in bands.values())
    caps = {band: min(row["power"] / 1000.0, max_kw) for band, row in bands.items() if row["power"] < TAPER_RATIO * best}
    return tuple(sorted((band, round(cap, 3)) for band, cap in caps.items() if cap > 0))


def charge_energy_kwh(
    caps: Sequence[tuple[int, float]], level: float, hours: float, max_kw: float, kwh_per_percent: float, efficiency: float
) -> float:
    """AC kWh the battery takes in `hours` (an hour, or the part of it that is left) that start at `level` (%).

    The battery charges at the cap of the band its level is in, so the hour is walked through the bands: the full rate (max_kw)
    where there is no cap, less where it tapers, and a part of an hour spent in each band. Never above max_kw * hours. Without
    caps that is exactly max_kw * hours: the plan is what it was.
    kwh_per_percent: battery kWh per % of level; efficiency: share of the AC energy that gets into the battery.
    """
    if not caps or hours <= 0:
        return max_kw * max(hours, 0.0)
    by_band = dict(caps)
    energy, remaining, current = 0.0, hours, max(level, 0.0)
    while remaining > 1e-9 and current < 100.0:
        band = band_of(current)
        rate = min(max_kw, by_band.get(band, max_kw))
        if rate <= 0:
            break
        room_ac = (band + TAPER_BAND - current) * kwh_per_percent / max(efficiency, 1e-6)
        time_needed = room_ac / rate
        if time_needed >= remaining:
            energy += rate * remaining
            break
        energy += room_ac
        remaining -= time_needed
        current = band + TAPER_BAND
    return energy


def level_before_charge(
    caps: Sequence[tuple[int, float]], target: float, hours: float, max_kw: float, kwh_per_percent: float, efficiency: float
) -> float:
    """The lowest level (%) an hour of charging can start at and still end at `target` (0 when it can reach it from empty)."""
    if kwh_per_percent <= 0:
        return target

    def end_level(start: float) -> float:
        return start + charge_energy_kwh(caps, start, hours, max_kw, kwh_per_percent, efficiency) * efficiency / kwh_per_percent

    if end_level(0.0) >= target:
        return 0.0
    low, high = 0.0, target
    for _ in range(40):
        middle = (low + high) / 2.0
        if end_level(middle) >= target:
            high = middle
        else:
            low = middle
    return high
