"""Constants and pure functions of the analysis."""
from __future__ import annotations

import datetime
import math
from statistics import median
from typing import Callable

from ...utils.helpers import reset_time_to_hour


ANALYSIS_DAYS = 400  # days kept in the database


# raised when the backfill reads more: the days from the statistics are read again (2: prices with state_class total,
# 3: the energy of the days recorded before FLOWS_VERSION is read again from the statistics)
ANALYSIS_BACKFILL_VERSION = 3


# the energy of a recorded day (charged, discharged ...) is measured with the states held until the next change;
# days without this marker were measured with lines between the rows, which counted a battery that blinks on a
# full battery as hours of charging: the backfill corrects them from the long-term statistics (the hourly means)
FLOWS_VERSION = 2


# a recorded day stays as it is (with its score) when the energy from the statistics differs less than this (kWh) in
# charged and discharged: its hours are no longer there, and the score needs the sell prices, which the statistics
# don't have
FLOWS_SCORE_TOLERANCE_KWH = 0.25


ANALYSIS_MONTHS = 13  # months published


ANALYSIS_LIST_DAYS = 31  # days published for the daily charts


ANALYSIS_TABLE_DAYS = 14  # days published for the daily tables


EXPENSIVE_HOURS = 4  # the most expensive hours of a day that are checked for an empty battery


EMPTY_MARGIN = 2.0  # % above the minimum battery level that counts as empty


SCORE_STEP_KWH = 0.05  # the battery level step of the hindsight optimisation (kWh); big moves are whole steps, small ones are exact


SCORE_STEPS_MIN = 60  # battery levels in the hindsight optimisation at least (a small battery)


SCORE_STEPS_MAX = 200  # and at most (a big battery): the run time grows with the square of it


SCORE_VERSION = 3  # raised when the hindsight model changes: older scores are not published (2: efficiency per power, profit requirements; 3: level neutral baseline, exact small moves, ignored consumers left out, standby, real hourly mean prices)


SCORE_MIN_POSSIBLE = 0.05  # currency: a day with less possible saving than this (or than its stored "noise") has no score of its own


SCORE_TARGET_MARGIN = 0.05  # share of the usable battery the energy the day's plan nets may lie outside the battery's range before the day is not scored


SCORE_MAX_DRIFT = 0.3  # share of the usable battery the day's energy balance may be off by (a level jump, a missed hour) before the day is not scored


SCORE_GAP_MIN_POINTS = 80  # of the 100 points of an hour a battery power or consumption sensor needs numbers for (fewer: it was lost, the hour is not scored)


LOSS_DAY_MIN_CHARGED = 0.2  # battery sizes a day has to charge for its measured loss


LOSS_OUTLIER_MADS = 3.0  # a day whose loss is more than this many (scaled) median absolute deviations from the median is left out


LOSS_OUTLIER_MIN_DAYS = 10  # days with a measured loss needed before outliers are left out


FORECAST_TYPES = ("ema", "trend", "average")


FORECAST_AUTO_DAYS = 30  # days the automatic forecast type is judged over (switch auto_forecast_type)


FORECAST_AUTO_MIN_DAYS = 7  # days with a recorded forecast needed before the type is chosen automatically


FORECAST_AUTO_MARGIN = 0.05  # how much smaller another type's error must be before the automatic choice changes


# rules that charge from the grid / that discharge (sell) the battery, see CHARGING_TYPES
CHARGE_RULES = (
    "cheapest_hour_fill_planner", "most_expensive_planner", "needed_before_max_level_planner",
    "negative_price_planner", "full_charge_planner", "grid_charge_to_sell",
)


SALE_RULES = (
    "most_profit", "sell_excess_kwh_available", "force_powerwall_max_hours_old_discharge",
    "discharge_before_solar", "grid_charge_to_sell",
)


DAY_SUMS = (
    "home", "ignored", "solar", "charged", "discharged", "solar_home", "battery_home", "grid_home",
    "solar_battery", "grid_battery", "export_solar", "export_battery", "solar_surplus",
)


PRICE_FORECAST_DAYS = 7  # days ahead a price forecast is recorded for


PRICE_HOURS_DAYS = 7  # days the hourly forecasts and real prices are kept


PRICE_CHART_DAYS = 2  # past days published for the hourly price chart


PRICE_CHART_LEADS = tuple(range(1, PRICE_FORECAST_DAYS + 1))  # days ahead shown in the charts


PRICE_DAY_MIN_HOURS = 20  # forecast hours needed to judge a day


PRICE_RANK_HOURS = 4  # the cheapest / most expensive hours of a day the forecast should find


PRICE_CORRECTION_DAYS = 30  # days the bias of the price forecast is measured over


PRICE_CORRECTION_MIN_DAYS = 7  # days needed per days ahead before the forecast is corrected


PRICE_CORRECTION_MAX = 0.5  # currency/kWh the forecast is corrected by at most


MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _r(value, decimals: int = 3):
    return round(float(value), decimals) if _is_num(value) else None


def split_flows(home: float, solar: float, charge: float, discharge: float) -> dict[str, float]:
    """kWh of an hour by source: solar first to the house, then the battery, the rest from the grid."""
    solar_home = min(solar, home)
    battery_home = min(discharge, home - solar_home)
    solar_left = solar - solar_home
    solar_battery = min(charge, solar_left)
    return {
        "solar_home": solar_home,
        "battery_home": battery_home,
        "grid_home": max(home - solar_home - battery_home, 0.0),
        "solar_battery": solar_battery,
        "grid_battery": charge - solar_battery,
        "export_solar": solar_left - solar_battery,
        "export_battery": discharge - battery_home,
    }


def grid_cost(kwh: float, buy: float, sell: float) -> float:
    """Cost of kWh from the grid (negative kWh: sold at the sell price)."""
    return kwh * buy if kwh > 0 else kwh * sell


def score_steps(usable: float) -> int:
    """Battery levels of the hindsight optimisation for a battery with this usable capacity (kWh)."""
    return min(max(math.ceil(usable / SCORE_STEP_KWH), SCORE_STEPS_MIN), SCORE_STEPS_MAX)


def hindsight_costs(rows: list[dict], start: float, ends: list[float], low: float, high: float,
                    max_charge: float, max_discharge: float, charge_efficiency: Callable[[float], float],
                    discharge_efficiency: Callable[[float], float], wear: float, min_profit_per_kwh: float = 0.0,
                    min_total_profit: float = 0.0, min_sell_kwh: float = 0.0) -> list[float | None]:
    """Lowest grid cost of a day with perfect knowledge of consumption, solar and prices, per end level.

    rows: per hour {"load": kWh house - solar (negative = surplus), "buy", "sell"}. The battery
    starts with start kWh, ends with at least each of ends (kWh) and stays within low..high; charging and
    discharging are limited per hour (AC kWh) and lose the efficiency at that hour's AC kWh
    (charge_efficiency / discharge_efficiency(kWh) like the optimiser's curves), wear is paid per
    kWh charged. Like the optimiser, min_profit_per_kwh (per kWh out of the battery) and
    min_total_profit (per hour selling to the grid) only steer the choice, and a
    sale needs min_sell_kwh: the result is the real cost of the cheapest plan BattMind may make.

    Big moves are whole level steps (SCORE_STEP_KWH). Two exact moves are added in every hour, so a load or
    a surplus smaller than a step is neither lost nor invented: the battery covers the whole house load (a
    load that is not sold, so min_sell_kwh doesn't apply), or stores the whole solar surplus. The part of
    the battery level that is not a whole step is carried with the level.
    """
    nothing: list[float | None] = [None] * len(ends)
    usable = high - low
    if usable <= 0.01 or (max_charge <= 0.0 and max_discharge <= 0.0):
        return nothing

    def ac_kwh(battery_kwh: float, efficiency: Callable[[float], float], charging: bool) -> float | None:
        """AC kWh of battery_kwh at the efficiency of the AC kWh itself (two fixed point passes)."""
        ac = battery_kwh
        for _ in range(2):
            value = efficiency(ac)
            if value <= 0.0:
                return None
            ac = battery_kwh / value if charging else battery_kwh * value
        return ac

    steps = score_steps(usable)
    step = usable / steps
    # AC kWh per number of battery level steps up (charged) and down (delivered) within an hour
    ups, downs = [0.0], [0.0]
    for moves, efficiency, charging, limit in ((ups, charge_efficiency, True, max_charge), (downs, discharge_efficiency, False, max_discharge)):
        while len(moves) <= steps:
            ac = ac_kwh(len(moves) * step, efficiency, charging)
            if ac is None or ac > limit + 1e-9:
                break
            moves.append(ac)
    # per battery level step: (objective incl. the profit requirements, real cost, kWh above the step) or None
    states: list[tuple[float, float, float] | None] = [None] * (steps + 1)
    start_rel = min(max(start - low, 0.0), usable)
    level = min(int(start_rel / step + 1e-9), steps)
    states[level] = (0.0, 0.0, max(start_rel - level * step, 0.0))
    for row in rows:
        new: list[tuple[float, float, float] | None] = [None] * (steps + 1)
        load, buy, sell = row["load"], row["buy"], row["sell"]
        idle = grid_cost(load, buy, sell)
        # the exact moves: (battery kWh, objective, real cost)
        cover = absorb = None
        if 0.01 < load <= max_discharge + 1e-9:
            efficiency = discharge_efficiency(load)
            if efficiency > 0.0:
                cover = (load / efficiency, load * min_profit_per_kwh)  # the house load is delivered, nothing is sold
        elif load < -0.01 and max_charge > 0.0:
            ac = min(-load, max_charge)
            efficiency = charge_efficiency(ac)
            if efficiency > 0.0:
                absorb = (ac * efficiency, grid_cost(load + ac, buy, sell) + wear * ac)
        for i, state in enumerate(states):
            if state is None:
                continue
            objective, cost, carry = state
            for j in range(max(i - len(downs) + 1, 0), min(i + len(ups) - 1, steps) + 1):
                if j == steps and carry > 1e-9:
                    continue  # the level step above the top has no room for the part of a step
                if j > i:
                    ac = ups[j - i]
                    real = grid_cost(load + ac, buy, sell) + wear * ac
                    penalty = 0.0
                elif j < i:
                    ac = downs[i - j]
                    sold = ac - max(load, 0.0)
                    if 0.01 < sold < min_sell_kwh:
                        continue
                    real = grid_cost(load - ac, buy, sell)
                    penalty = ac * min_profit_per_kwh + (min_total_profit if sold > 0.01 else 0.0)
                else:
                    real, penalty = idle, 0.0
                total = objective + real + penalty
                known = new[j]
                # the cheaper plan to a level; of equal plans the one that kept more energy (a step that wastes a part of itself is not it)
                if known is None or total < known[0] - 1e-9 or (total < known[0] + 1e-9 and carry > known[2]):
                    new[j] = (total, cost + real, carry)
            energy = i * step + carry
            if carry > 1e-9 and max_charge > 0.0:
                # the top of the range is not on the grid of whole steps: fill the battery exactly
                room = usable - energy
                ac = ac_kwh(room, charge_efficiency, True) if room > 1e-9 else None
                if ac is not None and ac <= max_charge + 1e-9:
                    real = grid_cost(load + ac, buy, sell) + wear * ac
                    total = objective + real
                    known = new[steps]
                    if known is None or total < known[0] - 1e-9:
                        new[steps] = (total, cost + real, 0.0)
            if cover is not None:
                left = energy - cover[0]
                if left >= -1e-9:
                    left = max(left, 0.0)
                    j = min(int(left / step + 1e-9), steps)
                    total = objective + cover[1]
                    known, kept = new[j], max(left - j * step, 0.0)
                    if known is None or total < known[0] - 1e-9 or (total < known[0] + 1e-9 and kept > known[2]):
                        new[j] = (total, cost, kept)
            if absorb is not None:
                grown = energy + absorb[0]
                if grown <= usable + 1e-9:
                    grown = min(grown, usable)
                    j = min(int(grown / step + 1e-9), steps)
                    total = objective + absorb[1]
                    known, kept = new[j], max(grown - j * step, 0.0)
                    if known is None or total < known[0] - 1e-9 or (total < known[0] + 1e-9 and kept > known[2]):
                        new[j] = (total, cost + absorb[1], kept)
        states = new
    results: list[float | None] = []
    for end in ends:
        required = min(max(end - low, 0.0), usable) - 1e-9
        best = None
        for level, state in enumerate(states):
            if state is not None and level * step + state[2] >= required and (best is None or state[:2] < best):
                best = state[:2]
        results.append(None if best is None else best[1])
    return results


def hindsight_cost(rows: list[dict], start: float, end: float, low: float, high: float,
                   max_charge: float, max_discharge: float, charge_efficiency: Callable[[float], float],
                   discharge_efficiency: Callable[[float], float], wear: float, min_profit_per_kwh: float = 0.0,
                   min_total_profit: float = 0.0, min_sell_kwh: float = 0.0) -> float | None:
    """hindsight_costs() for one end level."""
    return hindsight_costs(
        rows, start, [end], low, high, max_charge, max_discharge, charge_efficiency, discharge_efficiency, wear,
        min_profit_per_kwh=min_profit_per_kwh, min_total_profit=min_total_profit, min_sell_kwh=min_sell_kwh,
    )[0]


def day_score(hours: list[dict], start: float, end: float, low: float, high: float, levels: list[float],
              max_charge: float, max_discharge: float, charge_efficiency: Callable[[float], float],
              discharge_efficiency: Callable[[float], float], wear: float, min_profit_per_kwh: float = 0.0,
              min_total_profit: float = 0.0, min_sell_kwh: float = 0.0) -> tuple[dict | None, str | None]:
    """The grid cost of a closed day as it was, without the battery and with the best possible battery use: (score, None),
    or (None, why the day can not be scored).

    hours: per hour, all kWh: {"use": the house (without the ignored consumers), "solar", "ignored": what the ignored
    consumers and the chargers on their own meters took, "charge", "discharge": the battery's AC energy, "buy": the
    price, "sell": what a sold kWh is worth (sell_value())}. start/end/low/high/levels are battery levels in kWh (levels:
    every level measured in the day, which widens low..high where the battery went outside the configured limits).

    The ignored consumers (and the CJP chargers) are not part of the score. What they took from the solar surplus, then from
    the battery, is taken out of the day's solar and discharge, and the battery energy it was is not asked of the best
    possible plan either.

    The best possible plan has to net the same battery energy as the day did, by the efficiencies of the model: the
    battery's standby consumption and the model's own error are the day's loss, which both plans have. That makes the
    baseline "without battery" comparable: optimal_neutral is the best possible cost when the battery ends where it
    started (the saving the battery can make in a day), and actual - optimal (the price of the plan's mistakes) is
    what the published score subtracts from it.
    """
    low, high = min([low, *levels]), max([high, *levels])
    usable = high - low
    if usable <= 0.01:
        return None, "battery"
    loads: list[dict] = []
    stored = taken = given = no_battery = actual = 0.0
    for hour in hours:
        use, solar = hour["use"], hour["solar"]
        charge, discharge = hour["charge"], hour["discharge"]
        # the ignored consumers take the solar surplus first, then the battery, then the grid
        from_solar = min(hour["ignored"], max(solar - use, 0.0))
        from_battery = min(max(discharge - max(use - solar, 0.0), 0.0), hour["ignored"] - from_solar)
        load = use - solar + from_solar
        buy, sell = hour["buy"], hour["sell"]
        if charge > 0.0:
            efficiency = charge_efficiency(charge)
            if efficiency <= 0.0:
                return None, "battery"
            stored += charge * efficiency
        if discharge > 0.0:
            efficiency = discharge_efficiency(discharge)
            if efficiency <= 0.0:
                return None, "battery"
            taken += discharge / efficiency
            given += from_battery / efficiency
        loads.append({"load": load, "buy": buy, "sell": sell})
        no_battery += grid_cost(load, buy, sell)
        actual += grid_cost(load + charge - (discharge - from_battery), buy, sell) + wear * charge
    # what the flows say the battery gained against what it did: a jump of the level or a missed hour is not a loss
    if abs(start + stored - taken - end) > SCORE_MAX_DRIFT * usable:
        return None, "drift"
    target = start + stored - (taken - given)
    # the plan nets more energy than the battery has room for (an ignored consumer took the energy it refilled), or less than it can
    # hold: the best plan can not be asked for it, and clamping it would bill the day's plan for what it can not do
    margin = SCORE_TARGET_MARGIN * usable
    if target > high + margin or target < low - margin:
        return None, "drift"
    target = min(max(target, low), high)
    optimal, neutral = hindsight_costs(
        loads, start, [target, start], low, high, max_charge, max_discharge, charge_efficiency, discharge_efficiency, wear,
        min_profit_per_kwh=min_profit_per_kwh, min_total_profit=min_total_profit, min_sell_kwh=min_sell_kwh,
    )
    if optimal is None or neutral is None:
        return None, "battery"
    return {
        "actual": round(actual, 3),
        "no_battery": round(no_battery, 3),
        "optimal": round(min(optimal, actual), 3),
        "optimal_raw": round(optimal, 3),
        "optimal_neutral": round(min(neutral, no_battery), 3),
        # what the rounding of the model can be worth in a day (a few level steps at the dearest price): less possible than this is no score
        "noise": round(2.0 * (usable / score_steps(usable)) * max((abs(hour["buy"]) for hour in hours), default=0.0), 3),
        "v": SCORE_VERSION,
    }, None


def score_figures(score: dict) -> tuple[float, float]:
    """(saved, possible) of a day's stored score (day_score): possible is what the battery could have saved with the level at its
    start (without battery minus the best plan that ends where it started), saved is that minus what the day's plan lost against the
    best plan with the day's own end level (actual minus optimal). The score is saved / possible."""
    possible = _num(score.get("no_battery")) - _num(score.get("optimal_neutral"))
    return possible - (_num(score.get("actual")) - _num(score.get("optimal"))), possible


def measured_loss(charged: float, discharged: float, stored: float, charge_efficiency: float) -> float | None:
    """Round trip loss of AC kWh charged and discharged while the battery gained stored kWh (battery side).

    The level change is counted as the AC kWh it took to charge it (stored / charge efficiency), so
    a day that empties or fills the battery is neither too lossy nor too efficient.
    """
    if charge_efficiency <= 0.0:
        return None
    charged_used = charged - stored / charge_efficiency
    return round(1 - discharged / charged_used, 3) if charged_used > 0.0 else None


def loss_days(days: dict[str, dict], kwh_per_percent: float, charge_efficiency: float, min_charged: float) -> tuple[dict[str, tuple[float, float, float]], set[str]]:
    """The days with a measured loss {date: (charged, discharged, stored kWh)} and the dates left out as outliers.

    A day needs min_charged kWh charged and its start and end level. With LOSS_OUTLIER_MIN_DAYS such
    days, a day whose loss is further than LOSS_OUTLIER_MADS scaled median absolute deviations from
    the median (e.g. a jump of the battery level or a day the battery hardly ran) is an outlier.
    """
    values: dict[str, tuple[float, float, float]] = {}
    losses: dict[str, float] = {}
    for date, day in days.items():
        charged, discharged = _num(day.get("charged")), _num(day.get("discharged"))
        if charged < min_charged or not _is_num(day.get("soc_start")) or not _is_num(day.get("soc_end")):
            continue
        stored = (day["soc_end"] - day["soc_start"]) * kwh_per_percent
        loss = measured_loss(charged, discharged, stored, charge_efficiency)
        if loss is not None:
            values[date] = (charged, discharged, stored)
            losses[date] = loss
    outliers: set[str] = set()
    if len(losses) >= LOSS_OUTLIER_MIN_DAYS:
        middle = median(losses.values())
        spread = median(abs(loss - middle) for loss in losses.values())
        limit = LOSS_OUTLIER_MADS * 1.4826 * max(spread, 0.01)
        outliers = {date for date, loss in losses.items() if abs(loss - middle) > limit}
    return values, outliers


def summarize_day(rows: list[dict], soc_start: float | None, minimum: float) -> dict:
    """The hours of a day (sorted) added up."""
    day = dict.fromkeys(DAY_SUMS, 0.0)
    price_sum = price_hours = 0.0
    errors = {kind: [0.0, 0.0] for kind in FORECAST_TYPES}  # [sum |error|, sum error]
    predicted_hours = 0
    predicted_use = 0.0
    if soc_start is None and rows and _is_num(rows[0].get("soc")):
        # no previous day: the level at the end of the first hour
        soc_start = rows[0]["soc"]
    level = soc_start
    expensive: list[tuple[float, float | None, dict, dict]] = []
    for row in rows:
        home, solar = _num(row.get("home")), _num(row.get("solar"))
        charge, discharge = _num(row.get("charge")), _num(row.get("discharge"))
        flows = split_flows(home, solar, charge, discharge)
        for key, value in flows.items():
            day[key] += value
        day["home"] += home
        day["ignored"] += _num(row.get("ignored"))
        day["solar"] += solar
        day["charged"] += charge
        day["discharged"] += discharge
        day["solar_surplus"] += max(solar - _num(row.get("use")), 0.0)
        if _is_num(row.get("buy")):
            price_sum += row["buy"]
            price_hours += 1
            expensive.append((row["buy"], level, flows, row))
        prediction = row.get("pred") or {}
        if all(_is_num(prediction.get(kind)) for kind in FORECAST_TYPES):
            predicted_hours += 1
            predicted_use += _num(row.get("use"))
            for kind in FORECAST_TYPES:
                error = prediction[kind] - _num(row.get("use"))
                errors[kind][0] += abs(error)
                errors[kind][1] += error
        if _is_num(row.get("soc")):
            level = row["soc"]

    result = {key: round(value, 3) for key, value in day.items()}
    result["hours"] = len(rows)
    result["price_sum"] = round(price_sum, 3)
    result["price_hours"] = int(price_hours)
    result["soc_start"] = _r(soc_start, 1)
    result["soc_end"] = _r(level, 1)
    # the level at the end of the day is the one of its last hour: after a lost sensor in that hour the next day does not start from it
    result["soc_exact"] = bool(rows) and _is_num(rows[-1].get("soc"))
    if predicted_hours:
        result["forecast"] = {
            "hours": predicted_hours,
            "use": round(predicted_use, 3),
            **{kind: [round(errors[kind][0], 3), round(errors[kind][1], 3)] for kind in FORECAST_TYPES},
        }
    # the most expensive hours: was the battery empty while the house used grid power?
    empty = [
        (price, flows["grid_home"]) for price, start_level, flows, _ in sorted(expensive, key=lambda item: item[0], reverse=True)[:EXPENSIVE_HOURS]
        if start_level is not None and start_level <= minimum + EMPTY_MARGIN and flows["grid_home"] >= 0.05
    ]
    if expensive:
        result["expensive"] = {
            "hours": len(empty),
            "kwh": round(sum(kwh for _, kwh in empty), 3),
            "cost": round(sum(price * kwh for price, kwh in empty), 3),
        }
    return result


def forecast_errors(selected: list[dict]) -> dict | None:
    """The error of each consumption forecast type over the days (summarize_day's "forecast").

    days: the days with a recorded forecast, hours/actual: their hours and the measured kWh per
    hour; per type mae (kWh per hour), bias (> 0: too much), relative (mae / consumption) and
    forecast (what it expected per hour on average). None without a recorded forecast.
    """
    forecasts = [day["forecast"] for day in selected if isinstance(day.get("forecast"), dict) and _num(day["forecast"].get("hours"))]
    hours = sum(_num(forecast.get("hours")) for forecast in forecasts)
    use = sum(_num(forecast.get("use")) for forecast in forecasts)
    if not hours:
        return None
    result = {"days": len(forecasts), "hours": int(hours), "actual": round(use / hours, 3)}
    for kind in FORECAST_TYPES:
        absolute = sum(_num((forecast.get(kind) or [0, 0])[0]) for forecast in forecasts)
        bias = sum(_num((forecast.get(kind) or [0, 0])[1]) for forecast in forecasts)
        result[kind] = {
            "mae": round(absolute / hours, 3),
            "bias": round(bias / hours, 3),
            "relative": round(absolute / use, 3) if use > 0 else None,
            "forecast": round((use + bias) / hours, 3),
        }
    return result


def auto_forecast_choice(errors: dict | None, current: str, min_count: int, count_key: str) -> str | None:
    """The forecast type to use automatically: current, or another type whose error is
    FORECAST_AUTO_MARGIN smaller (so near-equal types don't swap back and forth).

    None while errors[count_key] (days or drives) is below min_count.
    """
    if not errors or _num(errors.get(count_key)) < min_count:
        return None
    if current not in FORECAST_TYPES:
        current = "ema"
    best = min(FORECAST_TYPES, key=lambda kind: errors[kind]["mae"])
    if errors[best]["mae"] < errors[current]["mae"] * (1 - FORECAST_AUTO_MARGIN):
        return best
    return current


def hourly_prices(prices: dict) -> dict[datetime.datetime, float]:
    """Prices per hour (15 minute prices averaged)."""
    sums: dict[datetime.datetime, list[float]] = {}
    for timestamp, price in prices.items():
        if isinstance(timestamp, datetime.datetime) and _is_num(price):
            bucket = sums.setdefault(reset_time_to_hour(timestamp), [0.0, 0.0])
            bucket[0] += price
            bucket[1] += 1
    return {hour: total / count for hour, (total, count) in sums.items()}


def evaluate_price_day(rows: list[dict]) -> dict[str, dict]:
    """The forecast of a day's hours against the real prices, per days ahead.

    rows: per hour {"real": price, "<days ahead>": forecast}. n hours, abs (sum |error|),
    bias (sum error, > 0: forecast too high), max (worst hour); cheap/dear: how many of the
    PRICE_RANK_HOURS cheapest/most expensive hours by the forecast really were, cheap_miss/
    dear_miss: currency/kWh lost on average by picking those hours instead of the real ones.
    """
    result: dict[str, dict] = {}
    rows = [row for row in rows if _is_num(row.get("real"))]
    for lead in range(1, PRICE_FORECAST_DAYS + 1):
        pairs = [(row[str(lead)], row["real"]) for row in rows if _is_num(row.get(str(lead)))]
        if len(pairs) < PRICE_DAY_MIN_HOURS:
            continue
        errors = [forecast - real for forecast, real in pairs]
        count = min(PRICE_RANK_HOURS, len(pairs))

        def ranked(by_forecast: bool, reverse: bool) -> list[int]:
            return sorted(range(len(pairs)), key=lambda i: pairs[i][0 if by_forecast else 1], reverse=reverse)[:count]

        def real_sum(indexes: list[int]) -> float:
            return sum(pairs[i][1] for i in indexes)

        cheap, cheapest = ranked(True, False), ranked(False, False)
        dear, dearest = ranked(True, True), ranked(False, True)
        result[str(lead)] = {
            "n": len(pairs),
            "abs": round(sum(abs(error) for error in errors), 3),
            "bias": round(sum(errors), 3),
            "max": round(max(abs(error) for error in errors), 3),
            "cheap": len(set(cheap) & set(cheapest)),
            "cheap_miss": round((real_sum(cheap) - real_sum(cheapest)) / count, 3),
            "dear": len(set(dear) & set(dearest)),
            "dear_miss": round((real_sum(dearest) - real_sum(dear)) / count, 3),
        }
    return result


def rules_of_entries(entries: list[dict]) -> dict[str, dict[str, float]]:
    """Charging history entries of a day per rule: charged kWh (c) and cost (cc), discharged kWh (d),
    their value (v: what the house did not buy + sales) and the savings (s)."""
    result: dict[str, dict[str, float]] = {}

    def add(rule: str, share: float, **values: float) -> None:
        bucket = result.setdefault(rule, {"c": 0.0, "cc": 0.0, "d": 0.0, "v": 0.0, "s": 0.0})
        for key, value in values.items():
            bucket[key] += value * share

    for entry in entries:
        rules = {key for key, value in (entry.get("rules") or {}).items() if value is True}
        charged = entry.get("charged") if isinstance(entry.get("charged"), dict) else {}
        discharged = entry.get("discharged") if isinstance(entry.get("discharged"), dict) else {}
        prices = entry.get("prices") if isinstance(entry.get("prices"), dict) else {}
        c_kwh = _num(charged.get("kWh"))
        if c_kwh > 0.0:
            cost = _num(charged.get("cost"))
            local = min(_num(charged.get("kWh_from_local_energy")), c_kwh)
            grid = c_kwh - local
            # the cost split like the charge price: solar at the sell price, the rest at the buy price
            weight_local = local * max(_num(prices.get("sell_price")), 0.0)
            weight_grid = grid * max(_num(prices.get("buy_price")), 0.0)
            local_cost = cost * weight_local / (weight_local + weight_grid) if weight_local + weight_grid > 0 else cost * local / c_kwh
            if local > 0.0:
                add("solar", 1.0, c=local, cc=local_cost)
            if grid > 0.0:
                charge_rules = [rule for rule in CHARGE_RULES if rule in rules] or ["no_rule"]
                for rule in charge_rules:
                    add(rule, 1 / len(charge_rules), c=grid, cc=cost - local_cost)
        d_kwh = _num(discharged.get("kWh"))
        if d_kwh > 0.0:
            savings = _num(discharged.get("savings"))
            value = savings + _num(discharged.get("cost"))
            sale_rules = [rule for rule in SALE_RULES if rule in rules] or (["force_discharge"] if "force_discharge" in rules else ["discharging"])
            for rule in sale_rules:
                add(rule, 1 / len(sale_rules), d=d_kwh, v=value, s=savings)
    return {rule: {key: round(value, 3) for key, value in bucket.items()} for rule, bucket in result.items()}
