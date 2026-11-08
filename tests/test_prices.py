"""Sell prices from the price providers."""
from __future__ import annotations

import datetime
import math

import pytest

from custom_components.battmind.const import build_config, default_options
from custom_components.battmind.prices import CombinedPriceProvider, create_price_provider
from custom_components.battmind.prices.base import calculate_sell_price, is_number
from custom_components.battmind.prices.energidataservice import EnergiDataServicePriceProvider
from custom_components.battmind.prices.stromligning import StromligningPriceProvider

TODAY = datetime.date(2026, 10, 30)
TOMORROW = TODAY + datetime.timedelta(days=1)
# Strømligning tariffs ex VAT (a real export): distribution per period, the rest flat
DISTRIBUTION = {hour: 0.9358 if 17 <= hour <= 20 else 0.3119 for hour in range(24)}
FLAT_TARIFFS = {
    "sensor.sell_tax": 0.008,
    "sensor.sell_nettariff": 0.043,
    "sensor.sell_systemtariff": 0.072,
    "sensor.sell_surcharge": 0.04,
}
SPOT = {TODAY: 2.611, TOMORROW: 2.21}


def _rows(day: datetime.date, price) -> list[dict]:
    return [
        {"start": datetime.datetime.combine(day, datetime.time(hour)).isoformat() + "+01:00", "price": price(hour)}
        for hour in range(24)
    ]


def _sell_with_tariffs(day: datetime.date):
    """Strømligning's sell sensor is the consumer price: spot plus every tariff."""
    return lambda hour: SPOT[day] + DISTRIBUTION[hour] + sum(FLAT_TARIFFS.values())


def _stromligning(tariff_overrides: dict | None = None) -> tuple[dict, dict]:
    """(config, price data) of Strømligning with every tariff sensor; tariff_overrides replaces tariff states."""
    config = build_config({
        **default_options(),
        "stromligning_buy_current_price": "sensor.buy",
        "stromligning_sell_current_price": "sensor.sell",
        "stromligning_sell_tomorrow_available": "binary_sensor.sell_tomorrow",
        "stromligning_sell_distribution": "sensor.sell_distribution",
        "stromligning_sell_electricity_tax": "sensor.sell_tax",
        "stromligning_sell_nettariff": "sensor.sell_nettariff",
        "stromligning_sell_systemtariff": "sensor.sell_systemtariff",
        "stromligning_sell_surcharge": "sensor.sell_surcharge",
        "sell_fee": 0.03,
    })
    data = {
        "attributes": {
            "sensor.buy": {"prices": _rows(TODAY, lambda hour: 4.0)},
            "sensor.sell": {"prices": _rows(TODAY, _sell_with_tariffs(TODAY))},
            "binary_sensor.sell_tomorrow": {"forecast_data": False, "prices": _rows(TOMORROW, _sell_with_tariffs(TOMORROW))},
            # like the real sensor: only today's distribution tariffs
            "sensor.sell_distribution": {"prices": _rows(TODAY, DISTRIBUTION.get)},
            **{entity_id: {} for entity_id in FLAT_TARIFFS},
        },
        "values": {**FLAT_TARIFFS, **(tariff_overrides or {})},
    }
    return config, data


def _provider() -> StromligningPriceProvider:
    config, data = _stromligning()
    provider = StromligningPriceProvider(config)
    provider.update(data, datetime.datetime.combine(TODAY, datetime.time(16, 40)))
    return provider


@pytest.mark.parametrize("day", [TODAY, TOMORROW])
@pytest.mark.parametrize("hour", [3, 17, 20, 21])
def test_stromligning_sell_price_is_spot_minus_the_selling_costs(day: datetime.date, hour: int) -> None:
    """Every tariff in the sell sensor is subtracted, also the distribution tariff of tomorrow,
    and selling costs the sell fee plus Energinet's net and system tariff."""
    provider = _provider()
    timestamp = datetime.datetime.combine(day, datetime.time(hour))
    assert provider.sell_real_prices[timestamp] == pytest.approx(round(SPOT[day] - 0.03 - 0.043 - 0.072, 2))


def test_selling_costs_lower_the_sell_price() -> None:
    tariffs = {"transmissions_nettarif": 0.1, "systemtarif": 0.1, "elafgift": 0.5, "tariffs": 0.2, "surcharge": 0.0}
    # 2.0 - 0.9 tariffs in the price - (0.03 fee + 0.1 net + 0.1 system tariff)
    assert calculate_sell_price(2.0, tariffs, 0.03) == pytest.approx(0.87)
    assert calculate_sell_price(2.0, tariffs, -0.03) == pytest.approx(0.87)
    # a raw price without tariffs: only the selling costs
    raw = {key: 0.0 for key in tariffs}
    assert calculate_sell_price(2.0, raw, 0.03, {"transmissions_nettarif": 0.05, "systemtarif": 0.09}) == pytest.approx(1.83)


def _eds_provider(sell_tariffs: bool) -> EnergiDataServicePriceProvider:
    config = build_config({
        **default_options(),
        "energidataservice_buy_entity_id": "sensor.eds_buy",
        "energidataservice_sell_entity_id": "sensor.eds_sell",
        "sell_fee": 0.03,
    })
    tariffs = {
        "additional_tariffs": {"transmissions_nettarif": 0.054, "systemtarif": 0.09, "elafgift": 0.01},
        "tariffs": {str(hour): 1.17 if 17 <= hour <= 20 else 0.39 for hour in range(24)},
    }
    rows = [{"hour": datetime.datetime.combine(TODAY, datetime.time(hour)).isoformat() + "+01:00", "price": 2.5} for hour in range(24)]
    sell = {"raw_today": rows, "tomorrow_valid": False}
    if sell_tariffs:
        sell["tariffs"] = tariffs
    provider = EnergiDataServicePriceProvider(config)
    provider.update(
        {"attributes": {"sensor.eds_buy": {"raw_today": rows, "tomorrow_valid": False, "tariffs": tariffs}, "sensor.eds_sell": sell}, "values": {}},
        datetime.datetime.combine(TODAY, datetime.time(16, 40)),
    )
    return provider


def test_raw_energi_data_service_sell_price_gets_the_selling_costs_from_the_buy_sensor() -> None:
    """The sell sensor without tariffs is the spot price: only fee, net and system tariff are subtracted."""
    timestamp = datetime.datetime.combine(TODAY, datetime.time(17))
    assert _eds_provider(False).sell_real_prices[timestamp] == pytest.approx(round(2.5 - 0.03 - 0.054 - 0.09, 2))
    # with tariffs in the sell sensor they are subtracted, plus the selling costs
    assert _eds_provider(True).sell_real_prices[timestamp] == pytest.approx(round(2.5 - 1.324 - 0.03 - 0.054 - 0.09, 2))


@pytest.mark.parametrize(("production_price", "sign"), [(-1.0, -1.0), (1.0, 1.0)], ids=["hourly sell price", "fixed solar sell price"])
def test_forecast_adder_makes_a_forecast_buy_dearer_and_a_forecast_sale_worth_less(production_price: float, sign: float) -> None:
    """A forecast price gets 1/30 per day ahead to prefer known prices: buying costs more, selling earns less.

    A sold kWh is worth the sell price, or with a fixed solar sell price fixed - the sell price, so there the
    sell price has to go up."""
    provider = create_price_provider(build_config({
        **default_options(),
        "energidataservice_buy_entity_id": "sensor.eds_buy",
        "energidataservice_sell_entity_id": "sensor.eds_sell",
        "sell_fee": 0.0,
        "production_price": production_price,
    }))
    forecast = [{"hour": datetime.datetime.combine(TOMORROW, datetime.time(hour)).isoformat() + "+01:00", "price": 2.0} for hour in range(24)]
    entity = {"raw_today": [], "tomorrow_valid": False, "forecast": forecast}
    provider.update_prices(
        {"attributes": {"sensor.eds_buy": entity, "sensor.eds_sell": entity}, "values": {}},
        datetime.datetime.combine(TODAY, datetime.time(16, 40)),
    )
    tomorrow = datetime.datetime.combine(TOMORROW, datetime.time(8))
    adder = 1 / 30
    assert provider.get_buy_forecast_prices()[tomorrow] == pytest.approx(2.0 + adder, abs=0.006)
    assert provider.get_sell_forecast_prices()[tomorrow] == pytest.approx(2.0 + sign * adder, abs=0.006)
    sale_value = (lambda price: price) if production_price == -1.0 else (lambda price: production_price - price)
    assert sale_value(provider.get_sell_forecast_prices()[tomorrow]) < sale_value(2.0)
    # without the adder (the recorded forecast) both are the plain forecast
    assert provider.get_buy_forecast_prices(adder=False)[tomorrow] == pytest.approx(2.0, abs=0.006)
    assert provider.get_sell_forecast_prices(adder=False)[tomorrow] == pytest.approx(2.0, abs=0.006)


NOT_FINITE = [float("nan"), float("inf"), float("-inf")]
NOT_FINITE_IDS = ["nan", "inf", "-inf"]
NOW = datetime.datetime.combine(TODAY, datetime.time(16, 40))


def _eds_combined(buy: dict, sell: dict):
    provider = create_price_provider(build_config({
        **default_options(),
        "energidataservice_buy_entity_id": "sensor.eds_buy",
        "energidataservice_sell_entity_id": "sensor.eds_sell",
        "sell_fee": 0.03,
    }))
    provider.update_prices({"attributes": {"sensor.eds_buy": buy, "sensor.eds_sell": sell}, "values": {}}, NOW)
    return provider


def _eds_today(bad_hour: int | None = None, bad=None) -> list[dict]:
    rows = [{"hour": datetime.datetime.combine(TODAY, datetime.time(hour)).isoformat() + "+01:00", "price": 2.5} for hour in range(24)]
    if bad_hour is not None:
        rows[bad_hour]["price"] = bad
    return rows


@pytest.mark.parametrize("bad", NOT_FINITE, ids=NOT_FINITE_IDS)
@pytest.mark.parametrize("broken", ["buy", "sell"])
def test_a_price_that_is_not_a_finite_number_is_dropped(broken: str, bad: float) -> None:
    """NaN or infinity as a price would make every total of the plan NaN: the hour has no price instead."""
    good, wrong = {"raw_today": _eds_today(), "tomorrow_valid": False}, {"raw_today": _eds_today(14, bad), "tomorrow_valid": False}
    provider = _eds_combined(wrong if broken == "buy" else good, wrong if broken == "sell" else good)
    prices = provider.get_buy_prices() if broken == "buy" else provider.get_sell_prices()
    assert datetime.datetime.combine(TODAY, datetime.time(14)) not in prices
    assert len(prices) == 23 and all(math.isfinite(price) for price in prices.values())


@pytest.mark.parametrize("bad", NOT_FINITE, ids=NOT_FINITE_IDS)
def test_tariffs_that_are_not_finite_numbers_drop_the_sell_prices_instead_of_zero_tariffs(bad: float) -> None:
    """Broken tariffs are not "no tariffs": a sell price without them would be too high, so it is dropped."""
    broken = {"additional_tariffs": {"transmissions_nettarif": bad, "systemtarif": 0.09, "elafgift": 0.01}, "tariffs": {str(h): 0.39 for h in range(24)}}
    plain = {"raw_today": _eds_today(), "tomorrow_valid": False}
    # tariffs in the sell sensor
    provider = _eds_combined(plain, {**plain, "tariffs": broken})
    assert provider.get_sell_prices() == {} and len(provider.get_buy_prices()) == 24
    # a raw sell sensor takes the selling costs from the buy sensor's tariffs
    provider = _eds_combined({**plain, "tariffs": broken}, plain)
    assert provider.get_sell_prices() == {} and len(provider.get_buy_prices()) == 24


@pytest.mark.parametrize("bad", NOT_FINITE, ids=NOT_FINITE_IDS)
def test_stromligning_tariff_that_is_not_a_finite_number_drops_the_sell_prices(bad: float) -> None:
    config, data = _stromligning({"sensor.sell_nettariff": bad})
    provider = CombinedPriceProvider(config, [StromligningPriceProvider(config)])
    provider.update_prices(data, NOW)
    assert provider.get_sell_prices() == {} and provider.get_buy_prices()
    # an unavailable tariff is 0 as before
    config, data = _stromligning()
    del data["values"]["sensor.sell_nettariff"]
    provider = CombinedPriceProvider(config, [StromligningPriceProvider(config)])
    provider.update_prices(data, NOW)
    assert provider.get_sell_prices()


def test_is_number_accepts_only_finite_numbers() -> None:
    assert is_number(1) and is_number(-0.5) and is_number(0.0)
    assert not any(is_number(value) for value in (None, "1.0", True, float("nan"), float("inf"), float("-inf")))


class _State:
    def __init__(self, state: str, attributes: dict | None = None) -> None:
        self.state = state
        self.attributes = attributes or {}


def _both_providers():
    return create_price_provider(build_config({
        **default_options(),
        "energidataservice_buy_entity_id": "sensor.eds",
        "energidataservice_sell_entity_id": "sensor.eds_sell",
        "stromligning_buy_current_price": "sensor.stromligning",
        "stromligning_sell_nettariff": "sensor.sell_nettariff",
    }))


def test_stromligning_has_priority_over_energi_data_service() -> None:
    provider = _both_providers()
    assert [type(p) for p in provider.providers] == [StromligningPriceProvider, EnergiDataServicePriceProvider]
    assert provider.buy_price_entity_ids() == ["sensor.stromligning", "sensor.eds"]
    # recorded history: per hour Strømligning first, Energi Data Service fills the gaps
    assert provider.combine_history({
        "sensor.eds": {1.0: 3.0, 2.0: 3.0},
        "sensor.stromligning": {1.0: 1.0},
        "sensor.other": {3.0: 9.0},
    }) == {1.0: 1.0, 2.0: 3.0}
    # the what-if history sources in the same order
    states = {"sensor.eds_sell": _State("1.0", {"tariffs": {}})}
    sources = provider.history_sources(states.get)
    assert [source["name"] for source in sources] == ["Strømligning", "Energi Data Service"]
    assert sources[0]["sell_tariff_entity_ids"] == {"transmissions_nettarif": "sensor.sell_nettariff"}
    assert sources[1]["sell_entity_id"] == "sensor.eds_sell" and sources[1]["sell_includes_tariffs"] is False


def test_combined_provider_reads_the_price_entities_and_merges_the_prices() -> None:
    provider = _both_providers()
    eds = {
        "raw_today": [{"hour": row["start"], "price": 3.0} for row in _rows(TODAY, lambda hour: 0.0)],
        "forecast": [{"hour": row["start"], "price": 2.0} for row in _rows(TOMORROW, lambda hour: 0.0)],
    }
    states = {
        # Strømligning only has the first 12 hours
        "sensor.stromligning": _State("2.0", {"prices": _rows(TODAY, lambda hour: 1.0)[:12]}),
        "sensor.eds": _State("3.0", eds),
        "sensor.eds_sell": _State("3.0", eds),
        "sensor.sell_nettariff": _State("0.05"),
    }
    snapshot = provider.snapshot(states.get)
    assert set(snapshot["attributes"]) == {"sensor.stromligning", "sensor.eds", "sensor.eds_sell", "sensor.sell_nettariff"}
    # only the tariff sensors are read as a state
    assert snapshot["values"] == {"sensor.sell_nettariff": 0.05}

    now = datetime.datetime.combine(TODAY, datetime.time(10, 30))
    provider.update_prices(snapshot, now)
    morning, evening = (datetime.datetime.combine(TODAY, datetime.time(hour)) for hour in (8, 20))
    assert provider.get_buy_price(now) == 1.0
    assert provider.get_buy_prices()[morning] == 1.0 and provider.price_source(morning) == "stromligning:real"
    assert provider.get_buy_prices()[evening] == 3.0 and provider.price_source(evening) == "energidataservice:real"
    tomorrow = datetime.datetime.combine(TOMORROW, datetime.time(8))
    assert provider.is_forecast(tomorrow)
    assert provider.get_buy_forecast_prices()[tomorrow] > provider.get_buy_forecast_prices(adder=False)[tomorrow] == pytest.approx(2.0, abs=0.005)
    assert tomorrow not in provider.get_buy_real_prices()
    assert provider.buy_price_entity_id() == "sensor.stromligning, sensor.eds"
