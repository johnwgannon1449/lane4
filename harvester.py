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
    detect_final_type,
    extract_pages,
    FINAL_PLACE_OFFSETS,
    group_into_bundles,
    is_event_header_any,
    is_time_plausible,
    load_conference_map,
    looks_like_psych_sheet,
    loose_event_match,
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
    "Final_Section", "Places_Found", "Status", "Reason", "Pass",
]

DEBUG_SUMMARY_HEADER = [
    "Bundle_ID", "Conference", "Files_In_Bundle", "Detected_Gender",
    "Events_Detected", "Events_Mapped", "Events_Output",
    "Men_Events_Output", "Women_Events_Output",
    "Events_Missing", "Men_Missing", "Women_Missing",
    "Events_Recovered_Pass2", "Missing_Event_List",
    "Team_Scores_Found", "Team_Score_Strategy", "Notes",
]

ANCHOR_PLACES = [1, 8, 16]

BOTH_GENDERS = ("men", "women")


# ── Event result accumulator ──────────────────────────────────────────────────

class EventAccumulator:
    """
    Collects (overall_place → seconds) for one event from one PDF, one gender.

    Places are stored by their **normalized overall place** (1-based across the
    entire field), not the displayed heat place.  The caller is responsible for
    applying the A/B/C-final offset before calling add().

    final_sections_seen tracks which heat types contributed data so we can
    distinguish "no B-final" (fewer_than_16_finalists) from genuine parse gaps.
    """

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

        # overall_place → seconds
        self.places: dict[int, float] = {}

        # Which final sections contributed to this accumulator
        self.final_sections_seen: set[str] = set()

    @property
    def has_b_final(self) -> bool:
        return "B" in self.final_sections_seen

    def add(self, place: int, time_str: str, final_type: str = "unknown"):
        """
        Add a result.  `place` must already be the normalized overall place
        (caller applied A/B/C offset).  `final_type` tracks heat origin.
        """
        sec = time_to_seconds(time_str)
        if sec is not None and place not in self.places:
            self.places[place] = sec
            if final_type:
                self.final_sections_seen.add(final_type)

    def merge_from(self, other: "EventAccumulator"):
        """
        Smart merge from another accumulator for the same event.

        Two cases:
        (a) Non-overlapping place ranges (B Final + A Final): merge all places,
            union section sets.  Overlap ≤ 3 is the threshold.
        (b) Substantially overlapping place ranges (Prelims vs Finals, or A-Final
            seen twice): keep the higher-quality data, i.e. prefer the accumulator
            with the better session_score, then completeness.
        """
        overlap = set(self.places) & set(other.places)
        if len(overlap) <= 3:
            # Non-overlapping sections → merge (B Final + A Final scenario)
            for p, t in other.places.items():
                if p not in self.places:
                    self.places[p] = t
            self.final_sections_seen |= other.final_sections_seen
            if other.session_score > self.session_score:
                self.session_score = other.session_score
        else:
            # Overlapping sections → keep the better-quality accumulator's data
            other_is_better = (
                other.session_score > self.session_score
                or (other.session_score == self.session_score
                    and other.completeness() > self.completeness())
            )
            if other_is_better:
                self.places = dict(other.places)
                self.final_sections_seen = set(other.final_sections_seen)
                self.session_score = other.session_score

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
        final_section: str,
        places: int,
        status: str,
        reason: str,
        pass_num: str = "first",
    ) -> dict:
        return {
            "Bundle_ID":           bundle_id,
            "Conference":          conference,
            "Source_File":         source,
            "File_Gender_Type":    file_gender_type,
            "Men_Section_Found":   men_found,
            "Women_Section_Found": women_found,
            "Raw_Event_Header":    raw_header,
            "Canonical_Event":     canonical or "",
            "Section_Type":        section,
            "Final_Section":       final_section,
            "Places_Found":        places,
            "Status":              status,
            "Reason":              reason,
            "Pass":                pass_num,
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

    # Current heat-section type: 'A', 'B', 'C', or 'unknown'.
    # Reset to 'unknown' at each new event header; updated by standalone labels.
    current_final_type: str = "unknown"

    def finalize():
        nonlocal current_event
        if current_event and current_event.places:
            target = men_finalized if current_event.gender == "men" else women_finalized
            existing = target.get(current_event.name)
            if existing is None:
                target[current_event.name] = current_event
            else:
                # Merge so B-final (places 9-16 after offset) and A-final
                # (places 1-8) accumulate in one object → complete 16-place anchor.
                existing.merge_from(current_event)
        current_event = None

    for raw_line in all_lines:
        line = raw_line.strip()

        # ── Section boundary detection ────────────────────────────────────────
        if _MEN_SECTION_RE.search(line):
            finalize()
            if file_gender_type != "women":
                current_section = "men"
                men_section_found = True
            current_final_type = "unknown"
            continue

        if _WOMEN_SECTION_RE.search(line):
            finalize()
            if file_gender_type != "men":
                current_section = "women"
                women_section_found = True
            current_final_type = "unknown"
            continue

        # ── Skip wrong-gender sections ────────────────────────────────────────
        if file_gender_type == "men" and current_section == "women":
            continue
        if file_gender_type == "women" and current_section == "men":
            continue

        # Compute once and reuse — is_event_header_any runs 3 regexes.
        _is_evt_hdr = is_event_header_any(line)

        # ── Standalone final-section label (e.g. "B Final", "A - Final") ──────
        # Only lines that are NOT event headers can be standalone final labels.
        # detect_final_type has its own "final" fast-path so this is cheap on
        # the vast majority of result rows.
        if not _is_evt_hdr:
            ft = detect_final_type(line)
            if ft:
                current_final_type = ft
                # Stay in the same event; the offset changes for upcoming rows.
                continue

        # ── Event header detection ─────────────────────────────────────────────
        if _is_evt_hdr:
            canonical, ev_gender = normalize_event_name_any(line)

            # Extract any final-type label embedded in the event header itself
            # e.g. "Event 26 Men 200 Yard Butterfly - B Final"
            ft_in_header = detect_final_type(line)

            if canonical and canonical in TARGET_EVENTS:
                # Resolve gender: explicit label > current_section > file type
                if ev_gender in ("men", "women"):
                    resolved_gender = ev_gender
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
                # Reset final type for the new event; use embedded label if present
                current_final_type = ft_in_header if ft_in_header else "unknown"

                if resolved_gender in ("men", "women"):
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
                            current_final_type,
                            0, "recognized", f"session={session}",
                        ))
            else:
                # Event header shape but unrecognized — close current to stop bleed
                finalize()
                current_final_type = ft_in_header if ft_in_header else "unknown"
                if line not in unrecognized_headers:
                    unrecognized_headers.append(line)
                debug_rows.append(debug_row(
                    file_gender_type,
                    men_section_found, women_section_found,
                    line, None, current_section,
                    current_final_type,
                    0, "unrecognized", "normalize_event_name_any returned None",
                ))
            continue

        # ── Result row ────────────────────────────────────────────────────────
        if current_event:
            place, time_str = parse_place_and_time(line)
            if place is not None:
                # Apply section offset ONLY if the displayed place looks like a
                # heat rank (1-8).  Some PDFs (e.g. ACC) show overall ranks
                # directly (B-Final: 9-16, C-Final: 17-24), so no offset is
                # needed.  Others (e.g. Big 12) show heat ranks 1-8 in every
                # section and need the offset to reach the overall rank.
                raw_offset = FINAL_PLACE_OFFSETS.get(current_final_type, 0)
                offset = raw_offset if (raw_offset > 0 and place <= 8) else 0
                current_event.add(place + offset, time_str, current_final_type)

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


# ── Second-pass event recovery helpers ───────────────────────────────────────

def _sort_paths_last_day_first(paths: list[tuple]) -> list[tuple]:
    """
    Sort (path, meta) pairs so the last day's file comes first.
    Uses day number in meta if present; falls back to reverse filename order.
    """
    def day_sort_key(path_meta):
        path, meta = path_meta
        day = meta.get("day") or ""
        m = re.search(r"(\d+)", str(day))
        if m:
            return -int(m.group(1))
        return path.name[::-1]  # reverse filename as tiebreaker

    return sorted(paths, key=day_sort_key)


def _second_pass_scan_file(
    pages: list[str],
    missing_events: list[str],
    gender: str,
    source: str,
    session: str,
    is_prelim: bool,
    bundle_id: str,
    conference: str,
    year: str,
) -> tuple[dict[str, EventAccumulator], list[dict]]:
    """
    Loose-scan one PDF's pages for ALL missing events in a single pass.

    Reads pages once and collects result rows for whichever target events appear.
    Returns ({event_name: EventAccumulator}, [debug_rows]).
    Only events with at least one result row are included in the output dict.
    """
    all_lines: list[str] = []
    for page in pages:
        all_lines.extend(page.splitlines())
        all_lines.append("<<<PAGE_BREAK>>>")

    # State per missing event: keep the best accumulator found so far
    found: dict[str, EventAccumulator] = {}
    match_lines: dict[str, str] = {}

    current_event: str | None = None    # which missing event we are collecting for
    current_acc: EventAccumulator | None = None
    current_final_type: str = "unknown"

    def finalize_current():
        nonlocal current_event, current_acc
        if current_event and current_acc and current_acc.places:
            prev = found.get(current_event)
            if prev is None:
                found[current_event] = current_acc
            else:
                # Merge so B/A-final sections accumulate into one object
                prev.merge_from(current_acc)
        current_event = None
        current_acc = None

    for raw_line in all_lines:
        line = raw_line.strip()

        if line == "<<<PAGE_BREAK>>>":
            if current_acc and not current_acc.places:
                # fruitless section — abandon and try again on next page
                current_event = None
                current_acc = None
                current_final_type = "unknown"
            continue

        if not line:
            continue

        # Compute once for this line — reused in standalone-label check and
        # the is_event_header guard inside the else branch below.
        _is_evt_hdr = is_event_header_any(line)

        # Standalone final-section label: update offset without closing accumulator
        if not _is_evt_hdr:
            ft = detect_final_type(line)
            if ft:
                current_final_type = ft
                continue

        # Check if this line loosely matches any of the missing events
        for ev in missing_events:
            if loose_event_match(line, ev, gender):
                finalize_current()
                # Extract embedded final type from the matching header
                ft_in_header = detect_final_type(line)
                current_final_type = ft_in_header if ft_in_header else "unknown"
                current_event = ev
                current_acc = EventAccumulator(ev, source, session, is_prelim, gender)
                match_lines[ev] = line
                break
        else:
            if current_acc:
                # Stop collecting if a different (first-pass) event header begins
                if _is_evt_hdr:
                    finalize_current()
                    current_final_type = "unknown"
                    continue
                place, time_str = parse_place_and_time(line)
                if place is not None:
                    raw_offset = FINAL_PLACE_OFFSETS.get(current_final_type, 0)
                    offset = raw_offset if (raw_offset > 0 and place <= 8) else 0
                    current_acc.add(place + offset, time_str, current_final_type)

    finalize_current()

    # Build debug rows for each recovered event
    debug_rows: list[dict] = []
    for ev, acc in found.items():
        sections_label = "/".join(sorted(acc.final_sections_seen)) if acc.final_sections_seen else "unknown"
        debug_rows.append({
            "Bundle_ID":           bundle_id,
            "Conference":          conference,
            "Source_File":         source,
            "File_Gender_Type":    "second_pass",
            "Men_Section_Found":   gender == "men",
            "Women_Section_Found": gender == "women",
            "Raw_Event_Header":    match_lines.get(ev, ""),
            "Canonical_Event":     ev,
            "Section_Type":        gender,
            "Final_Section":       sections_label,
            "Places_Found":        len(acc.places),
            "Status":              "recovered",
            "Reason":              f"loose_event_match pass2 session={session}",
            "Pass":                "second",
        })

    return found, debug_rows


def merge_bundle(
    bundle: dict,
    file_results: list[tuple],
) -> tuple[list[dict], list[dict], list[dict], list[dict], dict]:
    """
    Merge raw results from all files in a bundle.

    Phase 1: collect first-pass accumulators from parse_pdf_raw results.
    Phase 2: for events still missing, re-scan bundle PDFs with loose matching.
    Phase 3: extract team scores, prioritising the last-day file.

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

    # ── Phase 1: collect first-pass accumulators ──────────────────────────────
    gender_accs: dict[str, dict[str, list[EventAccumulator]]] = {
        "men":   {},
        "women": {},
    }
    all_flag_rows:  list[dict] = []
    all_debug_rows: list[dict] = []
    file_gender_types: list[str] = []

    for men_evs, women_evs, tms, fls, drs, fg_type in file_results:
        all_flag_rows.extend(fls)
        all_debug_rows.extend(drs)
        file_gender_types.append(fg_type)
        for name, acc in men_evs.items():
            gender_accs["men"].setdefault(name, []).append(acc)
        for name, acc in women_evs.items():
            gender_accs["women"].setdefault(name, []).append(acc)

    # Track which genders have data in this bundle
    bundle_genders: set[str] = set()
    for g in ("men", "women"):
        if gender_accs[g]:
            bundle_genders.add(g)
    if not bundle_genders:
        bundle_genders = {"men"}

    # ── Phase 2: second-pass targeted recovery ────────────────────────────────
    # Identify events that are still missing or have no complete anchor
    missing_by_gender: dict[str, list[str]] = {}
    for gender in sorted(bundle_genders):
        missing_for_gender = []
        for event_name in TARGET_EVENTS:
            accs = gender_accs[gender].get(event_name, [])
            if not accs:
                missing_for_gender.append(event_name)
            else:
                best = max(accs, key=lambda a: (a.completeness(), len(a.places)))
                if not best.anchor():
                    missing_for_gender.append(event_name)
        if missing_for_gender:
            missing_by_gender[gender] = missing_for_gender

    pass2_recovered: list[str] = []  # "gender:event" strings

    # Build a pages cache shared between Phase 2 and Phase 3 to avoid re-reading PDFs
    _pages_cache: dict[str, list[str]] = {}

    def _get_pages(path) -> list[str] | None:
        key = str(path)
        if key not in _pages_cache:
            try:
                _pages_cache[key] = extract_pages(key)
            except Exception:
                _pages_cache[key] = []
        return _pages_cache[key] or None

    if missing_by_gender:
        for path, meta in bundle["paths"]:
            pages = _get_pages(path)
            if pages is None:
                continue

            fg_type = classify_file_gender(meta, pages)
            session  = (
                "prelims" if meta["is_prelim"] else
                "finals"  if meta["is_final"]  else
                detect_session_type_from_text(pages)
            )

            for gender, missing_list in missing_by_gender.items():
                if fg_type == "men"   and gender == "women":
                    continue
                if fg_type == "women" and gender == "men":
                    continue

                # Filter out events that are already complete after earlier pass2 files
                still_needed = [
                    ev for ev in missing_list
                    if not any(
                        a.anchor()
                        for a in gender_accs[gender].get(ev, [])
                    )
                ]
                if not still_needed:
                    continue

                # One PDF read → scan for ALL still-needed events simultaneously
                found_map, drs = _second_pass_scan_file(
                    pages, still_needed, gender,
                    path.name, session, meta["is_prelim"],
                    bundle_id, conference, year,
                )
                all_debug_rows.extend(drs)

                for event_name, acc in found_map.items():
                    gender_accs[gender].setdefault(event_name, []).append(acc)
                    all_flag_rows.append(flag_fn(
                        gender, event_name, path.name,
                        f"Event found on second-pass loose scan "
                        f"(places found: {len(acc.places)})",
                    ))

    # ── Phase 3: team score extraction (last-day-first strategy) ─────────────
    all_team_rows: list[dict] = []
    team_score_strategy = "none"
    paths_sorted = _sort_paths_last_day_first(bundle["paths"])

    # Try last-day file's final pages first — reuse pages cache
    for path, meta in paths_sorted:
        pages = _get_pages(path)
        if pages is None:
            continue

        fg_type = classify_file_gender(meta, pages)
        found_in_this_file = False

        for gender in sorted(bundle_genders):
            if fg_type == "men"   and gender == "women":
                continue
            if fg_type == "women" and gender == "men":
                continue

            # Try last 5 pages first
            end_rows = parse_team_scores(
                pages[-5:], conference, path.name, gender, reverse_pages=True
            )
            if end_rows:
                all_team_rows.extend(end_rows)
                found_in_this_file = True
                team_score_strategy = f"end_pages:{path.name}"
                all_flag_rows.append(flag_fn(
                    gender, "", path.name,
                    f"Team scores found on final pages of {path.name}",
                ))
                continue

            # Fall back to full file scan
            full_rows = parse_team_scores(
                pages, conference, path.name, gender
            )
            if full_rows:
                all_team_rows.extend(full_rows)
                found_in_this_file = True
                team_score_strategy = f"full_scan:{path.name}"
                all_flag_rows.append(flag_fn(
                    gender, "", path.name,
                    f"Team scores found via full scan of {path.name}",
                ))

        if found_in_this_file:
            all_flag_rows.append(flag_fn(
                "", "", path.name,
                f"Team scores searched on last-day file: {path.name}",
            ))
            break  # stop once scores found in any file

    # If still nothing found, note it
    if not all_team_rows:
        sources = ", ".join(str(p.name) for p, _ in bundle["paths"])
        all_flag_rows.append(flag_fn("", "", sources, "Team scores not found in any bundle file"))
        team_score_strategy = "not_found"

    # Deduplicate team rows
    seen_teams: set[str] = set()
    unique_teams: list[dict] = []
    for row in all_team_rows:
        row_with_meta = {**row, "Year": year, "Bundle_ID": bundle_id}
        key = f"{row['Team'].lower()}|{row.get('Gender','')}"
        if key not in seen_teams:
            seen_teams.add(key)
            unique_teams.append(row_with_meta)

    # ── Final merge: pick best accumulator per event per gender ───────────────
    # pass2_targeted: events that lacked a complete anchor after first pass
    pass2_targeted: dict[str, set[str]] = {
        g: set(evs) for g, evs in missing_by_gender.items()
    }

    event_rows:      list[dict] = []
    events_found:    list[str]  = []
    events_missing:  list[str]  = []
    events_detected: set[str]   = set()

    for gender in sorted(bundle_genders):
        gender_events_found   = []
        gender_events_missing = []
        targeted = pass2_targeted.get(gender, set())

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
                # Recovered = was targeted for pass2 AND now has a complete anchor
                if event_name in targeted:
                    pass2_recovered.append(f"{gender}:{event_name}")
                    all_flag_rows.append(flag_fn(
                        gender, event_name, best.source,
                        f"Event recovered on second-pass (complete anchor now available)",
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
                missing      = best.missing_places()
                place_labels = {1: "1st", 8: "8th", 16: "16th"}
                miss_str     = ", ".join(place_labels[p] for p in missing)
                pass_label   = "both passes" if event_name in targeted else "first pass"

                # Distinguish a genuine field-size limitation from a parse gap.
                # If ONLY 16th is missing and the event never had B-Final data,
                # the conference likely ran fewer than 16 finalists.
                if missing == [16] and not best.has_b_final:
                    issue = (
                        "fewer_than_16_finalists — no B-Final section detected; "
                        "event likely had fewer than 16 entrants"
                    )
                elif missing == [16] and best.has_b_final:
                    issue = (
                        f"Event incomplete after {pass_label} — "
                        "16th place not found despite B-Final data present"
                    )
                else:
                    issue = (
                        f"Event incomplete after {pass_label} — "
                        f"{miss_str} place not found"
                    )
                all_flag_rows.append(flag_fn(
                    gender, event_name, best.source, issue,
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
    if pass2_recovered:
        notes_parts.append(f"pass2 recovered {len(pass2_recovered)} event(s)")

    men_out   = sum(1 for r in event_rows if r["Gender"] == "men")
    women_out = sum(1 for r in event_rows if r["Gender"] == "women")
    men_miss   = sum(1 for e in events_missing if e.startswith("men:"))
    women_miss = sum(1 for e in events_missing if e.startswith("women:"))

    summary = {
        "bundle_id":           bundle_id,
        "conference":          conference,
        "year":                year,
        "detected_gender":     detected_gender,
        "files":               [str(p.name) for p, _ in bundle["paths"]],
        "file_gender_types":   file_gender_types,
        "events_detected":     len(events_detected),
        "events_mapped":       len(events_found),
        "events_output":       len(event_rows),
        "men_events_output":   men_out,
        "women_events_output": women_out,
        "events_missing_list": events_missing,
        "men_missing":         men_miss,
        "women_missing":       women_miss,
        "events_recovered_p2": len(pass2_recovered),
        "n_teams":             len(unique_teams),
        "team_score_strategy": team_score_strategy,
        "n_flags":             len(all_flag_rows),
        "notes":               "; ".join(notes_parts) if notes_parts else "",
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
    p2    = summary["events_recovered_p2"]

    print(f"\n  ┌─ Bundle: {bid}")
    print(f"  │  Conference : {conf}  |  Gender(s): {gend}")
    print(f"  │  Files ({len(files)}): " + ", ".join(files[:3])
          + (" ..." if len(files) > 3 else ""))
    men_out   = summary.get("men_events_output", 0)
    women_out = summary.get("women_events_output", 0)
    gender_detail = f"men:{men_out} women:{women_out}"
    line = (
        f"  │  Anchors out: {summary['events_output']:2d}  ({gender_detail})  "
        f"Teams: {summary['n_teams']:2d}  Flags: {summary['n_flags']}"
    )
    if p2:
        line += f"  Pass2-recovered: {p2}"
    print(line)
    if summary["team_score_strategy"] != "none":
        print(f"  │  Team scores  : {summary['team_score_strategy']}")
    if miss:
        men_miss   = summary.get("men_missing", 0)
        women_miss = summary.get("women_missing", 0)
        print(f"  │  Missing ({len(miss)}: men:{men_miss} women:{women_miss}): "
              + ", ".join(miss[:6])
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
    # Cross-bundle dedup: key = (Conference, Year, Gender, Event)
    seen_anchors: set[tuple] = set()

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
        for row in ev_rows:
            anchor_key = (row["Conference"], row["Year"], row["Gender"], row["Event"])
            if anchor_key in seen_anchors:
                all_flags.append({
                    "Source_File": row.get("Source_File", ""),
                    "Bundle_ID":   row.get("Bundle_ID", ""),
                    "Conference":  row["Conference"],
                    "Year":        row["Year"],
                    "Gender":      row["Gender"],
                    "Event":       row["Event"],
                    "Issue":       "Duplicate anchor suppressed — same Conference/Year/Gender/Event already output",
                })
            else:
                seen_anchors.add(anchor_key)
                all_events.append(row)
        all_teams.extend(tm_rows)
        all_flags.extend(fl_rows)
        all_debug_r.extend(dr_rows)

        # Build debug summary row
        all_debug_s.append({
            "Bundle_ID":              summary["bundle_id"],
            "Conference":             summary["conference"],
            "Files_In_Bundle":        len(summary["files"]),
            "Detected_Gender":        summary["detected_gender"],
            "Events_Detected":        summary["events_detected"],
            "Events_Mapped":          summary["events_mapped"],
            "Events_Output":          summary["events_output"],
            "Men_Events_Output":      summary["men_events_output"],
            "Women_Events_Output":    summary["women_events_output"],
            "Events_Missing":         len(summary["events_missing_list"]),
            "Men_Missing":            summary["men_missing"],
            "Women_Missing":          summary["women_missing"],
            "Events_Recovered_Pass2": summary["events_recovered_p2"],
            "Missing_Event_List":     ", ".join(summary["events_missing_list"]),
            "Team_Scores_Found":      summary["n_teams"],
            "Team_Score_Strategy":    summary["team_score_strategy"],
            "Notes":                  summary["notes"],
        })

        all_summaries.append(summary)
        print_bundle_summary(summary)

    write_csv(output_dir / "event_anchors.csv",        ANCHOR_HEADER,       all_events)
    write_csv(output_dir / "team_scores.csv",          TEAM_HEADER,         all_teams)
    write_csv(output_dir / "review_flags.csv",         FLAG_HEADER,         all_flags)
    write_csv(output_dir / "debug_bundle_report.csv",  DEBUG_REPORT_HEADER, all_debug_r)
    write_csv(output_dir / "debug_bundle_summary.csv", DEBUG_SUMMARY_HEADER, all_debug_s)

    total_anchors  = len(all_events)
    total_missing  = sum(len(s["events_missing_list"]) for s in all_summaries)
    total_pass2    = sum(s["events_recovered_p2"] for s in all_summaries)

    print(f"\n{'='*64}")
    print(f"  Done.")
    print(f"  PDFs processed:        {total_pdfs}")
    print(f"  Bundles processed:     {len(all_summaries)}")
    print(f"  Event anchors output:  {total_anchors}  "
          f"(men: {sum(1 for r in all_events if r['Gender']=='men')}, "
          f"women: {sum(1 for r in all_events if r['Gender']=='women')})")
    if total_pass2:
        print(f"  Pass-2 recovered:      {total_pass2} event(s)")
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
