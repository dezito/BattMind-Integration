# Solar

Only with solar panels configured. See [How BattMind plans](../how-it-works.md#solar-forecast) for how the
solar surplus is forecast.

<p align="center">
  <img src="../../images/settings-solar.png" alt="The Solar settings" width="380">
</p>

---

## ☀️ Solar surplus charging

**Switch:** *Solar surplus charging*

Includes the forecast solar surplus in the plan. The surplus charges the battery before the grid, and in
hours with surplus the sun covers the house.

Turn it off only if the battery can't charge from solar (e.g. an AC-coupled battery that is controlled
separately). The plan then ignores the sun.

---

## 🎛️ Optimise solar charging

**Switch:** *Optimise solar charging*

On days where the sun fills the battery to max anyway, it doesn't matter **when** the battery is charged –
only which solar kWh are sold. So BattMind blocks solar charging in the hours where the surplus sells for
the most, and fills the battery in the cheaper solar hours instead. The battery ends the day at the same
level, so the plan for the next days doesn't change.

In the blocked hours the action is `blocked_solar_charge`: the inverter should not charge the battery from
solar, so the surplus is exported.

Blocking is a bet that the later sun fills the battery, so BattMind leaves room for a forecast that is too
high:

- **Margin:** an hour is only blocked when the battery is still filled with **80 %** of the later solar
  forecast the same day. Only the sun counts for this: a grid charge planned later the same day does not
  refill the battery in the test. The exception is an hour where the sold surplus earns more than a stored kWh
  could ever save later (the sell price is above the highest later buy price after the charging loss):
  then there is nothing to bet on.
- **Once an hour:** when the battery has been allowed to charge from solar in an hour (e.g. because the
  forecast dropped), solar charging isn't blocked again before the next hour. The battery can switch from
  blocked to charging once an hour, but not back and forth every few minutes.

**Example:** the sun fills the battery by 13:00 either way. The sell price is 0.90 kr/kWh at 09:00 and
0.20 kr/kWh at 13:00. BattMind blocks solar charging at 09:00 (the surplus is sold at 0.90) and lets the
battery catch up at 13:00 (where the surplus would only have earned 0.20).

> [!NOTE]
> It only works if your inverter can stop solar charging without stopping discharging, e.g. by setting the
> max. charge power to 0. See [Actions](../actions.md#blocked_solar_charge).

---

## Fixed solar sell price

**Setup:** *Solar panels → Fixed solar sell price*

With −1 (default) a sold kWh is worth the hourly sell price.

Some production agreements pay a fixed price, but settle the difference to the raw spot price with you:
a sold kWh is then worth `fixed price − the hour's spot price`. With a fixed price of 1.00 kr:

| Spot price | A sold kWh is worth |
|---|---|
| 0.50 kr | 1.00 − 0.50 = **+0.50 kr** |
| 1.00 kr | **0.00 kr** |
| 5.00 kr | 1.00 − 5.00 = **−4.00 kr** (you pay) |

Set the fixed price here, and BattMind values every sale – solar surplus and battery energy – this way.
Selling then pays in the **cheap** hours and costs money in the expensive ones, so the battery should take
the solar surplus in the expensive hours, and *Optimise solar charging* moves the sale to the cheap hours.

---

## kWh charged by solar

`sensor.<prefix>_kwh_charged_by_solar` counts the solar energy that went into the battery. The dashboard
shows the last 7 days.
