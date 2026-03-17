"""
Full validation harvest — runs all bundles one at a time, saves per-bundle
coverage CSVs to output/per_bundle/, then assembles the final outputs.
No parser changes.  No fixes.
"""
import sys, csv, json, time
from pathlib import Path

sys.path.insert(0, "/home/runner/workspace")
from parser_helpers import group_into_bundles, load_conference_map
from harvester import run

INPUT_DIR  = Path("input_pdfs")
OUTPUT_DIR = Path("output")
STAGING    = OUTPUT_DIR / "per_bundle"
STAGING.mkdir(parents=True, exist_ok=True)

REQUIRED_EVENTS = [
    "50 Free","100 Free","200 Free","500 Free","1650 Free",
    "100 Back","200 Back","100 Breast","200 Breast",
    "100 Fly","200 Fly","200 IM","400 IM","400 Free Relay",
]
OPTIONAL_EVENTS = {"1000 Free"}

def already_done(bid: str) -> bool:
    return (STAGING / f"{bid}.csv").exists()

def save_bundle_csv(bid: str, rows: list[dict]) -> None:
    path = STAGING / f"{bid}.csv"
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

def load_bundle_csv(bid: str) -> list[dict]:
    path = STAGING / f"{bid}.csv"
    if path.stat().st_size == 0:
        return []
    with open(path) as f:
        return list(csv.DictReader(f))

# ── Discover all bundles ────────────────────────────────────────────────────
conf_map = load_conference_map()
pdfs     = sorted(INPUT_DIR.glob("*.pdf")) + sorted(INPUT_DIR.glob("*.PDF"))
bundles  = group_into_bundles(pdfs, conf_map)

ordered_bids = sorted(bundles.keys())
print(f"\n{'='*60}")
print(f"  Full validation run — {len(ordered_bids)} bundles")
print(f"{'='*60}\n")

# ── Process each bundle ─────────────────────────────────────────────────────
t_total = time.time()
for bid in ordered_bids:
    stage_path = STAGING / f"{bid}.csv"
    if already_done(bid):
        print(f"  [SKIP  ] {bid} — already staged")
        continue

    print(f"  [RUN   ] {bid} ...", flush=True)
    t0 = time.time()
    try:
        run(INPUT_DIR, OUTPUT_DIR, bundle_filter=[bid])
        # Read the coverage CSV that harvester just wrote
        with open(OUTPUT_DIR / "event_coverage_report.csv") as f:
            rows = list(csv.DictReader(f))
        save_bundle_csv(bid, rows)
        elapsed = time.time() - t0
        captured = sum(1 for r in rows if r.get("Detected") == "yes")
        print(f"  [DONE  ] {bid}  {captured}/{len(rows)} captured  ({elapsed:.1f}s)")
    except Exception as e:
        print(f"  [ERROR ] {bid}: {e}")
        save_bundle_csv(bid, [])   # write empty so we skip on retry

print(f"\n  All bundles processed in {time.time()-t_total:.0f}s\n")

# ── Combine all coverage rows ───────────────────────────────────────────────
all_coverage: list[dict] = []
for bid in ordered_bids:
    rows = load_bundle_csv(bid)
    for r in rows:
        r["Bundle_ID"] = bid  # ensure present
    all_coverage.extend(rows)

# Write full event_coverage_report.csv
if all_coverage:
    fieldnames = list(all_coverage[0].keys())
    with open(OUTPUT_DIR / "event_coverage_report.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_coverage)
    print(f"  Wrote event_coverage_report.csv  ({len(all_coverage)} rows)")

# ── Build per-conference summary ────────────────────────────────────────────
from collections import defaultdict

conf_rows: dict[str, list[dict]] = defaultdict(list)
for r in all_coverage:
    conf_rows[r["Conference"]].append(r)

summary_rows: list[dict] = []

for conf, rows in sorted(conf_rows.items()):
    if conf == "Unknown":
        continue   # skip unresolved bundles

    # Required events only (exclude 1000 Free)
    required = [r for r in rows if r["Event_Name"] not in OPTIONAL_EVENTS]
    total_possible = len(required)
    captured = [r for r in required if r.get("Detected") == "yes"]
    missing  = [r for r in required if r.get("Detected") != "yes"]

    men_anchors   = sum(1 for r in rows
                        if r.get("Detected") == "yes" and r.get("Gender") == "men")
    women_anchors = sum(1 for r in rows
                        if r.get("Detected") == "yes" and r.get("Gender") == "women")

    missing_list  = "; ".join(
        f"{r['Gender']} {r['Event_Name']} ({r.get('Coverage_Status','')})"
        for r in missing
    )

    anchors = len(captured)
    if anchors == total_possible or all(
        r.get("Coverage_Status","") in
        ("missing_optional_1000","missing_data_ceiling","missing_likely_not_contested")
        for r in missing
    ):
        status = "Perfect"
    elif anchors >= 18:
        status = "Strong"
    else:
        status = "Gaps"

    summary_rows.append({
        "Conference":          conf,
        "Anchors_Captured":    anchors,
        "Total_Possible":      total_possible,
        "Men_Anchors":         men_anchors,
        "Women_Anchors":       women_anchors,
        "Missing_Events_Count": len(missing),
        "Missing_Events_List": missing_list,
        "Conference_Status":   status,
    })

with open(OUTPUT_DIR / "full_run_summary.csv", "w", newline="") as f:
    fnames = ["Conference","Anchors_Captured","Total_Possible","Men_Anchors",
              "Women_Anchors","Missing_Events_Count","Missing_Events_List",
              "Conference_Status"]
    w = csv.DictWriter(f, fieldnames=fnames)
    w.writeheader()
    w.writerows(summary_rows)
print(f"  Wrote full_run_summary.csv  ({len(summary_rows)} conferences)")

# ── Print overall summary ───────────────────────────────────────────────────
perfect = [r for r in summary_rows if r["Conference_Status"] == "Perfect"]
strong  = [r for r in summary_rows if r["Conference_Status"] == "Strong"]
gaps    = [r for r in summary_rows if r["Conference_Status"] == "Gaps"]
total_c = len(summary_rows)

all_missing_req = [
    r for r in all_coverage
    if r["Conference"] != "Unknown"
    and r.get("Detected") != "yes"
    and r["Event_Name"] not in OPTIONAL_EVENTS
]
parser_miss = [r for r in all_missing_req
               if r.get("Coverage_Status","") == "missing_true_parser_miss"]
ceiling     = [r for r in all_missing_req
               if r.get("Coverage_Status","") == "missing_data_ceiling"]
not_cont    = [r for r in all_missing_req
               if r.get("Coverage_Status","") in
               ("missing_optional_1000","missing_likely_not_contested")]

print(f"\n{'='*60}")
print(f"  FULL RUN — OVERALL SUMMARY")
print(f"{'='*60}")
print(f"  Conferences processed : {total_c}")
print(f"  Perfect               : {len(perfect)} ({100*len(perfect)//total_c}%)")
print(f"  Strong (≥18 anchors)  : {len(strong)} ({100*len(strong)//total_c}%)")
print(f"  Gaps (<18 anchors)    : {len(gaps)} ({100*len(gaps)//total_c}%)")
print(f"\n  Total missing events (excl. 1000 Free): {len(all_missing_req)}")
print(f"    missing_true_parser_miss     : {len(parser_miss)}")
print(f"    missing_data_ceiling         : {len(ceiling)}")
print(f"    missing_likely_not_contested : {len(not_cont)}")
print(f"\n  Per-conference breakdown:")
for r in summary_rows:
    print(f"    {r['Conference']:25s}  {r['Anchors_Captured']:3d}/{r['Total_Possible']:3d}  {r['Conference_Status']}")
print(f"\n{'='*60}\n")
