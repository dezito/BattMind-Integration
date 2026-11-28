# Entities and services

Back to the [README](https://github.com/dezito/BattMind-Integration/blob/main/README.md) · [Documentation index](README.md)

## Entities

The entity ids start with the prefix, here `battmind`.

### Settings (changed from the dashboard)

| Type | Entities |
|---|---|
| `switch` | Solar surplus charging, cheapest periods, fill up during cheapest period, most expensive periods, required before max level, discharge only at profit, prioritise discharging by energy cost, sell excess kWh, use midnight battery level, maximise profit, forced discharge of old kWh, optimise solar charging, negative prices, buy to sell, discharge before the sun, control the battery with the whole period optimisation, correct the price forecast, choose the forecast type automatically |
| `number` | Cheap price periods, increase for cheapest price / cheap periods, min. profit per kWh, min. total profit, min. kWh for export, max. hours for old kWh, recommended full charge every (days, 0 = off), hours a storm warning without an end time covers (only with a storm warning entity chosen) |
| `select` | Forecast type (average, EMA or trend) |
| `text` | Exclude selling hours (e.g. `17,18,19`) |
| `button` | Force planning, export data (a [backup](https://github.com/dezito/BattMind-Integration/blob/main/README.md#backup-export-and-import-of-data)), reset dashboard |

The plan is recalculated 5 seconds after a setting is changed. The values are kept across restarts.

### Results

| Entity | Content |
|---|---|
| `sensor.battmind_powerwall_action` | What the battery should do now (see [How to control the home battery](https://github.com/dezito/BattMind-Integration/blob/main/README.md#how-to-control-the-home-battery)), the planned hours per action as [attributes](actions.md#sensors-and-attributes) |
| `sensor.battmind_current_charging_rule` | Current rule as text |
| `sensor.battmind_battery_level_needed` | Required battery level until the next charge (%) |
| `sensor.battmind_kwh_charged_by_solar` | kWh charged by solar (only with solar panels) |
| `sensor.battmind_storm_warning` | The status of the [storm warning](rules/storm.md) and the level it keeps as attributes (only with a storm warning entity chosen) |
| `sensor.battmind_last_full_charge` | When the battery last reached its maximum level (minus 1 %), the base of the [full charge](rules/charging.md#-full-charge) |
| `sensor.battmind_analysis` | Savings this month (in Home Assistant's currency); the analysis per month and day as attributes (used by the dashboard) |
| `sensor.battmind_overview` | Overview and plan (markdown in the `overview` attribute) |
| `sensor.battmind_charging_history` | Charging history (markdown in the `history` attribute) |
| `sensor.battmind_emoji_description` | Emoji explanation (markdown in the `description` attribute) |

Large attributes (markdown, plans) are not stored in the recorder database.

## Services

| Service | Description |
|---|---|
| `battmind.enforce_planning` | Recalculate the plan now |
| `battmind.recalc_charging_history_today` | Recalculate today's charging history |
| `battmind.debug_info` | Returns internal state for troubleshooting |
| `battmind.export_debug` | Save an anonymised debug file for a GitHub issue |
| `battmind.export_data` | Save a backup of data and settings (see [Backup](https://github.com/dezito/BattMind-Integration/blob/main/README.md#backup-export-and-import-of-data)) |
| `battmind.import_data` | Replace data and settings with a backup |

All services optionally take `config_entry_id` so they only affect one instance (`import_data` requires it).
