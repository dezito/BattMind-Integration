"""Analysis (engine part): what the home battery earned and how well BattMind planned.

Recorded every hour (hour_end) in the analysis database, from the same power values as the
charging history:

- hours: the energy of the last 2 days (house, solar, battery), the battery level, the
  buy/sell price and the consumption the plan predicted for the hour per forecast type.
- days: the hours added up per day: where the house got its energy from (self-sufficiency),
  what went in and out of the battery (measured round trip loss), the error of each
  consumption forecast type, the most expensive hours with an empty battery and, when the
  day is over, the hindsight score: the saving reached compared with the best possible
  use of the battery with that day's consumption, solar and prices.
- plans: every evening the grid charging, sale and solar surplus planned for tomorrow.
- rule_days: charged/discharged kWh, cost and savings per charging rule, from the hourly
  entries of the charging history (the history combines old hours per day and loses the
  rules of each hour).
- price_hours: the buy price forecast of the price integration per hour and days ahead
  (without BattMind's daily adder), and the real price once it is known; kept a week.
- price_days: when all real prices of a day are known, the forecast error per days ahead and
  how well the forecast found the day's cheapest and most expensive hours.
- plan_check: per hour whether the battery did what the published action asked (engine/analysis/plan_check.py);
  only observed, kept 14 days, published as the attribute plan_check of sensor.<prefix>_powerwall_action.
- efficiency: AC and battery kWh per AC power step for charging and discharging, measured between
  changes of the battery level (utils/efficiency); the measured efficiency curves. The first time
  the recorder history of the battery power and level is measured too (coordinator).
- full_charge: {"last": aware datetime} when the battery last reached its max. level (engine/full_charge.py).

The first time the energy per hour of the last year is read from the recorder's long-term
statistics (coordinator); forecasts, plans and scores only exist from now on. Everything is
published per month and day on sensor.<prefix>_analysis.
"""
from __future__ import annotations

import datetime
import logging

from ..energy.energy import derive_power_values
from ...utils.power import unique_entity_ids, unit_factor_to_w
from .plan_check import PlanCheck
from .publishing import AnalysisPublishing
from .recording import AnalysisRecording
from .helpers import ANALYSIS_DAYS, ANALYSIS_BACKFILL_VERSION, DAY_SUMS, FLOWS_SCORE_TOLERANCE_KWH, FLOWS_VERSION, FORECAST_AUTO_DAYS, FORECAST_AUTO_MIN_DAYS, PRICE_FORECAST_DAYS, PRICE_CORRECTION_DAYS, PRICE_CORRECTION_MIN_DAYS, PRICE_CORRECTION_MAX, _num, _is_num, _r, auto_forecast_choice, forecast_errors, summarize_day

_LOGGER = logging.getLogger(__name__)


class Analysis(AnalysisRecording, AnalysisPublishing, PlanCheck):
    """The analysis database and sensor.<prefix>_analysis."""

    def load_analysis_db(self, database) -> None:
        db = database if isinstance(database, dict) else {}
        for key in ("hours", "days", "plans", "rule_days", "price_hours", "price_days", "plan_check"):
            if not isinstance(db.get(key), dict):
                db[key] = {}
        # the last full charge (engine/full_charge.py): {"last": aware datetime}; an old backup has none, a malformed one
        # is dropped (unknown, never "just charged")
        full_charge = db.get("full_charge")
        if full_charge is not None:
            last = full_charge.get("last") if isinstance(full_charge, dict) else None
            if not isinstance(last, datetime.datetime) or last.tzinfo is None:
                db.pop("full_charge")
        self.analysis_db = db

    def price_forecast_corrections(self) -> dict[int, float]:
        """Currency/kWh added to the price forecast per days ahead: minus its bias the last 30 days.

        Only for days ahead with PRICE_CORRECTION_MIN_DAYS judged days, at most
        PRICE_CORRECTION_MAX either way, and only when the option is on.
        """
        if not self.enabled("price_forecast_correction"):
            return {}
        first = (self.now().date() - datetime.timedelta(days=PRICE_CORRECTION_DAYS)).isoformat()
        selected = [day for date, day in self.analysis_db.get("price_days", {}).items() if date >= first and isinstance(day, dict)]
        corrections = {}
        for lead in range(1, PRICE_FORECAST_DAYS + 1):
            entries = [day[str(lead)] for day in selected if isinstance(day.get(str(lead)), dict)]
            hours = sum(_num(entry.get("n")) for entry in entries)
            if len(entries) >= PRICE_CORRECTION_MIN_DAYS and hours:
                bias = sum(_num(entry.get("bias")) for entry in entries) / hours
                corrections[lead] = round(min(max(-bias, -PRICE_CORRECTION_MAX), PRICE_CORRECTION_MAX), 3)
        return corrections

    def auto_forecast_type(self, manual: str) -> str | None:
        """The consumption forecast type that hit best the last FORECAST_AUTO_DAYS days (switch auto_forecast_type).

        None while fewer than FORECAST_AUTO_MIN_DAYS closed days have a recorded forecast. The
        choice starts at the selected (manual) type, moves only when another type's error is
        FORECAST_AUTO_MARGIN smaller and is kept in analysis_db["forecast_auto"].
        """
        today = self.now().date()
        first = (today - datetime.timedelta(days=FORECAST_AUTO_DAYS)).isoformat()
        days = [day for date, day in self.analysis_db["days"].items() if first <= date < today.isoformat() and isinstance(day, dict)]
        known = self.analysis_db.get("forecast_auto")
        choice = auto_forecast_choice(forecast_errors(days), known or manual, FORECAST_AUTO_MIN_DAYS, "days")
        if choice is not None and choice != known:
            self.analysis_db["forecast_auto"] = choice
            self.mark_dirty("analysis")
        return choice

    def forecast_state(self) -> tuple[bool, str]:
        """The switch auto_forecast_type and the consumption forecast type in use."""
        return bool(self.enabled("auto_forecast_type")), self.forecast_type

    def republish_forecast_if_changed(self) -> None:
        """The analysis shows the type in use: published again when the switch or the automatic choice changed."""
        if self.published_forecast_state is not None and self.published_forecast_state != self.forecast_state():
            self.publish_analysis()

    def analysis_backfilled(self) -> bool:
        return self.analysis_db.get("backfilled") == ANALYSIS_BACKFILL_VERSION

    # hourly ------------------------------------------------------------ #

    # backfill ---------------------------------------------------------- #

    def analysis_update(self, data: dict) -> None:
        """The first time: the energy per hour from the long-term statistics {entity: [(ts, mean)]}."""
        stats = data.get("stats") or {}
        home = self.config["home"]
        ids = home["entity_ids"]
        units = self._snap.units if self._snap else {}
        tz = self._snap.tz if self._snap else None

        def series(entity_id: str, power: bool = True) -> dict[float, float]:
            factor = unit_factor_to_w(units.get(entity_id)) if power else 1.0
            return {float(ts): float(value) * factor for ts, value in stats.get(entity_id) or [] if _is_num(value)}

        consumption = series(ids["power_consumption_entity_id"])
        flow = series(ids["powerwall_watt_flow_entity_id"])
        solar = series(self.config["solar"]["entity_ids"]["production_entity_id"])
        ignored = [series(entity_id) for entity_id in unique_entity_ids(ids["ignore_consumption_from_entity_ids"])]
        soc = series(ids["powerwall_battery_level_entity_id"], power=False)
        # the buy price history: per hour the first price provider with a price (PRICE_PROVIDER_PRIORITY)
        provider = self.price_provider
        price = provider.combine_history(
            {entity_id: series(entity_id, power=False) for entity_id in provider.buy_price_entity_ids()}
        ) if provider else {}
        positive_is_charging = home["invert_powerwall_watt_flow_entity_id"]

        by_date: dict[str, dict[str, dict]] = {}
        # the hours each sensor has statistics for: a missing hour is 0 W below, which is no measurement of the hour
        flow_hours: set[str] = set()
        solar_hours: set[str] = set()  # (also without a solar sensor: it is 0 in the recording as well)
        ignored_hours: set[str] = set()  # every ignored consumer
        solar_id = self.config["solar"]["entity_ids"]["production_entity_id"]
        for ts in sorted(consumption):
            hour = datetime.datetime.fromtimestamp(ts, tz).replace(tzinfo=None)
            hour_key = hour.strftime("%Y-%m-%dT%H")
            if ts in flow:
                flow_hours.add(hour_key)
            if not solar_id or ts in solar:
                solar_hours.add(hour_key)
            if all(ts in series_of_one for series_of_one in ignored):
                ignored_hours.add(hour_key)
            battery = flow.get(ts, 0.0)
            charging = max(battery if positive_is_charging else -battery, 0.0)
            discharging = max(-battery if positive_is_charging else battery, 0.0)
            values = derive_power_values(
                home, abs(consumption[ts]), sum(abs(s.get(ts, 0.0)) for s in ignored), charging, discharging, abs(solar.get(ts, 0.0)),
            )
            row = self._analysis_row(values)
            row["soc"] = _r(soc.get(ts), 1)
            row["buy"] = _r(price.get(ts))
            by_date.setdefault(hour.date().isoformat(), {})[hour.strftime("%Y-%m-%dT%H")] = row

        today = self.now().date()
        recent = (today - datetime.timedelta(days=1)).isoformat()
        oldest = (today - datetime.timedelta(days=ANALYSIS_DAYS)).isoformat()
        days = self.analysis_db["days"]
        minimum = self.config["solar"]["powerwall_battery_level_min"]
        previous_soc = None
        corrected = unchanged = 0
        for date in sorted(by_date):
            rows = by_date[date]
            if date < oldest:
                continue
            if date >= recent:
                for key, row in rows.items():
                    known = self.analysis_db["hours"].setdefault(key, row)
                    if known is row:
                        row["flows"] = FLOWS_VERSION  # read from the statistics: the time weighted mean
                        continue
                    if known.get("buy") is None and row.get("buy") is not None:
                        known["buy"] = row["buy"]
                    if key in flow_hours and known.get("flows") != FLOWS_VERSION:
                        # an hour recorded before the battery power was measured with held states: the energy of the
                        # hour again (home and use were calculated from the charging and discharging); the energy of a
                        # sensor without statistics stays as recorded
                        fields = ["home", "charge", "discharge"]
                        fields += ["solar"] if key in solar_hours else []
                        fields += ["ignored", "use"] if key in ignored_hours else []
                        for field in fields:
                            known[field] = row[field]
                        if key not in ignored_hours:
                            known["use"] = max(_num(known["home"]) - _num(known.get("ignored")), 0.0)  # of the new home
                        known["flows"] = FLOWS_VERSION
                        corrected += 1
            elif date not in days or days[date].get("statistics"):
                days[date] = {**summarize_day([rows[key] for key in sorted(rows)], previous_soc, minimum), "final": True, "statistics": True}
            elif days[date].get("flows") != FLOWS_VERSION:
                if self._analysis_correct_flows(date, days[date], rows, flow_hours & solar_hours & ignored_hours, previous_soc, minimum, tz):
                    corrected += 1
                else:
                    unchanged += 1
            previous_soc = days.get(date, {}).get("soc_end", previous_soc)
        for date in (today - datetime.timedelta(days=1), today):
            self._analysis_day(date, final=date < today)
        self.analysis_db["backfilled"] = ANALYSIS_BACKFILL_VERSION
        self.mark_dirty("analysis")
        if self.analysis_db.get("charging_history_flows") != FLOWS_VERSION:
            try:
                # the hourly charging history of the last two days, measured again from the buffers (once per FLOWS_VERSION)
                self.correct_recent_charging_history()
                self.analysis_db["charging_history_flows"] = FLOWS_VERSION
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Charging history of the last hours could not be measured again")
        _LOGGER.info(
            "Analysis: %s days read from the long-term statistics, %s recorded days/hours checked against them, %s recorded days left as they are",
            len(by_date), corrected, unchanged,
        )
        self.publish_analysis()

    @staticmethod
    def _hour_keys(date: str, tz) -> set[str]:
        """The hour keys of the local day (23 on the day summer time starts; the repeated hour of the 25 hour day is one key)."""
        day = datetime.date.fromisoformat(date)
        start = datetime.datetime.combine(day, datetime.time(0)).replace(tzinfo=tz).timestamp()
        end = datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(0)).replace(tzinfo=tz).timestamp()
        keys = set()
        while start < end:
            keys.add(datetime.datetime.fromtimestamp(start, tz).strftime("%Y-%m-%dT%H"))
            start += 3600
        return keys

    def _analysis_correct_flows(self, date: str, day: dict, rows: dict[str, dict], complete_hours: set[str], previous_soc: float | None, minimum: float, tz) -> bool:
        """A day recorded before FLOWS_VERSION, checked against the hourly mean statistics (the mean of the hour is the time
        weighted mean, whatever the states did in it). When charged or discharged differ by more than
        FLOWS_SCORE_TOLERANCE_KWH, the sums of the energy are replaced and the score of the day removed (its hours are gone
        and the statistics have no sell prices, so it can not be calculated again, and it is not shown wrong); within the
        tolerance the day stays as recorded. The forecast errors, the levels and the hours with an empty battery stay as
        recorded. The mean of an hour is the net power, so charging and discharging in the same hour count as their
        difference. A day without statistics of the consumption, the battery power, the solar production and the ignored
        consumers for every hour of the day (complete_hours: the hours that have them all), and a day that is not final,
        stay as they are (False): a sensor without statistics would count as 0.
        """
        keys = self._hour_keys(date, tz)
        if not day.get("final") or not keys <= rows.keys() or not keys <= complete_hours:
            return False
        fresh = summarize_day([rows[key] for key in sorted(rows)], previous_soc, minimum)
        if any(abs(_num(fresh[key]) - _num(day.get(key))) > FLOWS_SCORE_TOLERANCE_KWH for key in ("charged", "discharged")):
            for key in DAY_SUMS:
                day[key] = fresh[key]
            day.pop("score", None)
        day["flows"] = FLOWS_VERSION
        return True

    # rules ------------------------------------------------------------- #

    # publish ----------------------------------------------------------- #
