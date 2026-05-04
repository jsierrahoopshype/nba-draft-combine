#!/usr/bin/env python3
"""
Build pipeline for the NBA Draft Combine Dashboard.

Reads the cleaned NBA Draft Combine CSV, computes percentiles within
position cohorts (Guards / Wings / Bigs), aggregates Anthro / Athletic /
Shooting / Overall scores, and writes a static JSON file consumed by
index.html.
"""

import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(BASE_DIR, "data_sources", "Draft_Combine_cleaned.csv")
OUT = os.path.join(BASE_DIR, "data", "combine.json")

METRICS = {
    # Anthro
    "Body Fat %":                 {"category": "anthro",   "higher_is_better": False},
    "Hand Length (in)":           {"category": "anthro",   "higher_is_better": True},
    "Hand Width (in)":            {"category": "anthro",   "higher_is_better": True},
    "Height without shoes (in)":  {"category": "anthro",   "higher_is_better": True},
    "Standing reach (in)":        {"category": "anthro",   "higher_is_better": True},
    "Weight (lbs)":               {"category": "anthro",   "higher_is_better": True},
    "Wingspan (in)":              {"category": "anthro",   "higher_is_better": True},
    # Athletic
    "Bench Press (reps)":         {"category": "athletic", "higher_is_better": True},
    "Lane Agility (sec)":         {"category": "athletic", "higher_is_better": False},
    "Max Vertical Leap (in)":     {"category": "athletic", "higher_is_better": True},
    "Modified Lane Agility (sec)":{"category": "athletic", "higher_is_better": False},
    "Standing Vertical Leap (in)":{"category": "athletic", "higher_is_better": True},
    "3/4 Court Sprint (sec)":     {"category": "athletic", "higher_is_better": False},
}

POSITION_BUCKETS = {
    "PG": "Guards", "SG": "Guards",
    "SF": "Wings",
    "PF": "Bigs", "C": "Bigs",
}

CATEGORY_WEIGHTS = {"anthro": 0.30, "athletic": 0.50, "shooting": 0.20}


def bucket(position):
    if not isinstance(position, str) or not position.strip():
        return "Wings"
    lead = position.split("-")[0].strip().upper()
    return POSITION_BUCKETS.get(lead, "Wings")


def percentile_within(series, higher_is_better):
    ranks = series.rank(pct=True, na_option="keep") * 100
    if not higher_is_better:
        ranks = 100 - ranks
    return ranks


def _round(v, n=1):
    if v is None:
        return None
    if isinstance(v, float) and (pd.isna(v) or np.isinf(v)):
        return None
    return round(float(v), n)


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and (pd.isna(v) or np.isinf(v)):
        return None
    if isinstance(v, str):
        return v
    return float(v)


def main():
    df = pd.read_csv(CSV)
    df["bucket"] = df["Position"].apply(bucket)

    # Per-metric percentiles within bucket (all-time)
    pct_cols = {}
    for metric, meta in METRICS.items():
        pct_col = f"_pct__{metric}"
        df[pct_col] = (
            df.groupby("bucket")[metric]
              .transform(lambda s, hib=meta["higher_is_better"]: percentile_within(s, hib))
        )
        pct_cols[metric] = pct_col

    # Shooting: aggregate weighted overall make rate, percentile within bucket
    shot_att_cols = [c for c in df.columns if c.endswith(" Att")]
    shot_made_cols = [c.replace(" Att", " Made") for c in shot_att_cols]
    shot_made_cols = [c for c in shot_made_cols if c in df.columns]
    shot_att_cols = [c.replace(" Made", " Att") for c in shot_made_cols]

    df["_shooting_attempts"] = df[shot_att_cols].sum(axis=1, min_count=1)
    df["_shooting_made"] = df[shot_made_cols].sum(axis=1, min_count=1)
    df["_shooting_pct"] = df["_shooting_made"] / df["_shooting_attempts"]
    # If no attempts at all, leave NaN
    df.loc[df["_shooting_attempts"].fillna(0) == 0, "_shooting_pct"] = np.nan
    df["_pct__shooting"] = df.groupby("bucket")["_shooting_pct"].transform(
        lambda s: percentile_within(s, higher_is_better=True)
    )

    anthro_cols   = [pct_cols[m] for m, meta in METRICS.items() if meta["category"] == "anthro"]
    athletic_cols = [pct_cols[m] for m, meta in METRICS.items() if meta["category"] == "athletic"]
    df["score_anthro"]   = df[anthro_cols].mean(axis=1, skipna=True)
    df["score_athletic"] = df[athletic_cols].mean(axis=1, skipna=True)
    df["score_shooting"] = df["_pct__shooting"]

    def combine_rating(row):
        parts = []
        for cat, w in CATEGORY_WEIGHTS.items():
            v = row[f"score_{cat}"]
            if pd.notna(v):
                parts.append((v, w))
        if not parts:
            return np.nan
        total_w = sum(w for _, w in parts)
        return sum(v * w for v, w in parts) / total_w

    df["score_overall"] = df.apply(combine_rating, axis=1)

    # Pre-compute shooting drill grid metadata (each "spot" = drill_set + spot)
    # Each shooting column is one of:
    #   <Drill> <Spot> Att|Made|%
    # We pass the per-player drill rows through, plus per-spot percentiles within bucket.
    drill_specs = []
    for att_col in shot_att_cols:
        prefix = att_col[:-4]  # strip " Att"
        made_col = f"{prefix} Made"
        pct_col = f"{prefix} %"
        if made_col in df.columns and pct_col in df.columns:
            drill_specs.append({"name": prefix, "att": att_col, "made": made_col, "pct": pct_col})

    # For each drill, compute percentile of make % within bucket (only when player attempted >= 1)
    drill_pct_cols = {}
    for d in drill_specs:
        col = f"_pct__shot__{d['name']}"
        # Only consider rows where attempts > 0
        pct_series = df[d["pct"]].where(df[d["att"]].fillna(0) > 0)
        df[col] = (
            pct_series.groupby(df["bucket"]).transform(
                lambda s: percentile_within(s, higher_is_better=True)
            )
        )
        drill_pct_cols[d["name"]] = col

    # Build records
    records = []
    for _, row in df.iterrows():
        rec = {
            "season": int(row["Season"]),
            "player": row["Player"],
            "position": row["Position"] if isinstance(row["Position"], str) else None,
            "bucket": row["bucket"],
            "metrics": {},
            "scores": {
                "anthro":   _round(row["score_anthro"]),
                "athletic": _round(row["score_athletic"]),
                "shooting": _round(row["score_shooting"]),
                "overall":  _round(row["score_overall"]),
            },
            "shooting": {
                "attempts": _round(row["_shooting_attempts"], 0),
                "made":     _round(row["_shooting_made"], 0),
                "pct":      _round(row["_shooting_pct"] * 100 if pd.notna(row["_shooting_pct"]) else None, 1),
            },
            "drills": {},
            "display": {},
        }
        for metric in METRICS:
            rec["metrics"][metric] = {
                "raw": _clean(row[metric]),
                "pct": _round(row[pct_cols[metric]]),
            }
        for d in drill_specs:
            att = row[d["att"]]
            if pd.notna(att) and att > 0:
                rec["drills"][d["name"]] = {
                    "att":  _round(att, 0),
                    "made": _round(row[d["made"]], 0),
                    "pct":  _round(row[d["pct"]] * 100, 1) if pd.notna(row[d["pct"]]) else None,
                    "rank_pct": _round(row[drill_pct_cols[d["name"]]]),
                }
        for col in ["Height without shoes", "Height with shoes", "Wingspan", "Standing reach"]:
            if col in df.columns and isinstance(row[col], str) and row[col].strip():
                rec["display"][col] = row[col].strip()
        records.append(rec)

    # Sort by overall descending so default render order is meaningful
    records.sort(
        key=lambda r: (r["scores"]["overall"] is None, -(r["scores"]["overall"] or 0)),
    )

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(records),
        "season_range": [int(df["Season"].min()), int(df["Season"].max())],
        "buckets": {b: int((df["bucket"] == b).sum()) for b in sorted(df["bucket"].unique())},
        "metrics_meta": {
            m: {"category": meta["category"], "higher_is_better": meta["higher_is_better"]}
            for m, meta in METRICS.items()
        },
        "category_weights": CATEGORY_WEIGHTS,
        "drills": [d["name"] for d in drill_specs],
        "players": records,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False, default=str)
    print(f"Wrote {OUT}: {len(records)} players, "
          f"{out['season_range'][0]}–{out['season_range'][1]}, "
          f"buckets={out['buckets']}")


if __name__ == "__main__":
    main()
