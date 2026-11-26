"""Storm warning in the engine: reads the warning entity, gives the plan hours the level to keep and publishes the status.

The pure rules are in storm.py. This part holds the engine state (the snapshot's alert, the analysis days for the buffer, the
published sensor) and the rules plan's fix (apply_storm_to_rules): the optimiser plan keeps the level with OptimizerHour.floor
(planner/future/optimizer.py), a rule plan gets it by enforce_storm_floor() and this fix.

Without the option storm_warning_entity_id the snapshot has no alert and nothing here does anything: no floor, no sensor.
"""
from __future__ import annotations

import datetime
import logging
import math

from ..utils.formatting import emoji_parse
from ..utils.helpers import days_between, float_or_none, reset_time_to_hour
from .analysis.helpers import FORECAST_AUTO_DAYS, FORECAST_AUTO_MIN_DAYS, forecast_errors
from .storm import (
    STORM_DEFAULT_HOURS,
    STORM_DEFAULT_LEVEL,
    STORM_RULE,
    STATUS_ACTIVE,
    STATUS_COMING,
    STATUS_EXPIRED,
    STATUS_LATER,
    STATUS_NONE,
    STATUS_OFF,
    STATUS_OTHER,
    STATUS_WEAK,
    StormPlan,
    StormWindow,
    forecast_buffer,
    parse_alert,
    storm_plan,
)

_LOGGER = logging.getLogger(__name__)


class StormWarning:
    """The storm warning of the plan."""

    def storm_window(self) -> StormWindow | None:
        """What the warning entity says now, None without the option."""
        alert = self._snap.storm_alert if self._snap is not None else None
        if not alert:
            return None
        cover = float_or_none(self.setting("storm_cover_hours"))
        level = float_or_none(self.config["home"].get("storm_min_level"))
        return parse_alert(
            alert.get("state"),
            alert.get("attributes"),
            self.now(),
            self._snap.tz,
            int(level) if level is not None else STORM_DEFAULT_LEVEL,
            cover if cover is not None else STORM_DEFAULT_HOURS,
        )

    def storm_buffer(self) -> tuple[float, str]:
        """The part added to the need for a forecast that is off: what the last days measured, at least 20 %."""
        today = self.now().date()
        first = (today - datetime.timedelta(days=FORECAST_AUTO_DAYS)).isoformat()
        days = [day for date, day in self.analysis_db["days"].items() if first <= date < today.isoformat() and isinstance(day, dict)]
        return forecast_buffer(forecast_errors(days), self.forecast_type, FORECAST_AUTO_MIN_DAYS)

    def storm_setup(self, hours: list, gross_kwh: dict, efficiency: float, step: float) -> StormPlan | None:
        """Give the plan hours the level to keep (floor, floor_ahead) and publish the status. None without the option.

        gross_kwh: hour timestamp -> forecast house kWh of the hour with no solar (the part of the hour that is left in the
        current hour), for every hour of the forecast, also one without a price. The levels are rounded up to the optimiser's
        grid, so the DP can reach exactly what is asked.
        """
        window = self.storm_window()
        if window is None:
            return None
        solar = self.config["solar"]
        minimum, maximum = solar["powerwall_battery_level_min"], solar["powerwall_battery_level_max"]
        buffer, source = self.storm_buffer()
        plan = storm_plan(window, hours, gross_kwh, efficiency, self.battery_size_used()[0], minimum, maximum, buffer, source)
        for hour in hours:
            floor = plan.floors.get(hour.timestamp)
            if floor is not None:
                hour.floor = min(minimum + math.ceil((floor - minimum) / step - 1e-9) * step, maximum)
        ahead = None
        for hour in reversed(hours):
            if hour.floor is not None:
                ahead = hour.floor if ahead is None else max(ahead, hour.floor)
            hour.floor_ahead = ahead
        plan.floors = {hour.timestamp: hour.floor for hour in hours if hour.floor is not None}
        self.storm_state = {"plan": plan, "outcome": None}
        self.storm_publish()
        return plan

    def storm_status(self, plan: StormPlan, hours: list) -> str:
        """The status of the sensor: the window's, or later when it starts after the hours the plan covers."""
        window = plan.window
        if window.planned and not plan.floors and hours and window.start is not None and window.start >= hours[-1].timestamp + datetime.timedelta(hours=1):
            return STATUS_LATER
        return window.status

    def storm_outcome(self, hours: list, levels: list[float], plan_name: str) -> None:
        """How far the plan that controls the battery is from the levels it was asked to keep (0: all of them are kept)."""
        state = getattr(self, "storm_state", None)
        if not state:
            return
        short = max((hour.floor - level for hour, level in zip(hours, levels) if hour.floor is not None), default=0.0)
        state["outcome"] = {"plan": plan_name, "short_percent": round(max(short, 0.0), 1)}
        self.storm_publish()

    def _storm_notice(self, status: str, window, floors: dict) -> None:
        """The push notification of a storm: coming and going on are one problem (pushed once), over only on an explicit answer
        (the warning ended, is gone, or is no longer one that counts). A warning that can't be read or an entity that is unavailable
        says nothing, and a warning far ahead (later) is not a storm yet. Only the final status of the plan counts (storm_finish):
        a plan without prices never finishes, and says nothing."""
        if status in (STATUS_ACTIVE, STATUS_COMING):
            def shown(value):
                return value.strftime("%d/%m %H:%M") if isinstance(value, datetime.datetime) else "–"

            self.notice(
                "storm", True, level=window.level if window.level is not None else "–", start=shown(window.start), end=shown(window.end),
                keep=f"{max(floors.values()):.0f}" if floors else "–",
            )
        elif status in (STATUS_EXPIRED, STATUS_NONE, STATUS_OFF, STATUS_WEAK, STATUS_OTHER):
            self.notice("storm", False)
        else:
            self.notice("storm", None)  # later, unreadable, no start, invalid, entity unavailable: not judged (also not an earlier True)

    def storm_publish(self) -> None:
        """The state and attributes of sensor.<prefix>_storm_warning."""
        state = getattr(self, "storm_state", None)
        if not state:
            return
        plan: StormPlan = state["plan"]
        window = plan.window
        floors = plan.floors
        first = min(floors) if floors else None
        status = state.get("status") or window.status
        self.set_state("storm_warning", status)
        if state.get("status"):  # (the final status of the plan: storm_setup and storm_outcome publish the window's own, not final)
            self._storm_notice(status, window, floors)
        outcome = state.get("outcome") or {}

        tz = self._snap.tz if self._snap is not None else None

        def iso(value):
            """Timezone aware, like the other sensors that publish a time (the engine's times are naive local)."""
            return value.replace(tzinfo=tz).isoformat() if isinstance(value, datetime.datetime) else None

        self.set_attr("storm_warning", "awareness_level", window.level)
        self.set_attr("storm_warning", "awareness_type", window.kind)
        self.set_attr("storm_warning", "start", iso(window.start))
        self.set_attr("storm_warning", "end", iso(window.end))
        self.set_attr("storm_warning", "end_from", window.end_from)
        self.set_attr("storm_warning", "expected_use_kwh", round(plan.need_kwh, 2))
        self.set_attr("storm_warning", "buffer_percent", round(plan.buffer * 100.0))
        self.set_attr("storm_warning", "buffer_from", plan.buffer_from)
        self.set_attr("storm_warning", "needed_percent", round(plan.need_percent, 1))
        self.set_attr("storm_warning", "needed_capped", plan.capped)
        self.set_attr("storm_warning", "need_complete", plan.complete)  # False: an hour of the warning has no forecast yet
        self.set_attr("storm_warning", "keep_from", iso(first))
        self.set_attr("storm_warning", "keep_percent", round(max(floors.values()), 1) if floors else None)
        self.set_attr("storm_warning", "plan", outcome.get("plan"))
        self.set_attr("storm_warning", "short_percent", outcome.get("short_percent"))

    def storm_finish(self, plan: StormPlan | None, hours: list) -> None:
        """The status that is published when the plan is done (a window after the plan's hours is "later")."""
        state = getattr(self, "storm_state", None)
        if plan is None or not state:
            return
        state["status"] = self.storm_status(plan, hours)
        self.storm_publish()

    def storm_reason(self, ctx, plan: StormPlan, at: datetime.datetime) -> str:
        """The line of a plan entry that is for the storm: the level kept and what it is from."""
        window = plan.window
        level = plan.floors.get(at) or max(plan.floors.values(), default=0.0)
        return ctx.r(
            "storm_keep",
            level=f"{level:.0f}",
            time=window.start.strftime("%d/%m %H:%M") if window.start else "–",
            kwh=f"{plan.need_kwh:.1f}",
            buffer=f"{plan.buffer * 100.0:.0f}",
        )

    def storm_floor_now(self) -> float:
        """The level (%) the battery has to have at the end of the hour that is going on, 0 without a storm level."""
        state = getattr(self, "storm_state", None)
        if not state:
            return 0.0
        return state["plan"].floors.get(reset_time_to_hour(self.now()), 0.0)

    def apply_storm_to_rules(self, ctx) -> bool:
        """The rules control the battery: the hours enforce_storm_floor() changed in the rules' plan are changed in the plan.

        A charge becomes a charge session (the same entry the optimiser plan makes), a cancelled sale is dropped and the
        hour holds (or runs normally). Returns True when the plan changed.
        """
        changed = False
        state = getattr(self, "storm_state", None)
        for timestamp, (before, after, kwh, price) in sorted((getattr(self, "rule_storm", None) or {}).items()):
            plan_day = ctx.charging_plan.get(days_between(ctx.current_hour, timestamp))
            if plan_day is None or not state:
                continue
            plan = state["plan"]
            reason = self.storm_reason(ctx, plan, timestamp)
            if after == "grid_charging" and kwh >= 0.1:  # a few Wh is not worth a charge hour
                percentage = ctx.kwh_to_percentage(kwh) * ctx.efficiency
                session = {"Cost": round(kwh * price, 2), "kWh": round(kwh, 2), "battery_level": round(percentage, 2), "Price": round(price, 2), STORM_RULE: True}
                for period in [ts for ts in plan_day["force_discharge_timestamps"] if reset_time_to_hour(ts) == timestamp]:
                    del plan_day["force_discharge_timestamps"][period]
                # the hour's kWh are the whole hour's charge: what a rule already planned in it (one session per price period with
                # 15 minute prices) is replaced, not added to, and the totals follow
                earlier = [ts for ts, item in list(ctx.charge_hours.items()) if isinstance(ts, datetime.datetime) and reset_time_to_hour(ts) == timestamp and isinstance(item, dict)]
                earlier_cost = sum(ctx.charge_hours[ts]["Cost"] for ts in earlier)
                earlier_kwh = sum(ctx.charge_hours[ts]["kWh"] for ts in earlier)
                for ts in earlier:
                    del ctx.charge_hours[ts]
                    plan_day["charging_sessions"].pop(ts, None)
                ctx.charge_hours[timestamp] = session
                plan_day["force_powerwall_max_hours_old_discharge_timestamps"].pop(timestamp, None)
                for blocked in [ts for ts in plan_day["blocked_solar_charge_timestamps"] if reset_time_to_hour(ts) == timestamp]:
                    del plan_day["blocked_solar_charge_timestamps"][blocked]
                plan_day["total_cost"] += session["Cost"] - earlier_cost
                ctx.totals[:] = round(ctx.totals[0] + session["Cost"] - earlier_cost, 2), round(ctx.totals[1] + session["kWh"] - earlier_kwh, 2)
                self.add_charging_session_to_day(
                    ctx,
                    timestamp,
                    days_between(ctx.current_hour, timestamp),
                    reason=(
                        f"<details><summary>{emoji_parse({STORM_RULE: True})}{ctx.r('title.storm')} ({price:.2f}/{percentage:.0f}%)</summary>"
                        f"{ctx.r('charging_at', kwh=f'{kwh:.2f} kWh', price=f'{price:.2f} {ctx.valuta_kwh}')}<br>{reason}"
                        "</details>"
                    ),
                )
                changed = True
            elif after == "force_discharge":
                # a sale that the level caps: the sale periods of the hour together sell no more than kwh
                periods = [ts for ts in plan_day["force_discharge_timestamps"] if reset_time_to_hour(ts) == timestamp]
                total = sum(plan_day["force_discharge_timestamps"][ts]["kwh"] for ts in periods)
                if total > 0.0 and kwh < total:
                    for ts in periods:
                        entry = plan_day["force_discharge_timestamps"][ts]
                        entry["profit"] *= kwh / total
                        entry["kwh"] *= kwh / total
                    changed = True
            elif before in ("force_discharge", "discharge_allowed"):
                for period in [ts for ts in plan_day["force_discharge_timestamps"] if reset_time_to_hour(ts) == timestamp]:
                    del plan_day["force_discharge_timestamps"][period]
                if after == "discharge_allowed":
                    if timestamp not in plan_day["discharge_timestamps"]:
                        plan_day["discharge_timestamps"].append(timestamp)
                else:
                    if timestamp in plan_day["discharge_timestamps"]:
                        plan_day["discharge_timestamps"].remove(timestamp)
                    plan_day["blocked_discharge_timestamps"][timestamp] = (
                        f"<details><summary>{emoji_parse({STORM_RULE: True})}{ctx.r('title.storm_hold')}</summary>{reason}</details>"
                    )
                changed = True
        return changed
