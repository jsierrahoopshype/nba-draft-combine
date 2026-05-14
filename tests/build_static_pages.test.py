"""Fixture-driven test for build_static_pages.py.

Synthesizes a small in-memory combine.json, points the builder at a
temp output dir, and asserts the emitted directory structure and per-
file content for the four meaningful cases:

    1. Single-combine player with measurements + workouts
       (e.g., Cooper Flagg) -> "Measurements + Workout List" title.
    2. Single-combine player with workouts only
       (e.g., Paolo Banchero) -> "Workout List" title.
    3. Single-combine player with measurements only
       -> "Measurements" title.
    4. Multi-combine player (e.g., Adem Bona '23/'24) -> one default
       file (most recent) + one per season.

Plus HTML escaping (apostrophe in a player name).

Run: python3 tests/build_static_pages.test.py
"""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import build_static_pages as bsp  # noqa: E402


passed = 0
failed = 0


def ok(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name}{(' — ' + detail) if detail else ''}")


def eq(name, actual, expected):
    ok(name, actual == expected, f"expected {expected!r}, got {actual!r}")


def make_player(name, slug, season, *, has_m, has_w):
    """Build a player record minimal-enough to pass through the meta
    helpers. The has_measurements/has_workouts helpers only inspect
    scores, metrics[*].raw, and workouts — everything else is opaque."""
    return {
        "player": name,
        "season": season,
        "player_slug": slug,
        "scores": {
            "overall": 85.0 if has_m else None,
            "anthro":   80.0 if has_m else None,
            "athletic": 75.0 if has_m else None,
            "shooting": None,
        },
        "metrics": {
            "Height without shoes (in)": {"raw": 79.0 if has_m else None},
        },
        "workouts": ["Houston", "Orlando"] if has_w else None,
    }


def build_fixture(tmp_dir):
    """Write a fixture combine.json and run the builder, returning
    (counts, out_dir)."""
    data = {
        "season_range": [2009, 2025],
        "players": [
            make_player("Cooper Flagg",   "cooper-flagg",   2025, has_m=True,  has_w=True),
            make_player("Paolo Banchero", "paolo-banchero", 2022, has_m=False, has_w=True),
            make_player("Hasheem Thabeet", "hasheem-thabeet", 2009, has_m=True, has_w=False),
            make_player("Adem Bona",      "adem-bona",      2023, has_m=True,  has_w=True),
            make_player("Adem Bona",      "adem-bona",      2024, has_m=True,  has_w=True),
            make_player("Shaq O'Neal",    "shaq-o-neal",    2020, has_m=True,  has_w=False),
        ],
    }
    data_path = os.path.join(tmp_dir, "combine.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    out_dir = os.path.join(tmp_dir, "prerendered")
    counts = bsp.build(out_dir=out_dir, data_path=data_path)
    return counts, out_dir


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def main():
    with tempfile.TemporaryDirectory() as tmp_dir:
        counts, out_dir = build_fixture(tmp_dir)

        print("\n— Counts")
        # 5 unique player_slug values → 5 canonical pages.
        eq("5 player canonical pages", counts["player_canonical"], 5)
        # 4 single-combine player-seasons + 2 Adem Bona seasons = 6.
        eq("6 player-season pages", counts["player_season"], 6)
        # Season landers only for seasons that actually have players in
        # the fixture: 2025, 2024, 2023, 2022, 2020, 2009 → 6.
        eq("6 season landers (one per distinct fixture season)", counts["season"], 6)
        eq("3 position+season for smax", counts["position"], 3)

        print("\n— Directory shape")
        ok("p/cooper-flagg/index.html exists",
           os.path.isfile(os.path.join(out_dir, "p/cooper-flagg/index.html")))
        ok("p/cooper-flagg/2025/index.html exists",
           os.path.isfile(os.path.join(out_dir, "p/cooper-flagg/2025/index.html")))
        ok("p/adem-bona/index.html exists (default = most recent)",
           os.path.isfile(os.path.join(out_dir, "p/adem-bona/index.html")))
        ok("p/adem-bona/2023/index.html exists",
           os.path.isfile(os.path.join(out_dir, "p/adem-bona/2023/index.html")))
        ok("p/adem-bona/2024/index.html exists",
           os.path.isfile(os.path.join(out_dir, "p/adem-bona/2024/index.html")))
        ok("s/2025/index.html exists",
           os.path.isfile(os.path.join(out_dir, "s/2025/index.html")))
        ok("s/2009/index.html exists (oldest)",
           os.path.isfile(os.path.join(out_dir, "s/2009/index.html")))
        ok("pos/2025-bigs/index.html exists",
           os.path.isfile(os.path.join(out_dir, "pos/2025-bigs/index.html")))
        ok("pos/2025-wings/index.html exists",
           os.path.isfile(os.path.join(out_dir, "pos/2025-wings/index.html")))
        ok("pos/2025-guards/index.html exists",
           os.path.isfile(os.path.join(out_dir, "pos/2025-guards/index.html")))

        print("\n— Cooper Flagg (measurements + workouts)")
        html_text = read(os.path.join(out_dir, "p/cooper-flagg/index.html"))
        ok("title is 'Measurements + Workout List' variant",
           "<title>Cooper Flagg 2025 NBA Draft Combine Measurements + Workout List | HoopsMatic</title>" in html_text)
        ok("og:title mirrors",
           'property="og:title" content="Cooper Flagg 2025 NBA Draft Combine Measurements + Workout List | HoopsMatic"' in html_text)
        ok("description mentions pre-draft workouts",
           "confirmed pre-draft workouts" in html_text)
        ok("twitter:card summary",
           'name="twitter:card" content="summary"' in html_text)
        # Single-combine Cooper Flagg -> canonical URL omits ?season=
        ok("canonical URL omits ?season= for single-combine",
           '?player=cooper-flagg"' in html_text and 'season=' not in html_text.split('?player=cooper-flagg"')[1].split('"', 1)[0],
           "season param should not appear in single-combine canonical URL")
        ok("meta refresh present",
           'http-equiv="refresh" content="0;url=https://jsierrahoopshype.github.io/nba-draft-combine/?player=cooper-flagg"' in html_text)
        ok("JS replace present",
           'window.location.replace("https://jsierrahoopshype.github.io/nba-draft-combine/?player=cooper-flagg")' in html_text)
        ok("<noscript> fallback link to canonical",
           '<a href="https://jsierrahoopshype.github.io/nba-draft-combine/?player=cooper-flagg">' in html_text)

        print("\n— Paolo Banchero (workouts only)")
        html_text = read(os.path.join(out_dir, "p/paolo-banchero/index.html"))
        ok("title uses 'Workout List' variant (no Measurements)",
           "<title>Paolo Banchero 2022 NBA Draft Workout List | HoopsMatic</title>" in html_text)
        ok("title does NOT include 'Measurements'",
           "Measurements" not in html_text.split("<title>")[1].split("</title>")[0])
        ok("description focuses on pre-draft workouts",
           "Paolo Banchero" in html_text and "confirmed pre-draft workouts before the 2022 NBA Draft" in html_text)

        print("\n— Hasheem Thabeet (measurements only)")
        html_text = read(os.path.join(out_dir, "p/hasheem-thabeet/index.html"))
        title_inner = html_text.split("<title>")[1].split("</title>")[0]
        eq("title is 'Measurements' variant",
           title_inner,
           "Hasheem Thabeet 2009 NBA Draft Combine Measurements | HoopsMatic")
        ok("description does NOT mention workouts",
           "workouts" not in html_text.split("description\" content=\"")[1].split("\"")[0])

        print("\n— Adem Bona (multi-combine)")
        default_html = read(os.path.join(out_dir, "p/adem-bona/index.html"))
        s2023_html = read(os.path.join(out_dir, "p/adem-bona/2023/index.html"))
        s2024_html = read(os.path.join(out_dir, "p/adem-bona/2024/index.html"))
        # Default page = most recent (2024).
        ok("default page targets 2024 (most recent)",
           "Adem Bona 2024" in default_html and "season=2024" in default_html)
        ok("default page does NOT mention 2023",
           "2023" not in default_html.split("<title>")[1].split("</title>")[0])
        # Multi-combine: even the default canonical URL must include
        # ?season= so the redirected SPA lands on the same record we
        # described in the meta — otherwise the SPA's "most recent"
        # fallback could disagree with the meta if a new combine is
        # ingested between pre-render and click.
        ok("default canonical URL includes ?season=2024 for multi-combine",
           "season=2024" in default_html)
        ok("2023 page targets 2023",
           "Adem Bona 2023" in s2023_html and "season=2023" in s2023_html)
        ok("2024 page targets 2024",
           "Adem Bona 2024" in s2024_html and "season=2024" in s2024_html)

        print("\n— Season + position landers")
        s2025_html = read(os.path.join(out_dir, "s/2025/index.html"))
        eq("season lander title",
           s2025_html.split("<title>")[1].split("</title>")[0],
           "2025 NBA Draft Prospects | HoopsMatic")
        ok("season lander canonical URL",
           'content="https://jsierrahoopshype.github.io/nba-draft-combine/?season=2025"' in s2025_html)

        bigs_html = read(os.path.join(out_dir, "pos/2025-bigs/index.html"))
        eq("position lander title",
           bigs_html.split("<title>")[1].split("</title>")[0],
           "2025 NBA Draft Prospects: Bigs | HoopsMatic")
        ok("position lander canonical URL",
           'content="https://jsierrahoopshype.github.io/nba-draft-combine/?position=Bigs&amp;season_start=2025&amp;season_end=2025"' in bigs_html)

        print("\n— HTML escaping (apostrophe)")
        oneal_html = read(os.path.join(out_dir, "p/shaq-o-neal/index.html"))
        ok("apostrophe escaped in attribute context (description)",
           "Shaq O&#x27;Neal" in oneal_html or "Shaq O&#39;Neal" in oneal_html)
        ok("apostrophe rendered literally inside <noscript> body",
           "Shaq O'Neal" in oneal_html.split("<noscript>")[1].split("</noscript>")[0])
        ok("no raw double-quote breaks attribute",
           '"Shaq O\'Neal"' not in oneal_html)

    print(f"\nResults: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
