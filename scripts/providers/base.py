from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Protocol


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def decimal_or_none(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return Decimal(s)


def int_or_none(raw: str | None) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def decimal_to_str(d: Decimal | None) -> str:
    if d is None:
        return ""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


@dataclass(frozen=True)
class PriceEODRow:
    internal_symbol: str
    trading_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    adj_close: Decimal | None
    volume: int | None
    currency: str | None
    source: str
    quality_flags: dict[str, Any]


@dataclass(frozen=True)
class DividendRow:
    internal_symbol: str
    ex_date: date
    pay_date: date | None
    amount: Decimal
    currency: str | None
    source: str
    quality_flags: dict[str, Any]


@dataclass(frozen=True)
class SplitRow:
    internal_symbol: str
    ex_date: date
    ratio: Decimal
    source: str
    quality_flags: dict[str, Any]


@dataclass(frozen=True)
class CorporateActions:
    dividends: list[DividendRow]
    splits: list[SplitRow]


class MarketDataProvider(Protocol):
    name: str

    def fetch_prices_eod(
        self,
        *,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
        offline: bool,
        cache_dir: str,
        symbol_map: dict[str, str] | None = None,
    ) -> list[PriceEODRow]:
        ...

    def fetch_corporate_actions(
        self,
        *,
        symbols: Iterable[str],
        start_date: date,
        end_date: date,
        offline: bool,
        cache_dir: str,
        symbol_map: dict[str, str] | None = None,
    ) -> CorporateActions:
        ...

