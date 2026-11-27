# Storm warning

Optional. BattMind can keep the battery charged for a storm: it reads a weather warning from one entity and makes
sure the battery is at a level that covers the house while the warning lasts.

It is off until you choose a warning entity. Then the dashboard gets a *Storm warning* section, and two entities
are made: `sensor.battmind_storm_warning` (the status) and `number.battmind_storm_cover_hours`. Clearing the option
removes them again.

> **Without a backup power supply** the house is cut off from the grid in a power cut, and a full battery does not run
> it. The function only helps an installation where the battery can keep the house running in an outage. Use it only
> if it fits yours.

---

## 🌪️ Set it up

**Configure → Solar panels, weather and storm warning → Storm warning**

- **Storm warning (MeteoAlarm):** the binary sensor of the built-in MeteoAlarm integration for your region. Empty
  = off.
- **Lowest level that counts:** 2 = yellow, 3 = orange (default), 4 = red.

Only MeteoAlarm's attribute format is read (`onset`, `expires`, `awareness_level` such as `3; orange; Severe`, and
`awareness_type` such as `1; wind`). A warning from another integration is not understood and is ignored with the
status *Level or type unreadable*.

---

## Which warnings count

| Rule | Why |
|---|---|
| Only wind (1), snow/ice (2) and thunderstorms (3) | A forest fire or a heat warning is no reason to charge. |
| Only at or above the chosen level | A yellow warning is common and does not call for a charged battery by default. |
| **Only with a start time** (`onset`) | MeteoAlarm shows a warning as *on* before it has started, and a warning without a start time could mean "now" or "sometime". Nothing is planned for it: the status says *No start time (ignored)*. |
| The end is the warning's `expires` | Without one, the warning covers **Hours a storm warning without an end time covers** (default 12) from its start. The number is 1 to 72. |
| A level or a type that cannot be read is ignored | It is never taken for a storm. |

A warning that has started is *Active*; one that starts later is *Coming*. A warning that starts after the days the
plan covers is *Starts after the plan*. A warning that has ended, or ends before it starts, is ignored.

---

## What the plan keeps

For the hours of the warning the plan keeps a battery level (the *Level kept* on the dashboard):

- **The need** is the house use the forecast expects in the hours of the warning, **with no solar**, plus a buffer,
  divided by the discharge efficiency (the energy that has to be in the battery for the house to get it).
- **The buffer** is how far the consumption forecast was off over the last 30 days, but at least **20 %** and at most
  100 %. Until there are enough days with a recorded forecast, it is 20 %. The status shows which one is used.
- **The level falls hour by hour:** at the end of an hour the battery has to hold the minimum level plus what is
  still needed after that hour. The hour before the warning starts holds all of it. It is never more than the
  maximum level.
- **Nothing is sold under the level**, and the plan holds the battery rather than let the house use the energy
  that is kept.
- **Charging is placed in the cheapest hours** before the level is needed, so a storm costs what the cheapest way
  there costs. Energy older than *Max. age of kWh* may be kept for the storm: the storm level goes first.
- **If the level cannot be reached in time** (too little charge power, or the warning is close), the battery is
  charged as high as possible and the plan does not fail. *short_percent* on the sensor says how far short it is.

The same level is kept whether the [whole period optimisation](optimizer.md) controls the battery or the rules do:
the charge is marked 🌪️ and is not one of the rules with a switch, so no switch can turn it off. With
[use midnight battery level](selling.md#use-midnight-battery-level) every day is planned on its own, and a day ends
high enough for a storm that starts just after midnight to be reachable.

The kept level steers the plan only: the costs and totals in the overview are real money, without it.

Good to know:

- A charge that is needed for the storm is marked 🌪️ as a whole, also the part that goes beyond the need, so it is not
  stopped by a switched-off charging rule.
- The window is counted in clock time: a warning without an end time that crosses a daylight-saving change is an hour
  shorter or longer in real time.
- A warning that starts inside the hour that is going on has no hour before it: the level is kept from the end of that hour.
- While *Use midnight battery level* is on, only the next day's level is carried over: a day ends high enough for
  the next day to reach its level with full charge power, not more.

---

## The status and the dashboard

`sensor.battmind_storm_warning` has one of these states: *No warning*, *Warning entity unavailable*, *Below the
chosen level*, *Not a storm warning*, *No start time (ignored)*, *Level or type unreadable (ignored)*, *Ended*, *End
before start (ignored)*, *Starts after the plan*, *Coming* or *Active*.

Attributes: `awareness_level`, `awareness_type`, `start`, `end` (with the time zone), `end_from` (`entity` or `hours`),
`expected_use_kwh`, `buffer_percent`, `buffer_from` (`measured` or `default`), `needed_percent`, `needed_capped` (the need is
more than the battery holds above its minimum), `need_complete`, `keep_from`, `keep_percent`, `plan` (`optimal` or `rules`:
what controls the battery) and `short_percent`.

`need_complete` is `false` when an hour of the warning has no consumption forecast: a new installation has no history to
forecast from, and a warning can reach past the days the plan covers. The need is then at least what is shown (a forecast of 0
is never taken as no need), so a level may be missing or too low until there is history. The state is `entity_unavailable`
(not `unavailable`, which is Home Assistant's own state) when the warning entity is missing or unavailable.

The [what-if simulator](../what-if-simulator.md) does not simulate the storm warning: it replays past days.
