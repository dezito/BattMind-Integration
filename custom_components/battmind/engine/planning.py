"""The current plan: what the battery should do right now."""
from __future__ import annotations

import datetime
import hashlib
import logging

from ..const import ACTION_VALID_INTERVALS, ACTION_VALID_MIN_MINUTES, CHARGING_TYPES, EMOJI_DESCRIPTION_GROUPS, LEARNING_MIN_DAYS
from ..utils.formatting import active_charging_rules, emoji_parse, emoji_rules
from ..utils.helpers import float_or_none, limit_dict_size, reset_time_to_hour, start_of_day

_LOGGER = logging.getLogger(__name__)


class Planning:
    """The current plan: what the battery should do right now."""

    def plan_inputs_hash(self) -> str:
        """Hash of everything the plan depends on. The plan is skipped when it is unchanged."""
        cache = self.grid_price_cache
        settings = self._snap.settings if self._snap else {}
        ev_charging = self._snap.ev_charging if self._snap else []
        data = (
            reset_time_to_hour(self.now()),
            self.forecast_type,
            cache.get("using_offline_prices"),
            tuple(cache.get("prices", {}).items()),
            tuple(self.sell_price_cache.get("prices", {}).items()),
            self.get_battery_level(),
            tuple(self.battery_expenses.items()),
            tuple(sorted(settings.items())),
            ev_charging,  # only shown in the overview (🚗 cars), but it has to follow the cars' plans
            self._snap.storm_alert if self._snap else None,  # a warning that starts, ends or changes level changes the plan
            self.charge_taper(),  # a new measurement can change what the battery takes
            self.battery_size_used(),  # the measured capacity the plan counts on (switch learn_battery_capacity)
            self.full_charge_hash(),  # a full charge that becomes due or is done
        )
        return hashlib.blake2b(repr(data).encode(), digest_size=16).hexdigest()

    def append_overview_output(self, title: str) -> None:
        key = f"{self.now().strftime('%Y-%m-%d %H:%M')} {title}"
        if key not in self.overview_history:
            self.overview_history[key] = [self.last_overview]
            self.overview_history = limit_dict_size(self.overview_history, 10)

    def emoji_description(self) -> None:
        lines = [f"## {self.t('ui.emoji_description.header')} ##"]
        for group in EMOJI_DESCRIPTION_GROUPS:
            entries = sorted(((key, entry) for key, entry in CHARGING_TYPES.items() if entry["group"] == group), key=lambda kv: kv[1]["priority"])
            items = [f"**{entry['emoji']} {self.t(f'charging_type.{key}.description', default='No description')}**" for key, entry in entries]
            if items:
                lines.append(f"### {self.t(f'ui.emoji_description.groups.{group}')}")
                lines.extend(f"* {item}" for item in items)
        self.set_state("emoji_description", self.t("ui.emoji_description.markdown_hint", name=self.name))
        self.set_attr("emoji_description", "description", "\n".join(lines))

    def set_default_entity_states(self) -> None:
        self.set_state("overview", self.t("ui.set_default_entity_states.markdown_guide", name=self.name))
        if not self.last_overview:
            self.last_overview = f"<center>\n\n**{self.t('ui.set_default_entity_states.no_overview')}**\n\n</center>"
            self.set_attr("overview", "overview", self.last_overview)

    def _plan_today(self) -> dict:
        today = self.plan.get(0, {})
        return today if today.get("start_of_day") == start_of_day(self.now()) else {}

    def _current_hour_in(self, key: str):
        """Timestamp in today's plan[key] with the current hour, else None."""
        current_hour = self.now().hour
        for timestamp in self._plan_today().get(key, ()):
            if timestamp.hour == current_hour:
                return timestamp
        return None

    def current_hour_in_charge_hours(self):
        current_hour = reset_time_to_hour(self.now())
        for timestamp in self.charge_hours:
            if isinstance(timestamp, datetime.datetime) and reset_time_to_hour(timestamp) == current_hour:
                return timestamp
        return None

    def solar_block_released(self) -> bool:
        """The battery ran something else than blocked_solar_charge earlier in the current hour.

        Solar charging is then not blocked again before the next hour, so the battery can switch from
        blocked to charging once an hour, but not back and forth every few minutes.
        """
        return self.unblocked_hour == reset_time_to_hour(self.now())

    def current_period_in_force_discharge(self):
        now = self.now()
        periods = self.price_provider.sell_periods_in_hour(now) if self.price_provider else 1
        period_length = datetime.timedelta(minutes=60 / periods)
        for timestamp in self._plan_today().get("force_discharge_timestamps", {}):
            if timestamp <= now < timestamp + period_length:
                return timestamp
        return None

    def stamp_action(self) -> None:
        """When the published action was calculated (updated) and until when it counts (valid_until).

        The blueprint's fail-safe ends a forced mode that is not renewed, so only a calculation that succeeded stamps it:
        three planning intervals, at least ACTION_VALID_MIN_MINUTES (a long job may delay a run)."""
        if self._snap is None:
            return
        tz = self._snap.tz
        calculated = self.now().replace(tzinfo=tz)  # fold is kept, so the repeated hour in the autumn is the right one
        minutes = max(ACTION_VALID_INTERVALS * int(self.config["cron_interval"]), ACTION_VALID_MIN_MINUTES)
        # added in UTC: adding to a local time counts wall clock time across the summer time changes
        valid_until = (calculated.astimezone(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).astimezone(tz)
        self.set_attr("powerwall_action", "updated", calculated.isoformat(timespec="seconds"))
        self.set_attr("powerwall_action", "valid_until", valid_until.isoformat(timespec="seconds"))

    def charge_if_needed(self, force_recalculate: bool = False) -> str:
        """Update the plan and decide what the battery should do right now."""
        t = self.t
        current_rules: dict = {}
        try:
            self.full_charge_check()  # before the plan: a full battery ends a due full charge
            level_entity = self.config["home"]["entity_ids"]["powerwall_battery_level_entity_id"]
            if level_entity:
                # push notification: no level for BATTERY_LEVEL_MAX_AGE, so the plan runs on the fail-safe 100 % (get_battery_level)
                self.notice("battery_level_lost", self.get_battery_level_or_none() is None, entity=level_entity)
            self.cheap_grid_charge_hours(force_recalculate=force_recalculate)

            temperature_entity = self.config["home"]["entity_ids"].get("battery_temperature_entity_id", "")
            temperature = float_or_none(self.entity_state(temperature_entity)) if temperature_entity else None
            if temperature is not None and self._snap:
                unit = self._snap.units.get(temperature_entity)
                if unit in ("°F", "F"):
                    temperature = (temperature - 32.0) * 5.0 / 9.0
                elif unit in ("K", "kelvin"):
                    temperature -= 273.15
            temperature_min = self.config["solar"]["battery_temperature_min"]
            temperature_max = self.config["solar"]["battery_temperature_max"]

            # push notification: the battery is stopped because of its temperature (unavailable is a stop too: nothing is known)
            stopped_by_temperature = bool(temperature_entity) and (temperature is None or not temperature_min <= temperature <= temperature_max)
            if temperature_entity and temperature is None:
                action = "battery_stopped"
                rule = t("ui.charge_if_needed.temperature_unavailable")
            elif temperature is not None and not temperature_min <= temperature <= temperature_max:
                action = "battery_stopped"
                rule = t(
                    "ui.charge_if_needed.temperature_safety_stop",
                    temperature=f"{temperature:.1f}",
                    minimum=f"{temperature_min:.1f}",
                    maximum=f"{temperature_max:.1f}",
                )
            elif (charge_hour := self.current_hour_in_charge_hours()) is not None:
                action = "grid_charging"
                current_rules.update(active_charging_rules(self.charge_hours[charge_hour]))
                rule = t("ui.charge_if_needed.planned_charging", emoji=emoji_parse(self.charge_hours[charge_hour]))
            elif (sale_period := self.current_period_in_force_discharge()) is not None:
                # a storm warning keeps its level: nothing is exported under it, whatever the plan lists for the hour
                usage_floor = max(self.config["solar"]["powerwall_battery_level_min"], self.number("usage_reserve"), self.storm_floor_now())
                if self.get_battery_level() > usage_floor:
                    action = "force_discharge"
                    current_rules["force_discharge"] = True
                    # the rule that sells (most profit, old energy, ...) for the analysis per rule
                    current_rules.update(emoji_rules(self._plan_today()["force_discharge_timestamps"][sale_period].get("emoji", "")))
                    rule = t("ui.charge_if_needed.force_discharge")
                else:
                    # The export reserve is still available to normal house consumption.
                    action = "discharge_allowed"
                    rule = t("ui.charge_if_needed.discharge_allowed")
            elif self._current_hour_in("discharge_timestamps") is not None:
                action = "discharge_allowed"
                rule = t("ui.charge_if_needed.discharge_allowed")
            elif self._current_hour_in("force_powerwall_max_hours_old_discharge_timestamps") is not None:
                action = "discharge_allowed"
                current_rules["force_powerwall_max_hours_old_discharge"] = True
                rule = t("ui.charge_if_needed.force_powerwall_max_hours_old_discharge")
            elif self._current_hour_in("blocked_solar_charge_timestamps") is not None:
                action = "blocked_solar_charge"
                rule = t("ui.charge_if_needed.blocked_solar_charge")
            else:
                action = "force_solar_only_charging"
                rule = t("ui.charge_if_needed.not_charging")

            if temperature_entity:
                self.notice("temperature_stop", stopped_by_temperature, detail=rule.split("\n")[0] if stopped_by_temperature else "")

            if (supported := self.supported_action(action)) != action:
                # the rules don't plan an action the inverter can't run, this only catches leftovers
                _LOGGER.debug("Battery action %s is not supported by the inverter, running %s", action, supported)
                current_rules.clear()
                action = supported
                rule = t(f"ui.charge_if_needed.{'discharge_allowed' if action == 'discharge_allowed' else 'not_charging'}")

            if (float_or_none(self.entity_state(self.config["solar"]["entity_ids"]["production_entity_id"])) or 0.0) > 0:
                current_rules["solar"] = True

            if self.is_learning():
                rule += f"\n📚{t('ui.charge_if_needed.learning', days=self.consumption_days_known(), needed=LEARNING_MIN_DAYS)}"

            to_timestamp = self.now()
            from_timestamp = to_timestamp - datetime.timedelta(minutes=self.config["cron_interval"])
            watt_flow = int(self._wc.average(self.config["home"]["entity_ids"]["powerwall_watt_flow_entity_id"], from_timestamp, to_timestamp))
            if watt_flow != 0:
                # normalised: + = charging, - = discharging
                charging = (watt_flow if self.config["home"]["invert_powerwall_watt_flow_entity_id"] else -watt_flow) > 0
                key = "charging_watt" if charging else "discharging_watt"
                rule += f"\n{emoji_parse(current_rules)}{t(f'ui.charge_if_needed.{key}', watt=watt_flow)}{emoji_parse({'average': True})}"
            # the test of the user's automation (engine/automation_test.py) publishes another action for a while: the plan, the
            # rules of the planner and the unblocked hour stay what the plan decided, only the published action is held
            held = self.automation_hold_action(action)
            if held is None:
                self.session_rules.update(current_rules)
                self.set_charging_rule(rule)
            else:
                self.set_charging_rule(f"🧪{t('ui.automation_test.rule', action=t(f'ui.automation_test.action.{held}'))}")
            self.set_state("powerwall_action", held or action)
            self._published_action = held or action
            self.plan_check_note("test" if held else action)
            if action != "blocked_solar_charge":
                self.unblocked_hour = reset_time_to_hour(self.now())
            if action != self.last_powerwall_action:
                _LOGGER.info("Battery action: %s (%s)", action, rule.split("\n")[0])
            self.last_powerwall_action = action
            self.error_count = 0
            self.notice("job_failing", False)
            self.stamp_action()  # last: only a calculation that got all the way through renews the action
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Error running charge_if_needed()")
            self.notify(
                f"Error running charge_if_needed() ({self.error_count + 1}/3 before reload): {err} {type(err)}",
                title=f"{self.title} error",
                notification_id=f"{self.name}_charge_if_needed_restart_count_error",
            )
            self.error_count += 1
            if self.error_count >= 3 and self._out is not None:
                self._out.request_reload = True
                self.notice("job_failing", True, error=f"{err}"[:300])  # (pushed once: a reload loop that keeps failing is one problem)
            error_rule = f"{emoji_parse({'error': True})}{t('ui.charge_if_needed.calculation_error', error=err)}"
            self.set_charging_rule(error_rule)
            try:
                # an expired or dropped test action must not stay on the sensor because this calculation failed
                if self._automation_hold is not None and self.now() >= self._automation_hold["until"]:
                    self._automation_hold, self._automation_abort = None, "expired"
                self.automation_test_settle()
                hold = self._automation_hold
                if hold is not None and self._published_action == hold["action"]:
                    # the held action is still on the sensor: the rule still says that the test runs, with the error under it
                    test_rule = t("ui.automation_test.rule", action=t(f"ui.automation_test.action.{hold['action']}"))
                    self.set_charging_rule(f"🧪{test_rule}\n{error_rule}")
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Could not put the real action back after a failed calculation")
        return self.last_powerwall_action
