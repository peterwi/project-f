#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = ROOT / "config" / "policy.yml"


class PolicyError(Exception):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise PolicyError(msg)


def _get(d: dict, path: str):
    cur = d
    parts = path.split(".")
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            raise PolicyError(f"Missing policy key: {path}")
        cur = cur[p]
    return cur


def _require_between_0_1(value, path: str) -> float:
    _require(isinstance(value, (int, float)), f"{path} must be a number")
    f = float(value)
    _require(0.0 <= f <= 1.0, f"{path} must be between 0 and 1")
    return f


def main() -> int:
    if not POLICY_FILE.exists():
        raise PolicyError(f"Missing {POLICY_FILE}")

    policy = yaml.safe_load(POLICY_FILE.read_text(encoding="utf-8"))
    _require(isinstance(policy, dict), "policy.yml must be a mapping")

    # Account
    base_ccy = _get(policy, "account.base_currency")
    _require(base_ccy == "GBP", "account.base_currency must be GBP (confirmed eToro base currency)")

    # Execution
    _require(_get(policy, "execution.rebalance_cadence") == "weekly", "execution.rebalance_cadence must be weekly for v1")
    _require(_get(policy, "execution.rebalance_day") == "Monday", "execution.rebalance_day must be Monday for v1")
    window = _get(policy, "execution.execution_window_uk")
    _require(isinstance(window, dict), "execution.execution_window_uk must be a mapping")
    _require("start" in window and "end" in window, "execution.execution_window_uk must include start/end")

    # Locked constraints (docs/ETORO_CONSTRAINTS.md)
    _require(_get(policy, "constraints.long_only") is True, "constraints.long_only must be true")
    _require(_get(policy, "constraints.allow_short") is False, "constraints.allow_short must be false")
    _require(_get(policy, "constraints.allow_leverage") is False, "constraints.allow_leverage must be false")
    _require(float(_get(policy, "constraints.leverage_max")) == 1.0, "constraints.leverage_max must be 1.0")
    _require(_get(policy, "constraints.allow_cfds") is False, "constraints.allow_cfds must be false")

    tradable_types = _get(policy, "constraints.tradable_instrument_types")
    _require(isinstance(tradable_types, list) and tradable_types, "constraints.tradable_instrument_types must be a non-empty list")
    _require([t.lower() for t in tradable_types] == ["stock"], "Only 'stock' is tradable; ETFs are benchmark-only")

    # Portfolio bounds
    max_positions = _get(policy, "portfolio.max_positions")
    _require(isinstance(max_positions, int) and 1 <= max_positions <= 50, "portfolio.max_positions must be an int between 1 and 50")
    max_w = _require_between_0_1(_get(policy, "portfolio.max_position_weight"), "portfolio.max_position_weight")
    cash_buf = _require_between_0_1(_get(policy, "portfolio.min_cash_buffer"), "portfolio.min_cash_buffer")
    turnover = _require_between_0_1(_get(policy, "portfolio.max_turnover_per_rebalance"), "portfolio.max_turnover_per_rebalance")
    _require(max_w <= 0.10, "portfolio.max_position_weight must be <= 0.10 for v1")
    _require(cash_buf >= 0.0 and cash_buf <= 0.10, "portfolio.min_cash_buffer must be between 0 and 0.10")
    _require(turnover <= 0.50, "portfolio.max_turnover_per_rebalance must be <= 0.50 for v1")

    # Kill switch
    ks_enabled = _get(policy, "risk.kill_switch.enabled")
    _require(ks_enabled is True, "risk.kill_switch.enabled must be true")
    _require_between_0_1(_get(policy, "risk.kill_switch.max_drawdown"), "risk.kill_switch.max_drawdown")

    # Benchmarks (must not be tradable)
    _require(_get(policy, "benchmarks.tradable") is False, "benchmarks.tradable must be false")
    bench_syms = _get(policy, "benchmarks.symbols")
    _require(isinstance(bench_syms, list) and bench_syms, "benchmarks.symbols must be a non-empty list")

    # Reconcile required before trading stages
    _require(_get(policy, "reconcile.required") is True, "reconcile.required must be true")

    print("POLICY_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PolicyError as e:
        print(f"POLICY_INVALID: {e}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as e:
        print(f"POLICY_ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)

