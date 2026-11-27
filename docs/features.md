# Features

The full feature list. Back to the [README](../README.md) · [Documentation index](README.md)

- **Charging plan for several days** (1–7 days) based on electricity prices, forecasts and consumption history
- **Two ways to control the battery** – [read more](rules/optimizer.md)
  - **Whole period optimisation** (default): calculates the cheapest action for every hour of the whole forecast
    period at once (charging loss, wear, solar, buy and sell prices and the profit requirements). Every action is
    shown as the rule it corresponds to, and the rule switches decide which actions it may use
  - **Rules:** the charge and discharge rules below decide each hour
- **[Charging rules](rules/charging.md) that can be switched on and off individually** – solar surplus always
  charges first, and grid charging never pushes out solar that would be sold for less than the grid kWh cost
  - **Cheapest periods:** charge in the cheapest periods for the day's consumption (number of periods and price limits are configurable)
  - **Fill up during cheapest period:** charge the battery fully in the cheapest period
  - **Most expensive periods:** charge for the most expensive hours the battery doesn't cover
  - **Required before max level:** only charge what is missing before the solar panels fill the battery
  - **Negative prices:** fill the battery when charging pays, even if the energy is never used
  - **Buy to sell:** charge cheaply from the grid only to sell the kWh again when it pays after loss and wear
  - **Full charge:** for LFP batteries that need a periodic full charge for battery-management-system calibration: when the battery has not reached its maximum level for the set number of days, charge it to the maximum level in the cheapest hours before the end of tomorrow (no switch, 0 days = off)
- **[Discharging rules](rules/discharging.md)**
  - **Profit only:** block discharging in hours where the battery's kWh cost (incl. loss and wear) is higher than the price
  - **Prioritise by energy cost:** save the battery for the most expensive hours until it is charged again
  - **Before the sun:** use/sell the battery before the sun fills it anyway and the surplus would be sold cheaply
  - **Max. age of kWh:** energy older than X hours is used even without profit, and sold when that pays more
- **[Selling rules](rules/selling.md)**
  - **Maximise profit:** sell in the most expensive periods when that pays more than using the energy yourself
  - **Sell excess kWh:** sell the energy that isn't used before the battery is empty or full again (forced discharge)
  - **Use midnight battery level:** only look until midnight instead of the whole forecast period
  - min. profit per kWh and in total, min. kWh per sale, reserve for the house, and exclude specific selling hours
- **Charging power by battery level** – BattMind measures how much slower the battery charges when it is nearly full and the plan only counts on the energy it takes ([read more](dashboard-analysis.md#charging-power-by-battery-level))
- **[Storm warning](rules/storm.md) (optional)**
  - Choose MeteoAlarm's binary sensor under *Configure → Solar panels, weather and storm warning* (off by default): for a wind, snow/ice or thunderstorm warning with a start time at or above the chosen level, the battery is kept at the level the house needs during the warning with no solar plus a buffer for a forecast that is off (at least 20 %), and nothing is sold under it
- **[Solar](rules/solar.md) (optional)**
  - **Solar surplus charging:** include the solar surplus in the plan
  - **Optimise solar charging:** sell the solar surplus in the expensive hours and fill the battery in the cheap solar hours instead
  - forecast from [Solcast PV Forecast](https://github.com/BJReplay/ha-solcast-solar),
    [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) or
    [Helios Forecast](https://github.com/ReikanYsora/Helios-Forecast) and/or own history per cloud cover
- **Consumption forecast** from own history: average, EMA or trend
- **Price providers:** [Energi Data Service](https://github.com/MTrab/energidataservice) and/or
  [Strømligning](https://github.com/MTrab/stromligning), incl. sell price, tariffs, selling fee and refund, and
  [Energy Price Forecast](https://github.com/BackupBaTTerY/energypriceforecast-home-assistant) for prices and forecasts in most of Europe, and the
  spot forecast integrations Spot Forecast, Elprisprognos and Wattcast (wholesale prices with your VAT and surcharge), and EPEX Spot, GE-Spot, Octopus Energy and Nord Pool (your own published prices, next to a provider with a forecast);
  forecast prices are corrected with their measured bias
- **[Cost price of the battery's energy](how-it-works.md#the-cost-of-a-kwh-from-the-battery):** what the energy in the battery has cost (grid, solar, charge/discharge loss and wear)
- **Detailed charging history and overview** as markdown
- **[Analysis](dashboard-analysis.md#analysis)** of what the battery earns and how well BattMind plans
- **[What-if simulator](what-if-simulator.md):** replay the BattMind planner (rules or optimisation) on your history and compare battery capacity, power, loss, wear, reserve, the controller and every planning setting without changing the active plan; it skips incomplete hours, uses available Recorder/statistics history, restarts each data segment at the battery's minimum level, and applies historical sell tariffs conservatively
  - economy per month: charged (grid/solar), discharged, charge price, value of a discharged kWh, margin per kWh and savings
  - charged and discharged per rule, cycles and wear
  - measured round trip loss against the configured loss, and self-sufficiency (solar, battery, grid)
  - the error of each consumption forecast type against the actual consumption, and why another type is suggested when it is more accurate; optionally the most accurate type is chosen automatically
  - the price forecast against the real prices per day ahead: the error, and how often it found the cheapest and most expensive hours
  - a hindsight score per day, planned against what happened, days with an empty battery in the most expensive
    hours, and planned against measured solar surplus
  - the first time, the last year is read from Home Assistant's long-term statistics
- **[Ready-made dashboard](dashboard-analysis.md)** in the sidebar with overview, settings, analysis and a what-if comparison, built from your setup
- **Fail-safe:** BattMind stores the prices in its own [price database](how-it-works.md#prices). Hours
  without a price are filled from it, and if the price integration is down, the last prices are kept for 2
  hours, and then planning falls back to the database
- **Multiple instances:** each instance has its own prefix (e.g. one per battery)
- **Languages:** Danish, English, German, Polish and Ukrainian
- **Does not run in Home Assistant's main thread:** all calculations run in BattMind's own worker threads (the what-if
  simulator has a thread of its own), so Home Assistant is not blocked while the plan is calculated

| Battery level costs and plan | Bill with and without battery | Charging history |
| --- | --- | --- |
| ![Battery level costs and the charge/discharge plan](../images/battery-level-costs-and-plan.png) | ![The bill of the period with and without the battery](../images/plan-total-with-and-without-battery.png) | ![Charging history](../images/charging-history.png) |

| Economy per month | Planned against actual |
| --- | --- |
| ![Economy per month analysis](../images/economy-per-month.png) | ![Planned against actual analysis](../images/planned-against-actual.png) |
