# NBA Draft Combine Dashboard — Build Spec

A leaderboard-style dashboard for 26 years of NBA Draft Combine data (2000–2025), modeled on `jsierrahoopshype/salary-season-finder`. Cohort-relative percentile ratings turn the raw measurables into something insightful instead of a data dump.

---

## Goal in one sentence

Take the cleaned Draft Combine CSV (1,795 player-seasons, 94 columns) and produce a static single-page dashboard where every cell is colored by how good that measurement is **for that player's position**, with rolled-up Anthro / Athletic / Shooting / Overall scores per player.

## Architecture (mirror salary-season-finder)

```
nba-draft-combine/
├── index.html              # Single-file frontend (embedded CSS+JS)
├── build_data.py           # Python pipeline: CSV → data/combine.json
├── data_sources/
│   └── Draft_Combine_cleaned.csv
├── data/
│   └── combine.json        # Output, loaded by index.html
├── requirements.txt
├── .github/workflows/
│   └── deploy.yml          # Optional GitHub Pages auto-deploy
└── README.md
```

**Frontend**: vanilla JS, no build step. Embedded CSS + JS in `index.html`. Loads `data/combine.json` once at startup.

**Backend**: `build_data.py` reads the cleaned CSV, computes cohort percentiles, writes `data/combine.json`. Run manually when the underlying CSV changes (the combine only happens once a year).

## Visual design — match salary-season-finder

CSS variables (copy these from `salary-season-finder/index.html`):
```css
--bg: #f5f5f7;
--surface: #ffffff;
--surface-hover: #f0f0f2;
--border: #d1d1d6;
--text: #1d1d1f;
--text-secondary: #6e6e73;
--accent: #3b82f6;
--accent-dim: rgba(59, 130, 246, 0.15);
--green: #34c759;
--green-dim: rgba(52, 199, 89, 0.15);
--orange: #f59e0b;
--orange-dim: rgba(245, 158, 11, 0.15);
--red: #ef4444;
--red-dim: rgba(239, 68, 68, 0.15);
```

Fonts: `DM Sans` (UI) + `JetBrains Mono` (numeric values). Same Google Fonts import as the salary tool.

Card-based layout, 12px border radius, 1px borders, 0.15s transitions, no shadows beyond the subtle `0 2px 8px rgba(0,0,0,0.04)` on score cards. Mobile breakpoint at `768px`.

## Position bucketing (this is the key analytical decision)

Use **lead-position rule** to bucket every player into one of three groups:

| Bucket | Lead positions | Approx cohort size (all-time) |
|--------|---------------|-------------------------------|
| **Guards** | PG, SG, PG-SG, SG-PG, SG-SF | ~700 |
| **Wings** | SF, SF-SG, SF-PF | ~350 |
| **Bigs** | PF, PF-C, PF-SF, C, C-PF | ~675 |

Rule: split on `-`, take the first token. If first token is `PG` or `SG` → Guards. If `SF` → Wings. If `PF` or `C` → Bigs. Players with missing position fall to Wings as default (only ~5 rows).

## Pipeline: `build_data.py`

```python
import pandas as pd
import numpy as np
import json

CSV = "data_sources/Draft_Combine_cleaned.csv"
OUT = "data/combine.json"

# Metrics that need percentiling, with direction.
# higher_is_better=True means bigger raw value = better player
METRICS = {
    # Anthro
    "Body Fat %": {"category": "anthro", "higher_is_better": False},
    "Hand Length (in)": {"category": "anthro", "higher_is_better": True},
    "Hand Width (in)": {"category": "anthro", "higher_is_better": True},
    "Height without shoes (in)": {"category": "anthro", "higher_is_better": True},
    "Standing reach (in)": {"category": "anthro", "higher_is_better": True},
    "Weight (lbs)": {"category": "anthro", "higher_is_better": True},  # neutral really
    "Wingspan (in)": {"category": "anthro", "higher_is_better": True},
    # Athletic
    "Bench Press (reps)": {"category": "athletic", "higher_is_better": True},
    "Lane Agility (sec)": {"category": "athletic", "higher_is_better": False},
    "Max Vertical Leap (in)": {"category": "athletic", "higher_is_better": True},
    "Modified Lane Agility (sec)": {"category": "athletic", "higher_is_better": False},
    "Standing Vertical Leap (in)": {"category": "athletic", "higher_is_better": True},
    "3/4 Court Sprint (sec)": {"category": "athletic", "higher_is_better": False},
}
# Shooting metrics handled separately — aggregated as one weighted-by-attempts %

POSITION_BUCKETS = {
    "PG": "Guards", "SG": "Guards",
    "SF": "Wings",
    "PF": "Bigs", "C": "Bigs",
}

def bucket(position):
    if not isinstance(position, str) or not position:
        return "Wings"
    lead = position.split("-")[0]
    return POSITION_BUCKETS.get(lead, "Wings")

def percentile_within(series, higher_is_better):
    # Returns 0-100 percentile rank, NaN-safe
    ranks = series.rank(pct=True, na_option="keep") * 100
    if not higher_is_better:
        ranks = 100 - ranks
    return ranks

def main():
    df = pd.read_csv(CSV)
    df["bucket"] = df["Position"].apply(bucket)

    # Per-metric percentile within position bucket (all-time)
    pct_cols = {}
    for metric, meta in METRICS.items():
        pct_col = f"_pct__{metric}"
        df[pct_col] = (
            df.groupby("bucket")[metric]
              .transform(lambda s: percentile_within(s, meta["higher_is_better"]))
        )
        pct_cols[metric] = pct_col

    # Shooting score: aggregate weighted overall make-rate, then percentile within bucket
    shot_att_cols = [c for c in df.columns if c.endswith(" Att")]
    shot_made_cols = [c.replace(" Att", " Made") for c in shot_att_cols]
    df["_shooting_attempts"] = df[shot_att_cols].sum(axis=1, min_count=1)
    df["_shooting_made"] = df[shot_made_cols].sum(axis=1, min_count=1)
    df["_shooting_pct"] = df["_shooting_made"] / df["_shooting_attempts"]
    df["_pct__shooting"] = df.groupby("bucket")["_shooting_pct"].transform(
        lambda s: percentile_within(s, higher_is_better=True)
    )

    # Aggregate category scores: simple mean of available percentile metrics
    anthro_cols = [pct_cols[m] for m, meta in METRICS.items() if meta["category"] == "anthro"]
    athletic_cols = [pct_cols[m] for m, meta in METRICS.items() if meta["category"] == "athletic"]
    df["score_anthro"] = df[anthro_cols].mean(axis=1, skipna=True)
    df["score_athletic"] = df[athletic_cols].mean(axis=1, skipna=True)
    df["score_shooting"] = df["_pct__shooting"]

    # Overall Combine Rating: weighted blend, NaN-tolerant
    weights = {"anthro": 0.30, "athletic": 0.50, "shooting": 0.20}
    def combine_rating(row):
        parts = []
        for cat, w in weights.items():
            v = row[f"score_{cat}"]
            if pd.notna(v):
                parts.append((v, w))
        if not parts:
            return None
        total_w = sum(w for _, w in parts)
        return sum(v * w for v, w in parts) / total_w
    df["score_overall"] = df.apply(combine_rating, axis=1)

    # Build output: list of player records with raw values + percentiles + scores
    records = []
    for _, row in df.iterrows():
        rec = {
            "season": int(row["Season"]),
            "player": row["Player"],
            "position": row["Position"] if pd.notna(row["Position"]) else None,
            "bucket": row["bucket"],
            "metrics": {},
            "scores": {
                "anthro": _round(row["score_anthro"]),
                "athletic": _round(row["score_athletic"]),
                "shooting": _round(row["score_shooting"]),
                "overall": _round(row["score_overall"]),
            }
        }
        # Headline numeric metrics with both raw and percentile
        for metric in METRICS:
            raw = row[metric]
            pct = row[pct_cols[metric]]
            rec["metrics"][metric] = {
                "raw": _clean(raw),
                "pct": _round(pct),
            }
        # Pre-formatted display strings (height/wingspan/reach in feet-inches)
        for col in ["Height without shoes", "Height with shoes", "Wingspan", "Standing reach"]:
            if col in df.columns and pd.notna(row[col]):
                rec[col] = row[col]
        records.append(rec)

    out = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "rows": len(records),
        "season_range": [int(df["Season"].min()), int(df["Season"].max())],
        "buckets": {b: int((df["bucket"] == b).sum()) for b in df["bucket"].unique()},
        "metrics_meta": {m: {"category": meta["category"], "higher_is_better": meta["higher_is_better"]} for m, meta in METRICS.items()},
        "players": records,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False, default=str)
    print(f"Wrote {OUT}: {len(records)} players")

def _round(v, n=1):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return round(float(v), n)

def _clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return v if isinstance(v, str) else float(v)

if __name__ == "__main__":
    main()
```

`requirements.txt`:
```
pandas>=2.0
numpy>=1.24
```

## Frontend MVP — `index.html`

### Layout

1. **Header**: title "NBA Draft Combine Dashboard", subtitle "1,795 prospects, 2000–2025, scored against their position cohort"
2. **Filter bar** (sticky on desktop): season range, position bucket (All / Guards / Wings / Bigs), search by name
3. **Sort dropdown**: by Combine Rating (default), Anthro, Athletic, Shooting, or any individual metric
4. **Leaderboard table** (desktop) / **card list** (mobile)
5. **Row click → expanded panel** with all metrics including shooting drill grid

### Leaderboard row structure

```
| Rank | Player + Year + Pos | Combine Rating | Anthro | Athletic | Shooting | Wingspan | Vert | Sprint | ... |
```

- The four score columns and every metric cell get a **background-gradient color** based on percentile (see thresholds below)
- Numeric values use `JetBrains Mono`
- Heights/wingspans display as the formatted `7' 2.50''` string when available, raw inches as the `data-sort` attribute

### Color thresholds (apply to `--cell-bg` per percentile)

| Percentile | Color | RGB |
|-----------|-------|-----|
| ≥ 90 | deep green | `rgba(52, 199, 89, 0.35)` |
| 75–89 | light green | `rgba(52, 199, 89, 0.15)` |
| 25–74 | neutral | `transparent` (uses `--surface`) |
| 10–24 | light red | `rgba(239, 68, 68, 0.15)` |
| < 10 | deep red | `rgba(239, 68, 68, 0.35)` |

Cells with missing data: italic `—` in `--text-secondary`, no background.

### Filter behavior

- Position filter swaps the entire dataset to the chosen bucket (percentiles are pre-computed per bucket so no client-side recomputation needed)
- Season range and search are client-side filters on the rendered list
- "All positions" view shows everyone but **percentiles still come from each player's own bucket** (a 7-foot center isn't penalized for not being a fast guard)

### Expanded row panel

Click any row to expand. Shows:
- Full anthro grid (all 7 metrics with raw + percentile)
- Full athletic grid (all 6 metrics with raw + percentile)
- Shooting drill heatmap (3 ranges × 5 spots × 3 stats, percentages with cell coloring)
- Three category score badges + the overall rating prominent

## Mobile

Stack everything. Cards instead of table rows. Each card shows: player name, year, position bucket badge, four score badges in a 2×2 grid, then the top-3 standout metrics by percentile (highest 3). Tap → expanded view.

## Stretch features (don't build in v1)

1. **Doppelgängers**: when expanded, show 5 closest historical combines by Euclidean distance on normalized metric vector. Distance computed in `build_data.py` and pre-stored per player.
2. **Era toggle**: switch percentile cohort to 5-year era window vs all-time.
3. **Custom rating weights**: slider trio (Anthro / Athletic / Shooting) that recomputes the overall on the fly.
4. **Compare two players** side by side, salary-season-finder style.

## Acceptance criteria for v1

1. `python build_data.py` runs cleanly and produces `data/combine.json`
2. Opening `index.html` directly (no server needed) loads and displays the leaderboard
3. Default sort is by Combine Rating descending
4. Cell coloring is visible and matches the percentile thresholds
5. Position filter, season filter, and name search all work
6. Mobile (≤768px) renders without horizontal scroll
7. Tacko Fall (2019) shows in the Bigs cohort with high Anthro Score and middling-or-NaN Athletic Score (sanity check)
8. Keon Johnson (2021) shows the highest Max Vertical Leap percentile in the Guards cohort

## Deploy

GitHub Pages from `main` branch root. No build step needed — `index.html` and `data/combine.json` are the only files served.

## Notes for editorial reuse

Once shipped, this becomes the source for natural HoopsHype articles:
- "The 10 freakiest combine performances of the last 25 years" (sort by Combine Rating)
- "Every 2025 prospect's combine doppelgänger" (stretch feature 1)
- "What does Cooper Flagg's combine actually tell us?" (single-player deep dive using the rating)
