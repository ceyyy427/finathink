"""User declarations for archived marks; never convert or infer units."""
from collections.abc import Mapping


def snapshot_units(value):
    if value is None or value == {}:
        return {}
    if not isinstance(value, Mapping) or set(value) != {"price_currency", "volume_unit"}:
        raise ValueError("declare both price currency and volume unit")
    if value["price_currency"] not in ("CNY", "USD", "HKD", "EUR", "GBP", "JPY", "UNKNOWN"):
        raise ValueError("unsupported price currency declaration")
    if value["volume_unit"] not in ("shares", "units", "UNKNOWN"):
        raise ValueError("volume must already be normalized to tradable units")
    return dict(value)


def snapshot_asset_classes(value):
    """Optional explicit classifications, validated before any data request."""
    import re
    if value is None or value == {}:
        return {}
    if not isinstance(value, Mapping) or not 1 <= len(value) <= 20 or any(
        not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', key)
        or item not in ('EQUITY', 'ETF') for key, item in value.items()
    ):
        raise ValueError('asset classes require at most 20 explicit EQUITY/ETF declarations')
    return dict(value)
