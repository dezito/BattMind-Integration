# Code map

For contributors and coding agents. Linked from `AGENTS.md`.

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
│   ├── storm.py / storm_warning.py      Storm warning: pure rules (window, need, level per hour) and the engine part (snapshot alert, floors, status, the rules' fix)
│   ├── full_charge.py                   Full charge: last full charge detection, due state and deadline, requirement for the rules and the optimiser
│   ├── diagnostics.py                   Engine debug information
│   ├── energy/                          Price, solar, forecast, and energy inputs
│   ├── history/                         Recorder queries and stored history
│   ├── analysis/                        Results, recording, and published analysis
│   │   └── plan_check.py                Plan vs reality: observation only, stored in `analysis_db["plan_check"]`
│   ├── automation_test.py               Test of the user's automation: step verdicts, held action, markdown result
│   │                                    (the runner is `coordinator/automation_test.py`; the held action ends in `planning.py`)
│   └── battery.py / state.py            Battery model and planning state
├── prices/                              Price providers; CombinedPriceProvider is the only entry point
├── management/                          Storage, backups, dashboard, and blueprint setup
│   ├── health_checks.py                 HealthWatcher: the health repairs of an entry (sensors, units, value scale, prices, plan, frozen consumption)
│   ├── value_checks.py                  Pure checks of sensor values in the wrong scale (shared with Cable Juice Planner)
│   └── notifier.py                      Push notifications to the chosen notify services: conditions, debounce, once, daily summary (own store `.notices`)
├── utils/                               Shared codecs, formatting, and calculations
│   ├── solar_forecast.py                Forecast.Solar / Helios Forecast energy platform -> hourly solar rows (shared with Cable Juice Planner)
│   └── capacity.py                      Battery capacity bounds measured from the battery power and level (`analysis_db["capacity"]`, always); the plan counts on them only through `battery_size_used()` in `engine/battery.py` with the switch `learn_battery_capacity`
├── sensor.py / number.py / switch.py    Main HA entity platforms
├── select.py / button.py / text.py      Other HA entity platforms
├── entity.py / repairs.py               Shared entity base and repair flows
├── translations/ texts/                 HA strings and user-facing descriptions
├── dashboard/ blueprints/               Bundled dashboard and automation blueprint
│   └── dashboard/prices/                One price chart per price integration (the key of PROVIDER_CLASSES)
└── manifest.json                        HA integration metadata

tests/                                   Pytest suite
├── conftest.py                          Shared fixtures and simulated HA entities
├── test_engine.py / test_planner_rules.py  Engine and planning behavior
├── test_solar_optimizer.py / test_efficiency.py  Solar and efficiency calculations
├── test_capacity.py                     Measured capacity bounds (runs, bounds, restart, jumps) and the size the plan counts on (switch, efficiency sources, floor, hash)
├── test_notifier.py / test_push_sources.py  Push notifications: states, debounce, delivery, restart; the sources (engine, health checks, start, options)
├── test_analysis.py / test_debug_states.py      Analysis and published state
├── test_hindsight_score.py                         Hindsight score: best plan, baseline, unscored days, real hourly prices
├── test_recorder_history.py             Recorder fallbacks for what-if data
├── test_config_flow.py / test_init.py           Setup and lifecycle
├── test_backup.py / test_debug_export.py        Persistence and export
├── test_dashboard.py / test_blueprint_*.py       Dashboard and blueprint behavior
├── fixtures/                            Legacy PyScript config for the import tests
└── debug_replay.py                      Debug/replay helper

docs/                                    User documentation and behavior details
docs/development/                        Contributor and agent reference (this folder)
.github/workflows/validate.yml           CI: hassfest and HACS validation
.github/workflows/release.yml            Attaches battmind.zip to published releases
.github/workflows/release-notes.yml      Draft release with notes written by Claude
pytest.ini / requirements_test.txt       Pytest configuration and pinned test deps
```

Use this as a map, not an exhaustive inventory; check the current files before
editing.
