"""The sun in words per day, in the overview (engine/planner/overview.py `_solar_day_lines`): text only, from the plan's own figures."""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import pytest

from custom_components.battmind.engine.planner.overview import _hour_ranges

from .test_engine import DAY, snapshot
from .test_planner_rules import planned as _planned


def planned(hour: int, battery_level: float, settings: dict | None = None, **kwargs):
    """The plan of a day with the sun in the solar surplus prediction. The solar sensor is set only afterwards (see sun_lines):
    with it configured from the start the planner predicts the surplus from the (empty) history and overwrites the prediction."""
    return _planned(hour, battery_level, settings or {}, **kwargs)


SUN_LINES = re.compile(r"^☀️ .*$", re.MULTILINE)
TABLE = {day: {"day": "x", "date": "d", "when": "w", "emoji": "", "percentage": 0.0, "kWh": 0.0, "corrected": False} for day in range(3)}


def _job(engine, hour: int, settings: dict | None, function):
    now = datetime.datetime.combine(DAY, datetime.time(hour, 30))
    assert engine.run_job(snapshot(now, 50, settings or {}), function).error is None


def sun_lines(engine, hour: int = 0, settings: dict | None = None, installed: bool = True) -> list[str]:
    """The ☀️ lines of the overview of the engine's plan, built like the planner does after a plan, with solar installed."""
    if installed:
        engine.config["solar"]["entity_ids"]["production_entity_id"] = "sensor.solar"
    holder = {}

    def read() -> None:
        holder["overview"] = chr(10).join(engine._build_overview(engine.plan, engine.charge_hours, {}, TABLE))

    _job(engine, hour, settings, read)
    return SUN_LINES.findall(holder["overview"])


def lines_for(engine, plan_days=(0, 1), hour: int = 0) -> list[str]:
    """_solar_day_lines of the engine's plan, run inside a job (it reads the clock of the job)."""
    holder = {}

    def read() -> None:
        holder["lines"] = engine._solar_day_lines(engine.plan, list(plan_days))

    _job(engine, hour, None, read)
    return holder["lines"]


def _levels(engine, day: int, levels) -> None:
    """The plan's battery levels of a day, per hour the list the planner writes (its sum is the level at the end of the hour)."""
    engine.plan[day]["battery_level_flow"] = {hour: list(levels(hour)) for hour in range(24)}


def test_the_sentence_about_the_sun_comes_with_the_plans_first_figures() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    today, tomorrow = sun_lines(engine)[:2]
    # 3 kWh in each of the 7 hours 09:00-16:00
    assert today.startswith("☀️ I dag: solen giver cirka 21.0 kWh overskud mellem 09:00 og 16:00.")
    assert tomorrow.startswith("☀️ I morgen: solen giver cirka 21.0 kWh overskud mellem 09:00 og 16:00.")
    assert len(sun_lines(engine)) == 2  # only today and tomorrow


def _filling(hour: int):
    """55 % until 09:00, +15 % per sun hour; the hour that hits the maximum is written as [max, 0.0] by the planner."""
    if hour < 9:
        return (55.0,)
    if hour <= 10:
        return (55.0 + 15.0 * (hour - 9), 15.0)
    return (100.0, 0.0)


def test_a_battery_that_the_sun_fills_says_when_and_what_there_is_no_room_for() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    _levels(engine, 0, _filling)
    assert lines_for(engine)[0] == (
        "I dag: solen giver cirka 21.0 kWh overskud mellem 09:00 og 16:00. "
        "Batteriet er fuldt (100 %) kl. 12:00. Derefter er der ikke plads til cirka 12.0 kWh overskud i batteriet."
    )


def test_the_hour_that_fills_the_battery_is_not_taken_for_a_battery_that_was_full_before() -> None:
    """The planner writes [max, 0.0] for the hour that hits the maximum, so the first part of the list is not the level at the start
    of the hour: at 90 % before the sun, the first sun hour fills it (full by 10:00), it was not full before."""
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    _levels(engine, 0, lambda hour: (90.0,) if hour < 9 else (100.0, 0.0))
    text = lines_for(engine)[0]
    assert "Batteriet er fuldt (100 %) kl. 10:00." in text and "allerede fuldt" not in text
    assert "Derefter er der ikke plads til cirka 18.0 kWh overskud i batteriet." in text


def test_a_battery_the_sun_does_not_fill_says_where_it_gets_to() -> None:
    engine, _ = planned(0, 30, {}, solar=[0.0] * 9 + [0.4] * 7 + [0.0] * 8, days=3)
    _levels(engine, 0, lambda hour: (30.0 + 2.0 * (hour - 9), 2.0) if 9 <= hour <= 15 else ((44.0, 0.0) if hour > 15 else (30.0,)))
    assert lines_for(engine)[0] == "I dag: solen giver cirka 2.8 kWh overskud mellem 09:00 og 16:00. Batteriet når cirka 44 % kl. 16:00."


def test_a_battery_that_is_full_before_the_sun_says_so() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    _levels(engine, 0, lambda hour: (100.0, 0.0))
    assert lines_for(engine)[0] == (
        "I dag: solen giver cirka 21.0 kWh overskud mellem 09:00 og 16:00. "
        "Batteriet er allerede fuldt, når solen kommer: der er ikke plads til cirka 21.0 kWh overskud."
    )


def test_the_text_never_says_where_the_surplus_that_does_not_fit_goes() -> None:
    """It may go to the grid, to a car (Cable Juice Planner) or be curtailed: the text says only that there is no room for it."""
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    _levels(engine, 0, lambda hour: (100.0, 0.0))
    assert "net" not in lines_for(engine)[0].lower()


def test_the_level_at_the_start_of_the_first_sun_hour_is_the_end_of_the_hour_before() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    engine.plan[0]["battery_level_flow"] = {22: [40.0], 23: [40.0, 5.0]}
    holder = {}

    def read() -> None:
        holder["tomorrow_midnight"] = engine._solar_day_start_level(engine.plan, 1, 0)  # the end of 23:00-23:59 of today
        holder["same_day"] = engine._solar_day_start_level(engine.plan, 0, 23)  # the end of 22:00-22:59
        holder["unknown"] = engine._solar_day_start_level(engine.plan, 0, 5)
        holder["now"] = engine._solar_day_start_level(engine.plan, 0, 12)  # the hour going on, no hour before it in the plan: the level now

    _job(engine, 12, None, read)
    assert holder == {"tomorrow_midnight": 45.0, "same_day": 40.0, "unknown": None, "now": 50.0}


def test_a_missing_battery_level_leaves_out_the_battery_sentences_but_not_the_sun() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    engine.plan[0]["battery_level_flow"] = {}
    assert lines_for(engine)[0] == "I dag: solen giver cirka 21.0 kWh overskud mellem 09:00 og 16:00."


def test_the_plan_that_is_made_has_levels_the_text_can_read() -> None:
    """Not only built levels: with the real plan the text names a full hour or a level, and never fails."""
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    for line in lines_for(engine):
        assert re.search(r"Batteriet (er fuldt|er allerede fuldt|når cirka)", line), line


def test_today_counts_only_the_hours_that_are_left() -> None:
    engine, _ = planned(12, 30, {}, solar=True, days=3)
    assert sun_lines(engine, 12)[0].startswith("☀️ I dag: solen giver cirka 12.0 kWh overskud mellem 12:00 og 16:00.")
    # the sun is over: no text for today, tomorrow still has one
    late, _ = planned(18, 30, {}, solar=True, days=3)
    lines = sun_lines(late, 18)
    assert len(lines) == 1 and lines[0].startswith("☀️ I morgen:")


def test_a_day_without_sun_has_no_text() -> None:
    engine, _ = planned(0, 30, {}, solar=[0.0] * 24, days=3)
    assert sun_lines(engine) == []


@pytest.mark.parametrize("surplus", [[3.0] * 23, [3.0] * 25, []])
def test_a_forecast_that_does_not_cover_all_24_hours_has_no_text(surplus: list[float]) -> None:
    """An hour of a value would be a guess: no text rather than a wrong hour."""
    engine, _ = planned(0, 30, {}, solar=surplus, days=3)
    assert sun_lines(engine) == []


def test_without_solar_charging_in_the_plan_there_is_no_text() -> None:
    engine, _ = planned(0, 30, {"solar_charging": False}, solar=True, days=3)
    assert sun_lines(engine, 0, {"solar_charging": False}) == []


def test_without_solar_the_overview_has_no_sun_text() -> None:
    engine, _ = planned(0, 30, {}, solar=False, days=3)
    assert sun_lines(engine, installed=False) == []
    assert "☀️ I dag" not in engine.last_overview


def test_hours_where_solar_charging_is_held_back_are_named() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    start = engine.plan[0]["start_of_day"]
    engine.plan[0]["blocked_solar_charge_timestamps"] = {start.replace(hour=hour): {} for hour in (9, 10, 11, 14)}
    engine.plan[1]["blocked_solar_charge_timestamps"] = {}
    today, tomorrow = lines_for(engine)
    assert "Solopladningen holdes tilbage kl. 09:00-12:00, 14:00-15:00, så overskuddet sælges, når prisen er højere." in today
    assert "holdes tilbage" not in tomorrow
    assert "fyldes" not in today  # the optimiser may hold solar back without a later sun hour that fills the battery


def test_blocked_hours_of_another_day_are_not_named() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    other = engine.plan[1]["start_of_day"].replace(hour=10)
    engine.plan[0]["blocked_solar_charge_timestamps"] = {other: {}}
    assert "holdes tilbage" not in lines_for(engine)[0]


def test_a_blocked_stamp_that_is_text_or_no_time_is_read_or_left_out() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    day = engine.plan[0]["start_of_day"].date().isoformat()
    engine.plan[0]["blocked_solar_charge_timestamps"] = {f"{day}T09:00:00": {}, "no time": {}, None: {}}
    assert "holdes tilbage kl. 09:00-10:00," in lines_for(engine)[0]


def test_a_broken_figure_is_a_zero_not_an_error() -> None:
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    engine.plan[0]["solar_kwh_prediction"] = [None, "x", float("nan"), *[3.0] * 21]
    assert lines_for(engine)[0].startswith("I dag: solen giver cirka 63.0 kWh overskud mellem 03:00 og 24:00.")


def test_the_text_never_removes_the_table_when_it_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from custom_components.battmind.engine.planner.overview import Overview

    def broken(self, charging_plan, days):
        raise RuntimeError("broken")

    monkeypatch.setattr(Overview, "_solar_day_lines", broken)
    engine, _ = planned(0, 30, {}, solar=True, days=3)
    engine.config["solar"]["entity_ids"]["production_entity_id"] = "sensor.solar"
    holder = {}

    def read() -> None:
        holder["overview"] = chr(10).join(engine._build_overview(engine.plan, engine.charge_hours, {}, TABLE))

    _job(engine, 0, None, read)
    assert "Overskydende solproduktion tilgængelig" in holder["overview"] and "☀️ I dag" not in holder["overview"]


def test_the_hours_are_joined_to_ranges() -> None:
    assert _hour_ranges([10, 11, 12, 15]) == "10:00-13:00, 15:00-16:00"
    assert _hour_ranges([9]) == "09:00-10:00"
    assert _hour_ranges([23]) == "23:00-24:00"
    assert _hour_ranges([14, 9, 10, 11]) == "09:00-12:00, 14:00-15:00"
    assert _hour_ranges([]) == ""


TEXTS_DIR = Path(__file__).parents[1] / "custom_components" / "battmind" / "texts"
KEYS = ("solar_day_surplus", "solar_day_full", "solar_day_rises", "solar_day_to_grid", "solar_day_already_full", "solar_day_blocked")
PLACEHOLDERS = {
    "solar_day_surplus": {"day", "kwh", "start", "end"},
    "solar_day_full": {"level", "at"},
    "solar_day_rises": {"level", "at"},
    "solar_day_to_grid": {"kwh"},
    "solar_day_already_full": {"kwh"},
    "solar_day_blocked": {"hours"},
}


@pytest.mark.parametrize("language", ["da", "de", "en", "pl", "uk"])
def test_the_texts_exist_in_every_language_with_the_same_placeholders(language: str) -> None:
    texts = json.loads((TEXTS_DIR / f"{language}.json").read_text(encoding="utf-8"))["ui"]["cheap_grid_charge_hours"]
    for key in KEYS:
        assert set(re.findall(r"\{(\w+)\}", texts[key])) == PLACEHOLDERS[key], (language, key)
