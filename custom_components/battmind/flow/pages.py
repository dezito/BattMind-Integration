"""Pages shared by the config flow and the options flow."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector

from ..const import FLOW_STEPS, PRICE_PROVIDER_KEYS
from ..management.dependency_checks import disabled_sensors
from ..prices import STROMLIGNING_PLATFORM, STROMLIGNING_UNIQUE_ID_PREFIXES, has_stromligning_sell_prices
from ..utils.cjp import cjp_charger_power_entities
from ..utils.solcast import SOLCAST_PLATFORM
from .detection import Detection, async_detect, evidence_placeholders, verdict_values
from .schema import BOOLEAN, ENERGIDATASERVICE_URL, PRICE_PROVIDERS, build_schema, normalize_input, validate_step

# the sensors the suggestions are made from; they are only made again when one of them was changed in this session
DETECTION_SENSORS = ("power_consumption_entity_id", "powerwall_watt_flow_entity_id", "powerwall_battery_level_entity_id")
REDETECT = "redetect"  # a switch of the review page, not an option


class BattMindFlowPages:
    """Pages shared by the config flow and the options flow.

    The subclass sets ``self._options`` (flat option dict), ``self._queue`` (remaining
    page ids) and implements ``_async_finish``.
    """

    _options: dict[str, Any]
    _queue: list[str]
    _providers: list[str]
    # the three sensors as they were when the options were loaded (restored, imported, stored), and what was found for a set
    _baseline: tuple[str, ...]
    _baseline_sign: bool = False  # the sign the options had then: the stored one, not what the form says now
    _detections: dict[tuple[str, ...], Detection]

    def _detection_sensors(self) -> tuple[str, ...]:
        return tuple(self._options.get(key) or "" for key in DETECTION_SENSORS)

    def _set_baseline(self) -> None:
        """The sensors in the options now are not 'changed': what was stored, restored or imported is not detected again."""
        self._baseline = self._detection_sensors()
        self._baseline_sign = bool(self._options.get("invert_powerwall_watt_flow_entity_id"))

    def _page_enabled(self, step: str) -> bool:
        if step == "prices_energidataservice":
            return "energidataservice" in self._providers
        if step == "prices_stromligning":
            return "stromligning" in self._providers
        if step == "prices_stromligning_tariffs":
            return "stromligning" in self._providers and has_stromligning_sell_prices(self._options)
        if step == "weather":
            return bool(self._options.get("production_entity_id"))
        return True

    async def _async_next(self) -> ConfigFlowResult:
        while self._queue:
            step = self._queue.pop(0)
            if self._page_enabled(step):
                return await getattr(self, f"async_step_{step}")()
        return await self._async_finish()

    async def _async_finish(self) -> ConfigFlowResult:
        raise NotImplementedError

    def _placeholders(self) -> dict[str, str]:
        return {}

    async def _async_page(self, step: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        keys = FLOW_STEPS[step]
        errors: dict[str, str] = {}
        values_for_form = dict(self._options)
        if user_input is not None:
            values = normalize_input(keys, user_input)
            errors = validate_step(step, values)
            if not errors:
                self._options.update(values)
                return await self._async_next()
            values_for_form.update(values)
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id=step,
            data_schema=build_schema(keys, values_for_form, self.hass.config.currency),  # type: ignore[attr-defined]
            errors=errors,
            description_placeholders={
                **self._placeholders(),
                "eds_url": ENERGIDATASERVICE_URL,
                # the chargers BattMind leaves out of the house consumption by itself (the consumption page)
                "cjp_chargers": ", ".join(cjp_charger_power_entities(self.hass)) or "–",  # type: ignore[attr-defined]
                # the sensors of Strømligning and Solcast that are disabled (the entity picker can't offer them)
                "stromligning_disabled": disabled_sensors(self.hass, STROMLIGNING_PLATFORM, STROMLIGNING_UNIQUE_ID_PREFIXES),  # type: ignore[attr-defined]
                "solcast_disabled": disabled_sensors(self.hass, SOLCAST_PLATFORM, "total_kwh_forecast"),  # type: ignore[attr-defined]
            },
        )

    async def async_step_detection_review(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The battery sign and what the consumption includes, suggested from the recorder for the chosen sensors.

        Only looked up when one of the three sensors was changed in this session (or the user asks), so an import, a
        restored backup or stored options keep their values. What the history cannot decide keeps its current value.
        """
        keys = FLOW_STEPS["detection_review"]
        if user_input is not None and not user_input.get(REDETECT):
            self._options.update(normalize_input(keys, user_input))
            return await self._async_next()
        if user_input is not None:
            self._options.update(normalize_input(keys, user_input))
        sensors = self._detection_sensors()
        changed = sensors != self._baseline
        detection = self._detections.get(sensors)
        if all(sensors) and (user_input is not None or (changed and detection is None)):
            # a sign set before is only trusted for the sensor it was set for
            trusted = self._baseline_sign if sensors[1] == self._baseline[1] and self._baseline[1] else None
            detection = await async_detect(self.hass, sensors[0], sensors[1], sensors[2], trusted, None, self._left_out_consumers())
            if detection.reason not in ("timeout", "error"):  # these are tried again at the next visit
                self._detections[sensors] = detection
            self._options.update(verdict_values(detection))
        schema = build_schema(keys, self._options, self.hass.config.currency)  # type: ignore[attr-defined]
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="detection_review",
            data_schema=vol.Schema({**schema.schema, vol.Optional(REDETECT, default=False): BOOLEAN}),
            errors={"base": f"detection_{detection.reason}"} if detection and detection.reason else {},
            description_placeholders={**self._placeholders(), **evidence_placeholders(detection)},
        )

    def _left_out_consumers(self) -> list[str]:
        """What the engine leaves out of the house consumption, so the suggestions are read from the same number: the ignored
        ones and - only when the option is on, as in the engine - the chargers of Cable Juice Planner."""
        ignored = self._options.get("ignore_consumption_from_entity_ids") or []
        chargers = cjp_charger_power_entities(self.hass) if self._options.get("ignore_cjp_chargers", True) else []  # type: ignore[attr-defined]
        return [*([ignored] if isinstance(ignored, str) else ignored), *chargers]

    async def async_step_price_provider(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            providers = [p for p in user_input.get("price_providers", []) if p in PRICE_PROVIDERS]
            if not providers:
                errors["base"] = "no_price_provider"
            else:
                self._providers = providers
                for provider, keys in PRICE_PROVIDER_KEYS.items():
                    if provider not in providers:
                        for key in keys:
                            self._options[key] = ""
                return await self._async_next()
        schema = vol.Schema(
            {
                vol.Required("price_providers", default=self._providers): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=PRICE_PROVIDERS,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key="price_providers",
                    )
                )
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="price_provider", data_schema=schema, errors=errors, description_placeholders=self._placeholders()
        )

    async def async_step_general(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("general", user_input)

    async def async_step_battery_entities(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("battery_entities", user_input)

    async def async_step_battery_specs(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("battery_specs", user_input)

    async def async_step_battery_actions(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("battery_actions", user_input)

    async def async_step_consumption(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("consumption", user_input)

    async def async_step_prices_energidataservice(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_energidataservice", user_input)

    async def async_step_prices_stromligning(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_stromligning", user_input)

    async def async_step_prices_stromligning_tariffs(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_stromligning_tariffs", user_input)

    async def async_step_price_fees(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("price_fees", user_input)

    async def async_step_solar(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("solar", user_input)

    async def async_step_weather(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("weather", user_input)

    async def async_step_advanced(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("advanced", user_input)

    async def async_step_database(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("database", user_input)
