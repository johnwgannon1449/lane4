"""
Lane4 Data Harvester v1 — parser_helpers.py
Helper functions for PDF extraction and data normalization.
"""

import re
import json
import os
from pathlib import Path

# ── Target events ────────────────────────────────────────────────────────────

TARGET_EVENTS = [
    "50 Free",
    "100 Free",
    "200 Free",
    "500 Free",
    "1000 Free",
    "1650 Free",
    "100 Back",
    "200 Back",
    "100 Breast",
    "200 Breast",
    "100 Fly",
    "200 Fly",
    "200 IM",
    "400 IM",
]

# Map normalized distance+stroke → canonical label
_EVENT_NORM_MAP = {
    ("50",   "free"):    "50 Free",
    ("100",  "free"):    "100 Free",
    ("200",  "free"):    "200 Free",
    ("500",  "free"):    "500 Free",
    ("1000", "free"):    "1000 Free",
    ("1650", "free"):    "1650 Free",
    ("100",  "back"):    "100 Back",
    ("200",  "back"):    "200 Back",
    ("100",  "breast"):  "100 Breast",
    ("200",  "breast"):  "200 Breast",
    ("100",  "fly"):     "100 Fly",
    ("200",  "fly"):     "200 Fly",
    ("200",  "im"):      "200 IM",
    ("400",  "im"):      "400 IM",
}

# Relay keywords — any line matching these should be skipped
_RELAY_KEYWORDS = re.compile(
    r"\brelay\b|\b4x\b|\b4 x\b",
    re.IGNORECASE
)

# Diving keywords
_DIVING_KEYWORDS = re.compile(
    r"\bdiv(ing|e)?\b|\bplatform\b|\bspringboard\b",
    re.IGNORECASE
)


# ── Time helpers ─────────────────────────────────────────────────────────────

def time_to_seconds(time_str: str) -> float | None:
    """
    Convert a swim time string to decimal seconds.
      "19.85"    → 19.85
      "1:39.22"  → 99.22
      "15:48.90" → 948.90

    Returns None for DQ, NS, NT, SCR, or unparseable strings.
    """
    if not time_str:
        return None
    s = time_str.strip().upper()
    # Skip disqualified / scratched / no-time entries
    if s in {"DQ", "NS", "NT", "SCR", "X", "---", "--", ""}:
        return None
    # Strip leading/trailing punctuation that sometimes appears in PDFs
    s = re.sub(r"[^\d:.]", "", s)
    if not s:
        return None
    try:
        parts = s.split(":")
        if len(parts) == 1:
            return float(parts[0])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        return None
    return None


def seconds_to_time(sec: float) -> str:
    """Format seconds back to MM:SS.hh for display."""
    if sec is None:
        return ""
    if sec < 60:
        return f"{sec:.2f}"
    minutes = int(sec // 60)
    remainder = sec - minutes * 60
    if minutes < 60:
        return f"{minutes}:{remainder:05.2f}"
    hours = int(minutes // 60)
    minutes = minutes % 60
    return f"{hours}:{minutes:02d}:{remainder:05.2f}"


# ── Event name normalization ──────────────────────────────────────────────────

def normalize_event_name(raw: str) -> str | None:
    """
    Normalize a raw event name from a PDF to one of the 14 target events.
    Returns None if the event is a relay, diving, women's, or unrecognized.

    Examples accepted:
      "Event 3 Men 500 Yard Freestyle"    → "500 Free"
      "Men 100 Yard Breaststroke"         → "100 Breast"
      "Men's 200 Yard Individual Medley"  → "200 IM"
      "Event 5 Men 100 Yard Butterfly"    → "100 Fly"
    """
    raw_lower = raw.lower()

    # Reject relays and diving immediately
    if _RELAY_KEYWORDS.search(raw_lower):
        return None
    if _DIVING_KEYWORDS.search(raw_lower):
        return None

    # Reject women's events (various spellings)
    if re.search(r"\bwomens?\b|\bwomen'?s\b|\bwomen\b", raw_lower):
        return None

    # Determine stroke keyword first
    stroke = None
    if re.search(r"freestyle|(?<!\w)free(?!\w)", raw_lower):
        stroke = "free"
    elif re.search(r"backstroke|(?<!\w)back(?!\w)", raw_lower):
        stroke = "back"
    elif re.search(r"breaststroke|(?<!\w)breast(?!\w)", raw_lower):
        stroke = "breast"
    elif re.search(r"butterfly|(?<!\w)fly(?!\w)", raw_lower):
        stroke = "fly"
    elif re.search(r"individual\s+medley|medley(?!\s+relay)|\bim\b", raw_lower):
        stroke = "im"

    if stroke is None:
        return None

    # Find the distance: scan all numbers, prefer valid distances
    # (avoids grabbing the event number from "Event 3 Men 500 Yard Freestyle")
    valid_dists = {"50", "100", "200", "500", "1000", "1650", "400"}
    numbers = re.findall(r"\b(\d+)\b", raw_lower)

    # Try in reverse order (right-to-left) so we pick the distance closest
    # to the stroke word rather than an event-sequence number.
    for num in reversed(numbers):
        if num in valid_dists:
            result = _EVENT_NORM_MAP.get((num, stroke))
            if result:
                return result

    return None


# ── Conference name detection ─────────────────────────────────────────────────

def load_conference_map() -> dict:
    """Load conference_map.json from the project root."""
    map_path = Path(__file__).parent / "conference_map.json"
    if map_path.exists():
        with open(map_path, "r") as f:
            data = json.load(f)
        return data.get("mappings", {})
    return {}


def detect_conference(filename: str, pdf_title_text: str = "") -> str:
    """
    Detect conference name from filename, then from PDF text.
    Returns "Unknown" if no match found.
    """
    conf_map = load_conference_map()
    fname_lower = filename.lower()

    # Try filename against the map keys (substring match)
    for key, conf_name in conf_map.items():
        if key.lower() in fname_lower:
            return conf_name

    # Try PDF title text (first 2000 chars) against known conference names
    title_lower = pdf_title_text.lower()
    known = [
        ("centennial",      "Centennial"),
        ("liberty league",  "Liberty League"),
        ("nescac",          "NESCAC"),
        ("ncac",            "NCAC"),
        ("uaa",             "UAA"),
        ("new england",     "New England"),
        ("newmac",          "NEWMAC"),
        ("miac",            "MIAC"),
        ("sciac",           "SCIAC"),
        ("odac",            "ODAC"),
        ("presidents",      "Presidents"),
        ("landmark",        "Landmark"),
        ("midwest",         "Midwest"),
        ("heartland",       "Heartland"),
    ]
    for keyword, name in known:
        if keyword in title_lower:
            return name

    return "Unknown"


# ── PDF text extraction ───────────────────────────────────────────────────────

def extract_text_pdfplumber(pdf_path: str) -> list[str]:
    """
    Extract text page-by-page using pdfplumber.
    Returns a list of strings, one per page.
    """
    import pdfplumber
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
    return pages


def extract_text_pymupdf(pdf_path: str) -> list[str]:
    """
    Fallback: extract text page-by-page using PyMuPDF (fitz).
    Returns a list of strings, one per page.
    """
    import fitz
    pages = []
    doc = fitz.open(pdf_path)
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return pages


def extract_pages(pdf_path: str) -> list[str]:
    """
    Try pdfplumber first, fall back to PyMuPDF.
    Returns list of page text strings.
    """
    try:
        pages = extract_text_pdfplumber(pdf_path)
        # Sanity check: if we got mostly empty pages, try fitz
        non_empty = sum(1 for p in pages if len(p.strip()) > 50)
        if non_empty == 0:
            raise ValueError("pdfplumber returned mostly empty pages")
        return pages
    except Exception as e:
        try:
            return extract_text_pymupdf(pdf_path)
        except Exception as e2:
            raise RuntimeError(
                f"Both pdfplumber ({e}) and PyMuPDF ({e2}) failed"
            )


# ── Section detection (Men vs Women) ─────────────────────────────────────────

# Regex patterns to detect the start of a men's results section
_MEN_SECTION_RE = re.compile(
    r"\b(men'?s?)\s+(swimming|results|events?|championships?|team|individual)",
    re.IGNORECASE,
)
_WOMEN_SECTION_RE = re.compile(
    r"\b(women'?s?)\s+(swimming|results|events?|championships?|team|individual)",
    re.IGNORECASE,
)

# Patterns that indicate an event header (for men's events specifically)
_MEN_EVENT_RE = re.compile(
    r"""
    (?:
        event\s+\d+\s+                  # "Event 3 "
    )?
    (?:
        men'?s?\s+                      # "Men's " or "Men "
    )?
    (\d+)\s*                            # distance
    (?:yard|yd|meter|m)s?\s+            # "Yard" or "Meter"
    (freestyle|free|backstroke|back|    # stroke
     breaststroke|breast|butterfly|fly|
     individual\s+medley|medley|im)
    (?:\s*-\s*finals?)?                 # optional "- Final"
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Alternative: plain "Event X Men 500 Yard Freestyle" header format
_ALT_EVENT_RE = re.compile(
    r"event\s+\d+\s+men'?s?\s+(\d+)\s*(?:yard|yd|meter|m)s?\s+"
    r"(freestyle|free|backstroke|back|breaststroke|breast|butterfly|fly|"
    r"individual\s+medley|medley|im)",
    re.IGNORECASE,
)


def looks_like_men_event_header(line: str) -> bool:
    """Return True if a line looks like a men's swimming event header."""
    if _RELAY_KEYWORDS.search(line) or _DIVING_KEYWORDS.search(line):
        return False
    if re.search(r"women'?s?", line, re.IGNORECASE):
        return False
    return bool(_MEN_EVENT_RE.search(line)) or bool(_ALT_EVENT_RE.search(line))


def extract_event_name_from_header(line: str) -> str | None:
    """
    Extract and normalize event name from an event header line.
    Returns canonical event name or None.
    """
    # Strip out "Event N", "Men's", "Yard", etc. and try to normalize
    cleaned = re.sub(
        r"(event\s+\d+\s*|men'?s?\s*|women'?s?\s*|"
        r"\d+\s*(yard|yd|meter|m)s?\s*|-?\s*final[s]?\s*)",
        " ", line, flags=re.IGNORECASE
    ).strip()
    return normalize_event_name(line)  # Use the full line for better context


# ── Result row parsing ────────────────────────────────────────────────────────

def parse_place_and_time(line: str) -> tuple[int | None, str | None]:
    """
    Try to extract (place, time_str) from a result line.

    Handles formats like:
      "1    John Smith        Stanford        19.85"
      "  1  Smith, John       STAN            19.85  (19.84)"
      "1 Smith J              Stanford   1:39.22"

    Returns (None, None) if the line doesn't look like a result row.
    """
    line = line.strip()
    if not line:
        return None, None

    # Time pattern: optional M:SS.hh or MM:SS.hh or H:MM:SS.hh
    TIME_PAT = r"(?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2}"

    # The place should be a small integer at the start of the line (1-64)
    m = re.match(r"^(\d{1,2})\b", line)
    if not m:
        return None, None
    place = int(m.group(1))
    if place < 1 or place > 64:
        return None, None

    # Find ALL time-like tokens in the rest of the line
    times = re.findall(TIME_PAT, line)
    if not times:
        return None, None

    # The finals time is usually the last one before any parenthetical alt-time
    # Filter out values that look like scores (e.g., "32.0" with no colon for sprint)
    # We take the first valid time that converts to > 10 seconds
    for t in times:
        sec = time_to_seconds(t)
        if sec is not None and sec >= 10:
            return place, t

    # For 50 Free, times can be < 20s — accept any positive
    if times:
        return place, times[0]

    return None, None


# ── Psych-sheet detection ─────────────────────────────────────────────────────

def looks_like_psych_sheet(pages: list[str]) -> bool:
    """
    Heuristic: if the first few pages contain 'psych sheet', 'heat sheet',
    'Time Trials', or mostly 'NT' / 'NS' entries, flag it.
    """
    sample = " ".join(pages[:3]).lower()
    if any(kw in sample for kw in ["psych sheet", "heat sheet", "time trial"]):
        return True
    # If there are far more NT/NS occurrences than actual times, probably psych
    nt_count = len(re.findall(r"\bnt\b|\bns\b", sample))
    time_count = len(re.findall(r"\d:\d\d\.\d\d|\b\d\d\.\d\d\b", sample))
    if nt_count > 0 and time_count > 0 and nt_count > time_count * 0.5:
        return True
    return False


# ── Team score parsing ────────────────────────────────────────────────────────

_TEAM_SCORE_HEADER_RE = re.compile(
    r"(?:team|men'?s?)\s+(?:final\s+)?(?:team\s+)?(?:scores?|standings?|results?)",
    re.IGNORECASE,
)

def parse_team_scores(pages: list[str], conference: str, source_file: str) -> list[dict]:
    """
    Scan all pages for a team standings block and extract men's team scores.
    Returns list of dicts: {Conference, Team, Team_Score, Source_File}
    """
    results = []
    in_team_section = False
    in_women = False

    for page_text in pages:
        for line in page_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Detect start of team score section
            if _TEAM_SCORE_HEADER_RE.search(stripped):
                in_team_section = True
                in_women = bool(re.search(r"women'?s?", stripped, re.IGNORECASE))
                continue

            # Detect women's section start — stop collecting
            if in_team_section and re.search(r"women'?s?\s+(team|final|standing)", stripped, re.IGNORECASE):
                in_women = True

            # Detect men's section start after women's — re-enable
            if in_team_section and re.search(r"men'?s?\s+(team|final|standing)", stripped, re.IGNORECASE):
                in_women = False

            if not in_team_section or in_women:
                continue

            # Team score row: "1  Stanford  423.0" or "Stanford  423"
            m = re.match(
                r"(?:\d+\s+)?([A-Za-z][\w\s,.'&-]{2,40?}?)\s+(\d+(?:\.\d+)?)\s*$",
                stripped
            )
            if m:
                team_name = m.group(1).strip()
                score     = m.group(2).strip()
                # Filter out header-like lines
                if any(kw in team_name.lower() for kw in ["team", "place", "score", "school"]):
                    continue
                results.append({
                    "Conference":  conference,
                    "Team":        team_name,
                    "Team_Score":  score,
                    "Source_File": source_file,
                })

    return results
