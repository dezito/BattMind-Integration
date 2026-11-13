"""The generated dashboard: every language, every setting, entity names without the device name."""
from __future__ import annotations

import pytest
import yaml

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battmind.const import (
    ACTION_SWITCHES,
    CONTROLLABLE_ACTIONS,
    LANGUAGES,
    NUMBER_SETTINGS,
    SELECT_SETTINGS,
    SWITCH_SETTINGS,
    TEXT_SETTINGS,
    WHAT_IF_BUTTON_KEYS,
    WHAT_IF_FLAGS,
    WHAT_IF_INPUT_NUMBERS,
    WHAT_IF_INPUT_SWITCHES,
    WHAT_IF_OVERRIDE_NUMBERS,
    WHAT_IF_OVERRIDE_TEXTS,
    WHAT_IF_RULE_SELECTS,
    build_config,
)
from custom_components.battmind.management.dashboard_build import render_dashboard
from custom_components.battmind.utils.formatting import format_analysis_help_text, format_help_text, load_texts

from .conftest import MOCK_OPTIONS, PREFIX, async_unload_and_join, set_source_states
from .test_engine import OPTIONS


def rendered(language: str, prefix: str = "battmind") -> str:
    return render_dashboard(build_config({**OPTIONS, "language": language}), prefix)


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_every_language_renders_without_placeholders(language: str) -> None:
    content = rendered(language)
    assert "@@" not in content
    views = yaml.safe_load(content)["views"]
    assert [view["path"] for view in views] == ["overview", "settings", "analysis", "what-if"]


def _what_if_view(language: str, prefix: str = "battmind", **options) -> dict:
    content = render_dashboard(build_config({**OPTIONS, "language": language, **options}), prefix)
    return next(view for view in yaml.safe_load(content)["views"] if view["path"] == "what-if")


def _what_if_entities(view: dict) -> set[str]:
    """Every entity the view shows: entity rows, chart series and visibility conditions."""
    entities = {node["entity"] for node in walk(view) if isinstance(node.get("entity"), str)}
    for node in walk(view):
        if node.get("type") == "custom:config-template-card":
            entities |= set(node.get("entities", ()))
    return entities


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_what_if_view_is_made_of_standard_cards_and_entities(language: str) -> None:
    """The view has no custom card of BattMind: entities for the inputs, markdown and charts for the result."""
    view = _what_if_view(language, "house")
    text = yaml.safe_dump(view)
    assert "custom:battmind-what-if" not in text and "config_entry_id" not in text
    assert "/config/tools/action?service=battmind.what_if" in text

    expected = {
        *(f"number.house_{key}" for key in WHAT_IF_INPUT_NUMBERS),
        *(f"switch.house_{key}" for key in WHAT_IF_INPUT_SWITCHES),
        *(f"select.house_{key}" for key in WHAT_IF_RULE_SELECTS),
        *(f"number.house_{key}" for key in WHAT_IF_OVERRIDE_NUMBERS),
        *(f"text.house_{key}" for key in WHAT_IF_OVERRIDE_TEXTS),
        *(f"button.house_{key}" for key in WHAT_IF_BUTTON_KEYS),
        *(f"binary_sensor.house_{key}" for key in WHAT_IF_FLAGS),
        "sensor.house_what_if",
        "sensor.house_what_if_status",
    }
    assert _what_if_entities(view) >= expected
    # the progress sensor is read in the template of the progress card
    assert "sensor.house_what_if_progress" in text
    # no card for another instance or the default prefix is left behind
    assert not {entity for entity in _what_if_entities(view) if ".battmind_" in entity}
    other = yaml.safe_dump(_what_if_view(language))
    assert "sensor.battmind_what_if" in other and "house_" not in other


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_what_if_view_texts_have_no_corrupted_characters(language: str) -> None:
    view = _what_if_view(language)
    strings = [value for node in walk(view) for value in node.values() if isinstance(value, str)]
    assert strings
    assert all("\ufffd" not in value and "Ã" not in value for value in strings)
    if language == "da":
        text = yaml.safe_dump(view, allow_unicode=True)
        assert "Batteristørrelse (kWh)" in text or "Nuværende" in text


def _entity_names(language: str) -> dict[str, str]:
    import json
    from pathlib import Path

    path = Path(__file__).parents[1] / "custom_components" / "battmind" / "translations" / f"{language}.json"
    entity = json.loads(path.read_text(encoding="utf-8"))["entity"]
    return {f"{platform}.{key}": value["name"] for platform, items in entity.items() for key, value in items.items()}


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_what_if_read_more_covers_the_entities_shown_beneath_it(language: str) -> None:
    """Each "Read more" names every control of the card it belongs to (the entity name without its prefix)."""
    names = _entity_names(language)
    texts = load_texts(language)
    inputs_help = texts["dashboard.what_if_inputs_explain"]
    rules_help = texts["dashboard.what_if_rules_explain"]
    prefix = "What-if – "
    for platform, keys in (
        ("number", WHAT_IF_INPUT_NUMBERS), ("switch", WHAT_IF_INPUT_SWITCHES),
    ):
        for key in keys:
            label = names[f"{platform}.{key}"].removeprefix(prefix)
            assert label in inputs_help, (language, key, label)
    assert names["switch.what_if_run"] in inputs_help  # one switch: on runs, off stops
    # the rules card: the rule selectors and settings are explained as groups, the text and the reset by name
    assert names["text.what_if_exclude_sell_hours"].removeprefix(prefix) in rules_help
    assert names["button.reset_what_if_settings"].removeprefix(prefix) in rules_help
    view = _what_if_view(language)
    markdown = [node["content"] for node in walk(view) if node.get("type") == "markdown"]
    assert any(inputs_help in content for content in markdown)
    assert any(rules_help in content for content in markdown)


def test_what_if_view_hides_the_scenario_values_of_unsupported_rules() -> None:
    all_entities = _what_if_entities(_what_if_view("en"))
    for action in CONTROLLABLE_ACTIONS:
        entities = _what_if_entities(_what_if_view("en", unsupported_actions=[action]))
        hidden = {f"select.battmind_what_if_{key}" for key in ACTION_SWITCHES[action]}
        assert hidden <= all_entities
        assert not hidden & entities
        if action == "grid_charging":
            assert "number.battmind_what_if_cheap_price_periods" in all_entities
            assert not any(entity.startswith("number.battmind_what_if_cheap") for entity in entities)
        else:
            assert {entity for entity in all_entities - entities if entity.startswith("number.")} == set()


def _entities_by_heading(view: dict) -> dict[str, set[str]]:
    """The entity ids (without domain, prefix and what-if) shown beneath each heading of a view's sections."""
    groups: dict[str, set[str]] = {}
    for section in view["sections"]:
        heading = None
        for card in section.get("cards", ()):
            if card.get("type") == "heading":
                heading = card["heading"]
            elif card.get("type") == "entities" and heading:
                for row in card["entities"]:
                    if isinstance(row, dict) and "entity" in row:
                        key = row["entity"].split(".", 1)[1].removeprefix("battmind_").removeprefix("what_if_")
                        groups.setdefault(heading, set()).add(key)
    return groups


def test_what_if_rules_are_in_the_sections_of_the_settings_view() -> None:
    """Each scenario rule and setting is beneath the same heading as the rule or setting it overrides."""
    what_if = _entities_by_heading(_what_if_view("en"))
    settings = _entities_by_heading(yaml.safe_load(rendered("en"))["views"][1])
    sections = {"Control", "Profit requirements (all rules)", "Charging from the grid", "Discharging", "Selling", "Solar"}
    assert sections <= set(what_if) and sections <= set(settings)
    for heading in sections:
        assert what_if[heading] <= settings[heading], (heading, what_if[heading] - settings[heading])
    grouped = set().union(*(what_if[heading] for heading in sections))
    assert grouped == {key.removeprefix("what_if_") for key in (*WHAT_IF_RULE_SELECTS, *WHAT_IF_OVERRIDE_NUMBERS, *WHAT_IF_OVERRIDE_TEXTS)}


def test_what_if_rule_sections_leave_no_orphan_heading_when_their_rules_are_unsupported() -> None:
    """A section whose rules the battery cannot run is removed with its heading."""
    headings = lambda view: [card["heading"] for section in view["sections"] for card in section.get("cards", ()) if card.get("type") == "heading"]  # noqa: E731
    full = headings(_what_if_view("en"))
    reduced = headings(_what_if_view("en", unsupported_actions=["grid_charging"]))
    assert "Charging from the grid" in full and "Charging from the grid" not in reduced
    assert [heading for heading in full if heading != "Charging from the grid"] == reduced
    for section in _what_if_view("en", unsupported_actions=["grid_charging"])["sections"]:
        assert section.get("cards")
    # no entity is shown twice
    rows = [node["entity"] for node in walk(_what_if_view("en")) if isinstance(node.get("entity"), str) and node["entity"].startswith(("select.", "number.", "text.", "button.", "switch."))]
    assert len(rows) == len(set(rows))


def test_what_if_view_has_the_layout_of_the_dev_environment() -> None:
    """Controls first (inputs and the rules in the sections of the settings view), then the results in two columns."""
    view = _what_if_view("en")
    assert view["max_columns"] == 4 and view["dense_section_placement"] is True
    sections = view["sections"]
    assert [section.get("column_span", 1) for section in sections] == [4, 1, 1, 1, 1, 4, 4, 2, 2, 2, 2, 4]
    assert [card["type"] for card in sections[0]["cards"]] == ["heading", "markdown"]
    inputs = sections[1]["cards"][0]
    assert inputs["type"] == "entities" and inputs["entities"][0]["entity"] == "number.battmind_what_if_history_days"
    # the rules: the explanation and the reset button, then the sections of the settings view
    assert [card["type"] for card in sections[2]["cards"][:3]] == ["heading", "markdown", "entities"]
    headings = lambda section: [card["heading"] for card in section["cards"] if card["type"] == "heading"]  # noqa: E731
    assert headings(sections[2])[1:] == ["Control", "Profit requirements (all rules)"]
    assert headings(sections[3]) == ["Charging from the grid", "Solar"] and headings(sections[4]) == ["Discharging", "Selling"]
    # the progress of a running simulation (below the Selling section), then the heading above the results
    progress = sections[5]
    message, bar = progress["cards"]
    assert message["type"] == "markdown" and "progress" in message["content"]
    # the timer bar card: the sensor is the time left (1 second = 1 %) of 100 seconds, inverted, shown as "42 %"
    assert bar["type"] == "custom:timer-bar-card" and bar["entity"] == "sensor.battmind_what_if_progress"
    assert (bar["state_attribute"], bar["active_state"]) == ("status", ["running", "stopping"])
    assert bar["duration"] == {"fixed": 100, "units": "seconds"} and bar["remain_time"] == {"state": True, "units": "seconds"}
    assert bar["invert"] is True and bar["format"] == "%s %"
    assert {c["entity"] for cond in progress["visibility"] for c in cond["conditions"]} == {"sensor.battmind_what_if_status"}
    assert {c["state"] for cond in progress["visibility"] for c in cond["conditions"]} == {"running", "stopping"}
    assert [card["type"] for card in sections[6]["cards"]] == ["heading"] and sections[6]["cards"][0]["heading"] == "Results"

    # the status and the result with the monthly chart, then the module comparison, the capacity interval and the rules table
    result = sections[7]["cards"]
    assert [card["type"] for card in result] == ["markdown", "conditional"]
    assert "█" not in result[0]["content"]  # the bar is in its own card
    assert "sensor.battmind_what_if_status" in result[0]["content"] and result[0]["grid_options"]["columns"] == "full"

    def conditions(section: dict) -> set[str]:
        return {c["entity"] for node in walk(section) if node.get("type") == "conditional" for c in node["conditions"]}

    assert conditions(sections[8]) == {"binary_sensor.battmind_what_if_has_modules", "binary_sensor.battmind_what_if_has_months"}
    assert conditions(sections[9]) == {"binary_sensor.battmind_what_if_has_range", "binary_sensor.battmind_what_if_has_months"}
    assert "v.rules" in sections[10]["cards"][0]["content"]
    # a section that only holds a part of the result is hidden until the result has that part
    assert [section["visibility"][0]["entity"] for section in sections[8:11]] == [
        "binary_sensor.battmind_what_if_has_modules", "binary_sensor.battmind_what_if_has_range", "sensor.battmind_what_if",
    ]
    # no card option was lost in the layout: nothing in the view is empty (null)
    assert [node for node in walk(view) for value in node.values() if value is None] == []


def test_the_charts_of_the_range_and_the_modules_are_higher() -> None:
    """The monthly charts under the range and the module tables are 50 % higher than the others, the payback chart 100 %."""
    texts = load_texts("en")
    titles = {texts["dashboard.what_if_module_monthly_range_title"], texts["dashboard.what_if_module_monthly_modules_title"]}
    charts = [node for node in walk(_what_if_view("en")) if node.get("type") == "custom:apexcharts-card" and isinstance(node.get("header"), dict)]
    halves = [node for node in charts if " ".join(str(node["header"].get("title", "")).split()) in titles]
    assert len(halves) == 2
    assert {node["apex_config"]["chart"]["height"] for node in halves} == {"420px"}
    # the other what-if charts (results by month, profit, interval, payback) stay at 280 px
    from custom_components.battmind.engine.planner.what_if_charts import CHART_HEIGHT, PAYBACK_HEIGHT

    assert (CHART_HEIGHT, PAYBACK_HEIGHT) == ("280px", "560px")
    others = [node for node in walk(_what_if_view("en")) if node.get("type") == "custom:apexcharts-card" and node not in halves]
    assert all(node["apex_config"] == "${vars.chart.apex}" or node["apex_config"]["chart"]["height"] == "280px" for node in others)


def test_what_if_page_shows_money_with_two_decimals_and_kwh_with_one() -> None:
    view = _what_if_view("en")
    for node in walk(view):
        if isinstance(node.get("content"), str) and "{% macro" in node["content"]:
            assert "kWh{%- endmacro" not in node["content"].replace("'%.1f' | format(x) }} kWh{%- endmacro", "")
            assert "'%g'" not in node["content"]
    charts = [node for node in walk(view) if node.get("type") == "custom:apexcharts-card"]
    assert charts
    for chart in charts:  # every chart of the page is in the currency (or the currency per kWh)
        assert chart["yaxis"] == [{"decimals": 2}] and chart["all_series_config"]["float_precision"] == 2


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_the_colour_notes_of_the_charts_are_only_shown_when_the_chart_is_coloured(language: str) -> None:
    texts = load_texts(language)
    markdown = [node["content"] for node in walk(_what_if_view(language)) if node.get("type") == "markdown" and "content" in node]
    for flag, key in (("rg.profit_chart.gradient", "what_if_chart_growth_legend"), ("rg.interval_chart.gradient", "what_if_interval_color_legend")):
        note = texts[f"dashboard.{key}"]
        card = next(content for content in markdown if note in content)
        assert card.split(note)[0].rstrip().endswith("{% if " + flag + " %}")


def test_what_if_charts_only_show_when_the_result_has_their_part() -> None:
    """A chart of a capacity interval, a module comparison or months is behind the binary sensor of that part."""
    view = _what_if_view("en")
    for node in walk(view):
        if node.get("type") != "conditional":
            continue
        entities = {condition["entity"] for condition in node["conditions"]}
        assert entities <= {f"binary_sensor.battmind_{key}" for key in WHAT_IF_FLAGS}
    sections = [section for section in walk(view) if section.get("type") == "conditional"]
    assert {condition["entity"] for node in sections for condition in node["conditions"]} == {
        f"binary_sensor.battmind_{key}" for key in WHAT_IF_FLAGS
    }


def test_texts_follow_the_language() -> None:
    da = yaml.safe_load(rendered("da"))
    en = yaml.safe_load(rendered("en"))
    assert da["views"][1]["title"] == "Indstillinger"
    assert en["views"][1]["title"] == "Settings"
    assert da["views"][0]["sections"][0]["cards"][0]["header"]["title"] == "Husbatteri SOC prognose"
    assert en["views"][0]["sections"][0]["cards"][0]["header"]["title"] == "Home battery SOC forecast"


def test_currency_follows_home_assistant() -> None:
    texts = load_texts("en", "DKK")
    assert (texts["ui.common.valuta"], texts["ui.common.valuta_kwh"]) == ("kr", "kr/kWh")
    assert texts["dashboard.price_chart_title"].endswith("(kr/kWh)")
    assert load_texts("da", "EUR")["ui.common.valuta_kwh"] == "€/kWh"
    assert load_texts("da", "CHF")["ui.common.valuta"] == "CHF"
    content = render_dashboard(build_config({**OPTIONS, "language": "da"}), "battmind", currency="EUR")
    assert "€/kWh" in content and "kr/kWh" not in content and "{valuta" not in content


def test_every_language_has_every_text() -> None:
    """A key missing in a language (and in English, the fallback) is shown as the raw key."""
    import json
    from pathlib import Path

    def flat(data: dict, prefix: str = "") -> set[str]:
        keys: set[str] = set()
        for key, value in data.items():
            keys |= flat(value, f"{prefix}{key}.") if isinstance(value, dict) else {f"{prefix}{key}"}
        return keys

    folder = Path(__file__).parents[1] / "custom_components" / "battmind" / "texts"
    keys = {path.stem: flat(json.loads(path.read_text(encoding="utf-8"))) for path in folder.glob("*.json")}
    every = set().union(*keys.values())
    assert {language: sorted(every - found) for language, found in keys.items() if every - found} == {}


@pytest.mark.parametrize(("days", "span"), [(3, "2.5d"), (2, "1.5d"), (7, "6.5d")])
def test_chart_span_follows_days_to_predict(days: int, span: str) -> None:
    content = yaml.safe_load(render_dashboard(build_config({**OPTIONS, "days_to_predict": days}), "battmind"))
    # the analysis charts show fixed periods (months / the last 31 days)
    spans = [
        value for node in walk(content) for key, value in node.items()
        if key in ("graph_span", "selection_span") and "d" in str(value) and value not in ("400d", "31d", "6d")
        and not str(value).startswith("${")  # the what-if charts follow their result
    ]
    assert len(spans) == 4 and set(spans) == {span}


def test_every_setting_is_on_the_settings_view() -> None:
    settings_view = yaml.safe_load(rendered("en"))["views"][1]
    entities = {node["entity"] for node in walk(settings_view) if isinstance(node.get("entity"), str)}
    for platform, keys in (("switch", SWITCH_SETTINGS), ("number", NUMBER_SETTINGS), ("select", SELECT_SETTINGS), ("text", TEXT_SETTINGS)):
        for key in keys:
            assert f"{platform}.battmind_{key}" in entities, key


def test_settings_read_more_covers_the_displayed_rules() -> None:
    settings_view = yaml.safe_load(rendered("da"))["views"][1]
    help_texts = [node["content"] for node in walk(settings_view) if node.get("type") == "markdown"]
    profit_help = next(text for text in help_texts if "Min. profit per kWh" in text)
    charging_help = next(text for text in help_texts if "Billigste timer" in text)

    assert "Reserve til huset" in profit_help
    assert "***Reserve til huset***\n" in profit_help
    assert "Billige prisperioder" in charging_help
    assert "***Billigste timer***\n" in charging_help
    assert "***Billige prisperioder***\n" in charging_help
    assert "Stigning for billige prisperioder" in charging_help
    assert "Stigning for billigste pris" in charging_help


def test_read_more_text_uses_spaced_bold_italic_labels() -> None:
    assert format_help_text(
        "Group overview.\n* **First entity**: First explanation.\n  * **Nested entity**: Nested explanation.\n* **Second entity**: Second explanation."
    ) == (
        "Group overview.\n\n"
        "***First entity***\nFirst explanation.\n\n"
        "***Nested entity***\nNested explanation.\n\n"
        "***Second entity***\nSecond explanation."
    )
    assert format_help_text("Overview. **First metric**: one. **Second metric**: two.") == (
        "Overview.\n\n***First metric***\none.\n\n***Second metric***\ntwo."
    )
    assert format_analysis_help_text(
        "Summary. **First metric**: first detail. Second detail. Use e.g. Carnot."
    ) == (
        "Summary.\n***First metric***\nfirst detail.\nSecond detail.\nUse e.g. Carnot."
    )
    assert format_analysis_help_text("Minimum target level: next line. Following sentence.") == (
        "Minimum target level:\nNext line.\nFollowing sentence."
    )
    assert load_texts("da")["dashboard.rules_explain"].startswith("Ladning pr. regel:")
    assert "***Ladet (☀️)***\n" in load_texts("da")["dashboard.economy_explain"]


def test_analysis_sections_include_controls_for_their_graphs() -> None:
    dashboard = yaml.safe_load(rendered("da"))
    sections = dashboard["views"][2]["sections"]
    texts = load_texts("da")

    def section_entities(title: str) -> set[str]:
        section = next(
            section
            for section in sections
            if any(card.get("heading") == title for card in walk(section))
        )
        return {node["entity"] for node in walk(section) if isinstance(node.get("entity"), str)}

    assert section_entities(texts["dashboard.section_rules"]) == set()
    assert "switch.battmind_enhance_battery_efficiency" in section_entities(texts["dashboard.section_efficiency_curve"])
    assert "select.battmind_forecast_type" in section_entities(texts["dashboard.section_forecast"])
    assert "switch.battmind_auto_forecast_type" in section_entities(texts["dashboard.section_forecast"])
    assert "switch.battmind_price_forecast_correction" in section_entities(texts["dashboard.section_price_forecast"])


def test_own_entity_rows_show_the_entity_name() -> None:
    """name: {type: entity} - the entity name without "BattMind" in front."""
    content = yaml.safe_load(rendered("en", prefix="house"))
    for node in walk(content):
        entity = node.get("entity")
        if not isinstance(entity, str) or not entity.split(".")[-1].startswith("house_"):
            continue
        if "data_generator" in node or str(node.get("type", "")).startswith("custom:") or node.get("condition"):
            continue  # apexcharts series / custom cards / visibility conditions are not entity rows
        if entity == "button.house_enforce_planning":
            assert node.get("name") == "Force planning"  # badges: the entity name as text
            continue
        assert node.get("name") == {"type": "entity"}, entity


def _price_charts(**options) -> list[str]:
    content = yaml.safe_load(render_dashboard(build_config({**OPTIONS, **options}), "battmind"))
    # (the series of a what-if chart is a template string, those charts have no price axis)
    return [
        node["series"][0]["entity"] for node in walk(content)
        if node.get("type") == "custom:apexcharts-card" and isinstance(node["series"], list)
        and node["series"][0].get("yaxis_id") == "price"
    ]


def test_one_price_chart_stromligning_preferred() -> None:
    assert _price_charts() == ["sensor.price"]
    assert _price_charts(stromligning_buy_current_price="sensor.sl_price") == ["sensor.sl_price"]


def _sell_series(**options) -> str:
    content = yaml.safe_load(render_dashboard(build_config({**OPTIONS, **options}), "battmind"))
    sell_name = load_texts("en")["dashboard.sell_price"]
    return next(
        node["data_generator"] for node in walk(content)
        if node.get("yaxis_id") == "price" and node.get("name") == sell_name and "data_generator" in node
    )


def test_stromligning_sell_price_chart_uses_the_configured_sell_sensors() -> None:
    """Same sell price as the planner: the sell sensor minus every configured tariff and the sell fee."""
    generator = _sell_series(
        language="en",
        sell_fee=0.04,
        stromligning_buy_current_price="sensor.sl_buy",
        stromligning_sell_current_price="sensor.sl_sell",
        stromligning_sell_tomorrow_available="binary_sensor.sl_sell_tomorrow",
        stromligning_sell_forecasts="sensor.sl_sell_forecasts",
        stromligning_sell_distribution="sensor.sl_distribution",
        stromligning_sell_electricity_tax="sensor.sl_tax",
        stromligning_sell_nettariff="sensor.sl_nettariff",
        stromligning_sell_systemtariff="sensor.sl_systemtariff",
        stromligning_sell_surcharge="sensor.sl_surcharge",
    )
    for entity_id in (
        "sensor.sl_sell", "binary_sensor.sl_sell_tomorrow", "sensor.sl_sell_forecasts", "sensor.sl_distribution",
        "sensor.sl_tax", "sensor.sl_nettariff", "sensor.sl_systemtariff", "sensor.sl_surcharge",
    ):
        assert f"'{entity_id}'" in generator
    assert "Number('0.04')" in generator
    assert "nettariff_vat" not in generator and "0.025" not in generator


def test_energi_data_service_sell_price_chart_subtracts_the_sell_fee() -> None:
    generator = _sell_series(language="en", sell_fee=0.03)
    assert "Number('0.03')" in generator
    assert "sellCosts(row.hour)" in generator


def test_every_chart_has_a_shared_non_intersecting_tooltip() -> None:
    content = yaml.safe_load(rendered("en"))
    charts = [node for node in walk(content) if node.get("type") == "custom:apexcharts-card"]
    assert charts
    for chart in charts:
        if isinstance(chart["apex_config"], str):
            # the options come from the what-if result (what_if_charts.py, tested in test_what_if_charts.py)
            assert chart["apex_config"].startswith("${vars.")
            continue
        tooltip = chart["apex_config"]["tooltip"]
        assert tooltip["shared"] is True
        assert tooltip["intersect"] is False


def test_overview_plan_chart_series_share_the_battery_flow_hours() -> None:
    overview = yaml.safe_load(rendered("en"))["views"][0]
    chart = next(node for node in walk(overview) if node.get("type") == "custom:apexcharts-card" and len(node.get("series", [])) == 5)
    assert "enabledOnSeries" not in chart["apex_config"]["tooltip"]
    custom_tooltip = chart["apex_config"]["tooltip"]["custom"]
    assert "series.map((values, index)" in custom_tooltip
    assert "collapsedSeriesIndices" in custom_tooltip
    assert "values[dataPointIndex]" in custom_tooltip
    for series in chart["series"]:
        generator = series["data_generator"]
        assert "const flow = entity.attributes.battery_level_flow || [];" in generator
        assert "return flow.map(p =>" in generator
    assert all(series.get("curve") == "stepline" for series in chart["series"][1:])
    assert [series["color"] for series in chart["series"]] == ["#4CAF50", "#2196F3", "#FF9800", "#FDD835", "#F44336"]
    assert chart["series"][0]["curve"] == "straight"
    assert chart["series"][0]["opacity"] == 0.25
    assert chart["series"][1]["opacity"] == 0.6
    assert chart["series"][0]["show"]["in_brush"] is True
    assert all(series["show"]["in_brush"] is False for series in chart["series"][1:])
    assert [series.get("show", {}).get("hidden_by_default", False) for series in chart["series"]] == [False, False, False, True, False]


def test_price_overview_shows_the_battery_plan() -> None:
    for options in ({}, {"stromligning_buy_current_price": "sensor.sl_price"}):
        content = yaml.safe_load(render_dashboard(build_config({**OPTIONS, **options, "language": "en"}), "battmind"))
        chart = next(
            node for node in walk(content)
            if node.get("type") == "custom:apexcharts-card" and node.get("series", [{}])[0].get("yaxis_id") == "price"
        )
        actions = [series for series in chart["series"] if series.get("yaxis_id") == "state"]
        assert chart["apex_config"]["stroke"]["curve"] == "stepline"
        assert [series["name"] for series in actions] == ["Charge", "Force discharge"]
        assert all(series["type"] == "area" and series["curve"] == "stepline" for series in actions)
        assert all("_timestamps" in series["data_generator"] and "group_by" not in series for series in actions)
        assert all(not series.get("show", {}).get("hidden_by_default", False) for series in actions)
        assert all(series["show"]["in_brush"] is False for series in actions)
        assert chart["apex_config"]["tooltip"]["enabledOnSeries"] == [0, 1, 2, 4, 5]
        assert chart["apex_config"]["tooltip"]["followCursor"] is True
        assert chart["apex_config"]["tooltip"]["fixed"]["enabled"] is False
        custom = chart["apex_config"]["tooltip"]["custom"]
        assert "const indices = [0, 1, 2, 4, 5];" in custom
        assert custom.strip().endswith("}")
        for index in (0, 1, 2, 4, 5):
            generator = chart["series"][index]["data_generator"]
            assert "const times = [...new Set(" in generator
            assert "return times.map(time =>" in generator


def test_overview_does_not_show_raw_powerwall_action_card() -> None:
    overview = yaml.safe_load(rendered("en"))["views"][0]
    cards = [node for node in walk(overview) if node.get("type") == "custom:mushroom-entity-card"]
    assert all(card.get("entity") != "sensor.battmind_powerwall_action" for card in cards)


def test_price_forecast_chart_series_share_the_same_hours() -> None:
    content = yaml.safe_load(rendered("en"))
    chart = next(node for node in walk(content) if node.get("type") == "custom:apexcharts-card" and node.get("graph_span") == "6d")
    assert chart["span"] == {"start": "day", "offset": "-2d"}
    assert len(chart["series"]) == 9
    for series in chart["series"]:
        generator = series["data_generator"]
        assert "const hours = ((entity.attributes.price_forecast || {}).hours || []);" in generator
        assert "return hours.map(h =>" in generator
        assert ".filter(" not in generator


def test_price_forecast_analysis_matches_the_cjp_dashboard() -> None:
    content = yaml.safe_load(rendered("en"))
    cards = [
        node for node in walk(content)
        if node.get("type") == "markdown" and "| Days ahead |" in node.get("content", "")
    ]
    assert len(cards) == 1
    table = cards[0]["content"]
    assert "| Days ahead | days | Error 7 days | Error 30 days | Bias | Worst hour | Cheapest found | Correction |" in table
    assert "Most expensive found" not in table
    assert "first figures tomorrow at 13:00" in table


def test_efficiency_curve_chart_keeps_points_inside_its_time_window() -> None:
    content = yaml.safe_load(rendered("en"))
    chart = next(
        node for node in walk(content)
        if node.get("type") == "custom:apexcharts-card"
        and node.get("header", {}).get("title") == "Efficiency by AC power"
    )
    assert chart["graph_span"] == "24h"
    assert chart["span"] == {"end": "minute"}
    # the curves BattMind uses, and dashed every measured power step
    assert [series["name"] for series in chart["series"]] == ["Charging", "Discharging", "Charging, measured", "Discharging, measured"]
    assert [series.get("stroke_dash") for series in chart["series"]] == [None, None, 5, 5]
    assert "function(value, timestamp)" in chart["apex_config"]["xaxis"]["labels"]["formatter"]
    for series in chart["series"]:
        generator = series["data_generator"]
        assert "const range = end.getTime() - start.getTime();" in generator
        assert "start.getTime() + (p.power / 10000) * range" in generator
        assert "entity.attributes.efficiency_curves" in generator
    assert ".measured || {}).charging" in chart["series"][2]["data_generator"]


def test_efficiency_status_table_follows_the_published_sources() -> None:
    content = yaml.safe_load(rendered("en"))
    table = next(
        node["content"] for node in walk(content)
        if node.get("type") == "markdown" and "efficiency_curves" in str(node.get("content", ""))
    )
    assert "| | Curve used | | |" in table
    assert "configured{% elif source == 'measured' %}measured{% else %}none yet: round-trip loss" in table
    assert "power steps in use" in table and "kWh measured" in table
    assert "@@" not in table

def test_solar_forecast_chart_series_share_the_same_days() -> None:
    content = yaml.safe_load(rendered("en"))
    chart = next(
        node for node in walk(content)
        if node.get("type") == "custom:apexcharts-card"
        and node.get("series", [{}])[0].get("type") == "column"
        and "entity.attributes.plans" in node.get("series", [{}])[0].get("data_generator", "")
    )
    assert len(chart["series"]) == 2
    custom_tooltip = chart["apex_config"]["tooltip"]["custom"]
    assert "w.globals.seriesNames[index]" in custom_tooltip
    assert "values[dataPointIndex]" in custom_tooltip
    for series in chart["series"]:
        generator = series["data_generator"]
        assert "const days = entity.attributes.plans || [];" in generator
        assert "return days.map(d =>" in generator
        assert ".filter(" not in generator


def test_consumption_forecast_is_aligned_and_visually_distinguishes_totals() -> None:
    content = yaml.safe_load(rendered("en"))
    chart = next(
        node for node in walk(content)
        if node.get("type") == "custom:apexcharts-card" and node.get("header", {}).get("title") == "Consumption forecast"
    )
    assert len(chart["series"]) == 6
    assert chart["apex_config"]["stroke"]["dashArray"] == [0, 0, 0, 6, 6, 6]
    assert chart["apex_config"]["legend"]["position"] == "bottom"
    assert [series["name"] for series in chart["series"][3:]] == ["Total Avg", "Total EMA", "Total Trend"]
    for series in chart["series"]:
        generator = series["data_generator"]
        assert "const keys = [...new Set([" in generator
        assert "return keys.map(ts =>" in generator


async def test_sidebar_dashboard_can_be_edited_and_reset(
    hass: HomeAssistant, hass_ws_client, mock_entry: MockConfigEntry, tmp_path
) -> None:
    hass.config.config_dir = str(tmp_path)
    set_source_states(hass)
    entry = MockConfigEntry(domain=mock_entry.domain, title=mock_entry.title, data=dict(mock_entry.data), options={**MOCK_OPTIONS, "dashboard": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    client = await hass_ws_client(hass)

    async def command(message: dict) -> dict:
        await client.send_json_auto_id({"url_path": f"{PREFIX}-dashboard", **message})
        response = await client.receive_json()
        assert response["success"], response
        return response["result"]

    generated = await command({"type": "lovelace/config"})
    assert generated["views"]
    # storage mode: the frontend offers the edit UI and the raw configuration editor
    assert hass.data["frontend_panels"][f"{PREFIX}-dashboard"].config == {"mode": "storage"}

    await command({"type": "lovelace/config/save", "config": {"views": [{"title": "Mine"}]}})
    assert await command({"type": "lovelace/config"}) == {"views": [{"title": "Mine"}]}

    # the generated what-if view has no custom card, only entities and standard cards
    assert "custom:battmind-what-if" not in yaml.safe_dump(generated)
    custom = {"views": [{"title": "Mine", "cards": [{"type": "markdown", "content": "My own card"}]}]}
    await command({"type": "lovelace/config/save", "config": custom})
    restored = await command({"type": "lovelace/config"})
    assert restored == custom

    # a saved dashboard with the removed custom what-if card gets the generated What-if view instead of that view
    legacy = {"views": [
        {"title": "Mine", "cards": [{"type": "markdown", "content": "My own card"}]},
        {"title": "Old what-if", "cards": [{"type": "custom:battmind-what-if", "config_entry_id": entry.entry_id, "labels": {}}]},
    ]}
    await command({"type": "lovelace/config/save", "config": legacy})
    migrated = await command({"type": "lovelace/config"})
    assert migrated["views"][0] == legacy["views"][0]
    assert migrated["views"][1]["path"] == "what-if" and "custom:battmind-what-if" not in yaml.safe_dump(migrated)
    await command({"type": "lovelace/config/save", "config": custom})

    # the edits survive a reload, the generated dashboard is not shown over them
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await command({"type": "lovelace/config"}) == restored

    await hass.services.async_call("button", "press", {"entity_id": f"button.{PREFIX}_reset_dashboard"}, blocking=True)
    assert await command({"type": "lovelace/config"}) == generated
    await async_unload_and_join(hass, entry)


def _status_entities(**options) -> list[str]:
    content = yaml.safe_load(render_dashboard(build_config({**OPTIONS, **options}), "battmind"))
    cards = next(node["cards"] for node in walk(content) if any(card.get("heading") == "Status" for card in node.get("cards", [])))
    index = next(i for i, card in enumerate(cards) if card.get("heading") == "Status")
    return [row["entity"] for row in cards[index + 1]["entities"]]


def test_status_section_shows_the_configured_entities() -> None:
    config = build_config(OPTIONS)
    home = config["home"]["entity_ids"]
    expected = [
        home["powerwall_battery_level_entity_id"],
        home["powerwall_watt_flow_entity_id"],
        home["power_consumption_entity_id"],
    ]
    if config["solar"]["entity_ids"]["production_entity_id"]:
        expected += [config["solar"]["entity_ids"]["production_entity_id"], "sensor.battmind_kwh_charged_by_solar"]
    expected += [config["prices"]["entity_ids"]["energidataservice_buy_entity_id"], "sensor.battmind_analysis"]
    assert _status_entities() == expected


def test_status_section_drops_missing_entities() -> None:
    entities = _status_entities(
        powerwall_watt_flow_entity_id="", production_entity_id="", stromligning_buy_current_price="sensor.sl_price"
    )
    assert "sensor.powerwall_battery_now" not in entities
    assert "sensor.solar_power_production" not in entities
    assert "sensor.battmind_kwh_charged_by_solar" not in entities
    assert entities[-2:] == ["sensor.sl_price", "sensor.battmind_analysis"]


def _settings_rows(unsupported: list[str]) -> tuple[list[str], list[str]]:
    """Headings and entity rows of the settings view."""
    content = yaml.safe_load(render_dashboard(build_config({**OPTIONS, "language": "en", "unsupported_actions": unsupported}), "battmind"))
    view = content["views"][1]
    headings = [node["heading"] for node in walk(view) if node.get("type") == "heading"]
    rows = [row for node in walk(view) if node.get("type") == "entities" for row in node["entities"]]
    return headings, [row["entity"] for row in rows if "entity" in row]


def _entities_lists(unsupported: list[str]) -> list[list[dict]]:
    content = yaml.safe_load(render_dashboard(build_config({**OPTIONS, "unsupported_actions": unsupported}), "battmind"))
    return [node["entities"] for node in walk(content["views"][1]) if node.get("type") == "entities"]


@pytest.mark.parametrize("action", list(CONTROLLABLE_ACTIONS))
def test_unsupported_action_hides_its_rules_on_the_settings_view(action: str) -> None:
    all_headings, all_entities = _settings_rows([])
    headings, entities = _settings_rows([action])

    hidden = {f"switch.battmind_{key}" for key in ACTION_SWITCHES[action]}
    assert hidden <= set(all_entities)
    assert not hidden & set(entities)
    # everything else stays
    for entity in all_entities:
        if entity not in hidden and not entity.startswith("number.battmind_cheap"):
            assert entity in entities, entity
    for rows in _entities_lists([action]):
        types = [row.get("type") for row in rows]
        assert rows and types[0] != "divider" and types[-1] != "divider", rows
        assert all(not (a == b == "divider") for a, b in zip(types, types[1:])), rows

    if action == "grid_charging":
        # the whole section goes: its switches and the cheap price settings only those rules use
        assert not any(entity.startswith("number.battmind_cheap") for entity in entities)
        assert "Charging from the grid" in all_headings
        assert "Charging from the grid" not in headings
    else:
        assert headings == all_headings


async def test_sidebar_icon_follows_the_dashboard_icon_option(hass: HomeAssistant, mock_entry: MockConfigEntry, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    set_source_states(hass)
    entry = MockConfigEntry(domain=mock_entry.domain, title=mock_entry.title, data=dict(mock_entry.data), options={**MOCK_OPTIONS, "dashboard": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    # no choice yet: the default icon
    assert hass.data["frontend_panels"][f"{PREFIX}-dashboard"].sidebar_icon == "mdi:home-battery"

    hass.config_entries.async_update_entry(entry, options={**entry.options, "dashboard_icon": "mdi:solar-power"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.data["frontend_panels"][f"{PREFIX}-dashboard"].sidebar_icon == "mdi:solar-power"
    assert hass.data[LOVELACE_DATA].dashboards[f"{PREFIX}-dashboard"].config["icon"] == "mdi:solar-power"

    # a stored value that is not an icon name falls back to the default instead of breaking the sidebar
    hass.config_entries.async_update_entry(entry, options={**entry.options, "dashboard_icon": "not an icon"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.data["frontend_panels"][f"{PREFIX}-dashboard"].sidebar_icon == "mdi:home-battery"
    await async_unload_and_join(hass, entry)
