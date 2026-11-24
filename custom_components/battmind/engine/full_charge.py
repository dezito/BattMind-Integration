"""Full charge: when the battery was last full, and when the plan has to fill it (number full_charge_recommended).

Some LFP batteries need a full charge now and then so the battery management system can calibrate. The battery counts as
full at powerwall_battery_level_max - FULL_CHARGE_MARGIN; the moment it gets there is stored in
analysis_db["full_charge"]["last"] (timezone aware, also while the number is 0) and published as
sensor.<prefix>_last_full_charge.

Detection reads the real battery level only: every finite row of the battery level buffer since the previous check and
the current state, never the 100 % fail-safe of get_battery_level(). A short peak between two jobs is found in the buffer
rows. There is no hold time: the plan often discharges right after the maximum, so only reaching it counts. Without a
stored time the buffer (HISTORY_BUFFER_HOURS, the battery level has no long-term statistics) is the lookback; nothing
found means due, never "just charged". After a restart the first sample only sets where the battery is when a time is
stored (a battery that sits full keeps its time). A stored time after now is dropped (unknown).

Due: the number > 0, the battery not full now, and never seen full or more than the number of days ago. The deadline is
the end of tomorrow, or the end of today once the full charge is a day overdue (also when the time is unknown, so the
deadline can't slide for ever). The plan only gets the requirement when the battery can charge from the grid, the
deadline hour has a real price of the price integration (no forecast, no price history, not offline) and is inside
days_to_predict.

The what-if replay (planner/replay.py) has the full charge off: it never reads the live time of the last full charge.
"""
from __future__ import annotations

import datetime
import logging
import math

from ..const import FULL_CHARGE_MARGIN
from ..utils.helpers import days_between, float_or_none, reset_time_to_hour, start_of_day

_LOGGER = logging.getLogger(__name__)

FULL_CHARGE_DB_KEY = "full_charge"
# the requirement of a plan that has no full charge (what-if replay, number 0, not due)
FULL_CHARGE_OFF = {"days": 0.0, "last": None, "elapsed_days": None, "due": False, "overdue": False, "deadline": None, "active": False}
# seconds a stored last full charge may be after now (a job's time and the buffer rows) before it counts as wrong
FULL_CHARGE_FUTURE_TOLERANCE = 300.0


def _finite(value) -> float | None:
    number = float_or_none(value)
    return number if number is not None and math.isfinite(number) else None


class FullCharge:
    """The last full charge (sensor) and the full charge requirement of the plan."""

    def full_charge_level(self) -> float:
        """The battery level that counts as full (detection and the plan's "reached full" check)."""
        return self.config["solar"]["powerwall_battery_level_max"] - FULL_CHARGE_MARGIN

    def _full_charge_tz(self):
        return self._snap.tz if self._snap is not None else None

    def _full_charge_now(self) -> datetime.datetime:
        """Now, timezone aware (the snapshot's time is naive local time).

        The coordinator makes it with dt_util.now().replace(tzinfo=None), which keeps fold: in the repeated hour of the
        change to winter time fold=1 gives the second occurrence, so replace(tzinfo=tz) gives the real moment."""
        tz = self._full_charge_tz()
        now = self.now()
        if tz is None:
            return now.astimezone()
        return datetime.datetime.fromtimestamp(now.replace(tzinfo=tz).timestamp(), tz)

    def _stored_full_charge(self) -> datetime.datetime | None:
        entry = self.analysis_db.get(FULL_CHARGE_DB_KEY)
        last = entry.get("last") if isinstance(entry, dict) else None
        return last if isinstance(last, datetime.datetime) and last.tzinfo is not None else None

    def _full_charge_in_future(self, last: datetime.datetime) -> bool:
        # A stored time after now (the clock was wrong, a backup from another system): it is no full charge that happened,
        # so it counts as unknown (never a negative age) and the next full battery replaces it.
        return last.timestamp() > self._full_charge_now().timestamp() + FULL_CHARGE_FUTURE_TOLERANCE

    def last_full_charge(self) -> datetime.datetime | None:
        """When the battery last reached full (aware, in the time zone of the snapshot), None when never seen."""
        last = self._stored_full_charge()
        if last is None or self._full_charge_in_future(last):
            return None
        tz = self._full_charge_tz()
        # the stored time comes back with a fixed offset: in the real time zone the day arithmetic follows its DST
        return last.astimezone(tz) if tz is not None else last

    def full_charge_publish(self) -> None:
        self.set_state("last_full_charge", self.last_full_charge())

    def full_charge_check(self) -> None:
        """full_charge_observe() that never fails the job it runs in."""
        try:
            self.full_charge_observe()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not check whether the battery is full")

    def full_charge_observe(self) -> None:
        """Register the battery reaching full (every job that reads the battery level, and hour_end)."""
        if self._snap is None or self._wc is None:
            return
        entity_id = self.config["home"]["entity_ids"]["powerwall_battery_level_entity_id"]
        if not entity_id:
            return
        stored = self._stored_full_charge()
        if stored is not None and self._full_charge_in_future(stored):
            _LOGGER.warning("The last full charge %s is after now: it is dropped (unknown)", stored.isoformat(timespec="minutes"))
            self.analysis_db.pop(FULL_CHARGE_DB_KEY, None)
            self.mark_dirty("analysis")
            self.full_charge_publish()
        last = self.last_full_charge()
        threshold = self.full_charge_level() - 1e-9
        now_ts = self._full_charge_now().timestamp()
        since = getattr(self, "_full_charge_scanned", None)
        observations = [
            (float(ts), value) for ts, value in self._snap.buffers.get(entity_id) or []
            if (since is None or ts > since) and ts <= now_ts and _finite(value) is not None
        ]
        current = _finite(self.entity_state(entity_id))  # the real state: an unavailable sensor is no full battery
        if current is not None:
            observations.append((now_ts, current))
        reached_at = None
        for ts, value in sorted(observations, key=lambda row: row[0]):
            full = float(value) >= threshold
            previous = getattr(self, "_full_charge_full", None)
            # After a restart the first sample (the state at the start of the buffer) is only where the battery is: with a
            # stored time a battery that sits full would else move it to the start of the buffer at every restart.
            # Without one a full first sample is registered: the battery was full then, and nothing better is known.
            if full and previous is not True and not (previous is None and last is not None):
                reached_at = ts
            self._full_charge_full = full
        self._full_charge_scanned = now_ts
        if reached_at is None:
            return
        tz = self._full_charge_tz()
        at = datetime.datetime.fromtimestamp(reached_at, tz)
        if last is not None and at.timestamp() <= last.timestamp():
            return
        self.analysis_db[FULL_CHARGE_DB_KEY] = {"last": at}
        self.mark_dirty("analysis")
        self.full_charge_publish()
        _LOGGER.info("Battery full (%.1f%% or more) at %s", self.full_charge_level(), at.isoformat(timespec="minutes"))

    def _full_charge_priced(self, deadline: datetime.datetime) -> bool:
        """The deadline hour has a real price of the price integration (not a forecast, the price history or offline)."""
        provider = self.price_provider
        if provider is None or self.grid_price_cache.get("using_offline_prices"):
            return False
        real = getattr(provider, "get_buy_real_prices", None)
        prices = real() if callable(real) else {}
        return any(reset_time_to_hour(timestamp) == deadline for timestamp in prices)

    def full_charge_status(self) -> dict:
        """The full charge of this job: due, the deadline (naive local hour) and whether the plan gets the requirement."""
        if "full_charge" in self._job_cache:
            return self._job_cache["full_charge"]
        days = max(self.number("full_charge_recommended"), 0.0)
        last = self.last_full_charge()
        elapsed = None
        if last is not None:
            elapsed = (self._full_charge_now().timestamp() - last.timestamp()) / 86400.0
        full_now = getattr(self, "_full_charge_full", None) is True
        due = days > 0 and not full_now and (elapsed is None or elapsed > days)
        overdue = due and (elapsed is None or elapsed >= days + 1)
        deadline = active = None
        if due:
            deadline = start_of_day(self.now()) + datetime.timedelta(days=0 if overdue else 1, hours=23)
            in_plan = days_between(reset_time_to_hour(self.now()), deadline) < int(self.config["days_to_predict"])
            # without grid charging the rule counts as off (as the rules that need an action, docs/actions.md): no
            # requirement at all, so the plan (solar blocking too) is the one of the number 0
            supported = "grid_charging" not in self.unsupported_actions()
            active = in_plan and supported and self._full_charge_priced(deadline)
        status = {
            "days": days, "last": last, "elapsed_days": elapsed, "due": due, "overdue": overdue, "deadline": deadline,
            "active": bool(active),
        }
        self._job_cache["full_charge"] = status
        return status

    def full_charge_requirement(self) -> dict | None:
        """{"deadline": naive local hour, "level": max level, "full_level": counts as full} for the plan, None without."""
        status = self.full_charge_status()
        if not status["active"]:
            return None
        return {
            "deadline": status["deadline"],
            "level": self.config["solar"]["powerwall_battery_level_max"],
            "full_level": self.full_charge_level(),
            "elapsed_days": status["elapsed_days"],
        }

    def full_charge_hash(self) -> tuple:
        """What the plan depends on: it is made again when the full charge becomes due or is done."""
        status = self.full_charge_status()
        last = status["last"]
        return (status["due"], status["active"], status["deadline"], last.isoformat() if last is not None else None)
