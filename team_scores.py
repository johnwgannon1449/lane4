"""
team_scores.py — dedicated team-score extraction pipeline for Lane4 Data Harvester.

Completely isolated from event parsing (harvester.py / parser_helpers.py) and
from the validator.  Call run_team_scores(bundles, output_dir) after the
normal harvester run; it writes two new CSVs and returns their rows.

Design
------
  1. Source selection — for each bundle, rank its PDFs by how likely they are
     to contain FINAL standings (day-order keywords, "final"/"complete" hints,
     page count).  Try sources in ranked order; fall back to others if a source
     yields nothing.

  2. Section detection — scan the last MAX_TAIL_PAGES of each candidate PDF for
     known team-score headings.  If nothing found there, widen to the full PDF.

  3. Section extraction — split pages at gender-heading boundaries; collect all
     lines belonging to each gender section.

  4. Entry extraction — apply a regex to every line in the section, handling:
       • two-column compact rows  ("1. Team A 500 2. Team B 450")
       • single-column rows       ("1. Team A\n500" or "1. Team A 500")
       • mixed content rows       ("other stuff 3. Team C 320")
     Uses a look-ahead on the "N." marker pattern so team names that contain
     numbers (e.g. "A-10", "Texas A&M") are not prematurely split.

Outputs
-------
  output/team_scores.csv         — one row per (bundle, gender, team)
  output/team_score_coverage.csv — one row per bundle summarising extraction
"""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path
from typing import Optional

import pdfplumber

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

MAX_TAIL_PAGES:  int   = 10   # scan this many pages from end first
MAX_CONSEC_MISS: int   = 5    # stop collecting after this many blank entry lines
MIN_SCORE:       float = 1.0  # discard entries with score below this
MAX_RANK:        int   = 60   # discard entries with rank above this

# Headings that signal a team-score section
HEADING_RE = re.compile(
    r'(?:'
    r'scores\s*[-–]\s*(women|men)'        # "Scores - Women" / "Scores - Men"
    r'|(?:women|men)\s*[-–]\s*team\s+rank'# "Women - Team Rankings"
    r'|(?:women|men)\s+team\s+(?:score|stand)'  # "Women Team Scores"
    r'|combined\s+team\s+(?:score|rank|stand)'   # "Combined Team Scores"
    r'|team\s+(?:score|rank|standing)'           # "Team Scores" / "Team Rankings"
    r')',
    re.I,
)

# A scored team entry: "N. Team Name Score"
# Look-ahead ensures the score ends at either another "N. [A-Z]" marker or line end.
_ENTRY_RE = re.compile(
    r'(?<!\d)([1-9]\d{0,1})\.\s+'              # rank 1-99 (not preceded by digit)
    r'([A-Z][A-Za-z0-9 ,&()\'\-./]*?)'         # team name starts with capital
    r'\s+([\d,]+(?:\.\d+)?)'                   # score (integer or decimal)
    r'(?=\s+[1-9]\d{0,1}\.\s+[A-Z]|[\s\n]*$)',# lookahead: next entry or end
    re.MULTILINE,
)

# Through-event extractor for coverage notes
_THROUGH_RE = re.compile(r'through\s+event\s+(\d+)', re.I)

# Day keywords mapped to priority boost (higher = more final)
_DAY_PRIORITY: dict[str, int] = {
    'sunday': 9, 'saturday': 8, 'sat_': 8,
    'day5': 7, 'day_5': 7, 'day4': 6, 'day_4': 6,
    'day3': 5, 'day_3': 5, 'day2': 3, 'day_2': 3,
    'day1': 1, 'day_1': 1,
}

# Filename hints for "most complete" PDFs
_FINAL_HINTS = {
    'final': 6, 'complete': 6, 'full': 5,
    'championship': 4, 'results': 3,
}

# ─────────────────────────────────────────────────────────────────────────────
# Output headers
# ─────────────────────────────────────────────────────────────────────────────

TEAM_SCORES_HEADER = [
    "bundle_id", "conference", "gender_or_division",
    "rank", "team", "score",
    "source_pdf", "source_page", "section_heading",
]

COVERAGE_HEADER = [
    "bundle_id", "conference",
    "selected_pdf", "candidate_pages_scanned",
    "section_found", "rows_captured",
    "genders_found", "parse_status", "notes",
]


# ─────────────────────────────────────────────────────────────────────────────
# Source selection
# ─────────────────────────────────────────────────────────────────────────────

def _source_priority(path: Path) -> int:
    """Score a PDF by how likely it is to contain FINAL team standings."""
    fname = path.name.lower()
    score = 0
    for kw, pts in _DAY_PRIORITY.items():
        if kw in fname:
            score += pts
            break   # take the highest matching day hint only
    for kw, pts in _FINAL_HINTS.items():
        if kw in fname:
            score += pts
    return score


def rank_sources(paths: list[Path]) -> list[Path]:
    """Return PDF paths sorted highest-priority first."""
    ranked = sorted(paths, key=lambda p: (_source_priority(p), p.stat().st_size), reverse=True)
    return ranked


# ─────────────────────────────────────────────────────────────────────────────
# Gender detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_gender(heading: str) -> str:
    h = heading.lower()
    if 'women' in h:
        return 'women'
    if 'men' in h:
        return 'men'
    if 'combined' in h or 'overall' in h:
        return 'combined'
    return 'unknown'


# ─────────────────────────────────────────────────────────────────────────────
# Entry extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_entries(text: str) -> list[tuple[int, str, float]]:
    """
    Return list of (rank, team_name, score) from arbitrary text.

    Works on packed two-column rows, single-column rows, and rows where
    score entries are embedded alongside other content (mixed-layout pages).
    """
    results = []
    seen_ranks: set[int] = set()   # guard against duplicate captures on same page

    for m in _ENTRY_RE.finditer(text):
        rank_str, team_raw, score_str = m.group(1), m.group(2), m.group(3)
        try:
            rank = int(rank_str)
            score = float(score_str.replace(',', ''))
        except ValueError:
            continue
        if rank < 1 or rank > MAX_RANK:
            continue
        if score < MIN_SCORE:
            continue
        team = re.sub(r'\s+', ' ', team_raw).strip().rstrip('.,')
        if not team or len(team) < 2:
            continue
        key = rank
        if key in seen_ranks:
            # Allow tied ranks (two teams with same rank, e.g. both 7th)
            # but track as a pair so we don't infinitely duplicate
            pass
        results.append((rank, team, score))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Page-level section extraction
# ─────────────────────────────────────────────────────────────────────────────

def _parse_page(page, page_num: int) -> list[dict]:
    """
    Extract all team-score rows from one PDF page.

    Returns list of dicts with keys: gender_or_division, rank, team, score,
    source_page, section_heading.
    """
    raw_text = page.extract_text() or ""
    if not raw_text.strip():
        return []

    lines = raw_text.splitlines()
    results: list[dict] = []

    # Walk lines; track current gender section.
    current_gender: Optional[str] = None
    current_heading: str = ""
    section_lines: list[str] = []
    consec_miss = 0

    def _flush(gender, heading, body_lines):
        nonlocal results
        if not gender or not body_lines:
            return
        block = "\n".join(body_lines)
        entries = _extract_entries(block)
        for rank, team, score in entries:
            results.append({
                "gender_or_division": gender,
                "rank":               rank,
                "team":               team,
                "score":              score,
                "source_page":        page_num,
                "section_heading":    heading.strip(),
            })

    for line in lines:
        if HEADING_RE.search(line):
            # Flush previous section
            _flush(current_gender, current_heading, section_lines)
            current_gender = _detect_gender(line)
            current_heading = line.strip()
            section_lines = []
            consec_miss = 0
        elif current_gender is not None:
            # Check if this line adds any entries
            entries = _extract_entries(line)
            if entries:
                section_lines.append(line)
                consec_miss = 0
            else:
                # Allow a few blank-entry lines (continuation / interleaved content)
                consec_miss += 1
                if consec_miss < MAX_CONSEC_MISS:
                    section_lines.append(line)
                # If too many consecutive misses, don't cut the section — some
                # mixed-layout PDFs have long gaps between score entries.
                # We only hard-stop if we see a new major event heading.
                if re.search(r'^Event\s+\d+|^NAME\s+YR\b', line, re.I):
                    _flush(current_gender, current_heading, section_lines)
                    current_gender = None
                    section_lines = []

    _flush(current_gender, current_heading, section_lines)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# PDF-level extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_from_pdf(
    pdf_path: Path,
    tail_pages: int = MAX_TAIL_PAGES,
) -> tuple[list[dict], int, bool]:
    """
    Extract team-score rows from a single PDF.

    Returns:
        (rows, pages_scanned, section_found)
    """
    rows: list[dict] = []
    pages_scanned = 0
    section_found = False

    try:
        with pdfplumber.open(pdf_path) as pdf:
            n = len(pdf.pages)
            if n == 0:
                return rows, 0, False

            # First pass: last tail_pages pages
            start_idx = max(0, n - tail_pages)
            for pg_idx in range(start_idx, n):
                pg = pdf.pages[pg_idx]
                page_rows = _parse_page(pg, pg.page_number)
                pages_scanned += 1
                if page_rows:
                    rows.extend(page_rows)
                    section_found = True

            # If nothing found, widen to full document
            if not section_found and start_idx > 0:
                for pg_idx in range(0, start_idx):
                    pg = pdf.pages[pg_idx]
                    page_rows = _parse_page(pg, pg.page_number)
                    pages_scanned += 1
                    if page_rows:
                        rows.extend(page_rows)
                        section_found = True

    except Exception:
        pass

    return rows, pages_scanned, section_found


# ─────────────────────────────────────────────────────────────────────────────
# De-duplication / merge
# ─────────────────────────────────────────────────────────────────────────────

def _keep_final_standings(rows: list[dict]) -> list[dict]:
    """
    When multiple PDFs in a bundle each report standings (e.g. multi-day
    championships), keep only the most-complete standing for each
    (gender, rank) pair.  "Most complete" = highest score for rank 1 (proxy
    for most events counted).

    Ties in rank are preserved (two teams legitimately sharing a rank).
    """
    if not rows:
        return rows

    # Group by gender
    from collections import defaultdict
    by_gender: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_gender[r["gender_or_division"]].append(r)

    final: list[dict] = []
    for gender, grp in by_gender.items():
        # Find the "most complete" source: prefer the source_pdf that has the
        # highest rank-1 score (more events = more points accumulated).
        r1_by_source: dict[str, float] = {}
        for r in grp:
            if r["rank"] == 1:
                src = r["source_pdf"]
                if src not in r1_by_source or r["score"] > r1_by_source[src]:
                    r1_by_source[src] = r["score"]

        if r1_by_source:
            best_src = max(r1_by_source, key=lambda s: r1_by_source[s])
            grp = [r for r in grp if r["source_pdf"] == best_src]

        # De-duplicate exact (rank, team) pairs keeping the higher score entry
        seen: dict[tuple, dict] = {}
        for r in grp:
            key = (r["rank"], r["team"])
            if key not in seen or r["score"] > seen[key]["score"]:
                seen[key] = r
        final.extend(seen.values())

    return sorted(final, key=lambda r: (r["gender_or_division"], r["rank"]))


# ─────────────────────────────────────────────────────────────────────────────
# Bundle-level extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_bundle_team_scores(
    bundle_id: str,
    conference: str,
    paths: list[Path],
) -> tuple[list[dict], dict]:
    """
    Extract team scores for one bundle.

    Returns:
        (score_rows, coverage_info)
    """
    coverage: dict = {
        "bundle_id":            bundle_id,
        "conference":           conference,
        "selected_pdf":         "",
        "candidate_pages_scanned": 0,
        "section_found":        False,
        "rows_captured":        0,
        "genders_found":        "",
        "parse_status":         "section_not_found",
        "notes":                "",
    }

    if not paths:
        coverage["parse_status"] = "section_not_found"
        coverage["notes"] = "no_pdfs_in_bundle"
        return [], coverage

    ranked = rank_sources(paths)
    all_rows: list[dict] = []
    total_pages_scanned = 0
    notes: list[str] = []

    for pdf_path in ranked:
        rows, pages_scanned, section_found = _extract_from_pdf(pdf_path)
        total_pages_scanned += pages_scanned

        if not rows:
            if section_found:
                notes.append(f"{pdf_path.name}: heading_found_no_rows")
            continue

        for r in rows:
            r["source_pdf"]  = pdf_path.name
            r["bundle_id"]   = bundle_id
            r["conference"]  = conference

        all_rows.extend(rows)

    # Merge and de-duplicate across sources
    merged = _keep_final_standings(all_rows)

    genders = sorted({r["gender_or_division"] for r in merged})
    selected_pdf_name = merged[0]["source_pdf"] if merged else ""

    coverage["selected_pdf"]            = selected_pdf_name
    coverage["candidate_pages_scanned"] = total_pages_scanned
    coverage["section_found"]           = bool(merged)
    coverage["rows_captured"]           = len(merged)
    coverage["genders_found"]           = "|".join(genders)

    if merged:
        if len(genders) >= 2 or (len(genders) == 1 and genders[0] not in ("men", "women")):
            coverage["parse_status"] = "captured_complete"
        elif len(genders) == 1:
            # Only one gender found — could be a single-gender conference PDF
            coverage["parse_status"] = "captured_partial"
            notes.append(f"only_{genders[0]}_found")
        else:
            coverage["parse_status"] = "captured_partial"
    elif any("heading_found_no_rows" in n for n in notes):
        coverage["parse_status"] = "section_found_no_rows"
    else:
        coverage["parse_status"] = "section_not_found"

    if notes:
        coverage["notes"] = "; ".join(notes)

    return merged, coverage


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_team_scores(
    bundles: dict,
    output_dir: Path,
) -> tuple[list[dict], list[dict]]:
    """
    Process all bundles and write team-score CSVs.

    Parameters
    ----------
    bundles : dict
        Same structure returned by parser_helpers.group_into_bundles().
        Each value has keys: bundle_id, conference, paths (list of (Path, meta)).
    output_dir : Path
        Directory where output CSVs are written.

    Returns
    -------
    (all_score_rows, coverage_rows)
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    all_score_rows: list[dict] = []
    coverage_rows:  list[dict] = []

    for bundle_id, bundle in sorted(bundles.items()):
        conference = bundle.get("conference", "Unknown")
        paths = [p for p, _meta in bundle.get("paths", [])]

        score_rows, cov = extract_bundle_team_scores(bundle_id, conference, paths)
        all_score_rows.extend(score_rows)
        coverage_rows.append(cov)

    # ── Write team_scores.csv ─────────────────────────────────────────────
    with open(output_dir / "team_scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TEAM_SCORES_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_score_rows)

    # ── Write team_score_coverage.csv ──────────────────────────────────────
    with open(output_dir / "team_score_coverage.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COVERAGE_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(coverage_rows)

    # ── Console summary ───────────────────────────────────────────────────
    n_found   = sum(1 for c in coverage_rows if c["section_found"])
    n_rows    = len(all_score_rows)
    n_bundles = len(bundles)

    print(f"  [team_scores] Bundles scanned:    {n_bundles}")
    print(f"  [team_scores] Sections found:     {n_found} / {n_bundles}")
    print(f"  [team_scores] Team-score rows:    {n_rows}")
    print(f"  [team_scores] Wrote team_scores.csv  ({n_rows} rows)")
    print(f"  [team_scores] Wrote team_score_coverage.csv  ({len(coverage_rows)} rows)")

    return all_score_rows, coverage_rows
