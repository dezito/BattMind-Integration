"""Weather and solar forecast and the local energy prediction."""
from __future__ import annotations

import datetime
import logging

from ...const import WEATHER_CONDITION_DICT, price_decimals
from ...utils.solar_sources import CONFIRMED_SOLAR_SOURCES, solar_days
from ...utils.helpers import (
    average,
    day_of_week,
    get_closest_key,
    reset_time_to_hour,
    start_of_day,
    to_local_datetime,
)

_LOGGER = logging.getLogger(__name__)


# Weather scores used as keys in the solar database (0, 20, ..., 100)
SOLAR_SCORES = sorted(set(WEATHER_CONDITION_DICT.values()))


def forecast_day(value, tz):
    """The day of a daily forecast time as naive local time.

    A time that is midnight in its own offset names a date (2026-10-23T00:00:00+00:00, or the local midnight with
    the local offset): that date is kept, whatever the time zone. Any other time (the local midnight in UTC, 22:00Z
    in summer in Denmark) is converted to Home Assistant's time zone first.
    """
    wall_time = to_local_datetime(value)
    if isinstance(wall_time, datetime.datetime) and wall_time.time() == datetime.time(0):
        return wall_time
    return to_local_datetime(value, tz)


class EnergyForecast:
    """Weather and solar forecast and the local energy prediction."""

    def solar_check(self) -> dict:
        """The attribute solar_check: what the chosen solar forecast delivers (utils/solar_sources.py). It only
        reports facts: no plan depends on it, and anything that goes wrong gives no result."""
        try:
            source = self._snap.solar_source
            tz = self._snap.tz
            now = self.now()
            return {
                "source": source,
                "confirmed": source in CONFIRMED_SOLAR_SOURCES,
                "checked": now.replace(tzinfo=tz).isoformat(timespec="minutes"),
                "days": solar_days(self._snap.solcast, now.date(), tz),
            }
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("The check of the solar forecast failed: %s %s", err, type(err))
            return {}

    def _forecast_index(self) -> tuple[dict, dict]:
        """Hourly forecasts by hour and daily forecasts by date (parsed once per job)."""
        if "forecast_index" not in self._job_cache:
            hourly: dict = {}
            daily: dict = {}
            forecast_dict = self._snap.forecast_dict if self._snap else {}
            # the forecast times come with an offset (UTC from Home Assistant): in Home Assistant's time zone
            tz = self._snap.tz if self._snap else None
            for data in forecast_dict.get("hourly") or []:
                if data:
                    hourly.setdefault(reset_time_to_hour(to_local_datetime(data["datetime"], tz)), data)
            for data in forecast_dict.get("daily") or []:
                if data:
                    daily.setdefault(forecast_day(data["datetime"], tz).date(), data)
            self._job_cache["forecast_index"] = (hourly, daily)
        return self._job_cache["forecast_index"]

    def get_forecast(self, date: datetime.datetime):
        """Hourly forecast for the hour of date, else the daily forecast for the day."""
        hourly, daily = self._forecast_index()
        return hourly.get(reset_time_to_hour(date)) or daily.get(date.date())

    @staticmethod
    def forecast_score(data) -> float:
        """0-100 score of how good the weather is for solar production."""
        try:
            if "cloud_coverage" not in data or "uv_index" not in data or "temperature" not in data:
                return WEATHER_CONDITION_DICT[data["condition"]]
            cloud = (100 - data["cloud_coverage"]) / 100
            uv = min(data["uv_index"] / 11, 1)
            temperature = data["temperature"]
            temperature_score = temperature / 20 if temperature <= 20 else 20 / temperature
            if data["cloud_coverage"] >= 90:
                cloud *= 0.2
                uv *= 0.1
                temperature_score *= 0.5
            return (cloud * 0.5 + uv * 0.3 + temperature_score * 0.2) * 100
        except (KeyError, TypeError, ZeroDivisionError):
            return 50.0

    def get_solar_kwh_forecast(self) -> dict:
        """{hour: (kWh surplus, sell price)} from the solar forecast integration minus the predicted consumption.

        The rows are Solcast's detailedHourly, or the hourly rows of Forecast.Solar or Helios Forecast in the same shape
        (utils/solar_forecast.py): period_start in the local time zone and pv_estimate in kWh for the hour.
        """
        if not self.config["solar"]["entity_ids"]["forecast_entity_id"]:
            return {}
        sell_prices = self.get_sell_prices()
        forecast = {}
        for entity_id, rows in (self._snap.solcast if self._snap else {}).items():
            try:
                for data in rows:
                    date = to_local_datetime(data["period_start"], self._snap.tz if self._snap else None)
                    if date not in sell_prices or date in forecast:  # the repeated hour of the autumn: the first row, like the weather
                        continue
                    consumption = self.get_forecast_value(
                        self.power_values_db[date.hour].get("power_consumption_without_all_exclusion", [0.0])
                    )
                    surplus_kwh = round(max(data["pv_estimate"] * 1000.0 - consumption, 0.0) / 1000.0, 3)
                    forecast[date] = (surplus_kwh, self.sell_value(sell_prices[date]))
            except (KeyError, TypeError, AttributeError) as err:
                _LOGGER.error("Invalid solar forecast data in %s: %s", entity_id, err)
        return forecast

    def _database_solar(self, score: float, date: datetime.datetime) -> tuple[float, float, bool]:
        """(kWh surplus, sell value, has history) for an hour from the stored solar history at a weather score.

        has history is False when neither the score nor its neighbours have measurements: the 0 kWh
        is then unknown, not a measured 0.
        """
        scores = self.solar_available_db.get(date.hour)
        if not scores:
            return 0.0, 0.0, False
        power_list = get_closest_key(score, scores) or []
        forecast_value = self.get_forecast_value(power_list) if power_list else 0.0
        neighbours = []
        if len(power_list) <= 6 or forecast_value <= 1000.0:
            # little or low history: blend with the neighbouring weather scores
            if score >= 20 and (lower := get_closest_key(score - 20, scores)):
                neighbours.append(self.get_forecast_value(lower))
            if score <= 80 and (upper := get_closest_key(score + 20, scores)):
                neighbours.append(self.get_forecast_value(upper))
        values = ([forecast_value] * 3 if power_list else []) + neighbours
        sell_price = self.sell_value(self.get_price_forecast_value(self.kwh_avg_prices_db["history_sell"][date.hour][day_of_week(date)]))
        return max(average(values), 0.0) / 1000, sell_price, bool(values)

    def local_energy_prediction(self) -> None:
        """Predict the solar surplus per hour for today + the next days (self.solar_prediction_db).

        "total" and "total_sell" are read by position (index = hour of the day). An hour with neither weather nor a solar
        forecast row (e.g. Forecast.Solar has no row before sunrise) is 0.0 (no known sun) up to the last known hour of the
        day, so the hours after it keep their place; the hours after the last known hour stay absent (readers count a
        missing hour as 0). "known_hours" is the number of hours that had a value: 24 for a complete day.
        "total_database" and "total_forecast" only hold the known values (they are only summed).
        """
        if not self.inverter_available():
            return
        solcast = self.get_solar_kwh_forecast()
        now = self.now()
        today = start_of_day(now)
        sell_decimals = price_decimals(self.config) - 1  # the predicted sell prices are rounded to 1 decimal in kroner

        for day in range(int(self.config["days_to_predict"]) + 1):
            prediction = {key: [] for key in ("total_database", "total_database_sell", "total_forecast", "total_forecast_sell", "total", "total_sell")}
            known_hours = 0
            for hour in range(24):
                timestamp = today + datetime.timedelta(days=day, hours=hour)
                # only the remaining part of the current hour
                factor = 1.0 - now.minute / 60.0 if day == 0 and hour == now.hour else 1.0
                forecast = self.get_forecast(timestamp)
                hour_kwh = []
                hour_sell = []
                database_used = False
                if forecast is not None:
                    kwh, sell, known = self._database_solar(self.forecast_score(forecast), timestamp)
                    prediction["total_database"].append(round(kwh, 1))
                    prediction["total_database_sell"].append(round(sell, sell_decimals))
                    # no solar history for the hour and weather: Solcast alone instead of averaging with an unknown 0
                    if known or timestamp not in solcast:
                        database_used = True
                        weight = (0.5 if timestamp in solcast else 1.0) * factor
                        hour_kwh.append(round(kwh * weight, 1))
                        hour_sell.append(round(sell * weight, sell_decimals))
                if timestamp in solcast:
                    kwh, sell = solcast[timestamp]
                    weight = (1.5 if database_used else 1.0) * factor
                    hour_kwh.append(round(kwh * weight, 1))
                    hour_sell.append(round(sell * weight, sell_decimals))
                    prediction["total_forecast"].append(round(kwh, 1))
                    prediction["total_forecast_sell"].append(round(sell, sell_decimals))
                if hour_kwh:
                    # an earlier hour without any value: 0.0 so this hour keeps its index
                    padding = [0.0] * (hour - len(prediction["total"]))
                    prediction["total"].extend(padding)
                    prediction["total_sell"].extend(padding)
                    prediction["total"].append(round(average(hour_kwh), 1))
                    prediction["total_sell"].append(round(average(hour_sell), sell_decimals))
                    known_hours += 1
            prediction["known_hours"] = known_hours
            self.solar_prediction_db[day] = prediction
