"""Load, save and recalculate the charging history."""
from __future__ import annotations

import datetime
import logging
from copy import deepcopy

from ...const import CHARGING_HISTORY_CORRECT_HOURS
from ...utils.helpers import months_between, reset_time_to_hour
from ...utils.power import unique_entity_ids

# an hour measured again keeps its entry when charged and discharged are within this (kWh) of the recorded
CHARGING_HISTORY_CORRECT_TOLERANCE_KWH = 0.02

_LOGGER = logging.getLogger(__name__)


class HistoryStorage:
    """Load, save and recalculate the charging history."""

    def load_charging_history(self, database) -> None:
        database = database if isinstance(database, dict) else {}
        self.charging_history_db = {key: deepcopy(value) for key, value in database.items() if isinstance(key, datetime.datetime)}
        self.set_state("charging_history", self.t("ui.load_charging_history.default_state", name=self.name))
        # first pass only measures the size of the totals table at the bottom (see history_footer_bytes)
        self.charging_history_combine_and_set(get_ending_byte_size=True)
        self.charging_history_combine_and_set()

    def save_charging_history(self) -> None:
        if not self.charging_history_db:
            return
        now = self.now()
        for key in list(self.charging_history_db.keys()):
            if months_between(key, now) > self.config["database"]["charging_history_db_data_to_save"] + 1:
                _LOGGER.info("Removing %s from charging_history_db", key)
                del self.charging_history_db[key]
        self.charging_history_db = dict(sorted(self.charging_history_db.items(), key=lambda item: item[0], reverse=True))
        self.mark_dirty("charging_history")

    def _buffered_since(self, start: datetime.datetime) -> bool:
        """Whether the hour of start can be measured from the buffers: every sensor has states from before the start of
        the hour (no states would count as 0 W), and the consumption and the battery power have a number in the hour
        (power_values reads a lost sensor as 0 W; the solar sensor and the ignored consumers are read like that when
        they are lost, also by the recording, so they only need states)."""
        wc = getattr(self, "_wc", None)
        if wc is None:
            return False
        ids = self.config["home"]["entity_ids"]
        measured = [ids["power_consumption_entity_id"], ids["powerwall_watt_flow_entity_id"]]
        others = [self.config["solar"]["entity_ids"]["production_entity_id"], *unique_entity_ids(ids["ignore_consumption_from_entity_ids"])]
        start_ts = start.replace(tzinfo=wc.tz).timestamp()
        end = start.replace(minute=59, second=59, microsecond=0)
        for entity_id in filter(None, [*measured, *others]):
            rows = wc.buffers.get(entity_id)
            if not rows or rows[0][0] > start_ts:
                return False
        return all(wc.short_values(entity_id, start, end) for entity_id in filter(None, measured))
    def correct_recent_charging_history(self, hours: int = CHARGING_HISTORY_CORRECT_HOURS) -> tuple[int, int]:
        """The hourly charging history of the last `hours` finished hours measured again from the buffers: (corrected, failed).

        An hour recorded before the battery power was measured with every state held until the next one can have
        charging that never happened. The recorded rules, prices (buy, sell: the price lists only hold today and later)
        and the price of the battery energy of the hour stay; an hour whose charged and discharged energy is the same
        is left as it is, an hour without any energy now is removed, and an hour that was not recorded, or that has no
        number from every sensor in the buffers (it would count as 0 W), is not touched. Only the hourly entries of the
        last two days: older ones are combined per day (the newest CHARGING_HISTORY_COMBINE_AFTER entries are kept
        hourly, more than the hours here), so they are not recalculated.
        """
        now_hour = reset_time_to_hour(self.now())
        corrected = failed = skipped = 0
        dates: set[str] = set()
        for back in range(hours, 0, -1):
            start = now_hour - datetime.timedelta(hours=back)
            old = self.charging_history_db.get(start)
            if not isinstance(old, dict):
                continue  # an hour that was not recorded is not added
            if not self._buffered_since(start):
                skipped += 1
                continue
            prices = old.get("prices") if isinstance(old.get("prices"), dict) else {}
            try:
                fresh = self.charging_history(
                    timestamp=start, save_db=False, rules=dict(old.get("rules") or {}),
                    battery_kwh_price=prices.get("powerwall_kwh_price"), recorded_prices=prices,
                )
                charged, discharged = fresh["charged"]["kWh"], fresh["discharged"]["kWh"]
                was_charged = float((old.get("charged") or {}).get("kWh") or 0.0)
                was_discharged = float((old.get("discharged") or {}).get("kWh") or 0.0)
            except Exception as err:  # noqa: BLE001
                self.charging_history_db[start] = old
                failed += 1
                _LOGGER.warning("Charging history of %s could not be measured again: %s", start, err)
                continue
            if abs(charged - was_charged) <= CHARGING_HISTORY_CORRECT_TOLERANCE_KWH and abs(discharged - was_discharged) <= CHARGING_HISTORY_CORRECT_TOLERANCE_KWH:
                self.charging_history_db[start] = old  # the same energy: as it was recorded
                continue
            if charged <= 0.0 and discharged <= 0.0:
                self.charging_history_db.pop(start, None)
            corrected += 1
            dates.add(start.date().isoformat())
        if corrected:
            self.mark_dirty("charging_history")
            for date in dates:
                self.analysis_db.get("rule_days", {}).pop(date, None)  # the rules table of the day is made again from the hours
            self._analysis_rule_days()
            self.current_battery_level_expenses()
            self.charging_history_combine_and_set()
            self.save_charging_history()
        _LOGGER.info(
            "Charging history: %s of the last %s hours measured again, %s could not be, %s without numbers from every sensor in the buffers",
            corrected, hours, failed, skipped,
        )
        return corrected, failed

    def recalc_charging_history_today(self) -> int:
        now_hour = self.now().hour
        timestamp = reset_time_to_hour(self.now())
        for ts in list(self.charging_history_db.keys()):
            if isinstance(ts, datetime.datetime) and ts.date() == timestamp.date():
                self.charging_history_db.pop(ts, None)
        failures = 0
        for hour in range(0, now_hour + 1):
            try:
                self.charging_history(timestamp=timestamp.replace(hour=hour), save_db=hour == now_hour)
            except Exception as err:  # noqa: BLE001
                failures += 1
                _LOGGER.error("Charging history recalculation for hour %s failed: %s", hour, err)
        if failures:
            self.notify(
                f"⚠️ Finished {self.title} recalculation of today's charging history with {failures} failures. Check logs for details.",
                title=f"{self.title} recalculation complete with errors",
                notification_id=f"{self.name}_recalc_charging_history_today",
            )
        else:
            self.set_charging_rule("📟Finished recalculation of today's charging history")
            self.notify(
                f"✅ Finished {self.title} recalculation of today's charging history (hourly blocks) without errors.",
                title=f"{self.title} recalculation complete",
                notification_id=f"{self.name}_recalc_charging_history_today",
            )
        return failures
