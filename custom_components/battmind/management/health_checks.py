"""Health checks of a battery entry: repairs for what was set up earlier and has gone wrong since.

HealthWatcher judges the setup of one BattMind entry and raises a repair (issue registry, not fixable, warning) when
something is wrong. A repair goes away by itself once the problem is positively fixed. The watcher only reads: it calls
no service and switches nothing. Its repairs are:

- entities_missing: a configured sensor is disabled, or is in neither the entity registry nor the states
- entity_units: a power or battery level sensor reports a unit BattMind does not read
- blueprint_automation_lost: the automation made from the BattMind blueprint was switched on, and is now off or deleted
- plan_not_followed: the battery did not do what the published action asked (engine/analysis/plan_check.py)

The sensors that the Strømligning and Solcast repairs (dependency_checks.py) judge are left out of entities_missing.

A check that can't tell leaves a repair as it is: nothing is judged before Home Assistant has started, and a sensor
that is unavailable, a file that can't be read or an automation that is not in the registry yet is not a finding. A
repair that was deleted and is raised again comes back without the user's "ignore", so it is only deleted when the
problem is gone.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any, NamedTuple

from homeassistant.config import AUTOMATION_CONFIG_PATH
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from ..const import DOMAIN
from ..utils.power import power_unit_known, unique_entity_ids
from .automation_inputs import blueprint_automation_ids
from .dependency_checks import (
    ISSUE_BLUEPRINT_LOST,
    ISSUE_ENTITIES_MISSING,
    ISSUE_ENTITY_UNITS,
    ISSUE_PLAN_NOT_FOLLOWED,
    NOTHING,
    RECHECK_DELAY,
    _lines,
)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# what the store keeps, because a restart can't learn it again: the blueprint automation was seen in use (the file alone
# can't tell "never used" from "switched off"), and the sensors flagged with a unit, until they are judged OK again
STORE_AUTOMATION_SEEN = "automation_seen"
STORE_UNITS_FLAGGED = "units_flagged"
# the checks also run this often, so a change that nothing announced is still found
RECHECK_INTERVAL = datetime.timedelta(hours=1)
# plan_not_followed: the plan check (engine/analysis/plan_check.py) must have judged at least this many hours ...
PLAN_MIN_CHECKED_HOURS = 24
# ... and at least this many of them must not match the action the battery was given ...
PLAN_MIN_MISMATCHES = 6
# ... and fewer than this share (percent) of the judged hours may match; above it the battery follows the plan well enough
PLAN_MAX_MATCH_PERCENT = 80
# a sensor in one of these states is not judged: it keeps its previous verdict
UNJUDGED_STATES = ("unavailable", "unknown")
# the battery level is read as a percentage; a unit set to anything else is not read the way BattMind needs
BATTERY_LEVEL_UNIT = "%"
AUTOMATION_ON = "on"
AUTOMATION_OFF = "off"
# homeassistant.components.automation.EVENT_AUTOMATION_RELOADED: the string is kept here, so that the integration does not
# import the automation component (the test for the constant is in tests/test_health_checks.py)
EVENT_AUTOMATION_RELOADED = "automation_reloaded"


def store_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.health"


async def async_remove_health_store(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the store of the health checks of a config entry that was removed."""
    await Store(hass, STORE_VERSION, store_key(entry_id), private=True, atomic_writes=True).async_remove()


def _hour_text(key: str) -> str:
    """The local hour of the plan check ('2026-11-20T14') as it is read: '2026-11-20 14:00'."""
    day, _, hour = key.partition("T")
    return f"{day} {hour}:00" if len(day) == 10 and len(hour) == 2 and hour.isdigit() else key


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class PlanSummary(NamedTuple):
    """The plan check as it is published on the action sensor (engine/analysis/plan_check.py summarize_plan_check)."""

    days: int
    checked: int
    mismatches: int
    percent: float
    latest: list[tuple[str, str, float, float]]  # (hour as text, action key, charged kWh, discharged kWh), the latest first


def _plan_summary(value: Any) -> PlanSummary | None:
    """The published plan check, or None when it is missing or does not have the shape the check publishes."""
    if not isinstance(value, dict):
        return None
    days, checked, matching = value.get("days"), value.get("checked_hours"), value.get("matching_hours")
    percent, latest = value.get("match_percent"), value.get("latest_mismatches")
    if not (_is_count(days) and _is_count(checked) and _is_count(matching)) or matching > checked:
        return None
    if not (percent is None and checked == 0) and not _is_number(percent):
        return None
    if not isinstance(latest, list):
        return None
    rows: list[tuple[str, str, float, float]] = []
    for item in latest:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("hour"), str)
            or not isinstance(item.get("action"), str)
            or not _is_number(item.get("charged_kwh"))
            or not _is_number(item.get("discharged_kwh"))
        ):
            return None
        rows.append((_hour_text(item["hour"]), item["action"], float(item["charged_kwh"]), float(item["discharged_kwh"])))
    return PlanSummary(days, checked, checked - matching, float(percent or 0), rows)


def _unit_ok(unit: Any, battery_level: bool) -> bool:
    """Whether the code reads the unit of the sensor: the power units of utils/power.py, or % for the battery level."""
    if battery_level:
        return not unit or unit == BATTERY_LEVEL_UNIT
    return power_unit_known(unit)


class HealthWatcher:
    """Keeps the health repairs of one battery entry up to date: at start, every hour, after a registry change of a
    watched sensor, when the blueprint automation changes state, and when the plan check is published again."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        prefix: str,
        config: dict[str, Any],
        entities: Iterable[str],
        covered: Callable[[], set[str]],
        text: Callable[[str, str | None], str],
    ) -> None:
        """config: the BattMind configuration (build_config); entities: the configured entities of the entry;
        covered: the sensors the Strømligning and Solcast repairs already judge (called at each check); text: the
        translation of a key from texts/<language>.json (coordinator.t), for the words in the repair texts."""
        self.hass = hass
        self.entry = entry
        self.prefix = prefix
        self.action_sensor = f"sensor.{prefix}_powerwall_action"
        self._covered = covered
        self._text = text
        self.configured = set(entities)
        self._entities = sorted(self.configured)
        home = config["home"]["entity_ids"]
        power = unique_entity_ids(
            [
                home["powerwall_watt_flow_entity_id"],
                home["power_consumption_entity_id"],
                config["solar"]["entity_ids"]["production_entity_id"],
                *home["ignore_consumption_from_entity_ids"],
            ]
        )
        # the sensors BattMind reads with a unit: power (utils/power.py) and the battery level, only those it is configured with
        self._unit_entities = [(entity_id, False) for entity_id in power if entity_id in self.configured]
        level = home["powerwall_battery_level_entity_id"]
        if level in self.configured:
            self._unit_entities.append((level, True))
        self._store = Store(hass, STORE_VERSION, store_key(entry.entry_id), private=True, atomic_writes=True)
        # persisted: the blueprint automation was seen in use, and the sensors flagged with a unit (entity id -> unit)
        self._automation_seen = False
        self._unit_flagged: dict[str, str] = {}
        # False until a list was written: without one (the store is gone) the unit repair is only deleted when every sensor is judged
        self._unit_list_known = False
        # the automation entities of the blueprint found by the last check, and their state listener
        self._automations: frozenset[str] = frozenset()
        self._automation_unsub: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._later: CALLBACK_TYPE | None = None
        self._task: asyncio.Task | None = None
        self._rerun = False
        self._started = False
        self._stopped = False

    async def async_load(self) -> None:
        """Read the store (in the executor, by Store): what a restart can't learn again. A store that can't be read is
        logged and the defaults are kept (no automation seen, no unit list): the safe side, the entry still sets up. The
        next save writes over the old file, so the blueprint repair waits until the automation is seen on again."""
        try:
            data = await self._store.async_load()
        except (HomeAssistantError, OSError) as err:
            _LOGGER.warning("Could not read the BattMind health check store, starting without it: %s", err)
            return
        if not isinstance(data, dict):
            return
        self._automation_seen = data.get(STORE_AUTOMATION_SEEN) is True
        flagged = data.get(STORE_UNITS_FLAGGED)
        if isinstance(flagged, dict):
            self._unit_flagged = {str(entity_id): str(unit) for entity_id, unit in flagged.items()}
            self._unit_list_known = True

    @callback
    def start(self) -> None:
        """Start the checks. The first one runs once Home Assistant has started."""
        if (unsub := async_at_started(self.hass, self._on_started)) is not None:
            self._unsubs.append(unsub)
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed, event_filter=self._registry_filter))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_AUTOMATION_RELOADED, self._automation_reloaded))
        self._unsubs.append(async_track_time_interval(self.hass, self._hourly, RECHECK_INTERVAL))
        self._unsubs.append(async_track_state_change_event(self.hass, [self.action_sensor], self._action_changed))

    @callback
    def stop(self) -> None:
        """Stop watching. The repairs stay: an unload also happens at every reload, and a repair the user chose to ignore
        must not come back. The first check after the next start decides about them."""
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._watch_automations(frozenset())
        if self._later is not None:
            self._later()
            self._later = None
        if self._task is not None and not self._task.done():
            self._task.cancel()

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        self._schedule()

    @callback
    def _schedule(self) -> None:
        """Run a check as a task (the blueprint file is read in the executor). A request during a check runs one more."""
        if self._stopped or not self._started:
            return
        if self._task is not None and not self._task.done():
            self._rerun = True
            return
        self._task = self.entry.async_create_background_task(self.hass, self._async_run(), name=f"battmind_{self.prefix}_health_checks")

    async def _async_run(self) -> None:
        while True:
            self._rerun = False
            await self._async_check()
            if not self._rerun or self._stopped:
                return

    @callback
    def _hourly(self, _now: datetime.datetime) -> None:
        self._schedule()

    @callback
    def _automation_reloaded(self, _event: Event) -> None:
        self._schedule()

    @callback
    def _action_changed(self, event: Event) -> None:
        """Only when the plan check was published again: the other attributes change with every calculation."""
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if new is not None and (old is None or old.attributes.get("plan_check") != new.attributes.get("plan_check")):
            self._schedule()

    @callback
    def _automation_changed(self, event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or old.state != new.state:
            self._schedule()

    @callback
    def _registry_filter(self, data: Mapping[str, Any]) -> bool:
        """A watched sensor or blueprint automation, under its old or its new entity id (a rename changes the id)."""
        watched = self.configured | self._automations
        return data.get("entity_id") in watched or data.get("old_entity_id") in watched

    @callback
    def _registry_changed(self, _event: Event) -> None:
        """Check a minute later: Home Assistant reloads the integration after a sensor was enabled or disabled."""
        if self._later is not None:
            self._later()
        self._later = async_call_later(self.hass, RECHECK_DELAY, self._check_later)

    @callback
    def _check_later(self, _now: datetime.datetime) -> None:
        self._later = None
        self._schedule()

    async def _async_check(self) -> None:
        """Every check runs on its own: one that fails is logged with its name, and the others still run."""
        checks = (
            ("blueprint_automation_lost", self._async_check_blueprint),
            ("entities_missing", self._check_missing),
            ("entity_units", self._check_units),
            ("plan_not_followed", self._check_plan),
        )
        for name, check in checks:
            if self._stopped:
                return
            try:
                await check()
            except Exception:  # noqa: BLE001 - logged with the name of the check; the next check still runs
                _LOGGER.exception("BattMind health check %s failed (%s)", name, self.prefix)

    # ------------------------------------------------------------------ #
    # The repairs
    # ------------------------------------------------------------------ #

    async def _check_missing(self) -> None:
        """entities_missing: disabled, or in neither the entity registry nor the states (a sensor with a state but no
        registry entry, e.g. from YAML without a unique id, is fine)."""
        registry = er.async_get(self.hass)
        covered = self._covered()
        disabled: list[str] = []
        missing: list[str] = []
        for entity_id in self._entities:
            if entity_id in covered:
                continue
            entry = registry.async_get(entity_id)
            if entry is not None:
                if entry.disabled_by:
                    disabled.append(entity_id)
            elif self.hass.states.get(entity_id) is None:
                missing.append(entity_id)
        self._set_issue(ISSUE_ENTITIES_MISSING, bool(disabled or missing), {"disabled": _lines(disabled), "missing": _lines(missing)})

    async def _check_units(self) -> None:
        """entity_units: a power sensor with a unit the power conversion does not read, or a battery level in another unit
        than %. The sensors flagged last time are kept with their unit (in the store). The repair goes when every one of
        them is judged OK again or is no longer configured; a sensor without a state is not judged and keeps its flag.
        Without a stored list (the store is gone) the repair goes only when every sensor is judged and none is flagged."""
        configured = {entity_id for entity_id, _ in self._unit_entities}
        flagged = {entity_id: unit for entity_id, unit in self._unit_flagged.items() if entity_id in configured}
        unjudged = False
        for entity_id, battery_level in self._unit_entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNJUDGED_STATES:
                unjudged = True
                continue
            unit = state.attributes.get("unit_of_measurement") or None
            if _unit_ok(unit, battery_level):
                flagged.pop(entity_id, None)
            else:
                flagged[entity_id] = str(unit)
        if flagged != self._unit_flagged:
            self._unit_flagged = flagged
            self._unit_list_known = True
            await self._async_save()
        if flagged:
            self._create(ISSUE_ENTITY_UNITS, {"entities": _lines([f"{entity_id} ({unit})" for entity_id, unit in sorted(flagged.items())])})
        elif self._unit_list_known or not unjudged:
            self._delete(ISSUE_ENTITY_UNITS)

    async def _check_plan(self) -> None:
        """plan_not_followed: judged on the plan check published on the action sensor. Fewer hours than the minimum, a
        missing or malformed summary, or an unavailable sensor is unclear and leaves the repair as it is."""
        state = self.hass.states.get(self.action_sensor)
        if state is None or state.state in UNJUDGED_STATES:
            return
        summary = _plan_summary(state.attributes.get("plan_check"))
        if summary is None or summary.checked < PLAN_MIN_CHECKED_HOURS:
            return
        found = summary.mismatches >= PLAN_MIN_MISMATCHES and summary.percent < PLAN_MAX_MATCH_PERCENT
        latest = "\n".join(
            f"- {hour}: {self._action_text(action)} – {charged:.2f} / {discharged:.2f} kWh"
            for hour, action, charged, discharged in summary.latest
        )
        self._set_issue(
            ISSUE_PLAN_NOT_FOLLOWED,
            found,
            {
                "days": str(summary.days),
                "checked": str(summary.checked),
                "mismatches": str(summary.mismatches),
                "percent": str(round(summary.percent)),
                "min_checked": str(PLAN_MIN_CHECKED_HOURS),
                "min_mismatches": str(PLAN_MIN_MISMATCHES),
                "max_percent": str(PLAN_MAX_MATCH_PERCENT),
                "latest": latest or NOTHING,
            },
        )

    def _action_text(self, action: str) -> str:
        """The name of the action in the language of the entry (texts/<language>.json); the key itself when there is none."""
        return self._text(f"ui.automation_test.action.{action}", action)

    async def _async_check_blueprint(self) -> None:
        """blueprint_automation_lost: the automations of the blueprint in automations.yaml, mapped to their entities.

        An automation that was on is remembered (persisted). Once it was seen, the repair is raised when every automation
        of the file is off (or the file lists none), and deleted as soon as one is on. An automation that is not in the
        registry, or has no state, is unclear and leaves the repair as it is. An automation disabled in the registry has
        no state at all and is off. Automations kept in other files are not found and never raise the repair."""
        path = self.hass.config.path(AUTOMATION_CONFIG_PATH)
        ids = await self.hass.async_add_executor_job(blueprint_automation_ids, path, self.action_sensor)
        if self._stopped or ids is None:
            return  # the file can't be read: nothing is known
        registry = er.async_get(self.hass)
        entities: set[str] = set()
        running = unclear = False
        off: list[str] = []
        for automation_id in ids:
            entity_id = registry.async_get_entity_id("automation", "automation", automation_id)
            if entity_id is None:
                unclear = True  # not in the registry yet (a new automation or one Home Assistant has not loaded)
                continue
            entities.add(entity_id)
            entry = registry.async_get(entity_id)
            state = self.hass.states.get(entity_id)
            if entry is not None and entry.disabled_by:
                off.append(entry.name or entry.original_name or entity_id)
            elif state is None or state.state not in (AUTOMATION_ON, AUTOMATION_OFF):
                unclear = True
            elif state.state == AUTOMATION_ON:
                running = True
            else:
                off.append(state.attributes.get("friendly_name") or entity_id)
        self._watch_automations(frozenset(entities))
        if running:
            if not self._automation_seen:
                self._automation_seen = True
                await self._async_save()
            self._delete(ISSUE_BLUEPRINT_LOST)
        elif not unclear and self._automation_seen:
            self._create(ISSUE_BLUEPRINT_LOST, {"automations": _lines(off)})

    async def _async_save(self) -> None:
        """Write the state that a restart can't learn again. A failed write is logged; the next change writes again."""
        data: dict[str, Any] = {}
        if self._automation_seen:
            data[STORE_AUTOMATION_SEEN] = True
        if self._unit_list_known:
            data[STORE_UNITS_FLAGGED] = dict(self._unit_flagged)
        try:
            await self._store.async_save(data)
        except (OSError, HomeAssistantError) as err:
            _LOGGER.warning("Could not save the BattMind health check state: %s", err)

    def _watch_automations(self, entities: frozenset[str]) -> None:
        """Listen to the state of the blueprint automations found by the last check (again when the set changes)."""
        if entities == self._automations:
            return
        self._automations = entities
        if self._automation_unsub is not None:
            self._automation_unsub()
            self._automation_unsub = None
        if entities:
            self._automation_unsub = async_track_state_change_event(self.hass, sorted(entities), self._automation_changed)

    # ------------------------------------------------------------------ #
    # Issues
    # ------------------------------------------------------------------ #

    def _issue_id(self, issue: str) -> str:
        return f"{self.entry.entry_id}_{issue}"

    def _create(self, issue: str, placeholders: dict[str, str]) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(issue),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=issue,
            translation_placeholders={"prefix": self.prefix, **placeholders},
        )

    def _delete(self, issue: str) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(issue))

    def _set_issue(self, issue: str, found: bool, placeholders: dict[str, str]) -> None:
        if found:
            self._create(issue, placeholders)
        else:
            self._delete(issue)
