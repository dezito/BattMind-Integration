"""Health checks of a battery entry: repairs for what was set up earlier and has gone wrong since.

HealthWatcher judges the setup of one BattMind entry and raises a repair (issue registry, not fixable, warning) when
something is wrong. A repair goes away by itself once the problem is positively fixed. The watcher only reads: it calls
no service and switches nothing. Its repairs are:

- entities_missing: a configured sensor is disabled, or is in neither the entity registry nor the states
- entity_units: a power or battery level sensor reports a unit BattMind does not read
- power_without_unit: a power sensor without a unit whose values look like kW (value_checks.py), so it is read 1000 times too small
- battery_level_fraction: a battery level read as % whose values look like a share 0..1, so it is read 100 times too small
- price_unit_scale: a price sensor in a subunit of its currency (øre, cent), per MWh or per Wh, so the prices are in another scale (100 or 1000 times too large, or 1000 times too small)
- blueprint_automation_lost: the automation made from the BattMind blueprint was switched on, and is now off or deleted
- plan_not_followed: the battery did not do what the published action asked (engine/analysis/plan_check.py)
- consumption_frozen: the house consumption sensor has shown the same value for FROZEN_AFTER or longer (only that sensor:
  the plan uses it, and a frozen value is not an error the other sensors show). Nothing is deleted or replaced during the
  CONSUMPTION_GRACE after a start.

The value and price repairs only judge: they never convert a value, and what they flag is kept in the store (the values),
so a restart can't clear a repair it can't judge again.

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
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..utils.power import power_unit_known, unique_entity_ids
from .automation_inputs import blueprint_automation_ids
from .dependency_checks import (
    ISSUE_BATTERY_FRACTION,
    ISSUE_BLUEPRINT_LOST,
    ISSUE_CONSUMPTION_FROZEN,
    ISSUE_ENTITIES_MISSING,
    ISSUE_ENTITY_UNITS,
    ISSUE_PLAN_NOT_FOLLOWED,
    ISSUE_POWER_WITHOUT_UNIT,
    ISSUE_PRICE_UNIT,
    NOTHING,
    RECHECK_DELAY,
    _lines,
)
from .value_checks import (
    KW_LOOKING_MAX,
    POWER_SCALE_DAYS,
    VALUE_RECHECK,
    async_recorded_numbers,
    battery_fraction_verdict,
    could_be_fraction,
    could_be_kw,
    number_text,
    power_scale_verdict,
)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
# what the store keeps, because a restart can't learn it again: the blueprint automation was seen in use (the file alone
# can't tell "never used" from "switched off"), and the sensors flagged with a unit, until they are judged OK again
STORE_AUTOMATION_SEEN = "automation_seen"
STORE_UNITS_FLAGGED = "units_flagged"
# the sensors flagged by power_without_unit or battery_level_fraction (entity id -> the repair), until they are judged OK again
STORE_VALUES_FLAGGED = "values_flagged"
# the repairs (issue keys) whose list of flagged sensors is complete: read intact from the store, every sensor judged in one check,
# or no repair in the issue registry when the store had no list
STORE_VALUES_KNOWN = "values_known"
VALUE_ISSUES = (ISSUE_POWER_WITHOUT_UNIT, ISSUE_BATTERY_FRACTION)
# consumption_frozen: the house consumption sensor has shown the same value for this long (its last_changed is that old)
FROZEN_AFTER = datetime.timedelta(hours=6)
# consumption_frozen: no answer is given for this long after a start (a template may still show another value while Home
# Assistant starts). Until it is over, a repair is only kept or re-created from the store, or raised by the FROZEN_AFTER rule
CONSUMPTION_GRACE = datetime.timedelta(minutes=30)
# the value consumption_frozen is about, kept until the value changes: {"entity", "value" (the state as it was), "since" (ISO time)}
STORE_CONSUMPTION_FROZEN = "consumption_frozen"
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


def _utcnow() -> datetime.datetime:
    """The clock of the history reads of the value checks (tests set it to age a read)."""
    return dt_util.utcnow()


def _rounded_up_for_text(value: float) -> float:
    """The value rounded UP to the digits number_text shows (3 significant digits, whole numbers from 1000), so that the shown
    number is never below the value. The small margin keeps a float like 4.2 / 0.01 = 420.00000000000006 from rounding up again."""
    if value <= 0:
        return 0.0
    if value >= 1000:
        return float(math.ceil(value))
    step = 10.0 ** (math.floor(math.log10(value)) - 2)
    return math.ceil(value / step - 1e-9) * step


SAVES_KEY = f"{DOMAIN}_health_saves"


def _pending_saves(hass: HomeAssistant, entry_id: str) -> set[asyncio.Task[None]]:
    """The saves of an entry still being written. Kept in hass.data, so that a removal can wait for them."""
    return hass.data.setdefault(SAVES_KEY, {}).setdefault(entry_id, set())


async def async_remove_health_store(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the store of the health checks of a config entry that was removed. Every save still in progress is waited for
    first (also one started while waiting), so it can't write the file again after it was deleted."""
    current = asyncio.current_task()
    while pending := [task for task in _pending_saves(hass, entry_id) if task is not current and not task.done()]:
        # asyncio.wait, not gather: a removal that is cancelled must not cancel the saves it waits for
        await asyncio.wait(pending)
    try:
        await Store(hass, STORE_VERSION, store_key(entry_id), private=True, atomic_writes=True).async_remove()
    finally:
        hass.data.get(SAVES_KEY, {}).pop(entry_id, None)


def _hour_text(key: str) -> str:
    """The local hour of the plan check ('2026-11-20T14') as it is read: '2026-11-20 14:00'."""
    day, _, hour = key.partition("T")
    return f"{day} {hour}:00" if len(day) == 10 and len(hour) == 2 and hour.isdigit() else key


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _finite_number(value: Any) -> float | None:
    """The value as a finite number, None when it is no number (a state such as "unavailable" or "abc")."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class FrozenValue(NamedTuple):
    """The value consumption_frozen is about: the sensor, the state it shows (as text) and the time that state began (aware)."""

    entity: str
    value: str
    since: datetime.datetime


def _frozen_value(data: Any) -> FrozenValue | None:
    """The saved frozen value, or None when it is missing or not in the shape the check writes (it is then judged anew)."""
    if not isinstance(data, dict):
        return None
    entity, value, since = data.get("entity"), data.get("value"), data.get("since")
    if not isinstance(entity, str) or not entity or not isinstance(value, str) or _finite_number(value) is None:
        return None
    if not isinstance(since, str):
        return None
    moment = dt_util.parse_datetime(since)
    if moment is None or moment.tzinfo is None:
        return None
    return FrozenValue(entity, value, moment)


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
        price_units: Callable[[], tuple[list[tuple[str, str]], bool] | None] | None = None,
    ) -> None:
        """config: the BattMind configuration (build_config); entities: the configured entities of the entry;
        covered: the sensors the Strømligning and Solcast repairs already judge (called at each check); text: the
        translation of a key from texts/<language>.json (coordinator.t), for the words in the repair texts; price_units:
        the price entities in a unit the prices are not read in, as (problems, all judged) (CombinedPriceProvider
        .price_unit_problems); None when there is no price provider yet, so nothing is judged."""
        self.hass = hass
        self.entry = entry
        self.prefix = prefix
        self.action_sensor = f"sensor.{prefix}_powerwall_action"
        self._covered = covered
        self._text = text
        self._price_units = price_units
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
        # the house consumption sensor of consumption_frozen ("" when none is configured)
        self._consumption: str = home["power_consumption_entity_id"] or ""
        # consumption_frozen: the value the repair is about, kept until it changes (persisted)
        self._frozen: FrozenValue | None = None
        # when the watcher started (Home Assistant had started); the grace ends at the first judged check at least
        # CONSUMPTION_GRACE later, and until then the listener of the sensor does nothing (see _grace_passed)
        self._grace_started: datetime.datetime | None = None
        self._grace_over = False
        self._store = Store(hass, STORE_VERSION, store_key(entry.entry_id), private=True, atomic_writes=True)
        # persisted: the blueprint automation was seen in use, and the sensors flagged with a unit (entity id -> unit)
        self._automation_seen = False
        self._unit_flagged: dict[str, str] = {}
        # False until a list was written: without one (the store is gone) the unit repair is only deleted when every sensor is judged
        self._unit_list_known = False
        # power_without_unit and battery_level_fraction: the flagged sensors (persisted, entity id -> repair) and the repairs
        # whose list is known (persisted, per repair: complete when read intact from the store or when every sensor was judged in
        # one check), and in memory the last history read of each sensor, its verdict and the highest absolute value it showed
        self._value_flagged: dict[str, str] = {}
        self._value_known: set[str] = set()
        self._value_read: dict[str, datetime.datetime] = {}
        self._value_verdict: dict[str, bool | None] = {}
        self._value_max: dict[str, float] = {}
        # True after a failed history read (logged as a warning once, then at debug until a read works again)
        self._value_read_failed = False
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
        values = data.get(STORE_VALUES_FLAGGED)
        if isinstance(values, dict):
            self._value_flagged = {str(entity_id): issue for entity_id, issue in values.items() if issue in VALUE_ISSUES}
            # a list is known only when the store was read intact: no entry dropped as malformed, and the known list present
            known = data.get(STORE_VALUES_KNOWN)
            if len(self._value_flagged) == len(values) and isinstance(known, list):
                self._value_known = {issue for issue in known if issue in VALUE_ISSUES}
        # a malformed entry is None: with the repair in the registry it is then saved anew from the value seen first
        self._frozen = _frozen_value(data.get(STORE_CONSUMPTION_FROZEN))

    @callback
    def start(self) -> None:
        """Start the checks. The first one runs once Home Assistant has started."""
        if (unsub := async_at_started(self.hass, self._on_started)) is not None:
            self._unsubs.append(unsub)
        self._unsubs.append(self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed, event_filter=self._registry_filter))
        self._unsubs.append(self.hass.bus.async_listen(EVENT_AUTOMATION_RELOADED, self._automation_reloaded))
        self._unsubs.append(async_track_time_interval(self.hass, self._hourly, RECHECK_INTERVAL))
        self._unsubs.append(async_track_state_change_event(self.hass, [self.action_sensor], self._action_changed))
        if self._consumption:
            self._unsubs.append(async_track_state_change_event(self.hass, [self._consumption], self._consumption_changed))

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
        self._grace_started = _utcnow()
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
            ("power_without_unit", self._check_power_scale),
            ("battery_level_fraction", self._check_battery_scale),
            ("price_unit_scale", self._check_price_units),
            ("plan_not_followed", self._check_plan),
            ("consumption_frozen", self._check_consumption_frozen),
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

    async def _check_power_scale(self) -> None:
        """power_without_unit: the power sensors, judged as kW that is read as W (see _async_check_scale)."""
        await self._async_check_scale(ISSUE_POWER_WITHOUT_UNIT, [entity_id for entity_id, battery in self._unit_entities if not battery])

    async def _check_battery_scale(self) -> None:
        """battery_level_fraction: the battery level, judged as a share 0..1 that is read as a percentage."""
        await self._async_check_scale(ISSUE_BATTERY_FRACTION, [entity_id for entity_id, battery in self._unit_entities if battery])

    async def _async_check_scale(self, issue: str, entities: list[str]) -> None:
        """The verdict of each sensor: True (its values look wrong), False (positively fine) or None (can't tell: it keeps its
        flag). A sensor with a unit is fine for power (the unit is converted) and for the battery level in another unit than
        % (entity_units reports that). A sensor whose state could be in the wrong scale is judged by its history in the
        recorder: one read for all the sensors of the repair, and each sensor at most once per VALUE_RECHECK. The repair is
        raised for the flagged sensors. It is deleted when none is flagged any more and its list is known: read from the store,
        or every sensor of this repair was judged in one check (then an unclear sensor that is not flagged does not keep the
        repair). A list without a stored state and without a repair in the registry starts complete (nothing to forget). The
        list is per repair, so judging one repair's sensors never completes the list of the other. A failed history
        read keeps the verdicts and is not retried before VALUE_RECHECK."""
        battery = issue == ISSUE_BATTERY_FRACTION
        now = _utcnow()
        verdicts: dict[str, bool | None] = {}
        current: dict[str, float] = {}
        needs: list[str] = []
        for entity_id in entities:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNJUDGED_STATES:
                verdicts[entity_id] = None
                continue
            unit = state.attributes.get("unit_of_measurement") or None
            if unit is not None and not (battery and unit == BATTERY_LEVEL_UNIT):
                verdicts[entity_id] = self._judged_fine(entity_id)
                continue
            could = could_be_fraction(state.state) if battery else could_be_kw(state.state)
            if could is True:
                needs.append(entity_id)
                current[entity_id] = float(state.state)
            elif could is False:
                verdicts[entity_id] = self._judged_fine(entity_id)  # above the scale it is read in
            else:
                verdicts[entity_id] = None  # no number
        stale = [entity_id for entity_id in needs if entity_id not in self._value_read or now - self._value_read[entity_id] >= VALUE_RECHECK]
        if stale:
            failed = False
            try:
                numbers = await async_recorded_numbers(self.hass, stale)
            except Exception as err:  # noqa: BLE001 - the verdicts are kept and the read is not retried before VALUE_RECHECK
                failed = True
                self._log_read_failed(err)
                numbers = None
            else:
                self._value_read_failed = False
            for entity_id in stale:
                self._value_read[entity_id] = now
                if failed:
                    continue
                if numbers is None:  # no recorder: nothing to judge by, the sensor keeps its flag
                    self._value_verdict[entity_id] = None
                    continue
                values = [*numbers.get(entity_id, []), current[entity_id]]
                self._value_max[entity_id] = max(abs(value) for value in values)
                self._value_verdict[entity_id] = battery_fraction_verdict(values) if battery else power_scale_verdict(values)
        for entity_id in needs:
            verdicts[entity_id] = self._value_verdict.get(entity_id)

        flagged = dict(self._value_flagged)
        for entity_id in [entity_id for entity_id, found in flagged.items() if found == issue and entity_id not in entities]:
            del flagged[entity_id]  # no longer configured
        unclear = False
        for entity_id in entities:
            verdict = verdicts[entity_id]
            if verdict is True:
                flagged[entity_id] = issue
            elif verdict is False:
                flagged.pop(entity_id, None)
            else:
                unclear = True
        known_before = issue in self._value_known
        # no repair in the issue registry (a fresh start, or none was ever raised): nothing could have been forgotten, so the
        # list starts complete, also when one of its sensors is never judged
        if not known_before and ir.async_get(self.hass).async_get_issue(DOMAIN, self._issue_id(issue)) is None:
            self._value_known.add(issue)
        if not unclear:  # every sensor of this repair was judged: its list is complete
            self._value_known.add(issue)
        if flagged != self._value_flagged or (issue in self._value_known and not known_before):
            self._value_flagged = flagged
            await self._async_save()

        listed = [entity_id for entity_id in sorted(flagged) if flagged[entity_id] == issue]
        if listed:
            language = self.hass.config.language
            lines = []
            for entity_id in listed:
                seen = self._value_max.get(entity_id)  # the highest absolute value of the last history read
                if seen is None:  # never read (or the read failed): a number from the state alone could be below the real value
                    lines.append(entity_id)
                    continue
                current_value = self._current_abs(entity_id)
                if current_value is not None and current_value > seen:
                    # kept until the next history read (which holds this value too), so the number never goes down
                    seen = self._value_max[entity_id] = current_value
                lines.append(f"{entity_id} (≤ {number_text(_rounded_up_for_text(seen), language)})")
            placeholders = {"entities": _lines(lines), "days": str(POWER_SCALE_DAYS)}
            if not battery:
                placeholders["max"] = number_text(KW_LOOKING_MAX, language)
            self._create(issue, placeholders)  # again on each change: async_create_issue keeps the user's Ignore
        elif issue in self._value_known:  # the list is complete (read intact from the store, or every sensor judged in one check)
            self._delete(issue)

    def _current_abs(self, entity_id: str) -> float | None:
        """The absolute state value of a sensor now, None when it is no finite number."""
        state = self.hass.states.get(entity_id)
        try:
            value = abs(float(state.state)) if state is not None else None
        except (TypeError, ValueError):
            return None
        return value if value is not None and math.isfinite(value) else None

    def _judged_fine(self, entity_id: str) -> bool:
        """A sensor whose state is positively fine now: its verdict is False, and its read counts as made now, so an older True
        from the history is not raised again within VALUE_RECHECK."""
        self._value_verdict[entity_id] = False
        self._value_read[entity_id] = _utcnow()
        return False

    def _log_read_failed(self, err: Exception) -> None:
        """A failed history read is a warning the first time and debug after that, until a read works again."""
        if self._value_read_failed:
            _LOGGER.debug("BattMind could not read the history of the power and battery level sensors again: %s", err)
        else:
            self._value_read_failed = True
            _LOGGER.warning("BattMind could not read the history of the power and battery level sensors, retried later: %s", err)

    async def _check_price_units(self) -> None:
        """price_unit_scale: a price sensor in a subunit of its currency (øre, cent), per MWh or per Wh, judged by its unit alone. The
        price provider is asked for it; None (no provider yet) judges nothing, and the repair stays as it is."""
        result = None if self._price_units is None else self._price_units()
        if result is None:
            return
        problems, judged = result
        if problems:
            self._create(ISSUE_PRICE_UNIT, {"entities": _lines([f"{entity_id} ({unit})" for entity_id, unit in problems])})
        elif judged:
            self._delete(ISSUE_PRICE_UNIT)

    async def _check_consumption_frozen(self) -> None:
        """consumption_frozen: the house consumption sensor shows the same value for FROZEN_AFTER or longer (inform only).

        last_changed says how long the value has been the same, and the time of the repair is the earlier of that and the
        saved time. The repair is deleted only on a positive answer: a finite number different from the saved one (at a check,
        or at once by the listener, see _consumption_changed), a sensor that is no longer configured, or a repair that names
        another sensor while the saved value is lost. Unavailable, unknown and text are unclear: the repair and the saved value
        stay as they are.

        During CONSUMPTION_GRACE after the start nothing is deleted and nothing saved is replaced (see _judge_in_grace). The
        configuration answers need no reading, so they wait for the same time: the first check at least CONSUMPTION_GRACE after
        the start is judged by the rules after the grace."""
        before = self._frozen
        self._judge_consumption_frozen()
        if self._frozen != before:
            await self._async_save()

    @callback
    def _consumption_changed(self, event: Event) -> None:
        """A new state of the house consumption sensor: after the grace, a finite number different from the saved one is a
        positive answer for the repair, at once. Other states (unavailable, unknown, text, the saved value again) change
        nothing, and during the grace the listener does nothing at all (Home Assistant may still be starting)."""
        new = event.data.get("new_state")
        if not self._grace_over or self._frozen is None or new is None or new.state in UNJUDGED_STATES:
            return
        value = _finite_number(new.state)
        if value is None or value == _finite_number(self._frozen.value):
            return
        self._forget_frozen()
        # not an entry task: unload cancels those, and the save that forgets the value must still be written. The save is
        # pending until it is written, so the removal of the entry waits for it (async_remove_health_store)
        task = self.hass.async_create_task(self._async_save(), name=f"battmind_{self.prefix}_health_frozen")
        pending = _pending_saves(self.hass, self.entry.entry_id)
        pending.add(task)
        task.add_done_callback(pending.discard)

    def _judge_consumption_frozen(self) -> None:
        now = _utcnow()
        entity = self._consumption
        saved = self._frozen
        exists, named = self._issue_entity()
        # the configuration answers: no sensor, the saved sensor replaced, a repair that names another sensor (saved value lost)
        replaced = saved is not None and saved.entity != entity
        names_other = saved is None and exists and named not in (None, entity)
        if not entity or replaced or names_other:
            if self._grace_passed(now):
                self._forget_frozen()
            return
        state = self.hass.states.get(entity)
        value = _finite_number(state.state) if state is not None and state.state not in UNJUDGED_STATES else None
        if state is None or value is None:
            return  # unclear: the repair and the saved value stay as they are
        if not self._grace_passed(now):
            self._judge_in_grace(entity, state, now, saved, exists, named)
        else:
            self._judge_after_grace(entity, state, now, saved, exists, named, value)

    def _judge_in_grace(
        self, entity: str, state: State, now: datetime.datetime, saved: FrozenValue | None, exists: bool, named: str | None
    ) -> None:
        """During the grace nothing is deleted or replaced. A repair that exists is raised again from the saved values (the
        registry may have lost them; its Ignore is kept). Without a saved value a repair that exists is left as it is: no
        baseline is taken from a lost store before the grace is over. A new repair comes by the FROZEN_AFTER rule only when
        no repair for this sensor exists."""
        if saved is not None:
            if exists:
                self._raise_frozen(saved)
            elif _finite_number(state.state) == _finite_number(saved.value) and now - saved.since >= FROZEN_AFTER:
                self._raise_frozen(saved)
            return
        if exists:
            return
        if now - state.last_changed >= FROZEN_AFTER:
            frozen = FrozenValue(entity, state.state, state.last_changed)
            self._frozen = frozen
            self._raise_frozen(frozen)

    def _judge_after_grace(
        self,
        entity: str,
        state: State,
        now: datetime.datetime,
        saved: FrozenValue | None,
        exists: bool,
        named: str | None,
        value: float,
    ) -> None:
        if saved is not None:
            if value != _finite_number(saved.value):
                self._forget_frozen()  # the value changed: a positive answer
                return
            # the same value as saved; a restart resets last_changed, so the earlier of the two times is kept
            frozen = FrozenValue(entity, saved.value, min(saved.since, state.last_changed))
            self._frozen = frozen
            if (exists and named == entity) or now - frozen.since >= FROZEN_AFTER:
                self._raise_frozen(frozen)
            return
        if exists and named == entity:
            # the repair names this sensor, but its saved value is lost: the value seen now is the new baseline
            frozen = FrozenValue(entity, state.state, state.last_changed)
            self._frozen = frozen
            self._raise_frozen(frozen)
            return
        if now - state.last_changed >= FROZEN_AFTER:
            frozen = FrozenValue(entity, state.state, state.last_changed)
            self._frozen = frozen
            self._raise_frozen(frozen)
            return
        if exists:
            # a repair with unknown placeholders and the value not frozen long enough: baselined, not raised, so that a later
            # change removes it; it is raised at FROZEN_AFTER like any other
            self._frozen = FrozenValue(entity, state.state, state.last_changed)

    def _grace_passed(self, now: datetime.datetime) -> bool:
        """Whether the startup grace of consumption_frozen is over. It ends at the first check at least CONSUMPTION_GRACE after
        the start (that check is judged by the rules after the grace); it is never set back."""
        if not self._grace_over and self._grace_started is not None and now - self._grace_started >= CONSUMPTION_GRACE:
            self._grace_over = True
        return self._grace_over

    def _forget_frozen(self) -> None:
        """A positive answer: delete the repair and the saved value."""
        self._delete(ISSUE_CONSUMPTION_FROZEN)
        self._frozen = None

    def _raise_frozen(self, frozen: FrozenValue) -> None:
        """The repair text: the entity, the value as the language shows numbers, and since when (local time)."""
        self._create(
            ISSUE_CONSUMPTION_FROZEN,
            {
                "entity": frozen.entity,
                "value": number_text(_finite_number(frozen.value) or 0.0, self.hass.config.language),
                "since": dt_util.as_local(frozen.since).strftime("%Y-%m-%d %H:%M"),
            },
        )

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
        """Write the state that a restart can't learn again. A failed write is logged; the next change writes again. The write
        runs as a task of its own, registered in the pending saves of the entry: a check that is cancelled (an unload) does not
        stop it, and a removal of the entry waits for it."""
        pending = _pending_saves(self.hass, self.entry.entry_id)
        task = self.hass.async_create_task(self._write_state(), name=f"battmind_{self.prefix}_health_save")
        pending.add(task)
        task.add_done_callback(pending.discard)
        await asyncio.shield(task)

    async def _write_state(self) -> None:
        data: dict[str, Any] = {}
        if self._automation_seen:
            data[STORE_AUTOMATION_SEEN] = True
        if self._unit_list_known:
            data[STORE_UNITS_FLAGGED] = dict(self._unit_flagged)
        data[STORE_VALUES_FLAGGED] = dict(self._value_flagged)  # always with values_known: without it no list is read as known
        data[STORE_VALUES_KNOWN] = sorted(self._value_known)
        if self._frozen is not None:
            data[STORE_CONSUMPTION_FROZEN] = {
                "entity": self._frozen.entity,
                "value": self._frozen.value,
                "since": self._frozen.since.isoformat(),
            }
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


    def _issue_entity(self) -> tuple[bool, str | None]:
        """Whether the consumption_frozen repair is in the issue registry, and the sensor it names. The sensor is None when the
        repair has no placeholders: the registry restores a non-persistent issue after a restart without them."""
        found = ir.async_get(self.hass).async_get_issue(DOMAIN, self._issue_id(ISSUE_CONSUMPTION_FROZEN))
        if found is None:
            return False, None
        return True, (found.translation_placeholders or {}).get("entity")

    def _set_issue(self, issue: str, found: bool, placeholders: dict[str, str]) -> None:
        if found:
            self._create(issue, placeholders)
        else:
            self._delete(issue)
