from __future__ import annotations

from providers.base import MarketDataProvider
from providers.stooq import StooqProvider


def get_provider(name: str) -> MarketDataProvider:
    key = (name or "").strip().lower()
    if key in ("stooq", ""):
        return StooqProvider()
    raise ValueError(f"Unknown MARKET_PROVIDER: {name!r}")
