"""The values derived from a what-if result for the dashboard (engine/planner/what_if_view.py)."""
from __future__ import annotations

import math

import pytest

from custom_components.battmind.engine.planner.what_if_view import (
    build_view,
    duration_text,
    module_count_label,
    payback_series,
    plural_category,
    spread,
)
from custom_components.battmind.utils.formatting import load_texts, translate

MONTHS = ["2026-01", "2026-02", "2026-03", "2026-04"]


def translator(language: str = "en"):
    texts = load_texts(language, "DKK")
    return lambda key, default=None, **fmt: translate(texts, key, default, **fmt)


def months(values: list[float]) -> dict:
    return {key: {"profit": value, "bill": 0.0, "hours": 720} for key, value in zip(MONTHS, values)}


def assumptions(size: float, **rules) -> dict:
    return {
        "battery_size": size,
        "control_mode": "optimizer",
        "usage_reserve": 10.0,
        "min_profit_per_kwh": 0.25,
        "min_total_profit": 0.0,
        "min_sell_kwh": 0.2,
        "cheap_price_periods": 2.0,
        "cheapest_price_rise_threshold": 0.5,
        "cheap_price_period_rise_threshold": 0.1,
        "powerwall_max_hours_old": 48.0,
        "exclude_sell_hours": "17, 18",
        "rules": {"solar_charging": True, "cheapest_hour_fill_planner": True, "most_profit": True, **rules},
    }


def simulation(**extra) -> dict:
    result = {
        "period": {"start": "2026-01-01T00:00:00+01:00", "end": "2026-04-30T23:00:00+02:00", "hours": 2880, "days": 120.0},
        "current_assumptions": assumptions(10.0),
        "assumptions": assumptions(20.0, most_profit=False),
        "current": {"profit": 100.0, "with": {"bill": 900.0}, "months": months([10, 40, 30, 20])},
        "scenario": {"profit": 150.0, "with": {"bill": 850.0}, "months": months([20, 50, 40, 40])},
        "difference": {"profit": 50.0, "bill": -50.0},
    }
    result.update(extra)
    return result


def view(sim: dict, language: str = "en", inputs: dict | None = None) -> dict:
    return build_view(sim, translator(language), language, "kr", inputs)


def test_summary_and_monthly_series_with_the_best_month() -> None:
    result = view(simulation())

    assert result["summary"] == {
        "current_capacity": 10.0,
        "scenario_capacity": 20.0,
        "profit": {"current": 100.0, "scenario": 150.0, "difference": 50.0},
        "bill": {"current": 900.0, "scenario": 850.0, "difference": -50.0},
    }
    monthly = result["months"]
    assert monthly["order"] == MONTHS
    assert monthly["difference"] == {"2026-01": 10.0, "2026-02": 10.0, "2026-03": 10.0, "2026-04": 20.0}
    assert monthly["best"] == {"current": "2026-02", "scenario": "2026-02", "difference": "2026-04"}


def test_monthly_series_needs_more_than_three_months_of_both_results() -> None:
    sim = simulation()
    short = {key: sim["scenario"]["months"][key] for key in MONTHS[:3]}
    assert view({**sim, "scenario": {**sim["scenario"], "months": short}})["months"] is None
    assert view({**sim, "current": {**sim["current"], "months": None}})["months"] is None
    assert view(simulation())["range"] is None and view(simulation())["module"] is None


def test_a_month_missing_on_one_side_is_never_filled_with_a_favourable_value() -> None:
    sim = simulation()
    del sim["current"]["months"]["2026-04"]
    monthly = view(sim)["months"]
    assert "2026-04" not in monthly["current"]
    # a month without a Current value has no difference (it is not the scenario's whole profit), and is never "best"
    assert "2026-04" not in monthly["difference"]
    assert monthly["best"]["difference"] in monthly["difference"]


def test_range_rates_best_values_and_unavailable_intervals() -> None:
    sizes = [
        {"battery_size": 0.0, "profit": 0.0, "bill": 1000.0, "months": {key: 0.0 for key in MONTHS}},
        {"battery_size": 10.0, "profit": 100.0, "bill": 900.0, "months": {key: 25.0 for key in MONTHS}},
        {"battery_size": 20.0, "profit": 150.0, "bill": 850.0, "months": {key: 37.5 for key in MONTHS}},
        {"battery_size": 25.0, "profit": 140.0, "bill": 860.0, "months": {key: 35.0 for key in MONTHS}},
    ]
    result = view(simulation(range=sizes))["range"]

    rows = result["rows"]
    assert [row["rate"] for row in rows] == [None, 10.0, 5.0, -2.0]
    assert [row["interval_profit"] for row in rows] == [None, 100.0, 50.0, -10.0]
    assert [row["best_profit"] for row in rows] == [False, False, True, False]
    assert [row["best_rate"] for row in rows] == [False, True, False, False]
    assert [item["label"] for item in result["intervals"]] == ["0.0–10.0 kWh", "10.0–20.0 kWh", "20.0–25.0 kWh"]
    assert result["intervals"][1]["position"] == 15.0
    assert [item["battery_size"] for item in result["monthly"]] == [0.0, 10.0, 20.0, 25.0]

    # a size without a profit has no interval rate: nothing is made up
    sizes[2] = {**sizes[2], "profit": None}
    rows = view(simulation(range=sizes))["range"]["rows"]
    assert rows[2]["rate"] is None and rows[3]["rate"] is None and rows[2]["profit"] is None


def test_a_single_range_size_has_no_highlighted_best() -> None:
    rows = view(simulation(range=[{"battery_size": 10.0, "profit": 100.0, "bill": 900.0}]))["range"]["rows"]
    assert rows[0]["best_profit"] is False and rows[0]["best_rate"] is False


def test_monthly_range_series_are_limited_and_spread() -> None:
    sizes = [{"battery_size": float(size), "profit": float(size), "bill": 0.0, "months": {key: 1.0 for key in MONTHS}} for size in range(12)]
    monthly = view(simulation(range=sizes))["range"]["monthly"]
    assert [item["battery_size"] for item in monthly] == [0.0, 2.0, 4.0, 7.0, 9.0, 11.0]
    assert spread(list(range(3)), 6) == [0, 1, 2]


def module_comparison(**changes) -> dict:
    """Current 10 kWh, 5 kWh modules at 1000 each: current price 2000, bills 1200 without a battery."""
    annual_rows = [
        {"module_count": 0, "annual_bill": {"without": 1200.0, "with": 1000.0}, "cumulative_cost": 0.0,
         "marginal_vs_previous": {"annual_gain": None, "payback_years": None}},
        {"module_count": 1, "annual_bill": {"without": 1200.0, "with": 950.0}, "cumulative_cost": 1000.0,
         "marginal_vs_previous": {"annual_gain": 50.0, "payback_years": 20.0}},
        {"module_count": 2, "annual_bill": {"without": 1200.0, "with": 930.0}, "cumulative_cost": 2000.0,
         "marginal_vs_previous": {"annual_gain": 20.0, "payback_years": 50.0}},
    ]
    result = {
        "base_capacity": 10.0,
        "module_capacity": 5.0,
        "module_price": 1000.0,
        "module_count": 2,
        "annualization": {"available": True, "months": {"2025-11": {}}, "matched_hours": 8760, "represented_months": list(range(12))},
        "annual": {"method": "strict", "status_reason": None, "provenance": {}, "rows": annual_rows},
        "warnings": {"dst_replay_resets": 0, "tariff_estimated_hours": 0, "raw_current_sell_cost_hours": 0, "raw_unknown_sell_cost_hours": 0},
        "rows": [
            {"added_modules": 0, "battery_size": 10.0, "incremental_gain": None, "annualized_incremental_gain": None,
             "module_price": 0.0, "payback_years": None, "months": {key: 10.0 for key in MONTHS}},
            {"added_modules": 1, "battery_size": 15.0, "incremental_gain": 60.0, "annualized_incremental_gain": 60.0,
             "module_price": 1000.0, "payback_years": 16.67, "months": {key: 12.0 for key in MONTHS}},
            {"added_modules": 2, "battery_size": 20.0, "incremental_gain": 20.0, "annualized_incremental_gain": 20.0,
             "module_price": 1000.0, "payback_years": 50.0, "months": {key: 13.0 for key in MONTHS}},
        ],
    }
    result.update(changes)
    return result


def test_payback_series_crossings_follow_the_yearly_bills() -> None:
    comparison = module_comparison()
    lines = payback_series(comparison, comparison["annual"], translator(), "en")

    assert [line["modules"] for line in lines] == [-1, 0, 1, 2]
    none, current, first, second = lines
    assert (none["start"], none["rate"], none["dashed"]) == (0.0, 1200.0, True)
    # the current battery (10 kWh = two modules of 1000) pays back against no battery: 2000 / (1200 - 1000)
    assert (current["start"], current["years"], current["total_years"]) == (2000.0, 10.0, 10.0)
    # +1 module: the module alone 1000 / (1000 - 950); the whole battery 3000 / (1200 - 950)
    assert (first["start"], first["years"], first["total_years"]) == (3000.0, 20.0, 12.0)
    assert second["years"] == 50.0 and second["total_years"] == pytest.approx(4000 / 270)
    assert [line["capacity"] for line in lines[1:]] == [10.0, 15.0, 20.0]


def test_payback_never_comes_when_the_battery_saves_nothing() -> None:
    comparison = module_comparison()
    comparison["annual"]["rows"][1]["annual_bill"]["with"] = 1000.0
    comparison["annual"]["rows"][2]["annual_bill"]["with"] = 1010.0
    lines = payback_series(comparison, comparison["annual"], translator(), "en")
    assert lines[2]["years"] is None and lines[2]["total_years"] == pytest.approx(3000 / 200)
    assert lines[3]["years"] is None
    assert lines[3]["total_years"] == pytest.approx(4000 / 190)
    comparison["annual"]["rows"][0]["annual_bill"]["with"] = 1200.0
    lines = payback_series(comparison, comparison["annual"], translator(), "en")
    assert lines[1]["years"] is None and lines[1]["total_years"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"module_price": None},
        {"module_price": -1.0},
        {"module_capacity": 0.0},
        {"base_capacity": None},
    ],
)
def test_payback_series_is_unavailable_without_a_known_price(changes: dict) -> None:
    comparison = {**module_comparison(), **changes}
    assert payback_series(comparison, comparison["annual"], translator(), "en") is None


def test_payback_series_is_unavailable_without_known_bills() -> None:
    comparison = module_comparison()
    comparison["annual"]["rows"][0]["annual_bill"] = {"without": None, "with": None}
    assert payback_series(comparison, comparison["annual"], translator(), "en") is None
    comparison = module_comparison()
    # a current battery that is free is not a price: it is never treated as 0
    comparison["module_price"] = 0.0
    assert payback_series(comparison, comparison["annual"], translator(), "en") is None
    # without a current battery and with free modules every setup costs nothing: there is nothing to pay back
    comparison = module_comparison(base_capacity=0.0, module_price=0.0)
    for row in comparison["annual"]["rows"]:
        row["cumulative_cost"] = 0.0
    assert payback_series(comparison, comparison["annual"], translator(), "en") is None


def test_a_net_exporter_bill_is_drawn() -> None:
    comparison = module_comparison()
    for row in comparison["annual"]["rows"]:
        row["annual_bill"] = {"without": -100.0, "with": -300.0 - 10 * row["module_count"]}
    lines = payback_series(comparison, comparison["annual"], translator(), "en")
    assert lines[0]["rate"] == -100.0
    assert lines[1]["total_years"] == pytest.approx(2000 / 200)


def test_module_view_rows_chart_summary_and_monthly_extra_gain() -> None:
    result = view(simulation(module_comparison=module_comparison()))["module"]

    assert result["status"] is None and result["method"] == "strict"
    assert result["represented_months"] == 12 and result["matched_hours"] == 8760
    first, second, third = result["rows"]
    assert (first["added_modules"], first["whole_cost"], first["whole_gain"], first["whole_payback_years"]) == (0, 2000.0, 200.0, 10.0)
    assert first["payback_years"] is None and first["annual_gain"] is None
    assert (second["payback_years"], second["annual_gain"], second["whole_payback_years"]) == (20.0, 50.0, 12.0)
    assert third["payback_years"] == 50.0 and not result["has_no_payback"]

    chart = result["payback_chart"]
    assert chart["horizon"] == 30
    assert [line["dashed"] for line in chart["lines"]] == [True, False, False, False]
    assert chart["lines"][0]["points"] == [[0, 0.0], [30, 36000.0]]
    assert chart["lines"][1]["points"] == [[0, 2000.0], [30, 32000.0]]
    assert chart["lines"][1]["legend"].endswith("10 yr")
    assert [marker["x"] for marker in chart["markers"]] == [10.0, 12.0, 14.815]

    assert len(result["summary"]) == 4
    assert "10.0 kWh" in result["summary"][0] and "2000.00" in result["summary"][0] and "10 yr" in result["summary"][0]
    assert result["monthly"][0]["months"] == {key: 2.0 for key in MONTHS}
    assert result["monthly"][1]["months"] == {key: 3.0 for key in MONTHS}


def test_module_summary_leaves_out_sentences_with_unknown_numbers() -> None:
    comparison = module_comparison()
    comparison["annual"]["rows"][1]["marginal_vs_previous"] = {"annual_gain": None, "payback_years": None}
    summary = view(simulation(module_comparison=comparison))["module"]["summary"]
    # the sentence about the added module needs its yearly gain, the others do not
    assert len(summary) == 3
    assert all("None" not in sentence and "nan" not in sentence for sentence in summary)


def test_module_without_payback_is_marked_and_the_summary_says_so() -> None:
    comparison = module_comparison()
    comparison["annual"]["rows"][1]["marginal_vs_previous"] = {"annual_gain": -5.0, "payback_years": None}
    result = view(simulation(module_comparison=comparison))["module"]
    assert result["has_no_payback"] and result["rows"][1]["no_payback"]
    assert result["rows"][1]["payback_years"] is None


@pytest.mark.parametrize(
    ("annual", "annualization", "status"),
    [
        (None, {"available": True, "months": {"2025-11": {}}}, None),
        (None, {"available": False, "months": {"2025-11": {}}, "reason": "partial_year"}, "estimate_unavailable"),
        (None, {"available": True}, "rerun"),
        ({"method": "unavailable", "status_reason": "missing_sell_price", "rows": []}, {"months": {"2025-11": {}}}, "missing_sell_price"),
        ({"method": "unavailable", "status_reason": "missing_sell_cost", "rows": []}, {"months": {"2025-11": {}}}, "missing_sell_cost"),
        ({"method": "unavailable", "status_reason": "insufficient_coverage", "rows": []}, {"months": {"2025-11": {}}}, "estimate_unavailable"),
    ],
)
def test_module_status_when_the_annual_estimate_is_unavailable(annual, annualization, status) -> None:
    comparison = module_comparison(annual=annual, annualization={"matched_hours": 100, **annualization})
    result = view(simulation(module_comparison=comparison))["module"]
    assert result["status"] == status
    if status:
        assert result["payback_chart"] is None and result["summary"] == []
        assert all(row["annual_gain"] is None and row["payback_years"] is None for row in result["rows"])


def test_estimated_annual_result_reports_its_provenance() -> None:
    comparison = module_comparison()
    comparison["annual"] = {
        **comparison["annual"], "method": "estimated",
        "provenance": {"estimated_hours": 2000, "estimated_from_other_months": ["2025-12"]},
    }
    result = view(simulation(module_comparison=comparison))["module"]
    assert result["estimated"] and result["estimated_hours"] == 2000 and result["estimated_months"] == ["2025-12"]


def test_rules_table_lists_only_rules_active_on_a_side() -> None:
    rules = view(simulation())["rules"]

    assert rules["known"] == [True, True]
    groups = {group["group"]: group["rows"] for group in rules["groups"]}
    assert set(groups) == {"charging", "selling", "shared"}
    names = [row[0] for row in groups["charging"]]
    assert names[0] == "Control" and "Charging: Cheapest periods planner" in names
    most_profit = next(row for row in groups["selling"] if row[0].startswith("Discharging: Maximise"))
    assert most_profit[1:] == ["On", "—"]
    reserve = next(row for row in groups["shared"] if row[0].startswith("Reserve"))
    assert reserve[1:] == ["10 %", "10 %"]
    # the scenario has no selling rule: the selling settings are dashes, not the current values
    hours = next(row for row in groups["selling"] if row[0] == "Exclude selling hours")
    assert hours[1:] == ["17,18", "—"]
    minimum = next(row for row in groups["shared"] if row[0].startswith("Min. profit"))
    assert minimum[1:] == ["0.25 kr/kWh", "0.25 kr/kWh"]


def test_rules_of_a_side_without_battery_are_dashes_and_unknown_rules_are_flagged() -> None:
    sim = simulation()
    sim["current_assumptions"] = {**assumptions(0.0)}
    sim["assumptions"] = {key: value for key, value in assumptions(20.0).items() if key != "rules"}
    rules = view(sim)["rules"]
    assert rules["known"] == [True, False]
    assert rules["groups"] == []


def test_selected_values_use_labels_and_enabled_text() -> None:
    inputs = {"history_days": 365, "battery_size": 20.0, "most_profit": False, "usage_reserve": 25.0, "exclude_sell_hours": "1,3", "config_entry_id": "x", "unknown_key": 1.5}
    values = view(simulation(), inputs=inputs)["inputs"]
    assert values[0] == {"label": "History (days)", "value": "365"}
    assert {"label": "Battery size (kWh)", "value": "20.0"} in values
    switch = next(item for item in values if item["label"] == "Discharging: Maximise profit")
    assert switch["value"] not in ("False", "True", "")
    assert {"label": "unknown_key", "value": "1.5"} in values
    assert all(item["label"] != "config_entry_id" for item in values)


@pytest.mark.parametrize(
    ("language", "counts", "categories"),
    [
        ("en", [1, 2, 5], ["one", "other", "other"]),
        ("da", [1, 2], ["one", "other"]),
        ("pl", [1, 2, 4, 5, 12, 22], ["one", "few", "few", "other", "other", "few"]),
        ("uk", [1, 2, 5, 11, 21, 24], ["one", "few", "other", "other", "one", "few"]),
    ],
)
def test_plural_category_of_the_module_count(language: str, counts: list[int], categories: list[str]) -> None:
    assert [plural_category(language, count) for count in counts] == categories


def test_module_count_label_uses_the_plural_text_of_the_language() -> None:
    t = translator("pl")
    assert module_count_label(1, "pl", t) == "1 moduł"
    assert module_count_label(2, "pl", t) == "2 moduły"
    assert module_count_label(5, "pl", t) == "5 modułów"


@pytest.mark.parametrize(
    ("years", "expected"),
    [(0.0, "0 mo"), (0.04, "0 mo"), (0.5, "6 mo"), (2.0, "2 yr"), (2.5, "2 yr 6 mo"), (11.99, "12 yr"), (1.96, "2 yr")],
)
def test_duration_rounds_to_whole_months_and_carries_into_years(years: float, expected: str) -> None:
    assert duration_text(years, translator("en")) == expected


def test_every_language_formats_the_view_without_missing_texts() -> None:
    for language in ("da", "de", "en", "pl", "uk"):
        result = view(simulation(module_comparison=module_comparison()), language)
        text = repr(result)
        assert "dashboard." not in text and "ui.what_if" not in text, language
        assert not math.isnan(result["summary"]["profit"]["difference"])
