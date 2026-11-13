# Dashboard and analysis

With **Dashboard in the sidebar** on (default), each BattMind instance gets its own dashboard, e.g.
`/battmind-dashboard`. It is built on every startup from your setup: cards for price providers and a solar
forecast you don't use are removed. You choose the **Sidebar icon** of each instance when you add it (default
`mdi:home-battery`) and can change it under **Configure → General**.

It needs these HACS frontend cards: [ApexCharts Card](https://github.com/RomRider/apexcharts-card),
[Mushroom](https://github.com/piitaya/lovelace-mushroom),
[Config Template Card](https://github.com/iantrich/config-template-card) and
[card-mod](https://github.com/thomasloven/lovelace-card-mod).
With card-mod, tables on the overview and analysis pages have no visible row or column borders, and every other table row is slightly shaded, starting with the first
row (not in a table with a single row), also inside a group of hours.

## Overview

| Card | Shows |
|---|---|
| **Battery level forecast** | the forecast battery level for the plan, with the planned 🟢 charging, 🟡 discharging, 🔴 stop (solar only) and forced discharge |
| **Electricity prices** | the buy price incl. taxes, the sell price, the price forecast and the solar forecast. The title shows 🔵 lowest, 🟢 average and 🟣 highest price. A warning with a restart button appears if the price integration is down |
| **Current rule and action** | `sensor.<prefix>_current_charging_rule` and the action, incl. the battery's power right now |
| **Charging history** | each charge and discharge with its rule, kWh and price, and totals per month |
| **Overview** | the key figures of the period, the plan per day (the hours of the day as squares, and a row per stretch of hours with its reason and the battery level; click a row to see the numbers), the full bill of the period (with the card_mod add-on installed, each day starts with a line) without/with the battery (with [Cable Juice Planner](cookbook.md#together-with-cable-juice-planner-ev) also the cars' planned charging and the bill incl. the cars), and the solar forecast per day |
| **Status** | above the emoji explanation: the battery level and power, house consumption, solar production and kWh charged by solar, the current buy price and `sensor.<prefix>_analysis` (savings this month). Rows whose entity is not configured are left out |

| Battery level forecast | Electricity prices |
| --- | --- |
| ![Battery level forecast with the planned charging and discharging](../images/home-battery-soc-forecast.png) | ![Electricity prices with sell price, price forecast and solar forecast](../images/electricity-prices-per-hour.png) |

At the top: **Force planning** recalculates the plan now. The sidebar has the emoji explanation – see [Emojis](emojis.md).

## Settings

All rules, grouped, each group with a **Read more**:

| Group | Contents |
|---|---|
| Control | [whole period optimisation](rules/optimizer.md), forecast type, [choose the forecast type automatically](#forecast-accuracy), [correct the price forecast](how-it-works.md#prices) |
| Profit requirements | [min. profit per kWh, min. total profit, min. kWh for export, excluded sell hours](rules/selling.md#profit-requirements) |
| Charging from the grid | [cheapest periods, fill up, most expensive periods, required before max level, negative prices](rules/charging.md) |
| Discharging | [profit only, prioritise by energy cost, before the sun, max. age of kWh](rules/discharging.md) |
| Selling | [maximise profit, sell excess kWh, use midnight battery level, buy to sell](rules/selling.md) |
| Solar | [solar surplus charging, optimise solar charging](rules/solar.md) |

The plan is recalculated 5 seconds after a change.

## Analysis

What the battery earns, and how well BattMind plans. Everything is published on `sensor.<prefix>_analysis`.

The first time, the energy per hour of the last year is read from Home Assistant's **long-term
statistics** (the sensors need a `state_class`). Forecast errors, plans and scores only exist from the
first start, so those sections fill up over the first days.

### Economy per month

From the charging history:

| Column | Meaning |
|---|---|
| Charged (☀️) | kWh into the battery, in brackets the part from solar |
| Charge price | what a kWh in the battery cost: grid at the buy price, solar at the sell price it would otherwise have earned |
| Discharged | kWh out of the battery |
| Value | what a discharged kWh was worth: the energy the house didn't buy, plus sales |
| Spread | value minus charge price – the margin per kWh before loss and wear. The bigger, the better the battery moves energy from cheap to expensive hours |
| Avg. price | the month's average buy price over all hours |
| Savings | what the battery saved, after loss and wear |

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/economy-per-month.png" alt="Economy per month" width="760"></p>

</details>

### Per rule (12 months)

Charged and discharged kWh per rule. For charging, **est. gain** is the charged kWh after loss, valued
like the battery's discharges on average, minus their price and wear. For discharging, it is the actual
savings. Both tables show the same money from two sides – don't add them up.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/per-rule-12-months.png" alt="Charged and discharged kWh per rule over 12 months" width="380"></p>

</details>

### Cycles and wear

One cycle = discharged kWh equal to the battery size. **Wear** = wear per kWh × charged kWh. Use it to
check the wear you set against the battery's guarantee (e.g. 6000 cycles).

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/cycles-and-wear.png" alt="Cycles, wear and savings per month" width="760"></p>

</details>

### Measured round trip loss

`1 − discharged ÷ (charged − change of the battery level ÷ charging efficiency)`, measured with the
battery's power sensor (AC to AC), per month, the last 30 days and in total. The change of the battery level
is counted as the AC energy it took to charge it, so a day that empties the battery does not look too lossy
and a day that fills it does not look too efficient.

The loss is added up from whole days that charged at least 20 % of the battery size. With at least 10 such
days, a day whose loss is more than 3 scaled median absolute deviations from the median of all days is left
out as an outlier – typically a jump of the battery level (recalibration) or a day the battery hardly ran,
where its standby consumption dominates. The month table shows how many days were left out (**Outliers**).

**Used now** is the loss BattMind plans with: the configured one, or the measured one after 6 full cycles if
*loss from history* is on. When efficiency curves are configured, **Used now** is their combined loss at the
configured maximum charge/discharge power.

If the measured loss is far from the configured one, check the battery power sensor (and its sign) before
changing the loss.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/measured-round-trip-loss.png" alt="Measured round trip loss per month against the configured loss" width="760"></p>

</details>

### Battery efficiency

BattMind measures the charging and discharging efficiency at each AC power itself. Every hour it goes
through the battery power and battery level since the last time: between two changes of the battery
level the battery moved exactly the difference of the two levels, so the AC energy of the power sensor
in that time is compared with it (charging: battery kWh ÷ AC kWh, discharging: AC kWh ÷ battery kWh).
A measurement covers at least 1 % battery level and 2 minutes; it is skipped when the battery was idle
for more than 10 % of the time, ran the other way for more than 3 % of the energy, or the power sensor
was unavailable. The measurements are added up per 500 W step of AC power; consecutive measurements in
the same step share their boundaries, so the rounding of the battery level cancels out. A step is used
once it has at least 20 measurements and 2 kWh (efficiencies above 100 % are capped at 100 %, below
50 % are not used). Beyond 100 kWh in a step the older measurements count half, so the curve follows
an ageing battery.

The first time, BattMind reads the battery power and level from the Home Assistant recorder history
(up to 30 days, as far as the recorder keeps it; one day at a time in the background), so the curve
does not have to wait for new data. Only the history before the hourly measuring started is added,
so nothing is counted twice. BattMind's own charging history cannot be used for this: it stores the
energy per hour, and its battery percentages are calculated from that energy, not measured. The
long-term statistics only have hourly means, which cannot show when the battery level changed.

The solid lines are the curves BattMind plans with: a configured curve, otherwise the measured one. The
dashed lines show every measured step, including the ones without enough measurements yet. The table
below says per direction which curve is used, how many steps are in use and how much energy has been
measured. Without a curve the round-trip loss is used.

The configured battery size converts the battery level to kWh. If it is wrong, the charging and
discharging efficiency move in opposite directions; their product, the round trip, stays right.

BattMind interpolates linearly between the points and uses the nearest endpoint outside the range, just
as the whole-period optimiser does. **Enhance battery efficiency** under **Settings → Control** decides
whether the curves are used in calculations. Turning it off keeps the graph, the saved curves and the
measurements, but planning uses the round-trip loss.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/battery-efficiency.png" alt="Charging and discharging efficiency by AC power" width="760"></p>

</details>

### Self-sufficiency

Where the house's consumption came from per hour: first solar, then the battery, the rest from the grid.
**Self-sufficiency** = (solar + battery) ÷ consumption. **Export** is solar and battery energy sold.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/self-sufficiency.png" alt="Self-sufficiency per month" width="760"></p>

</details>

### Forecast accuracy

The average error per hour between the consumption the plan expected and the measured one, for each
forecast type. ✅ marks the type the plan uses, ⭐ the most accurate the last 30 days.

| Column | Meaning |
|---|---|
| Avg. kWh/hour | what each type expected per hour on average over 30 days. The 📏 **Actual** row at the top is the measured consumption |
| Error 7 / 30 days | the average error per hour in kWh |
| Relative | the error as a share of the consumption |
| Bias | > 0: the plan expected too much on average |

The error is counted hour by hour, so a type can hit the average and still miss a lot in the single
hours: the type closest to the actual average is not always ⭐. When another type is more accurate, a
warning shows the numbers behind the suggestion: both types' error, how much smaller it is, and what
each expected against the actual consumption. Choose the type under *Settings → Forecast type*.

Which type is best can change over time (e.g. into winter). With **Choose the forecast type automatically**
on (off by default; the switch is in this section and under *Settings → Control*), the plan uses the type
with the smallest error the last 30 days instead:

- it starts after 7 days of measurements; until then the selected type is used
- it starts at the selected type and only switches when another type has at least 5 % less error, so it
  does not swap back and forth between nearly equal types
- an info line under the table shows the type in use, or how many days it is still waiting for

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/forecast-accuracy.png" alt="Forecast accuracy of the consumption forecast types" width="760"></p>

</details>

### Hindsight score

How much of the possible saving was reached each day:

`score = (cost without battery − actual cost) ÷ (cost without battery − best possible cost)`

The best possible cost is calculated afterwards with the day's actual consumption, solar and prices, the
same start and end level, power, loss and wear, and the same battery model as the optimiser: the efficiency
at each hour's charge/discharge power (when the efficiency curves are used) and the profit requirements
(*min. profit per kWh*, *min. total profit*, *min. kWh to sell*). A trade that would not have met them is
not counted as a possible saving. Forecast errors still count against the score – 100 % would require
knowing the future.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/hindsight-score.png" alt="Hindsight score per day" width="380"></p>

</details>

### Planned against actual

Every evening at 23 BattMind saves what the plan wants to charge from the grid and sell the next day, and
compares it with what happened. Big differences come from forecast errors, and from plans that change
during the day when prices or the battery level change.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/planned-against-actual.png" alt="Planned grid charging and sale against actual" width="760"></p>

</details>

### Empty in the expensive hours

Days where the battery was empty (at most 2 % above the min. level) in one of the day's 4 most expensive
hours, and the kWh and money bought from the grid in them. Many such days suggest:
- the battery is too small for the evening, or
- the consumption forecast is too low (see **Forecast accuracy**), or
- the profit requirements are so high that BattMind doesn't charge.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/empty-in-expensive-hours.png" alt="Days with an empty battery in the most expensive hours" width="380"></p>

</details>

### Solar forecast

The solar surplus the plan expected for the next day (saved the evening before) against the measured
surplus: **Plan** is the combined forecast, **History (weather)** the part from the stored solar history,
**Solcast** the forecast integration. **Bias** > 0: too much sun was expected.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/solar-forecast.png" alt="Planned against measured solar surplus per day" width="760"></p>

</details>

### Price forecast

How well the price integration's forecast (e.g. Carnot in Energi Data Service, or Strømligning) hit the
real buy price. Every hour BattMind saves the forecast for the coming days, without its own small adder
per day ahead. When the real prices of a day are published (around 13:00 the day before), they are compared
with what the forecast said 1, 2, 3 … days before.

| Column | Meaning |
|---|---|
| Days ahead | how many days before the forecast was made. 1 = the day before, before the real prices came |
| Days | days compared the last 30 days |
| Error 7 / 30 days | the average error per hour in kr/kWh |
| Bias | > 0: the forecast was too high on average |
| Worst hour | the biggest error in a single hour |
| Cheapest found | how many of the day's 4 cheapest hours by the forecast really were among the 4 cheapest – the hours the plan charges in. In brackets the extra price per kWh of picking them by the forecast |
| Most expensive found | the same for the 4 most expensive hours – the hours the plan uses the battery in |
| Correction | what the plan adds to the forecast prices for that day ahead: minus the bias, after 7 days of measurements and at most ±0.50 kr. – while there are too few days, or when *Correct the price forecast* (*Settings → Control*) is off |

The level of the forecast matters less than the order of the hours: a forecast that is 0.30 kr too high
but finds the right cheap and expensive hours plans almost as well as the real prices. The charts show the
real price against the forecast 1 to 7 days before per hour (from 2 days ago to 4 days ahead), the
corrected forecast the plan uses for the coming hours, and the error per day for the last 31 days.

The measurements always use the forecast as the price integration published it, never the corrected one,
so the correction does not measure itself.

Only the buy price is measured, and only hours with a forecast from the price integration. Hours BattMind
fills from its own price history are not included. The first numbers come at 13:00 the day after the first
start, "3 days ahead" after 3 days.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/price-forecast.png" alt="The price forecast against the real price per day ahead" width="760"></p>

</details>

### History

The required battery level until the next charge (48 hours), kWh charged by solar (7 days) and the
consumption forecast per hour with the three forecast types.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/consumption-forecast.png" alt="Consumption forecast per hour with the three forecast types" width="100%"></p>

</details>

### What-if simulator

The **What-if** tab runs the [What-if simulator](what-if-simulator.md) from entities and standard cards
(no custom card). A short note on current versus scenario and a **Read more** explain the controls. The
controls come first: the inputs, and the scenario rules in the same sections as on the Settings tab
(Control, Profit requirements, Charging, Discharging, Selling and Solar). Under the **Results** heading
follow the status and result with the monthly chart, the module comparison, the capacity interval and the
rules table; the last three are hidden until the result has that part. The charts of a battery size,
the profit per added kWh and the payback show their values when you hover over them. A
link at the bottom opens **Settings → Developer tools → Actions** with the **battmind.what_if** action
preselected (requires an administrator) for the settings the tab does not have.

Current is an estimate assuming the current setup throughout the historical period, not the actual
historical result. Focus on the difference between current and scenario; it too is an estimate.

- **Inputs:** *What-if – History (days)*, *Battery size (kWh)*, the switch **Compare a range of battery
  sizes** with *From*, *To* and *Step*, and the switch **Compare battery modules** with *Module capacity*,
  *Price per module* and *Additional modules*. The two switches decide whether the range and the module
  entities are used; a range has at most 12 sizes. The switch **Run simulation** starts a calculation (one at a time) when it is
  turned on and requests cancellation when it is turned off, and the status entity shows *idle*, *running*, *stopping*,
  *stopped* or *failed*. Only these entities and the scenario entities below are used by a new
  calculation; they never change the planner's settings, are not part of a backup and do not trigger a
  planning run.
- **Rules and settings in the scenario:** one *current / on / off* selector for each rule switch, one
  number for each setting (reserve, minimum profit per kWh, minimum total profit, minimum kWh to sell,
  cheap price periods, rise thresholds, maximum age of kWh) and a text for the excluded selling hours.
  A selector on *current* and a number or text with no value (*unknown*) use the current setting, so only
  what you change is applied to the scenario; Current always uses the current settings. A number set to 0
  is a real value, not "current". **Reset to current** clears all of them. Rules the inverter cannot support
  are not shown, and a rule that needs an action the battery cannot run is never a scenario value.
- **Progress:** while a simulation runs, a section between the Selling section and the **Results** heading
  shows the message (loading phases, the real simulation progress and a remaining-time estimate once
  enough progress has been measured) and a bar of [Timer Bar Card](https://github.com/rianadon/timer-bar-card)
  (HACS, required for this card) that follows the *What-if progress* sensor. The sensor is 0 when nothing runs
  and 1 to 100 while it runs (1 while the history loads, because that card takes 0 as finished); the card
  reads it as 100 seconds with the percent as the time left, inverted, and shows "42 %".
- **Result:** a status line while it runs, stopping, failed or stopped, the completion time and selected
  values, the simulated period, warnings for missing hours, estimated or unavailable tariffs, unknown
  selling costs and missing sell prices, a summary of the Current and Scenario profit and their
  difference, and a table of profit and net bill. The calculated bill includes wear and the value of the
  energy remaining in the battery; battery purchase cost and fixed fees are excluded. The result is
  historical hindsight, not a forecast.
- **Months:** for periods longer than three months, a table and a bar chart show the gain per calendar
  month for Current, Scenario and the difference, so you can see when in the year it pays most. Bold marks
  the best month; months with few hours are incomplete and the figures are not a forecast. A range and a
  module comparison add the same monthly view for up to six sizes or module counts (gain versus zero extra
  modules). These charts are only shown when the last result has them (three binary sensors tell the
  dashboard).
- **Charts:** all charts are charts of ApexCharts Card, so the values show when you hover over them. The
  profit by battery size, the profit per added kWh and the payback charts do not have a time axis: BattMind
  places each battery size (or year) on one day of the chart and replaces the axis and tooltip labels, so
  the chart shows sizes and years. Currency is shown with two decimals and kWh with one decimal on the whole
  tab. The line patterns tell the payback lines apart without colour.
- **Range:** the table shows each simulated size with its profit, bill, the change in profit from the
  previous size and the change per added kWh, and highlights the highest total profit and the highest
  profit per added kWh (including ties); these are historical comparisons, not a recommendation or a
  measure of investment return. The profit chart plots the profit of each size; each point of the
  per-kWh chart is the average of the interval named under it, not the profit of a battery size. The
  colours are relative to the shown values, red for the lowest and green for the highest (green can be
  negative): the profit line is coloured segment by segment by the profit per added kWh of that segment,
  the per-kWh line and its dots by their value. A note under the chart explains the colours; there are no
  colours (a plain line) when the values are all equal or one is missing. Values
  between points are estimates. The per-kWh figures use rounded profits and can be imprecise for very
  small steps.
- **Rules table:** the rules and values used by Current and the scenario, taken from the completed
  simulation (not from the live settings). Enabled rules are permitted but do not necessarily execute; a
  dash marks an inactive rule or a zero-capacity case. Older saved results without rule values must be run
  again. The table shows the configured control mode; an optimizer that does not converge can fall back
  to rules for individual planning windows.

The optional module comparison shows historical gains alongside annual estimates and simple payback when
sufficient history and selling data are available. Missing hours can be estimated from the same or
nearest reference months; missing seasons increase uncertainty. It answers two different questions, and
the section keeps them apart:

- **What does the last added module earn on its own?** This is what matters when deciding whether to
  buy more: the module's price divided by the extra yearly gain it adds on top of the previous size
  ("module alone" columns of the table, and the last-module time in the graph legend).
- **What does the whole battery earn against no battery at all?** The current battery plus the added
  modules, priced together, against having no home battery ("whole battery" columns, and the total time in
  the graph legend). This time is an average of the current battery and the added modules, so it is
  shorter than the module-alone time only when the current battery pays back faster than the module does
  (the summary says so only then). It only answers how long the whole battery would take to pay back if
  bought today.

A short plain-language summary fills in the numbers of the run: the current battery's price, yearly
saving and payback; what the first extra module costs, earns and pays back on its own; and the whole
battery's payback with a reminder to use the module-alone time when considering buying more. A sentence
is left out unless every number in it is known (never replaced by 0). The table has one row per battery
size, with the historical gain from the previous size, then the module-alone columns (extra yearly gain,
module price, simple payback) and the whole-battery columns (total price, saving per year against no
battery, payback). The whole-battery columns are "—" when the current battery's price or the yearly bills
are unknown. The current battery is priced at the same price per kWh as a module.

The payback chart shows the accumulated cost over time at a constant yearly bill. A grey dashed line is
without a battery: it starts at 0 and rises with the whole estimated yearly bill. The current battery and
each extra module count have one line each: it starts at the purchase price (the current battery plus the
added modules) and rises with the yearly bill with that setup. The legend names each line with its battery
size in kWh: the current battery has one time; each extra module count has the whole battery's time
("total", where its line crosses the grey line) and the last module on its own (where its line crosses the
line before it, the table's simple payback; with a strict one-year window the chart can be slightly
longer), both counted from the start. The chart shows at most 30 years. It is not drawn when the current
battery's price or the bill without a battery is unknown, or when the module price is 0 (a missing price
is never treated as 0); without a current battery only the grey line and the extra module lines are drawn.
These are historical estimates, not predictions, and exclude installation and other investment costs.

A range takes longer and can delay planning. The last successful calculation for each BattMind instance is
saved with its completion time and selected values, shown by the **What-if** result sensor and kept
after a page refresh or Home Assistant restart, whether it was started from the tab, Developer tools or an
automation. A failed or stopped calculation does not replace the previous result, and the dashboard shows
its status and the reason instead of persistent notifications (calculations started through the action
still use persistent notifications). Cancellation can take time while an ongoing history query or
planning step finishes.

## Editing the dashboard

You can edit the dashboard like any other (**Edit**, or **⋮ → Raw configuration editor** to copy it). Once
you save your changes, your version is kept, and dashboard changes in later updates of the integration no
longer show up. Press **Reset dashboard** (on the integration's device) to throw your changes away and go
back to the generated dashboard.
