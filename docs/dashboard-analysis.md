# Dashboard and analysis

With **Dashboard in the sidebar** on (default), each BattMind instance gets its own dashboard, e.g.
`/battmind-dashboard`. It is built on every startup from your setup: cards for price providers and a solar
forecast you don't use are removed. You choose the **Sidebar icon** of each instance when you add it (default
`mdi:home-battery`) and can change it under **Configure → General**.

It needs these HACS frontend cards: [ApexCharts Card](https://github.com/RomRider/apexcharts-card),
[Mushroom](https://github.com/piitaya/lovelace-mushroom),
[Config Template Card](https://github.com/iantrich/config-template-card),
[Timer Bar Card](https://github.com/rianadon/timer-bar-card) and
[card-mod](https://github.com/thomasloven/lovelace-card-mod) (only for styling). If Home Assistant can't find
some of them, a persistent notification lists the missing cards with links to them, see
[the cards are missing](faq-troubleshooting.md#the-dashboard-shows-errors-or-a-notification-says-cards-are-missing).
With card-mod, tables on the overview and analysis pages have no visible row or column borders, and every other table row is slightly shaded, starting with the first
row (not in a table with a single row), also inside a group of hours.

## Overview

| Card | Shows |
|---|---|
| **Battery level forecast** | the forecast battery level for the plan, with the planned 🟢 charging, 🟡 discharging, 🔴 stop (solar only) and forced discharge |
| **Electricity prices** | the buy price incl. taxes, the sell price, the price forecast and the solar forecast (drawn only for Solcast). The title shows 🔵 lowest, 🟢 average and 🟣 highest price. A warning with a restart button appears if the price integration is down |
| **Current rule and action** | `sensor.<prefix>_current_charging_rule` and the action, incl. the battery's power right now |
| **Charging history** | each charge and discharge with its rule, kWh and price, and totals per month |
| **Overview** | the key figures of the period, the plan per day (the hours of the day as squares, and a row per stretch of hours with its reason and the battery level; click a row to see the numbers), the full bill of the period (with the card_mod add-on installed, each day starts with a line) without/with the battery (with [Cable Juice Planner](cookbook.md#together-with-cable-juice-planner-ev) also the cars' planned charging and the bill incl. the cars), and the solar forecast per day, with a sentence about the sun for today and tomorrow (see [The sun in words](#the-sun-in-words)) |
| **Status** | above the emoji explanation: the battery level and power, house consumption, solar production and kWh charged by solar, the current buy price and `sensor.<prefix>_analysis` (savings this month). Rows whose entity is not configured are left out |

| Battery level forecast | Electricity prices |
| --- | --- |
| ![Battery level forecast with the planned charging and discharging](../images/home-battery-soc-forecast.png) | ![Electricity prices with sell price, price forecast and solar forecast](../images/electricity-prices-per-hour.png) |

At the top: **Force planning** recalculates the plan now. The sidebar has the emoji explanation – see [Emojis](emojis.md).

### The sun in words

With solar configured and *Solar surplus charging* on, the overview has one sentence per day for today and tomorrow, below
the table of the expected solar surplus. It is text only: it says what the plan already counts on and never changes it. For
example: *Today: the sun gives about 21.0 kWh of surplus between 09:00 and 16:00. The battery is full (100 %) by 12:00. About
12.0 kWh of surplus after that does not fit in the battery.*

| Part | Where it comes from |
|---|---|
| The sun's surplus and its hours | the plan's forecast of the solar surplus per hour (what is left after the house), the hours with at least 0.1 kWh. For today only the hours that are left |
| *The battery is full (x %) by …* | the first sun hour whose battery level in the plan, at the end of the hour, is at the maximum level of the solar settings. The level is the plan's, so grid charging counts too |
| *The battery reaches about x % by …* | the plan's level at the end of the last sun hour, when the battery is not full |
| *The battery is already full when the sun comes* | the plan's level at the end of the hour before the first sun hour is at the maximum: there is no room for any of the surplus |
| *About x kWh of surplus after that does not fit in the battery* | the forecast surplus of the sun hours after the full hour, from 0.5 kWh |
| *Solar charging is held back …* | the hours the plan holds solar charging back to sell the surplus at a higher price: [Optimise solar charging](rules/solar.md), or the whole period optimisation. Whether the battery is filled later is not said |

The surplus is a forecast, so the sentence says *about*. The text does not say where the surplus that does not fit goes: it may be
sold to the grid, but a car charging with it (Cable Juice Planner) or a limit on the inverter is possible too, and a planned sale in a
sun hour lets the sun fill the battery again. The kWh in the sentence are the forecast of the hours that are left (for today) and
are not capped to the room in the battery, unlike the table above it, which is capped and counts the whole day. A day gets no sentence when there is no sun hour left, when solar
charging is off in the plan, or when the forecast does not cover all 24 hours of the day (an hour of a value would be a guess).

## Settings

All rules, grouped, each group with a **Read more**:

| Group | Contents |
|---|---|
| Control | [whole period optimisation](rules/optimizer.md), forecast type, [choose the forecast type automatically](#forecast-accuracy), [correct the price forecast](how-it-works.md#prices) |
| Profit requirements | [min. profit per kWh, min. total profit, min. kWh for export, excluded sell hours](rules/selling.md#profit-requirements) |
| Charging from the grid | [cheapest periods, fill up, most expensive periods, required before max level, negative prices, full charge (recommended full charge every, last full charge)](rules/charging.md) |
| Discharging | [profit only, prioritise by energy cost, before the sun, max. age of kWh](rules/discharging.md) |
| Selling | [maximise profit, sell excess kWh, use midnight battery level, buy to sell](rules/selling.md) |
| Solar | [solar surplus charging, optimise solar charging](rules/solar.md) |
| Test my automation | [a switch that publishes each action in turn and a table of which ones the battery follows](actions.md#test-your-automation) |

The plan is recalculated 5 seconds after a change.

## Analysis

What the battery earns, and how well BattMind plans. Everything is published on `sensor.<prefix>_analysis`.

The first time, the energy per hour of the last year is read from Home Assistant's **long-term
statistics** (the sensors need a `state_class`). Forecast errors, plans and scores only exist from the
first start, so those sections fill up over the first days.

The hourly energy BattMind records itself is the time weighted mean of the sensors: every state counts until
the sensor writes the next one. A battery that blinks between 0 and 5 kW for a few seconds on a full battery
is counted for those seconds only, not as an hour of charging. Days that were not measured this way are checked
against the long-term statistics, where the mean of an hour is the net power (charging and discharging in the
same hour count as their difference). Only a day with statistics of the consumption, the battery power, the solar
production and the ignored consumers for every hour is checked. When its charged or discharged energy differs by
more than 0.25 kWh, the sums of energy are replaced and the score of the day is removed: its hourly values are
gone and the statistics have no sell prices, so it cannot be scored again, and a wrong score is not shown. Other
days stay as they were recorded. The last two days still have their hourly values: those are corrected hour by
hour (from the hours that have statistics) and yesterday is scored again.
The hourly charging history of the last 48 hours is measured from the sensors' states in the same way, once
(its rules, its buy and sell price and the price of the battery energy of the hour stay as recorded, an hour that
cannot be measured is left, an hour that has no energy any more is removed, and the rules table of that day is
made again from the hours). Older charging history, the economy per month
for the older hours and the consumption history that forecasts are made from are not changed by this.

### Economy per month

From the charging history:

| Column | Meaning |
|---|---|
| Charged (☀️) | kWh into the battery, in brackets the part from solar |
| Charge price | what a kWh in the battery cost: grid at the buy price, solar at the sell price it would otherwise have earned |
| Discharged | kWh out of the battery |
| Value | what a discharged kWh was worth: the energy the house didn't buy, plus sales |
| Margin/kWh | value minus charge price – the margin per kWh before loss and wear. The bigger, the better the battery moves energy from cheap to expensive hours |
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

#### Charging power by battery level

A battery charges more slowly when it is nearly full, so the full *Max. charging power* is not reached all the way
to 100 %. BattMind measures this from the same data as the efficiency: every measurement also gives the average AC
power at a battery level, kept per band of 10 % (`efficiency_curves.taper` in the `analysis` attribute of
`sensor.battmind_analysis`: the bands, the power, the number of measurements and days, and the caps in use).

- **Careful on purpose.** Only measurements from hours that proved a grid charge count (the plan asked for it for at
  least 3/4 of the hour and the battery took more than the sun could have given, see the plan check): there the battery
  sets the power. A charge by the sun is as slow as the sun is, and would teach a taper that is not there.
  A band's power is the 90th percentile of its measurements, and it needs at least 6 measurements from 3 different
  days. A cap is only learned when the battery clearly charged faster in another band (below 90 % of the best
  band's power): a battery that is only ever charged slowly keeps the rate you set. A cap is never above that rate.
- **How it is used.** An hour is walked through the bands: the battery charges at the power of the band its
  level is in, so an hour that starts at 85 % takes less than one that starts at 50 %. The optimisation plans within
  that from the start, the level forecast and the solar charging use it, and the rules' charge hours that ask more
  than the battery takes are cut to it (the reason of the charge says so). The plan that blocks solar charging to sell
  the sun does not count on the sun refilling the battery faster than the slowest band takes. Nothing changes without
  measurements.
- **It takes a few days.** The history of Home Assistant's recorder has no plan to tell grid charges from solar
  charges, so it is not read: the taper is learned from the grid charges BattMind plans from now on. A cap stays until
  newer measurements replace it (the newest 200 of each band are kept).
- **Where the plan is cut.** The rules' charge hours are cut to what the battery takes; a sale that was paired with a
  charge that was cut keeps its planned kWh until the next plan (it is made again every interval from the real level).
- **Settings → Control → Learn charging power by battery level** decides whether the plan counts on it. The
  measuring goes on when it is off. The [what-if simulator](what-if-simulator.md) does not use it.

#### Measured capacity

Below the efficiency table, **Measured capacity** shows what the battery's power sensor saw per 100 % of battery level,
next to the configured battery size (`battery_capacity` in the `analysis` attribute of `sensor.battmind_analysis`). It is
always measured. Whether the plan counts on it is decided by a switch (below); the efficiency curves, the taper and the
measuring itself always use the configured size.

- **Two bounds, not one number.** The power sensor gives the energy that went through it, and the efficiency is not known
  exactly. Per 100 % of level, the energy that went in while charging is the capacity ÷ the charging efficiency, so it is
  an *upper* bound of the usable capacity (the inverter's own use pushes it up too). The energy that came out while
  discharging is the capacity × the discharging efficiency, so it is a *lower* bound. They are never blended. Their
  ratio, discharging ÷ charging, is the round trip of the runs; it is not the same figure as the measured loss above,
  which comes from whole days and includes what the battery uses while idle.
- **What counts.** A run is an unbroken stretch of the same measurements as the efficiency (see above) in one direction
  that covers at least 40 points of battery level. Both ends are the times the level changed, so the rounding of the
  level is the same at both ends and cancels. A run ends when a measurement is skipped, the direction changes, there is a
  gap, or the level jumps by more than 10 points in one step (a calibration of the battery; the jump itself is in no
  run). A run without a new measurement for 2 hours is closed. A bound is shown from 3 runs per direction (the median;
  the newest 20 are kept). BattMind measures what its recorded buffers of the battery power and level hold (the last 50
  hours, filled from the recorder when the integration starts); the longer recorder history that the efficiency reads the
  first time is not used for this. Another power sensor, level sensor or sign starts the measuring again.
- **What it says.** It only suggests, because it assumes that the battery level follows the energy in the battery evenly
  (a battery management system that recalibrates, or a battery whose level is not linear, moves the figures) and the
  measurements allow a little energy the other way and idle time. If a charging bound is more than 5 % below the
  configured capacity, the usable capacity looks smaller than configured (*smaller than configured*); if a discharging
  bound is more than 5 % above it, it looks larger (*larger than configured*). If more energy comes out than goes in
  per point, that points to a measurement problem: the sensor may measure the two directions differently, or the level does not follow
  the energy evenly (*check the sensor*). Otherwise the measurements do not contradict the configured capacity.
- **Settings → Control → Use measured battery capacity** (off by default; the switch is also above the table) decides
  whether the plan counts on the measured capacity. On, and only when the measurements say *smaller than configured* (the
  charging bound is more than 5 % below the configured capacity, with runs in both directions: at least 3 each), the plan
  uses the charging bound rounded down to 0.1 kWh instead of the configured battery size: for the conversion between
  battery level and kWh, the optimiser, the rules, the storm floor, the cost of what the battery holds, the measured loss
  and the cycles. It is never more than the configured size, and a bound under half of it is not believed (then the
  configured size is used, and **Used now** says *configured*). **Used now** shows what the plan counts on and why:
  *configured* (the switch is off, the measurements do not say smaller, or the bound is not believed), *measured*, or
  *configured: a measured efficiency curve is in use*. In that last case the configured size stays: the measured curve was
  measured with it, so it already follows the battery in the energy the plan converts, and changing the size as well
  would correct twice (a charging curve is capped at 100 %, so what the capped part would have corrected is not
  corrected: the plan then keeps more loss than measured, never less). With configured curves or the fixed round-trip
  loss the size is the only thing that can follow the measurements. The size is part of what makes the plan replan, and
  the card follows it after the next plan. The [what-if simulator](what-if-simulator.md) uses the configured (or
  scenario) size, and the hindsight score the size the plan counted on when the day was scored.
- **Limits.** The measurements give a bound, not the capacity, and assume that the level follows the energy evenly. The
  charging bound is an upper limit of what the battery holds, so the plan is nearer the runs than with the configured
  size and normally still not past them; with a very good configured efficiency (about 98 % or better) or a discharging
  efficiency below the measured round trip it can land a little on the pessimistic side (more loss than measured). The
  charging history keeps the energy in kWh and the level it is worth is calculated from it with the size in use, so the
  cost and the age of the energy in the battery follow a change of the size at once. A changed configured battery size,
  power sensor, level sensor or sign starts the measuring again. Check *Used now* and the plan after turning the switch on; turn it off to go back to the configured
  size at once.

<details>
<summary>Screenshot</summary>

<p align="center"><img src="../images/battery-efficiency.png" alt="Charging and discharging efficiency by power" width="760"></p>

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

`score = saved ÷ possible`

- **Possible** is what the battery could have saved that day: the cost without the battery minus the best
  possible cost, for a battery that ends the day at the level it started.
- **Saved** is possible minus what the plan lost: the actual cost minus the best possible cost for a plan that
  nets the same battery energy as the day did (by the efficiency model; the measured end level is only a check
  that the day's energy adds up).

So energy left in the battery for tomorrow, or taken from what was charged yesterday, is neither a gain nor a
loss, and a plan that is the best possible one scores 100 % whatever level the day ends at.

The best possible cost is calculated afterwards with the day's actual consumption, solar and prices, the same
power limits, wear and profit requirements (*min. profit per kWh*, *min. total profit*, *min. kWh to sell*), and
the efficiency at each hour's charge/discharge power (when the efficiency curves are used). A trade that would not
have met the requirements is not counted as a possible saving. Big moves are whole steps of about 0.05 kWh of
battery level (up to 0.2 kWh for the biggest batteries); in every hour the best plan can also cover the whole house load or store the whole solar surplus
exactly, so a small load or surplus is neither lost nor invented. Forecast errors still count against the score –
100 % would require knowing the future. A day whose possible saving is no more than the rounding of
the model (a few level steps at the day's highest price) shows – instead of a score; the 30 day figure still
adds those days' saved and possible amounts.

What is left out of the score:

- **Ignored consumers and Cable Juice Planner chargers** (also a charger on its own meter). What they took from
  the solar surplus and then from the battery is taken out of the day's solar and discharge, so the battery
  charging a car is not counted as a sale to the grid, and the best possible plan is not asked to keep that
  energy either.
- **The battery's standby consumption** (many batteries use tens of watts around the clock, which the battery
  power sensor shows as 0 W) and the efficiency model's own error. The best possible plan has to net the same
  battery energy as the day did, by the same efficiencies, so a battery that lost 1 kWh in standby is not blamed
  for it.

Only days that can be trusted are scored. A day has no score when an hour was not recorded, the house
consumption or battery power sensor was lost for a large part of an hour, the buy or sell price of an hour is not
a real price (a forecast, an estimate from the price history or an offline price), the battery level at the start or end was
not measured (never the 100 % fail-safe), the day's energy does not add up (more than 30 % of the usable battery
off, for example a jump of the level, or an ignored consumer took more battery energy than the battery has room
to refill), the battery cannot be modelled, the hours were recorded by an older version, or it is the day the
clocks go back (25 hours). The hourly price is the
mean of its price periods, and only when every period has a real price. The battery level is read from the
sensor itself, never held over from before it was lost. Days from the long-term statistics have no
hourly prices and are not scored. Below the table the dashboard lists the days of the last 30 without a score and
why.

When the calculation of the score changes, a day scored by an older version keeps its stored figures: it is shown
in the table with 🔄, by the formula of that version (version 1–2: saved is the cost without the battery minus the
actual cost, possible is the cost without the battery minus the best possible cost). Those days cannot be compared
with the newer ones, so they are not part of the 30 day figure, and they are not listed as days without a score. A
day whose hours were recorded by an older version has no stored score and is still listed as one.

A day that starts with a full battery and has no cheap hour left to charge in has little possible saving of its
own: its use of yesterday's energy is not counted as a saving that day. The score of a single day can therefore
jump; the 30 day figure adds saved and possible over all days.

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
**Forecast integration** the solar forecast integration you selected (Solcast, Forecast.Solar or Helios Forecast).
**Bias** > 0: too much sun was expected.

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

The last successful calculation for each BattMind instance is
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
