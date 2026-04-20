"""
Pre-generate cacheable sections for 10 core schools.
Sections: What School Is Known For, Campus Life (main + More), Cost Layer 1

Uses the existing pregen service — same model (claude-sonnet-4-6), same web search,
same DB write path. This is the right way to warm the cache.

Run from the repo root:
    python scripts/pregen_10_schools.py

Each school takes 60-120 seconds (3 web-search agentic loops in series).
Run this once on Replit after pulling from GitHub.
"""

import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.pregen import (
    get_or_generate_school_content,
    get_or_generate_program_content,
    read_school_cache,
)

# ---------------------------------------------------------------------------
# School registry — names must match SCHOOL_META keys exactly.
# Harvard and Stanford are OOU (no D3 swim data) but we still pregen
# known_for / campus_life / cost since those sections are school-level.
# ---------------------------------------------------------------------------

SCHOOLS = [
    {
        "name":        "Johns Hopkins University",
        "division":    "D3",
        "conference":  "Centennial",
        "region":      "Baltimore, MD",
        "school_type": "Research University",
        "meta": {
            "freshmanHousing":     "",
            "greekLife":           "moderate",
            "residentialPattern":  "mostly on-campus freshman year",
            "athleteIntegration":  "integrated",
            "genderRatio":         "slight male majority",
            "townVibe":            "urban",
            "campusTemperature":   "competitive, research-driven",
        },
    },
    {
        "name":        "Case Western Reserve University",
        "division":    "D3",
        "conference":  "UAA",
        "region":      "Cleveland, OH",
        "school_type": "Research University",
        "meta": {
            "freshmanHousing":     "North Residential Village suite-style",
            "greekLife":           "moderate",
            "residentialPattern":  "mostly on-campus freshman and sophomore year",
            "athleteIntegration":  "integrated",
            "genderRatio":         "slight male majority",
            "townVibe":            "urban neighborhood (University Circle)",
            "campusTemperature":   "intense STEM, collaborative within cohorts",
        },
    },
    {
        "name":        "Carnegie Mellon University",
        "division":    "D3",
        "conference":  "UAA",
        "region":      "Pittsburgh, PA",
        "school_type": "Research University",
        "meta": {
            "freshmanHousing":     "required freshman year",
            "greekLife":           "moderate",
            "residentialPattern":  "mix of on and off campus",
            "athleteIntegration":  "integrated",
            "genderRatio":         "male majority (STEM-heavy)",
            "townVibe":            "urban (Oakland/Squirrel Hill neighborhood)",
            "campusTemperature":   "highly competitive, career-driven",
        },
    },
    {
        "name":        "Emory",
        "division":    "D3",
        "conference":  "UAA",
        "region":      "Atlanta, GA",
        "school_type": "Research University",
        "meta": {
            "freshmanHousing":     "required freshman year",
            "greekLife":           "strong",
            "residentialPattern":  "mostly on-campus first two years",
            "athleteIntegration":  "integrated",
            "genderRatio":         "female majority",
            "townVibe":            "suburban Atlanta",
            "campusTemperature":   "pre-med dominant, social and warm",
        },
    },
    {
        "name":        "Macalester College",
        "division":    "D3",
        "conference":  "MIAC",
        "region":      "Saint Paul, MN",
        "school_type": "Liberal Arts College",
        "meta": {
            "freshmanHousing":     "required freshman year",
            "greekLife":           "none",
            "residentialPattern":  "mix of on and off campus",
            "athleteIntegration":  "integrated",
            "genderRatio":         "female majority",
            "townVibe":            "urban (Grand Avenue neighborhood)",
            "campusTemperature":   "globally focused, politically engaged",
        },
    },
    {
        "name":        "Bowdoin College",
        "division":    "D3",
        "conference":  "NESCAC",
        "region":      "Brunswick, ME",
        "school_type": "Liberal Arts College",
        "meta": {
            "freshmanHousing":     "residential college system",
            "greekLife":           "none (residential colleges replace)",
            "residentialPattern":  "mostly on-campus all four years",
            "athleteIntegration":  "central to campus life",
            "genderRatio":         "roughly even",
            "townVibe":            "small town, coastal Maine",
            "campusTemperature":   "outdoorsy, intellectual, collaborative",
        },
    },
    {
        "name":        "Worcester Polytechnic Institute",
        "division":    "D3",
        "conference":  "NEWMAC",
        "region":      "Worcester, MA",
        "school_type": "Technical Institute",
        "meta": {
            "freshmanHousing":     "required freshman year",
            "greekLife":           "moderate",
            "residentialPattern":  "mix of on and off campus",
            "athleteIntegration":  "integrated",
            "genderRatio":         "strong male majority",
            "townVibe":            "small city",
            "campusTemperature":   "project-driven, collaborative, fast-paced",
        },
    },
    {
        "name":        "MIT",
        "division":    "D3",
        "conference":  "NEWMAC",
        "region":      "Cambridge, MA",
        "school_type": "Technical Institute",
        "meta": {
            "freshmanHousing":     "guaranteed 4 years, dorm culture central",
            "greekLife":           "moderate",
            "residentialPattern":  "mostly on-campus",
            "athleteIntegration":  "integrated",
            "genderRatio":         "roughly even",
            "townVibe":            "urban (Cambridge/Boston)",
            "campusTemperature":   "intensely intellectual, maker culture",
        },
    },
    {
        "name":        "Harvard University",
        "division":    "D1",
        "conference":  "Ivy League",
        "region":      "Cambridge, MA",
        "school_type": "Research University",
        "meta": {
            "freshmanHousing":     "freshman yard (shared dorms by house)",
            "greekLife":           "none official (final clubs exist)",
            "residentialPattern":  "house system all four years",
            "athleteIntegration":  "integrated",
            "genderRatio":         "roughly even",
            "townVibe":            "urban (Cambridge)",
            "campusTemperature":   "competitive and diverse",
        },
    },
    {
        "name":        "Stanford University",
        "division":    "D1",
        "conference":  "ACC",
        "region":      "Palo Alto, CA",
        "school_type": "Research University",
        "meta": {
            "freshmanHousing":     "required freshman year",
            "greekLife":           "moderate",
            "residentialPattern":  "guaranteed housing all 4 years",
            "athleteIntegration":  "central to campus life",
            "genderRatio":         "roughly even",
            "townVibe":            "suburban (Silicon Valley)",
            "campusTemperature":   "entrepreneurial, collaborative, ambitious",
        },
    },
]


def _bar(school_name, i, total):
    print(f"\n[{i}/{total}] {'='*50}")
    print(f"  {school_name}")
    print(f"  {'='*50}")


def main():
    total = len(SCHOOLS)
    print(f"\nLane4 pregen — {total} schools")
    print(f"Model: claude-sonnet-4-6 with web_search_20250305")
    print(f"Each school: ~60-120s (3 agentic loops in series)")
    print(f"Estimated total: {total * 90 // 60}-{total * 120 // 60} minutes\n")

    results = {"hit": [], "generated": [], "failed": []}

    for i, school in enumerate(SCHOOLS, 1):
        name = school["name"]
        _bar(name, i, total)

        # Skip if already fully cached
        cached = read_school_cache(name)
        if cached.get("known_for") and cached.get("campus_life_main") and cached.get("coa"):
            print(f"  SKIP — already fully cached")
            results["hit"].append(name)
            continue

        t0 = time.time()
        try:
            content = get_or_generate_school_content(
                school_name=name,
                division=school["division"],
                conference=school["conference"],
                region=school["region"],
                school_type=school["school_type"],
                meta=school["meta"],
            )
            elapsed = time.time() - t0
            kf_len = len(content.get("known_for") or "")
            cl_len = len(content.get("campus_life_main") or "")
            coa    = content.get("coa")
            print(f"  known_for:        {kf_len} chars")
            print(f"  campus_life_main: {cl_len} chars")
            print(f"  coa:              ${coa:,}" if coa else "  coa:              missing")
            print(f"  elapsed:          {elapsed:.0f}s")
            results["generated"].append(name)
        except Exception as e:
            print(f"  FAILED: {e}")
            results["failed"].append(name)

    print(f"\n{'='*55}")
    print(f"DONE")
    print(f"  Cache hits (skipped): {len(results['hit'])}")
    print(f"  Generated:            {len(results['generated'])}")
    print(f"  Failed:               {len(results['failed'])}")
    if results["failed"]:
        print(f"\n  Failed schools:")
        for s in results["failed"]:
            print(f"    - {s}")
    print()


if __name__ == "__main__":
    main()
