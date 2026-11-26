"""The push notifications (management/notifier.py): states of a condition, debounce, delivery, restart, one time events."""
from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.battmind.management.notifier import PushNotifier, clean_state

SERVICE = "mobile_app_test"
OPTIONS = {"notify_list": [SERVICE], "notify_problems": True, "notify_storm": True, "notify_learning": True, "notify_summary": True}


class Clock:
    def __init__(self) -> None:
        self.time = 100_000.0

    def __call__(self) -> float:
        return self.time

    def advance(self, seconds: float) -> None:
        self.time += seconds


def _text(key: str, default: str | None = None, **fmt) -> str:
    return f"{key}|{sorted(fmt.items())}" if fmt else key


async def _notifier(hass: HomeAssistant, clock: Clock, **options) -> PushNotifier:
    notifier = PushNotifier(hass, "entry", "bm", {**OPTIONS, **options}, _text, clock)
    await notifier.async_load()
    return notifier


async def _settle(hass: HomeAssistant) -> None:
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_a_problem_is_sent_once_and_resolved_once(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    clock = Clock()
    notifier = await _notifier(hass, clock)

    for _ in range(3):  # the same problem again and again: one push
        notifier.report("battery_level_lost", True, entity="sensor.level")
        await _settle(hass)
        clock.advance(60)
    assert len(calls) == 1
    assert calls[0].data["title"] == "ui.push.battery_level_lost.title|[('entity', 'sensor.level')]"
    assert calls[0].data["message"].startswith("ui.push.battery_level_lost.problem")
    assert calls[0].data["data"] == {"tag": "battmind_bm_battery_level_lost"}  # the same tag for "resolved": it replaces the problem
    assert "battery_level_lost" in notifier.state["active"]

    notifier.report("battery_level_lost", None)  # not evaluated: nothing changes
    notifier.report("battery_level_lost", True, entity="sensor.level")
    await _settle(hass)
    assert len(calls) == 1

    clock.advance(400)  # (the debounce of "resolved" is 5 minutes for this kind)
    notifier.report("battery_level_lost", False)
    clock.advance(301)
    notifier.report("battery_level_lost", False)
    await _settle(hass)
    assert len(calls) == 2 and calls[1].data["message"] == "ui.push.battery_level_lost.resolved"
    assert calls[1].data["data"] == {"tag": "battmind_bm_battery_level_lost"}
    assert notifier.state["active"] == {}

    notifier.report("battery_level_lost", False)  # over and said so: nothing more
    await _settle(hass)
    assert len(calls) == 2


async def test_a_condition_that_flaps_inside_the_debounce_sends_nothing(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    clock = Clock()
    notifier = await _notifier(hass, clock)

    for _ in range(5):  # temperature_stop holds 15 minutes before the push; it is over after 10 every time
        notifier.report("temperature_stop", True, detail="55 C")
        clock.advance(600)
        notifier.report("temperature_stop", False)
        clock.advance(60)
    await _settle(hass)

    assert calls == []
    assert notifier.state["active"] == {}


async def test_the_push_waits_for_the_debounce_time_of_its_kind(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    clock = Clock()
    notifier = await _notifier(hass, clock)

    notifier.report("temperature_stop", True, detail="a")
    clock.advance(899)
    notifier.report("temperature_stop", True, detail="b")
    await _settle(hass)
    assert calls == []
    clock.advance(2)
    notifier.report("temperature_stop", True, detail="c")
    await _settle(hass)

    assert len(calls) == 1 and "('detail', 'c')" in calls[0].data["message"]  # the newest figures


async def test_a_service_that_is_not_there_yet_keeps_the_push_pending(hass: HomeAssistant, hass_storage) -> None:
    clock = Clock()
    notifier = await _notifier(hass, clock)

    notifier.report("startup_failed", True, error="boom")
    await _settle(hass)
    assert notifier.state["active"] == {}  # not sent: nothing is remembered as sent

    calls = async_mock_service(hass, "notify", SERVICE)  # the mobile app registered its service
    notifier.tick()
    await _settle(hass)
    assert calls == []  # (it waits before it tries again)
    clock.advance(301)
    notifier.tick()
    await _settle(hass)

    assert len(calls) == 1 and "startup_failed" in notifier.state["active"]


async def test_a_notify_entity_gets_the_message_through_send_message(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.phone", "unknown")
    notifier = await _notifier(hass, Clock(), notify_list=["phone"])

    notifier.report("startup_failed", True, error="boom")
    await _settle(hass)

    assert len(calls) == 1
    assert calls[0].data["entity_id"] == "notify.phone" and set(calls[0].data) == {"entity_id", "title", "message"}  # no data/tag


async def test_what_was_sent_is_not_sent_again_after_a_restart(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    clock = Clock()
    notifier = await _notifier(hass, clock)
    notifier.report("plan_not_followed", True)
    await _settle(hass)
    notifier.report_summary("2026-11-26", grid=1)
    await _settle(hass)
    await notifier.async_shutdown()
    assert len(calls) == 2

    restarted = await _notifier(hass, clock)  # the problem is still there after the restart, the summary was sent
    restarted.report("plan_not_followed", True)
    restarted.report_summary("2026-11-26", grid=1)
    await _settle(hass)
    assert len(calls) == 2 and "plan_not_followed" in restarted.state["active"]

    restarted.report("plan_not_followed", False)  # ... and when it is over, "resolved" goes
    await _settle(hass)
    assert len(calls) == 3 and calls[2].data["message"] == "ui.push.plan_not_followed.resolved"


async def test_nothing_goes_without_a_service_or_with_the_category_off(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    no_service = await _notifier(hass, Clock(), notify_list=[])
    no_service.report("plan_not_followed", True)
    storm_off = await _notifier(hass, Clock(), notify_storm=False)
    storm_off.report("storm", True, level=3)
    storm_off.report("plan_not_followed", True)  # (another category is still on)
    await _settle(hass)

    assert len(calls) == 1 and calls[0].data["message"].startswith("ui.push.plan_not_followed.problem")
    assert no_service.state["active"] == {} and "storm" not in storm_off.state["active"]


async def test_something_learned_is_sent_once_after_it_was_seen_not_ready(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    notifier = await _notifier(hass, Clock())

    notifier.report_ready("learning_efficiency", True)  # ready when first seen (an installation that was updated): noted, not sent
    notifier.report_ready("learning_consumption", False)
    notifier.report_ready("learning_consumption", False)
    await _settle(hass)
    assert calls == []

    notifier.report_ready("learning_consumption", True)
    notifier.report_ready("learning_consumption", True)
    await _settle(hass)
    notifier.report_ready("learning_consumption", True)
    notifier.report_ready("learning_efficiency", True)
    await _settle(hass)
    assert len(calls) == 1 and calls[0].data["message"] == "ui.push.learning_consumption.once"

    notifier.report_ready("learning_efficiency", False)  # reset (e.g. a new sensor): ready again is news
    notifier.report_ready("learning_efficiency", True)
    await _settle(hass)
    assert len(calls) == 2 and calls[1].data["message"] == "ui.push.learning_efficiency.once"


async def test_the_daily_summary_goes_once_per_date(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    notifier = await _notifier(hass, Clock())

    for _ in range(3):
        notifier.report_summary("2026-11-26", grid="8.2")
        await _settle(hass)
    notifier.report_summary("2026-11-27", grid="1.0")
    await _settle(hass)

    assert [call.data["message"] for call in calls] == [
        "ui.push.daily_summary.summary|[('grid', '8.2')]", "ui.push.daily_summary.summary|[('grid', '1.0')]",
    ]
    assert "_date" not in calls[0].data["message"]


async def test_a_failing_service_is_not_counted_as_sent(hass: HomeAssistant, hass_storage) -> None:
    async def broken(call) -> None:
        raise RuntimeError("down")

    hass.services.async_register("notify", SERVICE, broken)
    clock = Clock()
    notifier = await _notifier(hass, clock)
    notifier.report("job_failing", True, error="x")
    await _settle(hass)

    assert notifier.state["active"] == {}  # tried again later
    hass.services.async_remove("notify", SERVICE)
    calls = async_mock_service(hass, "notify", SERVICE)
    clock.advance(301)
    notifier.tick()
    await _settle(hass)
    assert len(calls) == 1 and "job_failing" in notifier.state["active"]


def test_a_malformed_stored_state_is_dropped() -> None:
    assert clean_state("garbage") == {"active": {}, "once": [], "seen": [], "summary": None}
    assert clean_state({"active": {"storm": 5.0, "nonsense": 1.0, "plan_not_followed": "x"}, "once": ["storm", 3, "nope"], "seen": "x", "summary": 7}) == {
        "active": {"storm": 5.0}, "once": ["storm"], "seen": [], "summary": None,
    }


def test_every_kind_has_its_category() -> None:
    from custom_components.battmind.management.notifier import CATEGORIES, CATEGORY_OPTIONS

    assert CATEGORIES and all(category in CATEGORY_OPTIONS for category in CATEGORIES.values())


async def test_a_problem_that_clears_while_its_push_is_on_its_way_is_resolved_afterwards(hass: HomeAssistant, hass_storage) -> None:
    release = asyncio.Event()
    calls = []

    async def slow(call) -> None:
        calls.append(call.data["message"])
        await release.wait()

    hass.services.async_register("notify", SERVICE, slow)
    notifier = await _notifier(hass, Clock())

    notifier.report("consumption_frozen", True, entity="sensor.x", value="5", since="a")
    await asyncio.sleep(0)
    notifier.report("consumption_frozen", False)  # a source that says "over" only once
    release.set()
    await _settle(hass)
    notifier.tick()
    await _settle(hass)

    assert [message.split("|")[0] for message in calls] == ["ui.push.consumption_frozen.problem", "ui.push.consumption_frozen.resolved"]
    assert notifier.state["active"] == {}


async def test_a_reload_lets_the_push_that_is_on_its_way_finish(hass: HomeAssistant, hass_storage) -> None:
    release = asyncio.Event()

    async def slow(call) -> None:
        await release.wait()

    hass.services.async_register("notify", SERVICE, slow)
    notifier = await _notifier(hass, Clock())
    notifier.report("job_failing", True, error="x")
    await asyncio.sleep(0)

    shutdown = hass.async_create_task(notifier.async_shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()  # waits for the send
    release.set()
    await shutdown

    assert "job_failing" in notifier.state["active"]  # sent, and written
    assert hass_storage["battmind.entry.notices"]["data"]["active"].get("job_failing")
    notifier.report("plan_not_followed", True)  # nothing is reported or saved after the shutdown
    assert notifier._pending == {}


async def test_the_timer_tries_again_without_any_job(hass: HomeAssistant, hass_storage) -> None:
    clock = Clock()
    notifier = await _notifier(hass, clock)
    notifier.start()
    notifier.report("startup_failed", True, error="boom")  # (the service is not registered yet: no job follows a failed start)
    notifier.report_summary("2026-11-26", day="26/11")
    await _settle(hass)
    assert notifier.state["active"] == {} and notifier.state["summary"] is None

    calls = async_mock_service(hass, "notify", SERVICE)
    clock.advance(301)
    async_fire_time_changed(hass, dt_util.utcnow() + __import__("datetime").timedelta(minutes=2))
    await _settle(hass)

    assert len(calls) == 2 and "startup_failed" in notifier.state["active"] and notifier.state["summary"] == "2026-11-26"
    await notifier.async_shutdown()


async def test_something_learned_is_tried_again_until_it_is_delivered(hass: HomeAssistant, hass_storage) -> None:
    clock = Clock()
    notifier = await _notifier(hass, clock)
    notifier.report_ready("learning_capacity", False)
    notifier.report_ready("learning_capacity", True)  # no service yet
    await _settle(hass)
    assert notifier.state["once"] == []

    calls = async_mock_service(hass, "notify", SERVICE)
    clock.advance(301)
    notifier.tick()
    await _settle(hass)
    notifier.tick()
    await _settle(hass)

    assert len(calls) == 1 and notifier.state["once"] == ["learning_capacity"]


async def test_a_category_that_was_off_announces_nothing_that_was_already_ready_when_it_is_turned_on(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    off = await _notifier(hass, Clock(), notify_learning=False)
    off.report_ready("learning_consumption", True)
    on = await _notifier(hass, Clock())  # the option was turned on (the entry reloads)
    on.report_ready("learning_consumption", True)
    await _settle(hass)

    assert calls == [] and on.state["once"] == ["learning_consumption"]


async def test_a_notify_list_that_is_one_string_is_read_as_one_service(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    notifier = await _notifier(hass, Clock(), notify_list=f"notify.{SERVICE}")
    notifier.report("startup_failed", True, error="x")
    await _settle(hass)

    assert len(calls) == 1


async def test_the_end_of_a_storm_waits_before_it_is_said(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    clock = Clock()
    notifier = await _notifier(hass, clock)
    notifier.report("storm", True, level=3, start="a", end="b", keep="80")
    await _settle(hass)

    notifier.report("storm", False)  # the warning entity says off for a moment ...
    clock.advance(600)
    notifier.report("storm", True, level=3, start="a", end="b", keep="80")  # ... and the storm is back: nothing was said
    await _settle(hass)
    assert len(calls) == 1

    notifier.report("storm", False)
    clock.advance(1801)
    notifier.report("storm", False)
    await _settle(hass)
    assert len(calls) == 2 and calls[1].data["message"] == "ui.push.storm.resolved"


async def test_a_measurement_under_suspicion_does_not_announce_itself_again(hass: HomeAssistant, hass_storage) -> None:
    calls = async_mock_service(hass, "notify", SERVICE)
    notifier = await _notifier(hass, Clock())
    notifier.report_ready("learning_capacity", False)
    notifier.report_ready("learning_capacity", True)
    await _settle(hass)
    assert len(calls) == 1

    notifier.report_ready("learning_capacity", None)  # inconsistent: not clear, nothing changes
    notifier.report_ready("learning_capacity", True)  # within again
    await _settle(hass)

    assert len(calls) == 1 and notifier.state["once"] == ["learning_capacity"]


async def test_a_problem_that_comes_back_while_its_resolved_is_on_its_way_keeps_its_figures(hass: HomeAssistant, hass_storage) -> None:
    release = asyncio.Event()
    calls = []

    async def slow(call) -> None:
        calls.append(call.data["message"])
        if len(calls) == 2:
            await release.wait()

    hass.services.async_register("notify", SERVICE, slow)
    notifier = await _notifier(hass, Clock())
    notifier.report("consumption_frozen", True, entity="sensor.x", value="5", since="a")
    await _settle(hass)
    notifier.report("consumption_frozen", False)  # its "resolved" is on its way (the service waits) ...
    await asyncio.sleep(0)
    notifier.report("consumption_frozen", True, entity="sensor.x", value="7", since="b")  # ... and it is back, with other figures
    release.set()
    await _settle(hass)
    notifier.tick()
    await _settle(hass)

    assert [message.split("|")[0] for message in calls] == [
        "ui.push.consumption_frozen.problem", "ui.push.consumption_frozen.resolved", "ui.push.consumption_frozen.problem",
    ]
    assert "('value', '7')" in calls[2]  # the text has its figures (not {value})


async def test_a_problem_that_was_sent_while_its_category_is_now_off_is_let_go(hass: HomeAssistant, hass_storage) -> None:
    hass_storage["battmind.entry.notices"] = {
        "version": 1, "minor_version": 1, "key": "battmind.entry.notices", "data": {"active": {"storm": 5.0, "plan_not_followed": 6.0}, "once": [], "seen": [], "summary": None},
    }
    notifier = await _notifier(hass, Clock(), notify_storm=False)

    assert list(notifier.state["active"]) == ["plan_not_followed"]
