"""Pages shared by the config flow and the options flow."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlowResult
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from ..const import ENTITY_OPTION_KEYS, FLOW_STEPS, OPTIONS_MENU, PRICE_PROVIDER_KEYS, PRICE_PROVIDER_PRIORITY, default_options, price_scale
from ..management.dependency_checks import NOTHING, disabled_sensors, sensors_registered, solcast_flow_problems
from ..prices import STROMLIGNING_PAGE_PREFIXES, STROMLIGNING_PLATFORM, STROMLIGNING_UNUSED_PREFIXES, detect_price_scale, has_stromligning_sell_prices, price_currency_errors, price_sensor_errors, DEFAULT_COUNTRY, default_providers, provider_errors, providers_for_country, unconfirmed_providers, provider_sections
from ..utils.cjp import cjp_charger_power_entities
from ..utils.solcast import SOLCAST_PLATFORM, solcast_endings_for_days
from ..utils.solar_sources import CONFIRMED_SOLAR_SOURCES, NO_SOLAR_SOURCE, SOLAR_SOURCES
from .detection import Detection, async_detect, evidence_placeholders, verdict_values
from .schema import BOOLEAN, ENERGIDATASERVICE_URL, PRICE_PROVIDER_NAMES, PRICE_PROVIDERS, SELECTORS, REQUIRED_KEYS, SPOT_PRICE_PROVIDERS, UNCONFIRMED_SUFFIX, ProviderSelector, build_schema, forecast_selector, normalize_input, provider_of, validate_step

# the sensors the suggestions are made from; they are only made again when one of them was changed in this session
DETECTION_SENSORS = ("power_consumption_entity_id", "powerwall_watt_flow_entity_id", "powerwall_battery_level_entity_id")
REDETECT = "redetect"  # a switch of the review page, not an option


# each choice of a solar forecast has its own solar page: a step id of its own gives it its own texts
SOLAR_PAGES = {"solcast_solar": "solar_solcast", "forecast_solar": "solar_forecast_solar", "helios_forecast": "solar_helios"}
# the Solcast page when every sensor the plan needs is ready: it says so, instead of an empty list of problems
SOLCAST_READY_PAGE = "solar_solcast_ready"


class BattMindFlowPages:
    """Pages shared by the config flow and the options flow.

    The subclass sets ``self._options`` (flat option dict), ``self._queue`` (remaining
    page ids) and implements ``_async_finish``.
    """

    _options: dict[str, Any]
    _queue: list[str]
    _providers: list[str]
    # the solar forecast chosen on the page before the solar page (not stored); None when that page was not shown
    _solar_source: str | None = None
    # why the price provider page is shown again (a price page was sent without a required sensor)
    _price_error: str = ""
    # the price page last sent without required sensors, and which: sent again like that, it leads back to the providers
    _price_warned: tuple[str, ...] = ()
    # what the Solcast page last shown listed as not ready
    _solcast_shown: str = ""
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
        if step == "solar" and self._solar_source not in SOLAR_SOURCES:
            keys = [key for key in keys if key != "forecast_entity_id"]  # no forecast: the page before cleared it
        errors: dict[str, str] = {}
        if step == "price_fees" and user_input is None:
            self._apply_price_scale(suggest_fee=True)  # the price pages are done: the fee suggestion follows the currency
        values_for_form = dict(self._options)
        if user_input is not None:
            values = normalize_input(keys, user_input)
            errors = validate_step(step, values)
            if step == "solar":
                errors = {**self._solar_errors(values), **errors}
            if step.startswith("prices_"):
                get_state = self.hass.states.get  # type: ignore[attr-defined]
                registry = er.async_get(self.hass)  # type: ignore[attr-defined]
                unique_id_of = lambda entity_id: getattr(registry.async_get(entity_id), "unique_id", None)  # noqa: E731
                errors = {**price_currency_errors({**self._options, **values}, keys, get_state), **price_sensor_errors(values, get_state, unique_id_of), **errors}
            missing = [key for key in self._required_price_sensors(step, keys) if not values.get(key)]
            warned, self._price_warned = self._price_warned, (step, *missing) if missing else ()
            if missing:
                errors = {key: value for key, value in errors.items() if key not in missing}
                if not errors and warned == self._price_warned:
                    # back to the providers, then every price page of the choice again: never past a required sensor
                    self._price_warned = ()
                    self._options.update(values)
                    self._queue = [*OPTIONS_MENU["prices"][1:], *(page for page in self._queue if page not in OPTIONS_MENU["prices"])]
                    self._price_error = "price_sensor_not_chosen"
                    return await self.async_step_price_provider()
                # the first time the page stays, so a forgotten field costs nothing; the field says how to leave
                errors.update(dict.fromkeys(missing, "price_sensor_required"))
            if not errors and step == "solar" and self._solar_source == "solcast_solar" and values.get("forecast_entity_id"):
                # the page judged the sensor it showed: another one that lacks something else is shown once, it never blocks
                waits = self._solcast_waiting(values["forecast_entity_id"])
                if waits and waits != self._solcast_shown:
                    errors = {"base": "solcast_check_again"}
            if not errors and step == "solar" and self._solar_source in SOLAR_SOURCES and not values.get("forecast_entity_id"):
                # no sensor of the chosen forecast: back to the choice (a flow has no back button), the page's values kept
                self._options.update(values)
                self._queue.insert(0, "solar")
                return self._show_solar_source({"base": "forecast_sensor_missing"})
            if not errors:
                previous_country = self._price_country()
                self._options.update(values)
                if step == "general" and not self._new_entry and self._price_country() != previous_country and set(self._providers) - set(providers_for_country(self._price_country(), PRICE_PROVIDERS)):
                    # the country was changed and a chosen provider has no prices in the new one: the price pages follow, to choose again
                    self._queue = list(OPTIONS_MENU["prices"])
                return await self._async_next()
            values_for_form.update(values)
        data_schema = build_schema(
            keys,
            values_for_form,
            self.hass.config.currency,  # type: ignore[attr-defined]
            self._notify_services() if step == "notifications" else None,
        )
        if step == "solar":
            data_schema = self._solar_schema(data_schema)
        required = self._required_price_sensors(step, keys)
        if required:
            # optional in the form, so the page can be sent without them (see above)
            data_schema = vol.Schema({
                vol.Optional(str(key), description=key.description) if str(key) in required else key: field
                for key, field in data_schema.schema.items()
            })
        shown = SOLAR_PAGES.get(self._solar_source, step) if step == "solar" else step
        solcast_waits = ""
        if shown == SOLAR_PAGES["solcast_solar"]:
            solcast_waits = self._solcast_waiting(values_for_form.get("forecast_entity_id") or "")
            self._solcast_shown = solcast_waits
            shown = shown if solcast_waits else SOLCAST_READY_PAGE
        stromligning_off = ""
        if step in STROMLIGNING_PAGE_PREFIXES:
            # a page of its own for "all enabled" and for "none found": an empty list of disabled ones would not say which
            prefixes = STROMLIGNING_PAGE_PREFIXES[step]
            stromligning_off = disabled_sensors(self.hass, STROMLIGNING_PLATFORM, prefixes, STROMLIGNING_UNUSED_PREFIXES)  # type: ignore[attr-defined]
            if not sensors_registered(self.hass, STROMLIGNING_PLATFORM, prefixes, STROMLIGNING_UNUSED_PREFIXES):  # type: ignore[attr-defined]
                shown = f"{step}_missing"
            elif stromligning_off == NOTHING:
                shown = f"{step}_ready"
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id=shown,
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                **self._placeholders(),
                "eds_url": ENERGIDATASERVICE_URL,
                # the chargers BattMind leaves out of the house consumption by itself (the consumption page)
                "cjp_chargers": ", ".join(cjp_charger_power_entities(self.hass)) or "–",  # type: ignore[attr-defined]
                # the disabled sensors of Strømligning on its pages (the picker can't offer them), and what the Solcast sensors lack
                "stromligning_disabled": stromligning_off,
                "solcast_problems": solcast_waits,
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

    async def async_step_country(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The country of the prices: the page before the providers (the options have it on the General page).

        Home Assistant's country is suggested for a new entry; a restored backup or an import shows its own, as before."""
        own_values = getattr(self, "_restore_backup", None) is not None or getattr(self, "_legacy_prefix", None) is not None
        if user_input is not None:
            country = str(user_input.get("price_country") or DEFAULT_COUNTRY).upper()
            self._options["price_country"] = country
            if not own_values:
                self._providers = default_providers(country)
            return await self._async_next()
        country = self._options.get("price_country") if own_values else self.hass.config.country  # type: ignore[attr-defined]
        schema = vol.Schema({vol.Required("price_country", default=str(country or DEFAULT_COUNTRY).upper()): SELECTORS["price_country"]})
        return self.async_show_form(step_id="country", data_schema=schema, description_placeholders=self._placeholders())  # type: ignore[attr-defined]

    def _price_country(self) -> str:
        """The country of the prices (the country page of the config flow, the General page of the options): it decides which providers are offered."""
        return str(self._options.get("price_country") or DEFAULT_COUNTRY).upper()

    async def async_step_solar_forecast_source(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Which solar forecast: the page before the solar page, whose sensor picker then offers that integration's sensors.

        The choice is not stored: it is read again from the platform of the forecast sensor. A sensor whose platform is not
        known (not in the entity registry, e.g. in a restored backup) is never cleared."""
        stored = self._options.get("forecast_entity_id") or ""
        platform = self._platform_of(stored)
        if user_input is not None:
            source = provider_of(str(user_input.get("solar_forecast_source") or NO_SOLAR_SOURCE))
            self._solar_source = source
            if source == NO_SOLAR_SOURCE or (platform is not None and platform != source):
                self._options["forecast_entity_id"] = ""  # no forecast, or a sensor of another source than the chosen one
            return await self._async_next()
        return self._show_solar_source()

    def _show_solar_source(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        stored = self._options.get("forecast_entity_id") or ""
        platform = self._platform_of(stored)
        # a value shows the label: the source's name, with the mark where nobody has confirmed it
        listed = {
            source: source if source == NO_SOLAR_SOURCE or source in CONFIRMED_SOLAR_SOURCES else source + UNCONFIRMED_SUFFIX
            for source in (NO_SOLAR_SOURCE, *SOLAR_SOURCES)
        }
        # a stored sensor of no forecast source (or one that is not known) preselects nothing: a guess would relabel it
        chosen = NO_SOLAR_SOURCE if not stored else platform if platform in SOLAR_SOURCES else None
        marker = vol.Required("solar_forecast_source", default=listed[chosen]) if chosen else vol.Required("solar_forecast_source")
        schema = vol.Schema(
            {
                marker: ProviderSelector(
                    selector.SelectSelectorConfig(options=list(listed.values()), mode=selector.SelectSelectorMode.LIST, translation_key="solar_forecast_sources")
                )
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="solar_forecast_source" if chosen else "solar_forecast_source_unknown", data_schema=schema, errors=errors or {},
            description_placeholders={**self._placeholders(), "stored_sensor": stored},
        )

    async def async_step_solar_solcast(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_page("solar", user_input)

    async def async_step_prices_stromligning_ready(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_page("prices_stromligning", user_input)

    async def async_step_prices_stromligning_missing(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_page("prices_stromligning", user_input)

    async def async_step_prices_stromligning_tariffs_ready(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_page("prices_stromligning_tariffs", user_input)

    async def async_step_prices_stromligning_tariffs_missing(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_page("prices_stromligning_tariffs", user_input)

    @staticmethod
    def _required_price_sensors(step: str, keys: list[str]) -> list[str]:
        """The sensors a price page can't do without."""
        return [key for key in keys if step.startswith("prices_") and key in REQUIRED_KEYS and key in ENTITY_OPTION_KEYS]

    def _solcast_waiting(self, forecast_entity: str) -> str:
        """What the Solcast sensors the plan needs still lack, "" when all are ready."""
        # all seven days while the Advanced page, where the days to plan are chosen, is still to come
        days = 7 if "advanced" in self._queue else int(self._options.get("days_to_predict") or default_options()["days_to_predict"])
        return solcast_flow_problems(self.hass, forecast_entity, solcast_endings_for_days(days))  # type: ignore[attr-defined]

    async def async_step_solar_forecast_source_unknown(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_solar_forecast_source(user_input)

    async def async_step_solar_solcast_ready(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_page("solar", user_input)

    async def async_step_solar_forecast_solar(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_page("solar", user_input)

    async def async_step_solar_helios(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_page("solar", user_input)

    def _platform_of(self, entity_id: str) -> str | None:
        """The integration of an entity in the entity registry, None when it is not known."""
        entry = er.async_get(self.hass).async_get(entity_id) if entity_id else None  # type: ignore[attr-defined]
        return entry.platform if entry else None

    def _solar_errors(self, values: dict[str, Any]) -> dict[str, str]:
        """The forecast sensor must be of the source chosen on the page before (an unknown platform is accepted)."""
        source = self._solar_source
        if source not in SOLAR_SOURCES:
            return {}
        entity_id = values.get("forecast_entity_id")
        if entity_id and self._platform_of(entity_id) not in (None, source):
            return {"forecast_entity_id": "forecast_wrong_source"}
        return {}

    def _solar_schema(self, schema: vol.Schema) -> vol.Schema:
        """The sensor picker offers the chosen source's sensors only. A stored sensor whose platform is not known keeps the
        picker of every source, so it can still be seen."""
        source = self._solar_source
        stored = self._options.get("forecast_entity_id") or ""
        if source not in SOLAR_SOURCES or (stored and self._platform_of(stored) is None):
            return schema
        return vol.Schema({key: forecast_selector(source) if key == "forecast_entity_id" else value for key, value in schema.schema.items()})

    async def async_step_price_provider(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {"base": self._price_error} if self._price_error else {}
        self._price_error = ""
        country = self._price_country()
        # only the providers that have prices in the country, in the order their prices are taken in (the lists follow it)
        offered = providers_for_country(country, PRICE_PROVIDER_PRIORITY)
        unconfirmed = unconfirmed_providers(country, offered)
        listed = {p: p + UNCONFIRMED_SUFFIX if p in unconfirmed else p for p in offered}  # the value shows 🧪
        if user_input is not None:
            picked = {provider_of(value) for part in provider_sections(offered) for value in user_input.get(part, {}).get("providers", [])}
            providers = [p for p in offered if p in picked]
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
        # what was chosen before stays as far as the country has it; nothing left starts as a new entry in the country does
        chosen = [p for p in self._providers if p in offered] or default_providers(country)
        # two sections, both open: the providers with a forecast, and those with published prices only
        fields = {
            vol.Required(part): section(
                vol.Schema(
                    {
                        vol.Required("providers", default=[listed[p] for p in names if p in chosen]): ProviderSelector(
                            selector.SelectSelectorConfig(
                                options=[listed[p] for p in names], multiple=True, mode=selector.SelectSelectorMode.LIST, translation_key="price_providers"
                            )
                        )
                    }
                ),
                {"collapsed": False},
            )
            for part, names in provider_sections(offered).items()
            if names
        }
        schema = vol.Schema(fields)
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="price_provider",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                **self._placeholders(),
                "country": country,
                # what the country does not have, so the user sees why a provider is missing from the list
                "unavailable": ", ".join(PRICE_PROVIDER_NAMES[p] for p in PRICE_PROVIDERS if p not in offered) or "–",
                # the order the prices are taken in, for the providers the country has
                "priority": " → ".join(PRICE_PROVIDER_NAMES[p] for p in offered),
            },
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
