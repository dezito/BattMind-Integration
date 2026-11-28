# BattMind-Integration agent guide

Instructions for coding agents working in this repository. Follow the existing
implementation and project documentation when this guide does not answer a
question. Do not guess behavior that affects battery planning, stored data, or
Home Assistant configuration.

## Project scope

BattMind is a Home Assistant custom integration installed through HACS. It
calculates battery plans and exposes entities/actions; it does not directly
control an inverter. Users connect the resulting actions to their own
automations or blueprints.

- Integration code: `custom_components/battmind/`
- Tests and shared fixtures: `tests/`
- User and behavior documentation: `docs/` and `README.md`
- Integration metadata: `custom_components/battmind/manifest.json`

## Code map

The module and test map is in `docs/development/code-map.md`; read it before
working in an unfamiliar part of the code. Use it as a guide, not an exhaustive
inventory; check the current files before editing. Prefer the narrowest
relevant module and test file over changing a broad layer for a local behavior
change.

Read the relevant documentation before changing behavior. In particular:

- Planning, energy inputs, and stored prices: `docs/how-it-works.md`
- Individual planning rules: `docs/rules/`
- Action entities for the user's automations: `docs/actions.md`
- Inverter setup: `docs/inverters/`
- What-if simulator: `docs/what-if-simulator.md`
- Setup and options: `docs/getting-started.md`
- Backups, import/export, and migration: `docs/migration.md` and the backup
  section in `README.md`
- Dashboard behavior: `docs/dashboard-analysis.md`
- Examples, troubleshooting, and emoji meanings: `docs/cookbook.md`,
  `docs/faq-troubleshooting.md`, and `docs/emojis.md`

## Important behavior and compatibility

- Treat optimizer and planner changes as financially significant. Preserve
  battery, grid, solar, buy/sell price, charging/discharging loss, and wear
  semantics. Keep decisions explainable in the published plan.
- Preserve the distinction between planning and inverter control. Do not add
  direct device control or assume a particular inverter, entity, tariff, or
  automation.
- Respect Home Assistant timestamps and configured time zones. Avoid naive
  datetime assumptions and test day boundaries, missing/future price data,
  and daylight-saving transitions when relevant.
- Timestamps from other integrations (price rows, the weather forecast, Solcast `period_start`) can carry any
  offset, often UTC. Read them with `to_local_datetime(value, tz)` (`utils/helpers.py`; `price_timestamp()` in
  `prices/base.py` is the same call for price rows): it converts to Home Assistant's time zone (`snapshot.tz`)
  before it drops the offset. A naive timestamp is local time. The providers get the zone through
  `CombinedPriceProvider.update_prices(..., tz)`. A daily weather forecast whose time is midnight in its own offset names a date and keeps it
  (`forecast_day()`); any other time is converted. The repeated hour of the autumn change shares one price key;
  which row wins depends on the provider (Energi Data Service: the last row; Strømligning: the first, its
  distribution tariff the last; Energy Price Forecast: the first). Data stored before the conversion (prices, averages and price hours, the
  weather score of `solar_available_db`) keeps its old hours when the source gave UTC; it is not migrated and
  phases out.
- Money limits are chosen for Danish kroner (the forecast adder, the rounding of prices, profit floors and ties, score
  thresholds, the cap of the price correction). A config entry stores `prices.price_scale` (how many of its units one
  krone is worth, `CURRENCY_PRICE_SCALE` in `const/base.py`); missing means 1.0, so existing entries and every result in
  kroner stay exactly as they were. The flow sets it from the unit of the price sensors (`prices/currency.py`), never
  from Home Assistant's currency setting (EUR by default, also in Denmark). A new money amount per kWh in the code is
  chosen for kroner and read through `price_scale(config)`, `scaled(config, amount)` and `price_decimals(config)`;
  never scale a value the user entered (the wear cost is theirs, in their currency) or the `-1` of the fixed solar sell
  price. A new entry's suggested sell fee and wear cost (both on the `price_fees` page, after the price pages, so the
  user sees the amount that is stored) and the starting values of the two price rise thresholds follow the price
  currency (an import or a restored backup keeps its own). A money field belongs on a page after the price pages. The
  rates are rough and only set the size of a limit, no price is converted between currencies. A price sensor in a subunit (ct, p, GBp, øre …) is refused on the Energi Data Service, Energy Price Forecast, spot forecast and published-price
  pages (`price_sensor_errors()`), and so is a price per MWh from Energi Data Service, Spot Forecast, Elprisprognos, EPEX Spot, GE-Spot,
  Octopus Energy or Nord Pool. Energy Price Forecast converts a
  price per MWh to kWh (`price_unit_divisor()`): its rows in the provider and its recorded history where the history is
  read (`CombinedPriceProvider.price_unit_divisors()`; the unit is the sensor's current one); units are read case-sensitively (`GBp` is not `GBP`), and all price sensors must be in one
  currency (`price_currency_errors()`), so a backup provider with a known unit is never in another currency.
- Preserve compatibility of config-entry options, entity IDs/unique IDs,
  stored data, backups, and migration from the PyScript version. Changes to
  these formats or identifiers require a migration/compatibility path and
  documentation.
- The config-flow suggestions (`flow/detection.py`: battery power sign and
  whether the consumption includes charging/discharging, read from recorder
  history) are the same file as in Cable Juice Planner. Change both together
  and keep `tests/test_detection.py` in sync. What the history cannot decide
  stays unknown and keeps the current value; never turn unknown into a default.
- The config flow pre-fills the solar and battery power sensors from the Energy dashboard (`flow/energy_prefs.py`, the same
  file as in CJP; change both together and keep `tests/test_energy_prefs.py` in sync): `suggested_value` only, on empty
  fields of the first setup; only a sole solar or battery source with an existing `sensor.` whose unit passes
  `unit_factor_to_w()` in `flow/detection.py` (W, kW, MW, mW, GW; not kWh, not no unit; deliberately stricter than
  `power_unit_known()`), not used by another entry (`skip_used=True`: one battery per entry). For an inverted or two-sensor
  battery the suggestion is the sensor the Energy dashboard generates for it, which disappears if that Energy configuration
  is changed. The battery sign still comes from the recorder; a PyScript import or a restored backup wins.
- BattMind reads the option `charger_power_entity_id` of Cable Juice Planner
  config entries to leave their chargers out of the house consumption.
  Renaming or moving that option in Cable Juice Planner needs a matching
  change here. A CJP car with `charger_in_house_meter` explicitly False has
  its charger on its own meter: it is not subtracted (`utils/cjp.py`), but
  it stays in `charger_entity_ids` for the stuck-reading check
  (`include_own_meter=True`). A reload happens when either list changes.
  BattMind never adds such a charger to the house (decided, documented in
  `docs/getting-started.md`): a forecast that is too low when the home
  battery supplies that charger is a known limitation.
- The integration supports Home Assistant from the `hacs.json` minimum
  version up to the newest release. Use a fallback when a Home Assistant API
  changes. For example, read stored restore states only through
  `management.backup.async_get_stored_state`, because Home Assistant 2026.11
  removed `RestoreStateData.last_states`.
- Data from Home Assistant entities and external price integrations can be
  missing, stale, unavailable, malformed, or non-numeric. Handle these cases
  explicitly and consistently with the existing fail-safe behavior; do not
  silently invent prices or measurements.
- All price handling goes through `CombinedPriceProvider` (`prices/`, built by
  `create_price_provider()`): which price entities to read, the snapshot, the
  recorded price history, the provider priority (`PRICE_PROVIDER_PRIORITY`:
  Strømligning, then Energi Data Service, then the providers of published prices (Octopus Energy, Nord Pool, EPEX Spot, GE-Spot), then Energy Price Forecast,
  then Spot Forecast, Elprisprognos and Wattcast) and the prices themselves. Other
  code calls its methods (the coordinator via `coordinator.price_provider`)
  and never reads price entities, config keys or provider attributes itself.
- Providers are merged on one common price interval, never a fixed one: the
  finest interval all configured providers fit on, which is the greatest
  common divisor of their own intervals, read from each provider's data (60,
  30, 15 or 10 minutes, or any other length that divides an hour; for example
  hourly plus 15 minute gives 15, 30 plus 60 gives 30, 10 plus 15 gives 5).
  A coarser provider's price is repeated over every slot of its own interval,
  and the provider priority applies per slot: the first provider with a real
  price for the slot wins, then forecasts, as before. Never hardcode 15
  minutes and never merge by timestamp alone: an hourly price at :00 next to
  a lower-priority provider's :15, :30 and :45 made the planner see a falsely
  cheap first quarter of every hour and plan only 15 minutes of charging per
  hour. The dashboard plan overview and the charging history still group
  adjacent periods into whole hours or continuous windows (for example
  03:00-03:52). Cable Juice Planner and BattMind share this rule; change both
  together. Tests must cover an hourly and a sub-hourly provider in the same
  hour, three providers with different resolutions, and intervals that have
  no common slot of 15 minutes (10 plus 15).
  A provider's price is never repeated beyond that provider's own interval
  that day (the most common gap between its prices that day): a row the
  provider left out (it drops a price that is not a number) is a gap in the
  combined prices. The next provider fills the slot; the price history only
  fills an hour that has no price at all.
  Hours filled from the price history stay one :00 entry in BattMind (its
  periods per hour are counted from the provider's own prices, so four slots
  would each be given the whole charge power); Cable Juice Planner gives them
  every slot of the grid because it takes capacity from the price intervals.
  Do not sync the two.
- Keep network and blocking work out of the event loop. Follow the existing
  coordinator/executor lifecycle and ensure workers and resources are cleaned
  up on unload.
- Do not add telemetry, undisclosed external services, credentials, or
  undocumented network endpoints.

## Implementation conventions

- Follow the existing Home Assistant patterns in this integration for config
  entries, coordinators, entities, services, repairs, and lifecycle management.
- Keep changes focused. Prefer extending the existing module responsible for
  the behavior over adding duplicate logic or broad refactors.
- Add or update tests for behavior changes. Prefer deterministic tests using
  the shared fixtures in `tests/conftest.py`; mock Home Assistant entities and
  external integrations rather than relying on a live installation or device.
- Test boundary conditions and failure paths as well as the expected path.
  For asynchronous lifecycle changes, verify setup, unload, and background
  task cleanup.
- Keep user-facing behavior and documentation aligned. Update README/docs when
  configuration, entities, planning behavior, backup formats, or migration
  steps change.
- After every new, changed, or removed feature, review and update the
  README, the relevant `docs/` pages, the automated tests, and the HA-dev
  seed, simulator, and other test data before the task is done. Remove
  outdated references and test data, and check that documentation and test
  data describe the actual behavior.
- Whenever changing dashboard content or behavior, review and update each
  affected "Read more" description so it accurately covers the entities,
  controls, and functions shown beneath it. Check that the descriptions do not
  omit new dashboard content or retain claims about removed content.
- When a HA-dev environment is available, apply and verify dashboard changes
  there as well. For dashboard analysis changes, check that every test
  device/config entry has sufficient representative recorder/history data for
  the analysis entities and time ranges being tested; current entity states
  alone are not enough. If required history is missing, create or update
  dedicated test history and states in HA-dev so the analysis page can render
  and calculate its results. Do not alter live user history. Report any
  missing data that could not safely be added, or if HA-dev is unavailable.
- If changing any file under `custom_components/battmind/texts/` or
  `custom_components/battmind/translations/`, update every supported language
  (`da`, `de`, `en`, `pl`, and `uk`) consistently. Check that keys and
  placeholders match across languages; do not update only Danish or English.
- Read the dashboard template only through `template_text()` (`management/dashboard_build.py`). The price
  chart of each price integration is `dashboard/prices/<provider>.yaml` (`<provider>` is the key of
  `PROVIDER_CLASSES`), included by a `# @include` line, and the provider's `dashboard_entities()` maps the
  external entities that chart uses; a test checks that both name the same entities. A new price integration
  needs a provider class and a chart file. Keep `expand_includes()` in sync with Cable Juice Planner.
- Do not edit generated/cache files such as `__pycache__`, `.pytest_cache`, or
  `.ruff_cache`.

## Calculations and economic estimates

These rules apply to code, tests, documentation, and the dashboard.

- Follow data and calculations from input to the displayed result; check
  units, timestamps, missing data, fees, losses, and boundary conditions.
- Do not let missing history or unknown prices turn into favourable default
  values. Reject the calculation or use a documented, conservative value.
- Clearly distinguish historical hindsight from a realistic forecast. Do not
  present an optimistic backtest result as expected savings, earnings, or
  payback time.
- Add regression tests for bugs found (write the test first and confirm it
  fails without the fix) and for scenarios with missing or incomplete data.
- Use Home Assistant data for validation when an environment is available and
  access is approved. Keep credentials and sensitive data out of code, logs,
  and reports.

## Project notes

Agents record lasting decisions and findings for this repository here (not in
private agent memory). Update or remove a note when it stops being true.

- Window values (`utils/power.py`, `WindowCalculator.short_values`) hold every sensor state until the next row
  (`_hold`). Never draw lines between rows: a sensor writes a row only when its state changes, so lines turned a
  battery power that blinks between 0 and 5 kW into hours of charging. A row that is no finite number ends the hold
  and gives no value (an average counts only known time). The hourly statistics stay linearly interpolated.
  Recorded analysis days and hours carry `flows` (`FLOWS_VERSION`); `analysis_update` checks older days with
  statistics (consumption, battery power, solar and ignored consumers) for every hour of the day and, when charged
  or discharged differ by more than `FLOWS_SCORE_TOLERANCE_KWH`, replaces the sums of energy from the statistics and
  drops the score (it cannot be calculated again); the kept hours of the last two days are corrected per hour. A
  sensor without statistics is never read as 0: its energy stays as recorded. The same job (once per `FLOWS_VERSION`,
  marker `charging_history_flows`) measures the hourly charging history of the last `CHARGING_HISTORY_CORRECT_HOURS`
  hours again from the buffers (`correct_recent_charging_history`): the rules, the buy and sell price (the price lists
  only hold today and later) and the battery price of the hour are kept; hours that were not recorded, have no number
  from every sensor in the buffers, or have the same energy within a tolerance are left alone; an hour with no energy
  is removed; the rules table of the day is made again. Older charging history and `power_values_db` are not
  migrated. Keep `History` in Cable Juice Planner in sync.
- Keep the worker-thread design so calculations never block HA:
  single-thread executor, snapshot -> job result. The what-if simulation is
  the exception: it runs on its own `BattMindEngine` in `_what_if_executor`,
  outside `_job_lock`, so it never holds up planning. An engine has one job at
  a time (`run_job` keeps per-job state on the instance), so never run two jobs
  on one engine from two threads. The what-if engine has empty databases: it
  gets the live round trip loss and measured curves only through the short
  `what_if_live_inputs` job on the worker (`what_if(live=...)`); a what-if
  engine without `live` would silently use empty defaults. Its result is not
  published or saved by the coordinator. `stop_what_ifs()` stops every running
  what-if (`_what_if_stopping`, also one started by the action, which has no
  dashboard job); it runs when the entry unloads and from `_on_ha_stop`,
  because Home Assistant stopping does not unload entries.
- Database fail-safe lives in `management/storage.py` (same code in CJP): every
  save goes through `BattMindStorage.async_save`, which first copies the valid
  current file to `battmind_backups/storage/` (one copy a day, 3 kept; the
  age comes from the newest copy's mtime, so restarts add none), and
  `async_load` moves a corrupt file to `.corrupt.<time>` and restores the newest
  valid copy. Tests: `tests/test_storage_failsafe.py`; `hass_storage` mocks the
  Store, so they place the files on disk themselves.
- `input_boolean.dezito_show_debug_button` in the generated dashboard is the
  user's own toggle for the debug-info badge. It is intentional; do not flag it.
- README/docs audience: first-time installs, possibly migrating from PyScript. No release notes or "changed since" text about development versions.
  Detailed docs live in `docs/` (English); the README is a short landing page
  that links into it. Anchor links to emoji headings need a leading hyphen
  (`#-buy-to-sell`). `docs/development/` is for contributors and coding
  agents, not for users.
- Releases (`hacs.json` `zip_release`, `.github/workflows/release.yml` and
  `release-notes.yml`, `.github/next_version.py`, `.github/check_version.py`):
  read `docs/development/releases.md` before changing them. Keep
  `next_version.py` and the release-notes prompt in sync with the CJP copies.
  Do not bump `manifest.json` `version` by hand: the release workflow sets it
  in the zip from the tag.
- The hindsight score (`day_score()` in `engine/analysis/helpers.py`, stored per closed day, published by
  `publishing.py`, explained in `docs/dashboard-analysis.md`) compares the day's plan with a level neutral baseline:
  possible = cost without battery - `optimal_neutral` (the best plan that ends at the start level), saved = possible -
  (actual - optimal). `optimal` is the best plan that nets the same battery energy as the day did (start + stored -
  delivered, by the efficiency model); the measured end level is only a plausibility check (`SCORE_MAX_DRIFT`), so
  standby use and model error are not blamed on the plan. Ignored consumers and CJP chargers, also those on their own
  meter (`own_meter_charger_entity_ids`), are not part of the score: they take the solar surplus first, then the
  battery, and that energy is taken out of the day's solar and discharge and out of what the best plan must net.
  Only measured data is scored: the hourly price is the mean of its price slots and only when every slot is a real
  price (`real_hour_price()`, source `:real`); the battery level comes only from the live state
  (`get_battery_level_or_none(stale_ok=False)`, never the 100 % fail-safe); recorded hours carry `sv`
  (`SCORE_VERSION`) and `gap`. A day with a missing hour, a lost sensor, a price that is not real, an unmeasured level,
  energy that does not add up, the 25-hour day or hours of an older version gets `no_score` with a reason, published
  as `score_missing`. Raise `SCORE_VERSION` whenever the model changes (older scores are then hidden; the hours are
  kept only a day, so they cannot be calculated again). `optimal` is capped at `actual`, so tests of the model use
  `optimal_raw`: `tests/test_hindsight_score.py` requires that a best possible plan scores 100 % when the day ends
  higher, ends lower, loses energy in standby, feeds an ignored consumer, or has loads and surpluses smaller than a
  level step. Keep a new case of that kind with any model change.
- Measured capacity (`utils/capacity.py`, `analysis_db["capacity"]`, published as `battery_capacity`) is always measured; the
  figures are two bounds that are never blended: charging (AC kWh per 100 % level) is an upper bound of the usable
  capacity, discharging a lower bound, because the efficiency is not known. Only `battery_size_used()` (`engine/battery.py`)
  lets the plan count on it, and only with the switch `learn_battery_capacity` on (default off) when the status is `below`:
  then min(configured, charging bound rounded down to 0.1 kWh), and a bound under half the configured size is not
  believed; both directions need their runs. With a measured efficiency curve in use it stays the configured size:
  that curve was measured with it, so correcting both would count twice. The charging history keeps kWh; the level a row
  is worth is always calculated from them with the size in use (`current_battery_level_expenses()`, `age_limits()`, the
  history table), never read from the stored `percentage` (only the history table falls back to it for a row without kWh), which is on the scale
  of the last combine. Every
  `measure_efficiency` call (efficiency,
  taper, capacity) and the capacity summary's reference stay on the configured size, so the measuring never feeds on
  itself; the what-if has no measurements and uses the configured or scenario size. The size is in `plan_inputs_hash`.
  Do not add another reader of `analysis_db["capacity"]`; new code uses `battery_size_used()` or `percentage_to_kwh()`.
- Push notifications (`management/notifier.py`, option `notify_list` and the `notify_*` categories) only inform. A source reports a
  condition as True (holds), False (explicitly over) or None (not evaluated this time: nothing changes); unknown or missing data
  is never False, and None never sends "resolved" (`notice_ready` too: None is "not clear", e.g. an inconsistent capacity, neither
  ready nor reset). The engine reports with `notice()` / `notice_ready()` in its job result (`JobResult.conditions`, `ready`,
  `summary`), the health checks through `HealthWatcher._push()` (a repair raised or deleted), the coordinator for the start and the
  hour end. A push counts as sent only after a notify service took it; what was sent is kept in the store
  `battmind.<entry_id>.notices` (not in the backup or the debug export). A timer (`PushNotifier.start()`) tries what is pending
  again every minute, so a failed start or a notify service that registers late is retried without a job. Debounce is by duration
  per kind (`DEBOUNCE`), never by a pause between sends, so a "resolved" is never swallowed. What is already ready when the notifier
  first sees it is only noted, never announced. Every problem has one source: the battery level is `battery_level_lost` (also left
  out of the `entities_missing` push), `sensors_unavailable` is only the house consumption and the battery power sensor (not the
  prices, not the solar sensors). The daily summary shows the plan as it is now, never a savings figure. The what-if and replay
  engines never feed the notifier. The PyScript import never turns the push notifications on (`NOT_IMPORTED_OPTIONS`). Keep
  `notify_list` at the top level of the config: the debug export redacts it by that key.

### HACS default-catalog review

Points a HACS reviewer raised; check every change against them.

- Never write the user's own configuration (`automations.yaml`,
  `scripts.yaml`, `configuration.yaml`, packages or any other YAML the user
  edits), not at startup and not from a repair fix flow, even atomically: one
  bad dump costs the user their automations. When a bundled blueprint input
  changes shape, keep the blueprint backward compatible (e.g. accept a single
  entity as well as a list) and, if the user has to act, raise a repair that
  tells them what to do in the editor. Reading the configuration is fine, in
  the executor. To read a blueprint automation's inputs, read
  `automations.yaml` (the file the UI editor edits): the automation entity's
  `raw_config` is the substituted configuration, without `use_blueprint`.
  Writing the integration's own files is fine: its `.storage` data, its
  backup/debug folders, and a bundled blueprint under
  `blueprints/automation/<domain>/` only when it is missing or the user
  confirmed the update repair.
- Services that replace or delete stored data, or change config entries or
  options (`import_data`, and any similar new service), must be registered
  with `homeassistant.helpers.service.async_register_admin_service`, not
  `hass.services.async_register`. Cover it with a test that calls the service
  with `Context(user_id=hass_read_only_user.id)` and expects `Unauthorized`.
- Check the integration in ha-dev, not only in tests, and read
  `home-assistant.log` for `Detected blocking call` and errors from the
  integration. Tests that fake Home Assistant objects (`SimpleNamespace`,
  mocks) must match what Home Assistant really holds; prefer real files and
  real components.
- README and `docs/getting-started.md`: the repository is in (or submitted to)
  the HACS default catalog, so the install steps lead with searching for the
  integration in HACS. Adding it as a custom repository is only a fallback
  after the steps.

### Home Assistant quality scale

Follow the [Integration Quality Scale rules](https://contributors.home-assistant.io/core/quality-scale/rules/)
where they fit a calculated integration, and
[Permissions](https://contributors.home-assistant.io/core/internals/permissions/) for actions. Keep these in
place and extend them to new code:

- Actions are registered in `async_setup` (action-setup); see the admin rule
  above.
- Errors from actions are `ServiceValidationError` / `HomeAssistantError`
  with `translation_domain=DOMAIN` and a `translation_key` from the
  `exceptions` section of `strings.json` (exception-translations), added in
  every language. No free-text messages; `_validation_error()` in
  `__init__.py` passes on a translated error and wraps anything else in
  `action_failed`.
- Every entity platform module sets `PARALLEL_UPDATES = 0`
  (parallel-updates); new platforms too.
- `diagnostics.py` returns the debug export data without the databases,
  anonymised by the same `Redactor` (diagnostics). New data in the debug
  export goes through the redactor.
- The README "Removal" section (docs-removal-instructions) lists what is left
  behind; update it when the integration writes new files or folders.
- Blocking I/O (listing, globbing, reading files) runs in the executor. A
  plain function given to `async_at_started`, a listener or `HassJob` runs in
  a thread: mark it `@callback` or make it `async`.
- Not applicable: discovery, reauthentication and connection tests (no device
  or external account).

### PyScript import

Many users still run the PyScript version, so the import must keep working and
stay easy: the config flow finds legacy files (`find_legacy_sources`,
`load_legacy_config` in `management/storage.py`, `options_from_legacy` in
`flow/schema.py`). Covered by `test_import_legacy_pyscript_config` in
`tests/test_config_flow.py`.

### Planner and optimiser rules (design requirements)

Keep these invariants when touching any planner rule:

- Solar surplus has first priority: grid charging may not use charge rate that
  solar uses in that hour, nor battery room that solar fills later
  (`grid_headroom_kwh`: highest level from the charge hour until the battery
  hits min). Solar first is soft when a price is known: grid may push solar
  out if that solar is sold for more than the grid kWh costs.
- `solar_prediction_db["total"]` is solar SURPLUS (production minus house
  consumption), so there is no battery consumption in hours with surplus.
- `min_profit_per_kwh` applies to discharging: every kWh out of the battery
  (used or sold). Charge rules check it on the later use/sale of the charged
  kWh; `negative_price_planner` (no discharge) only needs 0.01. `min_total_profit`
  and `min_sell_kwh` apply only to sales to the grid (decided with the user),
  never to charging or self consumption. Exception:
  `force_powerwall_max_hours_old_discharge` uses old energy even without
  profit, but only sells it when selling beats using it. In the optimiser,
  `min_profit_per_kwh` is a cost per delivered kWh and `min_total_profit` a
  fixed cost per force discharge hour.
- Charging loss is round trip: 1 kWh out costs price/(1-loss)
  (`calc_battery_loss_cost`), split evenly: grid kWh × sqrt(1-loss) into the
  battery, used/sold kWh ÷ sqrt(1-loss) out (`battery_efficiency()`).
- `most_profit` pairs a sale with the cheapest usage hours in the same
  discharge period (no full battery in between).
- The battery never charges and discharges in the same hour (execution
  priority: grid charge > force discharge > discharge).
- `use_midnight_battery_level` with the optimiser: each day of
  `days_to_predict` is optimised on its own (`optimize_per_day()`, max 24 h,
  ends at midnight, next day starts at the previous day's end level).
  Horizons follow `days_to_predict`, not fixed day/day+1 windows.
- Switch `optimizer_control` (default on): `apply_optimizer_plan()` replaces
  the rule plan with the DP's actions; rules still run for comparison and are
  the fallback if the optimiser fails. `explain()` maps every optimised action
  to its matching rule; rule switches are permissions (`RULE_SWITCHES`,
  `optimize_with_rules` forbids the action and re-optimises, up to `MAX_ROUNDS` = 40
  rounds: a forbidden action moves to the next best hour, so a long stretch needs
  one round per hour, 25 were seen. A plan that is not `converged` after the rounds
  still has actions of switched off rules: `shadow_plan()` then returns None, the
  rules control the battery and `optimizer_shadow["converged"]` is False. Never use
  a result of `optimize_with_rules` without checking `converged`). A held-back hour
  with a later, more expensive use belongs only to
  `prioritize_discharge_hours_by_energy_cost`, one without such an hour only to
  `only_discharge_on_profit` (decided with the user: switching one discharge
  switch off must change the plan on its own). The optimiser is
  always calculated.
- Unsupported battery actions (option `unsupported_actions`, page
  `battery_actions`, `CONTROLLABLE_ACTIONS` / `ACTION_SWITCHES` in
  `const/base.py`): the optimiser never chooses them (`OptimizerConfig.unsupported`),
  `enabled()` turns their rules off, and `supported_action()` maps a leftover
  action in `charge_if_needed()` and the rule plan. The switches stay available
  and keep their value (an unavailable entity is restored as `unavailable`, so
  the value would be lost); they only get the attribute `unsupported_action`.
  The generated dashboard hides them (`unsupported_entities()` in
  `management/dashboard_build.py`, plus `_ACTION_SETTINGS` for settings only
  those rules use); `_prune` then drops dangling dividers and a heading left
  with only its *Read more*.
  A new rule that needs an action must be added to `ACTION_SWITCHES`.
- Rule switches default on (except `use_midnight_battery_level`);
  `powerwall_max_hours_old` default 48, from 12 to 72 h (a shorter limit makes no sense; the number entity
  clamps old stored/imported values on restore). The max-age rule checks every planned day: its `old_keys`
  filter is exact per day, so there is no "days old" skip.
- Decided with the user:
  - `most_profit` checks `min_total_profit` against the profit shown in the plan (sell price − battery kWh cost
    incl. loss and wear), like `sell_excess` and Buy to sell. The max-age sale (`force_powerwall_max_hours_old_discharge`)
    still compares with using the energy (the cost of old energy is sunk): do not "fix" that to the shown profit.
  - The forecast adder (`PRICE_ADDER_DAY_BETWEEN_DIVIDER`) makes a forecast sale worth less: it is subtracted from a
    forecast SELL price, and added with a fixed solar sell price (`sell_adder_sign()` in `prices/base.py`, also in
    `Pricing._database_prices`). A forecast buy price is raised.
  - The 80 % margin of `optimize_solar_charging` (`margin_delta` of `_optimize_solar_blocking`) counts only the sun:
    a grid charge after the first blockable hour does not refill the battery in that test.
  - `usage_reserve` holds for every selling rule, incl. `grid_charge_to_sell`: it is skipped for a sale hour whose
    planned end level (without the pair) is under max(level_min, reserve), because charge and sale cancel each
    other. Stricter than the optimiser, which may export part of a charge above the reserve.
  - Planned grid charging never exceeds the charge power: kWh left in an hour are rounded DOWN (`_floor_kwh()` in
    `charge_rules.py`, 0.01 kWh), and with 15 min prices each price period takes `max_kwh_charging / periods`
    (`charge_periods_in_hour()`), so the periods of an hour together stay within the hour. The level forecast
    (`charging_flow.py`) and the Before the sun rule add up every charge session of the hour, not only the one
    that starts on the hour.
  - A price that is not a finite number (`is_number()` in `prices/base.py`) never reaches the plan: the providers
    skip such rows, tariffs that are NaN/inf give a NaN sell price (not zero tariffs, which would be a too high
    sell price) and `CombinedPriceProvider._combine()` drops every non-finite price, so the slot gets the next
    provider's price, an hour with no price at all is filled from the price history, else it has none. An unavailable tariff is still 0.
  - `optimize_with_rules()` accepts a plan when the max-age limits come back to a set it has seen (they can flip between
    two plans for ever): every plan keeps the max. age exactly, only forbidden actions of switched off rules make it
    "not converged". Do not count a limit cycle as not converged: that sends users with a charging history to the rules.
  - The rule path of Max. age (`force_powerwall_max_hours_old_discharge()`) marks an hour only while old energy is
    left: old kWh of an hour = what has become older than the limit by then (`ctx.old_energy["parcels"]`) minus what
    the battery has delivered since the plan starts (`ctx.old_energy["delivered"]`, the oldest kWh leave first, house
    use only in hours without solar surplus). It carries from day to day. Unknown-age energy is not tracked by the rule's
    count. In the age model it is never old and counts as fresh, so it is used up last: the old kWh of the history leave
    first (`_unaged_left()` in `optimizer.py`). Do not "fix" this to a true FIFO where unknown-age energy leaves first:
    that would put the old energy behind it and force the whole battery out before the rule reaches it.
  - A sale hour is a discharge hour: after all rules `future_charging()` calls `allow_discharge()` for every hour in
    `force_discharge_timestamps`, which drops a hold (Profit only / Prioritise / Maximise profit) in that hour and lists
    it in `discharge_timestamps`. `allow_discharge()` works on the hour of the timestamp (15 min price periods): a charge
    session in any period of the hour keeps it from changing anything.
  - With a reserve for the house (`usage_reserve` above the minimum level) the sale rules leave hours alone that another
    rule holds back (`sale_blocked_by_hold()` in `support.py`): the level forecast has no house use in a held hour, so a
    sale there ended under the reserve once the hold was dropped. Without a reserve the sale wins over the hold (the
    rule above). In a held hour `grid_charge_to_sell` shows only the exported part of the rate (rate minus house use)
    as the sale; the pair itself is sized as before.
  - `explain()` labels a charge *Negative prices* only when buy / efficiency² + wear <= -0.01 (the rule's own limit,
    `NEGATIVE_PRICE_MARGIN`), not for every price <= 0: that label decides which switches allow the charge.
  - A battery level of NaN/infinity (state "nan"/"inf", also in the recorded buffer) is handled like an unavailable
    sensor in `get_battery_level()`: the last recorded number if the sensor stopped giving numbers less than
    `BATTERY_LEVEL_MAX_AGE` (2 hours) ago (measured from the first row after that number, so a level that never
    changed still counts), else 100 % with a warning (the existing fail-safe; it used to stop the job with an
    OverflowError). The battery temperature already stops the battery for NaN/inf.
  - The planned-hours attributes never announce an unsupported action: with `force_solar_only_charging` unsupported its
    list is empty and the leftover hours are in `discharge_timestamps` (what `supported_action()` runs instead).
  - An hour that gets a charge session loses its hold (`add_charging_session_to_day()`): Profit only / Prioritise hold
    hours before the charge rules plan their charges.
  - `discharge_before_solar` means before the sun has filled the battery (decided with the user): the optimiser's
    `explain()` labels a sale 🌄 whenever the sun fills the battery to max later (≤ 24 h, no charge between), also in
    an hour after sunrise. The rule path (`discharge_before_solar()` in `discharge_rules.py`) uses the same definition:
    it scans into the next planned day when needed and only accepts a solar refill within 24 hours with no grid charge
    in between. It budgets only future solar that would otherwise be pushed out in the same continuous solar block,
    before the 24-hour limit or the next grid charge; a later separate solar period does not count. It does not count
    house use in a solar-surplus hour as a benefit. A sale in a solar hour disables
    solar charging in that hour; the remaining later solar must still be enough to refill the sold energy.
  - The `profit` of a sale in the optimised plan is after the cost of the kWh in the battery (incl. loss and wear),
    like the rules' (`optimal_battery_kwh_cost()`: the running average price over the optimal plan's own charges),
    not the sell revenue.
  - The end value of the energy left in the battery lets the optimisation charge for use after the period; this is
    documented in `docs/rules/optimizer.md`, not changed. `optimizer_shadow["requirements"]` holds the profit
    requirements (steering figure, not money) of the optimal plan, the rules and self consumption, calculated the
    same way by `requirement_cost()`; the overview shows the one of the plan that controls the battery.
- With `force_powerwall_max_hours_old_discharge` on, the plan never holds
  (`force_solar_only_charging` with consumption) energy older than
  `powerwall_max_hours_old`, with the optimiser or the rules in control. Age is
  FIFO: the history (`battery_expenses`, walked newest first up to the battery
  level in `FutureOptimizer.age_limits()`, so the what-if replay gets it too),
  then the kWh the plan charges (fresh when charged). Unknown-age energy is never
  old. The optimiser enforces it exactly in `optimize()`'s forward pass and gives
  the DP `OptimizerHour.old_above`; the rule plan is fixed by `enforce_max_age()`
  in `shadow_plan()` and `apply_max_age_to_rules()` (the optimiser off). Sales are
  not restricted by it.
- The shadow plan simulates rule hours with the planned kWh and respects
  `exclude_sell_hours`.
- Partial charge hours are real: the battery level is in `plan_inputs_hash`,
  so every cron interval replans from the new level. The optimiser models
  this with grid fractions 25/50/75/100 %.
- A fixed solar sell price (`production_price` != -1) is a settlement
  agreement: a sold kWh is worth fixed price − the hour's raw sell price and
  can be negative. Use `sell_value()` (`engine/pricing.py`) everywhere; keep
  price data and DBs raw and convert at use, never clamp at 0.
- The test harness has no sell entity (sell = buy price); use the
  `solar_engine()` helper in `tests/test_engine.py` for realistic scenarios.
- The sun sentence of the overview (`_solar_day_lines` in `engine/planner/overview.py`, today and tomorrow, below the solar
  surplus table) is text only and reads the plan's own figures: `solar_kwh_prediction` (only when all 24 hours are really known: `solar_known_hours` == 24, not just `len` == 24) and
  `battery_level_flow`. The level at the start of an hour is the end level of the hour before (`_solar_day_start_level`),
  never the first element of the hour's list: the planner writes `[max, 0.0]` for the hour that hits the maximum. The text
  never says where the surplus that does not fit goes (it may be sold, charged into a Cable Juice Planner car, or curtailed),
  and never claims the battery is filled later when solar charging is held back (the whole period optimisation holds it back
  without that). In tests the solar sensor is set after the plan: configured from the start, the planner overwrites a
  test's `solar_prediction_db` from the empty history.
- Periodic full charge (number `full_charge_recommended`, sensor `last_full_charge`; engine in `engine/full_charge.py`):
  - The battery counts as full at `powerwall_battery_level_max - FULL_CHARGE_MARGIN` (`const/base.py`). The same
    margin is used for detection and for the rules' and the optimiser's "reached full" check.
  - Register only from real battery level samples, never from the 100 % fail-safe value of `get_battery_level()`,
    and with no hold time (the plan often discharges right after reaching max). After a restart with a stored
    time, the first sample is only a baseline; a stored time in the future is dropped. An unknown last full
    charge (nothing stored, nothing in the history) counts as a day overdue, never as just charged.
  - No requirement is built while grid charging is unsupported (the rule counts as off and the dashboard hides
    the number and the sensor). `full_charge_planner` has no switch and is never forbidden in
    `optimize_with_rules`.
  - The optimiser requirement is optional: `full_deadline=None` must give an identical plan. It must never make
    the DP infeasible: `_full_charge_layer()` counts as full only at `full_level + step/2` (capped at
    `level_max`). If the forward pass misses, the plan without the requirement is returned, with `full_reached`
    and `full_dropped` checked against that plan; the failed plan is kept in `full_missed` so
    `optimize_with_rules` still learns the max-age limits from it.
  - `_full_charge_layer()` copies the storm floor penalty and `min_total_profit` from the main DP loop in
    `optimize()`: keep them in sync.
  - Only the run of rising hours into the first full hour is labelled `full_charge_planner`; earlier charges keep
    their own rule's label and can be forbidden. The rule path's top-up may be smaller than the minimum charge
    (`add_to_charge_hours` checks the hour's capacity, not the amount) but never pushes out solar or exceeds the
    charge power.
  - Replay and what-if keep the full charge off (`ReplayInputs.full_charge_status`) and never read the live
    `last_full_charge`.

- The storm warning (`engine/storm.py` pure rules, `engine/storm_warning.py` engine part) keeps a battery level per hour
  (`OptimizerHour.floor`). The shortfall penalty is steering only, like `min_profit_per_kwh`: never in costs, totals,
  `breakdown` or `requirement_cost`. Nothing is sold under the level: `step_hour` caps the export, and
  `enforce_storm_floor()` makes the rules' plan list only what may be sold (the plan is what runs, a sale runs at full
  rate until the next replan, and `charge_if_needed` also stops export under `storm_floor_now()`). A charge or hold
  that the plan needs for the storm is labelled `STORM_RULE`: it belongs to no rule switch and `optimize_with_rules()`
  never forbids it (the label covers the whole hour, also energy beyond the need: a known limitation). The storm level
  goes before max. age only where the storm needs the energy (`floor_ahead`, `storm_holds()`). The rules' fix is
  computed before the optimiser runs, so it also holds when the optimiser fails (`apply_storm_to_rules()`). The warning
  entity is deliberately not in `configured_entities` (an unavailable warning must not hold up startup), and the
  snapshot keeps only the attributes the plan reads. A forecast of 0 kWh or a missing hour is "not complete"
  (`need_complete`), never a favourable default. Warning statuses never use the string `unavailable` (Home Assistant's
  own state): it is `entity_unavailable`. Without the option no sensor, number or dashboard section exists
  (`remove_entity()` clears old ones).

- The charging power taper (`utils/taper.py`) is learned from the battery's own power and level (the same measurements as
  the efficiency, `measure_efficiency(details=)`) and only from hours that proved a grid charge (plan check `ok`, grid
  charging held 3/4 of the hour, the whole measurement inside such hours): a charge limited by the sun must never teach a
  cap. The recorder history is deliberately not read for it (it has no plan). A cap is the 90th percentile of a band, only
  relative to the best judged band, never above the configured rate; with `learn_charge_taper` off or too few measurements
  the plan is exactly as before (every new path returns the old value without caps). Every optimiser charge goes through
  `step_hour()`, which walks the hour through the bands (`charge_energy_kwh()`); the rules use the same figure
  (`taper_rate_at()`, the level forecast, `kwh_available_in_hour()`), and `taper_cuts()` + `apply_taper_to_rules()` cut the
  rules' charge hours that the taper (not the room) limits. The invariant "planned grid charging never exceeds the charge
  power" therefore means the tapered energy of the hour. The what-if replay has no taper.

- Solar forecast sources: `forecast_entity_id` accepts Solcast (read from `detailedHourly`) or a sensor of Forecast.Solar
  or Helios Forecast, read through the HA energy platform (`async_get_solar_forecast`) and normalised by
  `utils/solar_forecast.py` into Solcast-shaped hourly rows in `snapshot.solcast`.
  - `KEY_SEMANTICS` lists the supported integrations: Helios keys are hour starts, Forecast.Solar keys are period ends.
    Missing, invalid, partial or out-of-window hours are left out, never 0. Forecast.Solar planes are summed via the
    Energy dashboard prefs only for hours all planes cover.
  - `utils/solar_forecast.py` and `tests/test_solar_forecast.py` are byte-identical with Cable Juice Planner; change
    both together. The Solcast repair and the dashboard's Solcast entities apply only to Solcast.
  - The solar prediction lists (`solar_prediction_db[day]['total'/'total_sell']`, `solar_kwh_prediction`) are indexed by
    hour of the day: hours without any value are 0.0 up to the last known hour, and `known_hours` /
    `solar_known_hours` counts the real ones. A complete day means `known_hours == 24`, never `len == 24` alone.

### Sell price (`calculate_sell_price()` in `prices/base.py`)

Decided with the user: selling a kWh costs the **sell fee + Energinet's net
tariff (`transmissions_nettarif`) + system tariff (`systemtarif`)**, which the
electricity supplier charges for selling. Formula:

`sell price = price − every tariff/tax/surcharge in the price − (sell_fee + transmissions_nettarif + systemtarif)`

- Strømligning's sell sensor is a consumer price: it includes the
  distribution tariff (`tariffs`, per period), net tariff, system tariff,
  electricity tax and surcharge, so all are subtracted, and the net and
  system tariff once more as selling costs. Key mapping as Energi Data
  Service: distribution -> `tariffs`, nettariff -> `transmissions_nettarif`.
  The distribution sensor only has today's periods; other days use the same
  time of day from the latest known day (0 would give too high a sell price).
  It is still 0 without a distribution sensor, when it is unavailable, when its
  `prices` attribute has no valid rows, or for a time before its first row on
  the latest known day (`_distribution()` in `prices/stromligning.py`). That is
  a known, unfixed breach of the no-favourable-default rule (too high a sell
  price), same as in CJP. The repair in `docs/getting-started.md` only covers a
  configured sensor that later goes missing.
- Energi Data Service: a sell sensor with a `tariffs` attribute includes
  them. A sell sensor without it is a raw price (tariffs in the price = 0); the
  selling costs (net and system tariff) then come from the buy sensor's
  `tariffs` attribute (`get_sell_costs()` / `eds_sell_costs()`, what-if:
  provider `sell_costs`).
- `sell_fee` is what the buyer of your electricity charges for buying it (user's definition).
- Energy Price Forecast has no tariffs in its prices: sell price = the price of its sell sensor − `sell_fee`. The page
  requires the sell sensor and refuses a retail sensor or the buy sensor as the sell sensor (the retail price is higher than
  what a sale pays). An entry without one (a backup restored in the options menu) sells at its buy sensor's price less the fee (decided with the user): higher than a sale pays.
- Energy Price Forecast in a market without published prices (empty `raw_today`/`raw_tomorrow`, today only in
  `raw_forecast`) is refused on the price page (`price_sensor_forecast_only`), for both the buy and the sell sensor: past
  hours of today would have no price. Its own price source (Nord Pool/ENTSO-e) gives such a market published prices. A
  forecast never fills a slot that has a real price (`_combine`, per slot of the common interval, also across providers), and the
  price history stores only real prices.
- An entry that already has such a sensor (set up before, restored from a backup, or whose own price source was removed
  later) gets the repair `energypriceforecast_forecast_only` (`DependencyWatcher._check_forecast_only()`), only once the
  sensor has had only a forecast for about an hour (late prices make none). A sensor that can't be judged (unavailable,
  unknown, no price rows yet) is never judged: it neither creates nor removes the repair. A repair the user ignored stays
  ignored (do not delete and recreate it). A restored state of a sensor that Energy Price Forecast no longer provides
  does not count while its entry runs. Do not use the `freezer` fixture to test it: the clock is `_utcnow()`.
- A price sensor whose unit makes its prices 100 or 1000 times off (`price_unit_error()`: a subunit such as ct/kWh on any price
  key except Wattcast's own price key, which accepts exactly ct/kWh, read as euro; per MWh or per Wh on an Energi Data Service,
  Spot Forecast, Elprisprognos, EPEX Spot, GE-Spot, Octopus Energy or Nord Pool key, per Wh on an Energy Price Forecast key, in the history too
  (`price_unit_divisors()`); Energy Price Forecast converts its own per MWh) is refused on the price
  page and, while running, gives no prices (`_wrong_unit()` in the providers: the next provider or the stored prices fill in,
  never a price that is too high). Its recorded history is left out of the what-if (`price_unit_divisors()` returns None), and the
  health check repair `price_unit_scale` names it (`CombinedPriceProvider.price_unit_problems()` judges a sensor of `PRICE_SENSOR_KEYS` by `price_unit_error()`
  of its key, so Wattcast's ct/kWh and Energy Price Forecast's own per MWh are not flagged, any other entity of a price integration
  (Strømligning, Octopus Energy, the tariffs) by its unit alone with `price_unit_wrong()`, and Wattcast's forecast sensor not at all (its provider
  checks its unit). Only the first group is refused by the providers; a Strømligning sensor in the wrong unit is flagged but still used; no grace time).
- Spot forecast providers (`prices/spotprices.py`: Spot Forecast, Elprisprognos, Wattcast; pure row parsers in
  `prices/spot_rows.py`, the same file in both repositories) give wholesale prices, not retail prices. Buy price = spot ×
  (1 + `spot_vat`/100) + `spot_surcharge`; sell price = spot − `sell_fee`, with no VAT or surcharge. `spot_vat` and
  `spot_surcharge` have no default (None), are asked on the fee page only when such a provider is chosen, and a provider
  without them raises instead of giving prices, never a default of 0 (a 0 entered by the user is valid). Published rows are
  real; a forecast row never fills a slot that has a published price, and today's hours without a published price are not
  planned. Their recorded state is a wholesale price, so `records_history = False` keeps the recorded state of the sensors out of the
  price history, the analysis and the what-if (the combined provider still names the sensor in messages); their published
  prices are still stored in the integration's own price database. Wattcast needs its forecast sensor too and gives
  ct/kWh; its unit rule is the only subunit exception, and its forecast sensor's `forecast_unit` must be ct/kWh too (else
  no prices, one log warning, no repair). A spot sensor with price rows but none published is
  refused on the price page (`price_sensor_no_published`), and while running it logs a warning once; there is no repair
  for it. Spot Forecast is not offered in Denmark. Fixtures are synthetic, built from the documented row shapes: no running
  installation of the three integrations was checked.
- Providers of published prices (`prices/publishedprices.py`: EPEX Spot, GE-Spot, Octopus Energy, Nord Pool; pure row parsers of
  the first three in `prices/published_rows.py`, the same file in both repositories) read the prices the user's own integration
  publishes. They have no forecast (`has_forecast = False`: alone they are refused by `provider_errors()`), so a provider with a
  forecast fills the days after tomorrow; they stand before the estimating providers in `PRICE_PROVIDER_PRIORITY`. The buy price is
  the price of the sensor as it is (no VAT or surcharge of ours). The sell price is the wholesale price less `sell_fee`, never the
  buy price, and it exists only where the user's integration has a source for it: EPEX Spot's Market Price sensor, the required
  second Nord Pool sensor, GE-Spot's `raw_value` only from a `data_source` that is known and has a market price (not Strømligning
  or Amber), Octopus Energy's export rates. A missing sell sensor means no sell prices (never the buy price). Octopus Energy
  without an export tariff sells at 0 less the fee (`sells_nothing`) in every hour of the buy prices, also on the days a
  provider with a forecast fills (`_apply_sells_nothing()` in `CombinedPriceProvider`). Only today's and tomorrow's rows are
  prices (GE-Spot's tomorrow only while `tomorrow_valid is True`). The real prices of a provider are the price of the whole
  hour: `_combine()` repeats an hourly price over every slot of the common interval, so the quarters of a later provider never fill a
  slot of it. Octopus rates
  are pounds (an event entity has no unit; a rate above `OCTOPUS_MAX_PLAUSIBLE` is pence, so no prices), `records_history` is
  False for Octopus Energy (the state of its entity is a time), and GE-Spot has no recorded sell sensor. The price pages refuse
  a sensor without price rows (`price_sensor_no_rows`), the same sensor as buy and sell price (`price_sensor_same_sensor`), a
  sensor in the wrong role (`price_sensor_wrong_role`: EPEX Spot's Market Price and Total Price swapped, an Octopus Energy
  import event as export or the other way, found from the entity registry's unique id) and EPEX Spot's non-exchange sources
  (`price_sensor_not_exchange`). The Nord Pool provider is a subclass of the Energy Price Forecast provider and must not inherit
  its sell fallback, its MWh conversion or its checks. Fixtures are synthetic, built from the upstream integrations' code: the
  unit of GE-Spot's `raw_value` and Octopus Energy's entity ids and date formats were not checked against a running installation.
  A key twice in a json translation file is read without a word (the last one wins); `tests/test_published_providers.py`
  checks that no file has one.
- What-if without a sell sensor (a restored backup may lack it): Energy Price Forecast follows the live prices (the history of
  the buy sensor less the sell fee), but is not given a module payback or year estimate (`sell_price_from_buy_sensor`, the retail
  price is higher than what a sale pays). Energi Data Service does not: its tariffs attribute is not recorded (its
  `_unrecorded_attributes`), so the buy history cannot be turned into a sell price and the what-if keeps reporting that the sell
  price history is not available.
- The country of the prices (`price_country`, stored as `prices.country`, default `"DK"`: an entry, backup or import from before it
  is Danish, no migration) is chosen next to the language on the General page of the options and, in the config flow, on its
  own `country` page right before the price provider page (the country first, then the providers), not on the price provider
  page. Home Assistant's country is only a suggestion there for a new entry (a restored backup or an import shows its own as the
  default), and a new entry outside DK starts with Energy Price Forecast. The price provider page lists only the providers that have prices in the country (`providers_for_country()`) and
  names the others in its description (`unavailable`). Changing the country on the General page leads on to the price pages when a
  chosen provider has no prices in the new country. `provider_errors()` (`prices/availability.py`) refuses a choice with no forecast, and a
  provider with no prices in the country as a second guard (the selector rejects it first); it runs only on the price provider
  page, never on an unrelated page of a running setup. Every provider class has `countries` (None = any; Energi Data Service: the regions of its own integration; Strømligning: DK) and `has_forecast` (a
  property of the provider, not of what its sensor shows now).
- A price provider is confirmed per country only by hand: `confirmed_countries` on its class (a subset of `countries`),
  added when the user has seen it work with real data. Passing the self test or the automated tests (synthetic fixtures)
  never confirms one; so far Energi Data Service and Strømligning in DK are confirmed (user decision). The price provider
  page marks the offered providers not confirmed in the country with 🧪 before the name; they stay selectable. It lists the
  providers in two open sections by `has_forecast` (`provider_sections()` in `prices/availability.py`): with a forecast, and published prices only, each in the order of
  `PRICE_PROVIDER_PRIORITY`. Its description and the section texts stay short, and the section texts name no providers (a country
  may not have them). The description shows the offered providers in
  the order of `PRICE_PROVIDER_PRIORITY` (`priority`). The mark comes from the option value `<provider>_unconfirmed`
  (`ProviderSelector` in `flow/schema.py`), translated in `selector.price_providers` as 🧪 followed by the provider's name, so the
  list stays in the user's own language: keep `translation_key`, accept the plain name too, and store only the plain name.
  `prices/verification.py` (the same file in both repositories) is the self test: each provider is judged on its own prices,
  never the combined ones; the results are True, False or None, and None (tomorrow's prices before 20:00, a buy or sell sensor
  without a state, no sell prices unless the provider sells nothing) never counts as passed. Today only the slots from the
  current hour count; a False becomes None while another sensor of the provider has no state; a failed update fails even with
  old prices left. The prices are judged at the moment they were read (Cable Juice Planner: `last_update`, its price snapshot
  can be older than the job). A missing last hour of the day is tolerated (a market day can end before the local day): today
  until 20:00, in tomorrow's prices also after it. It only reports, in the
  attribute `price_check` of `sensor.battmind_powerwall_action`, keyed by provider name with no entity ids; no price, plan,
  repair or store depends on it. The price check
  on the Settings view links to the issue form `.github/ISSUE_TEMPLATE/price_provider_verification.yml` (*Price Provider
  Verification Report (Confirmed)*), pre-filled with only the provider names, the country, the versions and the `price_check`
  results (no entity ids, prices or other user data); the integration sends nothing itself, and the form's field ids must
  match the link's query parameters. A report never confirms a provider by itself: the maintainer adds the country to
  `confirmed_countries`.
- `sell_fee` is stored positive (`abs()`) and is always subtracted.
- Every price chart (`dashboard/prices/<provider>.yaml`, one per provider) is the Energy Price Forecast chart with its data source replaced by
  one inline `adapt()`: it reads the prices the plan is made on, whatever provider gave them, from attribute `price_chart` of
  `sensor.battmind_powerwall_action`. The attribute is set with every price update (`Pricing.update_grid_prices()`, `Pricing.price_chart_rows()`,
  `prices/chart_rows.py`, the same file in both repositories), not by the plan, which may not run: one row `{time, buy, sell, forecast}` per
  price period (15 minutes or an hour) for 7 days, real and forecast in one list. The sell price in it has `sell_fee` off already, so a
  chart never takes it off again (a test checks that no chart has the placeholder); it is `null` where the plan has none (a sell price from the
  price history is one), never the buy price or 0. The forecast zone starts at the first forecast period and is empty without one. A chart's
  card lists the plan entity in `entities` (an empty list never re-renders the card, so the chart would stay as it was at page load), and
  `dashboard_entities()` of a provider maps only the sensor of its restart card. The charts are generated from one template and differ only in
  the provider's entity and name (a test pins the adapter): change the template, regenerate all of them, and keep the attribute, the
  charts and their tests in step. `../ha-dev/tools/chart_check.py` runs the charts in headless Edge against the real combined provider
  (Strømligning and Energi Data Service have no stubs there, so they are not run).
- `plan_inputs_hash()` includes the sell prices, so new sell prices replan.

### Debug export

- The export is offered in the dashboard's Settings view, not as a badge on
  the overview. Where it is written and how to replay a file handed in by the
  user: `docs/development/debug-export.md`.
- The `Redactor` anonymises entity ids, names, locations, and secrets. Keep
  `management/debug_export.py` in sync with the CJP copy.
- Besides the coordinator data the export holds `blueprints` (the bundled
  blueprint's automations from `automations.yaml`, read only, with inputs and
  automation state; the installed blueprint files with their content only
  when they differ from the bundled one), `devices` and `integrations`
  (platform, manufacturer/model/firmware, integration version and config
  entry states). Device and config entry names, serial numbers and
  identifiers are never exported. The `Redactor` names blueprint input
  entities after their input and keeps registered service names
  (`number.set_value`), except `notify`/`script` services. Since 2026.10 a
  device has `config_entry_id`; `config_entries` is deprecated, so support
  both (minimum is 2025.12).

### Health checks (repairs)

- `management/health_checks.py` raises the repairs `entities_missing`,
  `entity_units`, `blueprint_automation_lost`, `plan_not_followed` and the
  value scale repairs below (`power_without_unit`, `battery_level_fraction`,
  `price_unit_scale`). They
  only inform; none of them controls anything. `plan_not_followed` reads the
  published `plan_check` attribute and needs `PLAN_MIN_CHECKED_HOURS`,
  `PLAN_MIN_MISMATCHES` and a match below `PLAN_MAX_MATCH_PERCENT`; its lines
  show charged/discharged kWh, so an opposite action is visible there. Being
  unavailable is not a repair. `engine/engine.py` `is_entity_available()` is
  called only for the house consumption sensor: from `inverter_available()`
  (solar configured) in the planning run (`local_energy_prediction()`) and at
  minute 59 of every hour, and from `power_values_to_db()` at minute 59 of every
  hour. Each call that finds the sensor unavailable logs a warning. Once more
  than 30 minutes have passed since the first such call (counted per
  integration, reset by any call that finds the sensor available), it posts a
  persistent notification. The sensor needs an entity registry entry, which
  gives its integration; a YAML sensor without a unique ID gets no notification.
- Delete an issue only on a positive answer (the problem is gone), never on an
  unclear one (unavailable/unknown state, unreadable `automations.yaml`).
  Delete + create resets the user's "Ignore", while `async_create_issue` on an
  existing issue keeps it. Stop, unload and reload never delete the issues;
  only removing the entry does, and it also deletes the `.health` store. An
  unreadable store starts with the safe defaults.
- The store `battmind.<entry_id>.health` is deliberately not in `DB_NAMES`, so
  it is not in BattMind's own backup (`management/backup.py`) nor in the debug
  export. A full Home Assistant backup still contains it.
- Units follow `power_unit_known()` in `utils/power.py` (the unit without an
  `h` is mW, W, kW, MW or GW, or no unit); the battery level only in %. CJP
  has the same rule. BattMind reads every power value, statistics included
  (`units=None`), through `unit_factor_to_w` in `utils/power.py` (the config
  flow's own copy in `flow/detection.py` doesn't strip the `h`, so a kWh
  sensor just isn't suggested). Keep the rule in line with the code that
  reads each sensor.
- `blueprint_automation_lost` needs the automation to have been seen on first,
  so users with their own automation never get it. Automations without an
  `id` or outside `automations.yaml` (packages, YAML mode) are not found. A
  registry-disabled automation counts as off.
- Value scale repairs (decided with the user: inform only, no fix flow and no
  conversion, because a blueprint can read the same sensor itself):
  `power_without_unit` (a power sensor without a unit whose values over
  `POWER_SCALE_DAYS` stay at most `KW_LOOKING_MAX` with fractions, so kW read
  as W), `battery_level_fraction` (every value 0..1 with fractions) and
  `price_unit_scale` (a price entity in a subunit, per MWh or per Wh, judged by
  its unit, and for the price sensors also the key it is configured under (`price_unit_error()`), through `CombinedPriceProvider.price_unit_problems`). The
  verdicts are in `management/value_checks.py`, the same file as in CJP
  (change both together, with `tests/test_value_checks.py`): True, False or
  None, and None never clears a repair. History is read in the recorder's
  executor, at most once per `VALUE_RECHECK` per sensor, and only for a
  sensor whose current state could be in the wrong scale. The flagged sensors
  are kept in the `.health` store (`values_flagged`, and `values_known` for
  the repairs whose list is known); with a known list for that repair the
  repair goes when no sensor is flagged any more. A repair's list is known
  only when it was read intact from the store or one check judged every
  sensor of that repair (True or False), or when the repair is not in the
  issue registry (nothing to forget: a fresh start knows its empty list). A
  lost store, a store without `values_known` or a malformed entry while the
  repair is open leaves it unknown, and then every sensor must be judged fine
  before the repair goes.
  `price_unit_scale` keeps no list: it goes only when every price entity was
  judged.
- `consumption_frozen`: only the house consumption sensor (`home.entity_ids.power_consumption_entity_id`). Raised when its
  value has not changed for 6 h (`FROZEN_AFTER`, by `last_changed`); the hourly check makes it visible 6 to 7 h after the
  last change. Other sensors are not checked this way, because a steady reading can be real for them. The frozen value,
  its entity and its time are kept in the `.health` store (`consumption_frozen`: entity, value, since), so a restart keeps
  the repair and its earlier time. Startup grace (`CONSUMPTION_GRACE`: from a start or reload of the entry until the first check that is at least
  30 min later and reads a number, so in practice up to about an hour): nothing
  is deleted, the stored value and time are not replaced, and a lost store gets no baseline. An existing repair is raised
  again from the stored values (its Ignore is kept); a new one may be raised by the 6 h rule only when no repair for this
  sensor is open. After the grace the repair is deleted only on a positive answer: a finite number different from the
  stored one (at a check, or at once when the listener sees it while Home Assistant runs), the sensor no longer configured,
  or a repair that names another sensor while the stored value is lost (the two configuration answers at the first check
  after the grace). Unavailable, unknown and text keep it. Without a stored value (lost or malformed store), an open repair
  that names this sensor takes the value seen at the first check after the grace as its baseline; a repair whose
  placeholders are unknown (the issue registry restores them as None after a restart) is baselined and raised only by the
  6 h rule.

## Tests and validation

On Windows run the suite with `../ha-dev/run_tests_windows.ps1 battmind
[pytest args]`; the Docker run `../ha-dev/dev.ps1 test battmind [pytest args]`
is the reference, and in the devcontainer `python -m pytest` works directly.
Run the focused test file(s) first, then the full suite when practical. Do not
create the venv on a network drive; it installs extremely slowly. Tests must
not depend on the run being slow: wait for real conditions or real time, not a
fixed number of `sleep(0)` yields. `tests/conftest.py` has `async_advance()`
for timers. When calling the test venv's Python directly, put
`../ha-dev/windows` (shims) on `PYTHONPATH`. Test the PyScript import with
`../ha-dev/dev.ps1 import-legacy` before adding the integration.

Keep test runs short (user decision). While fixing, run only the test files
that cover the change (`'-n' '0'` for a single file); after a failure rerun
only what failed (`--lf`, `-x` to stop at the first failure). Run the full
suite once, after the last change and before reporting the task done: not
after every fix or review round, and in a multi-agent task only once, by the
main agent, not by each subagent. `--durations=25` shows the slowest tests.

The devcontainer, the Windows venv and its shims, debugging in one process
(`'-n' '0'`), the pinned test dependencies, and where the ha-dev seed and
simulator data live: `docs/development/testing.md`.

The GitHub validation workflow runs Home Assistant `hassfest` and the HACS
integration validation action. Run the relevant checks when available for
changes to integration metadata, translations, or HACS structure. Do not claim
a check passed unless it was actually run. No repository Ruff configuration
or Ruff CI check is defined; follow the existing style rather than introducing
an unconfigured formatter or automatic rewrite.

## Before finishing a change

1. Review the diff and confirm it contains only intended changes.
2. Run the focused tests; run the full pytest suite for shared/core behavior
   changes when practical.
3. Run relevant Home Assistant/HACS validation for metadata or packaging
   changes, if available.
4. Check translations, documentation, and compatibility requirements above.
5. Report which checks ran and their results, and disclose any validation that
   could not be run.
