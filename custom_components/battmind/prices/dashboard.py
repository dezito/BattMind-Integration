"""The price entities of the dashboard templates: which configured price entity stands in for which template entity."""
from __future__ import annotations


def price_dashboard_entities(prices: dict[str, str]) -> dict[str, str]:
    """Template entity id -> configured price entity id ("" = not configured), for the price chart of the dashboard."""
    sl_buy = prices["stromligning_buy_current_price"]
    mapping = {
        "sensor.energi_data_service": prices["energidataservice_buy_entity_id"],
        "sensor.energi_data_service_salg": prices["energidataservice_sell_entity_id"],
        "sensor.stromligning_current_price_vat": sl_buy,
        "sensor.stromligning_forecasts_vat": prices["stromligning_buy_forecasts"],
        "binary_sensor.stromligning_tomorrow_available_vat": prices["stromligning_buy_tomorrow_available"],
        # the sell price chart uses the configured sell sensors and tariffs, like the planner
        "sensor.stromligning_15min_current_price_ex_vat": prices["stromligning_sell_current_price"],
        "sensor.stromligning_15min_forecasts_ex_vat": prices["stromligning_sell_forecasts"],
        "binary_sensor.stromligning_15min_tomorrow_available_ex_vat": prices["stromligning_sell_tomorrow_available"],
        "sensor.stromligning_15min_distribution_ex_vat": prices["stromligning_sell_distribution"],
        "sensor.stromligning_15min_electricity_tax_ex_vat": prices["stromligning_sell_electricity_tax"],
        "sensor.stromligning_15min_nettariff_ex_vat": prices["stromligning_sell_nettariff"],
        "sensor.stromligning_15min_systemtariff_ex_vat": prices["stromligning_sell_systemtariff"],
        "sensor.stromligning_15min_surcharge_ex_vat": prices["stromligning_sell_surcharge"],
    }
    if sl_buy:
        # one price chart: Strømligning is preferred, the Energi Data Service chart is removed
        mapping["sensor.energi_data_service"] = ""
        mapping["sensor.energi_data_service_salg"] = ""
    return mapping
