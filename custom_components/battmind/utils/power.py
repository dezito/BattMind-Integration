"""Window calculations on buffered recorder data.

The PyScript version asked the recorder for every window it needed (70+ queries per
planning run, running on the event loop). The integration keeps a rolling buffer of
states and hourly statistics per entity instead, and this module reproduces the old
``history.get_values`` / ``history.get_average_value`` interface from those buffers:

* short term: states inside the window plus the state at the window start, sampled at
  100 points with every state held until the next change (``include_start_time_state=True``).
  The PyScript version drew lines between the rows (``np.interp``), which is wrong for a
  sensor that only writes a row when it changes: a flow that jumps between 0 and 5000 W
  got ramps over the quiet minutes and its energy was several times too high.
* long term: hourly mean statistics for the window, interpolated to 100 points
* average = mean of both point sets

All functions are pure and run in the worker thread.
"""
from __future__ import annotations

import bisect
import datetime
import math
from typing import Iterable

NUM_POINTS = 100

_POWER_FACTORS_TO_W = {
    "mW": 0.001,
    "W": 1.0,
    "kW": 1000.0,
    "MW": 1000000.0,
    "GW": 1000000000.0,
}


def unique_entity_ids(entity_ids: Iterable[str | None] | None) -> list[str]:
    """The entity ids in order, without empty values and repeats (a sensor listed twice is counted once)."""
    return list(dict.fromkeys(entity_id for entity_id in entity_ids or [] if entity_id))


def unit_factor_to_w(unit: str | None) -> float:
    """Factor converting a power unit (kWh is treated as kW like power_convert) to W."""
    if not unit:
        return 1.0
    return _POWER_FACTORS_TO_W.get(unit.replace("h", ""), 1.0)


def _hold(points: list[tuple[float, float | None]], start_ts: float, end_ts: float, num: int = NUM_POINTS) -> list[float]:
    """The state at num evenly spaced times from start_ts to end_ts, each state held until the next one.

    A sensor writes a row only when its state changes, so a state is valid until the next row (not a
    point of a curve to draw a line through). points: sorted (ts, value|None); None is a state that
    is no number (unavailable, nan ...): it ends the hold of the value before it, and the times it
    covers give no value, so an average counts only the time that is known. Before the first row the
    first value is held back (a window that starts before the buffer).
    """
    if not points:
        return []
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    out = []
    span = end_ts - start_ts
    for i in range(num):
        x = start_ts + span * i / (num - 1) if num > 1 else start_ts
        j = max(bisect.bisect_right(xs, x) - 1, 0)
        value = ys[j]
        if value is not None:
            out.append(value)
    return out


def _interp(points: list[tuple[float, float]], start_ts: float, end_ts: float, num: int = NUM_POINTS) -> list[float]:
    """numpy.interp(linspace(start, end, num), xs, ys) without numpy."""
    if not points:
        return []
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    out = []
    span = end_ts - start_ts
    last = len(xs) - 1
    for i in range(num):
        x = start_ts + span * i / (num - 1) if num > 1 else start_ts
        if x <= xs[0]:
            out.append(ys[0])
            continue
        if x >= xs[last]:
            out.append(ys[last])
            continue
        j = bisect.bisect_right(xs, x) - 1
        x0, x1 = xs[j], xs[j + 1]
        y0, y1 = ys[j], ys[j + 1]
        out.append(y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0))
    return out


class WindowCalculator:
    """Calculate window values from state/statistics buffers.

    buffers:    entity_id -> sorted list of (epoch_ts, float|None)
    statistics: entity_id -> sorted list of (hour_start_epoch_ts, mean)
    units:      entity_id -> unit_of_measurement
    """

    def __init__(self, buffers, statistics, units, tz) -> None:
        self.buffers = buffers or {}
        self.statistics = statistics or {}
        self.units = units or {}
        self.tz = tz
        self._key_cache: dict[tuple[str, str], list[float]] = {}

    def _keys(self, kind: str, entity_id: str, rows: list) -> list[float]:
        cache_key = (kind, entity_id)
        keys = self._key_cache.get(cache_key)
        if keys is None:
            keys = [r[0] for r in rows]
            self._key_cache[cache_key] = keys
        return keys

    def _ts(self, naive: datetime.datetime) -> float:
        return naive.replace(tzinfo=self.tz).timestamp()

    def _short_points(self, entity_id: str, start_ts: float, end_ts: float) -> list[tuple[float, float | None]]:
        rows = self.buffers.get(entity_id)
        if not rows:
            return []
        keys = self._keys("short", entity_id, rows)
        # state at the window start (latest row at or before start) placed at start,
        # followed by every row strictly inside the window; a state that is no (finite)
        # number stays as None: it ends the hold of the value before it (see _hold)
        lo = bisect.bisect_right(keys, start_ts)
        hi = bisect.bisect_left(keys, end_ts)
        points: dict[float, float | None] = {}
        if lo > 0:
            points[start_ts] = rows[lo - 1][1]
        for ts, value in rows[lo:hi]:
            points[ts] = value
        return sorted(
            (ts, v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None)
            for ts, v in points.items()
        )

    def short_values(self, entity_id: str, start: datetime.datetime, end: datetime.datetime) -> list[float]:
        """The states of the window at up to 100 points (history.get_values(float_type=True, convert_to='W')).

        Each state is held until the next change, so the mean of the values is the time weighted mean
        of the window to within one point (a flow that is 5 kW for 30 s and 0 W for the rest of the hour
        is 42 W on average: 0 or 50 W with the points 36 s apart, never a line through the rows). Fewer
        than 100 values when the sensor was lost for part of the window; none when it never had a number.
        """
        if not entity_id:
            return []
        start, end = min(start, end), max(start, end)
        start_ts, end_ts = self._ts(start), self._ts(end)
        factor = unit_factor_to_w(self.units.get(entity_id))
        return [v * factor for v in _hold(self._short_points(entity_id, start_ts, end_ts), start_ts, end_ts)]

    def long_values(self, entity_id: str, start: datetime.datetime, end: datetime.datetime) -> list[float]:
        """Equivalent of history.get_longterm_values(state_type='mean', convert_to='W')."""
        rows = self.statistics.get(entity_id)
        if not entity_id or not rows:
            return []
        start, end = min(start, end), max(start, end)
        window_start = start.replace(minute=0, second=0, microsecond=0)
        window_end = end.replace(minute=0, second=0, microsecond=0)
        if window_start == window_end:
            window_end = window_start + datetime.timedelta(hours=1)
        start_ts, end_ts = self._ts(window_start), self._ts(window_end)
        keys = self._keys("long", entity_id, rows)
        lo = bisect.bisect_left(keys, start_ts)
        hi = bisect.bisect_left(keys, end_ts)
        points = [(ts, v) for ts, v in rows[lo:hi] if v is not None]
        factor = unit_factor_to_w(self.units.get(entity_id))
        return [v * factor for v in _interp(points, start_ts, end_ts)]

    def average(self, entity_id: str, start: datetime.datetime, end: datetime.datetime, error_state=0.0):
        """Equivalent of history.get_average_value(convert_to='W')."""
        if not entity_id:
            return error_state
        values = self.short_values(entity_id, start, end) + self.long_values(entity_id, start, end)
        if not values:
            return error_state
        return sum(values) / len(values)

    def last_numeric(self, entity_id: str, not_lost_before: datetime.datetime | None = None):
        """The last finite number recorded for the entity.

        not_lost_before: the number only counts if the sensor stopped giving numbers at or after this time (the next
        row after it, e.g. "unavailable"). A level that never changed has an old row but is still the level."""
        rows = self.buffers.get(entity_id) or []
        for index in range(len(rows) - 1, -1, -1):
            ts, value = rows[index]
            if isinstance(value, (int, float)) and math.isfinite(value):
                if not_lost_before is not None:
                    lost_ts = rows[index + 1][0] if index + 1 < len(rows) else ts
                    if lost_ts < self._ts(not_lost_before):
                        return None
                return value
        return None


def merge_rows(existing: list, new_rows: Iterable) -> list:
    """Merge rows (ts, value) keeping sort order and dropping duplicate timestamps."""
    merged = {ts: value for ts, value in existing}
    for ts, value in new_rows:
        merged[ts] = value
    return sorted(merged.items())
