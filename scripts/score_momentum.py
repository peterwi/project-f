#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "config" / "secrets.env"
COMPOSE_FILE = ROOT / "docker" / "compose.yml"

MODEL_VERSION = "momentum_v1"
MISSING_SCORE = -1.0e9


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


def _latest_ops_run_id() -> str:
    run_id = _psql_capture("select run_id from runs where cadence = 'ops' order by started_at desc limit 1;")
    if not run_id:
        raise RuntimeError("No runs found. Run: make run-ops")
    return run_id


def _artifacts_root(env: dict[str, str]) -> Path:
    return Path(env.get("ARTIFACTS_DIR", "/data/trading-ops/artifacts")).resolve()


def _d(raw: str) -> Decimal | None:
    s = (raw or "").strip()
    if not s:
        return None
    return Decimal(s)


@dataclass(frozen=True)
class FeatureRow:
    internal_symbol: str
    px0: Decimal | None
    px5: Decimal | None
    px21: Decimal | None
    px63: Decimal | None
    vol_21: Decimal | None

    def ret(self, px_back: Decimal | None) -> Decimal | None:
        if self.px0 is None or px_back is None:
            return None
        if px_back <= 0:
            return None
        return (self.px0 / px_back) - Decimal(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic momentum scorer (DB-only) -> signals_ranked + artifacts.")
    parser.add_argument("--run-id", help="Target run_id (defaults to latest ops run).")
    parser.add_argument("--asof-date", help="Override asof_date (YYYY-MM-DD). Default: from data_quality_reports for run.")
    args = parser.parse_args()

    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Missing {ENV_FILE}; create it from config/secrets.env.example")
    env = _read_env_file(ENV_FILE)

    run_id = (args.run_id or _latest_ops_run_id()).strip()
    if not run_id:
        raise ValueError("run_id is required")

    asof = (args.asof_date or "").strip()
    if not asof:
        asof = _psql_capture(
            f"""
            select asof_date::text
            from data_quality_reports
            where run_id = '{run_id}'
            order by generated_at desc
            limit 1;
            """
        )
    if not asof:
        asof = _psql_capture(f"select coalesce(asof_date::text,'') from runs where run_id = '{run_id}';")
    if not asof:
        raise RuntimeError("Missing asof_date for run; run data-quality gate first.")
    asof_date = date.fromisoformat(asof)

    run_meta = _psql_capture(
        f"""
        select coalesce(git_commit,'') || '|' || coalesce(config_hash,'')
        from runs where run_id = '{run_id}';
        """
    )
    git_commit, config_hash = (run_meta.split("|", 1) if run_meta else ("", ""))

    source = (os.environ.get("MARKET_PROVIDER", "") or env.get("MARKET_PROVIDER", "") or "stooq").strip().lower()

    enabled_symbols_raw = _psql_capture(
        """
        select coalesce(string_agg(internal_symbol, ',' order by internal_symbol), '')
        from config_universe
        where enabled = true
          and lower(coalesce(instrument_type,'')) = 'stock'
          and tradable_underlying = true;
        """
    )
    symbols = [s for s in enabled_symbols_raw.split(",") if s] if enabled_symbols_raw else []
    if not symbols:
        raise RuntimeError("No enabled tradable symbols; enable at least one stock in config/universe.csv.")

    quoted = ",".join("'" + s.replace("'", "''") + "'" for s in symbols)
    # Trading-day offsets (descending from asof): 1=asof, 6=5d back, 22=21d back, 64=63d back.
    features_raw = _psql_capture(
        f"""
        with p as (
          select
            internal_symbol,
            trading_date,
            adj_close,
            row_number() over (partition by internal_symbol order by trading_date desc) as rn_desc,
            lag(adj_close) over (partition by internal_symbol order by trading_date) as prev_adj
          from market_prices_eod
          where internal_symbol in ({quoted})
            and source = '{source.replace("'", "''")}'
            and trading_date <= '{asof_date.isoformat()}'
            and adj_close is not null
        ),
        agg as (
          select
            internal_symbol,
            max(case when rn_desc = 1 then adj_close end) as px0,
            max(case when rn_desc = 6 then adj_close end) as px5,
            max(case when rn_desc = 22 then adj_close end) as px21,
            max(case when rn_desc = 64 then adj_close end) as px63,
            stddev_pop(ln(adj_close / prev_adj)) filter (
              where rn_desc <= 22 and prev_adj is not null and adj_close > 0 and prev_adj > 0
            ) as vol_21
          from p
          group by internal_symbol
        )
        select
          internal_symbol || '|' ||
          coalesce(px0::text,'') || '|' ||
          coalesce(px5::text,'') || '|' ||
          coalesce(px21::text,'') || '|' ||
          coalesce(px63::text,'') || '|' ||
          coalesce(vol_21::text,'')
        from agg
        order by internal_symbol;
        """
    )

    feat_rows: list[FeatureRow] = []
    for line in (features_raw.splitlines() if features_raw else []):
        sym, px0, px5, px21, px63, vol = line.split("|", 5)
        feat_rows.append(
            FeatureRow(
                internal_symbol=sym,
                px0=_d(px0),
                px5=_d(px5),
                px21=_d(px21),
                px63=_d(px63),
                vol_21=_d(vol),
            )
        )

    by_sym = {r.internal_symbol: r for r in feat_rows}

    scored: list[tuple[str, float, dict[str, str]]] = []
    for sym in symbols:
        fr = by_sym.get(sym)
        if not fr:
            scored.append((sym, MISSING_SCORE, {"reason": "missing_features"}))
            continue
        r5 = fr.ret(fr.px5)
        r21 = fr.ret(fr.px21)
        r63 = fr.ret(fr.px63)
        vol21 = fr.vol_21

        # Convert to floats for stable sorting; if we don't have enough history, score is forced low.
        if r5 is None or r21 is None or r63 is None or vol21 is None:
            scored.append(
                (
                    sym,
                    MISSING_SCORE,
                    {
                        "px0": (str(fr.px0) if fr.px0 is not None else ""),
                        "ret_5d": (str(r5) if r5 is not None else ""),
                        "ret_21d": (str(r21) if r21 is not None else ""),
                        "ret_63d": (str(r63) if r63 is not None else ""),
                        "vol_21": (str(vol21) if vol21 is not None else ""),
                        "reason": "insufficient_history",
                    },
                )
            )
            continue

        r5f = float(r5)
        r21f = float(r21)
        r63f = float(r63)
        v21f = float(vol21)

        score = (0.20 * r5f) + (0.50 * r21f) + (0.30 * r63f) - (0.30 * v21f)
        detail = {
            "px0": (str(fr.px0) if fr.px0 is not None else ""),
            "ret_5d": (str(r5) if r5 is not None else ""),
            "ret_21d": (str(r21) if r21 is not None else ""),
            "ret_63d": (str(r63) if r63 is not None else ""),
            "vol_21": (str(vol21) if vol21 is not None else ""),
        }
        scored.append((sym, score, detail))

    scored.sort(key=lambda t: (-t[1], t[0]))

    artifacts = _artifacts_root(env)
    out_dir = artifacts / "signals" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    signals_path = out_dir / "signals_ranked.csv"
    features_path = out_dir / "features.csv"
    readme_path = out_dir / "README.md"

    with signals_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run_id", "asof_date", "internal_symbol", "score", "rank", "model_version"])
        for i, (sym, score, _) in enumerate(scored, start=1):
            score_s = f"{score:.12g}"
            w.writerow([run_id, asof_date.isoformat(), sym, score_s, i, MODEL_VERSION])

    with features_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["internal_symbol", "px0", "ret_5d", "ret_21d", "ret_63d", "vol_21"])
        for sym, _, detail in scored:
            w.writerow([sym, detail.get("px0", ""), detail.get("ret_5d", ""), detail.get("ret_21d", ""), detail.get("ret_63d", ""), detail.get("vol_21", "")])

    readme_path.write_text(
        "\n".join(
            [
                "# Signals (Momentum v1)",
                "",
                f"- run_id: `{run_id}`",
                f"- asof_date: `{asof_date.isoformat()}`",
                f"- model_version: `{MODEL_VERSION}`",
                f"- market_source: `{source}`",
                f"- git_commit: `{git_commit}`",
                f"- config_hash: `{config_hash}`",
                f"- signals_csv: `{signals_path}`",
                f"- features_csv: `{features_path}`",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Upsert into signals_ranked
    values: list[str] = []
    for i, (sym, score, _) in enumerate(scored, start=1):
        sym_sql = sym.replace("'", "''")
        score_sql = str(Decimal(str(score)))
        values.append(f"('{run_id}','{asof_date.isoformat()}','{sym_sql}',{score_sql},{i},'{MODEL_VERSION}')")

    _psql_exec(
        f"""
        insert into signals_ranked(run_id, asof_date, internal_symbol, score, rank, model_version)
        values {", ".join(values)}
        on conflict (run_id, internal_symbol) do update set
          asof_date = excluded.asof_date,
          score = excluded.score,
          rank = excluded.rank,
          model_version = excluded.model_version;
        """
    )

    count = _psql_capture(f"select count(*) from signals_ranked where run_id = '{run_id}';") or "0"
    print(f"Wrote {signals_path}")
    print(f"Wrote {features_path}")
    print(f"Wrote {readme_path}")
    print(f"run_id={run_id}")
    print(f"asof_date={asof_date.isoformat()}")
    print(f"signals_written={count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        print(f"ERROR: psql failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
