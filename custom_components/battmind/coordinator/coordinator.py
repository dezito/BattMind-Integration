"""Event loop side of BattMind.

The coordinator only does light work on the event loop: it collects a plain
:class:`Snapshot` of Home Assistant data, sends the calculation to the BattMind worker
thread and publishes the result. All heavy work (planning, history, predictions,
markdown) runs in the worker. The what-if simulation is the exception: it runs on a thread
and an engine of its own (see CoordinatorWhatIf), so it never holds up the worker.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import partial
from logging.handlers import QueueHandler, QueueListener
from typing import Any, Callable

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from ..const import CONF_ENTITY_PREFIX, DOMAIN, SIGNAL_UPDATE, build_config, default_options, normalize_language
from ..engine import BattMindEngine, JobResult, Snapshot
from ..engine.planner.what_if import PROGRESS_INTERVAL
from ..engine.planner.replay import WhatIfCancelled
from ..utils.cjp import cjp_charger_power_entities
from ..utils.formatting import translate
from ..utils.power import unique_entity_ids
from ..management.debug_export import DebugRecorder
from ..management.storage import BattMindStorage
from ..prices import CombinedPriceProvider
from .data import CoordinatorData
from .events import CoordinatorEvents
from .lifecycle import CoordinatorLifecycle
from .what_if import CoordinatorWhatIf

_LOGGER = logging.getLogger(__name__)


class BattMindCoordinator(CoordinatorLifecycle, CoordinatorEvents, CoordinatorData, CoordinatorWhatIf):
    """Owns the engine, the worker thread (and the what-if thread) and all scheduling for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.prefix: str = entry.data[CONF_ENTITY_PREFIX]
        self.config = build_config({**default_options(), **entry.options})
        self.texts: dict[str, str] = {}  # runtime texts, loaded in async_setup
        self.entity_names: dict[str, str] = {}  # '<platform>.<key>' -> name, loaded in async_setup
        self.engine: BattMindEngine | None = None
        self.storage = BattMindStorage(hass, entry.entry_id)
        self.language = normalize_language(self.config.get("language"), normalize_language(hass.config.language))
        self._init_what_if()

        self.data: dict[str, Any] = {}
        self.attributes: dict[str, dict[str, Any]] = {}
        self.settings: dict[str, Any] = {}
        # platform.key -> state from an imported backup, used by the entities instead of their last state
        self.restore_settings: dict[str, str] = {}
        self.solar_kwh_total: float | None = None
        self.started = False

        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"battmind_{self.prefix}_worker")
        # the what-if simulation (minutes to hours) runs on a thread of its own, on an engine of its own, so the worker
        # above, which the planning and the optimizer run on, is never held up by it (no _job_lock either)
        self._what_if_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"battmind_{self.prefix}_what_if")
        # set when Home Assistant stops: every running what-if (also one started by the action) stops at its next check
        self._what_if_stopping = threading.Event()
        self._job_lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._started_unsub: Callable[[], None] | None = None
        self._charge_task: asyncio.Task | None = None
        self._charge_rerun = False
        self._charge_rerun_force = False
        self._settings_debounce: Callable[[], None] | None = None

        self._buffers: dict[str, list[tuple[float, float | None]]] = {}
        self._statistics: dict[str, list[tuple[float, float]]] = {}
        self._statistics_refreshed = 0.0
        self._entity_platforms: dict[str, str | None] = {}
        self._sun_cache: dict[datetime.date, dict] = {}

        # The ignored consumption: the sensors the user listed plus (unless switched off) the chargers of the CJP cars, once
        # each. The engine reads this list, so every place that subtracts it sees the same one.
        listed = unique_entity_ids(self.config["home"]["entity_ids"]["ignore_consumption_from_entity_ids"])
        self.cjp_chargers: list[str] = cjp_charger_power_entities(hass) if self.config["home"]["ignore_cjp_chargers"] else []
        # not required to exist or be available at start: a CJP car that is not set up yet must not hold BattMind up
        self.auto_ignored_entities = [entity_id for entity_id in self.cjp_chargers if entity_id not in listed]
        self.config["home"]["entity_ids"]["ignore_consumption_from_entity_ids"] = [*listed, *self.auto_ignored_entities]
        self.config["home"]["auto_ignored_entity_ids"] = list(self.auto_ignored_entities)
        self.config["home"]["charger_entity_ids"] = cjp_charger_power_entities(hass)
        self._cjp_reload_scheduled = False

        home = self.config["home"]["entity_ids"]
        solar = self.config["solar"]["entity_ids"]
        battery_level = home["powerwall_battery_level_entity_id"]
        # entities whose history is buffered (live state events + one recorder load at startup)
        self.buffered_entities = sorted({
            e for e in (
                home["power_consumption_entity_id"],
                home["powerwall_watt_flow_entity_id"],
                battery_level,
                solar["production_entity_id"],
                *(home["ignore_consumption_from_entity_ids"] or []),
            ) if e
        })
        # hourly mean statistics are used for the power averages, not for the battery level
        self.statistics_entities = [e for e in self.buffered_entities if e != battery_level]
        # what the user configured: it must exist and be available before BattMind starts
        self.configured_entities = sorted({
            *self.buffered_entities,
            *self.config["prices"]["entity_ids"].values(),
            *self.config["forecast"]["entity_ids"].values(),
            solar["forecast_entity_id"],
        } - {""} - set(self.auto_ignored_entities))
        # the states and units of the snapshot also cover the chargers found by BattMind (they are read in the right unit)
        self.snapshot_entities = sorted({*self.configured_entities, *self.auto_ignored_entities})

        self._log_listener: QueueListener | None = None
        self._log_handler: QueueHandler | None = None

        self.last_job: dict[str, Any] = {}
        # last jobs and snapshots for the debug export (management/debug_export.py)
        self.debug = DebugRecorder()

    # ------------------------------------------------------------------ #
    # Configured entities
    # ------------------------------------------------------------------ #

    @property
    def legacy_prefix(self) -> str | None:
        """Prefix of the PyScript installation chosen for import in the config flow (None = no import)."""
        return self.entry.data.get("legacy_prefix")

    @property
    def price_provider(self) -> CombinedPriceProvider | None:
        """The engine's price provider: everything about prices (entities, snapshot, history)."""
        return self.engine.price_provider if self.engine is not None else None

    @property
    def signal(self) -> str:
        return SIGNAL_UPDATE.format(entry_id=self.entry.entry_id)

    # ------------------------------------------------------------------ #
    # Setup / unload
    # ------------------------------------------------------------------ #

    def t(self, key: str, default: str | None = None, **fmt) -> str:
        return translate(self.texts, key, default, **fmt)

    # ------------------------------------------------------------------ #
    # Startup sequence
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Triggers
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Settings entities
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # State publishing
    # ------------------------------------------------------------------ #

    async def _async_publish(self, result: JobResult, job_name: str) -> None:
        keys = set(result.states) | set(result.attributes)
        for key in keys:
            if key in result.states:
                self.data[key] = result.states[key]
            if key in result.attributes:
                self.attributes.setdefault(key, {}).update(result.attributes[key])

        self.last_job = {
            "job": job_name,
            "worker_thread": result.worker_thread,
            "duration_seconds": round(result.duration, 3),
            "finished": dt_util.now().isoformat(timespec="seconds"),
            "error": str(result.error) if result.error else None,
        }
        self.attributes.setdefault("current_charging_rule", {}).update(
            {
                "last_job": job_name,
                "worker_thread": result.worker_thread,
                "calculation_seconds": round(result.duration, 3),
            }
        )
        keys.add("current_charging_rule")

        if result.solar_kwh_increment:
            self.solar_kwh_total = round((self.solar_kwh_total or 0.0) + result.solar_kwh_increment, 3)
            keys.add("kwh_charged_by_solar")

        for key in keys:
            async_dispatcher_send(self.hass, self.signal, key)

        for message, title, notification_id in result.notifications:
            persistent_notification.async_create(self.hass, message, title=title, notification_id=notification_id)

        for name, encoded in result.db_saves.items():
            try:
                await self.storage.async_save(name, encoded)
                _LOGGER.debug("Saved database %s", name)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Could not save BattMind database %s: %s", name, err)

        if result.request_reload:
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    # ------------------------------------------------------------------ #
    # Worker jobs
    # ------------------------------------------------------------------ #

    async def async_run_job(
        self, job_name: str, *args, include_prices: bool = False, include_forecast: bool = False, publish: bool = True,
        **job_kwargs
    ) -> JobResult:
        """Collect a snapshot on the event loop and run the job in the worker thread.

        job_kwargs are passed to the job but not kept in the debug export (e.g. callbacks).
        publish=False is for a job that only reads (a question to the live engine): its result is returned, but not
        published (no sensor, no last_job) and not recorded for the debug export.
        """
        async with self._job_lock:
            cancel_check = job_kwargs.get("cancel_check")
            if cancel_check:
                cancel_check()
            snapshot = await self._async_snapshot(include_prices=include_prices, include_forecast=include_forecast)
            if cancel_check:
                cancel_check()
            func = getattr(self.engine, job_name)
            result: JobResult = await self.hass.loop.run_in_executor(
                self._executor, partial(self.engine.run_job, snapshot, func, *args, **job_kwargs)
            )
            if cancel_check:
                cancel_check()
            if publish:
                await self._async_publish(result, job_name)
                self.debug.record(job_name, args, snapshot, result, self.data.get("current_charging_rule"))
            elif result.db_saves or result.states or result.attributes or result.notifications or result.request_reload:
                # a job that only reads must not have anything to publish: it would be lost without a trace
                _LOGGER.error("Job %s was run without publishing but returned output that is discarded", job_name)
            _LOGGER.debug("Job %s ran in %s for %.3fs", job_name, result.worker_thread, result.duration)
            return result

    def _local_now(self) -> datetime.datetime:
        return dt_util.now().replace(tzinfo=None, microsecond=0)

    # ------------------------------------------------------------------ #
    # Recorder buffers
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Services
    # ------------------------------------------------------------------ #

    async def async_what_if(
        self,
        overrides: dict,
        history_days: int,
        progress: Callable[[dict], None] | None = None,
        capacity_range: list[float] | None = None,
        cancel_check: Callable[[], None] | None = None,
        module_comparison: dict | None = None,
    ) -> JobResult:
        """Load hourly recorder statistics and run an economic historical comparison.

        progress (optional) is called on the event loop while the history is loaded, with
        {"phase", "seconds"}, and then while the simulation runs (see WhatIf.what_if()).
        """
        if "recorder" not in self.hass.config.components:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="what_if_no_recorder")
        cancel_check = self._what_if_cancel_check(cancel_check)
        # in PRICE_PROVIDER_PRIORITY order: the what-if uses the first provider with price
        # history for each hour and falls back to the next one
        provider = self.price_provider
        price_providers = provider.history_sources(self.hass.states.get) if provider else []
        if not price_providers:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="what_if_no_buy_price")
        entities = {
            entity_id
            for entity_id in (
                *self.statistics_entities,
                *(
                    entity_id
                    for provider in price_providers
                    for entity_id in (
                        provider["buy_entity_id"],
                        provider["sell_entity_id"],
                        *provider.get("sell_tariff_entity_ids", {}).values(),
                    )
                ),
            )
            if entity_id
        }
        end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        start = end - datetime.timedelta(days=history_days)
        # the loading phase is reported when it changes and then every PROGRESS_INTERVAL seconds,
        # until the simulation reports its own progress
        started = time.monotonic()
        phase: list[str | None] = [None]

        @callback
        def report_phase(name: str | None = None) -> None:
            if name is not None:
                phase[0] = name
            if progress is not None and phase[0] is not None:
                progress({"phase": phase[0], "seconds": int(time.monotonic() - started)})

        @callback
        def tick(_now: datetime.datetime) -> None:
            report_phase()

        @callback
        def simulation_progress(info: dict) -> None:
            # the simulation reports its own progress from now on
            if cancel_check:
                try:
                    cancel_check()
                except WhatIfCancelled:
                    return
            phase[0] = None
            progress({**info, "seconds": int(time.monotonic() - started)})

        def report(info: dict) -> None:
            # called in the thread the simulation runs on
            self.hass.loop.call_soon_threadsafe(simulation_progress, info)

        cancel_ticker = async_track_time_interval(self.hass, tick, datetime.timedelta(seconds=PROGRESS_INTERVAL))
        try:
            return await self._async_what_if_run(
                overrides, history_days, start, end, entities, price_providers, report_phase,
                report if progress else None, capacity_range, cancel_check, module_comparison,
            )
        finally:
            cancel_ticker()

    async def _async_what_if_run(
        self,
        overrides: dict,
        history_days: int,
        start: datetime.datetime,
        end: datetime.datetime,
        entities: set[str],
        price_providers: list[dict],
        report_phase: Callable[[str], None],
        report: Callable[[dict], None] | None,
        capacity_range: list[float] | None = None,
        cancel_check: Callable[[], None] | None = None,
        module_comparison: dict | None = None,
    ) -> JobResult:
        """Read the Recorder history of the what-if comparison and run it on the what-if thread."""
        from homeassistant.components.recorder import get_instance, history
        from homeassistant.components.recorder.statistics import statistics_during_period

        try:
            if cancel_check:
                cancel_check()
            recorder = get_instance(self.hass)
            report_phase("statistics")
            stats = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                entities,
                "hour",
                None,
                {"mean", "state"},
            )
            if cancel_check:
                cancel_check()
            report_phase("states")
            states = await recorder.async_add_executor_job(
                partial(
                    history.get_significant_states,
                    self.hass,
                    start,
                    end,
                    sorted(entities),
                    None,
                    include_start_time_state=True,
                    significant_changes_only=False,
                    minimal_response=False,
                    no_attributes=True,
                )
            )
            if cancel_check:
                cancel_check()
            eds_sell_entities = {
                provider["sell_entity_id"]
                for provider in price_providers
                if provider["name"] == "Energi Data Service"
                and provider["sell_entity_id"]
                and provider["sell_includes_tariffs"]
            }
            if eds_sell_entities:
                report_phase("tariffs")
            tariff_states = (
                await recorder.async_add_executor_job(
                    partial(
                        history.get_significant_states,
                        self.hass,
                        start,
                        end,
                        sorted(eds_sell_entities),
                        None,
                        include_start_time_state=True,
                        significant_changes_only=False,
                        minimal_response=False,
                        no_attributes=False,
                    )
                )
                if eds_sell_entities
                else {}
            )
            if cancel_check:
                cancel_check()
        except WhatIfCancelled:
            raise
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="what_if_history_failed", translation_placeholders={"error": str(err)}) from err
        # the raw history of a year can be large: it is converted outside the event loop
        report_phase("hourly")
        combined_stats, statistical_timestamps, tariff_history = await self.hass.async_add_executor_job(
            self._what_if_history, entities, stats, states, tariff_states, start, end
        )
        # a price sensor that gives the price per MWh (Energy Price Forecast): its history is per kWh like the live prices; a sensor in the wrong unit has none
        for entity_id, divisor in (self.price_provider.price_unit_divisors(self.hass.states.get) if self.price_provider else {}).items():
            if entity_id in combined_stats:
                # (no divisor: the sensor is not in Home Assistant, its unit is unknown and its history is left out)
                combined_stats[entity_id] = [] if divisor is None else [(timestamp, value / divisor) for timestamp, value in combined_stats[entity_id]]
        if cancel_check:
            cancel_check()
        for provider in price_providers:
            if provider["name"] == "Energi Data Service" and provider["sell_entity_id"]:
                provider["sell_tariff_history"] = tariff_history.get(provider["sell_entity_id"], [])
        data = {
            "price_providers": price_providers,
            "stats": combined_stats,
            "long_term_statistics_timestamps": statistical_timestamps,
            "requested_period": {"start": start.timestamp(), "end": end.timestamp(), "days": history_days},
        }
        report_phase("preparing")
        job_kwargs = {"progress": report}
        if capacity_range is not None:
            job_kwargs["capacity_range"] = capacity_range
        if module_comparison is not None:
            job_kwargs["module_comparison"] = module_comparison
        if cancel_check:
            job_kwargs["cancel_check"] = cancel_check
        return await self._async_run_what_if_job(data, overrides, **job_kwargs)

    def _what_if_cancel_check(self, cancel_check: Callable[[], None] | None) -> Callable[[], None]:
        """cancel_check of a run, extended with the stop of Home Assistant (safe to call from the what-if thread)."""
        def check() -> None:
            if self._what_if_stopping.is_set():
                raise WhatIfCancelled()
            if cancel_check is not None:
                cancel_check()

        return check

    async def _async_run_what_if_job(self, data: dict, overrides: dict, **job_kwargs) -> JobResult:
        """Run the simulation on its own engine and thread; the live worker is only used for a short job.

        The live engine hands over the values the simulation needs from its databases (what_if_live_inputs) in a short
        job on its worker. The simulation then runs on a new engine with a copy of the configuration, in the what-if
        thread, without _job_lock: the live engine's per-job state (snapshot, caches) is never shared, and nothing it
        returns is published or saved (the databases of that engine are empty and are never marked as changed).
        """
        cancel_check = job_kwargs.get("cancel_check")
        if cancel_check:
            cancel_check()
        # a question to the live engine: nothing of it is published or recorded
        live = await self.async_run_job("what_if_live_inputs", publish=False)
        if live.error:
            # like any failed job: the caller sees it as the error of the result
            return live
        if cancel_check:
            cancel_check()
        engine = BattMindEngine(deepcopy(self.config), self.prefix, self.texts)
        result: JobResult = await self.hass.loop.run_in_executor(
            self._what_if_executor,
            partial(engine.run_job, self._what_if_snapshot(), engine.what_if, data, overrides, live=live.value, **job_kwargs),
        )
        if cancel_check:
            cancel_check()
        _LOGGER.debug("The what-if ran in %s for %.3fs", result.worker_thread, result.duration)
        return result

    def _what_if_snapshot(self) -> Snapshot:
        """What the simulation reads from Home Assistant: the time zone, the settings and the units of the statistics."""
        units = {}
        for entity_id in self.statistics_entities:
            state = self.hass.states.get(entity_id)
            units[entity_id] = state.attributes.get("unit_of_measurement") if state else None
        return Snapshot(now=self._local_now(), tz=dt_util.get_default_time_zone(), settings=dict(self.settings), units=units)

    async def async_debug_info(self) -> str:
        extra = {
            "status": {
                "started": self.started,
                "buffered_rows": {e: len(rows) for e, rows in self._buffers.items()},
                "statistics_rows": {e: len(rows) for e, rows in self._statistics.items()},
            },
            "worker": self.last_job,
        }
        result = await self.async_run_job("debug_info", extra)
        return result.value or ""

    async def async_recalc_charging_history_today(self) -> None:
        self.set_charging_rule("📟Recalculating today's charging history")
        await self.async_run_job("recalc_charging_history_today")
