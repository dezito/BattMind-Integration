# BattMind documentation

The [README](https://github.com/dezito/BattMind-Integration/blob/main/README.md) gives the short version. These pages go into detail: how BattMind thinks, what
every rule does, and how to connect it to your inverter.

## Start here

| Page | Read it when |
|---|---|
| [Getting started](getting-started.md) | you install BattMind for the first time |
| [Tested integrations](tested-integrations.md) | which price integrations and solar forecasts are tested, by country |
| [How BattMind plans](how-it-works.md) | you want to understand why the battery does what it does |
| [Actions and battery control](actions.md) | you build the automation that controls the inverter, check that the battery follows the actions, or [test your automation](actions.md#test-your-automation) |

## Rules

| Page | Content |
|---|---|
| [Whole period optimisation](rules/optimizer.md) | the default way of controlling the battery, and how it relates to the rules |
| [Charging from the grid](rules/charging.md) | cheapest periods, fill up, most expensive periods, required before max level, negative prices, buy to sell, full charge |
| [Discharging](rules/discharging.md) | profit only, prioritise by energy cost, before the sun, max. age of kWh |
| [Selling](rules/selling.md) | maximise profit, sell excess kWh, use midnight battery level, profit requirements, excluded sell hours |
| [Storm warning](rules/storm.md) | keep the battery charged for a MeteoAlarm storm warning (optional) |
| [Solar](rules/solar.md) | solar surplus charging, optimised solar charging, the solar forecast |

## Inverters

| Page | Content |
|---|---|
| [Huawei Solar](inverters/huawei.md) | ready-made actions for the blueprint (LUNA2000) |
| [SolarEdge](inverters/solaredge.md) | actions for SolarEdge Modbus Multi (StorEdge). Checked against the source, not tested on an inverter |
| [Deye hybrid (Solarman)](inverters/deye-solarman.md) | actions for ha-solarman. Checked against the source, not tested on an inverter |
| [Sofar hybrid (Solarman)](inverters/sofar-solarman.md) | actions for ha-solarman. Checked against the source, not tested on an inverter |

Have you set up another inverter, or tried one of the untested pages? Please share your actions under
[Blueprint setups](https://github.com/dezito/BattMind-Integration/discussions/categories/blueprint-setups)
or in an [issue](https://github.com/dezito/BattMind-Integration/issues), so a page can be added or corrected.

## Reference

| Page | Content |
|---|---|
| [Features](features.md) | the full feature list with screenshots |
| [Entities and services](reference.md) | the main settings, result entities and services |
| [How BattMind compares](comparison.md) | BattMind next to Predbat, EMHASS and Battery Controller, and its trade-offs |
| [Dashboard and analysis](dashboard-analysis.md) | every card on the dashboard, and how to read the analysis |
| [What-if simulator](what-if-simulator.md) | replay the planner on your history with another battery, controller or planning settings without changing the active plan |
| [Emojis](emojis.md) | what every emoji in the plan, the history and the current rule means |
| [Recipes](cookbook.md) | typical setups: no solar, solar with a fixed sell price, together with Cable Juice Planner … |
| [Switching from the PyScript version](migration.md) | import of configuration and databases |
| [FAQ and troubleshooting](faq-troubleshooting.md) | "why doesn't it charge now?", logs and debug info |
