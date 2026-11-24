"""Whole horizon optimisation - dynamic programming over the battery level.

Instead of rules run one after another, every hour of the plan (days_to_predict) gets one
of the actions charge_if_needed() can execute, chosen so the total cost of the horizon is
as low as possible. One action per hour means the battery never charges and discharges in
the same hour. It controls the battery (switch optimizer_control) or is shown next to the
rules; the rule based plan is simulated with the same model, so the totals can be compared.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field, replace

from ..utils.efficiency import EfficiencyCurve, efficiency_at_power
from ..utils.taper import charge_energy_kwh, level_before_charge
from .storm import STORM_RULE, STORM_SHORTFALL_PENALTY

HOLD = "force_solar_only_charging"  # the house buys from the grid while the battery keeps its energy
ACTIONS =("discharge_allowed", "force_solar_only_charging", "grid_charging", "force_discharge", "blocked_solar_charge")
# charge_if_needed() replans every cron interval from the new battery level, so a charge hour
# stops when its planned kWh are in the battery, and force discharge runs per sell period
# (15 min): grid charging and selling can be a part of the hour
FRACTIONS = (0.25, 0.5, 0.75, 1.0)
# Blocking solar charging bets on the later sun filling the battery anyway. Only block when the
# battery is still filled with this part of the later solar forecast, so a forecast that is a bit
# too high doesn't stop and start the blocking every few minutes.
SOLAR_BLOCK_MARGIN = 0.8
# Rounds of optimize_with_rules(): each forbids the actions of rules that are switched off and optimises again. A long
# stretch of similar hours needs one round per hour (25 were seen); 40 leaves room, a plan that still isn't settled
# isn't used (the rules control the battery).
MAX_ROUNDS = 40
# Negative prices: a grid kWh must earn at least this much after the charging loss and the wear, even if it is never
# used (charge_kwh_profit(price, 0) >= 0.01 in the rule)
NEGATIVE_PRICE_MARGIN = -0.01


@dataclass
class OptimizerHour:
    timestamp: object
    buy: float  # currency/kWh
    sell: float  # currency/kWh
    consumption: float  # kWh used by the house (0 when the solar surplus covers it)
    solar: float  # kWh solar surplus after the house consumption
    can_sell: bool = True  # False in the excluded sell hours
    fraction: float = 1.0  # part of the hour left (the current hour); scales the charge/discharge rate
    forbidden: set = field(default_factory=set)  # actions not allowed (their rule switch is off)
    block_allowed: bool = True  # False: solar charging may not be blocked (it was released earlier in the hour)
    old_above: float | None = None  # % level above which the energy is older than max_age_hours (set between the rounds)
    # % level to have at the end of the hour for a storm warning (None: no level to keep). Below it nothing is sold, and
    # the DP pays STORM_SHORTFALL_PENALTY per kWh it is short (steering only, never in a cost or a total)
    floor: float | None = None
    # The highest storm level (%) of this and the later hours (None: none ahead). The storm level goes before the max. age of the
    # energy: old energy may be held when using the battery in this hour would end under it
    floor_ahead: float | None = None


@dataclass
class OptimizerConfig:
    battery_size: float  # kWh
    level_min: float  # %
    level_max: float  # %
    max_charge: float  # kWh per hour (AC)
    max_discharge: float  # kWh per hour (AC)
    efficiency: float  # one way, round trip = efficiency²
    wear: float  # currency per kWh out of the battery
    charge_efficiency_curve: EfficiencyCurve = ()
    discharge_efficiency_curve: EfficiencyCurve = ()
    curve_fallback_efficiency: float | None = None
    min_profit_per_kwh: float = 0.0  # steers the choice only: a kWh out of the battery has to earn this much
    min_sell_kwh: float = 0.0
    min_total_profit: float = 0.0  # steers the choice only: a sale hour (to the grid) has to earn this much
    usage_reserve: float = 0.0  # % kept for the house; force discharge may not export below it
    step: float = 0.5  # % resolution of the battery level
    unsupported: frozenset = frozenset()  # actions the inverter can't run (never planned)
    solar_charging: bool = True  # False: the solar surplus never charges the battery (all of it is sold)
    # Energy older than this is not held back (powerwall_max_hours_old, None = no limit). The battery
    # empties first in, first out: what is in it at the start of an hour is the latest charges, so the
    # old energy is the usable level minus the % charged within the max age (and minus the part of
    # unknown age, which is never old).
    max_age_hours: float | None = None
    charged_before: tuple = ()  # ((hour, % level added), ...) charged before the first hour (the charging history)
    unaged: float = 0.0  # % level of unknown age
    # ((band start %, cap kW), ...): the battery takes less than max_charge in these battery level bands (utils/taper.py), () = no taper.
    # An hour charges at the lowest cap of the bands it can reach; every charge in the optimiser goes through step_hour()
    charge_taper: tuple = ()
    # Full charge (number full_charge_recommended): the battery has to reach full_level % at some hour up to and
    # including the hour that starts at full_deadline (naive local, like the hours). None: no requirement (the plan is
    # exactly the one without it). It is never a hard constraint: when it can't be met it is dropped (full_dropped).
    full_deadline: datetime.datetime | None = None
    full_level: float | None = None


@dataclass
class OptimizerResult:
    actions: list[str] = field(default_factory=list)
    grid_kwh: list[float] = field(default_factory=list)  # kWh bought for the battery per hour
    sold_kwh: list[float] = field(default_factory=list)  # kWh sold from the battery per hour
    levels: list[float] = field(default_factory=list)  # % at the end of each hour
    costs: list[float] = field(default_factory=list)  # currency per hour (negative = earned)
    total: float = 0.0  # currency incl. the value of the energy left at the end
    end_value: float = 0.0
    charged: list = field(default_factory=list)  # ((hour, % level added), ...) the plan's own charges
    old: list[float] = field(default_factory=list)  # % level older than max_age_hours at the start of each hour
    held_old: int = 0  # hours where holding old energy was replaced by using it
    unaged_end: float | None = None  # % level of unknown age left at the end (None: not tracked)
    # False: optimize_with_rules() used up its rounds and the plan still has an action whose rule is switched off
    converged: bool = True
    # full charge (cfg.full_deadline): the hour the plan first reaches cfg.full_level, and why the requirement was
    # dropped ("unreachable": no plan reaches it by the deadline; the plan is then the one without the requirement)
    full_reached: int | None = None
    full_dropped: str | None = None
    # the full charge plan whose forward pass used held old energy and so missed full (the result is then the plan without
    # the requirement): optimize_with_rules() learns the max. age limits from it, so the next round can reach full
    full_missed: OptimizerResult | None = field(default=None, repr=False)


def step_hour(
    hour: OptimizerHour, level: float, action: str, cfg: OptimizerConfig, limit: float | None = None, details: dict | None = None
) -> tuple[float, float, float, float]:
    """(new level %, cost, kWh out of the battery, kWh bought for the battery) of one hour with one action.

    limit: max kWh from the grid when grid charging / max kWh sold when force discharging
    (None = the full rate). details (optional) gets the kWh flows of the hour.
    """
    if cfg.battery_size == 0:
        if details is not None:
            details.update(solar_in=0.0, house=0.0, sold=0.0, grid=0.0, delivered=0.0)
        return 0.0, hour.consumption * hour.buy - hour.solar * hour.sell, 0.0, 0.0
    to_kwh = cfg.battery_size / 100.0
    room = max(cfg.level_max - level, 0.0) * to_kwh  # kWh battery side
    available = max(level - cfg.level_min, 0.0) * to_kwh
    curve_fallback = cfg.curve_fallback_efficiency or cfg.efficiency
    max_charge = cfg.max_charge * hour.fraction
    if cfg.charge_taper and (action == "grid_charging" or (cfg.solar_charging and hour.solar > 0.0 and action in ("discharge_allowed", "force_solar_only_charging"))):
        # the battery charges at the cap of the band its level is in: the hour is walked through the bands (only a charging action needs it)
        max_charge = charge_energy_kwh(cfg.charge_taper, level, hour.fraction, cfg.max_charge, to_kwh, cfg.efficiency)
    max_discharge = cfg.max_discharge * hour.fraction
    bought = hour.consumption
    exported = hour.solar
    stored = 0.0  # kWh into the battery (battery side)
    delivered = 0.0  # kWh out of the battery (AC side)
    battery_used = 0.0  # kWh removed from the battery (battery side)

    solar_in = house = 0.0
    if cfg.solar_charging and action in ("discharge_allowed", "force_solar_only_charging", "grid_charging") and hour.solar > 0.0:
        wanted = min(hour.solar, max_charge)
        charge_efficiency = efficiency_at_power(cfg.charge_efficiency_curve, wanted * 1000.0, curve_fallback)
        solar_in = min(wanted, room / charge_efficiency)
        stored += solar_in * charge_efficiency
        exported -= solar_in
    if action == "grid_charging":
        wanted = max(min(max_charge - solar_in, max_charge if limit is None else limit), 0.0)
        charge_efficiency = efficiency_at_power(cfg.charge_efficiency_curve, wanted * 1000.0, curve_fallback)
        grid_in = min(wanted, max((room - stored) / charge_efficiency, 0.0))
        stored += grid_in * charge_efficiency
        bought += grid_in
    elif action == "discharge_allowed" and hour.solar <= 0.0:
        wanted = min(hour.consumption, max_discharge)
        discharge_efficiency = efficiency_at_power(cfg.discharge_efficiency_curve, wanted * 1000.0, curve_fallback)
        delivered = house = min(wanted, available * discharge_efficiency)
        battery_used = delivered / discharge_efficiency
        bought -= delivered
    elif action == "force_discharge":
        # the house first, then the planned kWh to the grid
        wanted = min(max_discharge, max_discharge if limit is None else hour.consumption + limit)
        discharge_efficiency = efficiency_at_power(cfg.discharge_efficiency_curve, wanted * 1000.0, curve_fallback)
        house = min(hour.consumption, max_discharge, available * discharge_efficiency)
        usage_floor = max(cfg.level_min, cfg.usage_reserve, hour.floor or 0.0)
        export_available = max(level - usage_floor, 0.0) * to_kwh * discharge_efficiency - house
        export_wanted = max_discharge if limit is None else limit
        exported_from_battery = min(max(export_available, 0.0), export_wanted, max_discharge - house)
        delivered = house + exported_from_battery
        battery_used = delivered / discharge_efficiency
        bought -= house
        exported += exported_from_battery

    cost = bought * hour.buy - exported * hour.sell + delivered * cfg.wear
    new_level = level + (stored - battery_used) / to_kwh
    grid_kwh = bought - hour.consumption if action == "grid_charging" else 0.0
    if details is not None:
        details.update(solar_in=solar_in, house=house, sold=delivered - house, grid=grid_kwh, delivered=delivered)
    return min(max(new_level, cfg.level_min), cfg.level_max), cost, delivered, grid_kwh


def storm_holds(hour: OptimizerHour, level: float, cfg: OptimizerConfig) -> bool:
    """True when the battery may hold energy older than the max. age in this hour: using it for the house would end the hour under
    a storm level that is still to come (this hour's or a later one).

    The storm level goes before the max. age of the energy (decided with the user), but only where the storm needs it: with more
    energy in the battery than the level ahead, old energy is used as before.
    """
    if hour.floor_ahead is None or hour.consumption <= 0.0:
        return False
    return step_hour(hour, level, "discharge_allowed", cfg)[0] < hour.floor_ahead - 1e-6


@dataclass
class _Later:
    """What comes after an hour: the solar the battery can take the same day, the highest buy price."""

    solar: float
    buy: float


def _later(hours: list[OptimizerHour], cfg: OptimizerConfig) -> list[_Later]:
    later = [_Later(0.0, 0.0)] * len(hours)
    solar, buy, day = 0.0, 0.0, None
    for index in reversed(range(len(hours))):
        hour = hours[index]
        current = hour.timestamp.date() if hasattr(hour.timestamp, "date") else None
        if current != day:
            solar, day = 0.0, current
        later[index] = _Later(solar, buy)
        # where the battery tapers the sun can't refill it faster than the slowest band: never counted on more than that
        rate = min([cfg.max_charge, *(cap for _, cap in cfg.charge_taper)]) if cfg.charge_taper else cfg.max_charge
        solar += min(max(hour.solar, 0.0), rate * hour.fraction)
        buy = max(buy, hour.buy)
    return later


def _fresh(timestamp, cfg: OptimizerConfig, charges, unaged: float) -> float:
    """% level that is not older than cfg.max_age_hours at timestamp: the energy of unknown age and what was charged since.

    The history also counts a charge of the hour itself (it is just in); the plan's own charge of an hour is
    in the battery from the next hour.
    """
    since = timestamp - datetime.timedelta(hours=cfg.max_age_hours)
    return (
        unaged
        + sum(pct for charged_at, pct in cfg.charged_before if since < charged_at <= timestamp)
        + sum(pct for charged_at, pct in charges if since < charged_at < timestamp)
    )


def _unaged_left(unaged: float, level: float, cfg: OptimizerConfig) -> float:
    """The energy of unknown age is never old, so it counts as fresh and is used up last: the energy above it (the
    old kWh of the history) leaves first. It is capped by the usable energy in the battery."""
    return min(unaged, max(level - cfg.level_min, 0.0))


def old_energy(hour: OptimizerHour, level: float, cfg: OptimizerConfig, charges, unaged: float = 0.0) -> float:
    """% level older than the max age at the start of the hour (0 when there is none or at most one step).

    unaged: the % of energy of unknown age still in the battery (cfg.unaged, less what has left since).
    """
    if cfg.max_age_hours is None:
        return 0.0
    old = level - cfg.level_min - _fresh(hour.timestamp, cfg, charges, unaged)
    return old if old > cfg.step else 0.0


def old_energy_limits(hours: list[OptimizerHour], cfg: OptimizerConfig, charges, start_level: float, levels: list[float]) -> list[float | None]:
    """OptimizerHour.old_above of every hour for a plan with these charges and levels (None: the energy can't be old)."""
    limits: list[float | None] = []
    unaged = cfg.unaged
    for index, hour in enumerate(hours):
        unaged = _unaged_left(unaged, levels[index - 1] if index else start_level, cfg)
        above = cfg.level_min + _fresh(hour.timestamp, cfg, charges, unaged)
        limits.append(None if above + cfg.step >= cfg.level_max else round(above, 3))
    return limits


def enforce_max_age(
    hours: list[OptimizerHour], start_level: float, cfg: OptimizerConfig, actions: list[str], limits: list[float | None]
) -> tuple[list[str], list[float | None], list[float]]:
    """A given plan (e.g. the rules') without holding back energy older than cfg.max_age_hours.

    Walks the plan hour by hour; an hour that holds (house consumption, the battery keeps its energy)
    while old energy is in the battery uses the battery instead. Returns the actions, the kWh limits
    and the % level that was older than the max age at the start of each hour.
    """
    actions, limits = list(actions), list(limits)
    old_levels: list[float] = []
    charges: list = []
    level, unaged = start_level, cfg.unaged
    for index, hour in enumerate(hours):
        unaged = _unaged_left(unaged, level, cfg)
        old = old_energy(hour, level, cfg, charges, unaged)
        if old > 0.0 and actions[index] == HOLD and hour.consumption > 0.0 and not storm_holds(hour, level, cfg):
            actions[index], limits[index] = "discharge_allowed", None
        previous = level
        level = step_hour(hour, level, actions[index], cfg, limits[index])[0]
        unaged = _unaged_left(unaged, level, cfg)
        if level > previous + 1e-6:
            charges.append((hour.timestamp, level - previous))
        old_levels.append(round(old, 2))
    return actions, limits, old_levels


def _level_from(
    hours: list[OptimizerHour], level: float, cfg: OptimizerConfig, actions: list[str], limits: list, first: int, stop: int
) -> float:
    """The battery level at the end of hours[stop] with these actions, from `level` at the start of hours[first]."""
    for index in range(first, stop + 1):
        level = step_hour(hours[index], level, actions[index], cfg, limits[index])[0]
    return level


def enforce_storm_floor(
    hours: list[OptimizerHour], start_level: float, cfg: OptimizerConfig, actions: list[str], limits: list[float | None]
) -> tuple[list[str], list[float | None], dict[int, tuple[str, str]]]:
    """A given plan (the rules') that keeps the storm level of every hour (OptimizerHour.floor).

    Walks the plan. A sale that the level caps (step_hour exports only down to it) is cut to what may be sold, or dropped:
    the plan lists exactly what the battery may do. At the first hour that ends under its level, the cheapest earlier (or
    same) hour is changed: a sale is cancelled, house use becomes a hold (the house buys from the grid), or grid charging is
    planned with as small a part of the charge rate as reaches the level (the full rate when no part does, and the next
    round goes on). The price of a sale is what it earns, the price of a hold or a charge what the grid kWh cost. Stops when
    no change raises the level at that hour (the level can't be reached).
    Returns the actions, the limits and {index: (what it was, what it is now)} of the hours that were changed.
    """
    actions, limits = list(actions), list(limits)
    changed: dict[int, tuple[str, str]] = {}
    hold = "discharge_allowed" if HOLD in cfg.unsupported else HOLD

    def record(index: int, action: str) -> None:
        changed[index] = (changed.get(index, (actions[index],))[0], action)
        actions[index] = action

    for _ in range(8 * len(hours) + 1):
        level, violation, capped = start_level, None, None
        levels_before: list[float] = []
        for index, hour in enumerate(hours):
            levels_before.append(level)
            details: dict = {}
            level = step_hour(hour, level, actions[index], cfg, limits[index], details)[0]
            if hour.floor is not None and actions[index] == "force_discharge":
                free: dict = {}
                step_hour(replace(hour, floor=None), levels_before[index], actions[index], cfg, limits[index], free)
                if free["sold"] > details["sold"] + 0.01:
                    capped = (index, details["sold"])
                    break
            if hour.floor is not None and level < hour.floor - 1e-6:
                violation = index
                break
        if capped is not None:
            index, sold = capped
            if sold < max(cfg.min_sell_kwh, 0.01):
                record(index, hold)
                limits[index] = None
            else:
                record(index, "force_discharge")
                limits[index] = math.floor(sold * 100.0) / 100.0
            continue
        if violation is None:
            break
        floor = hours[violation].floor
        current = level
        best = None  # (price, index, action, limit)
        for index in range(violation + 1):
            hour = hours[index]
            before = levels_before[index]
            candidates: list[tuple[float, str, float | None]] = []
            if actions[index] == "force_discharge" and hold not in hour.forbidden:
                candidates.append((hour.sell, hold, None))
            if actions[index] == "discharge_allowed" and hold != "discharge_allowed" and hour.consumption > 0.0 and hold not in hour.forbidden:
                candidates.append((hour.buy, hold, None))
            if (
                "grid_charging" not in cfg.unsupported
                and before < cfg.level_max - 0.01
                and not (actions[index] == "grid_charging" and limits[index] is None)
            ):
                for fraction in FRACTIONS:
                    candidates.append((hour.buy, "grid_charging", None if fraction == 1.0 else cfg.max_charge * hour.fraction * fraction))
            for price, action, limit in candidates:
                if action == "grid_charging" and limits[index] is not None and actions[index] == "grid_charging" and limit is not None and limit <= limits[index]:
                    continue  # not more than the plan charges already
                trial_actions, trial_limits = list(actions), list(limits)
                trial_actions[index], trial_limits[index] = action, limit
                raised = _level_from(hours, before, cfg, trial_actions, trial_limits, index, violation)
                if raised <= current + 1e-6:
                    continue
                if action == "grid_charging" and raised < floor - 1e-6 and limit is not None:
                    continue  # a smaller part that doesn't reach the level: the full rate is tried
                if best is None or price < best[0] - 1e-9:
                    best = (price, index, action, limit)
                break  # the smallest part that works (or the cancelled sale, or the hold) of this hour
        if best is None:
            break
        _, index, action, limit = best
        record(index, action)
        limits[index] = limit
    return actions, limits, changed


def taper_cuts(
    hours: list[OptimizerHour], start_level: float, cfg: OptimizerConfig, actions: list[str], limits: list[float | None], result: OptimizerResult
) -> dict[int, float]:
    """{index: kWh} of the grid charge hours of a given plan (the rules') that ask more than the battery takes.

    result is simulate() of the plan with cfg (the battery tapers there); the same hour without the taper is what the plan was
    made for. Only the taper counts: an hour that asks more than its part of an hour allows is not a taper's doing."""
    plain = replace(cfg, charge_taper=())
    cuts: dict[int, float] = {}
    for index, hour in enumerate(hours):
        if actions[index] != "grid_charging":
            continue
        before = result.levels[index - 1] if index else start_level
        full = step_hour(hour, before, "grid_charging", plain, limits[index])[3]
        if result.grid_kwh[index] < full - 0.03:
            cuts[index] = result.grid_kwh[index]
    return cuts


def _allowed(hour: OptimizerHour, level: float, action: str, cfg: OptimizerConfig, delivered: float, later: _Later | None = None) -> bool:
    if action in hour.forbidden or action in cfg.unsupported:
        return False
    if (
        action == HOLD and hour.consumption > 0.0 and hour.old_above is not None and level > hour.old_above + cfg.step
        and not storm_holds(hour, level, cfg)
    ):
        return False  # old energy is used, not held back (unless the storm level needs it)
    if action == "force_discharge":
        return hour.can_sell and delivered - min(delivered, hour.consumption) >= max(cfg.min_sell_kwh, 0.01)
    if action == "grid_charging":
        return level < cfg.level_max - 0.01
    if action == "blocked_solar_charge":
        if hour.solar <= 0.0 or not hour.block_allowed:
            return False
        if later is None:
            return True
        # no bet: a stored kWh can never save more than the sold one earns now
        if hour.sell >= later.buy * cfg.efficiency**2:
            return True
        # else the battery must still be filled the same day with SOLAR_BLOCK_MARGIN of the later sun
        room = max(cfg.level_max - level, 0.0) * cfg.battery_size / 100.0
        return later.solar * SOLAR_BLOCK_MARGIN * cfg.efficiency >= room - 0.01
    return True


# Full charge: what a plan that is not full by the deadline pays in the DP (steering only, never in a cost). Large and finite,
# so the DP can't become infeasible: when no plan reaches full, every plan of the layer pays it, which is how it is detected.
FULL_CHARGE_PENALTY = 1e9
FULL_CHARGE_RULE = "full_charge_planner"


def _full_charge_deadline(hours: list[OptimizerHour], start_level: float, cfg: OptimizerConfig) -> int | None:
    """Index of the last hour of the full charge requirement (cfg.full_deadline), None without one.

    None too when the battery is full already at the start, or when no hour of these hours is before the deadline."""
    if cfg.full_deadline is None or cfg.full_level is None or start_level >= cfg.full_level - 1e-6:
        return None
    return next((i for i in range(len(hours) - 1, -1, -1) if hours[i].timestamp <= cfg.full_deadline), None)


def _full_charge_layer(
    hour: OptimizerHour, levels: list[float], index, options, full: list[float], not_full: list[float] | None,
    cfg: OptimizerConfig, later: _Later, to_kwh: float,
) -> tuple[list[float], list[tuple[str, float | None]]]:
    """One hour of the DP for the plans that have not been full yet (full charge requirement).

    full: the value of the next hour for plans that have been full (the plain DP), not_full: of those that have not
    (None in the deadline hour: not full at its end pays FULL_CHARGE_PENALTY). Solar charging can't be blocked before
    the battery has been full. The steering terms are those of optimize().

    The table works on the 0.5 % grid, the plan follows the exact level (up to step / 2 lower): full in the table is
    step / 2 above full_level, so a plan that is full here is full at its exact level too (at most level_max, which a
    charge reaches exactly)."""
    reached = min(cfg.full_level + cfg.step / 2, cfg.level_max) - 1e-6
    new_value, new_choice = [], []
    for level in levels:
        best_cost, best_option = None, options[0]
        for action, limit in options:
            if action == "blocked_solar_charge":
                continue
            new_level, cost, delivered, _ = step_hour(hour, level, action, cfg, limit)
            if not _allowed(hour, level, action, cfg, delivered, later):
                continue
            if new_level >= reached:
                after = full[index(new_level)]
            elif not_full is None:
                after = full[index(new_level)] + FULL_CHARGE_PENALTY
            else:
                after = not_full[index(new_level)]
            total = cost + delivered * cfg.min_profit_per_kwh + after
            if hour.floor is not None:  # the storm warning's level, as in optimize()
                total += max(hour.floor - new_level, 0.0) * to_kwh * STORM_SHORTFALL_PENALTY
            if action == "force_discharge":
                total += cfg.min_total_profit
            if best_cost is None or total < best_cost - 1e-9:
                best_cost, best_option = total, (action, limit)
        new_value.append(FULL_CHARGE_PENALTY * 2 if best_cost is None else best_cost)
        new_choice.append(best_option)
    return new_value, new_choice


def optimize(hours: list[OptimizerHour], start_level: float, cfg: OptimizerConfig, end_value_per_kwh: float) -> OptimizerResult:
    """Cheapest action per hour for the whole horizon (backward dynamic programming).

    end_value_per_kwh values the energy left in the battery (battery side) at the end of the
    horizon, else the cheapest plan would always empty the battery in the last hours.
    """
    steps = int(round((cfg.level_max - cfg.level_min) / cfg.step))
    levels = [cfg.level_min + i * cfg.step for i in range(steps + 1)]
    to_kwh = cfg.battery_size / 100.0

    def index(level: float) -> int:
        return min(max(int(round((level - cfg.level_min) / cfg.step)), 0), steps)

    # value[i] = lowest cost from this hour to the end starting at levels[i]
    value = [-(level - cfg.level_min) * to_kwh * end_value_per_kwh for level in levels]
    options = [(action, None) for action in ACTIONS if action not in ("grid_charging", "force_discharge")]
    options += [("grid_charging", cfg.max_charge * fraction) for fraction in FRACTIONS]
    options += [("force_discharge", cfg.max_discharge * fraction) for fraction in FRACTIONS]  # capped per hour by its rate
    choice: list[list[tuple[str, float | None]]] = []
    later = _later(hours, cfg)
    # full charge: a second layer for the plans that have not been full yet, only up to the deadline and only with a requirement
    deadline = _full_charge_deadline(hours, start_level, cfg)
    full_value: list[float] | None = None
    full_choice: list[list[tuple[str, float | None]]] = []
    for position in reversed(range(len(hours))):
        hour = hours[position]
        new_value, new_choice = [], []
        for level in levels:
            best_cost, best_option = None, options[0]
            for action, limit in options:
                new_level, cost, delivered, _ = step_hour(hour, level, action, cfg, limit)
                if not _allowed(hour, level, action, cfg, delivered, later[position]):
                    continue
                # min_profit_per_kwh / min_total_profit only steer the choice, the reported costs are real money
                total = cost + delivered * cfg.min_profit_per_kwh + value[index(new_level)]
                if hour.floor is not None:
                    total += max(hour.floor - new_level, 0.0) * to_kwh * STORM_SHORTFALL_PENALTY
                if action == "force_discharge":
                    total += cfg.min_total_profit
                if best_cost is None or total < best_cost - 1e-9:
                    best_cost, best_option = total, (action, limit)
            new_value.append(best_cost)
            new_choice.append(best_option)
        if deadline is not None and position <= deadline:
            full_value, layer = _full_charge_layer(
                hour, levels, index, options, value, full_value if position < deadline else None, cfg, later[position], to_kwh
            )
            full_choice = [layer, *full_choice]
        value, choice = new_value, [new_choice, *choice]

    result = OptimizerResult()
    # the deadline hour for the "reached full" check, also when the table can't promise full (the plan may still reach it
    # between the table's grid and full_level: then it is reached, not dropped)
    last = deadline
    # no plan reaches full by the deadline: the requirement is dropped and the plan is the one without it
    if deadline is not None and full_value[index(start_level)] >= FULL_CHARGE_PENALTY / 2:
        result.full_dropped, deadline = "unreachable", None
    level, unaged = start_level, cfg.unaged
    for position, (hour, hour_choice) in enumerate(zip(hours, choice)):
        action, limit = hour_choice[index(level)]
        if deadline is not None and position <= deadline and result.full_reached is None:
            action, limit = full_choice[position][index(level)]
        # the age of the energy follows from this plan's own charges, so it is checked on the way: old
        # energy is never held back (the table above only knows the limits of the previous round)
        unaged = _unaged_left(unaged, level, cfg)
        old = old_energy(hour, level, cfg, result.charged, unaged)
        if old > 0.0 and action == HOLD and hour.consumption > 0.0 and not storm_holds(hour, level, cfg):
            action, limit = "discharge_allowed", None
            result.held_old += 1
        previous = level
        level, cost, delivered, grid_kwh = step_hour(hour, level, action, cfg, limit)
        unaged = _unaged_left(unaged, level, cfg)
        result.unaged_end = unaged
        if level > previous + 1e-6:
            result.charged.append((hour.timestamp, level - previous))
        result.old.append(round(old, 2))
        result.actions.append(action)
        result.grid_kwh.append(round(grid_kwh, 2))
        result.sold_kwh.append(round(delivered - min(delivered, hour.consumption), 2) if action == "force_discharge" else 0.0)
        result.levels.append(round(level, 1))
        result.costs.append(round(cost, 3))
        if last is not None and position <= last and result.full_reached is None and level >= cfg.full_level - 1e-6:
            result.full_reached = position  # the exact level, not the 0.5 % grid of the DP
    if result.full_reached is not None:
        result.full_dropped = None
    elif deadline is not None:
        # the forward pass left the table's plan (old energy is used instead of held) and doesn't reach full: the holds and
        # charges made for the full charge are not kept, the plan is the one without the requirement. It is dropped unless
        # that plan reaches full by itself (its levels are rounded to 0.1 %). The failed plan is kept for its max. age limits.
        plain = optimize(hours, start_level, replace(cfg, full_deadline=None, full_level=None), end_value_per_kwh)
        plain.full_reached = next((j for j, value in enumerate(plain.levels[: last + 1]) if value >= cfg.full_level - 1e-6), None)
        plain.full_dropped = None if plain.full_reached is not None else "unreachable"
        plain.full_missed = result
        return plain
    result.end_value = (level - cfg.level_min) * to_kwh * end_value_per_kwh
    result.total = sum(result.costs) - result.end_value
    return result


def simulate(
    hours: list[OptimizerHour],
    start_level: float,
    cfg: OptimizerConfig,
    actions: list[str],
    end_value_per_kwh: float,
    limits: list[float | None] | None = None,
) -> OptimizerResult:
    """Cost of a given action per hour (e.g. the rule based plan) with the same model.

    limits: planned kWh per grid charging / force discharge hour (None = the full rate).
    """
    result = OptimizerResult(actions=list(actions))
    level = start_level
    for index_, (hour, action) in enumerate(zip(hours, actions)):
        limit = limits[index_] if limits else None
        level, cost, _, grid_kwh = step_hour(hour, level, action, cfg, limit)
        result.grid_kwh.append(round(grid_kwh, 2))
        result.levels.append(round(level, 1))
        result.costs.append(round(cost, 3))
    result.end_value = (level - cfg.level_min) * cfg.battery_size / 100.0 * end_value_per_kwh
    result.total = sum(result.costs) - result.end_value
    return result


def requirement_cost(
    hours: list[OptimizerHour],
    start_level: float,
    cfg: OptimizerConfig,
    actions: list[str],
    limits: list[float | None] | None = None,
) -> float:
    """What the profit requirements add to a plan in the optimisation's own figure (not money).

    optimize() adds min_profit_per_kwh for every kWh out of the battery and min_total_profit for every hour
    with a sale to the grid to the cost of an action, so that it only chooses actions that earn that much.
    The reported totals are real money without it. The same formula for any plan makes them comparable.
    """
    level, total = start_level, 0.0
    for index, (hour, action) in enumerate(zip(hours, actions)):
        details: dict = {}
        level, _, _, _ = step_hour(hour, level, action, cfg, limits[index] if limits else None, details)
        total += details["delivered"] * cfg.min_profit_per_kwh
        if details["sold"] > 0.01:
            total += cfg.min_total_profit
    return round(total, 2)


# The rule an optimised action corresponds to -> the switches that allow it. An action whose
# rule is switched off is forbidden in that hour and the period is optimised again.
_USE = ("cheapest_hour_fill_planner", "most_expensive_planner", "needed_before_max_level_planner")
_SELL = ("most_profit", "sell_excess_kwh_available", "force_powerwall_max_hours_old_discharge")
RULE_SWITCHES = {
    "cheapest_hour_fill_planner": _USE,
    "most_expensive_planner": _USE,
    "needed_before_max_level_planner": _USE,
    "negative_price_planner": ("negative_price_planner", *_USE),
    "grid_charge_to_sell": ("grid_charge_to_sell",),
    "most_profit": _SELL,
    "discharge_before_solar": ("discharge_before_solar", *_SELL),
    # held back for a later, more expensive hour / held back without one (using it now doesn't pay):
    # each switch on its own, so switching one off always changes what the optimisation may do
    "prioritize_discharge_hours_by_energy_cost": ("prioritize_discharge_hours_by_energy_cost",),
    "only_discharge_on_profit": ("only_discharge_on_profit",),
    "optimize_solar_charging": ("optimize_solar_charging",),
    # a sale while old energy leaves the battery first (only planned with max_age_hours, i.e. with this switch on)
    "force_powerwall_max_hours_old_discharge": ("force_powerwall_max_hours_old_discharge",),
}


def _full_charge_charges(result: OptimizerResult, cfg: OptimizerConfig, start_level: float | None = None) -> set[int]:
    """The grid charges that bring the plan to full for the full charge requirement (result.full_reached).

    Only the grid charges of the run of hours whose level rises into the first full hour (grid or sun): an earlier charge
    that is used before that (e.g. a cheap night charge for an expensive morning) is its own rule's, so a switch can forbid
    it. start_level: the level before the first hour (None: the first hour counts as rising)."""
    reached = result.full_reached
    if reached is None:
        return set()
    levels = result.levels

    def rises(j: int) -> bool:
        before = levels[j - 1] if j else start_level
        return before is None or levels[j] > before + 1e-6

    first = reached
    while first > 0 and rises(first - 1):
        first -= 1
    return {j for j in range(first, reached + 1) if result.actions[j] == "grid_charging" and result.grid_kwh[j] > 0.01}


def explain(hours: list[OptimizerHour], result: OptimizerResult, cfg: OptimizerConfig, start_level: float | None = None) -> list[dict | None]:
    """Which rule each optimised action corresponds to, and the hour it is for (index into hours).

    {"rule": key of RULE_SWITCHES, "target": index or None, "forbid": (index, action) to forbid when
    the rule is switched off, "kind": detail}, None for plain self consumption. A charge or a hold that the plan
    needs to keep the level of a storm warning is {"rule": STORM_RULE, ...}: it belongs to no switch. start_level
    (the level before the first hour) is what tells that; without it nothing is labelled for a storm in hour 0.
    """
    count = len(hours)
    full = cfg.level_max - 0.5
    empty = cfg.level_min + 0.5
    actions, levels = result.actions, result.levels

    def charging(index: int) -> bool:
        return actions[index] == "grid_charging" and result.grid_kwh[index] > 0.01

    def selling(index: int) -> bool:
        return actions[index] == "force_discharge" and result.sold_kwh[index] > 0.01

    floored = [j for j, hour in enumerate(hours) if hour.floor is not None]
    limits = [
        grid if action == "grid_charging" else sold if action == "force_discharge" else None
        for action, grid, sold in zip(actions, result.grid_kwh, result.sold_kwh)
    ]
    hold = "discharge_allowed" if HOLD in cfg.unsupported else HOLD
    base_shortfall: dict[int, float] = {}  # per hour: how far the plan itself is under the storm level from there on

    def shortfall(plan_actions: list[str], first: int) -> float:
        """% the plan is under the storm level in the hours from `first` on (the levels before it are the result's)."""
        level = (levels[first - 1] if first else start_level)
        total = 0.0
        for j in range(first, floored[-1] + 1):
            level = step_hour(hours[j], level, plan_actions[j], cfg, limits[j])[0]
            if hours[j].floor is not None:
                total += max(hours[j].floor - level, 0.0)
        return total

    def storm_needs(index: int, instead: str) -> bool:
        """The plan is further under the storm level in a later hour when this hour does `instead` of what it does."""
        if not floored or index > floored[-1] or (index == 0 and start_level is None):
            return False
        changed = list(actions)
        changed[index] = instead
        if index not in base_shortfall:
            base_shortfall[index] = shortfall(actions, index)
        return shortfall(changed, index) > base_shortfall[index] + 1e-6

    full_charges = _full_charge_charges(result, cfg, start_level)
    explanations: list[dict | None] = []
    for i, hour in enumerate(hours):
        explanation = None
        if charging(i):
            # until the next charge block the charged kWh are sold, used before the sun fills the battery, or used
            block_end = next((j for j in range(i + 1, count) if not charging(j)), count)
            end = next((j for j in range(block_end, count) if charging(j)), count)
            # the charged kWh are gone once the battery is empty or the sun has filled it (as for a
            # sale's source below): later sales or uses are not this charge's
            gone = next((j for j in range(block_end, end) if levels[j] <= empty or (hours[j].solar > 0.0 and levels[j] >= full)), None)
            window = range(block_end, end if gone is None else gone + 1)
            sales = [j for j in window if selling(j)]
            uses = [j for j in window if actions[j] in ("discharge_allowed", "force_discharge") and hours[j].consumption > 0.0]
            solar_full = next((j for j in window if hours[j].solar > 0.0 and levels[j] >= full), None)
            # the plan would be under the level a storm warning wants without this charge
            if storm_needs(i, hold):
                explanation = {"rule": STORM_RULE, "target": next((j for j in floored if j >= i), None)}
            elif i in full_charges:
                # full charge: a grid charge on the way to the plan's first full hour (no switch, never forbidden), also in
                # a negative price hour or when it is sold later (the sale itself is explained, and forbidden, on its own)
                explanation = {"rule": FULL_CHARGE_RULE, "target": result.full_reached}
            elif sales:
                target = max(sales, key=lambda j: (hours[j].sell, -j))
                # buying to sell switched off: the sale goes, the charge may still be used for the house
                explanation = {"rule": "grid_charge_to_sell", "target": target, "forbid": (target, "force_discharge")}
            elif hour.buy / cfg.efficiency**2 + cfg.wear <= NEGATIVE_PRICE_MARGIN:
                # as the rule: a kWh pays even if it is never used (price incl. the charging loss, plus wear, below -0.01)
                explanation = {"rule": "negative_price_planner", "target": None}
            elif solar_full is not None:
                before = [j for j in uses if j < solar_full]
                explanation = {"rule": "needed_before_max_level_planner", "target": max(before, key=lambda j: (hours[j].buy, -j)) if before else None}
            else:
                same_day = sorted(h.buy for h in hours if h.timestamp.date() == hour.timestamp.date())
                cheapest = hour.buy <= same_day[min(2, len(same_day) - 1)] + 0.005
                explanation = {
                    "rule": "cheapest_hour_fill_planner" if cheapest else "most_expensive_planner",
                    "target": max(uses, key=lambda j: (hours[j].buy, -j)) if uses else None,
                }
        elif selling(i):
            source = None
            for j in range(i - 1, -1, -1):
                if charging(j):
                    source = j
                    break
                if levels[j] <= empty or (levels[j] >= full and hours[j].solar > 0.0):
                    break
            refill = recharge = None
            for j in range(i + 1, min(count, i + 24)):
                if charging(j):
                    recharge = j
                    break
                if levels[j] <= empty and hours[j].solar <= 0.0:
                    break
                if hours[j].solar > 0.0 and levels[j] >= full:
                    refill = j
                    break
            if source is not None:
                explanation = {"rule": "grid_charge_to_sell", "target": source, "kind": "bought"}
            elif recharge is not None:
                explanation = {"rule": "grid_charge_to_sell", "target": recharge, "kind": "recharged"}
            elif refill is not None:
                explanation = {"rule": "discharge_before_solar", "target": refill}
            elif i < len(result.old) and result.old[i] > 0.0:
                explanation = {"rule": "force_powerwall_max_hours_old_discharge", "target": None}
            else:
                explanation = {"rule": "most_profit", "target": None}
        elif actions[i] == "force_solar_only_charging" and hour.consumption > 0.0 and (levels[i - 1] if i else levels[i]) > empty:
            target = next(
                (j for j in range(i + 1, count) if actions[j] in ("discharge_allowed", "force_discharge") and hours[j].buy > hour.buy and hours[j].consumption > 0.0),
                None,
            )
            # no later, more expensive use: the energy is held because using it now doesn't pay
            rule = "prioritize_discharge_hours_by_energy_cost" if target is not None else "only_discharge_on_profit"
            explanation = {"rule": rule, "target": target}
            # held for the storm: the battery is at the level a storm warning wants in this or a later hour
            if storm_needs(i, "discharge_allowed"):
                explanation = {"rule": STORM_RULE, "target": next((j for j in floored if j >= i), None)}
        elif actions[i] == "blocked_solar_charge":
            target = next((j for j in range(i + 1, count) if hours[j].solar > 0.0 and actions[j] != "blocked_solar_charge"), None)
            explanation = {"rule": "optimize_solar_charging", "target": target}
        explanations.append(explanation)
    return explanations


def optimize_with_rules(
    hours: list[OptimizerHour], start_level: float, cfg: OptimizerConfig, end_value_per_kwh: float, enabled, rounds: int = MAX_ROUNDS
) -> tuple[OptimizerResult, list[dict | None]]:
    """optimize(), but actions whose rule is switched off (enabled(switch) is False) are forbidden in that hour.

    With cfg.max_age_hours energy older than that is not held back. optimize() already makes sure of it
    for the plan it returns; when it had to change the plan, the plan is made again with the age limits
    of the charges in this plan, so the table knows where the old energy is.

    Forbidding an hour usually moves the action to the next best hour, which is forbidden in the next round,
    so a long stretch of similar hours needs many rounds (up to 25 were seen). The result is only a plan that
    follows the switches when a round found nothing more to forbid: result.converged says so. Without it
    (rounds used up) the caller must not use the plan to control the battery.
    """
    for hour in hours:
        hour.forbidden = set()
        hour.old_above = None
    result, explanations = None, []
    converged = False
    seen_limits = {tuple(hour.old_above for hour in hours)}
    for _ in range(rounds):
        result = optimize(hours, start_level, cfg, end_value_per_kwh)
        explanations = explain(hours, result, cfg, start_level)
        changed = False
        for index, (action, explanation) in enumerate(zip(result.actions, explanations)):
            # a charge for a storm warning belongs to no rule switch: it is never forbidden
            if explanation and explanation["rule"] == FULL_CHARGE_RULE:
                continue  # the full charge has no switch (only its number): it is never forbidden
            if explanation and explanation["rule"] != STORM_RULE and not any(enabled(switch) for switch in RULE_SWITCHES[explanation["rule"]]):
                forbid_index, forbid_action = explanation.get("forbid", (index, action))
                if forbid_action not in hours[forbid_index].forbidden:
                    hours[forbid_index].forbidden.add(forbid_action)
                    changed = True
        # the limits follow the charges of the latest plan: also drop limits this plan has no old energy for
        # (a full charge that missed full because of held old energy: the limits of that plan, so the next round reaches it)
        aged = result.full_missed if result.full_missed is not None and result.full_missed.held_old else result
        if cfg.max_age_hours is not None and (aged.held_old or any(hour.old_above is not None for hour in hours)):
            limits = old_energy_limits(hours, cfg, aged.charged, start_level, aged.levels)
            if limits != [hour.old_above for hour in hours]:
                # The limits can flip between two plans for ever (the charges of one plan give the limits of the other).
                # A limit set seen before is a cycle: stop there. Every plan keeps the max. age exactly (optimize()
                # enforces it in its forward pass), the limits only tell the table where the old energy is.
                if tuple(limits) not in seen_limits:
                    seen_limits.add(tuple(limits))
                    for hour, limit in zip(hours, limits):
                        hour.old_above = limit
                    changed = True
        if not changed:
            converged = True
            break
    result.converged = converged
    return result, explanations


def breakdown(
    hours: list[OptimizerHour],
    start_level: float,
    cfg: OptimizerConfig,
    actions: list[str],
    end_value_per_kwh: float,
    limits: list[float | None] | None = None,
    start_energy_as_cost: bool = False,
) -> dict:
    """The electricity bill of the period without and with the battery, item by item.

    Costs are positive, income negative; the items of each column add up to its bill. Without a
    battery the house buys all its consumption, all solar surplus is sold and the energy in the
    battery now stays (its value is deducted); with the battery the plan's flows are used and the
    energy left at the end is deducted. The profit is the difference of the bills.

    start_energy_as_cost (what-if): the energy in the battery at the start is not free. The without
    column gets no credit for it and the with column carries it as a cost ("start_value", valued like
    the energy left), so the bill with the battery is comparable between batteries and settings
    that start with different amounts of energy. The profit is the same either way.
    """
    keys = ("house_kwh", "house_cost", "battery_kwh", "battery_cost", "solar_kwh", "solar_income", "sold_kwh", "sold_income", "wear")
    without, with_battery = dict.fromkeys(keys, 0.0), dict.fromkeys(keys, 0.0)
    delivered_house = charged_solar = 0.0
    level = start_level
    for index, (hour, action) in enumerate(zip(hours, actions)):
        details: dict = {}
        level, _, _, _ = step_hour(hour, level, action, cfg, limits[index] if limits else None, details)
        without["house_kwh"] += hour.consumption
        without["house_cost"] += hour.consumption * hour.buy
        without["solar_kwh"] += hour.solar
        without["solar_income"] -= hour.solar * hour.sell
        with_battery["house_kwh"] += hour.consumption - details["house"]
        with_battery["house_cost"] += (hour.consumption - details["house"]) * hour.buy
        with_battery["battery_kwh"] += details["grid"]
        with_battery["battery_cost"] += details["grid"] * hour.buy
        with_battery["solar_kwh"] += hour.solar - details["solar_in"]
        with_battery["solar_income"] -= (hour.solar - details["solar_in"]) * hour.sell
        with_battery["sold_kwh"] += details["sold"]
        with_battery["sold_income"] -= details["sold"] * hour.sell
        with_battery["wear"] += details["delivered"] * cfg.wear
        delivered_house += details["house"]
        charged_solar += details["solar_in"]
    to_kwh = cfg.battery_size / 100.0
    start_value = (start_level - cfg.level_min) * to_kwh * end_value_per_kwh
    without["energy_value"] = 0.0 if start_energy_as_cost else -start_value
    with_battery["energy_value"] = -(level - cfg.level_min) * to_kwh * end_value_per_kwh
    if start_energy_as_cost:
        without["start_value"] = 0.0
        with_battery["start_value"] = start_value
    for column in (without, with_battery):
        column["paid"] = column["house_cost"] + column["battery_cost"]
        column["earned"] = column["solar_income"] + column["sold_income"]
        column["bill"] = (
            column["paid"] + column["earned"] + column["wear"] + column["energy_value"] + column.get("start_value", 0.0)
        )
    profit = without["bill"] - with_battery["bill"]
    cycled = delivered_house + with_battery["sold_kwh"]

    def rounded(column: dict) -> dict:
        return {key: round(value, 2) for key, value in column.items()}

    return {
        "without": rounded(without),
        "with": rounded(with_battery),
        "profit": round(profit, 2),
        "profit_per_kwh": round(profit / cycled, 2) if cycled > 0.0 else 0.0,
        "delivered_house_kwh": round(delivered_house, 2),
        "charged_grid_kwh": round(with_battery["battery_kwh"], 2),
        "charged_solar_kwh": round(charged_solar, 2),
        "end_level": round(level, 2),
        "start": hours[0].timestamp if hours else None,
        "end": hours[-1].timestamp if hours else None,
    }


def carry_floor(next_hours: list[OptimizerHour], cfg: OptimizerConfig) -> float | None:
    """The level a day has to end with so that the next day can still reach its storm levels (None: no level is needed).

    Walks back from each hour of the next day that has a level to keep, giving every hour up to it its full charge
    rate: what is left is what the day before has to bring. The next day optimises on its own from the level the day
    before ends with, so without this a storm just after midnight could not be charged for in time.
    """
    if cfg.battery_size <= 0.0:
        return None
    to_percent = 100.0 / cfg.battery_size
    carry = None
    for index, hour in enumerate(next_hours):
        if hour.floor is None:
            continue
        required = hour.floor
        for earlier in next_hours[: index + 1][::-1]:
            # the lowest level the hour can start at and still end at `required` (the battery tapers on the way up)
            if cfg.charge_taper:
                required = max(cfg.level_min, level_before_charge(cfg.charge_taper, required, earlier.fraction, cfg.max_charge, cfg.battery_size / 100.0, cfg.efficiency))
            else:
                required = max(cfg.level_min, required - cfg.max_charge * earlier.fraction * cfg.efficiency * to_percent)
        carry = required if carry is None else max(carry, required)
    return carry if carry is not None and carry > cfg.level_min + 1e-6 else None


def optimize_per_day(
    hours: list[OptimizerHour], start_level: float, cfg: OptimizerConfig, end_value_per_kwh: float, enabled
) -> tuple[OptimizerResult, list[dict | None]]:
    """optimize_with_rules() for each day on its own (max 24 hours, ends at midnight - use_midnight_battery_level).

    A day starts at the battery level the day before ends with; the energy left at midnight is
    valued like at the end of the period.
    """
    merged = OptimizerResult()
    explanations: list[dict | None] = []
    level = start_level
    days: dict = {}
    for hour in hours:
        days.setdefault(hour.timestamp.date(), []).append(hour)
    # full charge: only the day with the deadline has the requirement; a day before it that reaches full ends it
    full_pending = cfg.full_deadline is not None and cfg.full_level is not None and start_level < cfg.full_level - 1e-6
    day_list = list(days.values())
    for position, day_hours in enumerate(day_list):
        offset = len(merged.actions)
        # a storm level in the first hours of the next day: this day has to end high enough for it to be reachable
        carry = carry_floor(day_list[position + 1], cfg) if position + 1 < len(day_list) else None
        last_floor = day_hours[-1].floor
        if carry is not None:
            day_hours[-1].floor = max(last_floor or 0.0, carry)
        # what the earlier days charged is part of the age of the energy
        day_cfg = cfg
        if cfg.max_age_hours is not None:
            unaged = cfg.unaged if merged.unaged_end is None else merged.unaged_end
            day_cfg = replace(cfg, charged_before=(*cfg.charged_before, *merged.charged), unaged=unaged)
        # the last day that starts before the deadline (its hour itself may be missing from the hours)
        deadline_day = full_pending and day_hours[0].timestamp <= cfg.full_deadline and (
            position + 1 == len(day_list) or day_list[position + 1][0].timestamp > cfg.full_deadline
        )
        if cfg.full_deadline is not None and not deadline_day:
            day_cfg = replace(day_cfg, full_deadline=None, full_level=None)
        result, day_explanations = optimize_with_rules(day_hours, level, day_cfg, end_value_per_kwh, enabled)
        day_hours[-1].floor = last_floor
        if full_pending:
            reached = result.full_reached
            if reached is None and not deadline_day:
                reached = next((j for j, value in enumerate(result.levels) if value >= cfg.full_level - 1e-6), None)
            if reached is not None:
                merged.full_reached, full_pending = offset + reached, False
            elif deadline_day:
                merged.full_dropped, full_pending = result.full_dropped, False
        for name in ("actions", "levels", "costs", "grid_kwh", "sold_kwh", "charged", "old"):
            getattr(merged, name).extend(getattr(result, name))
        merged.held_old += result.held_old
        merged.unaged_end = result.unaged_end
        merged.converged = merged.converged and result.converged
        for explanation in day_explanations:
            if explanation is not None:
                explanation = dict(explanation)
                if explanation.get("target") is not None:
                    explanation["target"] += offset
                if "forbid" in explanation:
                    explanation["forbid"] = (explanation["forbid"][0] + offset, explanation["forbid"][1])
            explanations.append(explanation)
        level = result.levels[-1] if result.levels else level
    merged.end_value = (level - cfg.level_min) * cfg.battery_size / 100.0 * end_value_per_kwh
    merged.total = sum(merged.costs) - merged.end_value
    return merged, explanations
