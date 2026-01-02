#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "config" / "secrets.env"
COMPOSE_FILE = ROOT / "docker" / "compose.yml"

DEFAULT_BASE_CURRENCY = "GBP"
DEFAULT_ORDER_TYPE = "MKT"
DEFAULT_MAX_SLIPPAGE_BPS = 50
DEFAULT_MIN_NOTIONAL_BASE = 25.0
DEFAULT_MIN_NOTIONAL_PCT = 0.01


def _read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid env line (no '='): {raw_line}")
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _docker_compose_base() -> list[str]:
    return ["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(ENV_FILE)]


def _psql_capture(sql: str) -> str:
    env = _read_env_file(ENV_FILE)
    user = env.get("POSTGRES_USER", "").strip()
    db = env.get("POSTGRES_DB", "").strip()
    if not user or not db:
        raise ValueError("POSTGRES_USER and POSTGRES_DB must be set in config/secrets.env")
    cmd = _docker_compose_base() + [
        "exec",
        "-T",
        "postgres",
        "psql",
        "-q",
        "-U",
        user,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
        "-tA",
        "-c",
        sql,
    ]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _psql_exec(sql: str) -> None:
    env = _read_env_file(ENV_FILE)
    user = env.get("POSTGRES_USER", "").strip()
    db = env.get("POSTGRES_DB", "").strip()
    if not user or not db:
        raise ValueError("POSTGRES_USER and POSTGRES_DB must be set in config/secrets.env")
    cmd = _docker_compose_base() + [
        "exec",
        "-T",
        "postgres",
        "psql",
        "-q",
        "-U",
        user,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
    ]
    subprocess.run(cmd, input=sql.encode("utf-8"), check=True)


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "-c", "safe.directory=*", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _config_hash() -> str:
    try:
        b = ENV_FILE.read_bytes() if ENV_FILE.exists() else b""
        return hashlib.sha256(b).hexdigest()
    except Exception:
        return "UNKNOWN"


def _artifacts_root(env: dict[str, str]) -> Path:
    return Path(env.get("ARTIFACTS_DIR", "/data/trading-ops/artifacts")).resolve()


def _floor_units(notional: float, price: float) -> int:
    if price <= 0:
        return 0
    return int(math.floor(max(0.0, notional) / price))


@dataclass(frozen=True)
class TradeIntent:
    internal_symbol: str
    side: str
    units: int
    notional_value_base: float
    reference_price: float


@dataclass(frozen=True)
class TradeBuilderResult:
    ok: bool
    reason: str
    intended_count: int
    trades_path: str
    detail: dict


def build_trades_for_run(*, run_id: str, asof_date: str, policy: dict) -> TradeBuilderResult:
    env = _read_env_file(ENV_FILE)
    artifacts = _artifacts_root(env)
    run_dir = artifacts / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    dryrun_trades = os.environ.get("DRYRUN_TRADES", "").strip().lower() in ("1", "true", "t", "yes")
    base_currency = DEFAULT_BASE_CURRENCY
    min_notional_abs = float(policy.get("trade_builder", {}).get("min_notional_base", DEFAULT_MIN_NOTIONAL_BASE))
    min_notional_pct = float(policy.get("trade_builder", {}).get("min_notional_pct", DEFAULT_MIN_NOTIONAL_PCT))
    max_slippage_bps = int(policy.get("trade_builder", {}).get("max_slippage_bps", DEFAULT_MAX_SLIPPAGE_BPS))
    order_type = str(policy.get("trade_builder", {}).get("order_type", DEFAULT_ORDER_TYPE))

    result: dict = {
        "schema_version": "v1",
        "run_id": run_id,
        "asof_date_used": asof_date,
        "base_currency": base_currency,
        "policy": {
            "min_notional_base": min_notional_abs,
            "default_order_type": order_type,
            "default_max_slippage_bps": max_slippage_bps,
        },
        "prerequisites": {
            "reconciliation_passed": False,
            "targets_present": False,
            "prices_present": False,
            "missing_prices": [],
        },
        "result": {"trade_builder_ok": False, "reason": ""},
        "intended_trades": [],
        "determinism": {"git_commit": _git_commit(), "config_hash": _config_hash(), "generated_at_utc": datetime.now(timezone.utc).isoformat()},
    }

    out_path = run_dir / "trades_intended.json"
    detail: dict = {
        "dryrun_trades": dryrun_trades,
        "min_notional_base_abs": min_notional_abs,
        "min_notional_pct": min_notional_pct,
        "max_slippage_bps": max_slippage_bps,
    }

    if not dryrun_trades:
        result["result"] = {"trade_builder_ok": False, "reason": "DRYRUN_TRADES_DISABLED"}
        out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return TradeBuilderResult(False, "DRYRUN_TRADES_DISABLED", 0, str(out_path), detail)

    targets_raw = _psql_capture(
        f"""
        select internal_symbol || '|' || target_weight::text || '|' || coalesce(target_value_base::text,'') || '|' || asof_date::text
        from portfolio_targets
        where run_id = '{run_id}'
        order by internal_symbol;
        """
    )
    targets: list[tuple[str, float, str, str]] = []
    for line in (targets_raw.splitlines() if targets_raw else []):
        sym, w_s, tv_s, asof_s = line.split("|", 3)
        targets.append((sym, float(w_s), tv_s, asof_s))

    if not targets:
        result["prerequisites"]["targets_present"] = False
        result["result"] = {"trade_builder_ok": False, "reason": "TARGETS_MISSING"}
        out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return TradeBuilderResult(False, "TARGETS_MISSING", 0, str(out_path), detail)

    result["prerequisites"]["targets_present"] = True
    if any(t_asof != asof_date for _, _, _, t_asof in targets):
        result["result"] = {"trade_builder_ok": False, "reason": "TARGETS_ASOF_MISMATCH"}
        out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return TradeBuilderResult(False, "TARGETS_ASOF_MISMATCH", 0, str(out_path), detail)

    last_rec = _psql_capture(
        "select coalesce(passed::text,'') || '|' || coalesce(snapshot_id::text,'') from reconciliation_results order by evaluated_at desc limit 1;"
    )
    cash_base = 0.0
    positions: dict[str, float] = {}
    position_source = "ledger_views"
    if last_rec:
        passed_s, snapshot_id = last_rec.split("|", 1)
        if passed_s == "t" and snapshot_id:
            result["prerequisites"]["reconciliation_passed"] = True
            position_source = "reconciliation_snapshot"
            snap_row = _psql_capture(
                f"select coalesce(cash_base::text,'0') || '|' || coalesce(snapshot_date::text,'') from reconciliation_snapshots where snapshot_id = '{snapshot_id}';"
            )
            if snap_row:
                cash_s, snapshot_date = snap_row.split("|", 1)
                cash_base = float(cash_s or "0")
                detail["snapshot_id"] = snapshot_id
                detail["snapshot_date"] = snapshot_date
            pos_raw = _psql_capture(
                f"""
                select internal_symbol || '|' || units::text
                from reconciliation_snapshot_positions
                where snapshot_id = '{snapshot_id}'
                order by internal_symbol;
                """
            )
            for line in (pos_raw.splitlines() if pos_raw else []):
                sym, units_s = line.split("|", 1)
                positions[sym] = float(units_s)
    if position_source == "ledger_views":
        cash_s = _psql_capture("select coalesce(cash_base::text,'0') from ledger_cash_current;") or "0"
        cash_base = float(cash_s or "0")
        pos_raw = _psql_capture("select internal_symbol || '|' || units::text from ledger_positions_current order by internal_symbol;")
        for line in (pos_raw.splitlines() if pos_raw else []):
            sym, units_s = line.split("|", 1)
            positions[sym] = float(units_s)

    detail["position_source"] = position_source
    detail["cash_base"] = cash_base

    target_syms = [sym for (sym, _, _, _) in targets]
    price_syms = sorted(set(target_syms) | set(positions.keys()))
    if not price_syms:
        result["result"] = {"trade_builder_ok": False, "reason": "NO_SYMBOLS"}
        out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return TradeBuilderResult(False, "NO_SYMBOLS", 0, str(out_path), detail)

    in_list = ",".join("'" + s.replace("'", "''") + "'" for s in price_syms)
    prices_raw = _psql_capture(
        f"""
        select internal_symbol || '|' || close::text || '|' || coalesce(source,'')
        from market_prices_eod
        where trading_date = '{asof_date}'
          and internal_symbol in ({in_list})
        order by internal_symbol;
        """
    )
    prices: dict[str, tuple[float, str]] = {}
    for line in (prices_raw.splitlines() if prices_raw else []):
        sym, close_s, source_s = line.split("|", 2)
        try:
            prices[sym] = (float(close_s), source_s)
        except Exception:
            continue
    missing = [s for s in price_syms if s not in prices]
    if missing:
        result["prerequisites"]["prices_present"] = False
        result["prerequisites"]["missing_prices"] = missing
        result["result"] = {"trade_builder_ok": False, "reason": "PRICES_MISSING"}
        out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return TradeBuilderResult(False, "PRICES_MISSING", 0, str(out_path), detail)

    result["prerequisites"]["prices_present"] = True

    portfolio_value = cash_base
    for sym, units in positions.items():
        px, _src = prices.get(sym, (0.0, ""))
        portfolio_value += units * px
    detail["portfolio_value_base"] = portfolio_value

    effective_min_notional = min_notional_abs
    if portfolio_value > 0 and min_notional_pct > 0:
        effective_min_notional = min(min_notional_abs, float(portfolio_value) * float(min_notional_pct))
    detail["effective_min_notional_base"] = effective_min_notional
    result["policy"]["min_notional_base"] = effective_min_notional

    current_values: dict[str, float] = {}
    for sym in price_syms:
        units = positions.get(sym, 0.0)
        px, _src = prices[sym]
        current_values[sym] = units * px

    target_values: dict[str, float] = {}
    for sym, w, tv_s, _t_asof in targets:
        if tv_s:
            target_values[sym] = float(tv_s)
        else:
            target_values[sym] = float(w) * portfolio_value

    sells: list[TradeIntent] = []
    buys: list[TradeIntent] = []
    cash_after_sells = cash_base

    for sym in sorted(set(target_syms) | set(positions.keys())):
        px, _src = prices[sym]
        cur_val = current_values.get(sym, 0.0)
        tgt_val = target_values.get(sym, 0.0)
        delta = tgt_val - cur_val
        if delta < 0:
            cur_units = positions.get(sym, 0.0)
            sellable_units = int(math.floor(max(0.0, cur_units)))
            units = min(sellable_units, _floor_units(abs(delta), px))
            notional = float(units) * px
            if units > 0 and notional >= effective_min_notional:
                sells.append(TradeIntent(sym, "SELL", units, notional, px))
                cash_after_sells += notional

    cash_available = cash_after_sells
    for sym in sorted(set(target_syms) | set(positions.keys())):
        px, _src = prices[sym]
        cur_val = current_values.get(sym, 0.0)
        tgt_val = target_values.get(sym, 0.0)
        delta = tgt_val - cur_val
        if delta > 0:
            notional_wanted = min(delta, cash_available)
            units = _floor_units(notional_wanted, px)
            notional = (float(units) * px) if units > 0 else float(max(0.0, notional_wanted))
            if notional >= effective_min_notional:
                buys.append(TradeIntent(sym, "BUY", units, notional, px))
                cash_available -= notional

    intents = sorted(sells, key=lambda t: t.internal_symbol) + sorted(buys, key=lambda t: t.internal_symbol)
    intended_trades: list[dict] = []
    for i, t in enumerate(intents, start=1):
        row: dict = {
            "sequence": i,
            "internal_symbol": t.internal_symbol,
            "side": t.side,
            "notional_value_base": round(t.notional_value_base, 8),
            "order_type": order_type,
            "limit_price": None,
            "reference_price": round(t.reference_price, 8),
            "max_slippage_bps": max_slippage_bps,
            "rationale": "Deterministic rebalance vs target weights.",
        }
        if t.units > 0:
            row["units"] = t.units
        intended_trades.append(row)

    result["intended_trades"] = intended_trades
    result["result"] = {"trade_builder_ok": True, "reason": ("OK" if intended_trades else "NO_REBALANCE")}
    out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    _psql_exec(f"delete from ledger_trades_intended where run_id = '{run_id}';")
    for row in intended_trades:
        sym = row["internal_symbol"].replace("'", "''")
        side = row["side"]
        seq = int(row["sequence"])
        units = int(row["units"]) if ("units" in row and row["units"] is not None) else None
        notional = float(row["notional_value_base"])
        ref_px = float(row["reference_price"])
        units_sql = "null" if units is None else str(units)
        _psql_exec(
            f"""
            insert into ledger_trades_intended(
              run_id, sequence, internal_symbol, side,
              notional_value_base, units,
              order_type, limit_price, reference_price, max_slippage_bps
            )
            values (
              '{run_id}', {seq}, '{sym}', '{side}',
              {notional}, {units_sql},
              '{order_type}', null, {ref_px}, {max_slippage_bps}
            )
            on conflict (run_id, sequence) do update set
              internal_symbol = excluded.internal_symbol,
              side = excluded.side,
              notional_value_base = excluded.notional_value_base,
              units = excluded.units,
              order_type = excluded.order_type,
              limit_price = excluded.limit_price,
              reference_price = excluded.reference_price,
              max_slippage_bps = excluded.max_slippage_bps;
            """
        )

    return TradeBuilderResult(True, result["result"]["reason"], len(intended_trades), str(out_path), detail)


def _latest_run_id() -> str:
    run_id = _psql_capture("select run_id from runs order by created_at desc limit 1;")
    if not run_id:
        raise RuntimeError("No runs found.")
    return run_id


def _asof_for_run(run_id: str) -> str:
    asof = _psql_capture(
        f"""
        select coalesce(asof_date::text,'')
        from data_quality_reports
        where run_id = '{run_id}'
        order by generated_at desc
        limit 1;
        """
    )
    if not asof:
        asof = _psql_capture(f"select coalesce(asof_date::text,'') from runs where run_id = '{run_id}';")
    return asof


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic trade builder (intended trades only; no execution).")
    parser.add_argument("--run-id", help="Target runs.run_id (uuid). Defaults to latest run.")
    parser.add_argument("--asof-date", help="Override as-of date (YYYY-MM-DD). Defaults to data_quality_reports.asof_date.")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")

    run_id = (args.run_id or "").strip() or _latest_run_id()
    asof = (args.asof_date or "").strip() or _asof_for_run(run_id)
    if not asof:
        raise RuntimeError("Missing as-of date (no data_quality_reports row and runs.asof_date is null).")

    policy_path = ROOT / "config" / "policy.yml"
    policy = {}
    if policy_path.exists():
        import yaml  # local import to avoid importing yaml unless needed

        policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}

    res = build_trades_for_run(run_id=run_id, asof_date=asof, policy=policy if isinstance(policy, dict) else {})
    print(f"Wrote {res.trades_path}")
    print(f"trade_builder_ok={str(res.ok).lower()} intended_count={res.intended_count} reason={res.reason}")
    return 0 if res.ok else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        print(f"ERROR: psql failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
