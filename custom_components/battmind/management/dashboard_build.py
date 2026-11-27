"""Generated sidebar dashboard: the bundled template mapped onto one BattMind instance.

The template (dashboard/battmind.yaml) is written for the prefix ``battmind`` and the
author's price/solar entities. The price chart of each price integration is a file of its own
(dashboard/prices/<provider>.yaml) that the template includes with a ``# @include`` line.
At setup it is rewritten for this config entry:

* own entities ``<platform>.battmind_*`` -> ``<platform>.<prefix>_*``
* external entities (Energi Data Service, Strømligning, Solcast) -> the configured ones
* cards bound to an external entity that is not configured are removed
* the switches and settings of rules that need an unsupported battery action are removed
* a group heading of the analysis left without sections below it is removed (``_drop_empty_groups``)
* ``@@key@@`` placeholders -> ``dashboard.key`` of texts/<language>.json (BattMind's language),
  ``@@forecast_span@@`` -> days_to_predict - 0.5 days,
  ``@@valuta@@`` / ``@@valuta_kwh@@`` -> the currency of Home Assistant

The result is written to .storage and served as a Lovelace dashboard that can be edited in
the UI: once the user saves it, the saved copy is shown instead of the generated one until
it is reset (reset_dashboard button, or deleting the config in the raw configuration editor).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from homeassistant.components import frontend
from homeassistant.components.lovelace import dashboard as lovelace_dashboard
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import cached_json_fragment, json_fragment
from homeassistant.helpers.storage import Store

from ..const import DOMAIN, dashboard_icon, normalize_language, unsupported_actions, unsupported_switches
from ..utils.formatting import load_texts
from ..utils.solar_forecast import KEY_SEMANTICS
from ..prices import price_dashboard_entities
from .dashboard_cards import async_update_card_notification, async_watch_card_resources

_LOGGER = logging.getLogger(__name__)

TEMPLATE = Path(__file__).parent.parent / "dashboard" / "battmind.yaml"

_INCLUDE = re.compile(r"^(?P<indent> *)# @include (?P<name>\S+) *$", re.MULTILINE)
_OWN_ENTITY = re.compile(r"\b(sensor|binary_sensor|switch|number|select|text|button)\.battmind_")
_PLACEHOLDER = re.compile(r"@@(\w+)@@")
_SOLCAST_DAYS = ("tomorrow", "day_3", "day_4", "day_5", "day_6", "day_7")
_DROP = object()
_GAP = object()  # where _prune removed a list item


def expand_includes(text: str, directory: Path = TEMPLATE.parent) -> str:
    """Replace every ``# @include <file>`` line by the lines of directory/<file>, indented like the marker.

    The price chart of each price integration is a file of its own, dashboard/prices/<provider>.yaml
    (<provider> is the key of ``PROVIDER_CLASSES``). A missing file raises: a card must never vanish silently.
    """

    def expand(match: re.Match[str]) -> str:
        lines = (directory / match["name"]).read_text(encoding="utf-8").removesuffix("\n").split("\n")
        return "\n".join(f"{match['indent']}{line}" if line else "" for line in lines)

    return _INCLUDE.sub(expand, text)


def template_text() -> str:
    """The dashboard template with its included files (blocking file I/O). Everything reads the template through this."""
    return expand_includes(TEMPLATE.read_text(encoding="utf-8"))


def dashboard_url(prefix: str) -> str:
    """Frontend url of the dashboard (Lovelace requires a '-' in it)."""
    return f"{prefix.replace('_', '-')}-dashboard"


def dashboard_file(hass: HomeAssistant, entry_id: str) -> Path:
    return Path(hass.config.path(".storage", f"{DOMAIN}.{entry_id}.dashboard.yaml"))


def custom_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """The user's edited copy of the dashboard."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.dashboard_custom")


class BattMindDashboard(lovelace_dashboard.LovelaceYAML):
    """The generated YAML file, but editable: the frontend only offers editing in storage mode."""

    def __init__(self, hass: HomeAssistant, url_path: str, config: dict[str, Any], store: Store[dict[str, Any]], custom: dict[str, Any] | None, entry_id: str) -> None:
        super().__init__(hass, url_path, config)
        self._store = store
        self._custom = custom
        self._custom_json: json_fragment | None = None
        self._entry_id = entry_id

    @property
    def mode(self) -> str:
        return MODE_STORAGE

    @property
    def customized(self) -> bool:
        return self._custom is not None

    async def async_get_info(self) -> dict[str, Any]:
        info = await super().async_get_info()
        return {**info, "mode": self.mode}

    async def async_load(self, force: bool) -> dict[str, Any]:
        if self._custom is not None:
            config = deepcopy(self._custom)
            if any(_has_legacy_what_if_card(view) for view in config.get("views") or ()):
                # a saved dashboard from before the what-if view was made of entities: the view with the removed
                # custom card is replaced by the generated What-if view, the rest of the edits stay
                generated = next(
                    (view for view in (await super().async_load(force)).get("views") or () if view.get("path") == "what-if"),
                    None,
                )
                if generated is not None:
                    config["views"] = [
                        deepcopy(generated) if _has_legacy_what_if_card(view) else view for view in config["views"]
                    ]
            return config
        return await super().async_load(force)

    async def async_json(self, force: bool) -> json_fragment:
        if self._custom is not None:
            if self._custom_json is None:
                self._custom_json = cached_json_fragment(await self.async_load(force))
            return self._custom_json
        return await super().async_json(force)

    async def async_save(self, config: dict[str, Any]) -> None:
        self._custom = config
        self._custom_json = None
        await self._store.async_save({"config": config})
        self._config_updated()

    async def async_delete(self) -> None:
        """Back to the generated dashboard."""
        self._custom = None
        self._custom_json = None
        await self._store.async_remove()
        self._config_updated()


def _has_legacy_what_if_card(node: Any) -> bool:
    """Whether the node contains the custom card the what-if view used before it was made of entities."""
    if isinstance(node, dict):
        return node.get("type") == "custom:battmind-what-if" or any(_has_legacy_what_if_card(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_legacy_what_if_card(value) for value in node)
    return False


def external_entities(config: dict[str, Any], forecast_platform: str | None = None) -> dict[str, str]:
    """Template entity id -> configured entity id ("" = not configured).

    forecast_platform: the integration of the solar forecast sensor (entity registry), None when unknown.
    """
    prices = config["prices"]["entity_ids"]
    home = config["home"]["entity_ids"]
    production = config["solar"]["entity_ids"]["production_entity_id"]
    storm_warning = home.get("storm_warning_entity_id")
    mapping = {
        # status section on the overview
        "sensor.powerwall_charge": home["powerwall_battery_level_entity_id"],
        "sensor.powerwall_battery_now": home["powerwall_watt_flow_entity_id"],
        "sensor.house_power_consumption": home["power_consumption_entity_id"],
        "sensor.solar_power_production": production,
        # only created with solar production (sensor.py); the prefix is replaced afterwards
        "sensor.battmind_kwh_charged_by_solar": "sensor.battmind_kwh_charged_by_solar" if production else "",
        # only created with a storm warning entity (sensor.py, number.py): without it the storm section is left out
        "sensor.battmind_storm_warning": "sensor.battmind_storm_warning" if storm_warning else "",
        "number.battmind_storm_cover_hours": "number.battmind_storm_cover_hours" if storm_warning else "",
        **price_dashboard_entities(prices),
    }
    # Solcast: forecast_today plus the per-day sensors next to it. The charts read their detailedHourly, which
    # Forecast.Solar and Helios Forecast don't have (and Forecast.Solar's energy_production_today would match the
    # _today rule): with those the Solcast entities stay unmapped, so the charts show no solar forecast
    forecast = config["solar"]["entity_ids"]["forecast_entity_id"] if forecast_platform not in KEY_SEMANTICS else ""
    base = forecast[: -len("today")] if forecast.endswith("_today") else ""
    mapping["sensor.solcast_pv_forecast_forecast_today"] = forecast
    for day in _SOLCAST_DAYS:
        mapping[f"sensor.solcast_pv_forecast_forecast_{day}"] = f"{base}{day}" if base else ""
    return mapping


DEBUG_BUTTON_SWITCH = "input_boolean.dezito_show_debug_button"


# settings only used by the rules of an action: hidden with the rule switches when the action is unsupported
_ACTION_SETTINGS = {
    "grid_charging": (
        "number.battmind_cheap_price_periods",
        "number.battmind_cheap_price_period_rise_threshold",
        "number.battmind_cheapest_price_rise_threshold",
        # the full charge is a grid charge: its number and sensor mean nothing without grid charging
        "number.battmind_full_charge_recommended",
        "sensor.battmind_last_full_charge",
    ),
}


def unsupported_entities(config: dict[str, Any]) -> set[str]:
    """Template entity ids of the rules that need an action the battery can't run."""
    unsupported = unsupported_actions(config)
    keys = unsupported_switches(unsupported)
    entities = {f"switch.battmind_{key}" for key in keys}
    # the what-if view has a scenario value for each rule and setting as well
    entities.update(f"select.battmind_what_if_{key}" for key in keys)
    for action in unsupported:
        for entity in _ACTION_SETTINGS.get(action, ()):
            entities.add(entity)
            domain, name = entity.split(".battmind_", 1)
            entities.add(f"{domain}.battmind_what_if_{name}")
    return entities


def _is_type(node: Any, *types: str) -> bool:
    return isinstance(node, dict) and node.get("type") in types


def _tidy(items: list[Any]) -> list[Any]:
    """After a removal (_GAP): no divider at the ends or twice in a row, and no heading left with only its help text."""
    groups: list[list[Any]] = []
    for item in items:
        if not groups or _is_type(item, "heading"):
            groups.append([])
        groups[-1].append(item)
    result: list[Any] = []
    for group in groups:
        if _is_type(group[0], "heading") and _GAP in group and all(item is _GAP or _is_type(item, "heading", "markdown") for item in group):
            continue
        for item in group:
            if item is _GAP:
                continue
            if _is_type(item, "divider") and (not result or _is_type(result[-1], "divider")):
                continue
            result.append(item)
    while result and _is_type(result[-1], "divider"):
        result.pop()
    return result


def _prune(node: Any, missing: set[str]) -> Any:
    """Remove cards/series/list items bound to a missing entity, and containers left empty."""
    if isinstance(node, list):
        items = []
        for item in node:
            if isinstance(item, str) and item in missing:
                continue
            item = _prune(item, missing)
            items.append(_GAP if item is _DROP else item)
        return _tidy(items) if _GAP in items else items
    if isinstance(node, dict):
        if node.get("entity") in missing:
            return _DROP
        # (also visibility: without its entity the card must not end up always visible)
        if any(isinstance(cond, dict) and cond.get("entity") in missing for key in ("conditions", "visibility") for cond in node.get(key) or ()):
            return _DROP
        result = {}
        for key, value in node.items():
            pruned = _prune(value, missing)
            if pruned is _DROP:  # e.g. the single `card:` of a conditional
                return _DROP
            if isinstance(value, list) and value and not pruned and key in ("cards", "series", "entities", "sections"):
                return _DROP
            result[key] = pruned
        return result
    return node


def _is_group_heading(item: Any) -> bool:
    """A full-width section with only a title heading: the heading of a group of analysis sections."""
    cards = item.get("cards") if isinstance(item, dict) else None
    return (
        isinstance(item, dict) and item.get("type") == "grid" and item.get("column_span") == 4
        and isinstance(cards, list) and len(cards) == 1 and isinstance(cards[0], dict)
        and cards[0].get("type") == "heading" and cards[0].get("heading_style") == "title"
    )


def _starts_history(item: Any) -> bool:
    """The History ends the last group: its section has the heading icon mdi:chart-line, or (when that heading was dropped as
    empty) its graphs are sections without any heading."""
    if not isinstance(item, dict) or item.get("type") != "grid":
        return False
    cards = [card for card in item.get("cards") or () if isinstance(card, dict)]
    headings = [card for card in cards if card.get("type") == "heading"]
    return not headings or any(card.get("icon") == "mdi:chart-line" for card in headings)


def _drop_empty_groups(node: Any) -> Any:
    """In the analysis view: a group heading with no section below it (before the next group or the History) is removed."""
    if isinstance(node, list):
        return [_drop_empty_groups(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: _drop_empty_groups(value) for key, value in node.items()}
    sections = node.get("sections")
    if node.get("path") == "analysis" and isinstance(sections, list):
        node["sections"] = [
            item for index, item in enumerate(sections)
            if not (_is_group_heading(item) and (
                index + 1 >= len(sections) or _is_group_heading(sections[index + 1]) or _starts_history(sections[index + 1])
            ))
        ]
    return node


def _translate(node: Any, texts: dict[str, str]) -> Any:
    """Replace @@key@@ in every string with the dashboard text of the language (the key if missing)."""
    if isinstance(node, list):
        return [_translate(item, texts) for item in node]
    if isinstance(node, dict):
        return {key: _translate(value, texts) for key, value in node.items()}
    if isinstance(node, str) and "@@" in node:
        return _PLACEHOLDER.sub(lambda match: texts.get(f"dashboard.{match.group(1)}", match.group(1)), node)
    return node


class _Dumper(yaml.SafeDumper):
    """Keep the JavaScript templates readable: multi-line strings as literal blocks."""


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_str)


def render_dashboard(
    config: dict[str, Any], prefix: str, existing: set[str] | None = None, currency: str | None = None,
    entry_id: str = "",
    forecast_platform: str | None = None,
) -> str:
    """Build the dashboard YAML for one instance (blocking file I/O).

    existing: entity ids that exist in Home Assistant; the debug button is removed when its switch is not among them
    currency: hass.config.currency (None = the currency of the language's texts)
    forecast_platform: the integration of the solar forecast sensor (see external_entities)
    """
    mapping = external_entities(config, forecast_platform)
    template = yaml.safe_load(template_text())
    texts = load_texts(normalize_language(config.get("language")), currency)
    # the charts show the forecast period: days_to_predict minus the half day already passed
    texts["dashboard.forecast_span"] = f"{max(float(config['days_to_predict']) - 0.5, 0.5):g}d"
    template = _translate(template, texts)
    missing = {source for source, target in mapping.items() if not target}
    if existing is not None and DEBUG_BUTTON_SWITCH not in existing:
        missing.add(DEBUG_BUTTON_SWITCH)
    # the rules the battery can't run (unsupported_actions) are off, so their switches and settings are hidden
    missing |= unsupported_entities(config)
    content = yaml.dump(_drop_empty_groups(_prune(template, missing)), Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000)

    replace = {source: target for source, target in mapping.items() if target and source != target}
    if replace:
        pattern = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, sorted(replace, key=len, reverse=True))) + r")(?!\w)")
        content = pattern.sub(lambda match: replace[match.group(1)], content)
    if prefix != "battmind":
        content = _OWN_ENTITY.sub(lambda match: f"{match.group(1)}.{prefix}_", content)
    return content


def _write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


async def async_setup_dashboard(
    hass: HomeAssistant,
    entry: ConfigEntry,
    config: dict[str, Any],
    prefix: str,
    translate: Callable[..., str] | None = None,
) -> None:
    """Render the dashboard and add it to the sidebar until the entry unloads."""
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None:
        _LOGGER.warning("Lovelace is not loaded, the BattMind dashboard is not available")
        return
    url_path = dashboard_url(prefix)
    existing = lovelace.dashboards.get(url_path)
    if existing is not None and not isinstance(existing, BattMindDashboard):
        _LOGGER.warning("A dashboard already uses /%s, the BattMind dashboard is not added", url_path)
        return

    path = dashboard_file(hass, entry.entry_id)

    existing_ids = set(hass.states.async_entity_ids())
    forecast_entity = config["solar"]["entity_ids"]["forecast_entity_id"]
    forecast_entry = er.async_get(hass).async_get(forecast_entity) if forecast_entity else None
    forecast_platform = forecast_entry.platform if forecast_entry else None

    def _render() -> bool:
        content = render_dashboard(config, prefix, existing_ids, hass.config.currency, entry.entry_id, forecast_platform)
        return _write_if_changed(path, content)

    try:
        changed = await hass.async_add_executor_job(_render)
    except (OSError, yaml.YAMLError) as err:
        _LOGGER.error("Could not create the BattMind dashboard: %s", err)
        return

    store = custom_store(hass, entry.entry_id)
    stored = await store.async_load()
    title = "BattMind" if prefix == "battmind" else f"BattMind ({prefix})"
    icon = dashboard_icon(config.get("dashboard_icon"))
    battmind_dashboard = BattMindDashboard(
        hass,
        url_path,
        {"mode": MODE_STORAGE, "filename": str(path), "title": title, "icon": icon, "show_in_sidebar": True, "require_admin": False},
        store,
        stored["config"] if stored else None,
        entry.entry_id,
    )
    lovelace.dashboards[url_path] = battmind_dashboard
    frontend.async_register_built_in_panel(
        hass,
        "lovelace",
        frontend_url_path=url_path,
        sidebar_title=title,
        sidebar_icon=icon,
        require_admin=False,
        config={"mode": MODE_STORAGE},
        update=existing is not None,
    )
    _LOGGER.info("Dashboard available at /%s%s", url_path, " (edited in the UI)" if battmind_dashboard.customized else " (updated)" if changed else "")

    @callback
    def _remove() -> None:
        if lovelace.dashboards.get(url_path) is battmind_dashboard:
            lovelace.dashboards.pop(url_path)
            frontend.async_remove_panel(hass, url_path)

    entry.async_on_unload(_remove)

    if translate is not None:
        # with the coordinator's ``t`` a persistent notification lists the HACS cards that are missing
        try:
            await async_update_card_notification(hass, translate)
            async_watch_card_resources(hass, entry, translate)
        except Exception:  # noqa: BLE001 - the card check must never keep the dashboard from working
            _LOGGER.exception("Could not check the dashboard cards")


async def async_reset_dashboard(hass: HomeAssistant, prefix: str) -> None:
    """Drop the user's edits: the generated dashboard is shown again."""
    lovelace = hass.data.get(LOVELACE_DATA)
    dashboard = lovelace.dashboards.get(dashboard_url(prefix)) if lovelace else None
    if isinstance(dashboard, BattMindDashboard):
        await dashboard.async_delete()


async def async_remove_dashboard_file(hass: HomeAssistant, entry_id: str) -> None:
    await hass.async_add_executor_job(dashboard_file(hass, entry_id).unlink, True)
    await custom_store(hass, entry_id).async_remove()
