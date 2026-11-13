"""The chart configurations of the what-if view (engine/planner/what_if_charts.py)."""
from __future__ import annotations

import json

import pytest

from custom_components.battmind.engine.planner.what_if_charts import (
    BEST_COLOR,
    GRADIENT_HIGH,
    GRADIENT_LOW,
    gradient_color,
    NONE_COLOR,
    category_chart,
    payback_chart,
)
from .test_what_if_view import module_comparison, simulation, view

DECODER = json.JSONDecoder()


def embedded(js: str, marker: str) -> object:
    """The JSON value that follows `marker` in a piece of JavaScript."""
    return DECODER.raw_decode(js[js.index(marker) + len(marker):])[0]


def points_of(series: dict) -> list[list[float]]:
    return embedded(series["data_generator"], "return ")


def labels_of(chart: dict) -> list[str]:
    return embedded(chart["apex_config"]["xaxis"]["labels"]["formatter"], "L=")


def tooltips_of(chart: dict) -> list[list]:
    return embedded(chart["apex_config"]["tooltip"]["x"]["formatter"], "P=")


def all_javascript(chart: dict) -> list[str]:
    config = chart["apex_config"]
    return [
        config["xaxis"]["labels"]["formatter"], config["tooltip"]["x"]["formatter"],
        *(series["data_generator"] for series in chart["series"]),
    ]


def assert_plain_javascript(chart: dict) -> None:
    """No template literal or ${...} (the card of the dashboard replaces those), balanced brackets, EVAL for the formatters."""
    for js in all_javascript(chart):
        assert "${" not in js and "`" not in js
        for opening, closing in ("()", "{}", "[]"):
            assert js.count(opening) == js.count(closing), js
    config = chart["apex_config"]
    assert config["xaxis"]["labels"]["formatter"].startswith("EVAL:function(")
    assert config["tooltip"]["x"]["formatter"].startswith("EVAL:function(")


def test_a_category_chart_puts_point_i_at_day_i_plus_one_with_labels_and_tooltips() -> None:
    chart = category_chart("profit", "Battery profit", [
        {"label": "0", "tooltip": "0 kWh", "y": 0.0},
        {"label": "10", "tooltip": "10 kWh", "y": 1000.0},
        {"label": "20", "tooltip": "20 kWh", "y": 1500.0, "best": True},
        {"label": "30", "tooltip": "30 kWh", "y": -250.0},
    ])
    assert chart["days"] == 5  # a day before the first point and a day after the last
    (series,) = chart["series"]
    assert series["name"] == "Battery profit" and series["type"] == "line" and series["curve"] == "straight"
    assert points_of(series) == [[1, 0.0], [2, 1000.0], [3, 1500.0], [4, -250.0]]
    assert labels_of(chart) == ["", "0", "10", "20", "30", ""]
    assert tooltips_of(chart) == [[1, "0 kWh"], [2, "10 kWh"], [3, "20 kWh"], [4, "30 kWh"]]
    # the best point is a green marker on the third value of the series
    assert chart["apex_config"]["markers"]["discrete"] == [
        {"seriesIndex": 0, "dataPointIndex": 2, "fillColor": BEST_COLOR, "strokeColor": "#ffffff", "size": 6}
    ]
    assert chart["apex_config"]["tooltip"]["shared"] is True and chart["apex_config"]["tooltip"]["intersect"] is False
    assert_plain_javascript(chart)


def test_a_point_without_a_value_is_not_drawn_as_zero_but_keeps_its_label() -> None:
    assert category_chart("profit", "x", []) is None
    assert category_chart("profit", "x", [{"label": "a", "y": None}, {"label": "b", "y": float("nan")}]) is None
    chart = category_chart("profit", "x", [
        {"label": "a", "y": 1.0}, {"label": "b", "y": None}, {"label": "c", "y": 5.0, "best": True},
    ])
    assert points_of(chart["series"][0]) == [[1, 1.0], [3, 5.0]]
    assert labels_of(chart) == ["", "a", "b", "c", ""]
    # the marker counts the points that are drawn: the second one
    assert chart["apex_config"]["markers"]["discrete"][0]["dataPointIndex"] == 1


def test_the_formatters_read_the_start_of_the_window_from_the_chart_with_a_fallback() -> None:
    chart = category_chart("interval", "x", [{"label": "0–10", "y": 1.0}])
    axis = chart["apex_config"]["xaxis"]["labels"]["formatter"]
    assert ".globals.minX" in axis and '(window.__bm||{})["interval"]' in axis
    # the data generator stores the start of its window for the formatters
    assert '(window.__bm=window.__bm||{})["interval"]=start.getTime();' in chart["series"][0]["data_generator"]
    assert "start.getTime()+p[0]*864e5" in chart["series"][0]["data_generator"]
    # the crosshair label of the axis is off: it would show a date
    assert chart["apex_config"]["xaxis"]["tooltip"] == {"enabled": False}


def test_the_card_adds_no_point_and_has_one_tick_per_day() -> None:
    """apexcharts-card extends a line to the end of the window and, with a formatter, spaces the ticks evenly."""
    chart = category_chart("profit", "x", [{"label": "a", "y": 1.0}, {"label": "b", "y": None}, {"label": "c", "y": 2.0}])
    assert chart["series"][0]["extend_to"] is False
    assert chart["apex_config"]["xaxis"]["tickAmount"] == chart["days"] == 4
    # the tooltip gets the options as its second argument, the axis as its third: both are read
    for js in (chart["apex_config"]["xaxis"]["labels"]["formatter"], chart["apex_config"]["tooltip"]["x"]["formatter"]):
        assert "(o&&o.w)||(t&&t.w)" in js
    payback = payback_chart(chart_data(), "years")
    assert all(series["extend_to"] is False for series in payback["series"])
    assert payback["apex_config"]["xaxis"]["tickAmount"] == payback["days"]
    # only the paybacks are dots on the payback lines
    assert payback["apex_config"]["markers"]["size"] == 0 and category_chart("p", "x", [{"label": "a", "y": 1.0}])["apex_config"]["markers"]["size"] == 4


def test_gradient_colours_go_from_red_to_green() -> None:
    assert gradient_color(0.0) == "#d75c50" and gradient_color(1.0) == "#36a269"
    assert gradient_color(-5) == gradient_color(0.0) and gradient_color(9) == gradient_color(1.0)
    assert (GRADIENT_LOW, GRADIENT_HIGH) == ((215, 92, 80), (54, 162, 105))
    assert gradient_color(0.5) == "#"  + "".join(f"{round((a + b) / 2):02x}" for a, b in zip(GRADIENT_LOW, GRADIENT_HIGH))


def test_the_profit_line_is_coloured_per_segment_by_the_profit_per_added_kwh() -> None:
    points = [
        {"label": "0", "y": 0.0}, {"label": "10", "y": 100.0, "rate": 10.0},
        {"label": "20", "y": 150.0, "rate": 5.0}, {"label": "30", "y": 160.0, "rate": 1.0},
    ]
    chart = category_chart("profit", "x", points, gradient="segments")
    assert chart["gradient"] is True
    gradient = chart["apex_config"]["fill"]["gradient"]
    assert chart["apex_config"]["fill"]["type"] == "gradient" and gradient["type"] == "horizontal"
    assert chart["apex_config"]["stroke"]["fill"]["gradient"] == gradient  # a line is painted from fill, other series from stroke.fill
    stops = gradient["colorStops"][0]
    # three segments of a third each, a hard edge between them: the highest rate is green, the lowest red
    assert [stop["offset"] for stop in stops] == [0.0, 33.333, 33.333, 66.667, 66.667, 100.0]
    assert [stop["color"] for stop in stops[::2]] == [gradient_color(1.0), gradient_color((5 - 1) / 9), gradient_color(0.0)]
    # no colours when the rates do not differ, a rate is missing or a value is missing
    flat = [{"label": "a", "y": 1.0}, {"label": "b", "y": 2.0, "rate": 3.0}, {"label": "c", "y": 3.0, "rate": 3.0}]
    assert category_chart("p", "x", flat, gradient="segments")["gradient"] is False
    missing = [{"label": "a", "y": 1.0}, {"label": "b", "y": None, "rate": None}, {"label": "c", "y": 3.0, "rate": 2.0}]
    assert category_chart("p", "x", missing, gradient="segments")["gradient"] is False
    assert "fill" not in category_chart("p", "x", flat, gradient="segments")["apex_config"]
    # equal values (the profits are rounded) have no height to colour even when the rates differ
    level = [{"label": "a", "y": 1.0}, {"label": "b", "y": 1.0, "rate": 3.0}, {"label": "c", "y": 1.0, "rate": 1.0}]
    assert category_chart("p", "x", level, gradient="segments")["gradient"] is False


def test_the_interval_line_and_dots_are_coloured_by_their_value() -> None:
    points = [
        {"label": "0–10", "y": 10.0, "best": True}, {"label": "10–20", "y": 5.0}, {"label": "20–30", "y": -2.0},
    ]
    chart = category_chart("interval", "x", points, gradient="values")
    assert chart["gradient"] is True
    gradient = chart["apex_config"]["fill"]["gradient"]
    assert gradient["type"] == "vertical"
    top, bottom = gradient["colorStops"][0]
    assert (top["offset"], top["color"]) == (0, gradient_color(1.0)) and (bottom["offset"], bottom["color"]) == (100, gradient_color(0.0))
    markers = chart["apex_config"]["markers"]["discrete"]
    assert [marker["dataPointIndex"] for marker in markers] == [0, 1, 2]
    assert [marker["fillColor"] for marker in markers] == [gradient_color(1.0), gradient_color(7 / 12), gradient_color(0.0)]
    assert [marker["size"] for marker in markers] == [6, 4, 4]  # the best one is larger
    # one value, or all equal: no colours, the plain line
    assert category_chart("p", "x", points[:1], gradient="values")["gradient"] is False
    assert category_chart("p", "x", [{"label": "a", "y": 1.0}, {"label": "b", "y": 1.0}], gradient="values")["gradient"] is False
    assert category_chart("p", "x", points)["gradient"] is False


def chart_data() -> dict:
    result = view(simulation(module_comparison=module_comparison()))["module"]
    return result["payback_chart"]


def test_the_payback_chart_has_a_line_per_setup_and_a_point_at_each_payback() -> None:
    data = chart_data()
    chart = payback_chart(data, "years")
    assert len(chart["series"]) == len(data["lines"])
    horizon = data["horizon"]
    assert chart["days"] == horizon + 2

    years = sorted({0.0, float(horizon), *(float(marker["x"]) for marker in data["markers"])})
    for series, line in zip(chart["series"], data["lines"]):
        points = points_of(series)
        # the same years in every line (so the tooltip of a marker shows all lines), on the straight line of the setup
        assert [point[0] for point in points] == [1 + year for year in years]
        assert points[0][1] == pytest.approx(line["points"][0][1], abs=0.01)
        assert points[-1][1] == pytest.approx(line["points"][1][1], abs=0.01)
        assert series["name"] == line["legend"]
    first, rest = chart["series"][0], chart["series"][1:]
    assert first["color"] == NONE_COLOR and first["stroke_dash"] == 6  # without a battery: grey and dashed
    assert len({series["color"] for series in rest}) == len(rest)
    assert all(series["stroke_width"] == 3 and series["curve"] == "straight" for series in chart["series"])

    # a green marker on the line without a battery where a setup has paid back, named after the setup
    markers = chart["apex_config"]["markers"]["discrete"]
    assert len(markers) == len(data["markers"]) > 0
    tooltips = dict((day, text) for day, text in tooltips_of(chart))
    for marker, spec in zip(markers, data["markers"]):
        assert marker["seriesIndex"] == 0 and marker["fillColor"] == BEST_COLOR
        assert years[marker["dataPointIndex"]] == pytest.approx(spec["x"])
        assert tooltips[1 + spec["x"]] == spec["name"]
    assert tooltips[1.0] == "0 years" and tooltips[1 + float(horizon)] == f"{horizon:g} years"

    labels = labels_of(chart)
    assert labels[1] == "0" and labels[1 + horizon] == f"{horizon:g}" and len(labels) == chart["days"] + 1
    assert chart["apex_config"]["chart"]["height"] == "560px"  # twice as high as the other charts
    assert_plain_javascript(chart)


def test_the_payback_chart_is_not_drawn_without_numbers() -> None:
    data = chart_data()
    assert payback_chart({**data, "lines": []}, "years") is None
    assert payback_chart({**data, "horizon": None}, "years") is None
    broken = {**data, "lines": [{**data["lines"][0], "points": [[0, 0.0], [30, None]]}, *data["lines"][1:]]}
    assert payback_chart(broken, "years") is None
    # a marker outside the plotted years is not drawn
    far = {**data, "markers": [{"name": "far", "x": 99.0, "y": 1.0}]}
    assert payback_chart(far, "years")["apex_config"]["markers"]["discrete"] == []


def test_the_view_has_the_charts_only_when_they_can_be_drawn() -> None:
    sizes = [
        {"battery_size": 0.0, "profit": 0.0, "bill": 1000.0},
        {"battery_size": 10.0, "profit": 100.0, "bill": 900.0},
        {"battery_size": 20.0, "profit": 150.0, "bill": 850.0},
    ]
    result = view(simulation(range=sizes, module_comparison=module_comparison()))
    profit = result["range"]["profit_chart"]
    assert labels_of(profit)[1:4] == ["0.0", "10.0", "20.0"] and len(points_of(profit["series"][0])) == 3
    assert profit["apex_config"]["markers"]["discrete"][0]["dataPointIndex"] == 2  # the highest profit
    # the profit per added kWh of every interval, named by the sizes it compares
    interval = result["range"]["interval_chart"]
    assert labels_of(interval)[1:3] == ["0.0–10.0", "10.0–20.0"]
    assert tooltips_of(interval) == [[1, "0.0–10.0 kWh"], [2, "10.0–20.0 kWh"]]
    assert points_of(interval["series"][0]) == [[1, 10.0], [2, 5.0]]
    assert result["module"]["payback_apex"]["series"]
    # the profit per added kWh is 10 then 5: the profit line is coloured per segment and the interval line by value
    assert profit["gradient"] is True and interval["gradient"] is True

    # one size: no interval to plot
    single = view(simulation(range=sizes[:1]))["range"]
    assert single["profit_chart"] is not None and single["interval_chart"] is None
    # without a known price the payback chart is not drawn
    comparison = module_comparison(module_price=None)
    assert view(simulation(module_comparison=comparison))["module"]["payback_apex"] is None


@pytest.mark.parametrize("language", ["da", "de", "en", "pl", "uk"])
def test_the_series_names_follow_the_language(language: str) -> None:
    sizes = [{"battery_size": 0.0, "profit": 0.0, "bill": 1.0}, {"battery_size": 5.0, "profit": 10.0, "bill": 2.0}]
    result = view(simulation(range=sizes, module_comparison=module_comparison()), language)
    names = [result["range"]["profit_chart"]["series"][0]["name"], result["range"]["interval_chart"]["series"][0]["name"]]
    assert all(name and "dashboard." not in name for name in names) and names[0] != names[1]
