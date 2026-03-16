#!/usr/bin/env python3
"""
Lane4 Data Harvester v1
-----------------------
Batch PDF parser for NCAA swim conference championship results.

Usage:
    python harvester.py [--input INPUT_DIR] [--output OUTPUT_DIR]

Drops PDFs in input_pdfs/, run this script, get:
    output/event_anchors.csv
    output/team_scores.csv
    output/review_flags.csv
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
    extract_event_name_from_header,
    extract_pages,
    looks_like_psych_sheet,
    normalize_event_name,
    parse_place_and_time,
    parse_team_scores,
    time_to_seconds,
    seconds_to_time,
    _RELAY_KEYWORDS,
    _DIVING_KEYWORDS,
    _MEN_EVENT_RE,
    _ALT_EVENT_RE,
)

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_INPUT  = Path("input_pdfs")
DEFAULT_OUTPUT = Path("output")

ANCHOR_PLACES = [1, 8, 16]   # which places to extract for sec_per_place

ANCHOR_HEADER = [
    "Conference", "Event",
    "1st", "8th", "16th",
    "1st_seconds", "8th_seconds", "16th_seconds",
    "Sec_per_place", "Source_File",
]

TEAM_HEADER = ["Conference", "Team", "Team_Score", "Source_File"]

FLAG_HEADER = ["Source_File", "Conference", "Event", "Issue"]


# ── Event result accumulator ──────────────────────────────────────────────────

class EventAccumulator:
    """Collects (place, time) pairs for a single event."""

    def __init__(self, name: str):
        self.name   = name
        self.places: dict[int, float] = {}   # place → seconds

    def add(self, place: int, time_str: str):
        sec = time_to_seconds(time_str)
        if sec is not None and place not in self.places:
            self.places[place] = sec

    def anchor(self) -> dict | None:
        """Return anchor data dict if we have all 3 places, else None."""
        if all(p in self.places for p in ANCHOR_PLACES):
            t1  = self.places[1]
            t8  = self.places[8]
            t16 = self.places[16]
            spp = round((t16 - t1) / 15, 4) if t16 != t1 else 0.0
            return {
                "1st":          seconds_to_time(t1),
                "8th":          seconds_to_time(t8),
                "16th":         seconds_to_time(t16),
                "1st_seconds":  round(t1, 3),
                "8th_seconds":  round(t8, 3),
                "16th_seconds": round(t16, 3),
                "Sec_per_place": spp,
            }
        return None

    def missing_places(self) -> list[int]:
        return [p for p in ANCHOR_PLACES if p not in self.places]


# ── Core PDF parser ───────────────────────────────────────────────────────────

def is_men_event_header(line: str) -> bool:
    """Return True if line looks like a men's individual event header."""
    if _RELAY_KEYWORDS.search(line) or _DIVING_KEYWORDS.search(line):
        return False
    if re.search(r"\bwomen'?s?\b", line, re.IGNORECASE):
        return False
    # Must mention "men" or match a bare distance+stroke format after context
    return bool(_MEN_EVENT_RE.search(line)) or bool(_ALT_EVENT_RE.search(line))


def parse_pdf(pdf_path: Path, conference: str) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Parse one PDF and return (event_rows, team_rows, flag_rows).

    Strategy:
    1. Extract all text pages.
    2. Detect if it looks like a psych sheet → flag and skip.
    3. Scan line-by-line for men's event headers.
    4. For each event, collect place/time rows until the next event starts.
    5. Compute anchor data (1st, 8th, 16th) and sec_per_place.
    6. Attempt team score extraction.
    """
    source = pdf_path.name
    event_rows: list[dict] = []
    flag_rows:  list[dict] = []

    # ── Extract pages ─────────────────────────────────────────────────────────
    try:
        pages = extract_pages(str(pdf_path))
    except Exception as exc:
        flag_rows.append({
            "Source_File": source,
            "Conference":  conference,
            "Event":       "",
            "Issue":       f"PDF extraction failed: {exc}",
        })
        return event_rows, [], flag_rows

    # Title text for conference detection fallback
    title_text = " ".join(pages[:3])[:3000]

    # Resolve conference if still unknown
    if conference == "Unknown":
        conference = detect_conference(source, title_text)

    # ── Psych sheet check ─────────────────────────────────────────────────────
    if looks_like_psych_sheet(pages):
        flag_rows.append({
            "Source_File": source,
            "Conference":  conference,
            "Event":       "",
            "Issue":       "PDF looks like psych sheet — no final results extracted",
        })
        return event_rows, [], flag_rows

    # ── Collect all lines with page breaks preserved ──────────────────────────
    all_lines: list[str] = []
    for page in pages:
        all_lines.extend(page.splitlines())
        all_lines.append("<<<PAGE_BREAK>>>")

    # ── State machine ─────────────────────────────────────────────────────────
    current_event: EventAccumulator | None = None
    finalized: dict[str, EventAccumulator] = {}   # event_name → accumulator
    in_women_section = False

    # Track whether the PDF has any women's/combined header so we know it
    # needs section-aware parsing
    has_section_headers = any(
        re.search(r"\bwomen'?s?\s+(swimming|results|championship)", ln, re.IGNORECASE)
        for ln in all_lines
    )

    def finalize_event():
        nonlocal current_event
        if current_event and current_event.places:
            existing = finalized.get(current_event.name)
            if existing is None:
                finalized[current_event.name] = current_event
            else:
                # Prefer the accumulator that already has more places
                if len(current_event.places) > len(existing.places):
                    finalized[current_event.name] = current_event
        current_event = None

    for raw_line in all_lines:
        line = raw_line.strip()

        # ── Section detection ─────────────────────────────────────────────────
        if has_section_headers:
            if re.search(r"\bwomen'?s?\s+(swimming|results|championship|individual)", line, re.IGNORECASE):
                finalize_event()
                in_women_section = True
                continue
            if re.search(r"\bmen'?s?\s+(swimming|results|championship|individual)", line, re.IGNORECASE):
                finalize_event()
                in_women_section = False
                continue

        if in_women_section:
            continue

        # ── Event header detection ────────────────────────────────────────────
        if is_men_event_header(line):
            event_name = normalize_event_name(line)
            if event_name and event_name in TARGET_EVENTS:
                finalize_event()
                current_event = EventAccumulator(event_name)
                continue
            elif event_name is None and is_men_event_header(line):
                # Header detected but couldn't normalize — flag it
                # (only flag once, don't spam)
                pass

        # ── Result row parsing ────────────────────────────────────────────────
        if current_event:
            place, time_str = parse_place_and_time(line)
            if place is not None and time_str is not None:
                current_event.add(place, time_str)

    finalize_event()  # finish the last event

    # ── Build output rows ─────────────────────────────────────────────────────
    for event_name in TARGET_EVENTS:
        acc = finalized.get(event_name)
        if acc is None:
            # Event not found in this PDF — that's OK (not every conference
            # swims every event), no flag needed unless we expected it
            continue

        anchor = acc.anchor()
        if anchor:
            event_rows.append({
                "Conference":    conference,
                "Event":         event_name,
                "1st":           anchor["1st"],
                "8th":           anchor["8th"],
                "16th":          anchor["16th"],
                "1st_seconds":   anchor["1st_seconds"],
                "8th_seconds":   anchor["8th_seconds"],
                "16th_seconds":  anchor["16th_seconds"],
                "Sec_per_place": anchor["Sec_per_place"],
                "Source_File":   source,
            })
        else:
            missing = acc.missing_places()
            issue = "Missing " + ", ".join(
                {1: "1st", 8: "8th", 16: "16th"}[p] for p in missing
            ) + " place"
            flag_rows.append({
                "Source_File": source,
                "Conference":  conference,
                "Event":       event_name,
                "Issue":       issue,
            })

    if not finalized:
        flag_rows.append({
            "Source_File": source,
            "Conference":  conference,
            "Event":       "",
            "Issue":       "Could not determine men's section or no target events found",
        })

    # ── Team scores ───────────────────────────────────────────────────────────
    team_rows = parse_team_scores(pages, conference, source)
    if not team_rows:
        flag_rows.append({
            "Source_File": source,
            "Conference":  conference,
            "Event":       "",
            "Issue":       "No team scores found",
        })

    return event_rows, team_rows, flag_rows


# ── CSV writers ───────────────────────────────────────────────────────────────

def write_csv(path: Path, header: list[str], rows: list[dict]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Main batch runner ─────────────────────────────────────────────────────────

def run(input_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(input_dir.glob("*.pdf")) + sorted(input_dir.glob("*.PDF"))
    if not pdfs:
        print(f"\n  No PDFs found in {input_dir}/")
        print("  Drop your championship result PDFs into that folder and run again.\n")
        return

    print(f"\n{'='*60}")
    print(f"  Lane4 Data Harvester v1")
    print(f"{'='*60}")
    print(f"  Input:  {input_dir}/   ({len(pdfs)} PDF{'s' if len(pdfs) != 1 else ''} found)")
    print(f"  Output: {output_dir}/")
    print(f"{'='*60}\n")

    all_events: list[dict] = []
    all_teams:  list[dict] = []
    all_flags:  list[dict] = []

    for i, pdf_path in enumerate(pdfs, 1):
        print(f"  [{i}/{len(pdfs)}] {pdf_path.name} ...", end=" ", flush=True)
        try:
            conference = detect_conference(pdf_path.name)
            ev, tm, fl = parse_pdf(pdf_path, conference)
            all_events.extend(ev)
            all_teams.extend(tm)
            all_flags.extend(fl)

            # Per-file summary
            flags_for_file = len(fl)
            print(
                f"{len(ev)} event row{'s' if len(ev) != 1 else ''}, "
                f"{len(tm)} team row{'s' if len(tm) != 1 else ''}"
                + (f", {flags_for_file} flag{'s' if flags_for_file != 1 else ''}" if flags_for_file else "")
            )
        except Exception as exc:
            print(f"FAILED — {exc}")
            all_flags.append({
                "Source_File": pdf_path.name,
                "Conference":  "Unknown",
                "Event":       "",
                "Issue":       f"Unhandled error: {exc}",
            })
            if os.environ.get("HARVESTER_DEBUG"):
                traceback.print_exc()

    # Write outputs
    write_csv(output_dir / "event_anchors.csv", ANCHOR_HEADER, all_events)
    write_csv(output_dir / "team_scores.csv",   TEAM_HEADER,   all_teams)
    write_csv(output_dir / "review_flags.csv",  FLAG_HEADER,   all_flags)

    print(f"\n{'='*60}")
    print(f"  Done.")
    print(f"  PDFs processed:       {len(pdfs)}")
    print(f"  Event rows extracted: {len(all_events)}")
    print(f"  Team rows extracted:  {len(all_teams)}")
    print(f"  Flags generated:      {len(all_flags)}")
    print(f"\n  Outputs:")
    print(f"    {output_dir}/event_anchors.csv")
    print(f"    {output_dir}/team_scores.csv")
    print(f"    {output_dir}/review_flags.csv")
    print(f"{'='*60}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lane4 Data Harvester v1 — batch NCAA championship PDF parser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python harvester.py
  python harvester.py --input my_pdfs/ --output results/
  HARVESTER_DEBUG=1 python harvester.py   (verbose error traces)
        """,
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Folder containing championship PDFs (default: {DEFAULT_INPUT}/)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Folder for CSV outputs (default: {DEFAULT_OUTPUT}/)",
    )
    args = parser.parse_args()
    run(args.input, args.output)


if __name__ == "__main__":
    main()
