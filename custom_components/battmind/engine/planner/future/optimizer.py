"""The shadow plan and the optimizer plan."""
from __future__ import annotations

import datetime
import logging
import math
from collections import defaultdict
from dataclasses import replace

from ...optimizer import (
    OptimizerConfig,
    OptimizerHour,
    breakdown,
    enforce_max_age,
    optimize_per_day,
    optimize_with_rules,
    requirement_cost,
    simulate,
)
from ....utils.formatting import emoji_parse
from ....utils.helpers import average, days_between, float_or_none, reset_time_to_hour
from .._helpers import _running_battery_price

_LOGGER = logging.getLogger(__name__)


class FutureOptimizer:
    """Runs the optimizer next to the rules and applies its plan."""

    def shadow_optimize(self, hours: list[OptimizerHour], start_level: float, cfg: OptimizerConfig, end_value: float):
        """The optimal plan of the hours: (OptimizerResult, explanation per hour)."""
        # the rule switches decide which actions the optimiser may use;
        # use_midnight_battery_level: every day is optimised on its own until midnight
        optimize = optimize_per_day if self.enabled("use_midnight_battery_level") else optimize_with_rules
        return optimize(hours, start_level, cfg, end_value, self.enabled)

    def optimizer_config(self, max_kwh_charging: float, max_kwh_discharging: float, efficiency: float) -> OptimizerConfig:
        """The battery model of the optimizer from the configuration and the current settings."""
        solar = self.config["solar"]
        return OptimizerConfig(
            battery_size=solar["powerwall_battery_size"],
            level_min=solar["powerwall_battery_level_min"],
            level_max=solar["powerwall_battery_level_max"],
            max_charge=max_kwh_charging,
            max_discharge=abs(max_kwh_discharging),
            efficiency=efficiency,
            wear=abs(solar["powerwall_wear_cost_per_kwh"]),
            charge_efficiency_curve=self.charging_efficiency_curve(),
            discharge_efficiency_curve=self.discharging_efficiency_curve(),
            curve_fallback_efficiency=self._fallback_efficiency(),
            min_profit_per_kwh=self.get_min_profit_per_kwh(),
            min_sell_kwh=self.number("min_sell_kwh"),
            min_total_profit=self.get_min_total_profit(),
            usage_reserve=self.number("usage_reserve"),
            unsupported=self.unsupported_actions(),
        )

    def age_limits(self, ctx, start_level: float) -> dict:
        """The OptimizerConfig fields for max. age of kWh (powerwall_max_hours_old), {} with the rule switched off.

        The charging history is walked from the newest charge back until it fills the battery level (as
        current_battery_level_expenses() does): those charges are the energy in the battery now, the same
        in the what-if replay of a past day. What the history can't place has an unknown age and is never
        treated as old. The charges of the plan itself are added by the optimiser.
        """
        if not self.enabled("force_powerwall_max_hours_old_discharge"):
            return {}
        remaining = start_level
        charged_before = []
        for key in sorted((key for key in ctx.battery_expenses if isinstance(key, datetime.datetime)), reverse=True):
            entry = ctx.battery_expenses[key]
            # the history counts the kWh charged from the grid/sun, the battery level what got into the battery
            percentage = (float_or_none(entry.get("percentage") if isinstance(entry, dict) else None) or 0.0) * ctx.efficiency
            taken = min(max(percentage, 0.0), max(remaining, 0.0))
            if taken > 0.0:
                charged_before.append((reset_time_to_hour(key), taken))
            remaining -= taken
        # the unusable minimum is the oldest energy, so only what is left above it can be in the way
        unknown = max(remaining - ctx.config["solar"]["powerwall_battery_level_min"], 0.0)
        return {"max_age_hours": float(self.number("powerwall_max_hours_old")), "charged_before": tuple(charged_before), "unaged": unknown}

    def shadow_plan(self, ctx, days):
        """Whole horizon optimisation next to the rules (compared in the overview).

        Returns (hours, optimal result, explanations) for apply_optimizer_plan(), None without prices.
        """
        buy_by_hour = defaultdict(list)
        for timestamp, price in ctx.grid_prices.items():
            buy_by_hour[reset_time_to_hour(timestamp)].append(price)
        # the planned kWh per charge hour - charge_if_needed() stops when they are in the battery
        charge_hour_kwh = defaultdict(float)
        for ts, session in ctx.charge_hours.items():
            if isinstance(ts, datetime.datetime):
                charge_hour_kwh[reset_time_to_hour(ts)] += session["kWh"]
        exclude_hours = self.get_exclude_sell_hours()
        hours, rule_actions, rule_limits = [], [], []
        for day in days:
            data = ctx.charging_plan[day]
            # the planned kWh per sell hour - force discharge only runs in the planned sell periods
            force_hours = defaultdict(float)
            for ts, sale in data["force_discharge_timestamps"].items():
                force_hours[reset_time_to_hour(ts)] += sale["kwh"]
            blocked_solar_hours = {ts.hour for ts in data["blocked_solar_charge_timestamps"]}
            for hour in sorted(data["battery_level_flow"]):
                timestamp = data["start_of_day"].replace(hour=hour)
                prediction = data["hour_cost_prediction"][ctx.forecast_type].get(hour)
                if prediction is None or not buy_by_hour.get(timestamp):
                    continue
                solar = self.solar_surplus_in_hour(ctx.charging_plan, ctx.current_hour, timestamp)
                # the current hour only has the minutes left (the solar prediction is already scaled)
                fraction = max(1.0 - ctx.now.minute / 60.0, 0.05) if timestamp == ctx.current_hour else 1.0
                hours.append(OptimizerHour(
                    timestamp, average(buy_by_hour[timestamp]), self.sell_price_in_hour(ctx, timestamp),
                    0.0 if solar > 0.0 else prediction["kwh"] * fraction, solar,
                    can_sell=hour not in exclude_hours, fraction=fraction,
                    # an hour where solar charging was released isn't blocked again before the next hour
                    block_allowed=not (timestamp == ctx.current_hour and self.solar_block_released()),
                ))
                # same priority as charge_if_needed()
                limit = None
                if timestamp in charge_hour_kwh:
                    action, limit = "grid_charging", charge_hour_kwh[timestamp]
                elif timestamp in force_hours:
                    action, limit = "force_discharge", force_hours[timestamp]
                elif timestamp in data["discharge_timestamps"] or timestamp in data["force_powerwall_max_hours_old_discharge_timestamps"]:
                    action = "discharge_allowed"
                elif hour in blocked_solar_hours:
                    action = "blocked_solar_charge"
                else:
                    action = "force_solar_only_charging"
                # what charge_if_needed() runs when the inverter can't run the action
                action = self.supported_action(action)
                if action not in ("grid_charging", "force_discharge"):
                    limit = None
                rule_actions.append(action)
                rule_limits.append(limit)
        if not hours:
            return None
        wear = abs(ctx.config["solar"]["powerwall_wear_cost_per_kwh"])
        start_level = ctx.get_battery_level()
        optimizer_config = replace(
            self.optimizer_config(ctx.max_kwh_charging, ctx.max_kwh_discharging, ctx.efficiency), **self.age_limits(ctx, start_level)
        )
        # the rules don't know the age of the energy they charge themselves: they may not hold it back either
        # (the optimiser enforces it in its own plan)
        self.rule_old_energy = {}
        if optimizer_config.max_age_hours is not None:
            fixed, rule_limits, old_levels = enforce_max_age(hours, start_level, optimizer_config, rule_actions, rule_limits)
            self.rule_old_energy = {
                hour.timestamp: (old, hour.buy) for hour, before, after, old in zip(hours, rule_actions, fixed, old_levels) if before != after
            }
            rule_actions = fixed
        # a kWh left in the battery at the end saves the average buy price later (after wear and
        # the min. profit, else buying just to hold the energy at the end would pay)
        end_value = ctx.efficiency * max(average([hour.buy for hour in hours]) - wear - ctx.get_min_profit_per_kwh(), 0.0)
        optimal, explanations = self.shadow_optimize(hours, start_level, optimizer_config, end_value)
        rules = simulate(hours, start_level, optimizer_config, rule_actions, end_value, rule_limits)
        self_consumption = simulate(hours, start_level, optimizer_config, ["discharge_allowed"] * len(hours), end_value)
        optimal_limits = [
            grid if action == "grid_charging" else sold if action == "force_discharge" else None
            for action, grid, sold in zip(optimal.actions, optimal.grid_kwh, optimal.sold_kwh)
        ]
        # the action and kWh limit per hour of both plans (the what-if replay executes one of them)
        self.shadow_plan_actions = {
            "rules": {hour.timestamp: (action, limit) for hour, action, limit in zip(hours, rule_actions, rule_limits)},
            "optimal": {hour.timestamp: (action, limit) for hour, action, limit in zip(hours, optimal.actions, optimal_limits)},
        }
        self.optimizer_shadow = {
            # totals of the plan for the overview (the one that controls the battery is shown)
            "summary_rules": breakdown(hours, start_level, optimizer_config, rule_actions, end_value, rule_limits),
            "summary_optimal": breakdown(hours, start_level, optimizer_config, optimal.actions, end_value, optimal_limits),
            "rules": round(rules.total, 2),
            "optimal": round(optimal.total, 2),
            "self_consumption": round(self_consumption.total, 2),
            # the totals above are real money. The optimisation also adds the profit requirements (min. profit
            # per kWh, min. total profit) to the cost of an action as a steering figure: not money, but the
            # reason why an optimal plan can cost more than plain self consumption
            "requirements": {
                "optimal": requirement_cost(hours, start_level, optimizer_config, optimal.actions, optimal_limits),
                "rules": requirement_cost(hours, start_level, optimizer_config, rule_actions, rule_limits),
                "self_consumption": requirement_cost(hours, start_level, optimizer_config, ["discharge_allowed"] * len(hours)),
            },
            "hours": [
                {
                    "hour": hour.timestamp,
                    "action": action,
                    "battery_level": level,
                    "rules_action": rule_action,
                    "rules_battery_level": rule_level,
                    "grid_kwh": grid_kwh,
                    "sold_kwh": sold_kwh,
                    "rules_grid_kwh": rule_kwh,
                    "buy": round(hour.buy, 2),
                    "sell": round(hour.sell, 2),
                }
                for hour, action, level, grid_kwh, sold_kwh, rule_action, rule_level, rule_kwh in zip(
                    hours, optimal.actions, optimal.levels, optimal.grid_kwh, optimal.sold_kwh, rules.actions, rules.levels, rules.grid_kwh
                )
            ],
        }
        _LOGGER.debug(
            "Whole period optimisation over %s hours: optimal %.2f, rules %.2f, self consumption %.2f",
            len(hours), optimal.total, rules.total, self_consumption.total,
        )
        return hours, optimal, explanations

    def optimal_battery_steps(self, ctx, hours, optimal) -> list[tuple[float, float, float, float]]:
        """The content of the battery per hour of the optimal plan, for the price of the energy in it.

        (change of the content in kWh, grid kWh charged, grid price, solar price) per hour, the steps
        _running_battery_price() takes (the rules use the same for the plan they are building).
        """
        steps = []
        previous = ctx.get_battery_level()
        for hour, level, grid_kwh in zip(hours, optimal.levels, optimal.grid_kwh):
            steps.append((ctx.percentage_to_kwh(level - previous) / ctx.efficiency, grid_kwh, hour.buy, hour.sell))
            previous = level
        return steps

    def optimal_battery_kwh_cost(self, ctx, steps, index: int) -> float:
        """kWh price incl. loss and wear of the energy in the battery at the end of hour index of the optimal plan."""
        unit = ctx.battery_expenses.get("unit")
        if not isinstance(unit, (int, float)):
            unit = self.get_powerwall_kwh_price()
        minimum = ctx.config["solar"]["powerwall_battery_level_min"]
        start_kwh = ctx.percentage_to_kwh(max(ctx.get_battery_level() - minimum, 0.0)) / ctx.efficiency
        raw = _running_battery_price(start_kwh, unit, steps[: index + 1])
        return raw + ctx.calc_battery_loss_cost(raw) + abs(ctx.config["solar"]["powerwall_wear_cost_per_kwh"])

    def old_energy_entry(self, ctx, buy: float, old: float, level_text: str = "") -> dict:
        """The plan entry of an hour that uses energy older than the max. age (old: % level)."""
        battery_cost = ctx.battery_expenses.get("unit_with_loss_and_wear")
        known = isinstance(battery_cost, (int, float))
        profit = buy - battery_cost if known else 0.0
        profit_text = f" ({profit:.2f})" if known else ""
        cost_text = f"**{ctx.r('battery_cost_total')}**: **{battery_cost:.2f} {ctx.valuta_kwh}**<br>" if known else ""
        return {
            "kwh": ctx.percentage_to_kwh(old) * ctx.efficiency,
            "profit": profit,
            "reason": (
                f"<details><summary>{emoji_parse({'force_powerwall_max_hours_old_discharge': True})}{ctx.r('title.old_energy_used')}{profit_text}</summary>"
                f"{cost_text}{ctx.r('grid_buy_price')}: **{buy:.2f} {ctx.valuta_kwh}**<br>{level_text}"
                "</details>"
            ),
        }

    def apply_max_age_to_rules(self, ctx) -> bool:
        """The rules control the battery: hours that held back old energy use the battery instead (shadow_plan found them).

        Returns True when the plan changed.
        """
        changed = False
        for timestamp, (old, buy) in sorted((getattr(self, "rule_old_energy", None) or {}).items()):
            plan_day = ctx.charging_plan.get(days_between(ctx.current_hour, timestamp))
            if plan_day is None:
                continue
            plan_day["blocked_discharge_timestamps"].pop(timestamp, None)
            if timestamp not in plan_day["discharge_timestamps"]:
                plan_day["discharge_timestamps"].append(timestamp)
            plan_day["force_powerwall_max_hours_old_discharge_timestamps"][timestamp] = self.old_energy_entry(ctx, buy, old)
            changed = True
        return changed

    def apply_optimizer_plan(self, ctx, days, hours, optimal, explanations):
        """The optimiser controls the battery: its action per hour replaces the rule based plan.

        Every action is explained as the rule it corresponds to (same titles/emojis as the
        rules) with the hour it is for. Everything is built first and swapped in at the end,
        so a failure leaves the rule plan. Partial charge hours stop at their kWh (replanned
        every cron interval), and a sale uses the most expensive sell periods of the hour.
        """
        charge_titles = {
            "cheapest_hour_fill_planner": ctx.r("title.cheapest_hours"),
            "most_expensive_planner": ctx.r("title.most_expensive_hours"),
            "needed_before_max_level_planner": ctx.r("title.needed"),
            "negative_price_planner": ctx.r("title.negative_price"),
            "grid_charge_to_sell": ctx.r("title.buy_to_sell"),
        }
        sale_titles = {
            "grid_charge_to_sell": ctx.r("title.sell_bought_cheap"),
            "discharge_before_solar": ctx.r("title.sell_before_solar"),
            "most_profit": ctx.r("title.sell_instead_of_use"),
            "force_powerwall_max_hours_old_discharge": ctx.r("title.old_energy_sold"),
        }

        def when(index):
            return hours[index].timestamp.strftime("%d/%m %H:%M")

        def clock(index, timestamp):
            """The target's time in a title, with the date when it is another day than the hour."""
            target = hours[index].timestamp
            return target.strftime("%H:%M") if target.date() == timestamp.date() else when(index)

        new_charge_hours = {}
        new_plan = {
            day: {
                "charging_sessions": {},
                "discharge_timestamps": [],
                "force_discharge_timestamps": {},
                "blocked_discharge_timestamps": {},
                "blocked_solar_charge_timestamps": {},
                "force_powerwall_max_hours_old_discharge_timestamps": {},
            }
            for day in days
        }
        new_total_cost = new_total_kwh = 0.0
        old_energy = optimal.old or [0.0] * len(hours)  # % level older than max. age at the start of each hour
        battery_steps = self.optimal_battery_steps(ctx, hours, optimal)
        rows = zip(hours, optimal.actions, optimal.levels, optimal.grid_kwh, optimal.sold_kwh, explanations, old_energy)
        for index, (hour, action, level, grid_kwh, sold_kwh, explanation, old) in enumerate(rows):
            timestamp = hour.timestamp
            plan_day = new_plan[days_between(ctx.current_hour, timestamp)]
            rule = explanation["rule"] if explanation else None
            target = explanation["target"] if explanation else None
            level_text = f"{ctx.r('level_after_hour')}: **{level:.0f}%**<br>"
            if action == "grid_charging" and grid_kwh >= 0.1:  # a few Wh is not worth a charge hour
                percentage = ctx.kwh_to_percentage(grid_kwh) * ctx.efficiency
                session = {
                    "Cost": round(grid_kwh * hour.buy, 2),
                    "kWh": round(grid_kwh, 2),
                    "battery_level": round(percentage, 2),
                    "Price": round(hour.buy, 2),
                    rule: True,
                }
                if target is None:
                    target_text = ""
                elif rule == "grid_charge_to_sell":
                    target_text = (
                        f"{ctx.r('sold_at', time=when(target), price=f'{hours[target].sell:.2f} {ctx.valuta_kwh}')} · "
                        f"{ctx.r('profit_per_kwh')}: **{self.charge_kwh_profit(ctx, hour.buy, hours[target].sell):.2f} {ctx.valuta_kwh}**<br>"
                    )
                else:
                    target_text = (
                        f"{ctx.r('used_at', time=when(target), price=f'{hours[target].buy:.2f} {ctx.valuta_kwh}')} · "
                        f"{ctx.r('profit_per_kwh')}: **{self.charge_kwh_profit(ctx, hour.buy, hours[target].buy):.2f} {ctx.valuta_kwh}**<br>"
                    )
                new_charge_hours[timestamp] = session
                plan_day["charging_sessions"][timestamp] = {
                    **session,
                    "reason": (
                        f"<details><summary>{emoji_parse({rule: True})}{charge_titles[rule]} ({hour.buy:.2f}/{percentage:.0f}%)</summary>"
                        f"{ctx.r('charging_at', kwh=f'{grid_kwh:.2f} kWh', price=f'{hour.buy:.2f} {ctx.valuta_kwh}')}<br>"
                        f"{target_text}{level_text}"
                        "</details>"
                    ),
                }
                new_total_cost += grid_kwh * hour.buy
                new_total_kwh += grid_kwh
            elif action == "force_discharge" and sold_kwh > 0.01:
                title = sale_titles[rule]
                if rule == "grid_charge_to_sell" and explanation.get("kind") == "recharged":
                    title = ctx.r("title.sell_before_cheap_charge")
                    detail = f"{ctx.r('recharged_at', time=when(target), price=f'{hours[target].buy:.2f} {ctx.valuta_kwh}')}<br>"
                elif rule == "grid_charge_to_sell" and target is not None:
                    detail = f"{ctx.r('bought_at', time=when(target), price=f'{hours[target].buy:.2f} {ctx.valuta_kwh}')}<br>"
                elif rule == "discharge_before_solar" and target is not None:
                    detail = f"{ctx.r('solar_refills_at', time=when(target), price=f'{hours[target].sell:.2f} {ctx.valuta_kwh}')}<br>"
                else:
                    detail = f"{ctx.r('worth_more_sold_now')}<br>"
                emoji = emoji_parse({rule: True})
                periods, _, _ = self.sell_prices_for_window(ctx, timestamp, timestamp + datetime.timedelta(hours=1))
                periods = periods or {timestamp: hour.sell}
                period_kwh = abs(ctx.max_kwh_discharging) / len(periods)
                count = min(len(periods), max(1, math.ceil(sold_kwh / period_kwh - 0.01)))
                battery_kwh_cost = self.optimal_battery_kwh_cost(ctx, battery_steps, index)
                for period, price in sorted(periods.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[:count]:
                    plan_day["force_discharge_timestamps"][period] = {
                        "title": title,
                        "emoji": emoji,
                        "kwh": sold_kwh / count,
                        # like the rules: what the sale earns after what the kWh cost in the battery (incl. loss and wear)
                        "profit": sold_kwh / count * (price - battery_kwh_cost),
                        "reason": (
                            f"<details><summary>{emoji}{title}<br>({sold_kwh / count:.1f} kWh / {price:.2f} {ctx.valuta_kwh})</summary>"
                            f"{ctx.r('sell_price')}: **{price:.2f} {ctx.valuta_kwh}** · {detail}{level_text}"
                            "</details>"
                        ),
                    }
                # the battery also covers the house in the rest of the hour
                plan_day["discharge_timestamps"].append(timestamp)
            elif action == "discharge_allowed":
                plan_day["discharge_timestamps"].append(timestamp)
            elif action == "blocked_solar_charge":
                later = f"{ctx.r('charged_instead_at', time=when(target))}<br>" if target is not None else ""
                charged_at = f" ({clock(target, timestamp)})" if target is not None else ""
                plan_day["blocked_solar_charge_timestamps"][timestamp] = {
                    "reason": (
                        f"<details><summary>{emoji_parse({'blocked_solar_charge': True})}{ctx.r('title.blocked_solar')}{charged_at}</summary>"
                        f"{ctx.r('solar_sold_for')}: **{hour.sell:.2f} {ctx.valuta_kwh}**{' · ' + later if later else '<br>'}{level_text}"
                        "</details>"
                    )
                }
            elif rule == "only_discharge_on_profit":
                # held back without a later, more expensive use: the energy is worth more kept
                plan_day["blocked_discharge_timestamps"][timestamp] = (
                    f"<details><summary>{emoji_parse({'only_discharge_on_profit': True})}{ctx.r('title.no_profit')}</summary>"
                    f"{ctx.r('price_now')}: **{hour.buy:.2f} {ctx.valuta_kwh}** · {ctx.r('worth_more_later')}<br>{level_text}"
                    "</details>"
                )
            elif rule == "prioritize_discharge_hours_by_energy_cost":
                later = (
                    f"{ctx.r('used_instead_at', time=when(target), price=f'{hours[target].buy:.2f} {ctx.valuta_kwh}')}<br>"
                    if target is not None else f"{ctx.r('worth_more_later')}<br>"
                )
                title = ctx.r("title.most_expensive_prioritized") + (f" ({clock(target, timestamp)})" if target is not None else "")
                plan_day["blocked_discharge_timestamps"][timestamp] = (
                    f"<details><summary>{emoji_parse({'prioritize_discharge_hours_by_energy_cost': True})}{title}</summary>"
                    f"{ctx.r('price_now')}: **{hour.buy:.2f} {ctx.valuta_kwh}** · {later}{level_text}"
                    "</details>"
                )
            if old > 0.0 and action == "discharge_allowed":
                # like the rule: the battery is used in the hours where it holds energy older than the max. age
                plan_day["force_powerwall_max_hours_old_discharge_timestamps"][timestamp] = self.old_energy_entry(ctx, hour.buy, old, level_text)

        ctx.charge_hours.clear()
        ctx.charge_hours.update(new_charge_hours)
        for day in days:
            ctx.charging_plan[day].update(new_plan[day])
            ctx.charging_plan[day]["total_cost"] = sum(
                session["Cost"] for ts, session in new_charge_hours.items() if days_between(ctx.current_hour, ts) == day
            )
        return round(new_total_cost, 2), round(new_total_kwh, 2)
