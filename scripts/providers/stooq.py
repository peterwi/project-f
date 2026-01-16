from __future__ import annotations

import csv
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from providers.base import CorporateActions, PriceEODRow, decimal_or_none, int_or_none


@dataclass(frozen=True)
class _StooqRow:
    trading_date: date
    open: str | None
    high: str | None
    low: str | None
    close: str | None
    volume: str | None


class StooqProvider:
    name = "stooq"

    def _stooq_url(self, stooq_symbol: str) -> str:
        return f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"

    def _download(self, url: str, timeout_s: int) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "trading-ops/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.read()

    def _parse_csv(self, content: bytes) -> list[_StooqRow]:
        text = content.decode("utf-8", errors="replace")
        reader = csv.DictReader(text.splitlines())
        rows: list[_StooqRow] = []
        for r in reader:
            ds = (r.get("Date") or "").strip()
            if not ds:
                continue
            rows.append(
                _StooqRow(
                    trading_date=date.fromisoformat(ds),
                    open=(r.get("Open") or "").strip() or None,
                    high=(r.get("High") or "").strip() or None,
                    low=(r.get("Low") or "").strip() or None,
                    close=(r.get("Close") or "").strip() or None,
                    volume=(r.get("Volume") or "").strip() or None,
                )
            )
        return rows

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
        timeout_s = int(os.environ.get("MARKET_HTTP_TIMEOUT_SECONDS", "20") or "20")
        sym_map = symbol_map or {}
        cache_root = Path(cache_dir)
        raw_dir = cache_root / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        out: list[PriceEODRow] = []
        for internal_symbol in sorted(set(s.strip() for s in symbols if s and s.strip())):
            stooq_symbol = (sym_map.get(internal_symbol) or "").strip()
            if not stooq_symbol:
                raise ValueError(f"Missing stooq symbol mapping for internal_symbol={internal_symbol!r}")

            raw_path = raw_dir / f"prices_eod_{internal_symbol}.csv"
            if offline:
                if not raw_path.exists():
                    raise FileNotFoundError(f"Offline mode: missing cache file {raw_path}")
                content = raw_path.read_bytes()
            else:
                url = self._stooq_url(stooq_symbol)
                try:
                    content = self._download(url, timeout_s=timeout_s)
                except urllib.error.URLError as e:
                    raise RuntimeError(f"Failed to download {url}: {e}") from e
                raw_path.write_bytes(content)

            parsed = self._parse_csv(content)
            if not parsed:
                raise RuntimeError(f"No rows parsed from Stooq for {internal_symbol} ({stooq_symbol})")

            # Stooq has no adj_close column; in v1 we use adj_close=close and flag it.
            for r in parsed:
                if r.trading_date < start_date or r.trading_date > end_date:
                    continue
                qf = {"provider": "stooq", "adj_close": "synthetic_close", "stooq_symbol": stooq_symbol}
                out.append(
                    PriceEODRow(
                        internal_symbol=internal_symbol,
                        trading_date=r.trading_date,
                        open=decimal_or_none(r.open),
                        high=decimal_or_none(r.high),
                        low=decimal_or_none(r.low),
                        close=decimal_or_none(r.close),
                        adj_close=decimal_or_none(r.close),
                        volume=int_or_none(r.volume),
                        currency=None,
                        source="stooq",
                        quality_flags=qf,
                    )
                )

        return out

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
        # Stooq daily CSV endpoint does not provide a corporate actions feed.
        return CorporateActions(dividends=[], splits=[])
