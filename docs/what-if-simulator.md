# What-if simulator

The **battmind.what_if** action answers questions such as:

- Would a larger battery have saved more money during the last year?
- How would the result change across a range of battery capacities?
- How much would more charge/discharge power have improved the economy?
- How much do loss, wear or the reserve affect the result?
- Would the rules have done better or worse than the whole period optimisation?
- What would another rule switch, threshold or excluded selling hours have meant?

It replays the BattMind planner on your own hourly history twice: once with the current configuration
and settings, and once with the values you change. Both use the same hourly Home Assistant long-term
statistics for consumption, solar production and buy/sell prices. By default it uses up to 365 days.
The active plan is not changed, and no inverter command is sent.

**Current** is a simulated estimate assuming the current configuration and settings applied
throughout the historical period, not the actual historical result. Concentrate on the difference
between current and scenario, which compares both under the same replay assumptions. That
difference is also an estimate, not a guaranteed saving or an investment return.

## How the replay works

A separate planner (not the live one, its plan or its databases) plans every historical day with the
real BattMind rules and the whole period optimisation, exactly as the live planner does:

- **Prices:** only the real recorded prices of the configured buy/sell entities (Strømligning first,
  Energi Data Service for the hours without Strømligning history). The planner runs when the prices of
  the next day are published (13:00) and plans until the end of the next day; that plan is executed
  until the next publication. A history that starts before 13:00 is first planned with the prices of
  its own day. No price forecast or offline prices are used.
- **Consumption and solar:** the actual consumption (without ignored consumers) and the actual solar
  surplus of each hour are the forecast, so the plan is not disturbed by a bad forecast. The forecast
  type and the price forecast correction therefore have no effect in the simulator. The solar surplus
  is the solar production minus that consumption, so solar that ignored consumers used (for example an
  electric car) counts as surplus: the simulation exports it or uses it to charge the battery.
- **Controller:** with **optimizer_control** on, the hours are executed with the whole period
  optimisation, otherwise with the rule plan, both converted to battery actions the same way as the live
  battery control. The rules can only use what they are allowed to by the rule switches.
- **Execution:** every hour is calculated with the actual consumption and solar production and the
  same battery model (limits, charge/discharge efficiency, wear), so the two columns can be compared.
  The cost and age of the energy in the battery come from the simulated charging, the same as the live
  charging history.
- **Round-trip loss:** the replay has no charging history, so the round-trip loss currently measured
  by BattMind (or the configured one) is used for both columns.

## Run a simulation

There are two ways to run a simulation.

**From the dashboard.** The **What-if** tab of the BattMind dashboard has the inputs as entities: the
history in days, the battery size, a capacity range (a switch with start, end and step) and a module
comparison (a switch with module capacity, price and number of modules). The rules and settings of the
scenario are one entity each (*current*, *on* or *off* for a rule switch; a number or text that has no
value until you enter one, which means *current*). Turn on **Run simulation** (the switch
`switch.<prefix>_what_if_run`) to run one; turning it off cancels it. **Reset to current** (a button) clears
the scenario rules and settings. The inputs only feed the simulation: they are not planner settings, they do not
trigger a planning run and they are not part of a backup. One simulation runs at a time. The status
(`sensor.<prefix>_what_if_status`) shows *running* with the loading phase or the simulation progress,
*stopped*, or *failed* with the reason (for example an invalid range); `sensor.<prefix>_what_if_progress`
rises from 0 to 100 % while it runs; the result goes to `sensor.<prefix>_what_if` (see below). No persistent notification is created.

**From Developer tools or an automation.** Open **Developer tools → Actions**, select **BattMind: What-if
simulator**, choose the BattMind config entry and fill only the values you want to replace. Run the action to
receive a persistent notification with a two-column comparison of the configured values and the what-if
scenario; only values and rule switches that differ from the current setup are listed. The action also
keeps returning the full simulation response for automations and scripts. A progress notification is shown
while history is loaded and the simulation runs. While the history is loaded it shows the current step
(long-term statistics, raw Recorder history, Energi Data Service tariffs, hourly values, preparing the
simulation) and the elapsed seconds, updated when the step changes and at least every 10 seconds. When
the simulation starts it is updated at most every 10 seconds with the percentage done and the simulated
days, and the finished result (or an error) replaces that same notification. The action has more fields
than the dashboard (for example the battery levels, the charging and discharging power, the round-trip loss
and the wear cost).

The latest successful calculation for each BattMind instance is saved on the Home Assistant server,
with its completion time, submitted values and full simulation result. It is shown by the **What-if** sensor
(`sensor.<prefix>_what_if`: the state is the profit difference of the scenario, the attributes `result`
(the simulation as described below), `inputs`, `completed_at` and `view` (values derived for the dashboard,
such as interval rates and payback times)), also after reloading the page or restarting Home Assistant.
This applies to calculations started from the dashboard, Developer tools or automations. A failed or
stopped calculation leaves the previous saved result intact; removing the BattMind instance removes its saved
result. The attributes are not recorded in the Home Assistant database.

<p align="center">
  <img src="../images/what-if-result-notification.png" alt="The what-if action in Developer tools and the result notification comparing current and what-if" width="100%">
</p>

Example action:

    action: battmind.what_if
    data:
      config_entry_id: YOUR_CONFIG_ENTRY_ID
      history_days: 365
      battery_size: 20
      max_charging_power: 8000
      max_discharging_power: 8000
      round_trip_loss: 5
      wear_cost_per_kwh: 0.1
      usage_reserve: 30
      optimizer_control: false
      most_profit: false
      min_profit_per_kwh: 0.25
      exclude_sell_hours: "17,18,19"

To compare several capacities in one request, provide **range_start**, **range_end**, and
**range_step** together. The interval is in kWh, includes the start, and includes the end only when
the step reaches it exactly; a non-aligned end is not rounded or added. The interval can contain at
most 12 values, from 0 to 1000 kWh, and the step must be positive. Every interval result uses the
same other overrides and its own initial battery level and replay state. A zero-capacity result uses
the no-battery behavior described above. The existing **scenario** comparison remains unchanged;
the additional **range** response list contains one entry per capacity, with profit and bill
differences measured against **current**. If **battery_size** is also supplied, it still controls the
ordinary **scenario** comparison; each **range** entry uses its own capacity. Interval parameters
are not planner settings; they are saved only with the latest successful calculation. The capacity
replays run sequentially in the worker using separate
replay engines, so a range takes longer than one scenario but never runs concurrent calculations on
the live engine.

For an optional module comparison, provide **module_capacity** and **module_price** together;
**module_count** is optional (defaults to 5 and is limited to 12). The comparison starts with the
current battery capacity, then simulates that baseline and each cumulative whole-module increase.
Every case uses the same other supplied overrides, so the baseline is re-simulated with the candidates'
assumptions rather than reusing the differently configured **current** result. Module capacities may
be fractional, and zero module price is allowed. The module table reports historical total profit,
gain against the same-assumption baseline, the incremental historical gain from the previous module,
the entered module price and cumulative module cost separately. Each row also has **months**
(`YYYY-MM` → that month's battery profit) for the dashboard's monthly bar chart.

An annualized incremental gain and simple payback require a 365-day rolling requested window, with
at least 90% of the expected hours matched in **each local calendar-month portion** of that window.
Both partial months at the ends are assessed separately, even if they have the same month number.
Expected hours are the 8,760 hourly UTC instants in the half-open requested window, grouped by their
local month; this includes leap days and the actual DST transitions. Only unique matched instants on
that grid count. Unexpected, duplicate or out-of-window matched instants make annualization
unavailable, because their replay profit cannot safely be scaled using the valid count. Missing hours
are never filled with monthly averages for this comparison. Historical sell-price data must also be
available for every simulated hour, regardless of production-price configuration. When a raw Energi
Data Service sell price is used, its selling costs must be available as well. These gates do not
change the replay or its historical results.

Annualization scales the observed incremental gain by 8,760 divided by the actual matched hours; it
is a historical equivalent, not a future outcome. Payback is module price divided by positive annualized incremental gain; non-positive gains
have no payback, while a zero-priced module with a positive gain has zero simple payback. Module
costs are not subtracted from simulated profit and exclude installation, inverter, wiring, financing,
maintenance, taxes and future degradation. The replay uses recorded prices in its planning windows,
so forecast error and future prices, usage and solar production can make actual results different.

The separate `module_comparison.annual` result preserves those strict annualization fields and adds
an annual estimate for the marginal-module payback table and the payback graph. Each annual row also
has `annual_bill` (`without` = estimated yearly bill without a battery, `with` = bill with that module
count; null when unavailable), which the graph uses as the yearly bill of the grey no-battery line and
of each setup. It targets the complete 365-day requested window; a shorter
window is never extrapolated as a full year. When strict annualization is unavailable only because
hours are missing, the result uses monthly estimates (rather than scaling the matched gain) if at
least half of the 8,760 expected hours are matched and the existing calendar-month reference rule
can fill the rest. It estimates
each module case's missing monthly profit from that case's replayed `months[*].profit`, then
calculates cumulative gains against the baseline and marginal gains against the previous case.
This keeps module interactions in the case results rather than estimating profit differences
independently. Each case uses the effective charging, discharging and selling rules/settings from
the Settings page (and any explicit what-if overrides) used by its replay; filling missing hours
does not substitute default settings. The `annual.method` is `strict`, `estimated`, or
`unavailable`; an unavailable method also has a `status_reason`.
`annual.rows[*].module_count` is the number of added modules.
`cumulative_cost` and `annual_bill` feed the payback graph (a setup's line starts at the current
battery price plus `cumulative_cost` and rises with its `annual_bill.with`); the marginal table uses
the price of the last added module and `marginal_vs_previous.annual_gain` (its `payback_years`). The
graph's time for each extra module count is where its line crosses the line before it. It uses the
unscaled yearly bills, so it equals that figure for the `estimated` method; with `strict`, whose gains
are scaled to a full year, the graph and the whole-battery columns (also built from `annual_bill`) can
be somewhat longer than the table's module payback, never shorter. Payback is simple purchase cost
divided by positive annual gain; zero or negative gain has no payback.

The annual estimate's `coverage` reports matched and missing hours and their ratio.
`provenance.reference_months` maps estimated `YYYY-MM` periods to the calendar month numbers used;
`estimated_from_other_months` lists periods that borrowed from other calendar months, and
`estimated_hours` reports the number of imputed hours. `uncertainty.estimated_share` is the fraction
of the 8,760-hour window estimated, not a statistical confidence interval. Missing sell-price or
sell-cost valuation makes the annual result unavailable: no sell price or valuation is fabricated
for missing hours. The existing strict `annualization`, `annualized_incremental_gain`, and
`payback_years` fields remain unchanged and retain their original eligibility and calculation
semantics. The annual estimate is a historical replay estimate, not a price, usage, degradation, or
future performance forecast.

The `module_comparison.annualization` response includes `months`, with `expected_hours` and
`matched_hours` per `YYYY-MM`, and a `reason` when unavailable: `missing_requested_period`,
`partial_year`, `unexpected_timestamps`, `missing_sell_price`, `missing_sell_cost`, or `insufficient_monthly_coverage`
(in that precedence order). A partial month with very few requested hours must still meet 90%, a
deliberately conservative edge-window tradeoff. `matched_hours` counts unique on-grid instants for a
full requested year. The What-if tab has specific texts for `missing_sell_price` and `missing_sell_cost`;
other reasons use its coverage message.

`module_comparison.warnings` provides `dst_replay_resets`, `tariff_estimated_hours`,
`raw_current_sell_cost_hours`, and `raw_unknown_sell_cost_hours` as counts. A DST reset is counted
when consecutive matched UTC hours change offset and their naive local timestamps cause a replay
segment split; the replay and its results are unchanged. Repeated local times can also collide in
the replay's local-time keyed data. These effects may make the annualized result inaccurate and are
not quantified. Estimated tariffs and DST resets do not themselves make payback unavailable.
For raw Energi Data Service prices, current buy-sensor selling costs may be reused across history;
when those costs are unavailable, the existing sell-price calculation instead uses zero costs, but
module annualization and payback are suppressed with `missing_sell_cost`.
The warning counts identify the relevant hours but do not validate historical tariff accuracy.
The What-if tab displays these warnings when their counts are nonzero.
These module parameters are saved with the successful result but are not planner settings.

All fields are optional; a field you leave out uses the current setting. Power is in W, battery capacity
in kWh, and battery levels and round-trip loss are percentages.
Set **battery_size** to **0** to compare against a home without a battery. The scenario buys its
consumption and sells its solar surplus at the historical prices, without battery flows, wear or stored
energy value. Its reported starting and ending battery levels are 0%; battery levels have no physical
meaning without a battery. An explicitly supplied starting level is still validated against both
configurations, as in other simulations.

| Group | Fields |
|---|---|
| Battery | **battery_size**, **minimum_level**, **maximum_level**, **starting_level**, **max_charging_power**, **max_discharging_power**, **round_trip_loss**, **wear_cost_per_kwh** |
| Controller | **optimizer_control** (on: whole period optimisation, off: rules), **use_midnight_battery_level**, **solar_charging**, **enhance_battery_efficiency** |
| Profit and selling | **min_profit_per_kwh**, **min_total_profit**, **min_sell_kwh**, **usage_reserve**, **exclude_sell_hours** |
| Module comparison | **module_capacity**, **module_price**, **module_count** |
| Charging rules | **cheapest_hour_fill_planner**, **cheapest_hour_fill_up**, **cheap_price_periods**, **cheapest_price_rise_threshold**, **cheap_price_period_rise_threshold**, **most_expensive_planner**, **needed_before_max_level_planner**, **negative_price_planner**, **grid_charge_to_sell** |
| Discharging and selling rules | **only_discharge_on_profit**, **prioritize_discharge_hours_by_energy_cost**, **discharge_before_solar**, **most_profit**, **sell_excess_kwh_available**, **force_powerwall_max_hours_old_discharge**, **powerwall_max_hours_old** |
| Solar | **optimize_solar_charging** |

With **solar_charging** off the solar surplus never charges the battery in the simulation: the planner sees no
solar surplus, and the executed hours sell all of it instead of charging from it (the surplus is what is left
after the house consumption).

Setting **round_trip_loss** temporarily replaces the configured or measured efficiency curves (as if
**enhance_battery_efficiency** were off). The rule thresholds (cheap price periods, rise thresholds) only
change the result when the rules control the battery. The maximum kWh age works with both controllers. The notification shows
the current value beside the scenario value for each setting you changed.

## Reading the response

| Part | Meaning |
|---|---|
| **period** | first/last hour and the amount of historical data actually used |
| **data_sources** | counts of used entity-hour samples from long-term statistics and raw Recorder fallback |
| **price_provider** | the provider(s) that supplied the used hours, comma separated (**price_provider_hours** counts the hours of each) |
| **tariff_estimated_hours** | hours where the tariff of the same time of day on the nearest recorded day was used instead of an exact-hour value (never a value of another hour of the day, and about 31 days away at most) |
| **tariff_unavailable_hours** | hours left out of the simulation because required tariff history was unavailable, or only available further than 31 days from the hour |
| **sell_cost_unknown_hours** | hours with a raw Energi Data Service sell price whose selling costs (net and system tariff of the buy sensor) are unknown: they are counted as zero, so the sell value of those hours is too high. Shown in the notification and on the What-if tab |
| **current_assumptions** | battery values, settings, rule switches (**rules**) and controller (**control_mode**) of the current comparison |
| **assumptions** | the same for the scenario |
| **current** | historical economy with the current configuration and settings |
| **scenario** | economy with the changed values |
| **difference.profit** | scenario profit minus current profit; positive is better |
| **difference.bill** | scenario bill minus current bill; negative is better |
| **range** | only when all three capacity interval fields are supplied: entries with exactly **battery_size**, **profit**, **bill**, **difference_profit**, **difference_bill**, and **months** (`YYYY-MM` → that month's battery profit; the values sum to **profit** up to cent rounding), each compared with the current configuration |
| **module_comparison** | only when module capacity and price are supplied: same-assumption current-capacity baseline plus cumulative whole-module results, actual matched-hour coverage, incremental historical gains, separate module costs, and annualization/payback fields when data suffices |
| **coverage** | only when at least 24 hours of the requested period have no data: requested and missing hours, the hours missing before the first simulated hour and inside the period (**gap_hours**), the entity whose history starts last (**limited_by**) and the requested/simulated hours per month |
| **estimate** | the rough estimate of the whole requested period (see below), or empty when there is too little data |

Both **current** and **scenario** contain the net result with and without the battery, expected profit,
charged grid/solar energy, energy delivered to the house, sales, wear, the final battery level and the
profit, net result with the battery (**bill**) and simulated hours per calendar month (**months**). The
change of the battery level within a month is valued like the energy left at the end, so the months add
up to the profit and the net result. The items of the net result with the battery add up to **bill**:
what is bought, minus what is sold, plus wear, minus the value of the energy left (**energy_value**), plus the
value of the energy at the start (**start_value**). The column without a battery has neither. Because the
net result without a battery is the same for every setup, the difference in net result is the opposite
of the difference in profit.

## Less history than requested

When less history than requested is available, for example 65 of 100 days or 200 of 365 days, the
results above are always only the reliable figures: they are calculated from the simulated hours
alone. The notification then also says how many days are missing, which entity has no history
further back (and from which date its data starts), and how many hours inside the period are missing
because of gaps or missing tariffs.

Below the reliable results, a separate, clearly marked section estimates the net result after purchases
and sales (with the battery) of the whole requested period for both columns: the reliable net result,
an estimate for the missing days and the total, and the improvement of the scenario (a lower net result
is better). Each missing hour gets the average net result per hour of the same calendar month (any year) when
that month has at least 7 days of simulated data (or a quarter of a shorter requested period); otherwise
it gets the average of the nearest calendar month with enough data, and the notification lists those
months. The estimate is only shown when at least half of the requested hours were simulated; otherwise
the notification says that there is too little data. Season, prices and consumption of the missing days
are unknown, so the estimate is a rough approximation and not an expected saving.
A negative net result is not necessarily a cash credit: the calculation includes an estimated value for
energy remaining in the battery, but excludes the battery purchase price and any fixed fees.
A kWh in the battery is valued at the average buy price of the period, less wear and **min_profit_per_kwh**
(and the discharge loss): energy is only worth what it would earn when it is used or sold later, with at
least the minimum profit per kWh. The energy in the battery at the start is a cost of the net result with
the battery, valued the same way, so a battery that starts with more energy does not get it for free; the
profit is the same either way.

This is a hindsight calculation, not a forecast or guarantee: the planner knows the actual consumption
and solar production of the planned hours, so treat the result as an optimistic historical benchmark
rather than expected savings or battery payback. It uses Home Assistant Recorder's
long-term statistics first and fills missing hourly values only from fully covered, time-weighted raw
Recorder state history for the same configured entities. Unknown/unavailable state periods are never
filled or assumed. Hours without data for every required entity are skipped, so a gap does not discard
older valid history. The calculation treats each continuous section separated by a gap independently:
the first section uses the selected starting level and later sections restart at the battery's minimum
level rather than carrying an unknown state of charge across missing data. If both Strømligning and
Energi Data Service are configured, each hour uses Strømligning's price history first and falls back
to Energi Data Service for hours where Strømligning has no history, the same priority as the planner.
Buy and sell prices for an hour always come from the same provider, and the result reports how many
hours each provider supplied. The
simulator does not use BattMind's offline price database or assume that the buy price was earned when
selling.

Historical sell prices use BattMind's normal sell-price formula (the tariffs in the price, and the
selling costs: sell fee plus Energinet's net and system tariff), with the recorded tax/tariff
components. Energi Data Service tariff schedules are read from timestamped
Recorder attributes; Strømligning tariff sensors use the recorded hourly value. When a tariff sample is
missing at the exact simulation hour, the sample of the same local time of day on the nearest day is used
(the tariff depends on the time of day, so a value of another hour is never used; the earlier day wins a
tie), and the notification reports the number of estimated hours. A sample more than 31 days from the hour
is not used: the hour then counts as having no tariffs (see below). Energi Data Service does not save its tariff
attribute in the Recorder, so a schedule is only available if another Recorder setup kept it. A sell
sensor without a tariff attribute (tariffs disabled in Energi Data Service, a raw price) needs no
tariffs, the same as in the live plan; BattMind checks this from the sensor's current state. Its
selling costs use the net and system tariff of the buy sensor's current state for every hour; when
the buy sensor has no such tariffs the costs are unknown, counted as zero (the sell value is then too high)
and reported as **sell_cost_unknown_hours** in the notification and on the What-if tab. If the
tariffs of an hour are still unknown, the hour uses the next price provider with prices and tariffs.
If no provider has them, the hour is left out like missing data: there are no purchases or sales in
that hour, rather than purchases without the matching sales.

The live planner plans again every few minutes from the measured battery level; the replay plans once
per published price day, which gives the same plan as long as the forecast is right (it is, in the
replay). A year of history means a few hundred planner runs per column, so the simulation can take a
few minutes; the progress notification is shown meanwhile.
The displayed day count is based on hourly samples, not calendar dates. Without a configured historical
sell-price entity, variable export revenue is treated as zero (a fixed configured solar sell price is
still used). Home Assistant may provide fewer days than requested if older statistics are unavailable;
see [Less history than requested](#less-history-than-requested).
