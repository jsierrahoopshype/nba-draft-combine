"""Build static pre-rendered HTML pages for social-media crawlers.

Runs after build_data.py in CI. Reads data/combine.json and emits one
HTML file per (player, season) combination plus a small set of dashboard
filter routes (per-season landing pages, plus the current year's
position filters). Each emitted file carries the same <title>,
<meta name="description">, Open Graph, and Twitter Card tags that the
JS updatePageMeta() builds at runtime — so when a Bluesky / Twitter /
Facebook / LinkedIn / Discord crawler fetches the URL, it sees the
correct preview before the SPA ever runs.

A `<meta http-equiv="refresh">` plus a JS `window.location.replace()`
bounces real browsers to the canonical SPA URL (the `?player=…` shape
the existing app already supports). The pre-rendered tree never
duplicates the dashboard logic — it just owns the first byte for
external shares.

Failure mode: if anything in this script raises, the workflow's earlier
build_data.py commit still goes through (the YAML step is marked
continue-on-error). Pre-rendering is a "nice to have" — never breaks
the data refresh.
"""

import html
import json
import os
import re
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data", "combine.json")
OUT_DIR = os.path.join(BASE_DIR, "prerendered")
BASE_URL = "https://jsierrahoopshype.github.io/nba-draft-combine"

META_SUFFIX = " | HoopsMatic"
DEFAULT_DESC = (
    "Explore measurements, athletic scores, shooting drills, and historical "
    "comparisons for every NBA Draft Combine attendee since 2000."
)

# Mirrors the @media-independent JS updatePageMeta() logic from
# index.html. Any change to the runtime logic must be reflected here
# (and vice versa) so live and pre-rendered metas agree.

POSITIONS = ("Bigs", "Wings", "Guards")


def has_measurements(player):
    s = player.get("scores") or {}
    if any(s.get(k) is not None for k in ("anthro", "athletic", "shooting")):
        return True
    metrics = player.get("metrics") or {}
    for v in metrics.values():
        if v and v.get("raw") is not None:
            return True
    return False


def has_workouts(player):
    w = player.get("workouts")
    return isinstance(w, list) and len(w) > 0


def player_meta(player):
    """Return (title, description) for a player record. Three shapes
    based on what data the record actually carries."""
    name = player["player"]
    season = player["season"]
    has_m = has_measurements(player)
    has_w = has_workouts(player)
    if has_m and has_w:
        title = f"{name} {season} NBA Draft Combine Measurements + Workout List"
        desc = (
            f"{name}'s {season} NBA Draft Combine measurements, athletic scores, "
            f"shooting drills, historical comparisons, and confirmed pre-draft workouts."
        )
    elif has_m:
        title = f"{name} {season} NBA Draft Combine Measurements"
        desc = (
            f"{name}'s {season} NBA Draft Combine measurements, athletic scores, "
            f"shooting drills, and historical comparisons."
        )
    elif has_w:
        title = f"{name} {season} NBA Draft Workout List"
        desc = f"{name}'s confirmed pre-draft workouts before the {season} NBA Draft."
    else:
        # Defensive fallback — shouldn't fire for any current record.
        title = f"{name} {season} NBA Draft Profile"
        desc = f"{name}'s {season} NBA Draft profile."
    return title + META_SUFFIX, desc


def season_meta(year):
    return (
        f"{year} NBA Draft Prospects{META_SUFFIX}",
        f"Measurements, athletic scores, and shooting drills for prospects from "
        f"the {year} NBA Draft Combine.",
    )


def position_season_meta(position, year):
    return (
        f"{year} NBA Draft Prospects: {position}{META_SUFFIX}",
        f"{position} measurements and athletic scores from the {year} NBA Draft Combine.",
    )


def canonical_player_url(slug, season=None):
    if season is None:
        return f"{BASE_URL}/?player={slug}"
    return f"{BASE_URL}/?player={slug}&season={season}"


def canonical_season_url(year):
    return f"{BASE_URL}/?season={year}"


def canonical_position_url(position, year):
    return f"{BASE_URL}/?position={position}&season_start={year}&season_end={year}"


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title_html}</title>
<meta name="description" content="{description_attr}">
<meta property="og:title" content="{title_attr}">
<meta property="og:description" content="{description_attr}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical_attr}">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title_attr}">
<meta name="twitter:description" content="{description_attr}">
<meta http-equiv="refresh" content="0;url={canonical_attr}">
<script>window.location.replace({canonical_json});</script>
</head>
<body>
<noscript>
<h1>{title_html}</h1>
<p>{description_html}</p>
<p><a href="{canonical_attr}">Continue to NBA Draft Prospect Central</a></p>
</noscript>
</body>
</html>
"""


def render_page(title, description, canonical_url):
    # html.escape with quote=True covers attribute contexts; for the
    # JS string we use json.dumps which handles every escape (quotes,
    # backslashes, control chars, line separators).
    return HTML_TEMPLATE.format(
        title_html=html.escape(title, quote=False),
        title_attr=html.escape(title, quote=True),
        description_html=html.escape(description, quote=False),
        description_attr=html.escape(description, quote=True),
        canonical_attr=html.escape(canonical_url, quote=True),
        canonical_json=json.dumps(canonical_url),
    )


def slug_path_segment(slug):
    """Defensive slug sanitization for filesystem + URL safety.

    player_slug is already lowercase-alphanumeric-and-hyphens per
    build_data.py's _player_slug_base; this strip is belt-and-suspenders
    in case future ingestion introduces something unexpected."""
    return re.sub(r"[^a-z0-9-]", "", str(slug or "").lower()).strip("-") or "unknown"


def write_html(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def build(out_dir=OUT_DIR, data_path=DATA_PATH):
    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    players = data["players"]
    smin, smax = data["season_range"]

    # Start clean — stale files (renamed players, last-year position
    # combos) shouldn't linger. The directory is workflow-managed; the
    # repo carries whatever the most recent build produced.
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    counts = {"player_canonical": 0, "player_season": 0, "season": 0, "position": 0}

    # Group records by player_slug so we can emit one "default" page per
    # slug (most-recent season) plus one page per (slug, season). Sort
    # descending so default = most recent.
    groups = {}
    for rec in players:
        slug = rec.get("player_slug")
        if not slug:
            continue
        groups.setdefault(slug, []).append(rec)

    for slug, recs in groups.items():
        seg = slug_path_segment(slug)
        recs_sorted = sorted(recs, key=lambda r: r["season"], reverse=True)

        # Default page: most recent season, canonical URL omits ?season=
        # iff the slug has only one record (matches the SPA's playerUrl()
        # behavior). For multi-combine, include the season so the SPA
        # lands on the same record after redirect.
        default_rec = recs_sorted[0]
        title, desc = player_meta(default_rec)
        if len(recs_sorted) == 1:
            canon = canonical_player_url(slug)
        else:
            canon = canonical_player_url(slug, default_rec["season"])
        write_html(
            os.path.join(out_dir, "p", seg, "index.html"),
            render_page(title, desc, canon),
        )
        counts["player_canonical"] += 1

        # Per-season pages — one for each combine the player attended.
        for rec in recs_sorted:
            title, desc = player_meta(rec)
            canon = canonical_player_url(slug, rec["season"])
            write_html(
                os.path.join(out_dir, "p", seg, str(rec["season"]), "index.html"),
                render_page(title, desc, canon),
            )
            counts["player_season"] += 1

    # Per-season landing pages — every season in the dataset gets one.
    # Cheap (a few dozen files) and covers the "share the 2025 board"
    # case directly.
    seasons = sorted({int(p["season"]) for p in players})
    for year in seasons:
        title, desc = season_meta(year)
        canon = canonical_season_url(year)
        write_html(
            os.path.join(out_dir, "s", str(year), "index.html"),
            render_page(title, desc, canon),
        )
        counts["season"] += 1

    # Position+season for the current year only — keeps file count down
    # and matches "share this year's bigs" being the realistic case.
    for pos in POSITIONS:
        title, desc = position_season_meta(pos, smax)
        canon = canonical_position_url(pos, smax)
        write_html(
            os.path.join(out_dir, "pos", f"{smax}-{pos.lower()}", "index.html"),
            render_page(title, desc, canon),
        )
        counts["position"] += 1

    return counts


def main():
    counts = build()
    total = sum(counts.values())
    print(
        f"Pre-rendered {total} pages → {OUT_DIR}: "
        f"{counts['player_canonical']} player canonicals, "
        f"{counts['player_season']} player-seasons, "
        f"{counts['season']} season landers, "
        f"{counts['position']} position+season."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Never fail the workflow on the pre-render step — emit a
        # diagnostic and exit non-zero so CI surfaces it via
        # continue-on-error, but the earlier data commit still lands.
        print(f"Pre-render failed: {e!r}", file=sys.stderr)
        raise
