#!/usr/bin/env python3
"""
Build pipeline for the NBA Draft Combine Dashboard.

Reads the cleaned NBA Draft Combine CSV, computes percentiles within
position cohorts (Guards / Wings / Bigs), aggregates Anthro / Athletic /
Shooting / Overall scores, and writes a static JSON file consumed by
index.html.
"""

import csv
import json
import os
import unicodedata
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(BASE_DIR, "data_sources", "Draft_Combine_cleaned.csv")
WORKOUTS_CSV = os.path.join(BASE_DIR, "data_sources", "workouts.csv")
OUT = os.path.join(BASE_DIR, "data", "combine.json")

# Workouts Google Sheet — fixed ID, fetched via service-account auth in CI
# when GOOGLE_SHEETS_KEY env var is populated. Local builds fall back to the
# data_sources/workouts.csv file if present, then to no-data mode.
WORKOUTS_SHEET_ID = "1x6JGbo0A_9Elr6xrvDbXOdD_8kqyVz6n60t7PiF5Qb8"
WORKOUTS_SHEET_RANGE = "Sheet1!A:E"

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
    # Derived ratios — percentile'd within bucket but excluded from anthro/athletic
    # score aggregation (they double-count height/wingspan signal).
    "wingspan_to_height":         {"category": "ratio",    "higher_is_better": True},
    "reach_to_height":            {"category": "ratio",    "higher_is_better": True},
    "hand_area":                  {"category": "ratio",    "higher_is_better": True},
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


# ---------------------------------------------------------------------------
# Profile tag vocabulary — fixed list of 23 tags drawn from anthro / ratio /
# athletic metrics. Each tag is keyed off a single per-bucket percentile
# threshold; the negative-side tags fire on low percentiles, the positive-
# side ones on high percentiles, and a single mid-range tag (average-jumper)
# fires on the middle band.
#
# IMPORTANT — body fat direction
# Body Fat % is configured with higher_is_better=False in METRICS, so the
# stored percentile is already inverted: a player with raw body fat of
# 5.0% lands in a HIGH percentile (lean), and a player at 14.0% lands in
# a LOW percentile (high body fat). The tag rules below therefore use the
# normal pct>=75 / pct<=25 form. Do NOT flip them.
#
# Each tuple: (slug, label, category, metric, rule, threshold)
#   rule = "pct_gte" | "pct_lte" | "pct_range"
#   threshold = number for pct_gte/pct_lte, (low, high) for pct_range
TAG_VOCABULARY = [
    # Anthro (10)
    ("long-arms",                "Long arms",                 "anthro",   "Wingspan (in)",            "pct_gte", 75),
    ("short-arms",               "Short arms",                "anthro",   "Wingspan (in)",            "pct_lte", 25),
    ("big-hands",                "Big hands",                 "anthro",   "hand_area",                "pct_gte", 75),
    ("small-hands",              "Small hands",               "anthro",   "hand_area",                "pct_lte", 25),
    ("tall-frame",               "Tall frame",                "anthro",   "Height without shoes (in)","pct_gte", 75),
    ("short-frame",              "Short frame",               "anthro",   "Height without shoes (in)","pct_lte", 25),
    ("heavy-build",              "Heavy build",               "anthro",   "Weight (lbs)",             "pct_gte", 75),
    ("light-frame",              "Light frame",               "anthro",   "Weight (lbs)",             "pct_lte", 25),
    ("lean-build",               "Lean build",                "anthro",   "Body Fat %",               "pct_gte", 75),
    ("high-body-fat",            "High body fat",             "anthro",   "Body Fat %",               "pct_lte", 25),
    # Ratio (4)
    ("long-reach",               "Long reach",                "ratio",    "reach_to_height",          "pct_gte", 75),
    ("short-reach-for-height",   "Short reach for height",    "ratio",    "reach_to_height",          "pct_lte", 25),
    ("stretchy-wingspan",        "Stretchy wingspan",         "ratio",    "wingspan_to_height",       "pct_gte", 75),
    ("compact-wingspan",         "Compact wingspan",          "ratio",    "wingspan_to_height",       "pct_lte", 25),
    # Athletic (9)
    ("explosive-jumper",         "Explosive jumper",          "athletic", "Max Vertical Leap (in)",   "pct_gte", 75),
    ("limited-bounce",           "Limited bounce",            "athletic", "Max Vertical Leap (in)",   "pct_lte", 25),
    ("average-jumper",           "Average jumper",            "athletic", "Max Vertical Leap (in)",   "pct_range", (40, 60)),
    ("quick-first-step",         "Quick first step",          "athletic", "3/4 Court Sprint (sec)",   "pct_gte", 75),
    ("slower-burst",             "Slower burst",              "athletic", "3/4 Court Sprint (sec)",   "pct_lte", 25),
    ("smooth-mover",             "Smooth mover",              "athletic", "Lane Agility (sec)",       "pct_gte", 75),
    ("limited-lateral-quickness","Limited lateral quickness", "athletic", "Lane Agility (sec)",       "pct_lte", 25),
    ("strong",                   "Strong",                    "athletic", "Bench Press (reps)",       "pct_gte", 75),
    ("below-average-strength",   "Below-average strength",    "athletic", "Bench Press (reps)",       "pct_lte", 25),
]


def assign_tags_for_player(player_pcts):
    """player_pcts is a dict mapping metric name -> percentile (or None).
    Returns the list of tag slugs that fire for this player."""
    out = []
    for slug, _label, _category, metric, rule, threshold in TAG_VOCABULARY:
        pct = player_pcts.get(metric)
        if pct is None or (isinstance(pct, float) and pd.isna(pct)):
            continue
        if rule == "pct_gte" and pct >= threshold:
            out.append(slug)
        elif rule == "pct_lte" and pct <= threshold:
            out.append(slug)
        elif rule == "pct_range":
            lo, hi = threshold
            if lo <= pct <= hi:
                out.append(slug)
    return out


def _self_test_body_fat_direction():
    """Body fat is the most likely place to introduce a sign error. Run a
    synthetic 6-player dataset through the bucket-percentile pipeline and
    assert lean (bf=5.0) → lean-build, fat (bf=14.0) → high-body-fat. Raises
    AssertionError before any real work happens if the direction inverted."""
    fixture = pd.DataFrame([
        {"Player": "A_lean",  "Position": "PG", "Body Fat %":  5.0},
        {"Player": "B_fat",   "Position": "PG", "Body Fat %": 14.0},
        {"Player": "C_mid1",  "Position": "PG", "Body Fat %":  8.0},
        {"Player": "D_mid2",  "Position": "PG", "Body Fat %":  9.0},
        {"Player": "E_mid3",  "Position": "PG", "Body Fat %": 10.0},
        {"Player": "F_mid4",  "Position": "PG", "Body Fat %": 11.0},
    ])
    fixture["bucket"] = fixture["Position"].apply(bucket)
    pcts = fixture.groupby("bucket")["Body Fat %"].transform(
        lambda s: percentile_within(s, higher_is_better=False)
    )
    fixture["bf_pct"] = pcts

    # Verify direction of stored percentile
    a_pct = fixture.loc[fixture["Player"] == "A_lean",  "bf_pct"].iloc[0]
    b_pct = fixture.loc[fixture["Player"] == "B_fat",   "bf_pct"].iloc[0]
    assert a_pct > b_pct, f"Lean should have higher pct than fat (got {a_pct} vs {b_pct})"
    assert a_pct >= 75,   f"Player at bf=5.0 should be in top quartile of leanness (got pct={a_pct})"
    assert b_pct <= 25,   f"Player at bf=14.0 should be in bottom quartile of leanness (got pct={b_pct})"

    # Verify tag assignment
    a_tags = assign_tags_for_player({"Body Fat %": float(a_pct)})
    b_tags = assign_tags_for_player({"Body Fat %": float(b_pct)})
    assert "lean-build"    in a_tags, f"A_lean should have lean-build, got {a_tags}"
    assert "high-body-fat" not in a_tags, f"A_lean should NOT have high-body-fat, got {a_tags}"
    assert "high-body-fat" in b_tags, f"B_fat should have high-body-fat, got {b_tags}"
    assert "lean-build"    not in b_tags, f"B_fat should NOT have lean-build, got {b_tags}"


# ---- Workouts ingestion ----------------------------------------------------
# Three modes, in priority order:
#   1) GOOGLE_SHEETS_KEY env var present → fetch from Sheets API (CI).
#   2) data_sources/workouts.csv present → read from disk (local).
#   3) Neither → log warning, build without workouts.

# The workouts sheet uses full team names in column C (TEAM, e.g. "Indiana",
# "Oklahoma City") but abbreviations in columns D and E (DRAFT_TEAM and
# REAL_TEAM, e.g. "IND", "OKC"). We normalize the abbreviations to the full
# name on the way into combine.json so the frontend can do a straight
# string comparison against player.workouts.
TEAM_ABBR_TO_FULL = {
    "ATL": "Atlanta",
    "BOS": "Boston",
    "BKN": "Brooklyn",
    "CHA": "Charlotte",
    "CHI": "Chicago",
    "CLE": "Cleveland",
    "DAL": "Dallas",
    "DEN": "Denver",
    "DET": "Detroit",
    "GSW": "Golden State",
    "HOU": "Houston",
    "IND": "Indiana",
    "LAC": "LA Clippers",
    "LAL": "LA Lakers",
    "MEM": "Memphis",
    "MIA": "Miami",
    "MIL": "Milwaukee",
    "MIN": "Minnesota",
    "NOP": "New Orleans",
    "NYK": "New York",
    "OKC": "Oklahoma City",
    "ORL": "Orlando",
    "PHI": "Philadelphia",
    "PHX": "Phoenix",
    "POR": "Portland",
    "SAC": "Sacramento",
    "SAS": "San Antonio",
    "TOR": "Toronto",
    "UTA": "Utah",
    "WAS": "Washington",
}


def normalize_team(t):
    """Map a sheet-format team string ('IND', 'iND ', '') to the full team
    name used in the workouts list ('Indiana'). Unknown values pass through
    so editorial drift surfaces as a 'Did not work out' pill rather than a
    silent drop."""
    if t is None:
        return None
    s = str(t).strip()
    if not s:
        return None
    return TEAM_ABBR_TO_FULL.get(s.upper(), s)


def _norm_name(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def fetch_workouts_rows():
    """Return list of (player, year, team, draft_team, real_team) rows, or [] if
    no data source is available. Each cell is a string (possibly empty)."""
    key_json = os.environ.get("GOOGLE_SHEETS_KEY")
    if key_json:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds_info = json.loads(key_json)
            creds = service_account.Credentials.from_service_account_info(
                creds_info,
                scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
            )
            svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
            resp = svc.spreadsheets().values().get(
                spreadsheetId=WORKOUTS_SHEET_ID,
                range=WORKOUTS_SHEET_RANGE,
            ).execute()
            values = resp.get("values", [])
            if not values:
                print("Workouts: Sheets API returned no rows")
                return []
            # First row is header; the rest are data. Pad short rows to 5 cols.
            data_rows = values[1:]
            rows = []
            for r in data_rows:
                cells = list(r) + [""] * (5 - len(r))
                rows.append(tuple(c.strip() for c in cells[:5]))
            print(f"Workouts: fetched {len(rows)} rows from Google Sheets API")
            return rows
        except Exception as e:
            print(f"Workouts: Sheets API fetch failed ({e}); falling back to CSV/no-data")

    if os.path.exists(WORKOUTS_CSV):
        rows = []
        with open(WORKOUTS_CSV, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for r in reader:
                cells = list(r) + [""] * (5 - len(r))
                rows.append(tuple(c.strip() for c in cells[:5]))
        print(f"Workouts: read {len(rows)} rows from {WORKOUTS_CSV}")
        return rows

    print("No workouts data available (no GOOGLE_SHEETS_KEY env var and no "
          "data_sources/workouts.csv). Building without workouts.")
    return []


def merge_workouts(records, workouts_rows):
    """Mutates records in place: attaches workouts/draft_team/real_team to each
    matching player. Returns (matched_count, unmatched_count)."""
    # Initialize fields to None for every record so consumers can rely on the
    # keys being present.
    for rec in records:
        rec["workouts"] = None
        rec["draft_team"] = None
        rec["real_team"] = None

    if not workouts_rows:
        return (0, 0)

    # Group sheet rows by (normalized_name, year). Preserve order of teams.
    groups = OrderedDict()
    for player, year_str, team, draft_team, real_team in workouts_rows:
        if not player or not year_str:
            continue
        try:
            year = int(year_str)
        except ValueError:
            continue
        key = (_norm_name(player), year)
        g = groups.setdefault(key, {
            "display_name": player.strip(),
            "year": year,
            "teams": [],
            "draft_team": None,
            "real_team": None,
        })
        if team:
            g["teams"].append(team)
        if draft_team and not g["draft_team"]:
            g["draft_team"] = draft_team
        if real_team and not g["real_team"]:
            g["real_team"] = real_team

    # Build a name index of combine records. Detect collisions.
    index = defaultdict(list)
    for rec in records:
        index[(_norm_name(rec["player"]), int(rec["season"]))].append(rec)

    matched = 0
    unmatched = 0
    collisions = 0
    for key, group in groups.items():
        candidates = index.get(key, [])
        if not candidates:
            unmatched += 1
            print(f"  workouts row [{group['display_name']}] {group['year']} has no combine match — skipped")
            continue
        if len(candidates) > 1:
            collisions += 1
            print(f"  workouts row [{group['display_name']}] {group['year']} matches {len(candidates)} combine players — skipped")
            continue
        rec = candidates[0]
        rec["workouts"] = group["teams"] if group["teams"] else None
        # Normalize abbreviations from columns D/E into full team names that
        # match column C, so the frontend's exact-string comparison against
        # the workouts list works for the common case.
        raw_draft = group["draft_team"]
        raw_real  = group["real_team"]
        rec["draft_team"] = normalize_team(raw_draft)
        rec["real_team"]  = normalize_team(raw_real)
        # Warn if draft_team / real_team still don't appear in the workouts
        # list after normalization — that's a real "drafted without a workout"
        # case (or a sheet typo we can't auto-fix). Log the raw abbreviation
        # alongside the normalized name so the source sheet is easy to grep.
        wt_set = set(rec["workouts"] or [])
        for label, raw, norm in (("draft_team", raw_draft, rec["draft_team"]),
                                 ("real_team",  raw_real,  rec["real_team"])):
            if norm and norm not in wt_set:
                raw_repr = repr(raw) if raw and raw != norm else repr(norm)
                norm_note = f" ({norm})" if raw and raw != norm else ""
                print(f"  workouts note: {rec['player']} {rec['season']} "
                      f"{label}={raw_repr}{norm_note} not in workouts list "
                      f"— rendered as \"Did not work out\"")
        matched += 1
    if collisions:
        print(f"  workouts collisions: {collisions} groups skipped due to multiple matches")
    return (matched, unmatched)


def main():
    # Self-test BEFORE any real work — body fat direction is the easiest
    # place to introduce a sign flip; fail loud and early if the rule is
    # wrong.
    _self_test_body_fat_direction()

    df = pd.read_csv(CSV)

    # Fetch workouts up front so we can decide who to keep. Top picks
    # (Wembanyama 2023, Banchero/Holmgren 2022, Cunningham 2021, Morant/
    # Zion 2019, etc.) often skip combine measurements but still do team
    # workouts — they have editorial value via workouts/draft/real even
    # with no measured metrics. Players with workouts data are exempt
    # from the zero-metric drop below.
    workouts_rows = fetch_workouts_rows()
    workouts_keys = set()
    for player, year_str, *_ in workouts_rows:
        try:
            workouts_keys.add((_norm_name(player), int(year_str)))
        except (ValueError, TypeError):
            continue

    # Drop player-seasons with zero measured anthro/athletic metrics —
    # those rows correspond to invitees who didn't participate (no anthro,
    # no drills, no comps). Use raw input columns only (ratios are
    # derived). Exempt players who appear in the workouts sheet.
    raw_metric_cols = [m for m, meta in METRICS.items() if meta["category"] != "ratio"]
    measured = df[raw_metric_cols].notna().sum(axis=1)
    has_workouts = df.apply(
        lambda r: (_norm_name(r["Player"]), int(r["Season"])) in workouts_keys,
        axis=1,
    )
    keep = (measured > 0) | has_workouts
    dropped = int((~keep).sum())
    rescued = int((has_workouts & (measured == 0)).sum())
    df = df[keep].reset_index(drop=True)
    if dropped:
        print(f"Dropped {dropped} player-seasons with no measured metrics and no workouts")
    if rescued:
        print(f"Rescued {rescued} zero-metric player-seasons that have workouts data")

    df["bucket"] = df["Position"].apply(bucket)

    # Derived ratios. NaN-safe: if either input is missing, the ratio is NaN.
    df["wingspan_to_height"] = df["Wingspan (in)"] / df["Height without shoes (in)"]
    df["reach_to_height"]    = df["Standing reach (in)"] / df["Height without shoes (in)"]
    df["hand_area"]          = df["Hand Length (in)"] * df["Hand Width (in)"]

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

    # Doppelgängers: per-player top-5 closest combine performances by Euclidean
    # distance over the percentile vector of the 13 anthro+athletic metrics.
    # Within-bucket only; require at least 7 shared (both non-NaN) metrics;
    # distance is the RMS of differences over the shared metrics. Ratios are
    # excluded — they're derived from the raw metrics and would double-count.
    metric_keys = [m for m, meta in METRICS.items() if meta["category"] != "ratio"]
    pct_matrix = df[[pct_cols[m] for m in metric_keys]].to_numpy(dtype=float)
    mask = ~np.isnan(pct_matrix)
    M_filled = np.where(mask, pct_matrix, 0.0)
    mask_int = mask.astype(np.float64)

    shared = mask_int @ mask_int.T  # NxN: number of metrics where both have data
    A = M_filled ** 2
    sum_a = A @ mask_int.T
    sum_b = mask_int @ A.T
    cross = M_filled @ M_filled.T
    sq = np.maximum(sum_a + sum_b - 2.0 * cross, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        dist = np.sqrt(sq / shared)
    dist[shared < 7] = np.inf
    # Restrict comps to the same position bucket: percentiles are computed
    # per bucket, so cross-bucket distances aren't meaningful.
    buckets_arr = df["bucket"].to_numpy()
    same_bucket = buckets_arr[:, None] == buckets_arr[None, :]
    dist[~same_bucket] = np.inf
    np.fill_diagonal(dist, np.inf)

    # Top-5 per row, in sort order
    top_n = 5
    n_rows = dist.shape[0]
    top_idx = np.argpartition(dist, kth=min(top_n, n_rows - 1), axis=1)[:, :top_n]
    # Sort the top_n by actual distance
    row_idx = np.arange(n_rows)[:, None]
    sort_order = np.argsort(dist[row_idx, top_idx], axis=1)
    top_idx = top_idx[row_idx, sort_order]

    # Cache per-row metadata for quick lookup
    seasons = df["Season"].astype(int).to_numpy()
    players = df["Player"].to_numpy()
    positions = df["Position"].to_numpy()
    buckets = df["bucket"].to_numpy()

    doppelgangers_by_idx = []
    for i in range(n_rows):
        comps = []
        for j in top_idx[i]:
            d = dist[i, j]
            if not np.isfinite(d):
                continue
            comps.append({
                "season": int(seasons[j]),
                "player": str(players[j]),
                "position": str(positions[j]) if isinstance(positions[j], str) else None,
                "bucket": str(buckets[j]),
                "distance": _round(float(d), 2),
                "shared_metrics": int(shared[i, j]),
            })
        doppelgangers_by_idx.append(comps)

    # Tier thresholds for the comp similarity label. Computed from the actual
    # top-5 distance distribution across all players so the labels distribute
    # evenly across the dataset rather than skewing to one bucket.
    all_dists = [c["distance"] for comps in doppelgangers_by_idx for c in comps]
    if all_dists:
        comp_dist_tiers = {
            "very_similar":     _round(float(np.percentile(all_dists, 25)), 2),
            "similar":          _round(float(np.percentile(all_dists, 50)), 2),
            "somewhat_similar": _round(float(np.percentile(all_dists, 75)), 2),
            "loosely_similar":  _round(float(np.percentile(all_dists, 90)), 2),
        }
    else:
        comp_dist_tiers = {
            "very_similar": 12.0, "similar": 16.0,
            "somewhat_similar": 20.0, "loosely_similar": 25.0,
        }

    # Build records
    records = []
    for idx, (_, row) in enumerate(df.iterrows()):
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
            "doppelgangers": doppelgangers_by_idx[idx],
            "profile_tags": [],   # filled in below once per-metric pcts are populated
        }
        for metric in METRICS:
            rec["metrics"][metric] = {
                "raw": _clean(row[metric]),
                "pct": _round(row[pct_cols[metric]]),
            }
        # Compute profile tags from the just-populated metric percentiles
        rec["profile_tags"] = assign_tags_for_player(
            {m: rec["metrics"][m]["pct"] for m in METRICS}
        )
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
        "comp_dist_tiers": comp_dist_tiers,
        "tag_meta": {
            slug: {
                "label":    label,
                "category": category,
                "metric":   metric,
                "rule":     rule,
                # tuple thresholds (pct_range) become 2-element lists in JSON
                "threshold": list(threshold) if isinstance(threshold, tuple) else threshold,
            }
            for slug, label, category, metric, rule, threshold in TAG_VOCABULARY
        },
        "players": records,
    }

    # Workouts merge runs against the already-fetched rows from the top
    # of main() so we don't hit the Sheets API twice per build.
    matched, unmatched = merge_workouts(records, workouts_rows)
    print(f"Workouts merged: {matched} players, {unmatched} sheet groups had no combine match")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), allow_nan=False, default=str)
    print(f"Wrote {OUT}: {len(records)} players, "
          f"{out['season_range'][0]}–{out['season_range'][1]}, "
          f"buckets={out['buckets']}")


if __name__ == "__main__":
    main()
