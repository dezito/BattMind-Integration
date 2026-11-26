"""Setup, startup and unload of the coordinator."""
from __future__ import annotations

import asyncio
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from queue import SimpleQueue

from homeassistant.components import persistent_notification
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry, ConfigEntryChange
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, Event, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import translation
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from ..const import DOMAIN, ENTITY_UNAVAILABLE_STATES
from ..engine import BattMindEngine
from ..management.dependency_checks import DependencyWatcher
from ..management.health_checks import HealthWatcher
from ..management.notifier import PushNotifier
from ..prices import stromligning_entities_to_watch
from ..utils.cjp import CJP_DOMAIN, cjp_charger_power_entities
from ..utils.solcast import solcast_endings_for_days
from ..utils.formatting import load_texts
from ..management.backup import async_load_backup, async_restore_databases, pending_restores
from ..management.storage import DB_NAMES, load_legacy_databases

_LOGGER = logging.getLogger(__name__)


class CoordinatorLifecycle:
    """Setup, startup, restore of a backup, file log and unload."""

    async def async_setup(self) -> None:
        # texts for the overview, history, charging rule and notifications (texts/<language>.json,
        # English is the fallback for missing keys) with the currency of Home Assistant
        language = self.language
        self.texts = await self.hass.async_add_executor_job(load_texts, language, self.hass.config.currency)
        # entity names in the same language (HA would name translation_key entities in its own UI
        # language), English is the fallback for missing keys
        translations = await translation.async_get_translations(self.hass, language, "entity", {DOMAIN})
        self.entity_names = {
            key.removeprefix(f"component.{DOMAIN}.entity.").removesuffix(".name"): name
            for key, name in translations.items()
            # component.<domain>.entity.<platform>.<key>.name (not state or state_attributes names)
            if key.startswith(f"component.{DOMAIN}.entity.") and key.endswith(".name") and key.count(".") == 5
        }
        self.engine = BattMindEngine(self.config, self.prefix, self.texts)

        if self.config.get("enable_diagnostic_file_log"):
            await self._async_enable_file_log()
        # Home Assistant stopping does not unload the entries, and the what-if thread (a worker thread of an executor) is
        # waited for when the process exits: a simulation with hours left would hold up the stop or the restart
        self.entry.async_on_unload(self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_ha_stop))
        if self.auto_ignored_entities:
            _LOGGER.info("Leaving the consumption of the Cable Juice Planner chargers out of the house consumption: %s", ", ".join(self.auto_ignored_entities))
        if self.config["home"]["ignore_cjp_chargers"]:
            self.entry.async_on_unload(async_dispatcher_connect(self.hass, SIGNAL_CONFIG_ENTRY_CHANGED, self._on_config_entry_changed))

        # before the health checks and the first job: what was sent is known, so a restart does not send it again
        self.notifier = PushNotifier(self.hass, self.entry.entry_id, self.prefix, self.config, self.t)
        await self.notifier.async_load()
        self.entry.async_on_unload(self.notifier.start())
        await self._async_start_health_checks(self._start_dependency_checks())

        ent_reg = er.async_get(self.hass)
        for entity_id in self.configured_entities:
            entry = ent_reg.async_get(entity_id)
            self._entity_platforms[entity_id] = entry.platform if entry else None

        await self._async_apply_restore()
        self.set_charging_rule(f"📟{self.t('ui.init.script_starting')}")

    def _start_dependency_checks(self) -> DependencyWatcher:
        """Repairs for Strømligning and Solcast sensors BattMind needs but that are disabled or not provided."""
        stromligning = stromligning_entities_to_watch(self.config["prices"]["entity_ids"])
        forecast_entity = self.config["solar"]["entity_ids"]["forecast_entity_id"]
        watcher = DependencyWatcher(
            self.hass,
            self.entry,
            self.prefix,
            stromligning,
            forecast_entity,
            solcast_endings_for_days(int(self.config["days_to_predict"])),
        )
        watcher.start()
        self.entry.async_on_unload(watcher.stop)
        return watcher

    async def _async_start_health_checks(self, dependency: DependencyWatcher) -> None:
        """Health checks of the battery setup (management/health_checks.py). The sensors that the Strømligning and Solcast
        repairs judge are left out of the missing-sensor check."""
        watcher = HealthWatcher(
            self.hass,
            self.entry,
            self.prefix,
            self.config,
            self.configured_entities,
            dependency.covered_entities,
            self.t,
            price_units=self._price_unit_problems,
            notifier=self.notifier,
        )
        await watcher.async_load()
        watcher.start()
        self.entry.async_on_unload(watcher.stop)

    def _price_unit_problems(self) -> tuple[list[tuple[str, str]], bool] | None:
        """The price entities in a unit the prices are not read in (CombinedPriceProvider.price_unit_problems). None before
        the engine has a price provider: nothing is judged then, so a repair stays as it is."""
        provider = self.price_provider
        return None if provider is None else provider.price_unit_problems(self.hass.states.get)

    @callback
    def _on_config_entry_changed(self, change: ConfigEntryChange, entry: ConfigEntry) -> None:
        """A CJP car was added, removed, enabled, disabled, got another charger or had its charger moved into or out of the
        house meter (CJP option charger_in_house_meter): start again with the new lists.

        The signal also comes for every state change of a CJP entry (loading, reloading), so the chargers are
        compared with the ones this start used (the subtracted ones and every charger, which is checked for a hung
        reading); only a real difference reloads, and only once. Only listened to while ignore_cjp_chargers is on: with it
        off nothing is subtracted, and the list of every charger is read again at the next start.
        """
        if entry.domain != CJP_DOMAIN or self._cjp_reload_scheduled:
            return
        subtracted = cjp_charger_power_entities(self.hass)
        every_charger = cjp_charger_power_entities(self.hass, include_own_meter=True)
        if subtracted == self.cjp_chargers and every_charger == self.config["home"]["charger_entity_ids"]:
            return
        self._cjp_reload_scheduled = True
        _LOGGER.info("The chargers of Cable Juice Planner changed, reloading BattMind to count their consumption right")
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    async def _async_apply_restore(self) -> None:
        """Databases and settings of a backup imported or chosen in the config flow (management/backup.py)."""
        name = pending_restores(self.hass).pop(self.entry.entry_id, None)
        if name is None and (name := self.entry.data.get("restore_backup")):
            # only once, the restored databases are saved from now on
            data = {key: value for key, value in self.entry.data.items() if key != "restore_backup"}
            self.hass.config_entries.async_update_entry(self.entry, data=data)
        if not name:
            return
        try:
            backup = await async_load_backup(self.hass, name)
        except HomeAssistantError as err:
            _LOGGER.error("Could not restore the BattMind backup: %s", err)
            return
        restored = await async_restore_databases(self.storage, backup)
        self.restore_settings = dict(backup.get("settings") or {})
        _LOGGER.info(
            "Restored backup %s: databases %s, %s settings", name, ", ".join(restored) or "none", len(self.restore_settings)
        )
        persistent_notification.async_create(
            self.hass,
            self.t("ui.backup.imported", file=name, databases=", ".join(restored) or "-"),
            title=f"📟BattMind ({self.prefix})",
            notification_id=f"{self.prefix}_backup",
        )

    @callback
    def async_start(self) -> None:
        """Start once the platforms (and the restored settings) are set up."""
        if self.hass.state is CoreState.running:
            self._start_background()
        else:
            self._started_unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, self._start_background)

    @callback
    def stop_what_ifs(self) -> None:
        """Every running what-if (also one started by the action) stops at its next check, and no new one is started."""
        self._what_if_stopping.set()
        self.what_if_store.stop()

    @callback
    def _on_ha_stop(self, _event: Event) -> None:
        """Home Assistant is stopping (the entries are not unloaded): stop the what-ifs and a test of the automation."""
        self.stop_what_ifs()
        self.async_stop_automation_test()

    @callback
    def _start_background(self, _event: Event | None = None) -> None:
        self._started_unsub = None
        self.entry.async_create_background_task(self.hass, self._async_startup(), name=f"battmind_{self.prefix}_startup")

    async def async_unload(self) -> None:
        if self._started_unsub:
            self._started_unsub()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._settings_debounce:
            self._settings_debounce()
        # a running test of the automation puts BattMind's own action back while the worker still runs
        await self.async_unload_automation_test()
        if self._charge_task and not self._charge_task.done():
            self._charge_task.cancel()
        self._what_if_executor.shutdown(wait=False, cancel_futures=True)
        self._executor.shutdown(wait=False, cancel_futures=True)
        await self._async_disable_file_log()
        if self.notifier is not None:
            await self.notifier.async_shutdown()

    async def _async_enable_file_log(self) -> None:
        log_path = self.hass.config.path(f"battmind_{self.prefix}.log")

        def _create_handler() -> RotatingFileHandler:
            handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s (%(threadName)s) [%(name)s] %(message)s"))
            return handler

        file_handler = await self.hass.async_add_executor_job(_create_handler)
        queue: SimpleQueue = SimpleQueue()
        self._log_handler = QueueHandler(queue)
        self._log_handler.setLevel(logging.DEBUG)
        self._log_listener = QueueListener(queue, file_handler)
        self._log_listener.start()
        package_logger = logging.getLogger(__package__.rpartition(".")[0])
        package_logger.addHandler(self._log_handler)
        if package_logger.getEffectiveLevel() > logging.DEBUG:
            package_logger.setLevel(logging.DEBUG)

    async def _async_disable_file_log(self) -> None:
        if self._log_handler:
            logging.getLogger(__package__.rpartition(".")[0]).removeHandler(self._log_handler)
            self._log_handler = None
        if self._log_listener:
            listener = self._log_listener
            self._log_listener = None
            await self.hass.async_add_executor_job(listener.stop)

    async def _async_startup(self) -> None:
        # Let every integration's homeassistant_started listener get its turn
        # before the first calculation reads their entity states.
        await asyncio.sleep(0)
        t = self.t
        log_lines: list[str] = []
        job_error: BaseException | None = None
        _LOGGER.info("BattMind %s starting, calculations run in thread battmind_%s_worker_0", self.prefix, self.prefix)
        try:
            await self._async_validate_entities()

            self.set_charging_rule(f"📟{t('ui.startup.loading_db')}")
            databases = await self.storage.async_load()
            missing = [name for name in DB_NAMES if databases.get(name) is None]
            if missing and self.legacy_prefix:
                legacy = await self.hass.async_add_executor_job(
                    load_legacy_databases, self.hass.config.config_dir, self.legacy_prefix
                )
                imported = [name for name in missing if name in legacy]
                for name in imported:
                    databases[name] = legacy[name]
                if imported:
                    log_lines.append(f"📟Importeret eksisterende databaser: {', '.join(sorted(imported))}")

            self.set_charging_rule(f"📟{t('ui.startup.loading_history')}")
            await self._async_setup_buffers()
            _LOGGER.info(
                "History buffers: %s",
                ", ".join(f"{entity_id} {len(rows)} states" for entity_id, rows in self._buffers.items()) or "none",
            )

            result = await self.async_run_job("startup", databases, include_prices=True)
            info = result.value or {}
            if result.error:
                # run_job caught it: the start is not a success even though nothing was raised here
                job_error = result.error
                _LOGGER.error("BattMind startup job failed: %s %s", job_error, type(job_error))
                log_lines.append(f"⛔Error during startup: {job_error}")
            solar = t("ui.startup.configuration.configured") if info.get("solar_configured") else t("ui.startup.configuration.not_configured")
            powerwall = t("ui.startup.configuration.configured") if info.get("powerwall_configured") else t("ui.startup.configuration.not_configured")
            log_lines[:0] = [
                "---",
                f"### BattMind🤵‍♂️🔋⚡ ({self.prefix})",
                "",
                f"**{t('ui.startup.configuration.header')}:**",
                f"📟{t('ui.startup.configuration.solar')}: {solar}",
                f"📟{t('ui.startup.configuration.powerwall')}: {powerwall}",
                "",
                f"📟{t('ui.startup.calc_charging_plan')}",
            ]
            self.set_charging_rule(f"📟{t('ui.startup.calc_charging_plan')}")
            # before the first plan, so a new installation plans with its consumption right away
            await self._async_power_values_backfill()

            self._register_triggers()
            self.started = True
            await self._async_charge_if_needed(force=True)
            await self.async_run_job("charging_history", self._local_now().replace(minute=0, second=0))
            if not self.engine.analysis_backfilled():
                self.entry.async_create_background_task(
                    self.hass, self._async_analysis_backfill(), name=f"battmind_{self.prefix}_analysis_backfill"
                )
            if not self.engine.efficiency_backfilled():
                self.entry.async_create_background_task(
                    self.hass, self._async_efficiency_backfill(), name=f"battmind_{self.prefix}_efficiency_backfill"
                )
            if self.last_job.get("worker_thread"):
                log_lines.append(f"📟Worker: {self.last_job['worker_thread']}")
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Error during BattMind startup")
            log_lines.append(f"⛔Error during startup: {err}")
            self.set_charging_rule(f"⛔{t('ui.init.script_stopped')}.\n{t('ui.init.check_log')}:\n{err}")
            self._startup_failed_notification(log_lines, str(err))
        else:
            if job_error:
                self.set_charging_rule(f"⛔{t('ui.init.script_stopped')}.\n{t('ui.init.check_log')}:\n{job_error}")
                self._startup_failed_notification(log_lines, str(job_error))
            else:
                # a successful start only goes to the log
                persistent_notification.async_dismiss(self.hass, f"{self.prefix}_startup_notification")
                self.notice("startup_failed", False)
        finally:
            for line in log_lines:
                if line.strip("-# "):
                    _LOGGER.info(line.lstrip("#* ").replace("**", ""))

    def _startup_failed_notification(self, log_lines: list[str], error: str = "") -> None:
        """A failed start stays visible until the next start (a successful one only goes to the log), and is pushed."""
        self.notice("startup_failed", True, error=error[:300])
        persistent_notification.async_create(
            self.hass,
            "\n".join(log_lines),
            title=f"⛔BattMind ({self.prefix}) {self.t('ui.init.script_stopped')}",
            notification_id=f"{self.prefix}_startup_notification",
        )

    async def _async_validate_entities(self) -> None:
        t = self.t
        self.set_charging_rule(f"📟{t('ui.validation_procedure.entity_validation')}")
        entities = self.configured_entities
        missing = [e for e in entities if self.hass.states.get(e) is None]
        if missing:
            _LOGGER.warning("Entities not found, retrying in 2 minutes: %s", ", ".join(missing))
            self.set_charging_rule(f"📟{t('ui.validation_procedure.entity_missing_try_again')}")
            await asyncio.sleep(120)
            missing = [e for e in entities if self.hass.states.get(e) is None]
            if missing:
                persistent_notification.async_create(
                    self.hass,
                    "- " + "\n- ".join(missing) + f"\n\n{t('ui.validation_procedure.check_entity_ids')}",
                    title=f"BattMind ({self.prefix}) missing entity ids",
                    notification_id=f"{self.prefix}_validation_procedure_missing_entity_ids",
                )

        def _unavailable() -> list[str]:
            return [
                e for e in entities
                if (state := self.hass.states.get(e)) is not None and state.state in ENTITY_UNAVAILABLE_STATES
            ]

        unavailable = _unavailable()
        if not missing and not unavailable:
            _LOGGER.info("All %s configured entities are present and available", len(entities))
        if unavailable:
            _LOGGER.warning("Entities unavailable, waiting up to 2 minutes: %s", ", ".join(unavailable))
            self.set_charging_rule(f"📟{t('ui.validation_procedure.entity_unavailable_try_again')}")
            for _ in range(12):
                await asyncio.sleep(10)
                unavailable = _unavailable()
                if not unavailable:
                    break
            if unavailable:
                persistent_notification.async_create(
                    self.hass,
                    "- " + "\n- ".join(unavailable) + f"\n\n{t('ui.validation_procedure.check_entity_ids')}",
                    title=f"BattMind ({self.prefix}) {t('ui.validation_procedure.unavailable_entity_ids_title')}",
                    notification_id=f"{self.prefix}_validation_procedure_unavailable_entity_ids",
                )
