# Discharging

By default the battery may discharge to the house in every hour (`discharge_allowed`). These rules decide
in which hours it **may not**, so the energy is saved for when it is worth more – and when it should be
used even though it doesn't pay.

An hour where discharging is blocked becomes `force_solar_only_charging`: the battery can still charge from
solar, but the house uses the grid.

<p align="center">
  <img src="../../images/settings-discharging.png" alt="The Discharging settings" width="380">
</p>

---

## 🚫 Profit only

**Switch:** *Discharging: Profit only*

Blocks discharging in hours where the buy price is lower than what a kWh from the battery costs, incl. loss
and wear, plus **min. profit per kWh**.

The battery's cost is calculated for that hour: the energy already in the battery plus what is planned to
be charged before it (grid at the buy price, solar at the sell price it would otherwise have got).

**Example:** the battery was charged at night at 0.80 kr/kWh, so a kWh out costs 0.99 kr. At 14:00 the
price is 0.90 kr/kWh: using the battery would lose 0.09 kr per kWh. Discharging is blocked, and the energy
is saved for the evening.

Only the profit per kWh counts here – **min. total profit** is only for sales to the grid, so it doesn't
block small hours.

---

## ⏳ Prioritise by energy cost

**Switch:** *Discharging: Prioritise by energy cost*

Saves the battery for the most expensive hours until it is charged again.

From the hour the battery is fullest until it is charged again (by solar or the grid), BattMind goes through
the hours from the most expensive (price × consumption) down and gives each its share of the battery. When
the battery is used up, the remaining cheaper hours before the last prioritised hour are blocked.

**Example:** the battery holds 4 kWh after the night. 07–08 costs 1.80 kr, 17–20 costs 2.50 kr, and the
house uses 1 kWh per hour. The four evening hours get the 4 kWh, and the morning hours use the grid.

---

## 🌄 Before the sun

**Switch:** *Discharging: Before the sun*

On days where the sun fills the battery to max and the surplus is sold, every kWh emptied before the sun
has filled the battery is refilled with solar that would otherwise be sold. So a kWh used or sold before
that only has to beat the solar sell price:

`refill cost = solar sell price ÷ efficiency² + wear`

The rule allows discharging in blocked hours before the sun, and sells in the most expensive periods before
it, as long as `price − refill cost ≥ min. profit per kWh`. It never empties more than the solar surplus
that would be pushed out.

With the [whole period optimisation](optimizer.md) the sale can also be in an hour after sunrise, as long as
the sun fills the battery to max later (within 24 hours) and nothing is charged from the grid in between: the
kWh sold is then refilled by solar that would otherwise be sold. It is shown as 🌄 *Sells before the sun fills the
battery*.

**Example:** the sun fills the battery at 11:00 and 6 kWh surplus are sold at 0.40 kr/kWh. Refill costs
0.40 ÷ 0.9 + 0.10 = 0.54 kr/kWh. At 07:00 the price is 1.60 kr/kWh: using the battery earns 1.06 kr per
kWh, even though the night charge cost more.

---

## 🕰️ Max. age of kWh

**Switch:** *Forced discharge: Max. kWh age in hours* · **Number:** *Max. age of kWh in home battery*
(default 48 h, from 12 to 72 h)

Energy that has been in the battery longer than this is used even without profit: discharging is allowed
in every hour from then. This keeps old, expensive energy from blocking the battery forever – e.g. when it
was charged before a drop in prices.

BattMind follows the age of every kWh in the battery:

- **Energy already in the battery** gets its age from the charging history. The oldest kWh leave first, so
  only the part of the energy that is older than the limit counts as old. The what-if simulator does the same
  when it replays past days.
- **Energy the plan charges** is new when it goes in and becomes old after the set hours.
- **Energy of unknown age** (no charging history for it) is never treated as old.

Old energy is never held back. In an hour where the house uses power and old energy is in the battery, the
battery is used – instead of keeping the energy for a later, more expensive hour, or because using it
doesn't pay.

Old energy is not sold by force. It is sold only when selling it in the most expensive sell periods pays more
than using it, under the normal [profit requirements](selling.md#profit-requirements). Such a sale is shown
as 🕰️ *Selling old energy*. Selling needs an inverter that can force discharge to the grid (see
[Actions](../actions.md#when-the-inverter-cant-run-an-action)).

The hours that use old energy are listed in the attribute `force_powerwall_max_hours_old_discharge_timestamps`
of `sensor.<prefix>_powerwall_action`. Switching the switch off removes the limit; the other discharge rules
then decide as usual.

The plan only looks as far as its horizon (days to plan). Energy charged near the end of that horizon can
become older than the limit after the plan ends, and the plan can't see that.

**Example:** the limit is 48 h. On Monday at 02:00 the battery is charged with 4 kWh from the grid at
0.80 kr/kWh. On Wednesday at 07:00 the house uses 1 kWh while the price is 0.85 kr/kWh. Using that kWh costs
0.99 kr, so it doesn't pay, and *Profit only* would hold it back. But the energy is older than 48 h, so the
battery covers the house at 07:00 instead.
