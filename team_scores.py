"""
team_scores.py — dedicated team-score extraction pipeline for Lane4 Data Harvester.

Completely isolated from event parsing (harvester.py / parser_helpers.py) and
from the validator.  Call run_team_scores(bundles, output_dir) after the normal
harvester run; it writes two new CSVs and returns their rows.

Supported source formats
------------------------
1. Standard two-column compressed rows (most conferences):
       "1. Team A 500 2. Team B 450"
2. Single-column rows (ODAC, WAC):
       "1. Team A 500"
3. Mixed-content rows (MPSF, Patriot, Ivy multi-col):
       "other content 3. Team C 320"
4. Place-School-Points table (auxiliary score PDFs, MIAC, PCSC):
       "Place  School  Points" header
       "1 Team Name Team Name 1,474"  (school name sometimes doubled)

Source-selection priority
-------------------------
  Auxiliary score PDFs (filename contains "team score / team result /
  team ranking / score sheet / standings") are tried first.
  Within that tier: day-order keywords (saturday > day4 …) break ties.
  Final fallback: largest page count.

Fallback passes per PDF
-----------------------
  Pass 1 — last MAX_TAIL_PAGES pages (fast, covers most conferences).
  Pass 2 — full document, triggered when pass 1 found EXACTLY ONE gender
            (handles mid-document men's/women's sections like ODAC).
  Neither pass is triggered for auxiliary score PDFs scored above a
  threshold (they get a full scan regardless because they may be 1-page).

Outputs
-------
  output/team_scores.csv         — one row per (bundle, gender, team)
  output/team_score_coverage.csv — one row per bundle
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Optional

import pdfplumber

# ─────────────────────────────────────────────────────────────────────────────
# Tuning constants
# ─────────────────────────────────────────────────────────────────────────────

MAX_TAIL_PAGES:  int   = 10
MAX_CONSEC_MISS: int   = 5
MIN_SCORE:       float = 1.0
MAX_RANK:        int   = 60

# ─────────────────────────────────────────────────────────────────────────────
# Output schema
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
# Regexes — headings
# ─────────────────────────────────────────────────────────────────────────────

# Detects any line that signals the start of a team-score section.
# Covers standard HY-TEK ("Scores - Women"), ranking sub-headings
# ("Women - Team Rankings"), and the Place-School-Points table header
# style used in auxiliary score PDFs ("Men - Team Scores").
HEADING_RE = re.compile(
    r'(?:'
    r'scores\s*[-–]\s*(women|men)'               # "Scores - Women/Men"
    r'|(?:women|men)\s*[-–]\s*team\s+rank'        # "Women/Men - Team Rankings"
    r'|(?:women|men)\s*[-–]?\s*team\s+(?:score|stand)'   # "Men - Team Scores" / "Women Team Scores"
    r'|combined\s+team\s+(?:score|rank|stand)'    # "Combined Team Scores"
    r'|team\s+(?:score|rank|standing)'            # "Team Scores" / "Team Rankings"
    r')',
    re.I,
)

# Detects the column header of Place-School-Points tables (skip it)
_TABLE_HDR_RE = re.compile(r'^\s*place\s+school\s+points?\s*$', re.I)

# ─────────────────────────────────────────────────────────────────────────────
# Regexes — entry formats
# ─────────────────────────────────────────────────────────────────────────────

# Format 1: "N. TeamName Score"  (standard HY-TEK footer, 1- or 2-col)
# Lookahead: entry ends at next "N. [A-Z]" or end-of-line/string.
_ENTRY_RE = re.compile(
    r'(?<!\d)([1-9]\d{0,1})\.\s+'
    r'([A-Z][A-Za-z0-9 ,&()\'\-./]*?)'
    r'\s+([\d,]+(?:\s*\.\s*\d+)?)'
    r'(?=\s+[1-9]\d{0,1}\.\s+[A-Z]|[\s\n]*$)',
    re.MULTILINE,
)

# Format 2: "N TeamName [TeamName] Score"  (Place-School-Points table)
# Line-anchored; rank has NO period.  School name may be doubled.
_TABLE_ENTRY_RE = re.compile(
    r'^(\d{1,3})\s+'
    r'([A-Z][A-Za-z0-9 ,&()\'\-./\']*?)\s+'
    r'([\d,]+(?:\s*\.\s*\d+)?)\s*$',
    re.MULTILINE,
)

# Auxiliary score PDF filename keywords
_SCORE_AUX_RE = re.compile(
    r'(?:team\s*score|team\s*result|team\s*rank|team\s*standing'
    r'|score\s*sheet|score\s*summary|standings)',
    re.I,
)

# Day-order keywords for source priority
_DAY_PRIORITY: dict[str, int] = {
    'sunday': 9, 'saturday': 8, 'sat_': 8,
    'day5': 7, 'day_5': 7, 'day4': 6, 'day_4': 6,
    'day3': 5, 'day_3': 5, 'day2': 3, 'day_2': 3,
    'day1': 1, 'day_1': 1,
}

_FINAL_HINTS: dict[str, int] = {
    'final': 6, 'complete': 6, 'full': 5,
    'championship': 4, 'results': 3,
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_score_aux_pdf(path: Path) -> bool:
    """Return True if this PDF is a dedicated team-score auxiliary file."""
    return bool(_SCORE_AUX_RE.search(path.stem))


def _source_priority(path: Path) -> int:
    """Score a PDF by likelihood of containing FINAL team standings."""
    score = 0
    if _is_score_aux_pdf(path):
        score += 200          # always preferred over regular result PDFs
    fname = path.name.lower()
    for kw, pts in _DAY_PRIORITY.items():
        if kw in fname:
            score += pts
            break
    for kw, pts in _FINAL_HINTS.items():
        if kw in fname:
            score += pts
    return score


def rank_sources(paths: list[Path]) -> list[Path]:
    """Return PDF paths sorted highest-priority first."""
    return sorted(paths, key=lambda p: (_source_priority(p), p.stat().st_size), reverse=True)


def _detect_gender(heading: str) -> str:
    h = heading.lower()
    if 'women' in h:
        return 'women'
    if 'men' in h:
        return 'men'
    if 'combined' in h or 'overall' in h:
        return 'combined'
    return 'unknown'


def _normalize_score(raw: str) -> Optional[float]:
    """Parse scores like '1,474', '631. 50', '1,353. 50' → float."""
    s = raw.replace(',', '').replace(' ', '')
    try:
        return float(s)
    except ValueError:
        return None


def _dedup_name(name: str) -> str:
    """
    Remove doubled school name.  Auxiliary score PDFs repeat the school name
    in the same cell: 'Princeton University Princeton University' → 'Princeton
    University'.  We try splitting the word list into two equal halves; if the
    halves match, keep only the first.
    """
    parts = name.split()
    n = len(parts)
    if n < 2:
        return name
    for half in range(1, n // 2 + 1):
        if n % half == 0 and parts[:half] == parts[n - half:]:
            return ' '.join(parts[:n - half])
    # Also try: longest prefix that appears twice consecutively
    for half in range(n // 2, 0, -1):
        if parts[:half] == parts[half:half * 2]:
            return ' '.join(parts[:half])
    return name


# ─────────────────────────────────────────────────────────────────────────────
# Entry extraction — both formats
# ─────────────────────────────────────────────────────────────────────────────

def _extract_entries(text: str) -> list[tuple[int, str, float]]:
    """
    Extract (rank, team, score) from arbitrary text using Format 1
    ("N. TeamName Score").  Handles 1-col, 2-col, and mixed-content rows.
    """
    results = []
    for m in _ENTRY_RE.finditer(text):
        rank_str, team_raw, score_raw = m.group(1), m.group(2), m.group(3)
        try:
            rank = int(rank_str)
        except ValueError:
            continue
        score = _normalize_score(score_raw)
        if score is None or score < MIN_SCORE or rank < 1 or rank > MAX_RANK:
            continue
        team = re.sub(r'\s+', ' ', team_raw).strip().rstrip('.,')
        if len(team) < 2:
            continue
        results.append((rank, team, score))
    return results


def _extract_table_entries(text: str) -> list[tuple[int, str, float]]:
    """
    Extract (rank, team, score) from Place-School-Points table lines.
    Format 2: "N TeamName [TeamName] Score" — rank has NO period.
    Skips header rows and footer "Total" rows.
    """
    results = []
    for m in _TABLE_ENTRY_RE.finditer(text):
        rank_str, team_raw, score_raw = m.group(1), m.group(2), m.group(3)
        try:
            rank = int(rank_str)
        except ValueError:
            continue
        if rank < 1 or rank > MAX_RANK:
            continue
        score = _normalize_score(score_raw)
        if score is None or score < MIN_SCORE:
            continue
        team = re.sub(r'\s+', ' ', team_raw).strip().rstrip('.,')
        team = _dedup_name(team)
        if len(team) < 2:
            continue
        results.append((rank, team, score))
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Page-level extraction
# ─────────────────────────────────────────────────────────────────────────────

def _parse_page(page, page_num: int) -> list[dict]:
    """
    Extract all team-score rows from one PDF page.

    Detects both standard ("N. TeamName Score") and Place-School-Points table
    ("N TeamName Score") formats automatically per section.
    """
    raw_text = page.extract_text() or ""
    if not raw_text.strip():
        return []

    lines = raw_text.splitlines()
    results: list[dict] = []

    current_gender:   Optional[str]  = None
    current_heading:  str            = ""
    section_lines:    list[str]      = []
    table_mode:       bool           = False   # True → Place-School-Points format
    consec_miss:      int            = 0

    def _flush(gender, heading, body_lines, is_table):
        if not gender or not body_lines:
            return
        block = "\n".join(body_lines)
        if is_table:
            entries = _extract_table_entries(block)
        else:
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
        stripped = line.strip()

        if HEADING_RE.search(stripped):
            _flush(current_gender, current_heading, section_lines, table_mode)
            current_gender  = _detect_gender(stripped)
            current_heading = stripped
            section_lines   = []
            table_mode      = False
            consec_miss     = 0
            continue

        if current_gender is None:
            continue

        # Detect Place-School-Points table header — skip the line itself
        if _TABLE_HDR_RE.match(stripped):
            table_mode = True
            continue

        # Skip "Total" footer lines
        if re.match(r'^\s*total\b', stripped, re.I):
            continue

        # Track progress
        if table_mode:
            has_entry = bool(_TABLE_ENTRY_RE.search(stripped))
        else:
            has_entry = bool(_ENTRY_RE.search(line))

        if has_entry:
            section_lines.append(line)
            consec_miss = 0
        else:
            consec_miss += 1
            if consec_miss < MAX_CONSEC_MISS:
                section_lines.append(line)
            if re.search(r'^Event\s+\d+|^NAME\s+YR\b', stripped, re.I):
                _flush(current_gender, current_heading, section_lines, table_mode)
                current_gender = None
                section_lines  = []

    _flush(current_gender, current_heading, section_lines, table_mode)
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

    Pass 1: last ``tail_pages`` pages (fast, covers most conferences).
    Pass 2: full document — triggered when pass 1 found exactly one gender
            OR when the PDF is an auxiliary score file (may have scores on
            page 1).  This handles mid-document sections (e.g. ODAC men's
            scores) and auxiliary PDFs whose scores are at the top.

    Returns (rows, pages_scanned, section_found).
    """
    rows:          list[dict] = []
    pages_scanned: int        = 0
    section_found: bool       = False

    is_aux = _is_score_aux_pdf(pdf_path)

    try:
        with pdfplumber.open(pdf_path) as pdf:
            n = len(pdf.pages)
            if n == 0:
                return rows, 0, False

            # Pass 1 ─────────────────────────────────────────────────────────
            start_idx = 0 if is_aux else max(0, n - tail_pages)
            for pg_idx in range(start_idx, n):
                pg = pdf.pages[pg_idx]
                page_rows = _parse_page(pg, pg.page_number)
                pages_scanned += 1
                if page_rows:
                    rows.extend(page_rows)
                    section_found = True

            # Pass 2 ─────────────────────────────────────────────────────────
            # Trigger when:
            #   • Nothing found yet (widening fallback), OR
            #   • Exactly one gender found (might have missed the other in tail)
            genders_pass1 = {r["gender_or_division"] for r in rows}
            need_pass2 = (
                start_idx > 0
                and (not section_found or len(genders_pass1) == 1)
            )
            if need_pass2:
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
# De-duplication / merge across multiple PDFs in a bundle
# ─────────────────────────────────────────────────────────────────────────────

def _keep_final_standings(rows: list[dict]) -> list[dict]:
    """
    When multiple PDFs each report standings, keep the most-complete set per
    gender (proxy: highest rank-1 score → most events counted).
    Preserves legitimately tied ranks (two teams sharing a rank).
    """
    if not rows:
        return rows

    from collections import defaultdict
    by_gender: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_gender[r["gender_or_division"]].append(r)

    final: list[dict] = []
    for gender, grp in by_gender.items():
        # Best source = highest rank-1 score
        r1_by_source: dict[str, float] = {}
        for r in grp:
            if r["rank"] == 1:
                src = r["source_pdf"]
                if src not in r1_by_source or r["score"] > r1_by_source[src]:
                    r1_by_source[src] = r["score"]

        if r1_by_source:
            best_src = max(r1_by_source, key=lambda s: r1_by_source[s])
            grp = [r for r in grp if r["source_pdf"] == best_src]

        # De-duplicate exact (rank, team) pairs
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
    """Extract team scores for one bundle. Returns (score_rows, coverage_info)."""
    coverage: dict = {
        "bundle_id":               bundle_id,
        "conference":              conference,
        "selected_pdf":            "",
        "candidate_pages_scanned": 0,
        "section_found":           False,
        "rows_captured":           0,
        "genders_found":           "",
        "parse_status":            "section_not_found",
        "notes":                   "",
    }

    if not paths:
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

    merged = _keep_final_standings(all_rows)
    genders = sorted({r["gender_or_division"] for r in merged})
    selected_pdf_name = merged[0]["source_pdf"] if merged else ""

    coverage["selected_pdf"]            = selected_pdf_name
    coverage["candidate_pages_scanned"] = total_pages_scanned
    coverage["section_found"]           = bool(merged)
    coverage["rows_captured"]           = len(merged)
    coverage["genders_found"]           = "|".join(genders)

    if merged:
        both_genders = {"men", "women"}.issubset(set(genders))
        if both_genders or ("combined" in genders):
            coverage["parse_status"] = "captured_complete"
        else:
            coverage["parse_status"] = "captured_partial"
            notes.append(f"only_{genders[0]}_found")
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

    with open(output_dir / "team_scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TEAM_SCORES_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_score_rows)

    with open(output_dir / "team_score_coverage.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COVERAGE_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(coverage_rows)

    n_found   = sum(1 for c in coverage_rows if c["section_found"])
    n_rows    = len(all_score_rows)
    n_bundles = len(bundles)

    print(f"  [team_scores] Bundles scanned:    {n_bundles}")
    print(f"  [team_scores] Sections found:     {n_found} / {n_bundles}")
    print(f"  [team_scores] Team-score rows:    {n_rows}")
    print(f"  [team_scores] Wrote team_scores.csv  ({n_rows} rows)")
    print(f"  [team_scores] Wrote team_score_coverage.csv  ({len(coverage_rows)} rows)")

    return all_score_rows, coverage_rows
