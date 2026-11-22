"""The test of the user's own automation: running it step by step and the published result.

A switch starts the test (and stops it). One action after the other is published on the action sensor, the battery is
measured, and the result of every step is published as markdown on the automation_test sensor. The runner is a
background task; the engine holds the action and measures (engine/automation_test.py), so a runner that dies can not
leave the battery in a forced mode: the hold ends by itself. Nothing but the published action is changed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.config import AUTOMATION_CONFIG_PATH
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from ..engine.automation_test import (
    AUTOMATION_TEST_ACTIONS,
    HOLD_MARGIN_SECONDS,
    PASSES_NEEDED,
    PRECHECK_SKIPS,
    SAMPLE_SECONDS,
    SKIP_REASONS,
    STEP_TIMEOUT,
    STOP_REASONS,
    build_automation_test_markdown,
    judge_step,
)
from ..management.automation_inputs import actions_without_steps, automations_using_blueprint

_LOGGER = logging.getLogger(__name__)


class CoordinatorAutomationTest:
    """Run, stop and publish the test of the user's automation."""

    def _init_automation_test(self) -> None:
        # automation: "found" when the automation made from the blueprint was found in automations.yaml, else "unknown"
        self.automation_test_state: dict[str, Any] = {
            "status": "idle", "steps": [], "reason": None, "finished": None, "markdown": "", "automation": None,
        }
        self._automation_test_task: asyncio.Task | None = None

    @callback
    def async_restore_automation_test(self, attributes: dict[str, Any]) -> None:
        """The result of the last test, from before the restart. A test that was running did not finish."""
        steps = attributes.get("steps")
        status = attributes.get("status")
        if status not in ("done", "stopped", "failed") or not isinstance(steps, list):
            return
        # what is shown is built from the steps: one that is not what the test writes would raise in the markdown
        number = (int, float, type(None))
        if not all(
            isinstance(step, dict) and step.get("action") in AUTOMATION_TEST_ACTIONS and step.get("status") in ("ok", "fail", "skip")
            and (step.get("reason") in SKIP_REASONS if step.get("status") == "skip" else step.get("reason") is None)
            and all(isinstance(step.get(key), number) for key in ("charge", "discharge", "deficit", "surplus", "seconds"))
            for step in steps
        ):
            return
        reason, automation = attributes.get("reason"), attributes.get("automation")
        self.automation_test_state = {
            "status": status, "steps": steps, "reason": reason if reason in STOP_REASONS else None,
            "finished": attributes.get("finished"), "markdown": "", "automation": automation if automation in ("found", "unknown") else None,
        }
        self.automation_test_state["markdown"] = build_automation_test_markdown(self.t, self.automation_test_state)

    @callback
    def async_run_automation_test(self) -> None:
        """Start the test. Nothing happens while one runs or before BattMind has started."""
        if self.automation_test_state["status"] == "running" or not self.started or self.engine is None:
            return
        self._publish_automation_test(status="running", steps=[], reason=None, finished=None, automation=None)
        self._automation_test_task = task = self.entry.async_create_background_task(
            self.hass, self._async_automation_test(), name=f"battmind_{self.prefix}_automation_test"
        )
        task.add_done_callback(self._automation_test_ended)

    @callback
    def _automation_test_ended(self, _task: asyncio.Task) -> None:
        """A task that was stopped before it ran never gets to publish its end: the status must not stay on running."""
        if self.automation_test_state["status"] == "running":
            self._publish_automation_test(status="stopped", reason="stopped", finished=dt_util.now().isoformat(timespec="seconds"))

    @callback
    def async_stop_automation_test(self) -> None:
        """Stop the test: the step that runs ends, and BattMind's own action is published again."""
        if self._automation_test_task and not self._automation_test_task.done():
            self._automation_test_task.cancel()

    async def async_unload_automation_test(self) -> None:
        """Called when the entry is unloaded: stop the test and put the real action back before the worker is shut down."""
        task = self._automation_test_task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _publish_automation_test(self, **changes: Any) -> None:
        state = {**self.automation_test_state, **changes}
        state["markdown"] = build_automation_test_markdown(self.t, state)
        self.automation_test_state = state
        async_dispatcher_send(self.hass, self.signal, "automation_test")

    async def _async_automation_test(self) -> None:
        steps: list[dict[str, Any]] = []
        status, reason = "done", None
        try:
            begin = (await self.async_run_job("automation_test_begin", publish=False)).value
            if not begin or not begin["ok"]:
                status, reason = "failed", (begin or {}).get("reason") or "internal_error"
                return
            limits = (await self.async_run_job("automation_test_limits", publish=False)).value
            # an action the automation has no steps for does nothing: it can be seen from the file, not from the battery
            found = await self.hass.async_add_executor_job(
                automations_using_blueprint, self.hass.config.path(AUTOMATION_CONFIG_PATH), f"sensor.{self.prefix}_powerwall_action"
            )
            without_steps = actions_without_steps(found, AUTOMATION_TEST_ACTIONS)
            self._publish_automation_test(automation="found" if found else "unknown")
            for action in AUTOMATION_TEST_ACTIONS:
                if action in begin["unsupported"]:
                    steps.append({"action": action, "status": "skip", "reason": "unsupported"})
                    self._publish_automation_test(steps=list(steps))
                    continue
                if action in without_steps:
                    steps.append({"action": action, "status": "skip", "reason": "no_steps"})
                    self._publish_automation_test(steps=list(steps))
                    continue
                step, stop = await self._async_automation_test_step(action, limits)
                steps.append(step)
                self._publish_automation_test(steps=list(steps))
                if stop:
                    status, reason = "stopped", stop
                    break
        except asyncio.CancelledError:
            status, reason = "stopped", "stopped"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("The automation test failed")
            status, reason = "failed", "internal_error"
        finally:
            await self._async_automation_test_release()
            if status == "stopped":  # the steps that were not run are shown, not left out
                steps.extend({"action": action, "status": "skip", "reason": "not_reached"} for action in AUTOMATION_TEST_ACTIONS[len(steps):])
            self._publish_automation_test(
                status=status, steps=steps, reason=reason, finished=dt_util.now().isoformat(timespec="seconds")
            )

    async def _async_automation_test_release(self) -> None:
        """Put BattMind's own action back. Also run when the test is stopped or the entry is unloaded."""
        try:
            # with the forecast, like every planning run: the plan that is published again must not be made without the sun
            await asyncio.shield(self.async_run_job("automation_test_release", include_forecast=True))
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            _LOGGER.warning("The automation test could not put BattMind's own action back; it ends by itself", exc_info=True)

    async def _async_automation_test_step(self, action: str, limits: dict) -> tuple[dict[str, Any], str | None]:
        """One action: check that it can be proven, publish it, and measure until the battery has shown what it does.

        Returns the step and the reason the whole test must end (None: go on)."""
        args = (limits["level_min"], limits["level_max"], limits["margin"])
        # before anything is held: a step that can not prove anything is not run (a full battery can not be charged), and
        # what the battery does now is what the step has to change
        seen_before = (await self.async_run_job("automation_test_sample", False, publish=False)).value
        if not seen_before or not seen_before["sample"]:
            return self._step(action, "skip", "no_data", 0, None, None), None
        before = seen_before["sample"]
        check = judge_step(action, before, *args, before, limits["discharge_limit_w"])
        if check["verdict"] == "skip" and check["reason"] in PRECHECK_SKIPS:
            return self._step(action, "skip", check["reason"], 0, check, before), None

        held = (await self.async_run_job("automation_test_hold", action, STEP_TIMEOUT + HOLD_MARGIN_SECONDS, include_forecast=True)).value
        if held == "battery_stopped":
            return self._step(action, "skip", "battery_stopped", 0, None, None), "battery_stopped"
        if held != "ok":
            return self._step(action, "skip", "not_published", 0, None, None), "internal_error"
        started = time.monotonic()
        passes = 0
        last: dict[str, Any] | None = None  # the last measurement that had numbers, with its verdict
        while time.monotonic() - started < STEP_TIMEOUT:
            await asyncio.sleep(SAMPLE_SECONDS)
            seen = (await self.async_run_job("automation_test_sample", publish=False)).value
            if seen is None:
                continue  # the job failed: nothing was measured
            if seen["abort"] or not seen["held"]:
                reason = seen["abort"] or "expired"
                return self._step(action, "skip", reason, time.monotonic() - started, None, None), reason
            sample = seen["sample"]
            if sample is None:
                if seen["why"] == "no_data":
                    last, passes = None, 0  # a sensor lost its numbers: what was seen before does not count
                continue
            check = judge_step(action, sample, *args, before, limits["discharge_limit_w"])
            if check["verdict"] == "skip" and check["reason"] == "limit":
                # the battery got to a limit while it was forced: stop forcing at once, not at the end of the step
                await self._async_automation_test_release()
                return self._step(action, "skip", "limit", time.monotonic() - started, check, sample), None
            last = {**check, "sample": sample}
            passes = passes + 1 if check["verdict"] == "ok" else 0
            if passes >= PASSES_NEEDED:
                break
        seconds = time.monotonic() - started
        if last is None:
            return self._step(action, "skip", "no_data", seconds, None, None), None
        if passes >= PASSES_NEEDED:
            return self._step(action, "ok", None, seconds, last, last["sample"]), None
        if last["verdict"] == "skip":
            return self._step(action, "skip", last["reason"], seconds, last, last["sample"]), None
        return self._step(action, "fail", None, seconds, last, last["sample"]), None

    @staticmethod
    def _step(action: str, status: str, reason: str | None, seconds: float, check: dict | None, sample: dict | None) -> dict[str, Any]:
        """The record of one step: what was seen (W, rounded) when there is a measurement."""
        step: dict[str, Any] = {"action": action, "status": status, "reason": reason, "seconds": round(seconds)}
        if sample:
            step.update(charge=round(sample["charge"]), discharge=round(sample["discharge"]))
        if check:
            step.update(deficit=check["deficit"], surplus=check["surplus"])
            if status == "skip" and reason == "limit" and check.get("limit_side"):
                step["limit_side"] = check["limit_side"]
        return step
