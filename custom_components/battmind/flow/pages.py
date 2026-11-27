"""Pages shared by the config flow and the options flow."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from ..const import FLOW_STEPS, PRICE_PROVIDER_KEYS, default_options, price_scale
from ..management.dependency_checks import disabled_sensors
from ..prices import STROMLIGNING_PLATFORM, STROMLIGNING_UNIQUE_ID_PREFIXES, detect_price_scale, has_stromligning_sell_prices, price_currency_errors, price_sensor_errors, DEFAULT_COUNTRY, default_providers, provider_errors
from ..utils.cjp import cjp_charger_power_entities
from ..utils.solcast import SOLCAST_PLATFORM
from .detection import Detection, async_detect, evidence_placeholders, verdict_values
from .schema import BOOLEAN, ENERGIDATASERVICE_URL, PRICE_PROVIDERS, SPOT_PRICE_PROVIDERS, build_schema, normalize_input, validate_step

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
        if step == "prices_energypriceforecast":
            return "energypriceforecast" in self._providers
        if step in ("prices_spotforecast", "prices_elprognos", "prices_wattcast", "prices_epexspot", "prices_gespot", "prices_octopus", "prices_nordpool"):
            return step.removeprefix("prices_") in self._providers
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

    # True while a new entry is made (not changed): the defaults that are amounts of money follow the price currency then
    _new_entry: bool = False

    # set when a new entry's prices are not in kroner: the starting values of the price rise thresholds are scaled on finish
    _kroner_defaults_scaled = False

    def _apply_price_scale(self, suggest_fee: bool = False) -> None:
        """Store prices.price_scale from the unit of the price sensors (kept as it is when no sensor has a known unit).

        suggest_fee (the page of the fee, before it is shown): a new installation gets the default sell fee (0.03 kroner) and
        wear cost (0.1 kroner) in the price currency, so the user sees the amount that is stored. Not for an import or a
        restored backup (their own values stay) and not once the user has seen the page. For a new installation the
        thresholds follow on finish (_scale_money_defaults)."""
        previous = price_scale({"prices": {"price_scale": self._options.get("price_scale", 1.0)}})
        scale = detect_price_scale(self._options, self.hass.states.get)  # type: ignore[attr-defined]
        if scale is None:
            return
        self._options["price_scale"] = scale
        own_values = getattr(self, "_restore_backup", None) is not None or getattr(self, "_legacy_prefix", None) is not None
        if self._new_entry and not own_values and previous == 1.0 and scale != 1.0:
            self._kroner_defaults_scaled = True
            for key in ("sell_fee", "powerwall_wear_cost_per_kwh"):
                default = default_options()[key]
                if suggest_fee and self._options.get(key) == default:
                    self._options[key] = round(default * scale, 3)

    def _placeholders(self) -> dict[str, str]:
        return {}

    def _notify_services(self) -> list[str]:
        """The notify services and notify entities that can take a push notification ("notify.<name>"): not send_message (it needs an
        entity) and not persistent_notification (that is the bell, not a push)."""
        services = self.hass.services.async_services().get("notify", {})  # type: ignore[attr-defined]
        entities = self.hass.states.async_entity_ids("notify")  # type: ignore[attr-defined]
        return sorted({
            *(f"notify.{name}" for name in services if name not in ("send_message", "persistent_notification")),
            *entities,
        })

    def _page_keys(self, step: str) -> list[str]:
        """The fields of a page; the fee page also asks for the VAT and the surcharge when a wholesale price provider is chosen."""
        keys = FLOW_STEPS[step]
        if step == "price_fees" and any(name in self._providers for name in SPOT_PRICE_PROVIDERS):
            return [*keys, "spot_vat", "spot_surcharge"]
        return keys

    async def _async_page(self, step: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        keys = self._page_keys(step)
        errors: dict[str, str] = {}
        if step == "price_fees" and user_input is None:
            self._apply_price_scale(suggest_fee=True)  # the price pages are done: the fee suggestion follows the currency
        values_for_form = dict(self._options)
        if user_input is not None:
            values = normalize_input(keys, user_input)
            errors = validate_step(step, values)
            if step.startswith("prices_"):
                get_state = self.hass.states.get  # type: ignore[attr-defined]
                registry = er.async_get(self.hass)  # type: ignore[attr-defined]
                unique_id_of = lambda entity_id: getattr(registry.async_get(entity_id), "unique_id", None)  # noqa: E731
                errors = {**price_currency_errors({**self._options, **values}, keys, get_state), **price_sensor_errors(values, get_state, unique_id_of), **errors}
            if not errors:
                self._options.update(values)
                return await self._async_next()
            values_for_form.update(values)
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id=step,
            data_schema=build_schema(
                keys,
                values_for_form,
                self.hass.config.currency,  # type: ignore[attr-defined]
                self._notify_services() if step == "notifications" else None,
            ),
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

    # the country field is suggested from Home Assistant's country once, for a new entry (an import or a backup keeps its own)
    _price_country_shown = False

    def _suggest_price_country(self) -> None:
        own_values = getattr(self, "_restore_backup", None) is not None or getattr(self, "_legacy_prefix", None) is not None
        if self._new_entry and not own_values and not self._price_country_shown:
            country = str(self.hass.config.country or DEFAULT_COUNTRY).upper()  # type: ignore[attr-defined]
            self._options["price_country"] = country
            if country != DEFAULT_COUNTRY:
                self._providers = default_providers(country)
        self._price_country_shown = True

    async def async_step_price_provider(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        self._suggest_price_country()
        if user_input is not None:
            providers = [p for p in user_input.get("price_providers", []) if p in PRICE_PROVIDERS]
            country = str(user_input.get("price_country") or DEFAULT_COUNTRY).upper()
            self._options["price_country"] = country
            if not providers:
                errors["base"] = "no_price_provider"
            elif errors := provider_errors(country, providers):
                self._providers = providers
            else:
                self._providers = providers
                for provider, keys in PRICE_PROVIDER_KEYS.items():
                    if provider not in providers:
                        for key in keys:
                            self._options[key] = ""
                return await self._async_next()
        schema = vol.Schema(
            {
                vol.Required("price_country", default=self._options.get("price_country", DEFAULT_COUNTRY)): selector.CountrySelector(),
                vol.Required("price_providers", default=self._providers): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=PRICE_PROVIDERS, multiple=True, mode=selector.SelectSelectorMode.LIST, translation_key="price_providers")
                ),
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

    async def async_step_prices_energypriceforecast(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_energypriceforecast", user_input)

    async def async_step_prices_spotforecast(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_spotforecast", user_input)

    async def async_step_prices_elprognos(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_elprognos", user_input)

    async def async_step_prices_wattcast(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_wattcast", user_input)

    async def async_step_prices_epexspot(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_epexspot", user_input)

    async def async_step_prices_gespot(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_gespot", user_input)

    async def async_step_prices_octopus(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_octopus", user_input)

    async def async_step_prices_nordpool(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("prices_nordpool", user_input)

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

    async def async_step_storm(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("storm", user_input)

    async def async_step_notifications(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("notifications", user_input)

    async def async_step_advanced(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("advanced", user_input)

    async def async_step_database(self, user_input=None) -> ConfigFlowResult:
        return await self._async_page("database", user_input)
