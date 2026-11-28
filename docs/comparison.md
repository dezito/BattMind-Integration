# How BattMind compares with other home battery optimisers

Back to the [README](https://github.com/dezito/BattMind-Integration/blob/main/README.md) · [Documentation index](README.md)

Home battery optimisation can mean different things: a Home Assistant integration, a separate energy
optimisation service, or software that directly controls supported inverters.

BattMind plans in Home Assistant and exposes what the battery should do. An automation, such as the
included blueprint, applies the action to your inverter.

The examples below compare published approaches, not prices or performance in a controlled test.
Features, supported hardware and tariffs can change, and actual savings depend on prices, forecasts,
battery losses and household consumption.

BattMind's main goal is to minimise the electricity bill and maximise profitable export from the battery.
It combines whole-period optimisation with explainable rules and analyses what the battery actually
earned and how accurately the plan performed.

BattMind does more than select cheap charging hours. It tracks the cost of the energy stored in the
battery, including its source, charging and discharging losses, and battery wear. The plan can use this
to decide whether energy should be used now, kept for a more expensive period, or sold to the grid.

Compare the projects by approach and use case. The table describes features from BattMind's
documentation and the linked projects' published descriptions.

| Area | BattMind | [Predbat](https://github.com/springfall2008/batpred) | [EMHASS](https://github.com/davidusb-geek/emhass) | [Battery Controller](https://github.com/bvweerd/battery_controller) |
| --- | --- | --- | --- | --- |
| Focus and setup | Maximising battery savings and export income in a native HACS integration. | Home Assistant app with broad inverter support and deep configuration. | Add-on, Docker or Python optimisation of batteries and flexible loads. | Native HACS integration for vendor-independent battery control. |
| Planning | Whole-period optimisation or selectable rules, 1–7 days ahead. | Lowest-cost plan from available price, load and solar forecasts. | Configurable mathematical optimisation horizon and time step. | Dynamic programming over the available and modelled forecast horizon. |
| Prices and forecasts | Buy/sell prices, stored-energy cost, Solcast, Forecast.Solar or Helios Forecast and forecasts learned from HA history. | Import/export tariffs with configured solar and load forecasts. | Configurable price, PV and load forecasts, including ML options. | Import/export prices, PV forecasts and learned household consumption. |
| Loss and wear | Round-trip loss, charge/discharge efficiency curves measured by BattMind (or entered) and configurable wear. | Separate battery, inverter and charging losses plus cycle cost. | Battery efficiency and stress cost can be modelled. | Efficiency curves and degradation cost. |
| Charging and export | Grid charging, preserving energy for expensive hours and profitable forced export. | Modes from monitoring to automatic charging and forced export. | Determined by the selected model and connected automations. | Charge and discharge setpoints for several control modes. |
| Inverter control | Generic actions mapped by the included blueprint. | Direct control of many supported inverter integrations. | Plan output connected to HA automations. | Generic setpoints connected through HA automations. |
| Explanation and analysis | Named rules plus savings, wear, forecast accuracy and hindsight score. | Detailed plan, status, cost reports, web console and logs. | Publishes forecast and optimisation results. | Schedule, profitability and browser-based diagnostics. |
| Best suited for | Getting the best financial result from a home battery with transparent decisions and detailed evaluation. | Extensive inverter support and advanced control. | Whole-home energy and flexible-load optimisation. | Generic real-time battery setpoint control. |

Related projects include [PV Opt](https://github.com/stevebuk1/pv_opt), which focuses primarily on
Solis inverters and time-of-use tariffs, and
[Smart Battery Pilot](https://github.com/cygnusb/ha-smart-battery-pilot), a vendor-neutral Home Assistant
integration for price, consumption and PV-based battery planning.

## BattMind's trade-offs

| ✅ Advantages | ⚠️ Trade-offs |
| --- | --- |
| Combines prices, solar, consumption, loss and wear in one multi-day plan. | Good results depend on reliable data and forecasts. |
| Offers whole-period optimisation or individually selectable, explainable rules. | More options require more setup than a simple price scheduler. |
| Tracks stored-energy cost and evaluates savings, forecasts, cycles and wear. | Analysis requires suitable Home Assistant recorder statistics. |
| Works with any controllable inverter through generic actions and a blueprint. | You must map and test the inverter commands yourself. |
