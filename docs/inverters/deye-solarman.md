# Deye hybrid (Solarman)

Actions for the [blueprint](../actions.md#the-blueprint) with the
[Solarman](https://github.com/davidrapan/ha-solarman) integration (`ha-solarman`) and a Deye hybrid
inverter with a battery, using its `deye_hybrid` definition.

> **Not tested on a real inverter.** The entities, option names and ranges below are checked against the
> `deye_hybrid.yaml` definition in the ha-solarman repository (release 25.08.16 and later code on the main branch), but nobody has run these actions on a Deye yet.
> Try each action once by hand (*Developer tools → Actions*, or *Run actions* on the automation) and watch
> the battery before you rely on them. If they work for you, please share them under
> [Blueprint setups](https://github.com/dezito/BattMind-Integration/discussions/categories/blueprint-setups).

## Before you start

- The Deye is controlled through its **six programs** (time slots). Each program has a *SOC* and a
  *Charging* setting. The actions below write all six programs the same way, so it doesn't matter which
  one is active. Set the programs' start times in the inverter or in Home Assistant so they cover the whole
  day.
- The entity ids below are examples. The prefix is the name of your Solarman device, and the names may
  differ in your language. Copy the real ids from the device page.
- Replace what is marked `TODO`:
  - `YOUR_MIN_SOC`: the lowest battery level the inverter may discharge to, in % (e.g. `10`).
  - `YOUR_NORMAL_WORK_MODE`: the *Work Mode* you normally use: `Zero Export To Load`, `Zero Export To CT` or
    `Export First`. The actions set it back, because *Forced discharge* changes it.
  - `YOUR_CHARGE_AMPS`: the grid charging current in **ampere**, 0-240. It is not watt: roughly the power
    divided by the battery voltage, e.g. 5000 W / 51 V ≈ 98 A. Check the max. charge current of your battery.
  - `sensor.YOUR_BATTERY_LEVEL`: the battery level sensor (%).
- *Export Surplus Power* is in watt (0-12000). Change `5000` to the power you want to sell with.
- Choose *Edit in YAML* in each action field and paste.
- Modbus over Solarman can be slow. If a step is missed, add a *Delay* step (e.g. 2 seconds) between the
  steps, as described under [Actions](../actions.md#actions).

## Charge from grid

```yaml
- action: select.select_option
  target:
    entity_id: select.deye_time_of_use
  data:
    option: Week
- action: number.set_value
  target:
    entity_id:
      - number.deye_program_1_soc
      - number.deye_program_2_soc
      - number.deye_program_3_soc
      - number.deye_program_4_soc
      - number.deye_program_5_soc
      - number.deye_program_6_soc
  data:
    value: 100
- action: select.select_option
  target:
    entity_id:
      - select.deye_program_1_charging
      - select.deye_program_2_charging
      - select.deye_program_3_charging
      - select.deye_program_4_charging
      - select.deye_program_5_charging
      - select.deye_program_6_charging
  data:
    option: Grid
- action: number.set_value
  target:
    entity_id: number.deye_battery_grid_charging_current
  data:
    value: YOUR_CHARGE_AMPS # TODO
- action: select.select_option
  target:
    entity_id: select.deye_work_mode
  data:
    option: YOUR_NORMAL_WORK_MODE # TODO
- action: switch.turn_on
  target:
    entity_id: switch.deye_battery_grid_charging
```

## Forced discharge

```yaml
- action: select.select_option
  target:
    entity_id: select.deye_time_of_use
  data:
    option: Week
- action: switch.turn_off
  target:
    entity_id: switch.deye_battery_grid_charging
- action: select.select_option
  target:
    entity_id:
      - select.deye_program_1_charging
      - select.deye_program_2_charging
      - select.deye_program_3_charging
      - select.deye_program_4_charging
      - select.deye_program_5_charging
      - select.deye_program_6_charging
  data:
    option: Disabled
- action: number.set_value
  target:
    entity_id:
      - number.deye_program_1_soc
      - number.deye_program_2_soc
      - number.deye_program_3_soc
      - number.deye_program_4_soc
      - number.deye_program_5_soc
      - number.deye_program_6_soc
  data:
    value: YOUR_MIN_SOC # TODO
- action: number.set_value
  target:
    entity_id: number.deye_export_surplus_power
  data:
    value: 5000
# TODO: the definition also has a switch "Export Surplus". It is not checked whether the inverter
# needs it to be on for selling. If your Deye doesn't sell with the steps above, add this step, and
# decide yourself whether it should be turned off again in the other actions:
# - action: switch.turn_on
#   target:
#     entity_id: switch.deye_export_surplus
- action: select.select_option
  target:
    entity_id: select.deye_work_mode
  data:
    option: Export First
```

## Discharge allowed (normal operation)

```yaml
- action: select.select_option
  target:
    entity_id: select.deye_time_of_use
  data:
    option: Week
- action: switch.turn_off
  target:
    entity_id: switch.deye_battery_grid_charging
- action: select.select_option
  target:
    entity_id:
      - select.deye_program_1_charging
      - select.deye_program_2_charging
      - select.deye_program_3_charging
      - select.deye_program_4_charging
      - select.deye_program_5_charging
      - select.deye_program_6_charging
  data:
    option: Disabled
- action: number.set_value
  target:
    entity_id:
      - number.deye_program_1_soc
      - number.deye_program_2_soc
      - number.deye_program_3_soc
      - number.deye_program_4_soc
      - number.deye_program_5_soc
      - number.deye_program_6_soc
  data:
    value: YOUR_MIN_SOC # TODO
- action: select.select_option
  target:
    entity_id: select.deye_work_mode
  data:
    option: YOUR_NORMAL_WORK_MODE # TODO
```

## Solar charging only

Each program's SOC is set to the battery level now, so the battery doesn't discharge below it while the
sun can still charge it. As the battery charges, the SOC of the programs would stay behind, so the
5-minute repeat moves it up. The battery can therefore discharge back to the level of the last repeat, at
most.

The template has no default on purpose: if the battery level sensor is `unavailable` or not a number, the
step fails and the action stops, instead of setting the SOC to 0 and letting the battery discharge freely.

```yaml
- action: select.select_option
  target:
    entity_id: select.deye_time_of_use
  data:
    option: Week
- action: switch.turn_off
  target:
    entity_id: switch.deye_battery_grid_charging
- action: select.select_option
  target:
    entity_id:
      - select.deye_program_1_charging
      - select.deye_program_2_charging
      - select.deye_program_3_charging
      - select.deye_program_4_charging
      - select.deye_program_5_charging
      - select.deye_program_6_charging
  data:
    option: Disabled
- action: number.set_value
  target:
    entity_id:
      - number.deye_program_1_soc
      - number.deye_program_2_soc
      - number.deye_program_3_soc
      - number.deye_program_4_soc
      - number.deye_program_5_soc
      - number.deye_program_6_soc
  data:
    value: "{{ states('sensor.YOUR_BATTERY_LEVEL') | int }}" # TODO
- action: select.select_option
  target:
    entity_id: select.deye_work_mode
  data:
    option: YOUR_NORMAL_WORK_MODE # TODO
```

## While the car is charging

Use the same steps as *Solar charging only*: the battery doesn't discharge into the car.

## Not possible with these entities

- **Solar charging blocked:** the definition has no entity for limiting the charge power, so this action
  can't be done. Leave the field empty and select the action under *Modes the battery can't run* in the BattMind setup
  (see [Actions](../actions.md#when-the-inverter-cant-run-an-action)).
- **Stop battery:** the steps of *Solar charging only* stop the discharge, but the battery can still charge
  from the sun, so they are not a stop. Don't rely on BattMind's temperature protection with this setup;
  it is only a secondary safeguard in any case.

## Repeat interval

Keep **Repeat interval** at the default 5 minutes. It puts the settings right if they are changed in the
Deye app or on the inverter, and it moves the SOC in *Solar charging only* up.
