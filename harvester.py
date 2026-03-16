#!/usr/bin/env python3
"""
Lane4 Data Harvester v1
-----------------------
Batch PDF parser for NCAA swim conference championship results.

Usage:
    python harvester.py [--input INPUT_DIR] [--output OUTPUT_DIR]

Drop PDFs in input_pdfs/, run this script, get:
    output/event_anchors.csv   — 1st/8th/16th + sec_per_place per event per bundle
    output/team_scores.csv     — Men's team standings per bundle
    output/review_flags.csv    — Issues needing human review

File bundling:
    PDFs are grouped by (conference, year, gender).  Day/session/prelim
    labels in filenames are metadata within a bundle, not separate meets.
    Finals results are preferred over prelims during merge.

Set HARVESTER_DEBUG=1 for verbose error tracebacks.
"""

import argparse
import csv
import os
import re
import sys
import traceback
from pathlib import Path

from parser_helpers import (
    TARGET_EVENTS,
    detect_conference,
    extract_pages,
    group_into_bundles,
    is_men_event_header,
    is_time_plausible,
    load_conference_map,
    looks_like_psych_sheet,
    normalize_event_name,
    parse_filename_metadata,
    parse_place_and_time,
    parse_team_scores,
    detect_session_type_from_text,
    time_to_seconds,
    seconds_to_time,
)

# ── Output schema ─────────────────────────────────────────────────────────────

ANCHOR_HEADER = [
    "Conference", "Year", "Gender", "Bundle_ID", "Event",
    "1st", "8th", "16th",
    "1st_seconds", "8th_seconds", "16th_seconds",
    "Sec_per_place", "Source_File",
]

TEAM_HEADER = [
    "Conference", "Year", "Gender", "Bundle_ID",
    "Team", "Team_Score", "Source_File",
]

FLAG_HEADER = [
    "Source_File", "Bundle_ID", "Conference", "Year", "Gender", "Event", "Issue",
]

ANCHOR_PLACES = [1, 8, 16]


# ── Event result accumulator ──────────────────────────────────────────────────

class EventAccumulator:
    """Collects (place → seconds) for one event from one PDF."""

    def __init__(self, name: str, source: str, session: str, is_prelim: bool):
        self.name      = name
        self.source    = source       # filename this came from
        self.session   = session      # 'finals', 'prelims', 'unknown'
        self.is_prelim = is_prelim    # from filename metadata

        # session_score: higher = more likely to be finals
        # finals=3, unknown=1, prelims=0
        self.session_score = 3 if session == "finals" else (0 if session == "prelims" else 1)
        # Bump score if filename also says finals
        if not is_prelim and session in ("finals", "unknown"):
            self.session_score = max(self.session_score, 1)
        if is_prelim:
            self.session_score = 0

        self.places: dict[int, float] = {}

    def add(self, place: int, time_str: str):
        sec = time_to_seconds(time_str)
        if sec is not None and place not in self.places:
            self.places[place] = sec

    def completeness(self) -> int:
        """Number of the three anchor places we have (0-3)."""
        return sum(1 for p in ANCHOR_PLACES if p in self.places)

    def is_finals(self) -> bool:
        """True unless this is explicitly a prelim result."""
        return self.session_score > 0

    def anchor(self) -> dict | None:
        """Return anchor dict if all 3 places present, else None."""
        if all(p in self.places for p in ANCHOR_PLACES):
            t1  = self.places[1]
            t8  = self.places[8]
            t16 = self.places[16]
            spp = round((t16 - t1) / 15, 4) if t16 != t1 else 0.0
            return {
                "1st":           seconds_to_time(t1),
                "8th":           seconds_to_time(t8),
                "16th":          seconds_to_time(t16),
                "1st_seconds":   round(t1, 3),
                "8th_seconds":   round(t8, 3),
                "16th_seconds":  round(t16, 3),
                "Sec_per_place": spp,
            }
        return None

    def missing_places(self) -> list[int]:
        return [p for p in ANCHOR_PLACES if p not in self.places]


# ── Single-PDF raw parser ─────────────────────────────────────────────────────

def parse_pdf_raw(
    pdf_path: Path,
    meta: dict,
    bundle: dict,
) -> tuple[dict[str, EventAccumulator], list[dict], list[dict]]:
    """
    Parse one PDF and return raw event accumulators (not final rows).

    Returns:
        events     — {event_name: EventAccumulator}  (men's results only)
        team_rows  — list of team score dicts
        flag_rows  — list of flag dicts
    """
    source     = pdf_path.name
    conference = bundle["conference"]
    year       = bundle["year"]
    gender     = bundle["gender"]
    bundle_id  = bundle["bundle_id"]

    def flag(event: str, issue: str) -> dict:
        return {
            "Source_File": source,
            "Bundle_ID":   bundle_id,
            "Conference":  conference,
            "Year":        year,
            "Gender":      gender,
            "Event":       event,
            "Issue":       issue,
        }

    events:    dict[str, EventAccumulator] = {}
    flag_rows: list[dict] = []

    # ── Extract pages ─────────────────────────────────────────────────────────
    try:
        pages = extract_pages(str(pdf_path))
    except Exception as exc:
        flag_rows.append(flag("", f"PDF extraction failed: {exc}"))
        return events, [], flag_rows

    # Refine conference from PDF text if still Unknown
    if conference == "Unknown":
        title_text = " ".join(pages[:3])[:3000]
        resolved = detect_conference(source, title_text)
        if resolved != "Unknown":
            bundle["conference"] = resolved
            conference = resolved
            flag_rows.append(flag("", f"Conference unknown from filename — guessed from PDF text: {resolved}"))

    # ── Women-only file check ─────────────────────────────────────────────────
    if gender == "women":
        flag_rows.append(flag("", "Women-only file — event anchors skipped (men's output only)"))
        team_rows = parse_team_scores(pages, conference, source)
        return events, team_rows, flag_rows

    # ── Psych/heat sheet check ────────────────────────────────────────────────
    if looks_like_psych_sheet(pages):
        flag_rows.append(flag("", "PDF looks like psych/heat sheet — skipped"))
        return events, [], flag_rows

    # ── Detect session type ───────────────────────────────────────────────────
    # Filename metadata takes priority; PDF content is a tiebreaker.
    if meta["is_prelim"]:
        session = "prelims"
    elif meta["is_final"]:
        session = "finals"
    else:
        session = detect_session_type_from_text(pages)

    # ── Line-by-line state machine ────────────────────────────────────────────
    all_lines: list[str] = []
    for page in pages:
        all_lines.extend(page.splitlines())
        all_lines.append("<<<PAGE_BREAK>>>")

    # Check if combined PDF uses explicit section headers
    has_section_headers = any(
        re.search(r"\bwomen'?s?\s+(swimming|results|championship)", ln, re.IGNORECASE)
        for ln in all_lines
    )

    current_event: EventAccumulator | None = None
    in_women_section = False
    finalized: dict[str, EventAccumulator] = {}
    unrecognized_headers: list[str] = []

    def finalize():
        nonlocal current_event
        if current_event and current_event.places:
            existing = finalized.get(current_event.name)
            if existing is None:
                finalized[current_event.name] = current_event
            elif current_event.completeness() > existing.completeness():
                finalized[current_event.name] = current_event
        current_event = None

    for raw_line in all_lines:
        line = raw_line.strip()

        # Section boundary detection (combined PDFs)
        if has_section_headers:
            if re.search(
                r"\bwomen'?s?\s+(swimming|results|championship|individual)",
                line, re.IGNORECASE
            ):
                finalize()
                in_women_section = True
                continue
            if re.search(
                r"\bmen'?s?\s+(swimming|results|championship|individual)",
                line, re.IGNORECASE
            ):
                finalize()
                in_women_section = False
                continue

        if in_women_section:
            continue

        # Event header detection
        if is_men_event_header(line):
            event_name = normalize_event_name(line)
            if event_name and event_name in TARGET_EVENTS:
                finalize()
                current_event = EventAccumulator(
                    event_name, source, session, meta["is_prelim"]
                )
            else:
                # Header detected but normalization failed.
                # IMPORTANT: finalize and close current event to avoid bleeding
                # results from the next unrecognized event into the previous one.
                finalize()
                if line not in unrecognized_headers:
                    unrecognized_headers.append(line)
            continue

        # Result row
        if current_event:
            place, time_str = parse_place_and_time(line)
            if place is not None:
                current_event.add(place, time_str)

    finalize()

    # Flag unrecognized headers (deduped, capped at 5 to avoid log spam)
    for h in unrecognized_headers[:5]:
        flag_rows.append(flag("", f"Event header variation not normalized: {h!r}"))

    if not finalized:
        flag_rows.append(flag("", "No men's target events found in PDF"))

    # Team scores
    team_rows = parse_team_scores(pages, conference, source)

    return finalized, team_rows, flag_rows


# ── Bundle merge ──────────────────────────────────────────────────────────────

def merge_bundle(
    bundle: dict,
    file_results: list[tuple[dict[str, EventAccumulator], list[dict], list[dict]]],
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """
    Merge raw results from all files in a bundle into final CSV rows.

    Deduplication strategy per event:
    1. Higher session_score (finals > unknown > prelims) wins.
    2. Among same score tier, most complete (anchor places) wins.
    3. Tie → keep the version with more total places.
    4. Sanity check 1st-place time; flag if implausible.

    Returns (event_rows, team_rows, flag_rows, summary_dict).
    """
    conference = bundle["conference"]
    year       = bundle["year"]
    gender     = bundle["gender"]
    bundle_id  = bundle["bundle_id"]

    def flag(event: str, source: str, issue: str) -> dict:
        return {
            "Source_File": source,
            "Bundle_ID":   bundle_id,
            "Conference":  conference,
            "Year":        year,
            "Gender":      gender,
            "Event":       event,
            "Issue":       issue,
        }

    # Collect all accumulators per event name
    all_accs: dict[str, list[EventAccumulator]] = {}
    all_team_rows: list[dict] = []
    all_flag_rows: list[dict] = []

    for evs, tms, fls in file_results:
        all_flag_rows.extend(fls)
        for t in tms:
            all_team_rows.append({
                **t,
                "Year":      year,
                "Gender":    gender,
                "Bundle_ID": bundle_id,
            })
        for name, acc in evs.items():
            all_accs.setdefault(name, []).append(acc)

    # Deduplicate team rows — keep first occurrence per team
    seen_teams: set[str] = set()
    unique_teams: list[dict] = []
    for row in all_team_rows:
        key = row["Team"].lower()
        if key not in seen_teams:
            seen_teams.add(key)
            unique_teams.append(row)

    if not unique_teams:
        sources = ", ".join(str(p.name) for p, _ in bundle["paths"])
        all_flag_rows.append(flag("", sources, "Team scores not found in any bundle file"))

    # Per-event merging
    event_rows: list[dict] = []
    events_found:   list[str] = []
    events_missing: list[str] = []

    for event_name in TARGET_EVENTS:
        accs = all_accs.get(event_name)
        if not accs:
            events_missing.append(event_name)
            continue

        events_found.append(event_name)

        # Sort candidates: highest session_score first, then most complete
        accs_sorted = sorted(
            accs,
            key=lambda a: (a.session_score, a.completeness(), len(a.places)),
            reverse=True,
        )
        best = accs_sorted[0]

        # Flag session choice
        only_prelims = all(a.session_score == 0 for a in accs)
        if only_prelims:
            all_flag_rows.append(flag(
                event_name, best.source,
                "Only prelim results found — no finals detected"
            ))
        elif best.session_score == 1:
            # session was 'unknown' — note it
            pass  # don't spam flags for every unknown-session event

        # Flag multiple conflicting finals versions
        finals_accs = [a for a in accs if a.session_score >= 2]
        if len(finals_accs) > 1:
            times_1st = {round(a.places[1], 2) for a in finals_accs if 1 in a.places}
            if len(times_1st) > 1:
                sources_str = ", ".join(a.source for a in finals_accs)
                all_flag_rows.append(flag(
                    event_name, sources_str,
                    "Multiple conflicting finals versions — 1st-place times: "
                    + ", ".join(seconds_to_time(t) for t in sorted(times_1st))
                ))

        anchor = best.anchor()
        if anchor:
            # Sanity check on 1st-place time
            t1_sec = anchor["1st_seconds"]
            plausible = is_time_plausible(event_name, t1_sec)
            if not plausible:
                # Try next best candidate that is plausible
                fallback = None
                for alt in accs_sorted[1:]:
                    if 1 in alt.places and is_time_plausible(event_name, alt.places[1]):
                        if alt.anchor():
                            fallback = alt
                            break
                if fallback:
                    all_flag_rows.append(flag(
                        event_name, best.source,
                        f"Implausible 1st-place time {anchor['1st']} — "
                        f"using fallback from {fallback.source}"
                    ))
                    best = fallback
                    anchor = best.anchor()
                else:
                    all_flag_rows.append(flag(
                        event_name, best.source,
                        f"Implausible 1st-place time {anchor['1st']} — no better candidate, kept"
                    ))

            event_rows.append({
                "Conference":    conference,
                "Year":          year,
                "Gender":        gender,
                "Bundle_ID":     bundle_id,
                "Event":         event_name,
                "1st":           anchor["1st"],
                "8th":           anchor["8th"],
                "16th":          anchor["16th"],
                "1st_seconds":   anchor["1st_seconds"],
                "8th_seconds":   anchor["8th_seconds"],
                "16th_seconds":  anchor["16th_seconds"],
                "Sec_per_place": anchor["Sec_per_place"],
                "Source_File":   best.source,
            })
        else:
            missing = best.missing_places()
            place_labels = {1: "1st", 8: "8th", 16: "16th"}
            miss_str = ", ".join(place_labels[p] for p in missing)
            all_flag_rows.append(flag(
                event_name, best.source,
                f"Event missing from final merged bundle — {miss_str} place not found"
            ))
            events_missing.append(event_name)
            events_found.remove(event_name)

    summary = {
        "bundle_id":      bundle_id,
        "conference":     conference,
        "year":           year,
        "gender":         gender,
        "files":          [str(p.name) for p, _ in bundle["paths"]],
        "events_found":   events_found,
        "events_missing": events_missing,
        "n_anchors":      len(event_rows),
        "n_teams":        len(unique_teams),
        "n_flags":        len(all_flag_rows),
    }

    return event_rows, unique_teams, all_flag_rows, summary


# ── CSV writer ────────────────────────────────────────────────────────────────

def write_csv(path: Path, header: list[str], rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Bundle summary printer ─────────────────────────────────────────────────────

def print_bundle_summary(summary: dict):
    bid   = summary["bundle_id"]
    conf  = summary["conference"]
    year  = summary["year"]
    gend  = summary["gender"]
    files = summary["files"]
    found = summary["events_found"]
    miss  = summary["events_missing"]

    print(f"\n  ┌─ Bundle Summary: {bid}")
    print(f"  │  Conference : {conf}")
    print(f"  │  Year       : {year}   Gender: {gend}")
    print(f"  │  Files      : {len(files)}")
    for f in files:
        print(f"  │    · {f}")
    print(f"  │  Events found   ({len(found):2d}): {', '.join(found) if found else '—'}")
    if miss:
        print(f"  │  Events missing ({len(miss):2d}): {', '.join(miss)}")
    print(f"  │  Anchors: {summary['n_anchors']}  Teams: {summary['n_teams']}  Flags: {summary['n_flags']}")
    print(f"  └{'─'*55}")


# ── Main batch runner ─────────────────────────────────────────────────────────

def run(input_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(input_dir.glob("*.pdf")) + sorted(input_dir.glob("*.PDF"))
    if not pdfs:
        print(f"\n  No PDFs found in {input_dir}/")
        print("  Drop your championship result PDFs into that folder and run again.\n")
        return

    conf_map = load_conference_map()
    bundles  = group_into_bundles(pdfs, conf_map)

    total_pdfs = sum(len(b["paths"]) for b in bundles.values())

    print(f"\n{'='*64}")
    print(f"  Lane4 Data Harvester v1")
    print(f"{'='*64}")
    print(f"  Input:   {input_dir}/  ({total_pdfs} PDF{'s' if total_pdfs != 1 else ''})")
    print(f"  Bundles: {len(bundles)}")
    print(f"  Output:  {output_dir}/")
    print(f"{'='*64}\n")

    # Print bundle plan before processing
    for bid, b in sorted(bundles.items()):
        file_names = [p.name for p, _ in b["paths"]]
        print(f"  Bundle [{bid}]  ({len(file_names)} file{'s' if len(file_names) != 1 else ''})")
        for fn in file_names:
            print(f"    · {fn}")
    print()

    all_events:    list[dict] = []
    all_teams:     list[dict] = []
    all_flags:     list[dict] = []
    all_summaries: list[dict] = []

    for bid, bundle in sorted(bundles.items()):
        print(f"  Processing bundle: {bid}")
        file_results = []

        for path, meta in bundle["paths"]:
            session_label = (
                "prelims" if meta["is_prelim"] else
                "finals"  if meta["is_final"]  else "?"
            )
            print(f"    [{meta.get('gender','?'):8s}|{session_label:7s}] {path.name} ...",
                  end=" ", flush=True)
            try:
                evs, tms, fls = parse_pdf_raw(path, meta, bundle)
                file_results.append((evs, tms, fls))
                n_flags = len(fls)
                print(
                    f"{len(evs)} event(s), {len(tms)} team row(s)"
                    + (f", {n_flags} flag(s)" if n_flags else "")
                )
            except Exception as exc:
                print(f"FAILED — {exc}")
                if os.environ.get("HARVESTER_DEBUG"):
                    traceback.print_exc()
                all_flags.append({
                    "Source_File": path.name,
                    "Bundle_ID":   bid,
                    "Conference":  bundle["conference"],
                    "Year":        bundle["year"],
                    "Gender":      bundle["gender"],
                    "Event":       "",
                    "Issue":       f"Unhandled error: {exc}",
                })

        if not file_results:
            print(f"    → No results to merge for this bundle.\n")
            continue

        ev_rows, tm_rows, fl_rows, summary = merge_bundle(bundle, file_results)
        all_events.extend(ev_rows)
        all_teams.extend(tm_rows)
        all_flags.extend(fl_rows)
        all_summaries.append(summary)

        print_bundle_summary(summary)

    write_csv(output_dir / "event_anchors.csv", ANCHOR_HEADER, all_events)
    write_csv(output_dir / "team_scores.csv",   TEAM_HEADER,   all_teams)
    write_csv(output_dir / "review_flags.csv",  FLAG_HEADER,   all_flags)

    print(f"\n{'='*64}")
    print(f"  Done.")
    print(f"  PDFs processed:        {total_pdfs}")
    print(f"  Bundles processed:     {len(all_summaries)}")
    print(f"  Event anchors output:  {len(all_events)}")
    print(f"  Team rows output:      {len(all_teams)}")
    print(f"  Flags generated:       {len(all_flags)}")
    if all_summaries:
        total_missing = sum(len(s["events_missing"]) for s in all_summaries)
        if total_missing:
            print(f"  Events still missing:  {total_missing} across {len(all_summaries)} bundles")
    print(f"\n  Outputs:")
    print(f"    {output_dir}/event_anchors.csv")
    print(f"    {output_dir}/team_scores.csv")
    print(f"    {output_dir}/review_flags.csv")
    print(f"{'='*64}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lane4 Data Harvester v1 — bundle-aware NCAA championship PDF parser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python harvester.py
  python harvester.py --input my_pdfs/ --output results/
  HARVESTER_DEBUG=1 python harvester.py     (verbose error traces)

Bundling:
  Files are grouped by (conference, year, gender).
  Example group:
    acc_2026_men_day1.pdf  ─┐
    acc_2026_men_day2.pdf   ├─ bundle: acc_2026_men
    acc_2026_men_day3.pdf  ─┘
        """,
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=Path("input_pdfs"),
        help="Folder containing championship PDFs (default: input_pdfs/)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=Path("output"),
        help="Folder for CSV outputs (default: output/)",
    )
    args = parser.parse_args()
    run(args.input, args.output)


if __name__ == "__main__":
    main()
