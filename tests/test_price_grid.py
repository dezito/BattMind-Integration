"""Providers with different price intervals are merged on one common interval (same rule as Cable Juice Planner)."""
from __future__ import annotations

import datetime
import logging
from types import SimpleNamespace

import pytest

from custom_components.battmind.const import build_config, default_options
from custom_components.battmind.prices import CombinedPriceProvider
from custom_components.battmind.prices.intervals import common_interval_minutes, price_grid_minutes

DAY = datetime.date(2026, 11, 22)
NEXT_DAY = DAY + datetime.timedelta(days=1)


def _at(day: datetime.date, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime.combine(day, datetime.time(hour, minute))


def _series(day: datetime.date, minutes: int, price, hours=range(24)) -> dict:
    """{timestamp: price} every `minutes` minutes of the hours; price(hour, minute)."""
    return {
        _at(day, hour, minute): price(hour, minute)
        for hour in hours
        for minute in range(0, 60, minutes)
    }


def _stub(name: str, real: dict, forecast: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        source_name=name,
        buy_real_prices=real,
        buy_forecast_prices=forecast or {},
        sell_real_prices=real,
        sell_forecast_prices=forecast or {},
    )


def _combine(*providers: SimpleNamespace, kind: str = "buy"):
    return CombinedPriceProvider(build_config(default_options()), list(providers))._combine(kind)


def test_an_hourly_price_covers_every_quarter_of_its_hour_before_a_lower_provider() -> None:
    """The reported case: Strømligning hourly (:00 only) next to Energi Data Service every 15 minutes."""
    hourly = _stub("stromligning", _series(DAY, 60, lambda h, m: 2.0 + h / 100, hours=range(12)))
    quarters = _stub("energidataservice", _series(DAY, 15, lambda h, m: 2.5 + h / 100))
    prices, real, sources = _combine(hourly, quarters)

    assert len(prices) == 96 and real == prices
    for hour in range(12):
        for minute in (0, 15, 30, 45):
            assert prices[_at(DAY, hour, minute)] == pytest.approx(2.0 + hour / 100)
            assert sources[_at(DAY, hour, minute)] == "stromligning:real"
    for hour in range(12, 24):
        for minute in (0, 15, 30, 45):
            assert prices[_at(DAY, hour, minute)] == pytest.approx(2.5 + hour / 100)
            assert sources[_at(DAY, hour, minute)] == "energidataservice:real"


def test_the_periods_of_an_hour_are_the_same_for_every_hour() -> None:
    hourly = _stub("stromligning", _series(DAY, 60, lambda h, m: 1.0, hours=range(12)))
    quarters = _stub("energidataservice", _series(DAY, 15, lambda h, m: 2.0))
    provider = CombinedPriceProvider(build_config(default_options()), [hourly, quarters])
    provider.buy_prices, provider.buy_real_prices, provider.buy_price_sources = provider._combine("buy")
    provider.sell_prices, provider.sell_real_prices, provider.sell_price_sources = provider._combine("sell")
    assert {provider.buy_periods_in_hour(_at(DAY, hour)) for hour in range(24)} == {4}
    assert {provider.sell_periods_in_hour(_at(DAY, hour)) for hour in range(24)} == {4}


def test_a_forecast_only_sell_hour_counts_its_periods() -> None:
    quarters = _stub("energidataservice", _series(DAY, 15, lambda h, m: 2.0), _series(NEXT_DAY, 15, lambda h, m: 2.0))
    provider = CombinedPriceProvider(build_config(default_options()), [quarters])
    provider.sell_prices, provider.sell_real_prices, provider.sell_price_sources = provider._combine("sell")
    assert provider.sell_periods_in_hour(_at(NEXT_DAY, 5)) == 4


def test_hourly_providers_alone_keep_one_price_per_hour() -> None:
    first = _stub("stromligning", _series(DAY, 60, lambda h, m: 1.0, hours=range(10)))
    second = _stub("energidataservice", _series(DAY, 60, lambda h, m: 3.0))
    prices, _real, sources = _combine(first, second)
    assert len(prices) == 24 and all(timestamp.minute == 0 for timestamp in prices)
    assert sources[_at(DAY, 9)] == "stromligning:real" and sources[_at(DAY, 10)] == "energidataservice:real"


def test_forecast_hours_of_an_hourly_provider_get_every_slot_of_the_grid() -> None:
    hourly = _stub("stromligning", {}, _series(NEXT_DAY, 60, lambda h, m: 2.0))
    quarters = _stub("energidataservice", _series(DAY, 15, lambda h, m: 3.0))
    prices, real, sources = _combine(hourly, quarters)
    assert sum(1 for timestamp in prices if timestamp.date() == NEXT_DAY) == 96
    assert all(sources[timestamp] == "stromligning:forecast" for timestamp in prices if timestamp.date() == NEXT_DAY)
    assert all(timestamp.date() == DAY for timestamp in real)


def test_a_real_price_of_a_lower_provider_beats_a_forecast_of_a_higher_one() -> None:
    hourly = _stub("stromligning", {}, _series(DAY, 60, lambda h, m: 1.0))
    quarters = _stub("energidataservice", _series(DAY, 15, lambda h, m: 3.0))
    prices, _real, sources = _combine(hourly, quarters)
    assert set(sources.values()) == {"energidataservice:real"} and set(prices.values()) == {3.0}


@pytest.mark.parametrize(
    ("minutes", "grid"),
    [((60, 15), 15), ((60, 30), 30), ((60, 10), 10), ((10, 15), 5), ((60, 30, 10), 10), ((30, 20), 10), ((60,), 60)],
)
def test_the_grid_is_the_greatest_common_divisor_of_the_provider_intervals(minutes, grid) -> None:
    providers = [
        _stub(f"provider{index}", _series(DAY, step, lambda h, m, index=index: 1.0 + index))
        for index, step in enumerate(minutes)
    ]
    prices, _real, sources = _combine(*providers)
    assert len(prices) == 24 * 60 // grid
    assert {timestamp.minute % grid for timestamp in prices} == {0}
    assert set(sources.values()) == {"provider0:real"}
    assert price_grid_minutes(prices) == grid


def test_a_ten_and_a_fifteen_minute_provider_meet_on_five_minutes() -> None:
    ten = _stub("stromligning", _series(DAY, 10, lambda h, m: 1.0, hours=range(6)))
    fifteen = _stub("energidataservice", _series(DAY, 15, lambda h, m: 2.0))
    prices, _real, sources = _combine(ten, fifteen)
    assert len(prices) == 288
    assert prices[_at(DAY, 5, 5)] == 1.0 and prices[_at(DAY, 5, 50)] == 1.0
    assert sources[_at(DAY, 6, 5)] == "energidataservice:real" and prices[_at(DAY, 6, 10)] == 2.0


def test_three_providers_each_fill_the_slots_the_higher_ones_do_not_cover() -> None:
    hourly = _stub("stromligning", _series(DAY, 60, lambda h, m: 1.0, hours=range(8)))
    half = _stub("middle", _series(DAY, 30, lambda h, m: 2.0, hours=range(4, 16)))
    quarters = _stub("energidataservice", _series(DAY, 15, lambda h, m: 3.0))
    prices, _real, sources = _combine(hourly, half, quarters)
    assert len(prices) == 96
    assert {sources[_at(DAY, 7, minute)] for minute in (0, 15, 30, 45)} == {"stromligning:real"}
    assert {sources[_at(DAY, 8, minute)] for minute in (0, 15, 30, 45)} == {"middle:real"}
    assert {sources[_at(DAY, 16, minute)] for minute in (0, 15, 30, 45)} == {"energidataservice:real"}


def test_an_unusable_quarter_is_filled_by_the_next_provider_and_never_stretches_its_neighbour() -> None:
    quarters = _series(DAY, 15, lambda h, m: 1.0, hours=range(6))
    quarters[_at(DAY, 2, 15)] = float("nan")
    first = _stub("stromligning", quarters)
    second = _stub("energidataservice", _series(DAY, 60, lambda h, m: 9.0))
    prices, _real, sources = _combine(first, second)
    assert prices[_at(DAY, 2, 0)] == 1.0 and prices[_at(DAY, 2, 30)] == 1.0
    assert prices[_at(DAY, 2, 15)] == 9.0 and sources[_at(DAY, 2, 15)] == "energidataservice:real"


def test_an_interval_that_does_not_divide_the_hour_is_ignored_with_a_warning_once(caplog) -> None:
    from custom_components.battmind.prices import intervals

    intervals._WARNED_INTERVALS.clear()
    odd = {_at(DAY, 0, 0): 1.0, _at(DAY, 0, 45): 1.1, _at(DAY, 1, 30): 1.2, _at(DAY, 2, 15): 1.3}
    hourly = _stub("energidataservice", _series(DAY, 60, lambda h, m: 2.0, hours=range(4)))
    with caplog.at_level(logging.WARNING):
        prices, _real, _sources = _combine(_stub("stromligning", odd), hourly)
        _combine(_stub("stromligning", odd), hourly)
    # 45 minutes does not divide an hour, 15 and 30 do: the grid is 15 minutes
    assert price_grid_minutes(prices) == 15
    assert len([record for record in caplog.records if "interval of 45" in record.getMessage()]) == 1


def test_a_row_the_provider_left_out_is_a_gap_and_never_a_longer_price() -> None:
    """The providers drop a price that is no number, so it is a missing row: the price before it must not cover it."""
    quarters = _series(DAY, 15, lambda h, m: 1.0, hours=range(6))
    del quarters[_at(DAY, 2, 15)]
    del quarters[_at(DAY, 4, 15)], quarters[_at(DAY, 4, 30)]
    first = _stub("stromligning", quarters)
    second = _stub("energidataservice", _series(DAY, 15, lambda h, m: 9.0))
    prices, real, sources = _combine(first, second)
    for slot in (_at(DAY, 2, 15), _at(DAY, 4, 15), _at(DAY, 4, 30)):
        assert prices[slot] == 9.0 and sources[slot] == "energidataservice:real"
    assert prices[_at(DAY, 2, 0)] == 1.0 and prices[_at(DAY, 2, 30)] == 1.0 and prices[_at(DAY, 4, 45)] == 1.0
    alone, alone_real, _sources = _combine(first)
    assert _at(DAY, 2, 15) not in alone and _at(DAY, 4, 15) not in alone and _at(DAY, 4, 30) not in alone
    assert alone_real == alone and len(alone) == 6 * 4 - 3


def test_an_hourly_day_and_a_quarter_day_of_one_provider_keep_their_own_interval() -> None:
    mixed = {**_series(DAY, 60, lambda h, m: 1.0), **_series(NEXT_DAY, 15, lambda h, m: 2.0)}
    del mixed[_at(DAY, 5)]
    below = _stub("energidataservice", _series(DAY, 15, lambda h, m: 9.0), _series(NEXT_DAY, 15, lambda h, m: 9.0))
    prices, _real, sources = _combine(_stub("stromligning", mixed), below)
    # the hourly day is hourly: its prices cover the whole hour, the lower provider only fills the missing 05:00 hour
    for minute in (0, 15, 30, 45):
        assert prices[_at(DAY, 4, minute)] == 1.0 and sources[_at(DAY, 4, minute)] == "stromligning:real"
        assert prices[_at(DAY, 5, minute)] == 9.0 and sources[_at(DAY, 5, minute)] == "energidataservice:real"
    # the quarter day keeps its quarters
    assert {prices[_at(NEXT_DAY, 3, minute)] for minute in (0, 15, 30, 45)} == {2.0}
    assert len([timestamp for timestamp in prices if timestamp.date() == NEXT_DAY]) == 96


def test_a_tie_between_two_gaps_takes_the_smaller_and_a_day_with_one_price_keeps_its_hour() -> None:
    from custom_components.battmind.prices.intervals import provider_intervals

    half = datetime.timedelta(minutes=30)
    # gaps 30, 30, 60: 30 is the most common, so no price covers more than 30 minutes that day
    tie = {_at(DAY, 0): 1.0, _at(DAY, 0, 30): 1.0, _at(DAY, 1): 1.0, _at(DAY, 2): 1.0}
    assert [end - start for start, end, _price in provider_intervals(tie)] == [half, half, half, half]
    # gaps 30, 60, 60: 60 is the most common
    even = {_at(DAY, 0): 1.0, _at(DAY, 0, 30): 1.0, _at(DAY, 1, 30): 1.0, _at(DAY, 2, 30): 1.0}
    assert provider_intervals(even)[0][1] == _at(DAY, 0, 30)
    # gaps 15, 45, 15: two quarters and one 45 minute gap, the quarter is the most common
    tied = {_at(DAY, 0): 1.0, _at(DAY, 0, 15): 1.0, _at(DAY, 1): 1.0, _at(DAY, 1, 15): 1.0}
    assert provider_intervals(tied)[0][1] == _at(DAY, 0, 15)
    assert provider_intervals({_at(DAY, 7): 1.0}) == [(_at(DAY, 7), _at(DAY, 8), 1.0)]


def test_a_provider_that_cannot_be_read_is_skipped_and_the_others_are_used() -> None:
    broken = _stub("stromligning", {"not a time": 1.0})
    prices, _real, sources = _combine(broken, _stub("energidataservice", _series(DAY, 60, lambda h, m: 3.0)))
    assert len(prices) == 24 and set(sources.values()) == {"energidataservice:real"}


def test_a_real_provider_drops_a_price_that_is_no_number_and_it_is_missing_from_the_combined_prices() -> None:
    """Through the real provider class: 10:15 is NaN in a 15 minute feed, so the combined prices have no 10:15."""
    from custom_components.battmind.prices import create_price_provider

    config = build_config({**default_options(), "energidataservice_buy_entity_id": "sensor.eds"})
    provider = create_price_provider(config)
    rows = [
        {"hour": (datetime.datetime.combine(DAY, datetime.time(0)) + datetime.timedelta(minutes=15 * index)).isoformat() + "+01:00",
         "price": float("nan") if index == 41 else 2.0}
        for index in range(96)
    ]
    provider.update_prices({"attributes": {"sensor.eds": {"raw_today": rows}}, "values": {}}, _at(DAY, 9))
    real = provider.get_buy_real_prices()
    assert _at(DAY, 10, 15) not in real and _at(DAY, 10) in real and _at(DAY, 10, 30) in real and len(real) == 95
    # through the combined prices (the ones the planner reads): 10:15 is not made up from 10:00
    combined = provider.get_buy_prices()
    assert _at(DAY, 10, 15) not in combined and len(combined) == 95


def test_interval_helpers() -> None:
    assert common_interval_minutes([datetime.timedelta(minutes=60), datetime.timedelta(minutes=15)]) == 15
    assert common_interval_minutes([]) == 60
    assert price_grid_minutes({}) == 60
    assert price_grid_minutes(_series(DAY, 30, lambda h, m: 1.0)) == 30
