"""Rescue-criterion regression test.

The zero-metric drop in build_data.main() is bypassed for players who
have at least one *confirmed workout team* in the workouts sheet —
meaning a row whose team cell (column C) is non-empty after strip.
Rows that only populate draft_team / real_team metadata (columns D/E)
do not rescue, so editorial-only entries like Wembanyama 2023 don't
sneak in with no workout history to display.

Run: python3 tests/rescue_criterion.test.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import build_data  # noqa: E402


def case(label, rows, expected):
    actual = build_data.rescue_keys_from_workouts(rows)
    ok = actual == expected
    status = "✓" if ok else "✗"
    print(f"  {status} {label}")
    if not ok:
        print(f"      expected: {sorted(expected)}")
        print(f"      actual:   {sorted(actual)}")
    return ok


def main():
    passed = 0
    failed = 0

    print("— rescue_keys_from_workouts: confirmed-team gate")

    # Each fixture row mirrors the (player, year_str, team, draft_team,
    # real_team) shape returned by fetch_workouts_rows after padding.

    # 1. Confirmed workout team → rescued.
    ok = case(
        "non-empty team cell rescues",
        [("Paolo Banchero", "2022", "Houston", "ORL", "ORL")],
        {("paolo banchero", 2022)},
    )
    passed += ok; failed += not ok

    # 2. The Wembanyama case: only draft_team / real_team, no team cell.
    ok = case(
        "draft_team/real_team only → NOT rescued",
        [("Victor Wembanyama", "2023", "", "SAS", "SAS")],
        set(),
    )
    passed += ok; failed += not ok

    # 3. Whitespace-only team cell does not count.
    ok = case(
        "whitespace-only team cell → NOT rescued",
        [("Some Player", "2024", "   ", "", "")],
        set(),
    )
    passed += ok; failed += not ok

    # 4. Fully empty sheet entry → not rescued.
    ok = case(
        "empty sheet entry → NOT rescued",
        [("Empty Row Player", "2024", "", "", "")],
        set(),
    )
    passed += ok; failed += not ok

    # 5. Multiple rows, mixed: one with team, one without — single key.
    ok = case(
        "mixed rows: one confirmed, one metadata-only → single key",
        [
            ("Cade Cunningham", "2021", "Detroit", "DET", "DET"),
            ("Cade Cunningham", "2021", "",        "DET", "DET"),
        ],
        {("cade cunningham", 2021)},
    )
    passed += ok; failed += not ok

    # 6. Unparseable year is silently skipped (not a fatal error).
    ok = case(
        "unparseable year → silently skipped",
        [("Mystery", "not-a-year", "Boston", "", "")],
        set(),
    )
    passed += ok; failed += not ok

    # 7. Accented name normalizes the same way _norm_name handles it.
    ok = case(
        "accented name normalized via _norm_name",
        [("José Álvarado", "2021", "Atlanta", "", "")],
        {("jose alvarado", 2021)},
    )
    passed += ok; failed += not ok

    # 8. Short row (under 5 cells) is tolerated.
    ok = case(
        "short row (3 cells) still works when team present",
        [("Short Row Player", "2024", "Boston")],
        {("short row player", 2024)},
    )
    passed += ok; failed += not ok

    # 9. Short row with no team cell at all → not rescued.
    ok = case(
        "short row (2 cells, no team column) → NOT rescued",
        [("Bare Row", "2024")],
        set(),
    )
    passed += ok; failed += not ok

    print()
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
