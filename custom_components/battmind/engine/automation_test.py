"""The test of the user's own automation: does the battery do what each BattMind action asks for?

BattMind never sends a command; the user's automation (the blueprint) does, when the action sensor changes. To test it
BattMind publishes one action after the other on the action sensor, on purpose, and measures what the battery then does.
It only publishes and measures: the plan, the settings, the rules and the history of the planner are not touched.

* A step is only run when it can be proven: with no house load an action that must not discharge proves nothing, and
  with no solar surplus the action that blocks solar charging proves nothing. Such a step is reported as not tested,
  with the reason, never as passed.
* What is missing (a sensor without numbers, a stale battery power) is never read as "the battery did not move". A battery
  sensor that is quiet at 0 W (it reports only changes) may show that a step did not move the battery, never a stop.
* The held action is engine state that charge_if_needed reads every time it publishes (the cron, a forced recalculation
  and the settings all publish): it ends by itself after hold seconds, and at once when the battery stops for
  temperature, so a lost runner can not leave the battery in a forced mode.
"""
from __future__ import annotations

import datetime
import logging
import math
from typing import Callable

from ..utils.helpers import float_or_none
from ..utils.power import unique_entity_ids
from .analysis.plan_check import level_margin
from .energy.energy import derive_power_values

_LOGGER = logging.getLogger(__name__)

# the order is chosen so that every step starts from something the previous one moved: the battery is charged and
# discharged first, so "stop" can be seen as a change, and the two solar actions come last
AUTOMATION_TEST_ACTIONS = (
    "grid_charging",
    "force_discharge",
    "battery_stopped",
    "discharge_allowed",
    "force_solar_only_charging",
    "blocked_solar_charge",
)
# seconds
STEP_TIMEOUT = 300  # the longest a step waits for the battery to react
SETTLE_SECONDS = 20  # after the action was published, before the first measurement counts
WINDOW_SECONDS = 30  # a measurement is the mean of the last seconds
SAMPLE_SECONDS = 10  # between measurements
HOLD_MARGIN_SECONDS = 90  # the hold lives this long after the step's timeout, if nothing releases it
PASSES_NEEDED = 2  # measurements in a row that must agree
MIN_POINTS = 90  # of the 100 points of a window every sensor must have a number for
STALE_SECONDS = 300  # a battery power, consumption or solar sensor that has not reported for this long is not trusted
# W
MOVE_W = 200.0  # the least a battery must move to count as moving, and that more than normal operation could
STOPPED_W = 100.0  # below this the battery counts as standing still
LOAD_W = 300.0  # the house load above the solar (or the solar above the house) that a solar step needs to prove anything

# a verdict that means the step can not prove anything right now; it is also what a step is checked for before it is held
PRECHECK_SKIPS = ("limit", "no_load", "no_surplus", "ev", "no_baseline")


def mean_or_none(values: list[float], minimum: int = MIN_POINTS) -> float | None:
    """The mean of a window's points, None when the sensor did not have a number for enough of it."""
    if len(values) < minimum:
        return None
    return sum(values) / len(values)


def judge_step(
    action: str, sample: dict, level_min: float, level_max: float, margin: float, before: dict, discharge_limit_w: float,
) -> dict:
    """What one measurement says about one action.

    sample: charge and discharge (W, the battery), load (W, the house with the battery corrected away, the ignored
    consumers included), use (W, the house without the ignored consumers), ignored (W), solar (W), level (%).
    before: the measurement taken just before the action was published. An action that asks the battery to stop,
    stop discharging, stop charging or go back to normal can only be proven by a change, so the battery must have done
    the opposite before: standing still after a step that left it standing still proves nothing.
    discharge_limit_w: what the battery can discharge at most.
    Returns {"verdict": "ok" | "fail" | "skip", "reason": why it is a skip, "surplus"/"deficit": W}.
    """
    charge, discharge, level = sample["charge"], sample["discharge"], sample["level"]
    load_max, load_min = max(sample["load"], sample["use"]), min(sample["load"], sample["use"])
    solar, ignored = sample["solar"], sample["ignored"]
    # what normal operation could have moved with the battery on its own
    could_charge = max(solar - load_min, 0.0)
    could_discharge = max(load_max - solar, 0.0)
    surplus = max(solar - load_max, 0.0)
    deficit = max(load_min - solar, 0.0)
    full = level >= level_max - margin
    empty = level <= level_min + margin

    def result(verdict: str, reason: str | None = None) -> dict:
        return {"verdict": verdict, "reason": reason, "surplus": round(surplus), "deficit": round(deficit)}

    if action == "grid_charging":
        if full:
            return result("skip", "limit")
        if charge < MOVE_W:
            return result("fail")
        # more than the sun could have charged, or the sun may have done it alone
        return result("ok") if charge - could_charge >= MOVE_W else result("skip", "unproven")
    if action == "force_discharge":
        if empty:
            return result("skip", "limit")
        if discharge < MOVE_W:
            return result("fail")
        # more than the house used: it was sold
        return result("ok") if discharge - could_discharge >= MOVE_W else result("skip", "unproven")
    # a battery sensor that stopped reporting at 0 W (a template sensor reports only changes) can not prove a stop
    stale_still = bool(sample.get("battery_stale"))
    if action == "battery_stopped":
        if empty or full:
            return result("skip", "limit")
        if deficit < LOAD_W and surplus < LOAD_W:
            # normal operation would stand (nearly) still too: only a load or a surplus makes the stop visible
            return result("skip", "no_load")
        if max(before["charge"], before["discharge"]) < MOVE_W:
            return result("skip", "no_baseline")  # it was standing still already
        if stale_still:
            return result("skip", "no_data")
        return result("ok") if charge < STOPPED_W and discharge < STOPPED_W else result("fail")
    if action == "discharge_allowed":
        if empty:
            return result("skip", "limit")
        if ignored >= STOPPED_W:
            return result("skip", "ev")  # the blueprint runs the "while the car is charging" actions instead
        if deficit < LOAD_W:
            return result("skip", "no_load")
        if before["discharge"] >= STOPPED_W:
            return result("skip", "no_baseline")  # it discharged already: a forced discharge may still be running
        if discharge - could_discharge >= MOVE_W:
            return result("skip", "unproven")  # more than the house uses: not normal operation
        needed = max(STOPPED_W, 0.5 * min(deficit, discharge_limit_w))
        return result("ok") if discharge >= needed else result("fail")
    if action == "force_solar_only_charging":
        if empty:
            return result("skip", "limit")
        if deficit < LOAD_W:
            return result("skip", "no_load")  # only "does not discharge" can be seen, and that needs a load
        if before["discharge"] < MOVE_W:
            return result("skip", "no_baseline")  # it did not discharge before
        if stale_still:
            return result("skip", "no_data")
        return result("ok") if discharge < STOPPED_W else result("fail")
    if action == "blocked_solar_charge":
        if full:
            return result("skip", "limit")
        if ignored >= STOPPED_W:
            return result("skip", "ev")  # the blueprint runs the "while the car is charging" actions instead
        if surplus < LOAD_W:
            return result("skip", "no_surplus")
        if before["charge"] < MOVE_W:
            return result("skip", "no_baseline")  # it did not charge before
        if stale_still:
            return result("skip", "no_data")
        return result("ok") if charge < STOPPED_W else result("fail")
    raise ValueError(f"No test for the action {action}")


class AutomationTest:
    """The jobs of the engine that hold an action and measure the battery. The coordinator runs the steps."""

    def automation_test_begin(self) -> dict:
        """Whether a test can run now: {"ok": bool, "reason": ..., "unsupported": [...]}.

        A test never starts on a battery that is stopped for temperature (or whose temperature is unknown): the stop is a
        safety and the test must not be what ends it."""
        ids = self.config["home"]["entity_ids"]
        unsupported = sorted(set(self.unsupported_actions()) & set(AUTOMATION_TEST_ACTIONS))
        if not self.is_powerwall_configured() or not ids["power_consumption_entity_id"] or not ids["powerwall_battery_level_entity_id"]:
            return {"ok": False, "reason": "not_configured", "unsupported": unsupported}
        if not self.last_powerwall_action:
            return {"ok": False, "reason": "not_ready", "unsupported": unsupported}
        if self.last_powerwall_action == "battery_stopped":
            return {"ok": False, "reason": "battery_stopped", "unsupported": unsupported}
        if self._automation_level() is None:
            return {"ok": False, "reason": "no_level", "unsupported": unsupported}
        return {"ok": True, "reason": None, "unsupported": unsupported}

    def _automation_level(self) -> float | None:
        """The level of the battery sensor now. Unlike get_battery_level() a lost sensor is not read as 100 %."""
        value = float_or_none(self.entity_state(self.config["home"]["entity_ids"]["powerwall_battery_level_entity_id"]))
        return value if value is not None and math.isfinite(value) else None

    def automation_test_hold(self, action: str, seconds: float) -> str:
        """Publish action as BattMind's action for the next seconds (the plan is calculated as usual, only the published
        action and the rule text differ). Returns "ok", "battery_stopped" (stopped for temperature: nothing is held) or
        "not_published" (the calculation failed, so the action did not reach the sensor: nothing is held)."""
        if self.last_powerwall_action == "battery_stopped":
            return "battery_stopped"
        self._automation_hold = {"action": action, "until": self.now() + datetime.timedelta(seconds=seconds), "since": self.now()}
        self._automation_abort = None
        self.charge_if_needed()
        if self._automation_hold is None:
            return "battery_stopped" if self._automation_abort == "battery_stopped" else "not_published"
        if self._published_action != action:
            self._automation_hold = None
            self.automation_test_settle()
            return "not_published"
        return "ok"

    def automation_test_release(self) -> None:
        """End the hold and publish the real action again."""
        self._automation_hold = None
        self._automation_abort = None
        self.charge_if_needed()
        self.automation_test_settle()

    def automation_test_settle(self) -> None:
        """Make sure the sensor shows the real action when no hold is on. A calculation that failed before it published
        (prices, planner) would leave the last held action on the sensor, and so on the inverter."""
        if self._automation_hold is None and self.last_powerwall_action and self._published_action != self.last_powerwall_action:
            _LOGGER.warning("Publishing the last real action %s again, the calculation did not", self.last_powerwall_action)
            self.set_state("powerwall_action", self.last_powerwall_action)
            self._published_action = self.last_powerwall_action

    def automation_hold_action(self, real_action: str) -> str | None:
        """Called by charge_if_needed with what it decided: the held action, or None (no hold, expired or dropped)."""
        hold = self._automation_hold
        if hold is None:
            return None
        if self.now() >= hold["until"]:
            _LOGGER.warning("The automation test hold of %s ran out and is dropped", hold["action"])
            self._automation_hold, self._automation_abort = None, "expired"
            return None
        if real_action == "battery_stopped":
            _LOGGER.warning("The battery is stopped for temperature: the automation test is stopped")
            self._automation_hold, self._automation_abort = None, "battery_stopped"
            return None
        return hold["action"]

    def automation_test_sample(self, held: bool = True) -> dict:
        """What the battery does now: {"held": bool, "abort": reason or None, "sample": the measurement or None, "why": reason}.

        The measurement is the mean of the last WINDOW_SECONDS (the states of the sensors held until they change, no
        statistics) and needs a number for nearly all of the window from every sensor. held: measure for the action that
        is held, which needs a hold that has settled; False measures what the battery does before any step."""
        hold = self._automation_hold
        out: dict = {"held": hold is not None, "abort": self._automation_abort, "sample": None, "why": None}
        now = self.now()
        if held:
            if hold is None:
                return out
            if (now - hold["since"]).total_seconds() < SETTLE_SECONDS + WINDOW_SECONDS:
                out["why"] = "settling"
                return out
        start = now - datetime.timedelta(seconds=WINDOW_SECONDS)
        home, solar_config = self.config["home"], self.config["solar"]
        ids = home["entity_ids"]

        def window(entity_id: str) -> float | None:
            return mean_or_none(self._wc.short_values(entity_id, start, now)) if entity_id else None

        battery = window(ids["powerwall_watt_flow_entity_id"])
        consumption = window(ids["power_consumption_entity_id"])
        solar_id = solar_config["entity_ids"]["production_entity_id"]
        solar = window(solar_id) if solar_id else 0.0  # no solar sensor: there is no solar
        ignored_ids = unique_entity_ids(ids["ignore_consumption_from_entity_ids"])
        ignored_values = [window(entity_id) for entity_id in ignored_ids]
        level = self._automation_level()
        if None in (battery, consumption, solar, level) or None in ignored_values:
            out["why"] = "no_data"
            return out
        ignored = abs(sum(ignored_values))
        positive_is_charging = home["invert_powerwall_watt_flow_entity_id"]
        charge = max(battery if positive_is_charging else -battery, 0.0)
        discharge = max(-battery if positive_is_charging else battery, 0.0)
        # a sensor that stopped reporting shows its last number for ever: the three sensors the verdicts stand on must have
        # reported lately (a sensor that was never seen reporting is not checked). Sensors that report only changes are
        # quiet while nothing changes, so two quiet sensors are allowed: solar at (about) 0 in the dark, and a battery at
        # (about) 0 W, which then can not prove a stop (judge_step) but can show that a step did not move it
        reported, now_ts = self._snap.reported or {}, self._wc._ts(now)

        def stale(entity_id: str | None) -> bool:
            last = reported.get(entity_id) if entity_id else None
            return last is not None and now_ts - last > STALE_SECONDS

        battery_stale = stale(ids["powerwall_watt_flow_entity_id"])
        solar_stale = stale(solar_id) and not (abs(solar) < STOPPED_W and self._automation_dark(now))
        if stale(ids["power_consumption_entity_id"]) or solar_stale or (battery_stale and max(charge, discharge) >= STOPPED_W):
            out["why"] = "no_data"
            return out
        values = derive_power_values(home, abs(consumption), ignored, charge, discharge, abs(solar))
        out["sample"] = {
            "charge": charge,
            "discharge": discharge,
            "load": values["power_consumption"],
            "use": max(values["power_consumption_without_ignored"], 0.0),
            "ignored": ignored,
            "solar": abs(solar),
            "level": level,
            "battery_stale": battery_stale,
        }
        return out

    def _automation_dark(self, now: datetime.datetime) -> bool:
        """Whether the sun is down now (a solar sensor then shows 0 and may not report for a long time)."""
        try:
            sun = self.get_sun_events(now)
            return now <= sun["sunrise"] or now >= sun["sunset"]
        except Exception:  # noqa: BLE001
            return False  # not known: the solar sensor must have reported

    def automation_test_limits(self) -> dict:
        """The levels and the power the verdicts are judged against."""
        solar = self.config["solar"]
        return {
            "level_min": max(float(solar["powerwall_battery_level_min"]), self.number("usage_reserve")),
            "level_max": float(solar["powerwall_battery_level_max"]),
            "margin": level_margin(float(solar["powerwall_battery_size"])),
            "discharge_limit_w": float(solar["powerwall_discharging_power"]),
        }


def build_automation_test_markdown(t: Callable[..., str], state: dict) -> str:
    """The result as markdown (the texts are ui.automation_test.*): a table row per action and a summary.

    state: {"status", "steps": [{"action", "status": "ok" | "fail" | "skip", "reason", "seconds", values...}], "reason"}.
    """
    if state["status"] == "idle" and not state["steps"]:
        return t("ui.automation_test.not_run")
    icons = {"ok": "✅", "fail": "❌", "skip": "🔧"}
    if not state["steps"] and state["status"] != "running":
        # it ended before the first step (not ready, a battery that is stopped ...): nothing to tabulate or count
        lines = [f"**{t('ui.automation_test.status.' + state['status'])}**", ""]
        if state.get("reason"):
            lines += [t(f"ui.automation_test.stop_reason.{state['reason']}"), ""]
        return "\n".join([*lines, t("ui.automation_test.put_back"), "", t("ui.automation_test.legend")])
    lines = [
        f"**{t('ui.automation_test.status.' + state['status'])}**",
        "",
        f"| | {t('ui.automation_test.column_action')} | {t('ui.automation_test.column_result')} |",
        "|:-:|:--|:--|",
    ]
    counts = {"ok": 0, "fail": 0, "skip": 0}
    for step in state["steps"]:
        counts[step["status"]] += 1
        lines.append(f"| {icons[step['status']]} | {t('ui.automation_test.action.' + step['action'])} | {step_text(t, step)} |")
    if state["status"] == "running":
        lines += ["", t("ui.automation_test.running_note")]
    else:
        lines += ["", t("ui.automation_test.summary", ok=counts["ok"], fail=counts["fail"], skip=counts["skip"], total=len(state["steps"]))]
        if state.get("automation") == "unknown":
            lines.append(t("ui.automation_test.automation_unknown"))
        if state.get("reason"):
            lines.append(t(f"ui.automation_test.stop_reason.{state['reason']}"))
        if counts["fail"]:
            lines.append(t("ui.automation_test.fail_hint"))
        lines.append(t("ui.automation_test.put_back"))
    lines += ["", t("ui.automation_test.legend")]
    return "\n".join(lines)


def step_text(t: Callable[..., str], step: dict) -> str:
    """The result column of a step: what was seen, or why it was not tested."""
    seen = {key: round(step.get(key) or 0) for key in ("charge", "discharge", "deficit", "surplus")}
    seen["seconds"] = round(step.get("seconds") or 0)
    if step["status"] == "skip":
        return t(f"ui.automation_test.skip.{step['reason']}", **seen)
    text = t(f"ui.automation_test.{step['status']}.{step['action']}", **seen)
    if step["status"] == "fail" and step["action"] in ("discharge_allowed", "blocked_solar_charge"):
        text += " " + t("ui.automation_test.ev_hint")
    return text
