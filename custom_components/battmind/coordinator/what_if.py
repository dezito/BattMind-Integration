"""The what-if view: its inputs, running and stopping a simulation, and the published result.

The inputs are entities (numbers, switches, selects and a text) whose values are kept here. A button starts the
simulation in a background task: the live plan, the live settings and the inverter are never touched, and a changed
input never triggers a planning run. The progress is published on the what_if_status sensor, the last successful
result (kept in .storage, so it survives a restart) on the what_if sensor.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from ..const import (
    WHAT_IF_FLAGS,
    WHAT_IF_OVERRIDE_NUMBERS,
    WHAT_IF_OVERRIDE_TEXTS,
    WHAT_IF_RULE_SELECTS,
    unsupported_actions,
    unsupported_switches,
)
from ..engine.planner.replay import WhatIfCancelled
from ..engine.planner.what_if_view import build_view
from ..management.what_if_request import WHAT_IF_SCHEMA, what_if_module_options, what_if_range_values
from ..management.what_if_storage import DashboardJob, WhatIfStorage

_LOGGER = logging.getLogger(__name__)

# the request fields that are not overrides of the scenario
_REQUEST_FIELDS = (
    "config_entry_id", "history_days", "range_start", "range_end", "range_step",
    "module_capacity", "module_price", "module_count",
)
# a failed attempt is shown with one of these reasons (texts: dashboard.what_if_reason_<reason>)
INTERNAL_ERROR = "internal_error"


def failure_reason(err: BaseException) -> str:
    """The reason code of a failed simulation: the translation key of the error without its what_if_ prefix."""
    if isinstance(err, HomeAssistantError) and err.translation_key:
        return err.translation_key.removeprefix("what_if_")
    if isinstance(err, vol.Invalid):
        return "invalid_inputs"
    return INTERNAL_ERROR


class CoordinatorWhatIf:
    """Inputs, run/stop and published state of the what-if view."""

    what_if_store: WhatIfStorage
    what_if_job: DashboardJob | None = None

    def _init_what_if(self) -> None:
        self.what_if_store = WhatIfStorage(self.hass, self.entry.entry_id)
        # entity key -> value of the inputs (numbers, switches, selects and the text), set by the entities
        self.what_if_inputs: dict[str, Any] = {}
        self.what_if_job = None
        # idle | running | stopping | stopped | failed, with the reason of a failure and the progress of a run
        self.what_if_state: dict[str, Any] = {"status": "idle", "reason": None, "error": None, "progress": None}
        self.what_if_record: dict[str, Any] | None = None
        self.what_if_view: dict[str, Any] | None = None
        self._what_if_progress_sample: dict[str, float] | None = None
        self._what_if_days = 0

    # ------------------------------------------------------------------ #
    # Inputs
    # ------------------------------------------------------------------ #

    @callback
    def async_set_what_if_input(self, key: str, value: Any) -> None:
        """Keep a what-if input. It is only used by the next simulation, nothing is replanned."""
        self.what_if_inputs[key] = value

    @callback
    def async_reset_what_if_settings(self) -> None:
        """Back to Current: every scenario rule and setting stops being an override."""
        for key in (*WHAT_IF_RULE_SELECTS, *WHAT_IF_OVERRIDE_NUMBERS, *WHAT_IF_OVERRIDE_TEXTS):
            self.what_if_inputs.pop(key, None)
            async_dispatcher_send(self.hass, self.signal, key)

    def what_if_request(self) -> dict[str, Any]:
        """The validated simulation request of the inputs, the same as the action's data.

        Only rules and settings that were changed are overrides. Raises vol.Invalid or HomeAssistantError for an
        input that does not make a valid request; nothing is defaulted.
        """
        inputs = self.what_if_inputs
        data: dict[str, Any] = {}
        for key in ("what_if_history_days", "what_if_battery_size"):
            if inputs.get(key) is None:
                raise vol.Invalid(f"{key} has no value")
        data["history_days"] = int(inputs["what_if_history_days"])
        data["battery_size"] = float(inputs["what_if_battery_size"])
        for enabled, keys in (
            ("what_if_range", ("range_start", "range_end", "range_step")),
            ("what_if_modules", ("module_capacity", "module_price", "module_count")),
        ):
            if inputs.get(enabled):
                for key in keys:
                    value = inputs.get(f"what_if_{key}")
                    if value is None:
                        raise vol.Invalid(f"what_if_{key} has no value")
                    data[key] = float(value)
        unsupported = unsupported_switches(unsupported_actions(self.config))
        for entity_key, key in WHAT_IF_RULE_SELECTS.items():
            # a rule that needs an action the battery can't run is off, it can't be a scenario value
            if inputs.get(entity_key) in ("on", "off") and key not in unsupported:
                data[key] = inputs[entity_key] == "on"
        for entity_key, key in WHAT_IF_OVERRIDE_NUMBERS.items():
            if inputs.get(entity_key) is not None:
                data[key] = float(inputs[entity_key])
        for entity_key, key in WHAT_IF_OVERRIDE_TEXTS.items():
            if inputs.get(entity_key) is not None:
                data[key] = inputs[entity_key]
        data = WHAT_IF_SCHEMA({**data, "config_entry_id": self.entry.entry_id})
        what_if_range_values(data)
        what_if_module_options(data)
        return data

    # ------------------------------------------------------------------ #
    # Published state
    # ------------------------------------------------------------------ #

    def _publish_what_if_status(self, **changes: Any) -> None:
        self.what_if_state = {**self.what_if_state, **changes}
        async_dispatcher_send(self.hass, self.signal, "what_if_status")
        async_dispatcher_send(self.hass, self.signal, "what_if_progress")

    @callback
    def async_set_what_if_record(self, record: dict[str, Any] | None) -> None:
        """Publish a saved result (the last successful simulation) on the what_if sensor."""
        self.what_if_record = record
        self.what_if_view = None
        if record is not None:
            try:
                self.what_if_view = build_view(
                    record["simulation"], self.t, self.language, self.t("ui.common.valuta"), record.get("inputs")
                )
            except Exception:  # noqa: BLE001
                # an old or unexpected result: the sensor shows the result without the derived values
                _LOGGER.warning("The what-if result could not be summarised for the dashboard", exc_info=True)
        async_dispatcher_send(self.hass, self.signal, "what_if")
        for key in WHAT_IF_FLAGS:
            async_dispatcher_send(self.hass, self.signal, key)

    async def async_load_what_if_result(self) -> None:
        """Publish the last result saved by an earlier run (also from before a restart)."""
        try:
            record = await self.what_if_store.load()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("The saved what-if result could not be loaded", exc_info=True)
            return
        if record:
            self.async_set_what_if_record(record)

    # ------------------------------------------------------------------ #
    # Run and stop
    # ------------------------------------------------------------------ #

    @callback
    def async_run_what_if(self) -> None:
        """Switch what_if_run on: one simulation at a time, started in the background."""
        if self.what_if_job is not None or self.what_if_store.closed:
            return
        try:
            data = self.what_if_request()
        except (vol.Invalid, HomeAssistantError) as err:
            _LOGGER.debug("What-if inputs are not valid: %s", err)
            self._publish_what_if_status(status="failed", reason=failure_reason(err), error=str(err), progress=None)
            return
        job = DashboardJob(str(uuid4()))
        self.what_if_job = job
        # unloading the entry stops the run (WhatIfStorage.close) unless its result is already being saved
        self.what_if_store.dashboard_job = job
        self._what_if_progress_sample = None
        self._what_if_days = data["history_days"]
        self._publish_what_if_status(status="running", reason=None, error=None, progress=None)
        task = self.hass.async_create_background_task(
            self._async_what_if_task(job, data), name=f"battmind_{self.prefix}_what_if"
        )
        self.what_if_store.track(task)

    @callback
    def async_stop_what_if(self) -> None:
        """Switch what_if_run off: the worker stops at the next hour; a result that is being saved is kept."""
        job = self.what_if_job
        if job is None or job.committing or job.cancelled.is_set():
            return
        job.cancelled.set()
        self._publish_what_if_status(status="stopping")

    @callback
    def _what_if_progress(self, job: DashboardJob, info: dict[str, Any]) -> None:
        """Called on the event loop while the history is loaded and the simulation runs (about every 10 seconds)."""
        job.report(info)
        if self.what_if_job is not job:
            return
        progress = dict(info)
        days = self._what_if_days
        if "phase" in progress:
            progress["message"] = self.t(
                "ui.what_if.loading", days=days, phase=self.t(f"ui.what_if.phase_{progress['phase']}"),
                seconds=progress["seconds"],
            )
        else:
            # the remaining time follows from the hours done between two reports, only once it is meaningful
            seconds, percent = progress.get("seconds"), progress.get("percent")
            total = progress["total_hours"] * progress["steps"]
            done = (progress["step"] - 1) * progress["total_hours"] + progress["hours"]
            previous = self._what_if_progress_sample
            if (
                previous and seconds - previous["seconds"] >= 20 and done > previous["done"]
                and percent >= 5 and percent - previous["percent"] >= 2 and done < total
            ):
                progress["eta_seconds"] = int(
                    -(-(total - done) * (seconds - previous["seconds"]) // (done - previous["done"]))
                )
            if previous is None or done > previous["done"]:
                self._what_if_progress_sample = {"done": done, "seconds": seconds, "percent": percent}
            message = self.t(
                "ui.what_if.progress", percent=percent, step=progress["step"], steps=progress["steps"],
                days=round(progress["hours"] / 24.0), total=round(progress["total_hours"] / 24.0),
            )
            if (eta := progress.get("eta_seconds")) and eta > 0:
                message += " " + (
                    self.t("dashboard.what_if_eta_minutes", n=-(-eta // 60)) if eta >= 60
                    else self.t("dashboard.what_if_eta_seconds", n=eta)
                )
            progress["message"] = message
        self._publish_what_if_status(progress=progress)

    async def _async_what_if_task(self, job: DashboardJob, data: dict[str, Any]) -> None:
        """Run the simulation, save the result and publish the outcome. Commits only if it was not stopped."""
        outcome: dict[str, Any] = {"status": "idle", "reason": None, "error": None}
        try:
            capacity_range = what_if_range_values(data)
            module_comparison = what_if_module_options(data)
            overrides = {key: value for key, value in data.items() if key not in _REQUEST_FIELDS}
            kwargs: dict[str, Any] = {"cancel_check": job.check}
            if capacity_range is not None:
                kwargs["capacity_range"] = capacity_range
            if module_comparison is not None:
                kwargs["module_comparison"] = module_comparison
            job.check()
            result = await self.async_what_if(
                overrides, data["history_days"], lambda info: self._what_if_progress(job, info), **kwargs
            )
            job.check()
            if result.error:
                raise result.error
            record = {
                "completed_at": dt_util.utcnow().isoformat(),
                "inputs": {key: value for key, value in data.items() if key != "config_entry_id"},
                "simulation": result.value,
            }
            try:
                await self.what_if_store.save(record, job)
            except WhatIfCancelled:
                raise
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("The what-if result could not be saved: %s", err)
                outcome = {"status": "failed", "reason": "save_failed", "error": str(err)}
            else:
                self.async_set_what_if_record(record)
        except WhatIfCancelled:
            outcome = {"status": "stopped", "reason": None, "error": None}
        except Exception as err:  # noqa: BLE001
            if not isinstance(err, (HomeAssistantError, vol.Invalid)):
                _LOGGER.exception("The what-if simulation failed")
            outcome = {"status": "failed", "reason": failure_reason(err), "error": str(err)}
        finally:
            if self.what_if_job is job:
                self.what_if_job = None
            if self.what_if_store.dashboard_job is job:
                self.what_if_store.dashboard_job = None
            self._what_if_progress_sample = None
        self._publish_what_if_status(progress=None, **outcome)
