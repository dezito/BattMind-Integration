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

```text
custom_components/battmind/             The HA integration (HACS payload)
├── __init__.py                          Integration setup and platform forwarding
├── coordinator/                         Shared data, updates, events, and lifecycle
├── config_flow.py / flow/               Setup/options flow, pages, and schemas
├── const/                               Configuration, settings, and signal constants
├── engine/                              Planning and battery analysis
│   ├── engine.py / planning.py          Calculation orchestration
│   ├── optimizer.py / planner/          Optimization and charge/discharge decisions
│   │   ├── what_if.py                   What-if simulator calculation
│   │   ├── replay.py                    What-if replay of the planner on the history
│   │   └── future/                      Future-period planning rules and context
│   ├── pricing.py                       Price helpers, incl. `sell_value()`
│   ├── diagnostics.py                   Engine debug information
│   ├── energy/                          Price, solar, forecast, and energy inputs
│   ├── history/                         Recorder queries and stored history
│   ├── analysis/                        Results, recording, and published analysis
│   └── battery.py / state.py            Battery model and planning state
├── prices/                              Price providers; CombinedPriceProvider is the only entry point
├── management/                          Storage, backups, dashboard, and blueprint setup
├── utils/                               Shared codecs, formatting, and calculations
├── sensor.py / number.py / switch.py    Main HA entity platforms
├── select.py / button.py / text.py      Other HA entity platforms
├── entity.py / repairs.py               Shared entity base and repair flows
├── translations/ texts/                 HA strings and user-facing descriptions
├── dashboard/ blueprints/               Bundled dashboard and automation blueprint
└── manifest.json                        HA integration metadata

tests/                                   Pytest suite
├── conftest.py                          Shared fixtures and simulated HA entities
├── test_engine.py / test_planner_rules.py  Engine and planning behavior
├── test_solar_optimizer.py / test_efficiency.py  Solar and efficiency calculations
├── test_analysis.py / test_debug_states.py      Analysis and published state
├── test_recorder_history.py             Recorder fallbacks for what-if data
├── test_config_flow.py / test_init.py           Setup and lifecycle
├── test_backup.py / test_debug_export.py        Persistence and export
├── test_dashboard.py / test_blueprint_*.py       Dashboard and blueprint behavior
├── fixtures/                            Legacy PyScript config for the import tests
└── debug_replay.py                      Debug/replay helper

docs/                                    User documentation and behavior details
.github/workflows/validate.yml           CI: hassfest and HACS validation
.github/workflows/release.yml            Attaches battmind.zip to published releases
.github/workflows/release-notes.yml      Draft release with notes written by Claude
pytest.ini / requirements_test.txt       Pytest configuration and pinned test deps
```

Use this as a map, not an exhaustive inventory; check the current files before
editing. Prefer the narrowest relevant module and test file over changing a
broad layer for a local behavior change.

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
- Preserve compatibility of config-entry options, entity IDs/unique IDs,
  stored data, backups, and migration from the PyScript version. Changes to
  these formats or identifiers require a migration/compatibility path and
  documentation.
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
  Strømligning before Energi Data Service) and the prices themselves. Other
  code calls its methods (the coordinator via `coordinator.price_provider`)
  and never reads price entities, config keys or provider attributes itself.
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
- Add regression tests for bugs found and for scenarios with missing or
  incomplete data.
- Use Home Assistant data for validation when an environment is available and
  access is approved. Keep credentials and sensitive data out of code, logs,
  and reports.

## Project notes

Agents record lasting decisions and findings for this repository here (not in
private agent memory). Update or remove a note when it stops being true.

- Keep the worker-thread design so calculations never block HA:
  single-thread executor, snapshot -> job result.
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
  (`#-buy-to-sell`).
- Releases: `hacs.json` has `zip_release` with `filename: battmind.zip`, so
  HACS installs from that release asset (its download count is the install
  count). `.github/workflows/release.yml` builds it from
  `custom_components/battmind/` (files at the zip root) when a release is
  published; run it by hand with a tag for a release that lacks the zip. It
  sets `manifest.json` `version` in the zip from the tag (`v1.0.9` -> `1.0.9`),
  so the version in the repository need not be bumped by hand.
  `.github/workflows/release-notes.yml` (*Draft release*, run by hand with the
  new tag) lets Claude write the notes from the changes since the previous
  release and creates or updates a draft release; it never touches a
  published one. It needs the secret `CLAUDE_CODE_OAUTH_TOKEN`. Keep its
  prompt in sync with the CJP copy. A tag with a letter (`-beta.1`, `b1`,
  `rc1`) becomes a pre-release, compared with the latest release; a stable tag
  is compared with the latest stable release. Both workflows run
  `.github/check_version.py`, which rejects versions Home Assistant won't load
  (e.g. `1.0.7.1-beta.1`; use `1.0.7.1b1`).

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
  and `min_sell_kwh` apply only to sales to the grid (decided with the user
  2026-11-05), never to charging or self consumption. Exception:
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
  `optimize_with_rules` forbids the action and re-optimises). A held-back hour
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
- Decided with the user 2026-11-08:
  - `most_profit` checks `min_total_profit` against the profit shown in the plan (sell price − battery kWh cost
    incl. loss and wear), like `sell_excess` and Buy to sell. The max-age sale (`force_powerwall_max_hours_old_discharge`)
    still compares with using the energy (the cost of old energy is sunk): do not "fix" that to the shown profit.
  - The forecast adder (`PRICE_ADDER_DAY_BETWEEN_DIVIDER`) makes a forecast sale worth less: it is subtracted from a
    forecast SELL price, and added with a fixed solar sell price (`sell_adder_sign()` in `prices/base.py`, also in
    `Pricing._database_prices`). A forecast buy price is raised.
  - The 80 % margin of `optimize_solar_charging` (`margin_delta` of `_optimize_solar_blocking`) counts only the sun:
    a grid charge after the first blockable hour does not refill the battery in that test.
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
  time of day from the latest known day (never 0 = too high a sell price).
- Energi Data Service: a sell sensor with a `tariffs` attribute includes
  them. A sell sensor without it is a raw price (tariffs in the price = 0); the
  selling costs (net and system tariff) then come from the buy sensor's
  `tariffs` attribute (`get_sell_costs()` / `eds_sell_costs()`, what-if:
  provider `sell_costs`).
- `sell_fee` is stored positive (`abs()`) and is always subtracted.
- The dashboard's sell price series (Strømligning and Energi Data Service
  charts) must use the same formula and the configured sell entities
  (`@@sell_fee_value@@` is the configured fee). Keep it in sync with
  `calculate_sell_price()`.
- `plan_inputs_hash()` includes the sell prices, so new sell prices replan.

### Debug export

- `management/debug_export.py`, service `battmind.export_debug`, diagnostic
  button `export_debug`, and a discreet "Problem or error?" section at the
  bottom of the last column of the dashboard's Settings view (not a badge on
  the overview). Writes `<config>/battmind_debug/<prefix>_debug_<ts>.json.gz`
  (newest 5 kept) plus a persistent notification with a signed 24 h download
  link and a prefilled `.github/ISSUE_TEMPLATE/bug_report.yml` link.
- The `Redactor` anonymises entity ids, names, locations, and secrets. Keep
  this file in sync with the CJP copy.
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
  both (minimum is 2025.1).
- To analyse a file handed in by the user, run from the repo root:
  `PYTHONPATH=../ha-dev/windows %LOCALAPPDATA%/ha-dev-tests/venv/Scripts/python.exe tests/debug_replay.py <file> [failed|planning|<index>]`
  (default: failed, else planning, else last). Older exports may
  lack `planning_job`/`price_data` and replay less
  faithfully.

## Tests and validation

### Devcontainer

`../.devcontainer/devcontainer.json` opens the workspace folder (both
integration repos and `ha-dev`) in a container with Home Assistant installed.
Inside it, run `python -m pytest` from the repository root; it is Linux, so no
shims are needed. The dev HA runs on `http://localhost:8123`; after a code
change run `bash ha-dev/devcontainer/restart.sh`, and
`bash ha-dev/devcontainer/restart.sh reset` wipes the state and seeds again.
Seeding creates a BattMind entry wired to the simulator entities.

The seed data and simulator entities are in `../ha-dev/devcontainer/seed.py`
(`SIM_ENTITIES`/`SEEDS`), the simulator in
`../ha-dev/config/packages/simulator.yaml`, synthetic history and backups in
`../ha-dev/devcontainer/history.py`, and HA config in
`../ha-dev/devcontainer/configuration.yaml`. Test the PyScript import with
`../ha-dev/dev.ps1 import-legacy` before adding the integration.

### Outside the devcontainer, on Windows

Run the suite with `../ha-dev/run_tests_windows.ps1 battmind
[pytest args]`. The first run installs Python 3.14 and the test requirements
in `%LOCALAPPDATA%\ha-dev-tests\venv`; the shims in `../ha-dev/windows`
(fcntl, resource, a pytest plugin that disables the socket guard) must be on
`PYTHONPATH` when calling that venv directly. The system Python 3.11 lacks
HA/astral. Do not create the venv on a network drive; it installs extremely
slowly. The Docker run `../ha-dev/dev.ps1 test battmind [pytest args]` is the
reference. The dev HA runs locally in Docker Desktop, not on a remote server;
see `../ha-dev/README.md` (`ha-dev/dev.ps1 up|restart|reset|test`).
`tests/conftest.py` has `async_advance()` for timers.

The test configuration is in `pytest.ini`; test dependencies are pinned in
`requirements_test.txt`. Those dependencies target the Home Assistant
2026.10.x / Python 3.14 environment noted in that file.

From the repository root, run the focused test file(s) first, then the full
suite when practical:

```bash
python -m pytest tests/test_<area>.py
python -m pytest
```

If test dependencies are not installed, install the repository's declared
test requirements in the active environment:

```bash
python -m pip install -r requirements_test.txt
```

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
