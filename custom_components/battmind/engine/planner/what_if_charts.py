"""Chart configurations for the what-if view: the charts whose x-axis is not a time.

The chart card of the dashboard (apexcharts-card) always draws a time axis. A battery size or a number of years is
therefore drawn as a number of days: the first point is at day 1 (the window starts at day 0 and the card shows
`span: start: day`, so a tick is a midnight, i.e. a whole day), and the labels of the axis and of the tooltip turn the
day back into the size or the years. This keeps the hover tooltip of the card: the values are shown when the mouse is
over a point.

The view holds the finished pieces, so the card only plugs them in:

  {"days": the length of the window (graph_span), "series": [apexcharts-card series without entity],
   "apex_config": the options of the chart}

The strings of the formatters and of the data are JavaScript (apexcharts-card evaluates "EVAL:" options and
data_generator); they only use the JSON of the values and no template literals.
"""
from __future__ import annotations

import json
import math
from typing import Any

BEST_COLOR = "#388e3c"
LINE_COLOR = "#03a9f4"
NONE_COLOR = "#9e9e9e"
# the colour of a value from low to high (the gradient of the profit per added kWh): red to green
GRADIENT_LOW = (215, 92, 80)
GRADIENT_HIGH = (54, 162, 105)
# one colour and one stroke pattern per setup, so the lines can be told apart without colour (0 = a solid line)
COLORS = [
    "#03a9f4", "#e65100", "#388e3c", "#ad1457", "#6a1b9a", "#00838f",
    "#5d4037", "#c62828", "#558b2f", "#4527a0", "#00796b", "#9e5d00", "#455a64",
]
DASHES = [0, 6, 2, 8, 3, 5, 10, 4, 7, 9, 12, 1, 14]
NONE_DASH = 6
CHART_HEIGHT = "280px"
# the payback chart has long legends and several lines: twice as high
PAYBACK_HEIGHT = "560px"


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# the start of the window (the minimum of the axis) is the same in the data, the axis and the tooltip: the formatters
# read it from the chart and fall back to the one the data generator stored
_MIN_X = "((o&&o.w)||(t&&t.w)||{globals:{}}).globals.minX||(window.__bm||{})[KEY]"


def _axis_formatter(key: str, labels: list[str]) -> str:
    """Label of a tick: the label of the day when the tick is a whole day, otherwise nothing."""
    return (
        "EVAL:function(v,t,o){var T=typeof v==='number'?v:t,m=" + _MIN_X.replace("KEY", _json(key)) + ";"
        "if(!m||!isFinite(T))return '';var d=(T-m)/864e5,r=Math.round(d),L=" + _json(labels) + ";"
        "return Math.abs(d-r)<0.05&&L[r]?L[r]:'';}"
    )


def _tooltip_formatter(key: str, points: list[list[Any]]) -> str:
    """The title of the tooltip: the label of the point nearest to the mouse."""
    return (
        "EVAL:function(v,t,o){var T=typeof v==='number'?v:t,m=" + _MIN_X.replace("KEY", _json(key)) + ";"
        "if(!m||!isFinite(T))return '';var d=(T-m)/864e5,P=" + _json(points) + ",b=null;"
        "for(var i=0;i<P.length;i++){if(b===null||Math.abs(P[i][0]-d)<Math.abs(b[0]-d))b=P[i];}"
        "return b?b[1]:'';}"
    )


def _data_generator(key: str, data: list[list[Any]]) -> str:
    """The points as [timestamp, value]: day * 24 h after the start of the window."""
    return (
        "(window.__bm=window.__bm||{})[" + _json(key) + "]=start.getTime();"
        "return " + _json(data) + ".map(function(p){return [start.getTime()+p[0]*864e5,p[1]];});"
    )


def _marker(series: int, index: int, color: str = BEST_COLOR) -> dict[str, Any]:
    return {"seriesIndex": series, "dataPointIndex": index, "fillColor": color, "strokeColor": "#ffffff", "size": 6}


def gradient_color(ratio: float) -> str:
    """The colour at `ratio` (0 = lowest, 1 = highest value) as #rrggbb."""
    ratio = min(max(ratio, 0.0), 1.0)
    return "#" + "".join(f"{round(low + (high - low) * ratio):02x}" for low, high in zip(GRADIENT_LOW, GRADIENT_HIGH))


def _gradient(kind: str, stops: list[dict[str, Any]]) -> dict[str, Any]:
    """The options that colour the line of the (only) series.

    A line series is painted from `fill` by the chart card (apexcharts), the stroke options are for other series types:
    both are set, do not remove one of them.
    """
    gradient = {"type": kind, "colorStops": [stops], "opacityFrom": 1, "opacityTo": 1}
    return {"fill": {"type": "gradient", "gradient": gradient}, "stroke": {"fill": {"type": "gradient", "gradient": gradient}}}


def _segment_stops(rates: list[float]) -> list[dict[str, Any]]:
    """One colour per segment between two points (equally spaced), by its rate: low is red, high is green."""
    low, high = min(rates), max(rates)
    stops = []
    for index, rate in enumerate(rates):
        color = gradient_color((rate - low) / (high - low))
        for offset in (100 * index / len(rates), 100 * (index + 1) / len(rates)):
            stops.append({"offset": round(offset, 3), "color": color, "opacity": 1})
    return stops


def _apex_config(
    key: str, labels: list[str], points: list[list[Any]], markers: list[dict[str, Any]], days: int,
    height: str = CHART_HEIGHT, marker_size: int = 4,
) -> dict[str, Any]:
    return {
        "chart": {"height": height},
        "dataLabels": {"enabled": False},
        # only the discrete markers (the paybacks) are drawn when the size is 0; the hover marker shows the point
        "markers": {"size": marker_size, "strokeWidth": 0, "hover": {"size": 6}, "discrete": markers},
        "tooltip": {"shared": True, "intersect": False, "x": {"formatter": _tooltip_formatter(key, points)}},
        "xaxis": {
            "labels": {"formatter": _axis_formatter(key, labels), "rotate": 0, "rotateAlways": False},
            # one tick per day, the same ticks as the days of the labels (otherwise the ticks fall between the days)
            "tickAmount": days,
            "tooltip": {"enabled": False},
        },
    }


def category_chart(key: str, name: str, points: list[dict[str, Any]], gradient: str | None = None) -> dict[str, Any] | None:
    """One line through the points, the point i at day i + 1; None when no point has a finite value.

    A point is {label (under the axis), tooltip (title of the tooltip), y, best, rate}. A point without a finite y is
    left out of the line (never drawn as 0) but keeps its place and label on the axis.

    gradient colours the line like the earlier card did (red = lower, green = higher):
    "segments": each segment of the line by the `rate` of the point it ends at (the profit per added kWh);
    "values": the line and the dots by the value y. It only applies when every point has its numbers and they are not all
    equal; the result then has "gradient": True (the dashboard shows a note on the colours).
    """
    if not any(_finite(point.get("y")) for point in points):
        return None
    labels = [""] * (len(points) + 2)
    data: list[list[Any]] = []
    markers: list[dict[str, Any]] = []
    tooltips: list[list[Any]] = []
    for index, point in enumerate(points):
        day = index + 1
        labels[day] = str(point.get("label", ""))
        tooltips.append([day, str(point.get("tooltip", point.get("label", "")))])
        if _finite(point.get("y")):
            if point.get("best"):
                markers.append(_marker(0, len(data)))
            data.append([day, round(float(point["y"]), 4)])
    config = _apex_config(key, labels, tooltips, markers, len(points) + 1)
    colored = False
    ys = [point.get("y") for point in points]
    if gradient == "segments" and len(points) > 1 and all(_finite(y) for y in ys):
        rates = [point.get("rate") for point in points[1:]]
        # the profits are rounded: without a difference in height the line has no extent and is not drawn with a gradient
        if all(_finite(rate) for rate in rates) and max(rates) > min(rates) and max(ys) > min(ys):
            config.update(_gradient("horizontal", _segment_stops(rates)))
            colored = True
    elif gradient == "values" and len(points) > 1 and all(_finite(y) for y in ys) and max(ys) > min(ys):
        low, high = min(ys), max(ys)
        # a vertical gradient runs from the top (the highest value) to the bottom of the line
        config.update(_gradient("vertical", [
            {"offset": 0, "color": gradient_color(1.0), "opacity": 1},
            {"offset": 100, "color": gradient_color(0.0), "opacity": 1},
        ]))
        config["markers"]["discrete"] = [
            _marker(0, index, gradient_color((y - low) / (high - low))) if point.get("best")
            else {**_marker(0, index, gradient_color((y - low) / (high - low))), "size": 4, "strokeColor": "#ffffff"}
            for index, (point, y) in enumerate(zip(points, ys))
        ]
        colored = True
    return {
        "days": len(points) + 1,
        "gradient": colored,
        "series": [{
            "name": name, "color": LINE_COLOR, "type": "line", "curve": "straight", "stroke_width": 3,
            "extend_to": False,  # the card would otherwise add a point at the end of the window with the last value
            "data_generator": _data_generator(key, data),
        }],
        "apex_config": config,
    }


def _time_step(horizon: float) -> float:
    return next(step for step in (1, 2, 5, 10, horizon) if horizon / step <= 8 and horizon % step == 0)


def payback_chart(chart: dict[str, Any], years_text: str, key: str = "payback") -> dict[str, Any] | None:
    """The accumulated cost over time: one straight line per setup, each {legend, name, dashed, points}.

    chart is the "payback_chart" of the view: the horizon in years, the lines (two points each, 0 years and the
    horizon) and the markers {name, x, y} where a setup has paid back, which lie on the first line (no battery). The
    year y is at day y + 1. Every line gets a point at each marker so that the tooltip of the markers shows all lines.
    None when a value is not finite.
    """
    lines = chart.get("lines") or []
    horizon = chart.get("horizon")
    if not lines or not _finite(horizon) or horizon <= 0:
        return None
    markers = [
        marker for marker in chart.get("markers") or []
        if _finite(marker.get("x")) and _finite(marker.get("y")) and 0 <= marker["x"] <= horizon
    ]
    values = [point[1] for line in lines for point in line["points"]]
    if not values or not all(_finite(value) for value in values):
        return None
    years = sorted({0.0, float(horizon), *(float(marker["x"]) for marker in markers)})

    series = []
    for index, line in enumerate(lines):
        (_, start), (_, end) = line["points"]
        dashed = bool(line.get("dashed"))
        data = [[1 + year, round(start + (end - start) * year / horizon, 2)] for year in years]
        series.append({
            "name": line.get("legend") or line.get("name", ""),
            "color": NONE_COLOR if dashed else COLORS[max(index - 1, 0) % len(COLORS)],
            "type": "line", "curve": "straight", "stroke_width": 3, "extend_to": False,
            "stroke_dash": NONE_DASH if dashed else DASHES[max(index - 1, 0) % len(DASHES)],
            "data_generator": _data_generator(key, data),
        })

    days = math.ceil(horizon) + 2
    labels = [""] * (days + 1)
    step = _time_step(horizon) if float(horizon).is_integer() else horizon
    year = 0.0
    while year <= horizon + 1e-9:
        if float(year).is_integer():
            labels[1 + int(year)] = f"{year:g}"
        year += step
    tooltips = [[1 + year, f"{year:g} {years_text}"] for year in years]
    for marker in markers:
        # the point of a marker is named after the setup: the title of its tooltip
        tooltips = [point for point in tooltips if abs(point[0] - (1 + marker["x"])) > 1e-9]
        tooltips.append([1 + float(marker["x"]), str(marker["name"])])
    discrete = [_marker(0, years.index(float(marker["x"]))) for marker in markers]
    return {"days": days, "series": series, "apex_config": _apex_config(key, labels, tooltips, discrete, days, PAYBACK_HEIGHT, 0)}
