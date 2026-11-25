"""Plan check: did the battery do what the published action said, hour by hour?

Only observes. It reads the actions BattMind published (sensor.<prefix>_powerwall_action) and the energy
the battery measured, and never sends a command or changes the plan. An hour is only judged when the result
is clear: one action held most of it, every sensor it needs had numbers, the battery was not at a limit when
the hour started, and what the battery did can't be explained by normal operation (a battery that charges
from a solar surplus or discharges into the house is not proof that a grid charge or a forced discharge
was run). Everything else is counted as skipped, never as a match.
"""
from __future__ import annotations

import datetime
import logging
import math

from ...utils.power import unique_entity_ids
from .helpers import _is_num

_LOGGER = logging.getLogger(__name__)

PLAN_CHECK_DAYS = 14  # days of hours kept and counted (today included)
PLAN_CHECK_MIN_SHARE = 0.75  # one action must have held this share of the hour to be judged
PLAN_CHECK_MIN_COVERAGE = 0.9  # share of the hour the published actions must be known for (not a restart or a gap)
PLAN_CHECK_MAX_COVERAGE = 1.05  # more than this means the hour was counted twice: not judged
PLAN_CHECK_MIN_POINTS = 90  # of the up to 100 points a sensor's window gives: fewer means it was lost for part of it
PLAN_CHECK_MIN_KWH = 0.1  # kWh in an hour (100 W on average): less counts as a battery that doesn't move (standby, noise)
PLAN_CHECK_MIN_LOAD_KWH = 0.3  # what normal operation would have moved, at least, for a "did not discharge" ("did not charge") check
PLAN_CHECK_LEVEL_MARGIN = 2.0  # % from a battery limit at the start of the hour: the battery can't be expected to move
PLAN_CHECK_LATEST = 3  # mismatching hours in the published summary

# the actions that ask the battery for something that can be measured; discharge_allowed is normal operation
CHECKED_ACTIONS = frozenset({"grid_charging", "force_discharge", "force_solar_only_charging", "blocked_solar_charge", "battery_stopped"})
HOUR_SECONDS = 3600.0
LEVEL_WINDOW = datetime.timedelta(minutes=5)


def hour_key(start: datetime.datetime) -> str:
    return start.strftime("%Y-%m-%dT%H")


def _first_day(today: datetime.date) -> str:
    """The oldest date that counts: PLAN_CHECK_DAYS dates, today included."""
    return (today - datetime.timedelta(days=PLAN_CHECK_DAYS - 1)).isoformat()


def level_margin(battery_kwh: float) -> float:
    """% from a limit where the battery can't be expected to move: at least 2 %, and room for twice the energy
    a check needs, which is more than 2 % on a small battery."""
    return max(PLAN_CHECK_LEVEL_MARGIN, 200.0 * PLAN_CHECK_MIN_KWH / battery_kwh) if battery_kwh > 0 else PLAN_CHECK_LEVEL_MARGIN


def judge_hour(
    seconds: dict[str, float], row: dict, level_start: float | None, level_min: float, level_max: float, ambiguous: bool = False,
    margin: float = PLAN_CHECK_LEVEL_MARGIN,
) -> dict | None:
    """The verdict of one hour, or None when there is nothing to judge (normal operation, or no action known).

    seconds: the time each published action held in the hour; row: the hour's measurement (kWh, like
    analysis_db["hours"]); level_start: the battery level when the hour started, None when a sensor had no numbers
    (then nothing is judged); level_min/level_max: the lowest level the battery discharges to (the usage reserve
    counts) and the highest it charges to; ambiguous: the hour comes twice (the end of summer time).
    Returns {"a": action, "c": kWh charged, "d": kWh discharged, "ok": True/False, "skip": reason when ok is None}.
    """
    known = {action: value for action, value in seconds.items() if isinstance(value, (int, float)) and value > 0}
    if not known:
        return None
    if known.get("test"):
        # the user ran the test of the automation in this hour: the battery moved because of it, not because of the plan
        planned = {action: value for action, value in known.items() if action != "test"}
        return {"a": max(planned, key=planned.get) if planned else "test", "ok": None, "skip": "test"}
    action, held = max(known.items(), key=lambda item: item[1])
    if action not in CHECKED_ACTIONS:
        return None
    entry: dict = {"a": action, "ok": None}

    def number(key: str, default: float | None = None) -> float | None:
        value = row.get(key, default)
        return float(value) if _is_num(value) and math.isfinite(value) else None

    # use is the consumption without any exclusion; old rows may not have it
    charge, discharge, home, solar = (number(key) for key in ("charge", "discharge", "home", "solar"))
    use, ignored = number("use", home), number("ignored", 0.0)
    # a missing number (also NaN) is never read as "the battery didn't move"
    if level_start is None or not math.isfinite(level_start) or None in (charge, discharge, home, solar, use, ignored):
        entry["skip"] = "no_data"
        return entry
    entry["c"], entry["d"] = round(charge, 3), round(discharge, 3)

    # what normal operation could have moved, the largest and the smallest the house load can be
    load_max, load_min = max(home, use), min(home, use)
    could_charge_from_sun = max(solar - load_min, 0.0)  # the most normal operation charges from the sun
    could_discharge_to_house = max(load_max - solar, 0.0)  # the most normal operation discharges into the house
    # a battery that follows the action moves one way: what is compared with normal operation is the net flow of the hour
    net_charge, net_discharge = charge - discharge, discharge - charge
    full = level_start >= level_max - margin
    empty = level_start <= level_min + margin

    total = sum(known.values())
    if ambiguous:
        entry["skip"] = "twice"
    elif total < PLAN_CHECK_MIN_COVERAGE * HOUR_SECONDS:
        entry["skip"] = "partial"
    elif total > PLAN_CHECK_MAX_COVERAGE * HOUR_SECONDS:
        entry["skip"] = "twice"
    elif held < PLAN_CHECK_MIN_SHARE * HOUR_SECONDS:
        entry["skip"] = "changed"
    elif action == "grid_charging":
        if full:
            entry["skip"] = "limit"
        elif charge < PLAN_CHECK_MIN_KWH:
            entry["ok"] = False
        elif net_charge - could_charge_from_sun >= PLAN_CHECK_MIN_KWH:
            entry["ok"] = True  # more than the sun could have charged
        else:
            entry["skip"] = "unproven"  # normal operation charges that much from the sun too
    elif action == "force_discharge":
        if empty:
            entry["skip"] = "limit"
        elif discharge < PLAN_CHECK_MIN_KWH:
            entry["ok"] = False
        elif net_discharge - could_discharge_to_house >= PLAN_CHECK_MIN_KWH:
            entry["ok"] = True  # more than the house used: it was sold
        else:
            entry["skip"] = "unproven"  # normal operation discharges that much into the house too
    elif action == "force_solar_only_charging":
        # only "no discharge" is checked, never that it charges: there may be no sun
        if empty:
            entry["skip"] = "limit"
        elif load_min - solar < PLAN_CHECK_MIN_LOAD_KWH:
            entry["skip"] = "no_load"
        else:
            entry["ok"] = discharge < PLAN_CHECK_MIN_KWH
    elif action == "blocked_solar_charge":
        if full:
            entry["skip"] = "limit"
        elif ignored >= PLAN_CHECK_MIN_KWH:
            entry["skip"] = "ev"  # the blueprint runs the "while the car is charging" actions instead
        elif solar - load_max < PLAN_CHECK_MIN_LOAD_KWH:
            entry["skip"] = "no_surplus"
        else:
            entry["ok"] = charge < PLAN_CHECK_MIN_KWH
    else:  # battery_stopped: neither charges nor discharges
        would_charge = solar - load_max >= PLAN_CHECK_MIN_LOAD_KWH and not full
        would_discharge = load_min - solar >= PLAN_CHECK_MIN_LOAD_KWH and not empty
        if not (would_charge or would_discharge):
            entry["skip"] = "no_load"  # normal operation would not have moved the battery either
        else:
            entry["ok"] = charge < PLAN_CHECK_MIN_KWH and discharge < PLAN_CHECK_MIN_KWH
    return entry


def summarize_plan_check(table: dict, today: datetime.date) -> dict:
    """What is published: the judged hours of the last PLAN_CHECK_DAYS, how many matched and the latest mismatches."""
    first = _first_day(today)
    hours = {key: entry for key, entry in table.items() if isinstance(entry, dict) and isinstance(key, str) and key[:10] >= first}
    judged = {key: entry for key, entry in hours.items() if isinstance(entry.get("ok"), bool)}
    matching = sum(1 for entry in judged.values() if entry["ok"])
    mismatches = sorted((key for key, entry in judged.items() if not entry["ok"]), reverse=True)[:PLAN_CHECK_LATEST]
    return {
        "days": PLAN_CHECK_DAYS,
        "checked_hours": len(judged),
        "matching_hours": matching,
        "skipped_hours": len(hours) - len(judged),
        "match_percent": round(100.0 * matching / len(judged)) if judged else None,
        "latest_mismatches": [
            {"hour": key, "action": judged[key].get("a"), "charged_kwh": judged[key].get("c"), "discharged_kwh": judged[key].get("d")}
            for key in mismatches
        ],
    }


class PlanCheck:
    """The time each published action held per hour, and the verdict of the finished hour."""

    def plan_check_note(self, action: str) -> None:
        """Called each time the action is published: the time since the last call counts for the action published then."""
        try:
            now = self.now()
            self._plan_check_credit(now)
            self._plan_tally["last"], self._plan_tally["action"] = now, action
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Plan check could not note the action")

    def _plan_check_credit(self, until: datetime.datetime) -> None:
        """Add the time from the last call to until to the hours, to the action that held. A gap longer than two
        calculation intervals (a restart, a pause) is not counted, so the hour is seen as incomplete."""
        tally = self._plan_tally
        last, action = tally["last"], tally["action"]
        if last is None or action is None or until <= last:
            return
        end = min(until, last + datetime.timedelta(minutes=2 * int(self.config["cron_interval"])))
        cursor = last
        while cursor < end:
            start = cursor.replace(minute=0, second=0, microsecond=0)
            segment_end = min(end, start + datetime.timedelta(hours=1))
            hour = tally["hours"].setdefault(hour_key(start), {})
            hour[action] = hour.get(action, 0.0) + (segment_end - cursor).total_seconds()
            cursor = segment_end

    def _plan_check_level(self, start: datetime.datetime) -> float | None:
        """The battery level when the hour started, if the battery power, the consumption, the ignored consumers and the
        solar production had numbers for nearly all of the hour (or the minutes before it, for the level); None
        otherwise. The level is read from the sensor's own states, not from BattMind's fallback value (100 when the
        sensor is lost). A lost solar sensor counts as 0 W in the measuring, which is only right in the dark."""
        wc = getattr(self, "_wc", None)
        if wc is None or not self._buffered_since(start):
            return None
        ids = self.config["home"]["entity_ids"]
        end = start.replace(minute=59, second=59, microsecond=0)
        needed = [ids["powerwall_watt_flow_entity_id"], ids["power_consumption_entity_id"], *unique_entity_ids(ids["ignore_consumption_from_entity_ids"])]
        solar_id = self.config["solar"]["entity_ids"]["production_entity_id"]
        if solar_id:
            sun = self.get_sun_events(start)
            if not (end <= sun["sunrise"] or start >= sun["sunset"]):  # not a dark hour
                needed.append(solar_id)
        for entity_id in needed:
            if len(wc.short_values(entity_id, start, end)) < PLAN_CHECK_MIN_POINTS:
                return None
        level = wc.short_values(ids["powerwall_battery_level_entity_id"], start - LEVEL_WINDOW, start)
        return level[-1] if len(level) >= PLAN_CHECK_MIN_POINTS else None

    def _plan_check_ambiguous(self, start: datetime.datetime) -> bool:
        """Whether the hour comes twice in the configured time zone (the end of summer time). The measuring reads the first
        one, so the second can't be judged."""
        tz = getattr(getattr(self, "_wc", None), "tz", None)
        if tz is None:
            return False
        return start.replace(tzinfo=tz, fold=0).utcoffset() != start.replace(tzinfo=tz, fold=1).utcoffset()

    def _plan_check_record_hour(self, start: datetime.datetime) -> None:
        """hour_end: judge the hour that starts at start from what the actions held and what the battery measured."""
        key = hour_key(start)
        self._plan_check_credit(start + datetime.timedelta(hours=1))  # the last minute of the hour is the last action's
        tally = self._plan_tally
        if tally["last"] is not None:
            tally["last"] = max(tally["last"], start + datetime.timedelta(hours=1))
        seconds = tally["hours"].pop(key, {})
        for old in [old for old in tally["hours"] if old < key]:
            del tally["hours"][old]
        row = self.analysis_db["hours"].get(key)
        if not isinstance(row, dict):
            return
        solar = self.config["solar"]
        level_min = max(float(solar["powerwall_battery_level_min"]), self.number("usage_reserve"))
        entry = judge_hour(
            seconds, row, self._plan_check_level(start), level_min, float(solar["powerwall_battery_level_max"]),
            self._plan_check_ambiguous(start), level_margin(self.battery_size_used()[0]),
        )
        table = self.analysis_db["plan_check"]
        if entry is None:
            table.pop(key, None)  # an hour that comes twice must not keep the verdict of the first
        else:
            table[key] = entry
        oldest = _first_day(start.date())
        for old in [old for old in table if not isinstance(old, str) or old[:10] < oldest]:
            del table[old]
        self.mark_dirty("analysis")

    def plan_check_summary(self) -> dict:
        return summarize_plan_check(self.analysis_db.get("plan_check") or {}, self.now().date())
