#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class PredArtifact:
    path: Path


def _find_latest_pred(mlruns_dir: Path) -> PredArtifact:
    candidates = sorted(mlruns_dir.rglob("artifacts/pred.pkl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No pred.pkl found under: {mlruns_dir}")
    return PredArtifact(path=candidates[0])


def _extract_last_date_ranked(pred: pd.DataFrame) -> pd.DataFrame:
    if isinstance(pred.index, pd.MultiIndex) and len(pred.index.levels) >= 2:
        dt_level = pred.index.names.index("datetime") if "datetime" in pred.index.names else 0
        inst_level = pred.index.names.index("instrument") if "instrument" in pred.index.names else 1
        dts = pred.index.get_level_values(dt_level)
        last_dt = pd.Timestamp(dts.max())
        mask = dts == last_dt
        pred_last = pred.loc[mask]
        pred_last = pred_last.reset_index()
        dt_col = pred_last.columns[dt_level]
        inst_col = pred_last.columns[inst_level]
        if "score" in pred_last.columns:
            score_col = "score"
        else:
            # Qlib typically uses a single column for predictions; fallback to first numeric column.
            numeric_cols = [c for c in pred_last.columns if c not in {dt_col, inst_col}]
            if not numeric_cols:
                raise ValueError("Prediction dataframe has no score column.")
            score_col = numeric_cols[0]
        pred_last = pred_last[[dt_col, inst_col, score_col]].rename(
            columns={dt_col: "datetime", inst_col: "internal_symbol", score_col: "score"}
        )
    else:
        raise ValueError("Unexpected prediction format (expected MultiIndex with datetime/instrument).")

    asof = date.fromisoformat(pd.Timestamp(pred_last["datetime"].max()).date().isoformat())
    pred_last["asof_date"] = asof.isoformat()
    pred_last = pred_last.drop(columns=["datetime"])

    pred_last = pred_last.sort_values(["score", "internal_symbol"], ascending=[False, True], kind="mergesort")
    pred_last["rank"] = range(1, len(pred_last) + 1)
    return pred_last[["asof_date", "internal_symbol", "score", "rank"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export ranked signals from Qlib mlruns artifacts (shadow mode).")
    parser.add_argument("--mlruns", required=True, help="Path to mlruns folder (FileStore).")
    parser.add_argument("--out", required=True, help="Output path (.parquet or .csv).")
    args = parser.parse_args()

    mlruns_dir = Path(args.mlruns).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pred_artifact = _find_latest_pred(mlruns_dir)
    pred = pd.read_pickle(pred_artifact.path)
    ranked = _extract_last_date_ranked(pred)

    if out_path.suffix.lower() == ".parquet":
        ranked.to_parquet(out_path, index=False)
    else:
        ranked.to_csv(out_path, index=False)

    print(f"pred_path={pred_artifact.path}")
    print(f"out={out_path}")
    print(f"asof_date={ranked['asof_date'].iloc[0] if len(ranked) else ''}")
    print(f"rows={len(ranked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

