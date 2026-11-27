"""Push notifications to the notify services the user chose (option notify_list).

BattMind only informs; nothing here controls the battery or the inverter. A push goes out when something CHANGES, never on a timer:

- a condition (report): a problem that starts and, when it is over, "resolved". It has three answers: True (active), False (over,
  an explicit answer) and None (not evaluated this time: nothing changes). The push goes after the condition has held for the
  debounce time of its kind (DEBOUNCE), so a value that flaps around a limit sends one push, not ten. "Resolved" replaces the
  problem on the phone (same tag) and goes after the same kind of wait.
- a one time event (report_ready): "learned" kinds, sent once when something that was not ready becomes ready. What is already
  ready when the notifier first sees it is only noted, so an update never sends "ready" for something that has been ready for weeks.
- the daily summary (report_summary): once per date.

A push counts as sent only when a service took it. A service that is not there yet (the mobile app registers its notify service
after Home Assistant started) leaves the push pending, and it is tried again at the next call of tick(). What was sent is kept in
a store of its own (battmind.<entry_id>.notices), so a restart never sends it again, and what is kept is only a few keys and dates.
The store is deliberately not part of the BattMind backup or the debug export (like the health store).
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from collections.abc import Callable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
SAVE_DELAY = 1.0
SEND_TIMEOUT = 15.0
RETRY_AFTER = 300.0  # seconds before a push that could not be delivered is tried again

# kind -> category (the option notify_<category> switches the category on or off)
CATEGORIES: dict[str, str] = {
    "battery_level_lost": "problems",
    "plan_not_followed": "problems",
    "entities_missing": "problems",
    "consumption_frozen": "problems",
    "sensors_unavailable": "problems",
    "temperature_stop": "problems",
    "startup_failed": "problems",
    "job_failing": "problems",
    "history_failing": "problems",
    "storm": "storm",
    "learning_consumption": "learning",
    "learning_efficiency": "learning",
    "learning_capacity": "learning",
    "daily_summary": "summary",
}
# seconds a condition must hold before "problem" goes, and before "resolved" goes
DEBOUNCE: dict[str, tuple[float, float]] = {
    "battery_level_lost": (0.0, 300.0),  # (the level was already lost for BATTERY_LEVEL_MAX_AGE before it counts)
    "temperature_stop": (900.0, 900.0),
    "job_failing": (0.0, 600.0),
    "sensors_unavailable": (0.0, 600.0),
    "storm": (0.0, 1800.0),  # (a warning entity that says "off" for a moment is not the end of the storm)
}
RETRY_EVERY = datetime.timedelta(minutes=1)  # how often what is pending is looked at (RETRY_AFTER keeps a failed push waiting)
DEFAULT_DEBOUNCE = (0.0, 0.0)
CATEGORY_OPTIONS = {"problems": "notify_problems", "storm": "notify_storm", "learning": "notify_learning", "summary": "notify_summary"}


def store_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}.notices"


def clean_state(data: Any) -> dict:
    """The stored state as {"active": {kind: since}, "once": [kinds], "seen": [kinds], "summary": date|None}; a malformed
    value is dropped, so a broken file never stops a push (the worst case is one push again)."""
    data = data if isinstance(data, dict) else {}
    active = data.get("active")
    return {
        "active": {
            kind: float(since)
            for kind, since in (active.items() if isinstance(active, dict) else [])
            if kind in CATEGORIES and isinstance(since, (int, float)) and not isinstance(since, bool)
        },
        "once": sorted({kind for kind in (data.get("once") if isinstance(data.get("once"), list) else []) if kind in CATEGORIES}),
        "seen": sorted({kind for kind in (data.get("seen") if isinstance(data.get("seen"), list) else []) if kind in CATEGORIES}),
        "summary": data["summary"] if isinstance(data.get("summary"), str) else None,
    }


async def async_remove_notices_store(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the store of a config entry that was removed."""
    await Store(hass, STORE_VERSION, store_key(entry_id), private=True, atomic_writes=True).async_remove()


class PushNotifier:
    """The push notifications of one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        prefix: str,
        options: dict[str, Any],
        translate: Callable[..., str],
        now: Callable[[], float] = time.time,
    ) -> None:
        self._hass = hass
        self._prefix = prefix
        self._t = translate
        self._now = now
        chosen = options.get("notify_list") or []
        self._targets: list[str] = [
            str(name).removeprefix("notify.") for name in ([chosen] if isinstance(chosen, str) else chosen) if str(name).strip()
        ]
        self._enabled = {category: bool(options.get(option)) for category, option in CATEGORY_OPTIONS.items()}
        self._store: Store = Store(hass, STORE_VERSION, store_key(entry_id), private=True, atomic_writes=True)
        self.state: dict = clean_state(None)
        # kind -> {"phase" (problem, resolved, once or summary), "since", "params"}: what is owed and not yet delivered
        self._pending: dict[str, dict] = {}
        # kind -> the last True/False a source reported with its parameters (what a delivery that was on its way missed)
        self._last: dict[str, tuple[bool, dict]] = {}
        self._retry_at: dict[str, float] = {}
        self._busy: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._unsub_timer: CALLBACK_TYPE | None = None

    # ------------------------------------------------------------------ #
    # Store
    # ------------------------------------------------------------------ #

    async def async_load(self) -> None:
        """Load what was sent. Before the first job publishes, so a restart does not send an active problem again."""
        try:
            self.state = clean_state(await self._store.async_load())
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Could not read the push notification state of %s, starting without it", self._prefix)
            self.state = clean_state(None)
        # a problem that was sent while the category is off (or no service is chosen) now would never get its "resolved", and a real one
        # of the same kind would never be sent after it: it is let go
        self.state["active"] = {kind: since for kind, since in self.state["active"].items() if self._on(kind)}

    def _save(self) -> None:
        if not self._closed:
            self._store.async_delay_save(lambda: self.state, SAVE_DELAY)

    def start(self) -> CALLBACK_TYPE:
        """Look at what is pending every minute, whether a job runs or not (a start that failed runs none). Returns the function that
        stops the timer: the caller registers it on the entry (async_on_unload), which Home Assistant also runs when a setup fails."""
        self._unsub_timer = async_track_time_interval(self._hass, self._on_timer, RETRY_EVERY)
        return self._stop_timer

    @callback
    def _stop_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _on_timer(self, _now: datetime.datetime) -> None:
        self.tick()

    async def async_shutdown(self) -> None:
        """Let the pushes that are on their way finish (a reload that a failing job asks for must not cut off the push that says
        so), cancel what is still not done after SEND_TIMEOUT, and write the state. Nothing is reported or saved afterwards."""
        self._closed = True
        self._stop_timer()
        tasks = list(self._tasks)
        if tasks:
            _, unfinished = await asyncio.wait(tasks, timeout=SEND_TIMEOUT)
            for task in unfinished:
                task.cancel()
            if unfinished:
                await asyncio.wait(unfinished)
        await self._store.async_save(self.state)

    # ------------------------------------------------------------------ #
    # Reports from the sources
    # ------------------------------------------------------------------ #

    def _on(self, kind: str) -> bool:
        """Whether a push of this kind may go: a notify service is chosen and its category is on."""
        return bool(self._targets) and self._enabled.get(CATEGORIES[kind], False)

    def report(self, kind: str, active: bool | None, **params: Any) -> None:
        """A condition: True = it holds, False = it is over (an explicit answer), None = not evaluated, nothing changes."""
        if self._closed:
            return
        if not self._on(kind):
            self._pending.pop(kind, None)
            return
        if active is None:
            return
        self._last[kind] = (active, params)
        sent = kind in self.state["active"]
        if active == sent:
            # the push already says it (a problem that was sent and still holds, or one that is over and was never sent)
            self._pending.pop(kind, None)
            return
        phase = "problem" if active else "resolved"
        pending = self._pending.get(kind)
        if pending is None or pending["phase"] != phase:
            pending = self._pending[kind] = {"phase": phase, "since": self._now(), "params": params}
        else:
            pending["params"] = params or pending["params"]
        self._evaluate(kind)

    def report_ready(self, kind: str, ready: bool | None) -> None:
        """Something learned: sent once when it becomes ready after the notifier saw it not ready. None: not clear this time (the
        measurement is under suspicion), so nothing changes: only a real reset (False) makes ready news again."""
        if self._closed or ready is None or not self._on(kind):
            return
        state = self.state
        if not ready:
            self._pending.pop(kind, None)
            changed = kind not in state["seen"] or kind in state["once"]
            if kind not in state["seen"]:
                state["seen"].append(kind)
            if kind in state["once"]:
                state["once"].remove(kind)  # (reset, e.g. a measured capacity that started again: ready again is news)
            if changed:
                self._save()
            return
        if kind in state["once"]:
            return
        if kind in state["seen"]:
            self._pending[kind] = self._pending.get(kind) or {"phase": "once", "since": self._now(), "params": {}}
            self._evaluate(kind)
        else:
            state["once"].append(kind)  # ready when first seen: noted, not announced
            self._save()

    def report_summary(self, date: str, **params: Any) -> None:
        """The daily summary of a date, sent once per date."""
        if self._closed or not self._on("daily_summary") or self.state["summary"] == date:
            return
        pending = self._pending.get("daily_summary")
        if pending is None or pending["params"].get("_date") != date:
            self._pending["daily_summary"] = {"phase": "summary", "since": self._now(), "params": {**params, "_date": date}}
        self._evaluate("daily_summary")

    def tick(self) -> None:
        """Try again what is pending (a service that was not there yet, a debounce time that has passed)."""
        if self._closed:
            return
        for kind in list(self._pending):
            if not self._on(kind):
                self._pending.pop(kind, None)
                continue
            self._evaluate(kind)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #

    def _evaluate(self, kind: str) -> None:
        pending = self._pending.get(kind)
        if pending is None:
            return
        # (a debounce time only applies to a problem and its "resolved": one time events and the summary go at once)
        wait = DEBOUNCE.get(kind, DEFAULT_DEBOUNCE)[0 if pending["phase"] == "problem" else 1] if pending["phase"] in ("problem", "resolved") else 0.0
        now = self._now()
        if now - pending["since"] >= wait and now >= self._retry_at.get(kind, 0.0):
            self._start(kind, pending["phase"], pending["params"])

    def _start(self, kind: str, phase: str, params: dict) -> None:
        if kind in self._busy or self._now() < self._retry_at.get(kind, 0.0):
            return
        self._busy.add(kind)
        task = self._hass.async_create_background_task(self._async_deliver(kind, phase, params), f"battmind_{self._prefix}_push_{kind}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _async_deliver(self, kind: str, phase: str, params: dict) -> None:
        try:
            shown = {key: value for key, value in params.items() if not key.startswith("_")}
            title = self._t(f"ui.push.{kind}.title", f"BattMind ({self._prefix})", **shown)
            message = self._t(f"ui.push.{kind}.{phase}", kind, **shown)
            if not await self._async_send(kind, title, message):
                self._retry_at[kind] = self._now() + RETRY_AFTER
                return
            self._retry_at.pop(kind, None)
            self._sent(kind, phase, params)
        finally:
            self._busy.discard(kind)

    def _sent(self, kind: str, phase: str, params: dict) -> None:
        """A service took the push: remember it (the state only changes here, never before the delivery)."""
        state = self.state
        if phase == "problem":
            state["active"][kind] = self._now()
        elif phase == "resolved":
            state["active"].pop(kind, None)
        elif phase == "once":
            state["once"].append(kind)
            if kind in state["seen"]:
                state["seen"].remove(kind)
        elif phase == "summary":
            state["summary"] = params["_date"]
        current = self._pending.get(kind)
        if current is not None and current["phase"] == phase:
            self._pending.pop(kind, None)
        # a source that said the opposite while the push was on its way (and says it only once, like a frozen value that moves) is
        # not forgotten: what was sent no longer matches the last answer
        last = self._last.get(kind)
        if phase == "problem" and last is not None and last[0] is False and kind not in self._pending:
            self._pending[kind] = {"phase": "resolved", "since": self._now(), "params": {}}
        elif phase == "resolved" and last is not None and last[0] is True and kind not in self._pending:
            self._pending[kind] = {"phase": "problem", "since": self._now(), "params": last[1]}  # (its text needs its figures)
        self._save()

    async def _async_send(self, kind: str, title: str, message: str) -> bool:
        """Hand the push to every chosen service that exists. True when at least one took it."""
        delivered = False
        tag = f"battmind_{self._prefix}_{kind}"
        for name in self._targets:
            try:
                if self._hass.services.has_service("notify", name):
                    # (a legacy notify service: the tag lets "resolved" replace the problem on the phone)
                    data: dict[str, Any] = {"title": title, "message": message, "data": {"tag": tag}}
                    service = name
                elif self._hass.states.get(f"notify.{name}") is not None and self._hass.services.has_service("notify", "send_message"):
                    # a notify entity: the service takes the entity, a title and a message only
                    data = {"entity_id": f"notify.{name}", "title": title, "message": message}
                    service = "send_message"
                else:
                    continue  # not registered (yet): kept pending and tried again
                async with asyncio.timeout(SEND_TIMEOUT):
                    await self._hass.services.async_call("notify", service, data, blocking=True)
                delivered = True
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not send the push notification %s (%s): %s", kind, self._prefix, err)
        return delivered
