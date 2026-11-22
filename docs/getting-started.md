# Getting started

This page takes you from installation to a battery that BattMind controls. Plan about 30 minutes, and a
few days of watching the plan before you trust it.

## 1. Check what you need

| You need | Why |
|---|---|
| A **battery level** sensor (%) | where the plan starts from |
| A **battery power** sensor (W or kW) | measures what goes in and out (charging history, cost price, loss) |
| A **house consumption** sensor (W or kW) – the house's consumption, not the grid import | the consumption forecast per hour |
| [Energi Data Service](https://github.com/MTrab/energidataservice), [Strømligning](https://github.com/MTrab/stromligning) and/or [Energy Price Forecast](https://github.com/BackupBaTTerY/energypriceforecast-home-assistant) | buy and sell prices per hour |
| A way to control the inverter from Home Assistant | BattMind only decides – your automation does it |

Optional, but recommended with solar panels:

- a **solar production** sensor (W or kW)
- [Solcast PV Forecast](https://github.com/BJReplay/ha-solcast-solar)
- a weather integration with `weather.get_forecasts` (Met.no, AccuWeather, OpenWeather …)

For the dashboard you need the HACS frontend cards `apexcharts-card`, `mushroom`, `config-template-card`, `timer-bar-card`
and `card-mod`. If Home Assistant can't find some of them, a notification lists the missing ones with links to install
them (see [the cards are missing](faq-troubleshooting.md#the-dashboard-shows-errors-or-a-notification-says-cards-are-missing)).

> [!TIP]
> Before you start, find out how your inverter is controlled: which entities or services set forced charging,
> forced discharging and the max. charge/discharge power. Look under *Developer tools → Actions* and *States*
> for your inverter integration. You need them in [step 5](#5-control-the-battery).

Optional for additional battery protection: a battery temperature sensor. The inverter/BMS remains the
primary safety system.

## 2. Install

1. Open **HACS**, search for **BattMind**, install it, and restart Home Assistant.
2. **Settings → Devices & services → Add integration → BattMind**.


Not in the HACS search? Add it as a custom repository first: **HACS** → ⋮ → **Custom repositories** →
`https://github.com/dezito/BattMind-Integration` with type **Integration**, then search again.

## 3. Go through the setup

The setup is split into small pages. Every field has a help text – here are the ones that matter most.

### Prefix and language

The prefix starts every entity id: with `battmind` you get `sensor.battmind_powerwall_action`. Use one
prefix per battery. If the [PyScript version](migration.md) still runs, choose another prefix (e.g.
`battmind_ha`).

The language is used for the entity names, the dashboard, the charging rule and notifications. It can be
changed later under **Configure → General**; the entry reloads and the entity names follow (names you
renamed yourself are kept).

The optional **sidebar icon** is the icon of the dashboard in the sidebar (default `mdi:home-battery`). Pick another
one, e.g. `mdi:solar-power`, to tell several instances apart. It can be changed under **Configure → General**;
leave the field empty for the default icon.

### Home battery – sensors

- **Battery power:** BattMind expects **positive while discharging, negative while charging**. If your
  sensor is the other way round, **Invert** should be on; the [check page](#check-the-battery-and-consumption-settings)
  suggests it from the recorder.
- **Battery temperature:** optional. When selected, BattMind requests `battery_stopped` outside the safe
  range and while the sensor is unavailable. Configure **Stop battery** in the control blueprint first.

### Home battery – specifications

| Field | How to set it |
|---|---|
| Battery size | the **usable** capacity in kWh |
| Min. level | the reserve BattMind never plans below (e.g. 5–10 %) |
| Max. level | the highest level BattMind charges to (usually 100 %) |
| Charge / discharge power | the battery's max. power in W – limits how many kWh fit in an hour |
| Charge/discharge loss | the **round trip** loss as a fraction: 0.1 = 10 %. A typical lithium battery with inverter loses 8–15 % |
| Loss from history | after 6 full cycles BattMind measures the loss itself – leave it on |
| Charging / discharging efficiency curve | optional object mapping AC power in W to efficiency in percent, e.g. 1000 W = 88 %, 3000 W = 94 %, 5000 W = 96 %. BattMind interpolates between points. Leave empty to let BattMind measure the curve itself (the round-trip loss is used until there are enough measurements) |
| Enhance battery efficiency | switch under **Settings → Control** that decides whether BattMind uses the configured curves. Turning it off keeps the curves and uses the round-trip loss instead |
| Min. / max. safe temperature | limits in °C from the battery manufacturer; defaults are 0 to 50 °C |

The loss and the wear (on the page *Refund and fees*, see [prices](#prices)) decide how big a price difference has
to be before BattMind uses the battery. See [How BattMind plans](how-it-works.md#the-cost-of-a-kwh-from-the-battery).

### Home battery – controllable modes

Leave everything unselected if your inverter can run all the [actions](actions.md#the-six-actions). If it
can't run one of them, e.g. it can't block solar charging or sell from the battery, select it under
**Modes the battery can't run**. BattMind then never plans that action, and the rules that need it count as
off and are hidden on the dashboard. See [When the inverter can't run an action](actions.md#when-the-inverter-cant-run-an-action).

### Consumption

- **House consumption:** the house's own consumption. Whether the sensor includes the battery's charging or
  discharging is set on the [check page](#check-the-battery-and-consumption-settings), so it isn't counted twice.
- **Ignored consumers:** power sensors subtracted from the house, e.g. the EV charger. They aren't in the
  forecast, so the battery isn't planned to cover them. The house consumption used for the solar surplus is never below 0 W.
- **Ignore Cable Juice Planner chargers** (on by default): with [Cable Juice Planner](cookbook.md#together-with-cable-juice-planner-ev)
  installed, BattMind also subtracts the charger power of every car you have set up there, so you don't add
  them to the list yourself. The page shows the chargers it found. A car added later is picked up
  automatically (BattMind reloads once), and a charger that is also in your list is subtracted once. Turn it off if the
  house consumption sensor does not include the chargers, e.g. a separate meter: subtracting them would
  then take consumption that was never there out of the forecast.
- **A charger that hangs:** A Cable Juice Planner charger that has not reported for over 30 minutes while it still reads a number other
  than 0 (a hung integration is not *unavailable*) is taken to be stuck: it is not subtracted, in the current hour and in the
  hours since it got stuck, until it reports again, and a warning is logged. The higher consumption is the safe side. It is the
  report time that counts, not the value, so a charger that holds a steady value is fine as long as it keeps reporting; the
  other ignored sensors are always subtracted, whatever they report.

### Check the battery and consumption settings

After the sensors for the battery and the house consumption, BattMind looks at the history the recorder has for the battery and the house consumption – as far back as it takes,
up to a few weeks – and suggests three settings, with the numbers it used so you can see that they fit:

| Setting | How it is found |
|---|---|
| **Invert** (battery power sign) | an unbroken stretch of 1–3 hours in which the battery level rises while the power has one sign: that sign means charging |
| **Consumption includes charging** | at the moments the battery starts or stops charging, the house consumption steps by about the charging power (a sensor from grid and solar) or does not step (the total load of the house) |
| **Consumption includes discharging** | at the moments the battery starts or stops discharging, the consumption does not step (the total load, which the battery covers) or falls by about the discharge power (a sensor from grid and solar) |

Only the minutes around each start and stop are compared, so a load that is different in the evening than at night, or an
EV that charges in the same hours but starts and stops at other moments, does not count. One start or stop is not enough:
at least four of the latest must agree, and in practice a few days of history are needed. A consumer that starts and
stops in the same minutes as the battery and is about as big (an EV or a heat pump on the same cheap-hour schedule) can fake
one direction: the charging and the discharging must therefore tell the same story – a sensor of the total load (does not
include the charging, includes the discharging) or one from grid and solar (the opposite) – and both must be decided,
otherwise nothing is suggested. Add such a consumer under *Ignored consumers* and look again to get a suggestion. Two
such consumers, one for each direction, can still fake both – check the numbers. A sensor that includes both or
neither direction is never suggested: set it by hand. Each answer needs only a short stretch of unbroken history – a
stretch of an hour or two, or about 25 minutes around a start or stop – but it may be days or weeks back if that is where
the history is clear. A battery that sells to the grid, a load that follows the battery, or a sensor that never changes
leaves the answer open.

The brackets show what an answer rests on: **h** = hours of unbroken data, **n** = the number of starts and stops
compared, and how far back it was found (for example `n=4, -2 d`). ✅ means on, ⬜ off and ❔ that the history could not
decide it: the current value is then kept, never a guess. Everything can be changed, and nothing is saved before you
continue. The suggestions are only made when you have chosen or changed one of the three sensors in the flow, so a
restored backup, an import from PyScript and the stored settings keep their values; tick *Detect again* to look again
(the page is the last one of *Home battery* and of *House consumption* under *Configure*). The numbers are only suggestions – check that they fit before you continue.

The consumption numbers are read after the consumers that the forecast itself leaves out are subtracted (the ignored ones and, when the switch above is on, the chargers of Cable Juice Planner), so
the page compares what the planner works with. If the page shows a message instead of numbers (no recorder, the reading took
too long, a power sensor with an unknown unit, no usable history), the current values are kept; tick *Detect again* to try
again.

### Prices

Choose Energi Data Service, Strømligning and/or Energy Price Forecast. With several, the priority is Strømligning,
then Energi Data Service, then Energy Price Forecast: the price of the first one that has one is used for every hour,
and the next fills the gaps (also in the what-if and the analysis). All price sensors must be in the same currency
(a backup provider in another currency is refused).

- **Energi Data Service:** enable *Carnot* in Energi Data Service to plan beyond the published prices.
  Without a separate sell sensor, the buy price minus tariffs is used as sell price. A sell sensor
  without tariffs is used as the spot price; the net and system tariff paid when selling then come
  from the buy sensor.
- **Energy Price Forecast** (for prices outside Denmark): choose its retail price sensor (*Current retail price*) as
  the buy price – it gives the published prices of today and tomorrow and the forecast after them. It only exists when
  retail pricing is turned on in Energy Price Forecast: its estimate (Germany, the Netherlands, Denmark, Austria and
  Norway) or your own formula (every market). In a market where Energy Price Forecast publishes no prices, only a
  forecast (Finland, Great Britain, SE1/SE2 and others), set a Nord Pool or ENTSO-e sensor as its own price source
  and use its own retail formula: the page refuses a buy or sell sensor with only a forecast for today, and an existing setup gets a repair when one has had only a forecast for at least an hour. Choose its wholesale price sensor
  (*Price forecast series*) as the sell
  price (required: the retail price is higher than what a sale pays, so it is refused as a sell sensor; an entry
  restored from a backup without one sells at the buy price less the sell fee). The sensors must give the
  price per kWh in the currency (EUR/kWh, GBP/kWh, …), not in cents or pence: BattMind refuses a sensor in
  ct/kWh or p/kWh. A sensor in EUR/MWh (a wholesale sensor often is) is converted to kWh, also in the recorded history (read in the unit the sensor has now: if you change its unit, the old history is off by 1000; a sensor that is missing from Home Assistant has its history left out).
- **Strømligning:** the buy sensors are required. The sell sensors and the tariff page give an accurate
  sell price: Strømligning's sell price includes every tariff, so the distribution (transport) tariff,
  net tariff, system tariff, electricity tax and surcharge are all subtracted. The distribution sensor
  only has today's tariffs; other days use the same time of day from the latest known day.
  Strømligning switches its sensors excl. VAT (which the sell sensors and the tariffs use) off by default,
  and a disabled sensor can't be selected: enable them first under **Settings → Devices & services →
  Entities** (*Show disabled entities*). The price forecasts also need *Include price forecasts for the next week?* in
  the Strømligning options. If a sensor BattMind uses is disabled later, or Strømligning stops providing it, a
  **repair** names it. Until it is fixed a missing tariff counts as 0, which makes the sell price too high.
- **Refund** is subtracted from the buy price. Selling costs the **sell fee** (what the buyer of your electricity charges for buying it) plus Energinet's net
  and system tariff:
  sell price = price − the tariffs and taxes in it − (sell fee + net tariff + system tariff).
  The price charts on the dashboard show the same sell price.
- **Wear per kWh:** battery price ÷ expected lifetime in kWh, e.g. 40 000 kr ÷ (10 kWh × 6000 cycles) ≈ 0.67 kr/kWh.
  It is on this page because it is an amount in the currency of the prices: a new installation suggests 0.10 kr in
  that currency (0.013 € with euro prices). A higher value makes BattMind more careful.

### Solar panels and weather (optional)

Leave the page empty without solar. With solar:

- **Inverter's max. power:** limits solar and battery power together.
- **Fixed solar sell price:** −1 uses the hourly sell price. Set a price if your production agreement pays
  a fixed price and settles the difference to the spot price – see [Fixed solar sell price](rules/solar.md#fixed-solar-sell-price).
- **Solcast:** select e.g. `sensor.solcast_pv_forecast_forecast_today`. The other days are found automatically,
  but Solcast switches the forecast sensors of day 3 and on off by default: enable `…_forecast_day_3` and on
  for as many days as you plan (**Settings → Devices & services → Entities**, *Show disabled entities*), and keep
  *Hourly detail* on under *Forecast attributes breakdown* in the Solcast options. A **repair** lists the sensors that are disabled, named
  differently or without hourly detail; the days without Solcast are planned from the weather forecast and
  the stored solar history.
- **Weather:** the hourly and daily forecast. Together with the stored solar history per weather type it
  gives a solar forecast that learns your roof – also without Solcast.

### Planning (advanced)

| Field | Default | Meaning |
|---|---|---|
| Days to plan | 7 | the plan covers today and the next days. More days give better decisions about saving energy for later, but cost more CPU and rely on price forecasts |
| Interval | 5 min | how often the plan is checked. It is only recalculated when prices, battery level or settings have changed |
| Dashboard | on | a ready-made dashboard in the sidebar |

Everything can be changed later under **Configure** on the integration.

## 4. Look at the plan

Open the **BattMind** dashboard in the sidebar:

<p align="center">
  <img src="../images/overview-dashboard.png" alt="The BattMind dashboard's Overview view" width="100%">
</p>

- **Overview** shows the battery level forecast, the prices and the plan with a reason for every hour.
  Click a row in the plan to see the numbers behind it.
- **Settings** has all the rules. They are all on by default, and the
  [whole period optimisation](rules/optimizer.md) controls the battery.

Give it a day or two. At the first start BattMind reads the consumption of the last days from Home
Assistant's long-term statistics, and the forecast gets better as it stores the consumption per hour. The
solar forecast gets better as it stores the solar surplus per weather type. Until there are 3 days of
consumption history, BattMind doesn't sell from the battery or block solar charging, see
[Learning the consumption](how-it-works.md#learning-the-consumption).

## 5. Control the battery

BattMind only sets `sensor.<prefix>_powerwall_action`. The automation that makes the inverter follow it is
yours. The easiest way is the blueprint that comes with the integration:

1. *Settings → Automations & scenes → Blueprints* → **BattMind - control the home battery** → **Create automation**.
2. Select the BattMind sensor.
3. For each of the six actions, add what your inverter should do. See [Actions](actions.md) for what each
   action means, and [Huawei Solar](inverters/huawei.md) for a complete example.
4. Keep **Repeat interval** at 5 minutes (default): the action is sent again, so a lost command or a mode
   the inverter changed by itself is put right. Use 30 seconds for a battery controlled via Modbus.

> [!TIP]
> Start with the automation **off** and watch `sensor.<prefix>_powerwall_action` for a day. When the actions
> look right, turn it on.

## 6. Next steps

- [What-if simulator](what-if-simulator.md) - replay your history with another battery, controller (rules or optimisation) or planning settings

- [How BattMind plans](how-it-works.md) – the model behind the decisions
- [Recipes](cookbook.md) – settings for typical setups
- [Dashboard and analysis](dashboard-analysis.md) – how to see what the battery earns
