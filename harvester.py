#!/usr/bin/env python3
"""
Lane4 Data Harvester v2
-----------------------
Gender-aware batch PDF parser for NCAA swim conference championship results.

Usage:
    python harvester.py [--input DIR] [--output DIR] [--bundles B1,B2,...]

Outputs (in output/):
    event_anchors.csv         — 1st/8th/16th anchor times per event per gender per bundle
    team_scores.csv           — Team standings
    review_flags.csv          — Issues needing human review
    debug_bundle_report.csv   — Per-event-header detail for every file processed
    debug_bundle_summary.csv  — Per-bundle summary

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
    is_event_header_any,
    is_time_plausible,
    load_conference_map,
    looks_like_psych_sheet,
    normalize_event_name_any,
    parse_filename_metadata,
    parse_place_and_time,
    parse_team_scores,
    detect_session_type_from_text,
    classify_file_gender,
    time_to_seconds,
    seconds_to_time,
    _MEN_SECTION_RE,
    _WOMEN_SECTION_RE,
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

DEBUG_REPORT_HEADER = [
    "Bundle_ID", "Conference", "Source_File", "File_Gender_Type",
    "Men_Section_Found", "Women_Section_Found",
    "Raw_Event_Header", "Canonical_Event", "Section_Type",
    "Places_Found", "Status", "Reason",
]

DEBUG_SUMMARY_HEADER = [
    "Bundle_ID", "Conference", "Files_In_Bundle", "Detected_Gender",
    "Events_Detected", "Events_Mapped", "Events_Output", "Events_Missing",
    "Missing_Event_List", "Team_Scores_Found", "Notes",
]

ANCHOR_PLACES = [1, 8, 16]

BOTH_GENDERS = ("men", "women")


# ── Event result accumulator ──────────────────────────────────────────────────

class EventAccumulator:
    """Collects (place → seconds) for one event from one PDF, one gender."""

    def __init__(
        self,
        name: str,
        source: str,
        session: str,
        is_prelim: bool,
        gender: str = "men",
    ):
        self.name      = name
        self.source    = source
        self.session   = session
        self.is_prelim = is_prelim
        self.gender    = gender  # 'men' or 'women'

        # session_score: 3 = confirmed finals, 1 = unknown, 0 = prelims
        if is_prelim or session == "prelims":
            self.session_score = 0
        elif session == "finals":
            self.session_score = 3
        else:
            self.session_score = 1  # unknown → assume finals but rank lower

        self.places: dict[int, float] = {}

    def add(self, place: int, time_str: str):
        sec = time_to_seconds(time_str)
        if sec is not None and place not in self.places:
            self.places[place] = sec

    def completeness(self) -> int:
        return sum(1 for p in ANCHOR_PLACES if p in self.places)

    def is_finals(self) -> bool:
        return self.session_score > 0

    def anchor(self) -> dict | None:
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


# ── Single-PDF parser (gender-aware) ─────────────────────────────────────────

def parse_pdf_raw(
    pdf_path: Path,
    meta: dict,
    bundle: dict,
) -> tuple[
    dict[str, EventAccumulator],   # men_events
    dict[str, EventAccumulator],   # women_events
    list[dict],                    # team_rows
    list[dict],                    # flag_rows
    list[dict],                    # debug_rows (per-event-header)
    str,                           # file_gender_type
]:
    """
    Parse one PDF, returning separate men's and women's event accumulators.

    File gender classification:
      men      — process men's events only (skip women's section)
      women    — process women's events only (skip men's section)
      combined — parse both sections, route events to correct gender bucket
      unknown  — treat as combined
    """
    source     = pdf_path.name
    conference = bundle["conference"]
    year       = bundle["year"]
    bundle_id  = bundle["bundle_id"]

    men_events:   dict[str, EventAccumulator] = {}
    women_events: dict[str, EventAccumulator] = {}
    flag_rows:    list[dict] = []
    debug_rows:   list[dict] = []

    def flag(gender: str, event: str, issue: str) -> dict:
        return {
            "Source_File": source,
            "Bundle_ID":   bundle_id,
            "Conference":  conference,
            "Year":        year,
            "Gender":      gender,
            "Event":       event,
            "Issue":       issue,
        }

    def debug_row(
        file_gender_type: str,
        men_found: bool,
        women_found: bool,
        raw_header: str,
        canonical: str | None,
        section: str,
        places: int,
        status: str,
        reason: str,
    ) -> dict:
        return {
            "Bundle_ID":          bundle_id,
            "Conference":         conference,
            "Source_File":        source,
            "File_Gender_Type":   file_gender_type,
            "Men_Section_Found":  men_found,
            "Women_Section_Found": women_found,
            "Raw_Event_Header":   raw_header,
            "Canonical_Event":    canonical or "",
            "Section_Type":       section,
            "Places_Found":       places,
            "Status":             status,
            "Reason":             reason,
        }

    # ── Extract pages ─────────────────────────────────────────────────────────
    try:
        pages = extract_pages(str(pdf_path))
    except Exception as exc:
        flag_rows.append(flag("", "", f"PDF extraction failed: {exc}"))
        return men_events, women_events, [], flag_rows, debug_rows, "error"

    # Refine conference from PDF text if still Unknown
    if conference == "Unknown":
        title_text = " ".join(pages[:3])[:3000]
        resolved = detect_conference(source, title_text)
        if resolved != "Unknown":
            bundle["conference"] = resolved
            conference = resolved
            flag_rows.append(flag("", "", f"Conference guessed from PDF text: {resolved}"))

    # ── Psych/heat sheet check ────────────────────────────────────────────────
    if looks_like_psych_sheet(pages):
        flag_rows.append(flag("", "", "PDF looks like psych/heat sheet — skipped"))
        return men_events, women_events, [], flag_rows, debug_rows, "psych"

    # ── File gender classification ────────────────────────────────────────────
    file_gender_type = classify_file_gender(meta, pages)

    if file_gender_type == "women":
        flag_rows.append(flag("women", "", "Women-only file — men's anchors skipped; extracting women's events"))
    elif file_gender_type == "men":
        pass  # normal
    else:  # combined or unknown
        flag_rows.append(flag("combined", "", f"Combined/unknown file — parsing both sections ({file_gender_type})"))

    # ── Session type ──────────────────────────────────────────────────────────
    if meta["is_prelim"]:
        session = "prelims"
    elif meta["is_final"]:
        session = "finals"
    else:
        session = detect_session_type_from_text(pages)

    # ── Build flat line list ──────────────────────────────────────────────────
    all_lines: list[str] = []
    for page in pages:
        all_lines.extend(page.splitlines())
        all_lines.append("<<<PAGE_BREAK>>>")

    # ── State machine ─────────────────────────────────────────────────────────
    # current_section: which gender we're currently in
    if file_gender_type == "men":
        current_section = "men"
    elif file_gender_type == "women":
        current_section = "women"
    else:
        current_section = "unknown"

    current_event: EventAccumulator | None = None  # the active accumulator
    men_finalized:   dict[str, EventAccumulator] = {}
    women_finalized: dict[str, EventAccumulator] = {}
    men_section_found   = False
    women_section_found = False
    unrecognized_headers: list[str] = []

    def finalize():
        nonlocal current_event
        if current_event and current_event.places:
            target = men_finalized if current_event.gender == "men" else women_finalized
            existing = target.get(current_event.name)
            if existing is None:
                target[current_event.name] = current_event
            elif current_event.completeness() > existing.completeness():
                target[current_event.name] = current_event
        current_event = None

    for raw_line in all_lines:
        line = raw_line.strip()

        # ── Section boundary detection ────────────────────────────────────────
        if _MEN_SECTION_RE.search(line):
            finalize()
            if file_gender_type != "women":
                current_section = "men"
                men_section_found = True
            continue

        if _WOMEN_SECTION_RE.search(line):
            finalize()
            if file_gender_type != "men":
                current_section = "women"
                women_section_found = True
            continue

        # ── Skip wrong-gender sections ────────────────────────────────────────
        if file_gender_type == "men" and current_section == "women":
            continue
        if file_gender_type == "women" and current_section == "men":
            continue

        # ── Event header detection ─────────────────────────────────────────────
        if is_event_header_any(line):
            canonical, ev_gender = normalize_event_name_any(line)

            if canonical and canonical in TARGET_EVENTS:
                # Resolve gender: explicit label > current_section > file type
                if ev_gender in ("men", "women"):
                    resolved_gender = ev_gender
                    # Update section tracking
                    if ev_gender == "men":
                        men_section_found = True
                        if file_gender_type != "women":
                            current_section = "men"
                    else:
                        women_section_found = True
                        if file_gender_type != "men":
                            current_section = "women"
                elif current_section in ("men", "women"):
                    resolved_gender = current_section
                else:
                    # bare header in unknown context — skip (can't assign gender)
                    resolved_gender = None

                finalize()

                if resolved_gender in ("men", "women"):
                    # Only collect if file_gender_type allows this gender
                    if (
                        file_gender_type == "men"     and resolved_gender == "men"
                        or file_gender_type == "women"   and resolved_gender == "women"
                        or file_gender_type in ("combined", "unknown")
                    ):
                        current_event = EventAccumulator(
                            canonical, source, session, meta["is_prelim"], resolved_gender
                        )
                        debug_rows.append(debug_row(
                            file_gender_type,
                            men_section_found, women_section_found,
                            line, canonical, resolved_gender,
                            0, "recognized", f"session={session}",
                        ))
            else:
                # Event header shape but unrecognized — close current to stop bleed
                finalize()
                if line not in unrecognized_headers:
                    unrecognized_headers.append(line)
                debug_rows.append(debug_row(
                    file_gender_type,
                    men_section_found, women_section_found,
                    line, None, current_section,
                    0, "unrecognized", "normalize_event_name_any returned None",
                ))
            continue

        # ── Result row ────────────────────────────────────────────────────────
        if current_event:
            place, time_str = parse_place_and_time(line)
            if place is not None:
                current_event.add(place, time_str)

    finalize()

    # Update debug rows with final place counts
    # (we log them at header-detection time, so places_found is 0 there —
    # update after finalize for recognized events)
    for dr in debug_rows:
        if dr["Status"] == "recognized" and dr["Canonical_Event"]:
            ev_name = dr["Canonical_Event"]
            target  = men_finalized if dr["Section_Type"] == "men" else women_finalized
            acc = target.get(ev_name)
            if acc and acc.source == source:
                dr["Places_Found"] = len(acc.places)

    # Flag unrecognized event headers (capped at 5 to avoid log spam)
    for h in unrecognized_headers[:5]:
        flag_rows.append(flag("", "", f"Event header variation not normalized: {h!r}"))

    if not men_finalized and file_gender_type != "women":
        flag_rows.append(flag("men", "", "No men's target events found in PDF"))
    if not women_finalized and file_gender_type != "men":
        flag_rows.append(flag("women", "", "No women's target events found in PDF"))

    # ── Team scores ───────────────────────────────────────────────────────────
    team_rows: list[dict] = []
    if file_gender_type != "women":
        team_rows.extend(parse_team_scores(pages, conference, source, "men"))
    if file_gender_type != "men":
        team_rows.extend(parse_team_scores(pages, conference, source, "women"))

    return men_finalized, women_finalized, team_rows, flag_rows, debug_rows, file_gender_type


# ── Bundle merge ──────────────────────────────────────────────────────────────

def _pick_best_acc(
    event_name: str,
    accs: list[EventAccumulator],
    gender: str,
    flag_fn,
    flag_rows: list[dict],
) -> EventAccumulator | None:
    """
    From a list of EventAccumulators for one event/gender, select the best.

    Priority: session_score desc, completeness desc, total places desc.
    Sanity-checks the winner's 1st-place time.
    Returns None if no accumulator has any places.
    """
    if not accs:
        return None

    accs_sorted = sorted(
        accs,
        key=lambda a: (a.session_score, a.completeness(), len(a.places)),
        reverse=True,
    )
    best = accs_sorted[0]

    # Flag: only prelims found
    if all(a.session_score == 0 for a in accs):
        flag_rows.append(flag_fn(gender, event_name, best.source,
            "Only prelim results found — no finals detected"))

    # Flag: multiple conflicting finals
    finals_accs = [a for a in accs if a.session_score >= 2]
    if len(finals_accs) > 1:
        times_1st = {round(a.places[1], 2) for a in finals_accs if 1 in a.places}
        if len(times_1st) > 1:
            src = ", ".join(a.source for a in finals_accs)
            flag_rows.append(flag_fn(gender, event_name, src,
                "Multiple conflicting finals versions — 1st-place times: "
                + ", ".join(seconds_to_time(t) for t in sorted(times_1st))))

    # Sanity check 1st-place time
    if best.anchor() and 1 in best.places:
        if not is_time_plausible(event_name, best.places[1], gender):
            fallback = None
            for alt in accs_sorted[1:]:
                if 1 in alt.places and is_time_plausible(event_name, alt.places[1], gender):
                    if alt.anchor():
                        fallback = alt
                        break
            if fallback:
                flag_rows.append(flag_fn(gender, event_name, best.source,
                    f"Implausible 1st-place time {seconds_to_time(best.places[1])} "
                    f"— using fallback from {fallback.source}"))
                best = fallback
            else:
                flag_rows.append(flag_fn(gender, event_name, best.source,
                    f"Implausible 1st-place time {seconds_to_time(best.places[1])} "
                    f"— no better candidate, kept"))

    return best


def merge_bundle(
    bundle: dict,
    file_results: list[tuple],
) -> tuple[list[dict], list[dict], list[dict], list[dict], dict]:
    """
    Merge raw results from all files in a bundle.

    Returns (event_rows, team_rows, flag_rows, debug_rows, summary_dict).
    """
    conference = bundle["conference"]
    year       = bundle["year"]
    bundle_id  = bundle["bundle_id"]

    def flag_fn(gender: str, event: str, source: str, issue: str) -> dict:
        return {
            "Source_File": source,
            "Bundle_ID":   bundle_id,
            "Conference":  conference,
            "Year":        year,
            "Gender":      gender,
            "Event":       event,
            "Issue":       issue,
        }

    # Collect per-gender accumulators
    gender_accs: dict[str, dict[str, list[EventAccumulator]]] = {
        "men":   {},
        "women": {},
    }
    all_team_rows: list[dict] = []
    all_flag_rows: list[dict] = []
    all_debug_rows: list[dict] = []
    file_gender_types: list[str] = []

    for men_evs, women_evs, tms, fls, drs, fg_type in file_results:
        all_flag_rows.extend(fls)
        all_debug_rows.extend(drs)
        file_gender_types.append(fg_type)
        for t in tms:
            all_team_rows.append({
                **t,
                "Year":      year,
                "Bundle_ID": bundle_id,
            })
        for name, acc in men_evs.items():
            gender_accs["men"].setdefault(name, []).append(acc)
        for name, acc in women_evs.items():
            gender_accs["women"].setdefault(name, []).append(acc)

    # Deduplicate team rows
    seen_teams: set[str] = set()
    unique_teams: list[dict] = []
    for row in all_team_rows:
        key = f"{row['Team'].lower()}|{row.get('Gender','')}"
        if key not in seen_teams:
            seen_teams.add(key)
            unique_teams.append(row)

    if not unique_teams:
        sources = ", ".join(str(p.name) for p, _ in bundle["paths"])
        all_flag_rows.append(flag_fn("", "", sources, "Team scores not found in any bundle file"))

    # Per-event, per-gender merging
    event_rows: list[dict] = []
    events_found:   list[str] = []
    events_missing: list[str] = []
    events_detected: set[str] = set()

    # Track which genders have data in this bundle
    bundle_genders = set()
    for g in ("men", "women"):
        if gender_accs[g]:
            bundle_genders.add(g)
    if not bundle_genders:
        bundle_genders = {"men"}  # default fallback for reporting

    for gender in sorted(bundle_genders):
        gender_events_found   = []
        gender_events_missing = []

        for event_name in TARGET_EVENTS:
            accs = gender_accs[gender].get(event_name)
            if not accs:
                gender_events_missing.append(f"{gender}:{event_name}")
                continue

            events_detected.add(event_name)
            best = _pick_best_acc(event_name, accs, gender, flag_fn, all_flag_rows)
            if best is None:
                gender_events_missing.append(f"{gender}:{event_name}")
                continue

            anchor = best.anchor()
            if anchor:
                gender_events_found.append(event_name)
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
                all_flag_rows.append(flag_fn(
                    gender, event_name, best.source,
                    f"Event missing from final merged bundle — {miss_str} place not found",
                ))
                gender_events_missing.append(f"{gender}:{event_name}")

        events_found.extend(gender_events_found)
        events_missing.extend(gender_events_missing)

    detected_gender = "/".join(sorted(bundle_genders)) if bundle_genders else "unknown"
    notes_parts = []
    if "unknown" in file_gender_types:
        notes_parts.append("some files had unknown gender")
    if "psych" in file_gender_types or "error" in file_gender_types:
        notes_parts.append("some files skipped (psych/error)")

    summary = {
        "bundle_id":        bundle_id,
        "conference":       conference,
        "year":             year,
        "detected_gender":  detected_gender,
        "files":            [str(p.name) for p, _ in bundle["paths"]],
        "file_gender_types": file_gender_types,
        "events_detected":  len(events_detected),
        "events_mapped":    len(events_found),
        "events_output":    len(event_rows),
        "events_missing_list": events_missing,
        "n_teams":          len(unique_teams),
        "n_flags":          len(all_flag_rows),
        "notes":            "; ".join(notes_parts) if notes_parts else "",
    }

    return event_rows, unique_teams, all_flag_rows, all_debug_rows, summary


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
    gend  = summary["detected_gender"]
    files = summary["files"]
    miss  = summary["events_missing_list"]

    print(f"\n  ┌─ Bundle: {bid}")
    print(f"  │  Conference : {conf}  |  Gender(s): {gend}")
    print(f"  │  Files ({len(files)}): " + ", ".join(files[:3])
          + (" ..." if len(files) > 3 else ""))
    print(f"  │  Anchors out: {summary['events_output']:2d}  "
          f"Teams: {summary['n_teams']:2d}  Flags: {summary['n_flags']}")
    if miss:
        print(f"  │  Missing ({len(miss)}): {', '.join(miss[:6])}"
              + (" ..." if len(miss) > 6 else ""))
    if summary["notes"]:
        print(f"  │  Notes: {summary['notes']}")
    print(f"  └{'─'*55}")


# ── Main batch runner ─────────────────────────────────────────────────────────

def run(input_dir: Path, output_dir: Path, bundle_filter: list[str] | None = None):
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(input_dir.glob("*.pdf")) + sorted(input_dir.glob("*.PDF"))
    if not pdfs:
        print(f"\n  No PDFs found in {input_dir}/")
        print("  Drop your championship result PDFs into that folder and run again.\n")
        return

    conf_map = load_conference_map()
    bundles  = group_into_bundles(pdfs, conf_map)

    # Apply bundle filter if requested
    if bundle_filter:
        filtered = {}
        for bid, b in bundles.items():
            if any(f.lower() in bid.lower() for f in bundle_filter):
                filtered[bid] = b
        if not filtered:
            # Try prefix/partial match
            for bid, b in bundles.items():
                for f in bundle_filter:
                    if bid.lower().startswith(f.lower()):
                        filtered[bid] = b
        bundles = filtered
        print(f"\n  [TEST MODE] Running {len(bundles)} bundle(s): {', '.join(bundles.keys())}")

    total_pdfs = sum(len(b["paths"]) for b in bundles.values())

    print(f"\n{'='*64}")
    print(f"  Lane4 Data Harvester v2  (gender-aware)")
    print(f"{'='*64}")
    print(f"  Input:   {input_dir}/  ({total_pdfs} PDF{'s' if total_pdfs != 1 else ''})")
    print(f"  Bundles: {len(bundles)}")
    print(f"  Output:  {output_dir}/")
    print(f"{'='*64}\n")

    for bid, b in sorted(bundles.items()):
        file_names = [p.name for p, _ in b["paths"]]
        print(f"  Bundle [{bid}]  ({len(file_names)} file{'s' if len(file_names) != 1 else ''})")
        for fn in file_names:
            print(f"    · {fn}")
    print()

    all_events:   list[dict] = []
    all_teams:    list[dict] = []
    all_flags:    list[dict] = []
    all_debug_r:  list[dict] = []
    all_debug_s:  list[dict] = []
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
                men_evs, women_evs, tms, fls, drs, fg_type = parse_pdf_raw(path, meta, bundle)
                file_results.append((men_evs, women_evs, tms, fls, drs, fg_type))
                print(
                    f"men:{len(men_evs)} women:{len(women_evs)} events  "
                    f"teams:{len(tms)}  flags:{len(fls)}  [{fg_type}]"
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
                    "Gender":      "",
                    "Event":       "",
                    "Issue":       f"Unhandled error: {exc}",
                })

        if not file_results:
            print(f"    → No results to merge.\n")
            continue

        ev_rows, tm_rows, fl_rows, dr_rows, summary = merge_bundle(bundle, file_results)
        all_events.extend(ev_rows)
        all_teams.extend(tm_rows)
        all_flags.extend(fl_rows)
        all_debug_r.extend(dr_rows)

        # Build debug summary row
        all_debug_s.append({
            "Bundle_ID":          summary["bundle_id"],
            "Conference":         summary["conference"],
            "Files_In_Bundle":    len(summary["files"]),
            "Detected_Gender":    summary["detected_gender"],
            "Events_Detected":    summary["events_detected"],
            "Events_Mapped":      summary["events_mapped"],
            "Events_Output":      summary["events_output"],
            "Events_Missing":     len(summary["events_missing_list"]),
            "Missing_Event_List": ", ".join(summary["events_missing_list"]),
            "Team_Scores_Found":  summary["n_teams"],
            "Notes":              summary["notes"],
        })

        all_summaries.append(summary)
        print_bundle_summary(summary)

    write_csv(output_dir / "event_anchors.csv",        ANCHOR_HEADER,       all_events)
    write_csv(output_dir / "team_scores.csv",          TEAM_HEADER,         all_teams)
    write_csv(output_dir / "review_flags.csv",         FLAG_HEADER,         all_flags)
    write_csv(output_dir / "debug_bundle_report.csv",  DEBUG_REPORT_HEADER, all_debug_r)
    write_csv(output_dir / "debug_bundle_summary.csv", DEBUG_SUMMARY_HEADER, all_debug_s)

    total_anchors = len(all_events)
    total_missing = sum(len(s["events_missing_list"]) for s in all_summaries)

    print(f"\n{'='*64}")
    print(f"  Done.")
    print(f"  PDFs processed:        {total_pdfs}")
    print(f"  Bundles processed:     {len(all_summaries)}")
    print(f"  Event anchors output:  {total_anchors}  "
          f"(men: {sum(1 for r in all_events if r['Gender']=='men')}, "
          f"women: {sum(1 for r in all_events if r['Gender']=='women')})")
    print(f"  Team rows output:      {len(all_teams)}")
    print(f"  Flags generated:       {len(all_flags)}")
    if total_missing:
        print(f"  Events still missing:  {total_missing} across {len(all_summaries)} bundle(s)")
    print(f"\n  Outputs:")
    print(f"    {output_dir}/event_anchors.csv")
    print(f"    {output_dir}/team_scores.csv")
    print(f"    {output_dir}/review_flags.csv")
    print(f"    {output_dir}/debug_bundle_report.csv")
    print(f"    {output_dir}/debug_bundle_summary.csv")
    print(f"{'='*64}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lane4 Data Harvester v2 — gender-aware NCAA championship PDF parser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python harvester.py
  python harvester.py --input my_pdfs/ --output results/
  python harvester.py --bundles acc_2026,odac_2026   (test specific bundles)
  HARVESTER_DEBUG=1 python harvester.py
        """,
    )
    parser.add_argument("--input",  "-i", type=Path, default=Path("input_pdfs"),
                        help="Folder with PDFs (default: input_pdfs/)")
    parser.add_argument("--output", "-o", type=Path, default=Path("output"),
                        help="Folder for CSV outputs (default: output/)")
    parser.add_argument("--bundles", "-b", type=str, default=None,
                        help="Comma-separated bundle ID substrings to process (test mode)")
    args = parser.parse_args()

    bundle_filter = [x.strip() for x in args.bundles.split(",")] if args.bundles else None
    run(args.input, args.output, bundle_filter)


if __name__ == "__main__":
    main()
