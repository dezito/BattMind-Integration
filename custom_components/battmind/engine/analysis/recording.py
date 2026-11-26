"""Recording of the hours, days, prices and plans."""
from __future__ import annotations

import datetime
import logging

from ...const import HISTORY_BUFFER_HOURS
from ...utils.capacity import add_capacity_measurements, capacity_summary, clean_capacity
from ...utils.efficiency import EFFICIENCY_BACKFILL_VERSION, add_efficiency_measurement, measure_efficiency, measured_efficiency_steps
from ...utils.taper import add_taper_measurement
from .plan_check import hour_key
from ...utils.helpers import days_between, reset_time_to_hour
from ...utils.power import unique_entity_ids, unit_factor_to_w
from .helpers import ANALYSIS_DAYS, FLOWS_VERSION, FORECAST_TYPES, PRICE_FORECAST_DAYS, PRICE_HOURS_DAYS, SCORE_GAP_MIN_POINTS, SCORE_VERSION, _is_num, _num, _r, day_score, evaluate_price_day, hourly_prices, rules_of_entries, summarize_day

_LOGGER = logging.getLogger(__name__)


class AnalysisRecording:
    """Recording of the hours, days, prices and plans."""

    def analysis_hour_end(self, timestamp: datetime.datetime) -> None:
        """hour_end: record the hour, tomorrow's plan (at 23) and publish. Never fails the hour."""
        try:
            self._analysis_record_hour(timestamp.replace(minute=0, second=0, microsecond=0))
            try:
                self._plan_check_record_hour(timestamp.replace(minute=0, second=0, microsecond=0))
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Plan check of the hour %s failed", timestamp)  # never fails the rest of the analysis
            self._analysis_record_prices()
            if timestamp.hour == 23:
                self._analysis_record_plan(timestamp.date() + datetime.timedelta(days=1))
            self._analysis_record_efficiency()
            self._analysis_record_taper(timestamp.replace(minute=0, second=0, microsecond=0))
            self._analysis_record_capacity(timestamp.replace(minute=0, second=0, microsecond=0))
            self._notice_hour_end(timestamp)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Analysis of the hour %s failed", timestamp)
        self.publish_analysis()

    def _analysis_row(self, values: dict) -> dict:
        """kWh of one hour from power_values() (W averaged over the hour)."""
        def kwh(key: str) -> float:
            return round(_num(values.get(key)) / 1000, 3)

        return {
            "home": kwh("power_consumption"),
            "ignored": kwh("ignored_consumption"),
            "use": kwh("power_consumption_without_all_exclusion"),
            "solar": kwh("solar_production"),
            "charge": kwh("powerwall_charging_consumption"),
            "discharge": kwh("powerwall_discharging_consumption"),
        }

    def _analysis_record_hour(self, start: datetime.datetime) -> None:
        end = start.replace(minute=59, second=59)
        row = self._analysis_row(self.power_values(start, end))
        row["flows"] = FLOWS_VERSION
        # the level of a real sample only (never the 100 % fail-safe), the hour's mean of the real prices of its slots only
        level = self.get_battery_level_or_none(stale_ok=False)  # the live state: a level held from before a lost sensor is not measured
        row["soc"] = None if level is None else round(level, 1)
        row["sv"] = SCORE_VERSION  # the hour is recorded like the score needs it (real hourly prices, a measured level, the gap flag)
        row["buy"] = _r(self.real_hour_price(start))
        row["sell"] = _r(self.real_hour_price(start, sell_prices=True))
        if own := self._analysis_own_kwh(start, end):
            row["own"] = own
        if self._analysis_gap(start, end):
            row["gap"] = True  # the hour is a measurement of 0 W where a sensor was lost: a day with it is not scored
        today = self._plan_today()
        predictions = today.get("hour_cost_prediction", {}) if start.date() == self.now().date() else {}
        row["pred"] = {
            kind: round(predictions[kind][start.hour]["kwh"], 3)
            for kind in FORECAST_TYPES
            if isinstance(predictions.get(kind), dict) and isinstance(predictions[kind].get(start.hour), dict)
        }
        hours = self.analysis_db["hours"]
        hours[start.strftime("%Y-%m-%dT%H")] = row
        # a day whose last hour was missed (restart) is finished by the next day, a score of an
        # older hindsight model is calculated again while the hours are kept
        for date in sorted({key[:10] for key in hours}):
            known = self.analysis_db["days"].get(date, {})
            outdated = isinstance(known.get("score"), dict) and known["score"].get("v") != SCORE_VERSION
            if date < start.date().isoformat() and (not known.get("final") or outdated):
                self._analysis_day(datetime.date.fromisoformat(date), final=True)
        self._analysis_day(start.date(), final=start.hour == 23)
        oldest = (start.date() - datetime.timedelta(days=1)).isoformat()
        for key in [key for key in hours if key[:10] < oldest]:
            del hours[key]
        self.mark_dirty("analysis")

    def _analysis_day(self, date: datetime.date, final: bool) -> None:
        key = date.isoformat()
        keys = sorted(hour for hour in self.analysis_db["hours"] if hour[:10] == key)
        rows = [self.analysis_db["hours"][hour] for hour in keys]
        if not rows:
            return
        solar = self.config["solar"]
        previous = self.analysis_db["days"].get((date - datetime.timedelta(days=1)).isoformat(), {})
        day = summarize_day(rows, previous.get("soc_end"), solar["powerwall_battery_level_min"])
        # the level at the end of the day is the level of its last hour: after a missed last hour the next day does not start from it
        day["soc_exact"] = bool(day["soc_exact"]) and keys[-1].endswith("T23")
        if all(row.get("flows") == FLOWS_VERSION for row in rows):
            day["flows"] = FLOWS_VERSION  # an hour recorded the old way is left for the backfill to check
        if final:
            day["final"] = True
            score, reason = self._analysis_score(date, {hour for hour in self.analysis_db["hours"] if hour[:10] == key}, rows, day, previous)
            if score is not None:
                day["score"] = score
            else:
                day["no_score"] = reason  # why, published as the days without a score
        self.analysis_db["days"][key] = day

    def _analysis_own_kwh(self, start: datetime.datetime, end: datetime.datetime) -> float:
        """kWh a charger on its own meter (a CJP car with charger_in_house_meter off) took in the hour: it is not in the house
        consumption, but the battery or the sun may have fed it, which the score leaves out like an ignored consumer."""
        home = self.config["home"]
        ignored = set(unique_entity_ids(home["entity_ids"]["ignore_consumption_from_entity_ids"]))
        own = [entity_id for entity_id in unique_entity_ids(home.get("own_meter_charger_entity_ids") or []) if entity_id not in ignored]
        return round(sum(abs(self._ignored_watt(entity_id, start, end)) for entity_id in own) / 1000, 3)

    def _analysis_gap(self, start: datetime.datetime, end: datetime.datetime) -> bool:
        """The house consumption or the battery power gave no number for too much of the hour (a lost sensor is 0 W in the energy)."""
        if self._wc is None:
            return False
        ids = self.config["home"]["entity_ids"]
        start_ts = start.replace(tzinfo=self._wc.tz).timestamp()
        for key in ("power_consumption_entity_id", "powerwall_watt_flow_entity_id"):
            entity_id = ids.get(key)
            if not entity_id:
                continue
            rows = self._wc.buffers.get(entity_id)
            # a buffer that starts inside the hour holds its first state backwards in time: the hour before it is not measured
            if not rows or rows[0][0] > start_ts or len(self._wc.short_values(entity_id, start, end)) < SCORE_GAP_MIN_POINTS:
                return True
        return False

    def _analysis_score(self, date: datetime.date, present: set[str], rows: list[dict], day: dict, previous: dict) -> tuple[dict | None, str | None]:
        """The day's grid cost: actual, without the battery and with the best possible battery use (helpers.day_score), or why
        there is no score. A day is only scored when every hour of the local day was recorded from sensors that gave
        numbers, with real prices, and the battery level at both ends is a real measurement."""
        tz = self._wc.tz if self._wc is not None else None
        key = date.isoformat()
        # an hour recorded before this model (prices of the :00 slot of any source, the fail-safe level, no gap flag) is not trusted
        if any(row.get("sv") != SCORE_VERSION for row in rows):
            return None, "version"
        expected = self._hour_keys(key, tz)
        # the hour that occurs twice when the clocks go back is one key: its energy is lost, the day is not scored
        if not expected <= present or self._local_day_hours(key, tz) != len(expected):
            return None, "hours"
        if any(row.get("gap") for row in rows):
            return None, "gap"
        if not all(_is_num(row.get("buy")) and _is_num(row.get("sell")) for row in rows):
            return None, "prices"
        if (
            not _is_num(day.get("soc_start")) or not _is_num(day.get("soc_end")) or not _is_num(previous.get("soc_end"))
            or previous.get("soc_exact") is not True or not _is_num(rows[-1].get("soc"))
        ):
            return None, "soc"
        solar = self.config["solar"]
        # the hours store the raw sell price, a sale is worth sell_value() of it
        hours = [
            {
                "use": max(_num(row["home"]) - _num(row.get("ignored")), 0.0),
                "solar": _num(row["solar"]),
                "ignored": _num(row.get("ignored")) + _num(row.get("own")),
                "charge": _num(row["charge"]),
                "discharge": _num(row["discharge"]),
                "buy": row["buy"],
                "sell": self.sell_value(row["sell"]),
            }
            for row in rows
        ]
        levels = [self.percentage_to_kwh(level) for level in (day["soc_start"], day["soc_end"], *(row["soc"] for row in rows if _is_num(row.get("soc"))))]
        return day_score(
            hours,
            self.percentage_to_kwh(day["soc_start"]),
            self.percentage_to_kwh(day["soc_end"]),
            self.percentage_to_kwh(solar["powerwall_battery_level_min"]),
            self.percentage_to_kwh(solar["powerwall_battery_level_max"]),
            levels,
            self.max_kwh_charging,
            self.max_kwh_discharging,
            # the optimiser's efficiency at the hour's power and its profit requirements
            self.charging_efficiency,
            self.discharging_efficiency,
            abs(solar["powerwall_wear_cost_per_kwh"]),
            min_profit_per_kwh=self.get_min_profit_per_kwh(),
            min_total_profit=self.get_min_total_profit(),
            min_sell_kwh=self.number("min_sell_kwh"),
        )

    def _efficiency_measure(self, flow: list, levels: list, start: float, end: float) -> float:
        """Add the measurements of the battery power and level rows between start and end; returns the time measured until."""
        database = self.analysis_db.setdefault("efficiency", {})
        measurements, until = measure_efficiency(
            flow,
            levels,
            start,
            end,
            unit_factor_to_w(self._wc.units.get(self.config["home"]["entity_ids"]["powerwall_watt_flow_entity_id"])),
            self.config["home"]["invert_powerwall_watt_flow_entity_id"],
            self.config["solar"]["powerwall_battery_size"],
        )
        for direction, ac_kwh, battery_kwh, seconds in measurements:
            add_efficiency_measurement(database.setdefault(direction, {}), ac_kwh, battery_kwh, seconds)
        self.mark_dirty("analysis")
        return until

    def _analysis_record_efficiency(self) -> None:
        """Charging and discharging efficiency per AC power step, measured since the last time (utils/efficiency)."""
        ids = self.config["home"]["entity_ids"]
        flow_id, level_id = ids["powerwall_watt_flow_entity_id"], ids["powerwall_battery_level_entity_id"]
        if self._wc is None or not flow_id or not level_id:
            return
        database = self.analysis_db.setdefault("efficiency", {})
        end = self.now().replace(tzinfo=self._wc.tz).timestamp()
        # the buffers hold the states of the last HISTORY_BUFFER_HOURS
        start = max(_num(database.get("until")), end - (HISTORY_BUFFER_HOURS - 2) * 3600)
        # the recorder history before this is read by analysis_efficiency_backfill()
        database.setdefault("from", start)
        database["until"] = self._efficiency_measure(self._wc.buffers.get(flow_id) or [], self._wc.buffers.get(level_id) or [], start, end)

    def efficiency_backfilled(self) -> bool:
        return (self.analysis_db.get("efficiency") or {}).get("backfilled") == EFFICIENCY_BACKFILL_VERSION

    def _taper_hours(self) -> set[str]:
        """The hours (plan check keys) that proved a grid charge: grid charging held at least 3/4 of the hour and the battery took more
        than the sun could have given (plan_check "ok"). There the battery's own limit, not the source, sets the power. A solar charge
        is as slow as the sun, a partial or held charge as slow as it was asked to be, and none of them is accepted."""
        table = self.analysis_db.get("plan_check") or {}
        return {key for key, entry in table.items() if isinstance(entry, dict) and entry.get("a") == "grid_charging" and entry.get("ok") is True}

    def _taper_measure(self, flow: list, levels: list, start: float, end: float) -> float:
        """Add the charging power measured between two battery levels per band (utils/taper.py); returns the time measured until.

        Only measurements that end in an hour where the plan asked for grid charging count (see _taper_hours)."""
        database = self.analysis_db.setdefault("taper", {})
        details: list = []
        _, until = measure_efficiency(
            flow,
            levels,
            start,
            end,
            unit_factor_to_w(self._wc.units.get(self.config["home"]["entity_ids"]["powerwall_watt_flow_entity_id"])),
            self.config["home"]["invert_powerwall_watt_flow_entity_id"],
            self.config["solar"]["powerwall_battery_size"],
            details,
        )
        accepted = self._taper_hours()

        def local(timestamp: float) -> datetime.datetime:
            return datetime.datetime.fromtimestamp(timestamp, self._wc.tz).replace(tzinfo=None)

        for direction, start_level, end_level, ac_kwh, seconds, ended in details:
            # the whole measurement is inside accepted hours: a one that began in a solar hour is not the battery's pace
            if direction == "charging" and hour_key(local(ended)) in accepted and hour_key(local(ended - seconds)) in accepted:
                # the local day (not the UTC day): a night charge across 00:00 UTC is one night
                add_taper_measurement(database, start_level, end_level, ac_kwh, seconds, local(ended).date().toordinal())
        self.mark_dirty("analysis")
        return until

    def _analysis_record_taper(self, hour_start: datetime.datetime) -> None:
        """The charging power per battery level band of the hour that ended, measured since the last time (never fails the hour)."""
        try:
            ids = self.config["home"]["entity_ids"]
            flow_id, level_id = ids["powerwall_watt_flow_entity_id"], ids["powerwall_battery_level_entity_id"]
            if self._wc is None or not flow_id or not level_id:
                return
            database = self.analysis_db.setdefault("taper", {})
            end = (hour_start + datetime.timedelta(hours=1)).replace(tzinfo=self._wc.tz).timestamp()
            start = max(_num(database.get("until")), end - (HISTORY_BUFFER_HOURS - 2) * 3600)
            database["until"] = self._taper_measure(self._wc.buffers.get(flow_id) or [], self._wc.buffers.get(level_id) or [], start, end)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not measure the charging power taper")

    def _analysis_record_capacity(self, hour_start: datetime.datetime) -> None:
        """The battery capacity bounds of the hour that ended (utils/capacity.py), measured since the last time (never fails the hour).

        Always measured, whatever the switch learn_battery_capacity says (it only decides whether the plan counts on the result,
        engine/battery.py battery_size_used()). The configured capacity is only given to measure_efficiency as the plausibility check
        of a measurement, never as part of the measured value, so the plan's size never feeds back into the measuring. It measures
        what the buffers hold (HISTORY_BUFFER_HOURS, filled from the recorder when the entry starts), at most the last
        HISTORY_BUFFER_HOURS - 2 hours the first time; the long recorder history of the efficiency backfill is not read for it.

        The power and level sensors, the sign and the configured battery size are kept with the measurements: when one of them
        changes, what was measured is dropped (it measured another battery or another sign)."""
        try:
            ids = self.config["home"]["entity_ids"]
            flow_id, level_id = ids["powerwall_watt_flow_entity_id"], ids["powerwall_battery_level_entity_id"]
            if self._wc is None or not flow_id or not level_id:
                return
            positive_is_charging = bool(self.config["home"]["invert_powerwall_watt_flow_entity_id"])
            identity = [flow_id, level_id, positive_is_charging, round(float(self.config["solar"]["powerwall_battery_size"]), 3)]
            database = self.analysis_db["capacity"] = clean_capacity(self.analysis_db.get("capacity"))
            if database.get("sensors") != identity:
                # another sensor, sign or battery size (a module added): what was measured is not this battery's any more
                database = self.analysis_db["capacity"] = {**clean_capacity(None), "sensors": identity}
            end = (hour_start + datetime.timedelta(hours=1)).replace(tzinfo=self._wc.tz).timestamp()
            # (a time measured until in the future, from a clock that jumped or a restored backup, must not stop the measuring)
            start = max(min(_num(database.get("until")), end), end - (HISTORY_BUFFER_HOURS - 2) * 3600)
            details: list = []
            _, until = measure_efficiency(
                self._wc.buffers.get(flow_id) or [],
                self._wc.buffers.get(level_id) or [],
                start,
                end,
                unit_factor_to_w(self._wc.units.get(flow_id)),
                positive_is_charging,
                self.config["solar"]["powerwall_battery_size"],
                details,
            )
            add_capacity_measurements(database, details, end)
            database["until"] = until
            self._job_cache.pop("battery_size", None)  # (read earlier in the hour end: the new runs can change it)
            self.mark_dirty("analysis")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not measure the battery capacity")

    def analysis_efficiency_backfill(self, data: dict) -> None:
        """The first time: the measurements of the recorder history {"flow": rows, "level": rows} (coordinator).

        Only before the time the hourly measuring started, so nothing is counted twice.
        """
        database = self.analysis_db.setdefault("efficiency", {})
        flow, levels = data.get("flow") or [], data.get("level") or []
        if flow and levels and self._wc is not None:
            start = min(flow[0][0], levels[0][0])
            if "from" in database:
                self._efficiency_measure(flow, levels, start, _num(database["from"]))
            else:
                # measured without knowing where it started: measured again from the history
                database.pop("charging", None)
                database.pop("discharging", None)
                database["until"] = self._efficiency_measure(flow, levels, start, self.now().replace(tzinfo=self._wc.tz).timestamp())
                database["from"] = start
        _LOGGER.info("Efficiency: %s battery power and %s battery level states read from the recorder", len(flow), len(levels))
        database["backfilled"] = EFFICIENCY_BACKFILL_VERSION
        self.mark_dirty("analysis")
        self.publish_analysis()

    def _analysis_record_prices(self) -> None:
        """The buy price forecast per days ahead, the real prices and the days they are all known for."""
        provider = self.price_provider
        if provider is None:
            return
        current_hour = reset_time_to_hour(self.now())
        hours = self.analysis_db["price_hours"]
        # the forecasts as published, without the penalty per day ahead the providers add
        for hour, price in hourly_prices(provider.get_buy_forecast_prices(adder=False)).items():
            lead = days_between(current_hour, hour)
            if 1 <= lead <= PRICE_FORECAST_DAYS:
                hours.setdefault(hour.strftime("%Y-%m-%dT%H"), {})[str(lead)] = round(price, 3)
        for hour, price in hourly_prices(provider.get_buy_real_prices()).items():
            hours.setdefault(hour.strftime("%Y-%m-%dT%H"), {})["real"] = round(price, 3)

        price_days = self.analysis_db["price_days"]
        by_date: dict[str, list[dict]] = {}
        for key in sorted(hours):
            by_date.setdefault(key[:10], []).append(hours[key])
        for date, rows in by_date.items():
            # a whole day (23 hours on the day summer time starts)
            if date not in price_days and sum(1 for row in rows if _is_num(row.get("real"))) >= 23:
                price_days[date] = evaluate_price_day(rows)
        oldest = (current_hour.date() - datetime.timedelta(days=PRICE_HOURS_DAYS)).isoformat()
        for key in [key for key in hours if key[:10] < oldest]:
            del hours[key]
        self.mark_dirty("analysis")

    def _plan_figures(self, date: datetime.date) -> tuple[dict, float, float, list[datetime.datetime]]:
        """The plan of a date: its plan day ({} when the plan has none), the grid charging (kWh), the sale (kWh) and the times of the
        charges. One calculation for the analysis (plans) and the daily push notification, so the two always show the same figures."""
        plan_day = next(
            (day for day in self.plan.values()
             if isinstance(day, dict) and isinstance(day.get("start_of_day"), datetime.datetime) and day["start_of_day"].date() == date),
            {},
        )
        charges = [
            (timestamp, _num(value.get("kWh"))) for timestamp, value in self.charge_hours.items()
            if isinstance(timestamp, datetime.datetime) and timestamp.date() == date and isinstance(value, dict)
        ]
        sale = sum(_num(value.get("kwh")) for value in plan_day.get("force_discharge_timestamps", {}).values() if isinstance(value, dict))
        return plan_day, sum(kwh for _, kwh in charges), sale, sorted(timestamp for timestamp, kwh in charges if kwh > 0.0)

    def _analysis_record_plan(self, date: datetime.date) -> None:
        """The evening before: grid charging, sale and solar surplus planned for date."""
        _, grid, sale, _ = self._plan_figures(date)
        entry = {"grid": round(grid, 2), "sale": round(sale, 2)}
        prediction = self.solar_prediction_db.get(1) or {}
        if self.is_solar_configured() and prediction.get("total"):
            entry["solar"] = round(sum(prediction["total"]), 2)
            entry["solar_database"] = round(sum(prediction["total_database"]), 2) if prediction.get("total_database") else None
            entry["solar_forecast"] = round(sum(prediction["total_forecast"]), 2) if prediction.get("total_forecast") else None
        self.analysis_db["plans"][date.isoformat()] = entry
        self.mark_dirty("analysis")

    def _notice_hour_end(self, timestamp: datetime.datetime) -> None:
        """What the push notifications read at the hour end: what was learned, and the daily summary. Never fails the hour."""
        try:
            self.notice_ready("learning_consumption", not self.is_learning())
            database = self.analysis_db.get("efficiency") or {}
            self.notice_ready(
                "learning_efficiency",
                all(any(row["used"] for row in measured_efficiency_steps(database.get(direction), direction == "charging")) for direction in ("charging", "discharging")),
            )
            summary = capacity_summary(self.analysis_db.get("capacity"), self.config["solar"]["powerwall_battery_size"])
            # (inconsistent is under suspicion, not gone: neither ready nor reset)
            self.notice_ready(
                "learning_capacity",
                None if summary["status"] == "inconsistent" else summary["charging"]["kwh"] is not None and summary["discharging"]["kwh"] is not None,
            )
            self._notice_summary(timestamp)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not prepare the push notifications of the hour %s", timestamp)

    def _notice_summary(self, timestamp: datetime.datetime) -> None:
        """The daily summary: from the configured hour, once tomorrow's prices are all real (the plan is not a guess at the prices),
        the plan of tomorrow as it is now (what is planned, no savings: a plan changes with the prices and the forecast). The
        notifier sends it once per date."""
        if not (self.config.get("notify_summary") and self.config.get("notify_list")):
            return
        if timestamp.hour < int(self.config.get("notify_summary_hour", 20)) or self._out is None:
            return
        provider = self.price_provider
        tomorrow = timestamp.date() + datetime.timedelta(days=1)
        tz = self._snap.tz if self._snap is not None else None
        # every hour of tomorrow has a real price: the prices are per naive local hour, so the repeated hour of the day the clocks go
        # back is one of them (24 on that day, 23 on the day they go forward)
        if provider is None or tz is None:
            return
        known = {hour for hour in hourly_prices(provider.get_buy_real_prices()) if hour.date() == tomorrow}
        if len(known) < len(self._hour_keys(tomorrow.isoformat(), tz)):
            return
        plan_day, grid, sale, times = self._plan_figures(tomorrow)
        if not plan_day:
            return
        ranges: list[str] = []
        hours = sorted({time.replace(minute=0, second=0, microsecond=0) for time in times})
        for hour in hours:
            if ranges and hour == ranges[-1][1] + datetime.timedelta(hours=1):
                ranges[-1][1] = hour
            else:
                ranges.append([hour, hour])
        level = self.get_battery_level_or_none(stale_ok=False)
        self._out.summary = {
            "date": tomorrow.isoformat(),
            "day": tomorrow.strftime("%d/%m"),
            "grid": f"{grid:.1f}",
            "hours": ", ".join(f"{start:%H:%M}–{(end + datetime.timedelta(hours=1)):%H:%M}" for start, end in ranges) or "–",
            "sale": f"{sale:.1f}",
            "level": "–" if level is None else f"{level:.0f}",
        }

    def _analysis_rule_days(self) -> None:
        """Charged/discharged per rule and day from the hourly charging history entries.

        A day with more than one entry still has its hours; a day with one entry may be
        combined already and keeps what was saved before (combined days are only used the
        first time, with the rules of the whole day).
        """
        by_date: dict[str, list[dict]] = {}
        for timestamp, entry in self.charging_history_db.items():
            if isinstance(timestamp, datetime.datetime) and isinstance(entry, dict):
                by_date.setdefault(timestamp.date().isoformat(), []).append(entry)
        rule_days = self.analysis_db["rule_days"]
        changed = False
        for date, entries in by_date.items():
            if len(entries) == 1 and date in rule_days:
                continue
            rules = rules_of_entries(entries)
            if rule_days.get(date) != rules:
                rule_days[date] = rules
                changed = True
        if changed:
            self.mark_dirty("analysis")

    def _analysis_prune(self) -> None:
        oldest = (self.now().date() - datetime.timedelta(days=ANALYSIS_DAYS)).isoformat()
        for name in ("days", "plans", "rule_days", "price_days"):
            table = self.analysis_db[name]
            for key in [key for key in table if key < oldest]:
                del table[key]
