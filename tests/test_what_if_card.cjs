const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
global.crypto = require("node:crypto").webcrypto;

class Element {
  constructor(tag = "") {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.style = {};
    this._textContent = "";
    this.elements = {};
  }
  set textContent(value) { this._textContent = value; this.children = []; }
  get textContent() { return this._textContent; }
  attachShadow() { this.shadowRoot = new Element("shadow"); }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this[`on_${name}`] = callback; }
  getBoundingClientRect() { return { left: 100, width: 600, height: 200 }; }
}

global.HTMLElement = Element;
global.document = {
  createElement: tag => new Element(tag),
  createElementNS: (_, tag) => new Element(tag),
};
const timers = new Map();
let nextTimer = 1;
global.setInterval = callback => { const id = nextTimer++; timers.set(id, callback); return id; };
global.clearInterval = id => timers.delete(id);
let Card;
global.customElements = {
  get: () => undefined,
  define: (_, element) => { Card = element; },
};
const cardSource = fs.readFileSync("custom_components/battmind/dashboard/what-if-card.js", "utf8");
vm.runInThisContext(cardSource);

const labels = Object.fromEntries([
  "title", "help", "history", "battery", "range", "range_start", "range_end", "range_step",
  "module_compare", "module_capacity", "module_price", "module_count", "module_section",
  "module_baseline_note", "module_history", "module_months", "module_insufficient_history", "module_added", "module_profit",
  "module_coverage_requirement", "module_missing_sell_price", "module_missing_sell_cost", "module_rerun",
  "module_dst_warning", "module_tariff_warning", "module_current_cost_warning",
  "module_gain_baseline", "module_incremental_gain", "module_annualized_gain", "module_cumulative_cost",
  "module_payback", "module_no_payback", "module_years", "module_note",
  "module_estimate_note", "module_estimated_hours", "module_estimated_months",
  "module_reliability_details", "module_estimate_unavailable", "module_graph_title",
  "module_graph_note", "module_graph_modules", "module_graph_no_payback", "module_estimate_details",
  "module_monthly_title", "module_monthly_note", "module_monthly_month", "module_monthly_range_title",
  "module_monthly_modules_title", "module_bill_title", "module_bill_note", "module_bill_none",
  "run", "running", "failed", "results", "completed", "used_settings", "load_failed", "current", "scenario", "profit", "bill", "difference",
  "period", "hours", "missing", "tariffs", "sell_history", "chart", "interval_profit",
  "interval_per_kwh", "interval_chart", "interval_explain", "bill_explain", "form_explain",
  "scenario_section", "range_section", "highest_profit", "highest_rate", "interval_example", "range_columns", "interval_curve_note", "chart_growth_legend", "interval_color_legend",
  "interpolated", "interpolation_note",
  "days", "empty",
].map(key => [key, key]));
const charts = card => card._results.children.flatMap(node => node.children.filter(child => child.tag === "svg"));
const tables = card => card._results.children.flatMap(node => node.children.filter(child => child.tag === "table"));
const form = { elements: {
  history_days: { value: "30" }, battery_size: { value: "0" },
  range_start: { value: "0" }, range_end: { value: "30" }, range_step: { value: "5" },
  module_capacity: { value: "2.5" }, module_price: { value: "0" }, module_count: { value: "2" },
} };
const simulation = {
  period: { start: "2026-01-01", end: "2026-01-31", hours: 720 },
  coverage: { missing_hours: 24 },
  tariff_estimated_hours: 1,
  tariff_unavailable_hours: 2,
  current: { profit: 10, with: { bill: 40 } },
  scenario: { profit: 0, with: { bill: 50 } },
  difference: { profit: -10, bill: 10 },
  current_assumptions: { battery_size: 10 },
  assumptions: { battery_size: 0, sell_price_history_available: false },
  range: [{ battery_size: 0, profit: 0, bill: 50 }, { battery_size: 5, profit: 4, bill: 46 }],
  module_comparison: {
    annualization: {
      available: false, matched_hours: 720, minimum_matched_hours: 7884, represented_months: [1], months: {},
    },
    rows: [
      { added_modules: 0, battery_size: 10, profit: 10, gain_vs_current_capacity: 0,
        incremental_gain: null, annualized_incremental_gain: null, module_price: 0,
        cumulative_module_cost: 0, payback_years: null },
      { added_modules: 1, battery_size: 12.5, profit: 12, gain_vs_current_capacity: 2,
        incremental_gain: 2, annualized_incremental_gain: null, module_price: 0,
        cumulative_module_cost: 0, payback_years: null },
    ],
  },
};
const serviceOnly = handler => async message => {
  if (message.type === "battmind/what_if_last") return null;
  if (message.type === "battmind/what_if_status") return { job_id: null, status: "idle" };
  const result = await handler(message);
  if (result?.response?.simulations) {
    const values = Object.values(result.response.simulations);
    return { job_id: message.job_id, status: "completed", result: values.length === 1 ? values[0] : null };
  }
  return result;
};

async function main() {
  for (const language of ["da", "de", "en", "pl", "uk"]) {
    const translated = JSON.parse(fs.readFileSync(`custom_components/battmind/texts/${language}.json`, "utf8")).dashboard;
    const entities = JSON.parse(fs.readFileSync(`custom_components/battmind/translations/${language}.json`, "utf8")).entity;
    for (const [key, name] of Object.entries(whatIfRuleLabels[language].names)) {
      assert.equal(name, (entities.switch[key] || entities.number[key] || entities.text[key]).name);
    }
    for (const labelsForLanguage of Object.values(whatIfRuleLabels)) {
      for (const key of ["charging", "discharging", "selling", "shared"]) assert(labelsForLanguage.groups[key]);
    }
    for (const key of [...whatIfRuleSwitches, "usage_reserve", "min_profit_per_kwh", "min_total_profit",
      "min_sell_kwh", "cheap_price_periods", "cheapest_price_rise_threshold",
      "cheap_price_period_rise_threshold", "powerwall_max_hours_old", "exclude_sell_hours"]) {
      assert(whatIfRuleLabels[language].names[key], `${language} lacks a name for ${key}`);
    }
    for (const key of ["edit_title", "edit_note", "edit_loading", "edit_unavailable", "edit_unsupported", "edit_reset"]) {
      assert(whatIfRuleLabels[language][key], `${language} lacks ${key}`);
      assert(!/[?\uFFFD]/.test(whatIfRuleLabels[language][key]));
    }
    const oldLabels = Object.fromEntries(Object.entries(labels).filter(([key]) => !key.startsWith("module_")));
    const legacy = new Card();
    legacy.setConfig({ config_entry_id: `legacy-${language}`, labels: oldLabels });
    legacy.hass = { language, config: { currency: "DKK" }, callWS: serviceOnly(async () => null) };
    const cardElement = legacy.shadowRoot.children.at(-1);
    const formElement = cardElement.children.find(node => node.tag === "form");
    const comparisonToggle = formElement.children.find(node => node.className === "check" &&
      node.children.some(child => child.textContent === translated.what_if_module_compare));
    assert(comparisonToggle);
    assert.equal(legacy._labels().module_price, translated.what_if_module_price);
    assert.equal(legacy._labels().module_note, translated.what_if_module_note);
    for (const [key, value] of Object.entries(legacy._labels()).filter(([key]) => key.startsWith("module_"))) {
      assert.equal(value, translated[`what_if_${key}`]);
    }
    legacy._show(simulation);
    const moduleTable = tables(legacy).at(-1);
    assert(moduleTable.children[0].children.some(node => node.textContent === translated.what_if_module_payback));
    assert(!JSON.stringify(moduleTable).includes("undefined"));
  }
  const rulesCard = new Card();
  rulesCard.setConfig({ config_entry_id: "rules", labels });
  rulesCard.hass = { language: "en", config: { currency: "DKK" }, callWS: serviceOnly(async () => null) };
  const ruleSimulation = structuredClone(simulation);
  ruleSimulation.current_assumptions = {
    battery_size: 10, control_mode: "rules",
    rules: { cheapest_hour_fill_planner: true, cheapest_hour_fill_up: true,
      force_powerwall_max_hours_old_discharge: true, solar_charging: false,
      optimize_solar_charging: true, discharge_before_solar: true },
    cheap_price_periods: 2, cheapest_price_rise_threshold: 0.5,
    cheap_price_period_rise_threshold: 0.1, powerwall_max_hours_old: 48,
    min_sell_kwh: 0.234, min_profit_per_kwh: 0.004, exclude_sell_hours: "2,3",
  };
  ruleSimulation.assumptions = {
    battery_size: 15, control_mode: "optimizer",
    rules: { optimizer_control: true, cheapest_hour_fill_planner: false, cheapest_hour_fill_up: true,
      force_powerwall_max_hours_old_discharge: false, solar_charging: false,
      optimize_solar_charging: true, most_profit: true },
    cheap_price_periods: 5, powerwall_max_hours_old: 24,
    min_sell_kwh: 0.3, exclude_sell_hours: "",
  };
  rulesCard._show(ruleSimulation);
  let ruleRows = tables(rulesCard).at(-1).children.map(row => row.children.map(cell => cell.textContent));
  const names = whatIfRuleLabels.en.names;
  assert(!ruleRows.some(row => row[0] === names.solar_charging));
  assert(!ruleRows.some(row => row[0] === names.optimize_solar_charging));
  assert(!ruleRows.some(row => row[0] === names.discharge_before_solar));
  assert.deepEqual(ruleRows.find(row => row[0] === names.cheapest_hour_fill_up), [names.cheapest_hour_fill_up, "On", "—"]);
  assert.deepEqual(ruleRows.find(row => row[0] === names.cheap_price_periods), [names.cheap_price_periods, "2", "—"]);
  assert.deepEqual(ruleRows.find(row => row[0] === names.powerwall_max_hours_old), [names.powerwall_max_hours_old, "48 h", "—"]);
  assert.deepEqual(ruleRows.find(row => row[0] === names.min_sell_kwh), [names.min_sell_kwh, "0.234 kWh", "0.3 kWh"]);
  assert.deepEqual(ruleRows.find(row => row[0] === names.min_profit_per_kwh), [names.min_profit_per_kwh, "0.004 DKK/kWh", "—"]);
  assert.deepEqual(ruleRows.find(row => row[0] === names.exclude_sell_hours), [names.exclude_sell_hours, "2,3", "—"]);
  ruleSimulation.current_assumptions.exclude_sell_hours = "unknown";
  rulesCard._show(ruleSimulation);
  assert(!tables(rulesCard).at(-1).children.some(row => row.children[0].textContent === names.exclude_sell_hours));
  assert.deepEqual(ruleRows.find(row => row[0] === names.cheapest_price_rise_threshold),
    [names.cheapest_price_rise_threshold, "0.50 DKK/kWh", "—"]);
  const groupHeaders = tables(rulesCard).at(-1).children
    .filter(row => row.children.length === 1).map(row => row.children[0].textContent);
  assert.deepEqual(groupHeaders, ["Charging", "Discharging", "Selling", "Shared"]);
  const noSelling = structuredClone(ruleSimulation);
  noSelling.current_assumptions.rules.force_powerwall_max_hours_old_discharge = false;
  noSelling.assumptions.control_mode = "rules";
  noSelling.assumptions.rules.optimizer_control = false;
  noSelling.assumptions.rules.most_profit = false;
  rulesCard._show(noSelling);
  const noSellingRows = tables(rulesCard).at(-1).children.map(row => row.children[0].textContent);
  assert(!noSellingRows.includes(names.min_sell_kwh));
  assert(!noSellingRows.includes(names.exclude_sell_hours));
  assert(noSellingRows.includes(names.min_profit_per_kwh));
  noSelling.assumptions.control_mode = "optimizer";
  noSelling.assumptions.rules.optimizer_control = true;
  rulesCard._show(noSelling);
  assert(!tables(rulesCard).at(-1).children.some(row => row.children[0].textContent === names.min_sell_kwh));
  ruleSimulation.current_assumptions.battery_size = 0;
  rulesCard._show(ruleSimulation);
  ruleRows = tables(rulesCard).at(-1).children.map(row => row.children.map(cell => cell.textContent));
  assert(!ruleRows.some(row => row[0] === names.cheapest_hour_fill_up));
  assert(!ruleRows.some(row => row[0] === names.powerwall_max_hours_old));
  assert.deepEqual(ruleRows.find(row => row[0] === names.min_sell_kwh), [names.min_sell_kwh, "—", "0.3 kWh"]);
  const legacyRules = structuredClone(ruleSimulation);
  delete legacyRules.assumptions.rules;
  delete legacyRules.current_assumptions.rules;
  rulesCard._show(legacyRules);
  assert(rulesCard._results.children.some(node => node.textContent === whatIfRuleLabels.en.unavailable));
  const ruleEditCard = new Card();
  ruleEditCard.setConfig({ config_entry_id: "rule-edit", labels });
  let ruleRequest;
  const ruleSettings = {
    switches: Object.fromEntries(whatIfRuleSwitches.map(key => [key, key !== "most_profit"])),
    unsupported: ["negative_price_planner"],
    numbers: {
      usage_reserve: { value: 10, min: 0, max: 100, step: 1 },
      min_sell_kwh: { value: 0.2, min: 0.2, max: 1, step: 0.1 },
    },
    exclude_sell_hours: "1,2",
  };
  ruleEditCard.hass = {
    language: "en", config: { currency: "DKK" },
    callWS: serviceOnly(async message => {
      if (message.type === "battmind/what_if_settings") return ruleSettings;
      ruleRequest = message;
      return { response: { simulations: { battmind: simulation } } };
    }),
  };
  await new Promise(resolve => setTimeout(resolve, 0));
  const ruleInputs = ruleEditCard._ruleInputs;
  assert.equal(ruleInputs.get("negative_price_planner").disabled, true);
  assert.equal(ruleInputs.get("most_profit").checked, false);
  assert.equal(ruleInputs.get("solar_charging").checked, true);
  assert.equal(ruleInputs.get("usage_reserve").value, "10");
  assert.equal(ruleInputs.get("exclude_sell_hours").value, "1,2");
  await ruleEditCard._run(form, false);
  assert.deepEqual(ruleRequest.data, { history_days: 30, battery_size: 0 });
  ruleInputs.get("most_profit").checked = true;
  ruleInputs.get("usage_reserve").value = "25";
  ruleInputs.get("exclude_sell_hours").value = "1, 3";
  await ruleEditCard._run(form, false);
  assert.deepEqual(ruleRequest.data, {
    history_days: 30, battery_size: 0, most_profit: true, usage_reserve: 25, exclude_sell_hours: "1,3",
  });
  const failedRulesCard = new Card();
  failedRulesCard.setConfig({ config_entry_id: "rule-failed", labels });
  failedRulesCard.hass = {
    language: "en", config: { currency: "DKK" }, callWS: serviceOnly(async () => null),
  };
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(failedRulesCard._ruleStatus.textContent, whatIfRuleLabels.en.edit_unavailable);
  const card = new Card();
  card.setConfig({ config_entry_id: "entry-1", labels });
  card.connectedCallback();
  let request;
  card.hass = {
    language: "en", config: { currency: "DKK" },
    callWS: serviceOnly(async message => {
      request = message;
      return { response: { simulations: { battmind: simulation } } };
    }),
  };
  await card._run(form, true, true);
  assert.equal(request.type, "battmind/what_if_start");
  assert.equal(request.config_entry_id, "entry-1");
  assert.match(request.job_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.deepEqual(request.data, {
    history_days: 30, battery_size: 0,
    range_start: 0, range_end: 30, range_step: 5,
    module_capacity: 2.5, module_price: 0, module_count: 2,
  });
  assert.equal(card._session.running, false);
  assert.equal(card._button.disabled, false);
  assert.equal(card._status.textContent, "");
  const summary = card._results.children.find(node => node.className === "summary");
  assert(summary);
  assert.deepEqual(summary.children.map(metric => metric.children.map(node => node.textContent)), [
    ["current", "10.00 DKK", "10 kWh"],
    ["scenario", "0.00 DKK", "0 kWh"],
    ["difference", "-10.00 DKK", "scenario – current"],
  ]);
  const svg = charts(card)[0];
  assert(svg);
  assert.equal(svg.children.filter(node => node.tag === "circle").length, 2);
  assert(svg.children.some(node => node.tag === "text" && node.textContent === "4.00"));
  const intervalGraph = charts(card)[1];
  assert(intervalGraph.children.some(node => node.tag === "text" && node.textContent === "0.80"));
  assert(intervalGraph.children.some(node => node.tag === "text" && node.textContent === "0–5 kWh"));
  const moduleRows = tables(card)[2].children;
  assert.equal(moduleRows.length, 3);
  assert.equal(moduleRows[0].children[0].textContent, "module_added / module_capacity");
  assert.equal(moduleRows[1].children[0].textContent, "0 · 10 kWh");
  assert.equal(moduleRows[2].children[0].textContent, "1 · 12.5 kWh");
  assert.equal(moduleRows[2].children[3].textContent, "0.00 DKK");
  assert.equal(moduleRows[2].children[4].textContent, "—");
  const reliabilityCard = new Card();
  reliabilityCard.setConfig({ config_entry_id: "reliability", labels });
  reliabilityCard.hass = { language: "en", config: { currency: "DKK" }, callWS: serviceOnly(async () => null) };
  const unreliable = structuredClone(simulation);
  unreliable.module_comparison.annualization.reason = "missing_sell_price";
  unreliable.module_comparison.warnings = {
    dst_replay_resets: 2, tariff_estimated_hours: 50, raw_current_sell_cost_hours: 3,
    raw_unknown_sell_cost_hours: 4,
  };
  reliabilityCard._show(unreliable);
  assert(reliabilityCard._results.children.some(node => node.textContent === "module_missing_sell_price"));
  const reliability = reliabilityCard._results.children.find(node => node.tag === "details");
  const warningText = reliability.children.map(node => node.textContent);
  for (const expected of ["module_dst_warning (2)", "module_tariff_warning (50)",
    "module_current_cost_warning (3)", "module_missing_sell_cost (4)"]) assert(warningText.includes(expected));
  assert(!reliabilityCard._results.children.some(node =>
    node.className === "warning" && node.textContent === "module_current_cost_warning (3)"));
  unreliable.module_comparison.annualization.reason = "missing_sell_cost";
  reliabilityCard._show(unreliable);
  assert.equal(tables(reliabilityCard)[2].children[2].children[4].textContent, "—");
  assert(reliabilityCard._results.children.some(node => node.textContent === "module_missing_sell_cost"));
  const oldResult = structuredClone(simulation);
  oldResult.module_comparison.annualization.available = true;
  delete oldResult.module_comparison.annualization.months;
  oldResult.module_comparison.rows[1].annualized_incremental_gain = 500;
  oldResult.module_comparison.rows[1].payback_years = 2;
  reliabilityCard._show(oldResult);
  assert.equal(tables(reliabilityCard)[2].children[2].children[2].textContent, "—");
  assert.equal(tables(reliabilityCard)[2].children[2].children[4].textContent, "—");
  assert(reliabilityCard._results.children.some(node => node.textContent === "module_rerun"));
  const estimated = structuredClone(simulation);
  estimated.module_comparison.warnings = { raw_current_sell_cost_hours: 3 };
  estimated.module_comparison.annual = {
    method: "estimated", provenance: { estimated_hours: 2000, estimated_from_other_months: ["2026-01"] },
    rows: [
      { module_count: 0, cumulative_cost: 0, marginal_vs_previous: null,
        annual_bill: { without: 1200, with: 1000 }, cumulative_vs_baseline: { annual_gain: 0 } },
      { module_count: 1, cumulative_cost: 1000, marginal_vs_previous: { annual_gain: 100, payback_years: 10 },
        annual_bill: { without: 1200, with: 900 }, cumulative_vs_baseline: { annual_gain: 100 } },
      { module_count: 2, cumulative_cost: 2000, marginal_vs_previous: { annual_gain: -50, payback_years: null },
        annual_bill: { without: 1200, with: 950 }, cumulative_vs_baseline: { annual_gain: 50 } },
    ],
  };
  estimated.module_comparison.rows.push({ added_modules: 2, battery_size: 15, profit: 11,
    gain_vs_current_capacity: 1, incremental_gain: -1, annualized_incremental_gain: null,
    module_price: 1000, cumulative_module_cost: 2000, payback_years: null });
  reliabilityCard._show(estimated);
  assert(reliabilityCard._results.children.some(node =>
    node.className === "warning" && node.textContent === "module_current_cost_warning (3)"));
  const strict = structuredClone(estimated);
  strict.module_comparison.annual.method = "strict";
  reliabilityCard._show(strict);
  assert(reliabilityCard._results.children.some(node =>
    node.className === "warning" && node.textContent === "module_current_cost_warning (3)"));
  reliabilityCard._show(estimated);
  const estimatedRows = tables(reliabilityCard)[2].children;
  assert.equal(estimatedRows[2].children[2].textContent, "100.00 DKK");
  assert.equal(estimatedRows[0].children[2].textContent, "Estimated annual incremental gain");
  assert.equal(estimatedRows[2].children[4].textContent, "10 module_years");
  assert.equal(estimatedRows[3].children[4].textContent, "—");
  assert.equal(estimatedRows[3].children[4].attributes["aria-label"], "module_no_payback");
  const moduleChart = reliabilityCard._results.children.find(node => node.className === "module-chart");
  const chartSvg = moduleChart.children.find(node => node.tag === "svg");
  assert.equal(chartSvg.attributes.viewBox, "0 0 600 200");
  assert.equal(chartSvg.style.minWidth, undefined);
  assert.equal(chartSvg.children.filter(node => node.tag === "line").length, 3);
  const legend = moduleChart.children.find(node => node.className === "module-legend");
  assert.equal(legend.children[0].children[1].textContent, "1 module_graph_modules: 10 yrs");
  assert.equal(legend.children[1].children[1].textContent, "2 module_graph_modules: 40 yrs");
  const billChart = reliabilityCard._results.children.filter(node => node.className === "module-chart")[1];
  const billSvg = billChart.children.find(node => node.tag === "svg");
  assert.equal(billSvg.children.filter(node => node.tag === "line").length, 5);
  assert.deepEqual(billChart.children.find(node => node.className === "module-legend").children
    .map(node => node.children[1].textContent),
  ["module_bill_none", "0 module_graph_modules", "1 module_graph_modules", "2 module_graph_modules"]);
  for (const hoverSvg of [chartSvg, billSvg]) {
    const hoverGroup = hoverSvg.children.at(-1);
    assert.equal(hoverGroup.style.display, "none");
    hoverSvg.on_pointermove({ clientX: 400 });
    assert.equal(hoverGroup.style.display, "");
    assert(hoverGroup.children.some(node => node.tag === "text" && /module_graph_modules: .* DKK/.test(node.textContent)));
    hoverSvg.on_pointerleave({ pointerType: "mouse" });
    assert.equal(hoverGroup.style.display, "none");
  }
  const details = card._results.children.filter(node => node.className === "chart-detail");
  assert.equal(details.length, 2);
  assert.equal(details[0].textContent, "0 kWh — chart: 0.00 DKK");
  svg.on_pointermove({ clientX: 250 });
  assert.equal(details[0].textContent, "1 kWh — chart: ≈0.80 DKK (interpolated)");
  svg.on_pointermove({ clientX: 500 });
  assert.equal(details[0].textContent, "4 kWh — chart: ≈3.20 DKK (interpolated)");
  assert.equal(card._results.children.find(node => node.className === "chart-select").selectedIndex, -1);
  svg.getBoundingClientRect = () => ({ left: 100, width: 1200, height: 200 });
  svg.on_pointermove({ clientX: 600 });
  assert.equal(details[0].textContent, "1 kWh — chart: ≈0.80 DKK (interpolated)");
  intervalGraph.on_pointerdown({ clientX: 400, pointerType: "touch" });
  assert.equal(details[1].textContent, "0–5 kWh — interval_chart: 0.80 DKK/kWh");
  const selectors = card._results.children.filter(node => node.className === "chart-select");
  assert.equal(selectors[0].attributes["aria-label"], "chart (DKK)");
  selectors[0].value = "1";
  selectors[0].on_change();
  assert.equal(details[0].textContent, "5 kWh — chart: 4.00 DKK");
  assert(intervalGraph.children.some(node => node.tag === "line"));
  assert(!card._results.children.some(node => node.textContent === labels.interval_curve_note));
  assert(!card._results.children.some(node => node.textContent === labels.chart_growth_legend));
  assert(!card._results.children.some(node => node.textContent === labels.interval_color_legend));
  const rangeRows = tables(card)[1].children;
  assert.deepEqual(rangeRows[0].children.slice(-2).map(node => node.textContent), [
    labels.interval_profit, labels.interval_per_kwh,
  ]);
  for (const language of ["da", "de", "en", "pl", "uk"]) {
    const translated = JSON.parse(fs.readFileSync(`custom_components/battmind/texts/${language}.json`, "utf8")).dashboard;
    for (const key of ["what_if_module_estimated_annual_gain", "what_if_module_current_cost_warning"]) {
      assert(translated[key]);
      assert(!/[?\uFFFD]/.test(translated[key]), `${language} ${key} contains corrupted characters`);
      assert(cardSource.includes(JSON.stringify(translated[key])), `${language} ${key} differs from card fallback`);
    }
    const oldLabels = { ...labels };
    delete oldLabels.interval_profit;
    oldLabels.interval_per_kwh = "";
    card._config.labels = oldLabels;
    card._hass.language = `${language}-${language.toUpperCase()}`;
    card._renderedRevision = -1;
    card._renderSession();
    assert.equal(card._baselineNote.textContent, translated.what_if_baseline_note);
    card._show(simulation);
    assert.deepEqual(tables(card)[1].children[0].children.slice(-2).map(node => node.textContent), [
      translated.what_if_interval_profit, translated.what_if_interval_per_kwh,
    ]);
  }
  card._config.labels = labels;
  card._hass.language = "en";
  card._show(simulation);
  assert(!card._results.children.some(node => node.textContent === "module_monthly_title (DKK)"));
  const monthKeys = ["2026-01", "2026-02", "2026-03", "2026-04"];
  const monthData = values => Object.fromEntries(monthKeys.map((key, index) => [key, values[index]]));
  const monthly = {
    ...simulation,
    current: { ...simulation.current, months: Object.fromEntries(monthKeys.map(key => [key, { profit: 2 }])) },
    scenario: { ...simulation.scenario, months: Object.fromEntries(monthKeys.map((key, index) => [key, { profit: index + 1 }])) },
    range: simulation.range.map(item => ({ ...item, months: monthData([item.battery_size, 1, 2, 3]) })),
    module_comparison: { ...simulation.module_comparison, rows: simulation.module_comparison.rows.map(row => ({
      ...row, months: monthData([row.added_modules, 5, 1, 1]),
    })) },
  };
  card._show(monthly);
  const monthlyTitles = card._results.children.filter(node => node.tag === "h3").map(node => node.textContent);
  for (const key of ["module_monthly_title", "module_monthly_range_title", "module_monthly_modules_title"]) {
    assert(monthlyTitles.includes(`${key} (DKK)`), key);
  }
  const bars = card._results.children.filter(node => node.className === "module-chart");
  assert.equal(bars.length, 3);
  for (const chart of bars) {
    const svg = chart.children.find(node => node.tag === "svg");
    assert.equal(svg.attributes.viewBox, "0 0 600 200");
    assert(svg.children.some(node => node.tag === "rect"));
    const barHover = svg.children.at(-1);
    svg.on_pointermove({ clientX: 300 });
    assert.equal(barHover.style.display, "");
    assert(barHover.children.some(node => node.tag === "text" && /DKK/.test(node.textContent)));
    assert(!svg.style.minWidth);
  }
  card._show({ ...monthly, scenario: { ...monthly.scenario, months: { "2026-01": { profit: 1 } } } });
  assert(!card._results.children.some(node => node.textContent === "module_monthly_title (DKK)"));
  card._show(simulation);
  card._show({ ...simulation, range: [
    { battery_size: 0, profit: 0, bill: 50 },
    { battery_size: 8, profit: 4616.34, bill: -4566.34 },
  ] });
  const interpolatedChart = charts(card)[0];
  const interpolatedDetail = card._results.children.find(node => node.className === "chart-detail");
  interpolatedChart.on_pointermove({ clientX: 520 });
  assert.match(interpolatedDetail.textContent, /6 kWh/);
  assert.match(interpolatedDetail.textContent, /3,462\.26 DKK/);
  assert.match(interpolatedDetail.textContent, /interpolated/);
  const hoverGroup = interpolatedChart.children.find(node => node.tag === "g");
  assert.equal(hoverGroup.style.display, "");
  assert.equal(hoverGroup.children[2].textContent, "6 kWh: ≈3,462.26 DKK");
  assert.equal(hoverGroup.children[0].attributes.x1, 420);
  interpolatedChart.on_pointermove({ clientX: 640 });
  assert.match(interpolatedDetail.textContent, /8 kWh/);
  assert.doesNotMatch(interpolatedDetail.textContent, /interpolated/);
  const capacityControl = card._results.children.find(node => node.tag === "input" && node.className === "chart-select");
  assert.equal(capacityControl.attributes["aria-label"], "battery");
  assert.equal(interpolatedDetail.attributes["aria-live"], "polite");
  capacityControl.value = "6";
  capacityControl.on_input();
  assert.match(interpolatedDetail.textContent, /6 kWh.*3,462\.26 DKK/);
  interpolatedChart.on_pointerleave({ pointerType: "mouse" });
  assert.equal(hoverGroup.style.display, "none");
  interpolatedChart.on_pointermove({ clientX: 100 });
  assert.match(interpolatedDetail.textContent, /^0 kWh/);
  interpolatedChart.on_pointermove({ clientX: 700 });
  assert.match(interpolatedDetail.textContent, /^8 kWh/);
  card._show({ ...simulation, range: [
    { battery_size: 0, profit: 10, bill: 40 },
    { battery_size: 2, profit: 20, bill: 30 },
    { battery_size: 8, profit: 8, bill: 42 },
  ] });
  const uneven = charts(card)[0];
  const unevenDetail = card._results.children.find(node => node.className === "chart-detail");
  uneven.on_pointermove({ clientX: 400 });
  assert.match(unevenDetail.textContent, /4 kWh.*≈19\.56 DKK/);
  assert(charts(card)[0].children.some(node => node.tag === "path"));
  const curve = [{ position: 0, value: 10 }, { position: 2, value: 20 }, { position: 8, value: 8 }];
  const slopes = smoothSlopes(curve);
  for (let i = 0; i < curve.length - 1; i++) {
    for (let step = 0; step <= 100; step++) {
      const x = curve[i].position + (curve[i + 1].position - curve[i].position) * step / 100;
      const y = smoothValue(curve[i], curve[i + 1], slopes[i], slopes[i + 1], x);
      assert(y >= Math.min(curve[i].value, curve[i + 1].value) - 1e-10);
      assert(y <= Math.max(curve[i].value, curve[i + 1].value) + 1e-10);
    }
  }
  assert.equal(smoothSlopes([{ position: 0, value: 1 }, { position: 0, value: 2 }]), null);
  const intervalUneven = charts(card)[1];
  const intervalDetail = card._results.children.filter(node => node.className === "chart-detail")[1];
  assert(intervalUneven.children.some(node => node.tag === "path"));
  intervalUneven.on_pointermove({ clientX: 280 });
  assert.match(intervalDetail.textContent, /^2 kWh.*≈3\.25 DKK\/kWh.*interpolated/);
  intervalUneven.on_pointermove({ clientX: 640 });
  assert.match(intervalDetail.textContent, /^2–8 kWh.*-2\.00 DKK\/kWh/);
  intervalUneven.on_pointermove({ clientX: 100 });
  assert.match(intervalDetail.textContent, /^0–2 kWh.*5\.00 DKK\/kWh/);
  assert(card._results.children.some(node => node.textContent === labels.interval_curve_note));
  assert(card._results.children.some(node => node.textContent === labels.chart_growth_legend));
  assert(card._results.children.some(node => node.textContent === labels.interval_color_legend));
  const gradient = uneven.children.find(node => node.tag === "defs").children[0];
  assert.deepEqual(gradient.children.map(stop => stop.attributes["stop-color"]), [
    "rgb(54, 162, 105)", "rgb(54, 162, 105)", "rgb(215, 92, 80)", "rgb(215, 92, 80)",
  ]);
  assert.deepEqual(gradient.children.map(stop => stop.attributes.offset), ["0%", "25%", "25%", "100%"]);
  assert.equal(gradient.attributes.x1, 60);
  assert.equal(gradient.attributes.x2, 540);
  assert.equal(gradient.attributes.y1, "0");
  assert.equal(gradient.attributes.y2, "0");
  assert.match(uneven.children.find(node => node.tag === "path").style.stroke, /^url\(#battmind-profit-gradient-/);
  assert(uneven.children.filter(node => node.tag === "circle").every(node => !node.style.fill));
  const intervalGradient = intervalUneven.children.find(node => node.tag === "defs").children[0];
  assert.notEqual(intervalGradient.attributes.id, gradient.attributes.id);
  assert.equal(intervalGradient.attributes.y1, 145);
  assert.equal(intervalGradient.attributes.y2, 40);
  assert(intervalUneven.children.filter(node => node.tag === "circle").every(node => node.style.fill === `url(#${intervalGradient.attributes.id})`));
  card._show({ ...simulation, range: [
    { battery_size: 0, profit: 0, bill: 50 },
    { battery_size: 2, profit: 10, bill: 40 },
    { battery_size: 8, profit: 16, bill: 34 },
    { battery_size: 10, profit: 26, bill: 24 },
  ] });
  const increasingGradient = charts(card)[0].children.find(node => node.tag === "defs").children[0];
  assert(charts(card)[0].children.filter(node => node.tag === "circle").every(node => node.attributes.class !== "best-point"));
  assert.deepEqual(increasingGradient.children.map(stop => stop.attributes["stop-color"]), [
    "rgb(54, 162, 105)", "rgb(54, 162, 105)",
    "rgb(215, 92, 80)", "rgb(215, 92, 80)",
    "rgb(54, 162, 105)", "rgb(54, 162, 105)",
  ]);
  assert.deepEqual(increasingGradient.children.map(stop => stop.attributes.offset),
    ["0%", "20%", "20%", "80%", "80%", "100%"]);
  card._show(simulation);
  assert(card._results.children.some(node => node.textContent === labels.scenario_section));
  assert(card._results.children.some(node => node.textContent === labels.range_section));
  assert(card._results.children.some(node => node.textContent === labels.range_columns));
  assert(card._results.children.some(node => node.textContent === labels.interval_example));
  assert.deepEqual(rangeRows[1].children.map(node => node.textContent), ["0 kWh", "0.00 DKK", "50.00 DKK", "–", "–"]);
  assert.deepEqual(rangeRows[2].children.map(node => node.textContent), ["5 kWh", "4.00 DKK", "46.00 DKK", "4.00 DKK", "0.80 DKK/kWh"]);
  assert.equal(rangeRows[2].children[1].className, "best");
  assert.equal(rangeRows[2].children[1].children[0].textContent, labels.highest_profit);
  assert.equal(rangeRows[2].children[4].children[0].textContent, labels.highest_rate);
  assert.equal(intervalGraph.children.filter(node => node.attributes.class === "best-point").length, 1);
  assert.equal(intervalGraph.children.filter(node => node.tag === "polyline").length, 1);
  assert.equal(intervalGraph.attributes["aria-label"], "interval_chart (DKK/kWh)");
  card._config.labels = { ...labels };
  delete card._config.labels.interval_chart;
  card._show(simulation);
  assert.equal(charts(card)[1].attributes["aria-label"], "Profit change per added kWh of battery capacity (DKK/kWh)");
  card._config.labels = labels;
  card._show(simulation);
  assert(card._results.children.some(node => node.textContent === labels.sell_history));
  assert(card._results.children.some(node => node.className === "warning" && node.textContent === "missing: 24 hours"));
  assert(card._results.children.some(node => node.className === "warning" && node.textContent === "tariffs: 1 / 2"));
  assert(card._results.children.some(node => node.className === "warning" && node.textContent === labels.sell_history));

  card._show({ ...simulation, range: [
    { battery_size: 0, profit: 0, bill: 50 },
    { battery_size: 2, profit: 6, bill: 44 },
    { battery_size: 5, profit: 3, bill: 47 },
    { battery_size: 5, profit: 3, bill: 47 },
    { battery_size: 9, profit: 3, bill: 47 },
  ] });
  const comparisonRows = tables(card)[1].children;
  assert.deepEqual(comparisonRows[2].children.slice(-2).map(node => node.textContent), ["6.00 DKK", "3.00 DKK/kWh"]);
  assert.deepEqual(comparisonRows[3].children.slice(-2).map(node => node.textContent), ["-3.00 DKK", "-1.00 DKK/kWh"]);
  assert.deepEqual(comparisonRows[4].children.slice(-2).map(node => node.textContent), ["–", "–"]);
  assert.deepEqual(comparisonRows[5].children.slice(-2).map(node => node.textContent), ["0.00 DKK", "0.00 DKK/kWh"]);
  assert.equal(comparisonRows[2].children[1].className, "best");
  assert.equal(comparisonRows[2].children[4].className, "best");
  const intervalTexts = charts(card)[1].children.filter(node => node.tag === "text").map(node => node.textContent);
  assert(intervalTexts.includes("-1.00"));
  assert(intervalTexts.includes("0.00"));
  const intervalCurve = charts(card)[1];
  assert(intervalCurve.children.some(node => node.tag === "path"));
  assert(intervalCurve.children.some(node => node.tag === "defs"));
  assert(!charts(card)[0].children.some(node => node.tag === "defs"));
  assert(!card._results.children.some(node => node.textContent === labels.chart_growth_legend));
  const intervalControl = card._results.children.filter(node => node.tag === "input" && node.className === "chart-select").at(-1);
  const intervalCurveDetail = card._results.children.filter(node => node.className === "chart-detail")[1];
  intervalControl.value = "2";
  intervalControl.on_input();
  assert.match(intervalCurveDetail.textContent, /^2 kWh.*DKK\/kWh.*interpolated/);
  intervalControl.value = "3.5";
  intervalControl.on_input();
  assert.match(intervalCurveDetail.textContent, /^2–5 kWh.*-1\.00 DKK\/kWh/);
  intervalControl.value = "0";
  intervalControl.on_input();
  assert.equal(intervalCurveDetail.textContent, "");
  assert.equal(intervalControl.attributes["aria-invalid"], "true");
  intervalCurve.on_pointermove({ clientX: 160 });
  assert.equal(intervalControl.attributes["aria-invalid"], "false");
  assert.match(intervalCurveDetail.textContent, /^0–2 kWh.*3\.00 DKK\/kWh/);
  intervalControl.value = "0";
  intervalControl.on_input();
  intervalControl.on_change();
  assert.match(intervalCurveDetail.textContent, /^0–2 kWh.*3\.00 DKK\/kWh/);
  assert.equal(intervalControl.attributes["aria-invalid"], "false");
  intervalControl.value = "20";
  intervalControl.on_input();
  assert.equal(intervalCurveDetail.textContent, "");
  intervalControl.on_change();
  assert.match(intervalCurveDetail.textContent, /^5–9 kWh.*0\.00 DKK\/kWh/);
  const gradientId = gradient.attributes.id;
  for (const language of ["da", "de", "en", "pl", "uk"]) {
    const translated = JSON.parse(fs.readFileSync(`custom_components/battmind/texts/${language}.json`, "utf8")).dashboard;
    card._config.labels = { ...labels };
    delete card._config.labels.chart_growth_legend;
    card._config.labels.chart_color_legend = "stale total-profit legend";
    delete card._config.labels.interval_color_legend;
    card._hass.language = language;
    card._show({ ...simulation, range: [
      { battery_size: 0, profit: 0, bill: 50 },
      { battery_size: 2, profit: -2, bill: 52 },
      { battery_size: 4, profit: -6, bill: 56 },
    ] });
    assert(card._results.children.some(node => node.textContent === translated.what_if_chart_growth_legend));
    assert(!card._results.children.some(node => node.textContent === "stale total-profit legend"));
    assert(card._results.children.some(node => node.textContent === translated.what_if_interval_color_legend));
    const negativeGradient = charts(card)[0].children.find(node => node.tag === "defs").children[0];
    assert.notEqual(negativeGradient.attributes.id, gradientId);
    assert.deepEqual(negativeGradient.children.map(stop => stop.attributes["stop-color"]), [
      "rgb(54, 162, 105)", "rgb(54, 162, 105)", "rgb(215, 92, 80)", "rgb(215, 92, 80)",
    ]);
  }
  card._config.labels = labels;
  card._hass.language = "en";
  card._show({ ...simulation, range: [
    { battery_size: 0, profit: 0, bill: 50 },
    { battery_size: 2, profit: 6, bill: 44 },
    { battery_size: 5, profit: 3, bill: 47 },
    { battery_size: 5, profit: 3, bill: 47 },
    { battery_size: 9, profit: 3, bill: 47 },
  ] });
  const capacityDots = charts(card)[0].children.filter(node => node.tag === "circle");
  assert.equal((capacityDots[1].attributes.cx - capacityDots[0].attributes.cx) /
    (capacityDots[2].attributes.cx - capacityDots[0].attributes.cx), 2 / 5);
  card._show({ ...simulation, range: [{ battery_size: 0, profit: 0, bill: 50 }] });
  assert.equal(charts(card).length, 1);
  assert.notEqual(tables(card)[1].children[1].children[1].className, "best");
  assert(card._results.children.some(node => node.textContent === labels.bill_explain));
  card._show({ ...simulation, range: [
    { battery_size: 0, profit: -2, bill: 52 },
    { battery_size: 2, profit: -4, bill: 54 },
    { battery_size: 4, profit: -6, bill: 56 },
  ] });
  const negativeRows = tables(card)[1].children;
  assert.equal(negativeRows[1].children[1].className, "best");
  assert.equal(negativeRows[2].children[4].className, "best");
  assert.equal(negativeRows[3].children[4].className, "best");
  assert(!charts(card)[1].children.some(node => node.tag === "defs"));
  assert(!card._results.children.some(node => node.textContent === labels.chart_growth_legend));
  assert(!card._results.children.some(node => node.textContent === labels.interval_color_legend));
  card._show({ ...simulation, range: [
    { battery_size: 0, profit: -2, bill: 52 },
    { battery_size: 2, profit: -2, bill: 52 },
    { battery_size: 4, profit: -2, bill: 52 },
  ] });
  assert(charts(card).every(chart => !chart.children.some(node => node.tag === "defs")));
  assert(!card._results.children.some(node => node.textContent === labels.chart_growth_legend || node.textContent === labels.interval_color_legend));
  card._show({ ...simulation, range: [
    { battery_size: 0, profit: 0, bill: 50 },
    { battery_size: 0.001, profit: 0.01, bill: 49.99 },
    { battery_size: 10.001, profit: 0.02, bill: 49.98 },
  ] });
  const tinyRows = tables(card)[1].children;
  assert.equal(tinyRows[2].children[0].textContent, "0.001 kWh");
  assert.equal(tinyRows[3].children[4].textContent, "0.00 DKK/kWh");
  assert(charts(card)[1].children.some(node => node.tag === "text" && node.textContent === "0.00"));
  const tinyGraph = charts(card)[0];
  const tinyDetail = card._results.children.find(node => node.className === "chart-detail");
  const tinyDot = tinyGraph.children.filter(node => node.tag === "circle")[1];
  tinyGraph.on_pointermove({ clientX: 100 + tinyDot.attributes.cx });
  assert.match(tinyDetail.textContent, /^0\.001 kWh.*0\.01 DKK/);

  await card._run(form, false);
  assert.equal("range_start" in request.data, false);
  const withoutRange = { ...simulation };
  delete withoutRange.range;
  card._hass.callWS = serviceOnly(async () => ({ response: { simulations: { battmind: withoutRange } } }));
  await card._run(form, false);
  const singleChart = charts(card).at(-1);
  assert.equal(singleChart.children.filter(node => node.tag === "circle").length, 2);
  const singleSelector = card._results.children.find(node => node.className === "chart-select");
  assert.equal(singleSelector.children[0].textContent, "current (10 kWh) — 10.00 DKK");
  assert.equal(singleSelector.children[1].textContent, "scenario (0 kWh) — 0.00 DKK");
  card._hass.callWS = serviceOnly(async () => ({ response: { simulations: {} } }));
  await card._run(form, false);
  assert.match(card._status.textContent, /Unexpected simulation response/);
  card._hass.callWS = serviceOnly(async () => { throw new Error("unavailable"); });
  await card._run(form, false);
  assert.match(card._status.textContent, /unavailable/);
  card._hass.callWS = serviceOnly(async () => { throw { code: "service_failed", message: { detail: "No history available" } }; });
  await card._run(form, false);
  assert.match(card._status.textContent, /No history available/);
  assert.doesNotMatch(card._status.textContent, /\[object Object\]/);
  card._hass.callWS = serviceOnly(async () => { throw { error: { code: "failed", message: "Invalid request" } }; });
  await card._run(form, false);
  assert.match(card._status.textContent, /Invalid request/);
  assert.equal(card._button.disabled, false);

  let resolve;
  card._hass.callWS = serviceOnly(() => new Promise(done => { resolve = done; }));
  const pending = card._run(form, false);
  assert.equal(card._button.disabled, true);
  card.disconnectedCallback();
  const replacement = new Card();
  replacement.setConfig({ config_entry_id: "entry-1", labels });
  replacement.hass = card._hass;
  replacement.connectedCallback();
  assert.equal(replacement._button.disabled, true);
  await replacement._run(form, false);
  resolve({ response: { simulations: { battmind: simulation } } });
  await pending;
  assert.equal(replacement._button.disabled, false);
  assert(charts(replacement).length);
  replacement.disconnectedCallback();
  const reopened = new Card();
  reopened.setConfig({ config_entry_id: "entry-1", labels });
  reopened.hass = card._hass;
  reopened.connectedCallback();
  assert(charts(reopened).length);
  assert.equal(reopened._session.listeners.size, 1);
  const other = new Card();
  other.setConfig({ config_entry_id: "entry-2", labels });
  other.hass = card._hass;
  other.connectedCallback();
  assert.equal(other._results.textContent, labels.empty);
  assert.equal(other._button.disabled, false);
  reopened.disconnectedCallback();
  assert.equal(reopened._session.listeners.size, 0);

  let resolveAbsent;
  other._hass.callWS = serviceOnly(() => new Promise(done => { resolveAbsent = done; }));
  const pendingAbsent = other._run(form, true);
  other.disconnectedCallback();
  resolveAbsent({ response: { simulations: { other: simulation } } });
  await pendingAbsent;
  const returned = new Card();
  returned.setConfig({ config_entry_id: "entry-2", labels });
  returned.hass = card._hass;
  returned.connectedCallback();
  assert(charts(returned).length);
  const cardElement = returned.shadowRoot.children.find(node => node.tag === "ha-card");
  const renderedForm = cardElement.children.find(node => node.tag === "form");
  const historyInput = renderedForm.children.find(node => node.tag === "label" && node.children[0]?.name === "history_days").children[0];
  const batteryInput = renderedForm.children.find(node => node.tag === "label" && node.children[0]?.name === "battery_size").children[0];
  assert.equal(historyInput.value, "30");
  assert.equal(batteryInput.value, "0");
  assert.equal(renderedForm.children.find(node => node.className === "check").children[0].checked, true);

  let rejectAbsent;
  returned._hass.callWS = serviceOnly(() => new Promise((_, reject) => { rejectAbsent = reject; }));
  const pendingError = returned._run(form, false);
  returned.disconnectedCallback();
  rejectAbsent(new Error("connection lost"));
  await pendingError;
  const afterError = new Card();
  afterError.setConfig({ config_entry_id: "entry-2", labels });
  afterError.hass = card._hass;
  afterError.connectedCallback();
  assert.match(afterError._status.textContent, /connection lost/);
  assert.equal(afterError._button.disabled, false);
  afterError.disconnectedCallback();

  let resolveCancelRun;
  let cancelJob;
  const cancelCard = new Card();
  cancelCard.setConfig({ config_entry_id: "entry-cancel", labels });
  cancelCard.hass = {
    language: "en", config: { currency: "DKK" },
    callWS: message => {
      if (message.type === "battmind/what_if_last") return Promise.resolve(null);
      if (message.type === "battmind/what_if_status") return Promise.resolve(cancelJob
        ? { job_id: cancelJob, status: "running", progress: { phase: "statistics", seconds: 8 } }
        : { job_id: null, status: "idle" });
      if (message.type === "battmind/what_if_start") {
        cancelJob = message.job_id;
        return new Promise(resolve => { resolveCancelRun = resolve; });
      }
      assert.equal(message.type, "battmind/what_if_cancel");
      assert.equal(message.config_entry_id, "entry-cancel");
      assert.equal(message.job_id, cancelJob);
      return Promise.resolve({ job_id: cancelJob, status: "stopping" });
    },
  };
  cancelCard.connectedCallback();
  await new Promise(resolve => setImmediate(resolve));
  cancelCard._session.result = simulation;
  const cancelRun = cancelCard._run(form, false);
  assert.equal(cancelCard._stopButton.hidden, false);
  assert.equal(cancelCard._button.disabled, true);
  await timers.get(cancelCard._refreshTimer)();
  assert.equal(cancelCard._progress.hidden, false);
  assert.match(cancelCard._progressText.textContent, /Loading long-term statistics/);
  await cancelCard._stop();
  assert.equal(cancelCard._stopButton.disabled, true);
  assert.equal(cancelCard._status.textContent, "Stopping simulation…");
  resolveCancelRun({ job_id: cancelJob, status: "cancelled" });
  cancelJob = null;
  await cancelRun;
  assert.equal(cancelCard._session.result, simulation);
  assert.equal(cancelCard._status.textContent, "Simulation stopped");
  assert.equal(cancelCard._stopButton.hidden, true);
  assert.equal(cancelCard._button.disabled, false);
  cancelCard.disconnectedCallback();

  const remote = new Card();
  let remoteStatus = {
    job_id: "external-job", status: "running",
    progress: { phase: "states", seconds: 20 },
  };
  remote.setConfig({ config_entry_id: "entry-remote", labels });
  remote.hass = {
    language: "en", config: { currency: "DKK" },
    callWS: async message => {
      if (message.type === "battmind/what_if_last") return null;
      if (message.type === "battmind/what_if_status") return remoteStatus;
      assert.equal(message.job_id, "external-job");
      remoteStatus = { job_id: "external-job", status: "stopping" };
      return remoteStatus;
    },
  };
  remote.connectedCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(remote._button.disabled, true);
  assert.equal(remote._stopButton.hidden, false);
  assert.equal(remote._progress.hidden, false);
  assert.equal("value" in remote._progressBar.attributes, false);
  assert.match(remote._progressText.textContent, /Loading historical states/);
  const remotePeer = new Card();
  remotePeer.setConfig({ config_entry_id: "entry-remote", labels });
  remotePeer.hass = remote._hass;
  remotePeer.connectedCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(remotePeer._session, remote._session);
  assert.match(remotePeer._progressText.textContent, /Loading historical states/);
  remoteStatus = {
    job_id: "external-job", status: "running",
    progress: { percent: 10, step: 1, steps: 1, hours: 100, total_hours: 1000, seconds: 10 },
  };
  await timers.get(remote._refreshTimer)();
  assert.equal(remote._progressBar.value, 10);
  assert.equal(remotePeer._progressBar.value, 10);
  assert.doesNotMatch(remote._progressText.textContent, /Estimated time remaining/);
  remoteStatus = {
    job_id: "external-job", status: "running",
    progress: { percent: 20, step: 1, steps: 1, hours: 200, total_hours: 1000, seconds: 35 },
  };
  await timers.get(remote._refreshTimer)();
  assert.equal(remote._progressBar.value, 20);
  assert.equal(remotePeer._progressBar.value, 20);
  assert.match(remote._progressText.textContent, /Estimated time remaining/);
  assert.match(remote._progressText.textContent, /estimate/);
  await remote._stop();
  assert.equal(remote._stopButton.disabled, true);
  remoteStatus = { job_id: null, status: "idle" };
  await timers.get(remote._refreshTimer)();
  assert.equal(remote._button.disabled, false);
  assert.equal(remote._stopButton.hidden, true);
  remote._hass.callWS = async message => {
    if (message.type === "battmind/what_if_status") throw new Error("status unavailable");
    return null;
  };
  await timers.get(remote._refreshTimer)();
  assert.match(remote._status.textContent, /status unavailable/);
  remote._hass.callWS = async message => message.type === "battmind/what_if_status"
    ? { job_id: null, status: "idle" } : null;
  await timers.get(remote._refreshTimer)();
  assert.equal(remote._status.textContent, "");
  remote.disconnectedCallback();
  remotePeer.disconnectedCallback();

  const persisted = new Card();
  persisted.setConfig({ config_entry_id: "entry-3", labels });
  persisted.hass = {
    language: "en", config: { currency: "DKK" },
    callWS: async message => {
      if (message.type === "battmind/what_if_status") return { job_id: null, status: "idle" };
      assert.equal(message.type, "battmind/what_if_last");
      assert.equal(message.config_entry_id, "entry-3");
      return { completed_at: "2026-01-31T12:00:00+00:00", inputs: {
        history_days: 30, battery_size: 0, range_start: 0, range_end: 10, range_step: 2,
        module_capacity: 2.5, module_price: 125, module_count: 3,
      }, simulation };
    },
  };
  persisted.connectedCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert(charts(persisted).length);
  assert(persisted._results.children.some(node => node.tag === "p" && node.textContent.startsWith("completed:")));
  const list = persisted._results.children.find(node => node.tag === "ul");
  assert(list.children.some(node => node.textContent === "battery: 0"));
  assert(list.children.some(node => node.textContent === "history: 30"));
  assert.equal(persisted._formInputs.history.value, "30");
  assert.equal(persisted._formInputs.battery.value, "0");
  assert.equal(persisted._formInputs.rangeToggle.checked, true);
  assert.equal(persisted._formInputs.inputs[1].value, "10");
  assert.equal(persisted._formInputs.inputs[2].disabled, false);
  assert.equal(persisted._formInputs.moduleToggle.checked, true);
  assert.equal(persisted._formInputs.moduleInputs.capacity.value, "2.5");
  assert.equal(persisted._formInputs.moduleInputs.price.value, "125");
  assert.equal(persisted._formInputs.moduleInputs.count.value, "3");
  const refresh = timers.get(persisted._refreshTimer);
  persisted._hass.callWS = async () => { throw new Error("offline"); };
  await refresh();
  assert.equal(persisted._session.result, simulation);
  assert(persisted._status.textContent.includes("offline"));
  persisted._hass.callWS = async () => { throw { error: { code: "failed", message: "Load refused" } }; };
  await refresh();
  assert.match(persisted._status.textContent, /Load refused/);
  assert.doesNotMatch(persisted._status.textContent, /\[object Object\]/);
  persisted._hass.callWS = async message => message.type === "battmind/what_if_status" ? { job_id: null, status: "idle" } : ({
    completed_at: "2026-02-01T12:00:00+00:00", inputs: { battery_size: 20 }, simulation,
  });
  await refresh();
  assert.equal(persisted._session.inputs.battery_size, 20);
  assert.equal(persisted._formInputs.battery.value, "20");
  assert.equal(persisted._formInputs.rangeToggle.checked, false);
  assert.equal(persisted._session.loadError, null);
  persisted.disconnectedCallback();

  let resolveEdited;
  const edited = new Card();
  edited.setConfig({ config_entry_id: "entry-edited", labels });
  edited.hass = { language: "en", config: { currency: "DKK" },
    callWS: message => message.type === "battmind/what_if_status"
      ? Promise.resolve({ job_id: null, status: "idle" })
      : new Promise(resolve => { resolveEdited = resolve; }) };
  edited.connectedCallback();
  await new Promise(resolve => setImmediate(resolve));
  edited._formInputs.battery.value = "42";
  edited._form.on_input();
  resolveEdited({ completed_at: "2026-01-31T12:00:00+00:00", inputs: { battery_size: 10 }, simulation });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(edited._formInputs.battery.value, "42");
  assert(edited._session.result);
  const editedRefresh = timers.get(edited._refreshTimer);
  edited._hass.callWS = async message => message.type === "battmind/what_if_status"
    ? { job_id: null, status: "idle" }
    : { completed_at: "2026-02-01T12:00:00+00:00", inputs: { battery_size: 12 }, simulation };
  await editedRefresh();
  assert.equal(edited._formInputs.battery.value, "42");
  edited.disconnectedCallback();

  let resolveOld;
  const stale = new Card();
  stale.setConfig({ config_entry_id: "entry-4", labels });
  stale.hass = {
    language: "en", config: { currency: "DKK" },
    callWS: message => message.type === "battmind/what_if_last"
      ? new Promise(resolve => { resolveOld = resolve; })
      : message.type === "battmind/what_if_status"
        ? Promise.resolve({ job_id: null, status: "idle" })
        : Promise.resolve({ job_id: message.job_id, status: "completed", result: simulation }),
  };
  stale.connectedCallback();
  await new Promise(resolve => setImmediate(resolve));
  await stale._run(form, false);
  resolveOld({ completed_at: "2025-01-01T00:00:00+00:00", inputs: { battery_size: 999 }, simulation });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(stale._session.inputs.battery_size, 0);
  assert.equal(stale._session.completedAt, null);
  stale.disconnectedCallback();
  assert.equal(timers.size, 0);
}

main().catch(error => { console.error(error); process.exitCode = 1; });
