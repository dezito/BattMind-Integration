# Actions and battery control

<!-- {% raw %} -->

BattMind sends **no commands** to the inverter. It sets `sensor.<prefix>_powerwall_action` to what the
battery should do right now, and your automation makes the inverter do it.

## The six actions

| Action | The battery should | Typical inverter setting |
|---|---|---|
| `battery_stopped` | stop charging and discharging because the configured temperature is unsafe or unavailable | stop forced modes, set charge and discharge power to 0 |
| `grid_charging` | charge from the grid now | forced charge from the grid at full power |
| `force_discharge` | discharge to the house and sell the rest to the grid | forced discharge at full power |
| `discharge_allowed` | **run normally** – BattMind doesn't really control the battery in this state | the inverter's normal self-consumption mode: charge from solar, discharge to the house |
| `force_solar_only_charging` | charge from solar, but not discharge – the energy is saved for later | discharge power 0, no grid charging, charge power max |
| `blocked_solar_charge` | not charge from solar – the surplus is sold | charge power 0, discharge power max |

`discharge_allowed` is the normal state. Most hours of the day are in it, and the battery simply does what
the inverter does on its own. BattMind only steps in with the other five actions.

Why the battery is in an action is shown in `sensor.<prefix>_current_charging_rule` (e.g. *Charging up to
💸*) and in the plan on the dashboard.

### When the inverter can't run an action

Not every inverter can run all the actions. Select the ones yours can't run in the configuration
(*Configure → Home battery → controllable modes*). `discharge_allowed` and `battery_stopped` are always
used. BattMind then never plans an unsupported action, neither with the
[optimiser](rules/optimizer.md) nor with the rules, and the rules that need it count as off:

| Unsupported action | Rules that count as off |
|---|---|
| `grid_charging` | cheapest periods (and fill up), most expensive periods, required before max level, negative prices, buy to sell, full charge |
| `force_discharge` | maximise profit, sell excess kWh, before the sun, buy to sell. *Max. kWh age* still uses old energy, but can't sell it (selling needs force discharge) |
| `force_solar_only_charging` | profit only, prioritise by energy cost, maximise profit. These hold energy for a later hour, which needs a battery that can stop discharging |
| `blocked_solar_charge` | optimise solar charging |

The switches keep their value and get the attribute `unsupported_action`, so they work again when you clear
the selection. The generated dashboard hides them under **Settings**, together with the settings only those
rules use (the cheap price periods with `grid_charging`), and a section left without settings is hidden
with its *Read more*. A dashboard you have edited in the UI is not changed: press *Reset dashboard* to get
the generated one. The what-if simulator uses the same limits. If an unsupported action is somehow still due,
BattMind runs `force_solar_only_charging` instead of `grid_charging` or `blocked_solar_charge` (if the
inverter supports it), and `discharge_allowed` otherwise. The planned hours in the attributes follow it: with
`force_solar_only_charging` unsupported, `force_solar_only_charging_timestamps` is empty and the hours that
would have been in it are in `discharge_timestamps`.

Leave the blueprint's field for an unsupported action empty.

## The blueprint

The blueprint **BattMind - control the home battery** is installed with the integration and works with all
inverters, because you choose what happens with Home Assistant's normal action editor.

Others may already have set it up for your inverter: see
[Blueprint setups](https://github.com/dezito/BattMind-Integration/discussions/categories/blueprint-setups). Paste a
setup under *Settings → Automations & scenes → Create automation → ⋮ → Edit in YAML* and change what is
marked `TODO`. When yours works, share it there with **New discussion**, so the next one with the same
inverter can copy it.

### BattMind

| Input | Meaning |
|---|---|
| BattMind action | `sensor.<prefix>_powerwall_action` |
| Repeat interval | run the current action again with this interval (default 5 minutes, 0 = off). See [below](#repeat-interval) |
| Custom triggers | extra triggers that run the current action again, e.g. a change on an inverter entity |

### Actions

One field per action: **Stop battery**, **Charge from grid**, **Forced discharge**, **Discharge allowed
(normal operation)**, **Solar charging only** and **Solar charging blocked**. Typically *Number: Set value* on the battery's max.
charge/discharge power, *Switch: Turn on/off*, or a service from the inverter integration. Fields left
empty do nothing.

The steps of an action run right after each other. If the inverter doesn't take commands that close together
(e.g. over Modbus or a slow cloud), add a *Delay* step (e.g. 2 seconds) between them in the action. That is
safe: if BattMind changes action during the delay, the run starts over and sends the whole new action.

Tips per action:

- **Stop battery** - stop forced charge/discharge and set both maximum powers to 0. This action must be
  configured before you select a temperature sensor. It is a secondary safeguard, not a replacement for
  the battery management system or the manufacturer's temperature protection.

- **Charge from grid** – if the inverter's forced charge needs a duration or a target level, use a long
  duration (e.g. 120 min) and a target of 100 %. BattMind stops the charge by changing action when the
  planned kWh are in.
- **Forced discharge** – the same: BattMind changes action when the sale is over.
- **Discharge allowed** – stop any forced charge/discharge and set the charge and discharge power back to
  max. Without this, a forced mode from an earlier action keeps running.
- **Solar charging only** – stop forced charge/discharge, set the discharge power (and grid charging) to 0,
  keep the charge power at max.
- <a id="blocked_solar_charge"></a>**Solar charging blocked** – set the charge power to 0 and keep the
  discharge power at max. It is only used when [optimise solar charging](rules/solar.md#-optimise-solar-charging)
  is on, and only in hours with solar surplus.

### EV (optional)

Without this, the battery may discharge into the car while it charges – the car is just another consumer.
If you don't want that:

| Input | Meaning |
|---|---|
| EV charger power | one or more power sensors of the chargers, in W or kW |
| Car is charging above | above this power (default 1000 W) the car counts as charging. The change has to last 30 seconds |
| While the car is charging | runs instead of *Discharge allowed* and *Solar charging blocked* while the car charges, e.g. discharge power 0. *Charge from grid*, *Forced discharge* and *Solar charging only* still follow BattMind |
| Allow discharging into the car when | while this entity is on, *Discharge allowed* runs as normal, e.g. Cable Juice Planner's `switch.cjp_<car>_powerwall_discharge_above_needed` |
| Only above this battery level | together with the battery level sensor: only allowed while the battery is above this value, e.g. `number.cjp_<car>_ev_charge_after_powerwall_battery_level` |

Tip: with Cable Juice Planner, BattMind leaves the car's charger out of the consumption forecast by itself
(**Ignore Cable Juice Planner chargers** on the consumption page, on by default). For any other charger, add its
power sensor under **Ignored consumers** in the BattMind setup, but only when the house consumption sensor includes
that charger: a charger on its own meter is not in it, so there is nothing to subtract.

### Repeat interval

By default the current action is sent again **every 5 minutes**. Without a repeat, the action is only
sent when BattMind changes it – so if a command is lost (the inverter's cloud doesn't answer), the inverter
changes its mode by itself, or the settings are changed elsewhere (e.g. in the inverter's app), nothing puts
it right until the next change of action. The repeat catches that within 5 minutes.

Change it if your inverter needs something else:

- **Modbus:** some batteries fall back to their own mode if no command arrives. Repeat every 30 seconds.
- **Huawei Solar:** forced charge/discharge runs for a given duration (e.g. 120 min) – the 5 minutes renew it
  long before it runs out.
- **0 = off:** only if every command reliably sticks, and the inverter never changes mode on its own.

The repetition starts over every time BattMind changes action, and also when Home Assistant starts or the
automations are reloaded (not with 0).

## Your own automation

If you'd rather not use the blueprint:

```yaml
alias: BattMind - control home battery
triggers:
  - trigger: state
    entity_id: sensor.battmind_powerwall_action
    not_to: [unavailable, unknown]
actions:
  - choose:
      - conditions: "{{ trigger.to_state.state == 'grid_charging' }}"
        sequence: []  # e.g. force charging from the grid
      - conditions: "{{ trigger.to_state.state == 'force_discharge' }}"
        sequence: []  # e.g. force discharge to the grid
      - conditions: "{{ trigger.to_state.state == 'force_solar_only_charging' }}"
        sequence: []  # e.g. discharge power 0
      - conditions: "{{ trigger.to_state.state == 'blocked_solar_charge' }}"
        sequence: []  # e.g. charge power 0
    default: []  # discharge_allowed: normal operation, stop forced modes, charge/discharge power max
```

## Does the battery follow the action?

BattMind can't see your automation, but it measures what the battery does. After every hour it compares the
action it published with the energy the battery charged and discharged, and shows the result in the attribute
`plan_check` of `sensor.<prefix>_powerwall_action`:

| Action | Match | Mismatch |
|---|---|---|
| Charge from grid | the battery charged more than the sun alone could have charged it | it charged less than 0.1 kWh |
| Forced discharge | it discharged more than the house used | it discharged less than 0.1 kWh |
| Solar charging only | it did not discharge (it is never required to charge: there may be no sun) | it discharged 0.1 kWh or more |
| Solar charging blocked | it did not charge | it charged 0.1 kWh or more |
| Stop battery | it neither charged nor discharged | it did either, 0.1 kWh or more |

0.1 kWh in an hour is 100 W on average. *Discharge allowed* is normal operation and is not checked.

Normal operation also charges from a solar surplus and discharges into the house. A grid charge or a forced
discharge is therefore only a match when the battery's net flow in the hour (charged minus discharged, or the
other way round) was more than that; if it did only what normal operation does, the hour is skipped, because it
doesn't show that your automation ran the action.

An hour is **skipped, never counted as a match or a mismatch**, when it can't prove anything:

- **Changed:** one action didn't hold at least three quarters of the hour (BattMind ends a grid charge when the
  planned kWh are in).
- **Partial:** the actions are known for less than 90 % of the hour (a restart, or the first hour after a start).
- **No data:** the battery power, the consumption, an ignored consumer or, in daylight, the solar production had no
  numbers for part of the hour, or the battery level had none in the minutes before it (the level is read from the
  sensor itself). A lost sensor is never read as a battery that didn't move or as no sun.
- **Limit:** the battery was within 2 % of its highest level when the hour started (grid charging, solar charging
  blocked) or of its lowest level, the usage reserve included (forced discharge, solar charging only). On a
  battery smaller than 10 kWh the margin is larger, so there is room for the 0.1 kWh.
- **Nothing to show:** normal operation would not have moved the battery either, e.g. no consumption for
  "did not discharge", or no solar surplus for "did not charge".
- **Car:** an ignored consumer (e.g. the car, see *EV* above) used power during an hour with solar charging
  blocked. The blueprint runs *While the car is charging* then, so the hour isn't judged.
- **Summer time:** the hour that comes twice when the clocks go back.
- **Test:** you ran the [test of your automation](#test-your-automation) in that hour. The battery moved because of
  the test, not because of the plan.

| Attribute | Content |
|---|---|
| `days` | the days counted, today included (14) |
| `checked_hours` / `matching_hours` / `skipped_hours` | the hours of those days that were judged, that matched, and that were skipped. An hour with no action recorded or with normal operation isn't counted |
| `match_percent` | `matching_hours` of `checked_hours` in %, empty when no hour was judged yet |
| `latest_mismatches` | the three latest hours that didn't match, with the action and the kWh charged and discharged |

A low percentage means the battery didn't do what the action asked: the automation isn't connected yet, uses
the wrong entity, or the inverter ignores the command. Look at the blueprint's automation traces. The check only
observes: it never sends a command and never changes the plan. When the plan is clearly not followed, BattMind raises
a repair as well (see [Repairs](faq-troubleshooting.md#repairs)).

## Test your automation

The hourly check above waits for the plan to use an action. To find out right away whether your automation does
what each action asks, run the test: turn on **Test my automation** under *Settings* on the dashboard (the switch
`switch.<prefix>_automation_test_run`). BattMind then publishes one action after the other on
`sensor.<prefix>_powerwall_action`, measures what the battery does, and shows the result as a table under the switch
(the attribute `markdown` of `sensor.<prefix>_automation_test`). Click *Show the test result* to see it; it is open while the test runs.

The table lists every step from the moment the test starts, so you can see what will be tested and how far it is:
⏳ waits, ▶️ is tested now, and a step gets its result (✅, ❌ or 🔧) when it is done. A line above the table counts
the steps that are done, and when the test ends only the results are left. The steps are tested in the order of the
table below; *Stop battery* is last.

| Step | Passes when | Needs |
|---|---|---|
| Charge from grid | the battery charges at 200 W or more than the sun alone could | a battery that is not full |
| Forced discharge | it discharges 200 W or more than the house uses | a battery above the usage reserve |
| Discharge allowed | it discharges into the house, at least half of what the house lacks or the battery can give, and not more than the house uses; after a sale it fails if the battery still sells at least half of what it sold before | the house using 300 W or more than the sun gives, the battery not discharging just before or selling (discharging 200 W or more than the house lacked from the sun, as the forced discharge of the step before does), and no car charging |
| Solar charging only | it does not discharge | the house using 300 W or more than the sun gives, and the battery discharging (200 W or more) just before |
| Solar charging blocked | it does not charge | a solar surplus of 300 W or more, no car charging, and the battery charging (200 W or more) just before |
| Stop battery | it neither charges nor discharges (under 100 W) | the battery moving (200 W or more) just before (the test moves it first, see below), and the house using 300 W or more than the sun gives, or the sun giving 300 W or more than the house uses (normal operation would then move the battery) |

A step that asks the battery to stop or to do less can only be proven by a change: the measurement taken just before
the step has to show the opposite, otherwise the step is not proven (a battery that stood still already, or that
a step before left in a forced mode, would pass without your automation doing anything). *Stop battery* is the last
step, and the steps before it leave the battery standing still, so when the battery does not move just before it (and
the step would not be skipped for another reason, such as a full battery or no house load), the test first publishes
*Forced discharge* (or *Charge from grid* when that one can't be run) until the battery moves, up to 5 minutes, and then
publishes the stop over it. A battery that does not move even then leaves *Stop battery* not tested, with the reason
that the battery stood still already. *Discharge allowed* is proven from both sides: from a battery that
still sold (after *Forced discharge*), where the sale must end and the battery must still feed the house, and from a
battery that stood still (when the step before it was skipped or did not move it). Only a battery that fed the house already leaves it not proven,
and so does a battery that still gives a little more than the house uses after the sale ended (a battery power
measured before the inverter can read a few percent above the house).

Every step passes as soon as two measurements 10 seconds apart agree, usually after a minute, and fails if the
battery has not shown it within 5 minutes. A step ends at once, without a verdict, if the battery reaches its
highest level, or its lowest level or the usage reserve, while it is forced. A step that can't prove anything right
now is skipped in seconds, before its action is published (the battery too full or too empty, no house load or solar
surplus to show it, the battery already doing what the action asks, a car charging, a sensor without a number, an
action the inverter can't run, or no steps in your automation). A whole test therefore takes from a few seconds to
about 30 minutes: about a minute for every step that works, and up to 5 minutes for every step that fails. A sensor
must have reported within the last 5 minutes (battery power, consumption, solar production), or the step is not tested. Two quiet sensors
are allowed, because many sensors only report when the value changes: a solar sensor at 0 W in the dark, and a battery
power sensor at 0 W. A battery sensor that is quiet at 0 W can show that a step did not make the battery move, but it
can't prove a stop, so the stopping steps are then not tested.

- ✅ **Worked:** the battery did what the action asks.
- ❌ **The battery did not react:** open the automation's trace and check that it ran the steps for the action and
  that the inverter took the commands. For *Discharge allowed* and *Solar charging blocked* the blueprint runs
  *While the car is charging* instead when a car is charging. *Discharge allowed* that kept selling after
  *Forced discharge* means that your steps for it do not end the forced discharge (e.g. a missing
  `stop_forcible_charge` or a forced mode that runs for a set time).
- 🔧 **Not tested:** the step could not prove anything right now, and the reason is shown. The solar actions need
  sun and a house load: *Solar charging blocked* can only be tested while the solar production is above the house's
  use, and *Solar charging only* and *Discharge allowed* while the house uses more than the sun gives. BattMind
  decides this itself, from the measurements, so there is nothing to choose. Run the test again when the conditions
  are right. A step is not tested when the battery is too full or too empty to show it (close to its highest level,
  or to its lowest level or the usage reserve), and the result says which. A step is also not tested when the
  inverter can't run the action (*When the inverter can't run an action*), when a sensor has no number or has not
  reported lately, when the sun or the house load could explain what was seen, when the battery was already doing what the action asks, or when a car is charging (the
  blueprint then runs *While the car is charging* instead of *Discharge allowed* and *Solar charging blocked*).
- 🔧 **No steps:** your automation was made from the blueprint, and the field for the action is empty, so it does
  nothing. BattMind reads this from `automations.yaml` (the file the automation editor writes; it only reads it) by
  looking for the blueprint automation that uses this action sensor. An automation kept in a package or in YAML mode
  is not found: the result then says so and the steps are tested as usual. *Stop battery* is only sent as the
  safety stop for a battery temperature that is too high, too low or unknown (see *Stop battery* above). Without a
  temperature sensor BattMind never sends it, so the result says that no steps are needed; with a sensor it warns
  that your automation does not stop the battery.

What the test does and does not do:

- It charges the battery from the grid and discharges it to the grid for a few minutes, and it sends your
  automation the same actions the plan would. The energy it moves is counted like any other energy in the history
  of the hour, so the hour's savings figures include it, and the planner's rules active in the rest of that hour
  are credited with it. The plan and the settings are not changed.
- While a step runs, `sensor.<prefix>_current_charging_rule` shows *🧪Testing your automation: …* with the action
  of the step, also when a calculation fails during it (the error is then shown on the line below). When every step
  is done, the test ends on its own and the switch turns off.
- Nothing is tested while the battery is stopped for temperature, and the test ends at once if it becomes so.
  The temperature stop is a safety that the test never overrides.
- Turning the switch off stops the test, and BattMind's own action is published again. A test is never resumed:
  if Home Assistant restarts or BattMind is reloaded in the middle of one, BattMind publishes the plan's action
  when it starts again, and your automation sends it then (the blueprint does so when its repeat interval is on).
- The hours of a test are shown as skipped in the check above, with the reason *Test*.
- The result of the last finished test is kept over a restart.

## Sensors and attributes

| Entity | Content |
|---|---|
| `sensor.<prefix>_powerwall_action` | the action now |
| ↳ `battery_level_flow` | the forecast battery level per hour |
| ↳ `price_check` | the self test of the price providers on their own prices, per provider (see [getting started](getting-started.md#prices)); it only reports |
| ↳ `solar_check` | what the solar forecast delivers: the source, and per day the hours with a value and their kWh (see [getting started](getting-started.md#solar-panels-and-weather-optional)); only with a forecast, it only reports |
| ↳ `price_chart` | the buy and sell prices the plan is made on, per price period for 7 days (`time`, `buy`, `sell`, `forecast`), real and forecast in one list: what the price chart of the dashboard draws |
| ↳ `charge_timestamps`, `force_discharge_timestamps`, `discharge_timestamps`, `blocked_solar_charge_timestamps`, `force_solar_only_charging_timestamps`, `force_powerwall_max_hours_old_discharge_timestamps` | the planned hours per action, for your own automations or charts |
| ↳ `hour_cost_prediction_ema` / `_trend` / `_avg` | the forecast consumption per hour (and `sum_*` accumulated) |
| ↳ `optimizer_shadow_summary` | totals of the [optimised plan, the rule plan and self consumption](rules/optimizer.md#comparing-the-two), and the profit requirements steering each of them (`requirements`, a steering figure, not money) |
| ↳ `plan_check` | whether the battery did what the action asked, hour by hour: [see above](#does-the-battery-follow-the-action) |
| `sensor.<prefix>_automation_test` | the status of the [test of your automation](#test-your-automation) (idle, running, done, stopped, failed); the result is in `markdown` and `steps`; while the test runs `plan` lists every step and `current` the one that is measured now |
| `switch.<prefix>_automation_test_run` | on while the test runs; turn it on to run the test and off to stop it |
| `sensor.<prefix>_current_charging_rule` | why, as text. The attributes `worker_thread` and `calculation_seconds` show where and how fast the plan was calculated |
| `sensor.<prefix>_battery_level_needed` | the battery level needed until the next charge (%), with the forecast in `battery_level_flow_prediction` |

<!-- {% endraw %} -->
