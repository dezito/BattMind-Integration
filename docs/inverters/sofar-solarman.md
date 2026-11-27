# Sofar hybrid (Solarman)

Actions for the [blueprint](../actions.md#the-blueprint) with the
[Solarman](https://github.com/davidrapan/ha-solarman) integration (`ha-solarman`) and a Sofar hybrid
inverter with a battery (HYD, ESI), using its `sofar_g3hyd` definition.

> **Not tested on a real inverter.** The entities, option names and units below are checked against the
> `sofar_g3hyd.yaml` definition in the ha-solarman repository, but nobody has run these actions on a Sofar
> yet. Other Sofar definitions in ha-solarman (`sofar_hybrid.yaml`, `sofar_g3.yaml`, `sofar_string.yaml`)
> are not checked and may use other names. Try each action once by hand (*Developer tools → Actions*, or
> *Run actions* on the automation) and watch the battery before you rely on them. If they work for you,
> please share them under
> [Blueprint setups](https://github.com/dezito/BattMind-Integration/discussions/categories/blueprint-setups).

## Before you start

- The entity ids below are examples. The prefix is the name of your Solarman device, and the names may
  differ in your language (ha-solarman has translations for several languages). Copy the real ids from the
  device page.
- Choose *Edit in YAML* in each action field and paste.
- Change `5000` to your battery's max. power in watt. `20000` is the highest value used in the
  definition's own example.

## How it works

Normal operation is *Storage Control Mode* **Self Use**. For everything else the actions use **Passive**
mode, where three numbers control the battery. All of them are in watt, and the sign matters:

| Entity | Meaning |
|---|---|
| *Passive: Minimum Battery power* | the lowest battery power. **Negative = discharge**, positive = charge |
| *Passive: Maximum Battery power* | the highest battery power |
| *Passive: Grid power* | the grid power the inverter aims for. The actions set it to 0, as the definition's own example does |

With the minimum and maximum equal, the battery runs at exactly that power. With a range, it moves inside it.

The three numbers are written to the inverter together (all six registers in one go). The actions write
all three before they select Passive. **Check on the first test run that all three values stuck**: it is not
verified that ha-solarman uses the value of the number written just before. If a value is lost, add a
*Delay* step between the numbers, or write the registers in one call with `solarman.write_multiple_registers`
as the header of `sofar_g3hyd.yaml` shows.

The definition has no timeout for Passive mode. Whether the inverter leaves it by itself is not verified; the
5-minute repeat sends the command again.

## Charge from grid

```yaml
- action: number.set_value
  target:
    entity_id: number.sofar_passive_grid_power
  data:
    value: 0
- action: number.set_value
  target:
    entity_id: number.sofar_passive_maximum_battery_power
  data:
    value: 5000
- action: number.set_value
  target:
    entity_id: number.sofar_passive_minimum_battery_power
  data:
    value: 5000
- action: select.select_option
  target:
    entity_id: select.sofar_storage_control_mode
  data:
    option: Passive
```

## Forced discharge

```yaml
- action: number.set_value
  target:
    entity_id: number.sofar_passive_grid_power
  data:
    value: 0
- action: number.set_value
  target:
    entity_id: number.sofar_passive_minimum_battery_power
  data:
    value: -5000
- action: number.set_value
  target:
    entity_id: number.sofar_passive_maximum_battery_power
  data:
    value: -5000
- action: select.select_option
  target:
    entity_id: select.sofar_storage_control_mode
  data:
    option: Passive
```

## Discharge allowed (normal operation)

```yaml
- action: select.select_option
  target:
    entity_id: select.sofar_storage_control_mode
  data:
    option: Self Use
```

## Solar charging only

The battery power is kept between 0 and the maximum: no discharge, but the sun can still charge it.

```yaml
- action: number.set_value
  target:
    entity_id: number.sofar_passive_grid_power
  data:
    value: 0
- action: number.set_value
  target:
    entity_id: number.sofar_passive_maximum_battery_power
  data:
    value: 5000
- action: number.set_value
  target:
    entity_id: number.sofar_passive_minimum_battery_power
  data:
    value: 0
- action: select.select_option
  target:
    entity_id: select.sofar_storage_control_mode
  data:
    option: Passive
```

## While the car is charging

Use the same steps as *Solar charging only*: the battery doesn't discharge into the car.

## Stop battery and Solar charging blocked

No source describes these two for Sofar, so the step below is a **suggestion built from the entities
only**. Test it before you rely on it. For *Solar charging blocked*, leave the field empty and select
that action under *Modes the battery can't run* in the BattMind setup (see
[Actions](../actions.md#when-the-inverter-cant-run-an-action)).

Stop battery (minimum and maximum 0):

```yaml
- action: number.set_value
  target:
    entity_id: number.sofar_passive_grid_power
  data:
    value: 0
- action: number.set_value
  target:
    entity_id: number.sofar_passive_maximum_battery_power
  data:
    value: 0
- action: number.set_value
  target:
    entity_id: number.sofar_passive_minimum_battery_power
  data:
    value: 0
- action: select.select_option
  target:
    entity_id: select.sofar_storage_control_mode
  data:
    option: Passive
```

## Repeat interval

Keep **Repeat interval** at the default 5 minutes. It sends the Passive values again, so a lost command
or a change made in the Sofar app is put right.
