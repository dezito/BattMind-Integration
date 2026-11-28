# SolarEdge (StorEdge)

<!-- {% raw %} -->

Actions for the [blueprint](../actions.md#the-blueprint) with the
[SolarEdge Modbus Multi](https://github.com/WillCodeForCats/solaredge-modbus-multi) integration and a
battery on a StorEdge inverter. Change `5000` to your battery's max. power (watt).

> **Not tested on a real inverter.** The entities, option names and units below are checked against the
> integration's source code (version 4.0.15), but nobody has run these actions on a SolarEdge yet. Try each
> action once by hand (*Developer tools → Actions*, or *Run actions* on the automation) and watch the
> battery before you rely on them. If they work for you, please share them under
> [Blueprint setups](https://github.com/dezito/BattMind-Integration/discussions/categories/blueprint-setups).

## Before you start

- In the SolarEdge Modbus Multi options, turn on **Power Control Options → Storage Control**. It is off by
  default, and without it the entities below don't exist. They are only enabled by default when the
  integration finds a battery.
- The entity ids below are examples. The prefix is the name you gave the integration plus the inverter
  number (`solaredge_i1`), and Home Assistant's language can change it. Copy the real ids from the
  inverter's device page.
- Choose *Edit in YAML* in each action field and paste.

## How it works

*Storage Control Mode* **Maximize Self Consumption** is the normal operation. For everything else the
actions switch it to **Remote Control**, which makes the command entities (*Storage Command Mode*,
*Storage Default Mode*, *Storage Charge Limit*, *Storage Discharge Limit* and *Storage Command Timeout*)
available. The limits are in watt.

Three things in the actions come from that:

- The command entities are unavailable until the inverter reports Remote Control, and the integration
  doesn't refresh right after the mode is written. Home Assistant ignores a call to an unavailable
  entity without an error, so each action waits (up to 30 seconds) until *Storage Command Mode* is
  available. If it never is, the action stops, and the next repeat tries again.
- *Storage Command Timeout* is set to 3600 seconds, so a command lapses if BattMind and Home Assistant stop
  sending it. The 5-minute repeat renews it long before. Neither the integration's source nor the
  README says what the inverter does when the timeout runs out, and the wiki and SolarEdge's power control
  PDF were not read.
- *Storage Default Mode* is set to **Maximize Self Consumption** in every Remote Control action. The name
  suggests it is what the inverter returns to when a command lapses, so a lapse then means normal
  operation and not a leftover grid charge. That step is **derived from the name**, and what it
  does is not verified.

## Charge from grid

```yaml
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_ac_charge_policy
  data:
    option: Always Allowed
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_control_mode
  data:
    option: Remote Control
- wait_template: "{{ not is_state('select.solaredge_i1_storage_command_mode', 'unavailable') }}"
  timeout: "00:00:30"
  continue_on_timeout: false
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_default_mode
  data:
    option: Maximize Self Consumption
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_command_timeout
  data:
    value: 3600
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_command_mode
  data:
    option: Charge from Solar Power and Grid
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_charge_limit
  data:
    value: 5000
```

*AC Charge Policy* stays on *Always Allowed* afterwards. Whether that also lets the inverter charge from
the grid in normal operation is not verified; see the comment in *Discharge allowed*.

## Forced discharge

```yaml
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_control_mode
  data:
    option: Remote Control
- wait_template: "{{ not is_state('select.solaredge_i1_storage_command_mode', 'unavailable') }}"
  timeout: "00:00:30"
  continue_on_timeout: false
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_default_mode
  data:
    option: Maximize Self Consumption
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_command_timeout
  data:
    value: 3600
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_command_mode
  data:
    option: Discharge to Maximize Export
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_discharge_limit
  data:
    value: 5000
```

## Discharge allowed (normal operation)

```yaml
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_control_mode
  data:
    option: Maximize Self Consumption
# TODO (optional): if you want AC Charge Policy back to what you normally use after a grid charge,
# add a select.select_option step on select.solaredge_i1_ac_charge_policy with that option.
```

## Solar charging only

Charge from solar, never discharge. The charge limit is written every time, so it can't keep an old value
from an earlier action. The discharge limit of 0 is an extra precaution that is derived from the
option names, not verified.

```yaml
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_control_mode
  data:
    option: Remote Control
- wait_template: "{{ not is_state('select.solaredge_i1_storage_command_mode', 'unavailable') }}"
  timeout: "00:00:30"
  continue_on_timeout: false
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_default_mode
  data:
    option: Maximize Self Consumption
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_command_timeout
  data:
    value: 3600
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_command_mode
  data:
    option: Charge from Solar Power
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_charge_limit
  data:
    value: 5000
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_discharge_limit
  data:
    value: 0
```

## While the car is charging

Use the same steps as *Solar charging only*: the battery doesn't discharge into the car.

## Stop battery and Solar charging blocked

No source describes these two for SolarEdge, so the steps below are **suggestions built from the option
names only**. Test them before you rely on them. For *Solar charging blocked*, you can instead leave the
field empty and select that action under *Modes the battery can't run* in the BattMind setup (see
[Actions](../actions.md#when-the-inverter-cant-run-an-action)).

Stop battery (both limits 0):

```yaml
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_control_mode
  data:
    option: Remote Control
- wait_template: "{{ not is_state('select.solaredge_i1_storage_command_mode', 'unavailable') }}"
  timeout: "00:00:30"
  continue_on_timeout: false
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_default_mode
  data:
    option: Maximize Self Consumption
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_command_timeout
  data:
    value: 3600
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_command_mode
  data:
    option: Solar Power Only (Off)
- action: number.set_value
  target:
    entity_id:
      - number.solaredge_i1_storage_charge_limit
      - number.solaredge_i1_storage_discharge_limit
  data:
    value: 0
```

Solar charging blocked (charge limit 0, discharge to the house):

```yaml
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_control_mode
  data:
    option: Remote Control
- wait_template: "{{ not is_state('select.solaredge_i1_storage_command_mode', 'unavailable') }}"
  timeout: "00:00:30"
  continue_on_timeout: false
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_default_mode
  data:
    option: Maximize Self Consumption
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_command_timeout
  data:
    value: 3600
- action: select.select_option
  target:
    entity_id: select.solaredge_i1_storage_command_mode
  data:
    option: Discharge to Minimize Import
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_charge_limit
  data:
    value: 0
- action: number.set_value
  target:
    entity_id: number.solaredge_i1_storage_discharge_limit
  data:
    value: 5000
```

## Repeat interval

Keep **Repeat interval** at the default 5 minutes. It renews the command timeout and puts the settings
right if they are changed elsewhere, e.g. in the SolarEdge app.

<!-- {% endraw %} -->
