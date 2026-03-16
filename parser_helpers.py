"""
Lane4 Data Harvester — parser_helpers.py
Helper functions for PDF extraction, event normalization, metadata parsing,
bundle grouping, and session detection.
"""

import re
import json
import os
from pathlib import Path


# ── Target events ─────────────────────────────────────────────────────────────

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

# Map (distance_str, stroke_key) → canonical label
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

# Valid event distances — used to reject event-sequence numbers (e.g. "Event 3")
_VALID_DISTS = {"50", "100", "200", "400", "500", "1000", "1650"}

# Relay keywords — skip any line matching
_RELAY_KEYWORDS = re.compile(r"\brelay\b|\b4x\b|\b4 x\b", re.IGNORECASE)

# Diving keywords
_DIVING_KEYWORDS = re.compile(
    r"\bdiv(ing|e)?\b|\bplatform\b|\bspringboard\b", re.IGNORECASE
)


# ── Filename metadata token sets ──────────────────────────────────────────────

_GENDER_WOMEN_TOKENS = frozenset([
    "women", "womens", "woman",
])
_GENDER_MEN_TOKENS = frozenset([
    "men", "mens", "man",
])
_GENDER_COMBINED_TOKENS = frozenset([
    "combined", "full", "complete", "both", "coed",
])
_SESSION_PRELIM_TOKENS = frozenset([
    "prelim", "prelims", "preliminary", "preliminaries", "heats", "heat",
])
_SESSION_FINAL_TOKENS = frozenset([
    "final", "finals",
])
_DAY_OF_WEEK_TOKENS = frozenset([
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
])
# All tokens we strip before building a conference slug from the filename
_META_STRIP_TOKENS = (
    _GENDER_WOMEN_TOKENS | _GENDER_MEN_TOKENS | _GENDER_COMBINED_TOKENS |
    _SESSION_PRELIM_TOKENS | _SESSION_FINAL_TOKENS | _DAY_OF_WEEK_TOKENS |
    frozenset([
        "swimming", "swim", "diving", "s", "d", "sd",
        "championship", "championships", "champ", "champs",
        "results", "result", "conference", "ncaa", "session",
        "event", "events", "day", "daily", "meet", "invite", "invitational",
        "full", "complete", "final", "finals",
        "w",  # "w" alone is ambiguous — strip it; gender detected separately
    ])
)

# Built-in conference keyword list (supplement to conference_map.json)
_BUILTIN_CONF = [
    ("nescac",        "NESCAC"),
    ("ncac",          "NCAC"),
    ("centennial",    "Centennial"),
    ("libertyleague", "Liberty League"),
    ("liberty",       "Liberty League"),
    ("uaa",           "UAA"),
    ("newmac",        "NEWMAC"),
    ("miac",          "MIAC"),
    ("sciac",         "SCIAC"),
    ("odac",          "ODAC"),
    ("presidents",    "Presidents"),
    ("landmark",      "Landmark"),
    ("midwest",       "Midwest"),
    ("heartland",     "Heartland"),
    ("empireeight",      "Empire 8"),
    ("empire8",          "Empire 8"),
    ("patriot",          "Patriot"),
    ("ivyleague",        "Ivy League"),
    ("ivyl",             "Ivy League"),
    ("ivy",              "Ivy League"),
    ("pac12",            "Pac-12"),
    ("pac10",            "Pac-10"),
    ("bigten",           "Big Ten"),
    ("big10",            "Big Ten"),
    ("bigeight",         "Big Eight"),
    ("big12",            "Big 12"),
    ("bigwest",          "Big West"),
    ("bigeast",          "Big East"),
    ("acc",              "ACC"),
    ("sec",              "SEC"),
    ("maac",             "MAAC"),
    ("asun",             "ASUN"),
    ("caa",              "CAA"),
    ("cciw",             "CCIW"),
    ("gliac",            "GLIAC"),
    ("glvc",             "GLVC"),
    ("psac",             "PSAC"),
    ("summitleague",     "Summit League"),
    ("americaeast",      "America East"),
    ("horizonleague",    "Horizon League"),
    ("atlantic10",       "Atlantic 10"),
    ("atlanticten",      "Atlantic 10"),
    ("a10",              "Atlantic 10"),
    ("wac",              "WAC"),
    ("mac",              "MAC"),
    ("meac",             "MEAC"),
    ("socon",            "SoCon"),
    ("southernconf",     "SoCon"),
    ("sunbelt",          "Sun Belt"),
    ("ovc",              "OVC"),
    ("mvc",              "MVC"),
    ("cac",              "CAC"),
    ("csac",             "CSAC"),
    ("amcc",             "AMCC"),
    ("nia",              "NIA"),
    ("cc",               "CC"),
    ("d3swim",           "D3Swim"),
]


# ── Time helpers ──────────────────────────────────────────────────────────────

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
    if s in {"DQ", "NS", "NT", "SCR", "X", "---", "--", ""}:
        return None
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
    """Format seconds back to M:SS.hh for display."""
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
    Normalize a raw event name string → canonical event label or None.

    Returns None for relays, diving, women's events, or unrecognized patterns.
    Handles "Event 3 Men 500 Yard Freestyle", "Men's 200 IM", "100 Butterfly", etc.
    """
    raw_lower = raw.lower()

    if _RELAY_KEYWORDS.search(raw_lower):
        return None
    if _DIVING_KEYWORDS.search(raw_lower):
        return None

    # Reject women's labels in any spelling
    if re.search(r"\bwomens?\b|\bwomen'?s\b|\bwomen\b", raw_lower):
        return None

    # Identify stroke
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

    # Extract distance — scan right-to-left so we pick the real distance
    # rather than an event-sequence number that appears earlier in the string
    numbers = re.findall(r"\b(\d+)\b", raw_lower)
    for num in reversed(numbers):
        if num in _VALID_DISTS:
            result = _EVENT_NORM_MAP.get((num, stroke))
            if result:
                return result

    return None


# ── Conference detection ──────────────────────────────────────────────────────

def load_conference_map() -> dict:
    """Load conference_map.json from the project root."""
    map_path = Path(__file__).parent / "conference_map.json"
    if map_path.exists():
        with open(map_path, "r") as f:
            data = json.load(f)
        return data.get("mappings", {})
    return {}


def _conf_token_match(key: str, text: str) -> bool:
    """
    Match a conference key against text using word-boundary regex.

    Also tries matching the key against a de-underscored version of the text
    so that 'bigeast' matches 'big_east' and 'a10' matches 'a_10'.

    Prevents short keys like 'cc' from matching inside 'acc'.
    """
    pattern = r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])"
    if re.search(pattern, text):
        return True
    # Also try against text with underscores/hyphens/spaces removed
    compressed = re.sub(r"[_\-\s]", "", text)
    return bool(re.search(pattern, compressed))


def detect_conference(filename: str, pdf_title_text: str = "") -> str:
    """
    Detect conference name from filename (after stripping metadata tokens),
    then from PDF title text, then fall back to "Unknown".
    """
    conf_map = load_conference_map()

    # Strip metadata from filename before matching
    stem = Path(filename).stem.lower()
    tokens = re.split(r"[_\s\-]+", stem)
    clean_tokens = [
        t for t in tokens
        if t not in _META_STRIP_TOKENS
        and not re.fullmatch(r"20\d\d", t)
        and not re.fullmatch(r"day[1-9]", t)
        and not re.fullmatch(r"d[1-9]", t)
    ]
    clean_stem = "_".join(clean_tokens)
    fname_lower = filename.lower()

    # conf_map first (user-defined takes priority) — substring is fine here
    # because the user controls the keys
    for key, conf_name in conf_map.items():
        k = key.lower()
        if _conf_token_match(k, fname_lower) or _conf_token_match(k, clean_stem):
            return conf_name

    # Built-in list — word-boundary match to avoid 'cc' inside 'acc'
    for key, name in _BUILTIN_CONF:
        if _conf_token_match(key, clean_stem) or _conf_token_match(key, fname_lower):
            return name

    # PDF title text fallback
    title_lower = pdf_title_text.lower()
    for key, name in _BUILTIN_CONF:
        if _conf_token_match(key, title_lower):
            return name

    return "Unknown"


# ── Filename metadata parsing ─────────────────────────────────────────────────

def parse_filename_metadata(filename: str) -> dict:
    """
    Extract meet metadata from a PDF filename.

    Returns a dict with:
        year           (str | None)   — e.g. "2026"
        gender         (str)          — 'men', 'women', or 'combined'
        is_prelim      (bool)         — True if filename says prelim/heats
        is_final       (bool)         — True if filename says finals
        day            (str | None)   — e.g. 'day1', 'friday'
        conference_hint (str)         — slug of conf tokens only, no metadata
    """
    stem = Path(filename).stem.lower()
    tokens = re.split(r"[_\s\-]+", stem)

    # ── Year ──────────────────────────────────────────────────────────────────
    year = None
    for t in tokens:
        if re.fullmatch(r"20\d\d", t):
            year = t
            break

    # ── Gender ────────────────────────────────────────────────────────────────
    # Scan in order; first match wins.
    # "w" alone is treated as women (e.g. "acc_2026_w_day1").
    gender = "combined"
    for t in tokens:
        if t in _GENDER_WOMEN_TOKENS or t == "w":
            gender = "women"
            break
        if t in _GENDER_MEN_TOKENS:
            gender = "men"
            break
        if t in _GENDER_COMBINED_TOKENS:
            gender = "combined"
            break

    # ── Session ───────────────────────────────────────────────────────────────
    is_prelim = any(t in _SESSION_PRELIM_TOKENS for t in tokens)
    is_final  = any(t in _SESSION_FINAL_TOKENS  for t in tokens) and not is_prelim

    # ── Day ───────────────────────────────────────────────────────────────────
    day = None
    for i, t in enumerate(tokens):
        if re.fullmatch(r"day[1-9]", t) or re.fullmatch(r"d[1-9]", t):
            day = t
            break
        # Handle "day_3" → tokens ['day', '3'] split by separator
        if t == "day" and i + 1 < len(tokens) and re.fullmatch(r"[1-9]", tokens[i + 1]):
            day = f"day{tokens[i + 1]}"
            break
        if t in _DAY_OF_WEEK_TOKENS:
            day = t
            break

    # ── Conference hint ───────────────────────────────────────────────────────
    # Keep only tokens that aren't metadata.
    conf_tokens = []
    for t in tokens:
        if t in _META_STRIP_TOKENS:
            continue
        if re.fullmatch(r"20\d\d", t):
            continue
        if re.fullmatch(r"day[1-9]", t) or re.fullmatch(r"d[1-9]", t):
            continue
        conf_tokens.append(t)

    return {
        "year":             year,
        "gender":           gender,
        "is_prelim":        is_prelim,
        "is_final":         is_final,
        "day":              day,
        "conference_hint":  "_".join(conf_tokens),
    }


# ── Bundle grouping ───────────────────────────────────────────────────────────

def group_into_bundles(pdf_paths: list, conf_map: dict | None = None) -> dict:
    """
    Group PDF paths into meet bundles.

    A bundle = (conference, year, gender).
    Day/session/prelim labels are metadata *within* a bundle, not separators.

    Returns dict of bundle_id → {
        'bundle_id': str,
        'conference': str,
        'year': str,
        'gender': str,
        'paths': [(Path, meta_dict), ...]
    }
    """
    if conf_map is None:
        conf_map = load_conference_map()

    bundles: dict = {}

    for path in pdf_paths:
        meta = parse_filename_metadata(path.name)

        # Resolve conference from hint and raw filename
        conf = "Unknown"
        hint = meta["conference_hint"]
        fname_lower = path.name.lower()

        for key, conf_name in conf_map.items():
            k = key.lower()
            if _conf_token_match(k, fname_lower) or _conf_token_match(k, hint):
                conf = conf_name
                break

        if conf == "Unknown":
            for key, name in _BUILTIN_CONF:
                if _conf_token_match(key, hint) or _conf_token_match(key, fname_lower):
                    conf = name
                    break

        meta["conference"] = conf
        year   = meta["year"]   or "unknown"
        gender = meta["gender"]

        # Bundle key: slugify conference + year + gender
        conf_slug = re.sub(r"[^a-z0-9]", "", conf.lower())
        bundle_id = f"{conf_slug}_{year}_{gender}"

        if bundle_id not in bundles:
            bundles[bundle_id] = {
                "bundle_id":  bundle_id,
                "conference": conf,
                "year":       year,
                "gender":     gender,
                "paths":      [],
            }
        bundles[bundle_id]["paths"].append((path, meta))

    return bundles


# ── PDF text extraction ───────────────────────────────────────────────────────

def extract_text_pdfplumber(pdf_path: str) -> list[str]:
    import pdfplumber
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


def extract_text_pymupdf(pdf_path: str) -> list[str]:
    import fitz
    pages = []
    doc = fitz.open(pdf_path)
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return pages


def extract_pages(pdf_path: str) -> list[str]:
    """Try pdfplumber, fall back to PyMuPDF. Returns list of page strings."""
    try:
        pages = extract_text_pdfplumber(pdf_path)
        if sum(1 for p in pages if len(p.strip()) > 50) == 0:
            raise ValueError("pdfplumber returned mostly empty pages")
        return pages
    except Exception as e:
        try:
            return extract_text_pymupdf(pdf_path)
        except Exception as e2:
            raise RuntimeError(
                f"Both pdfplumber ({e}) and PyMuPDF ({e2}) failed"
            )


# ── Session type detection from PDF content ───────────────────────────────────

def detect_session_type_from_text(pages: list[str]) -> str:
    """
    Detect whether a PDF contains finals or prelim results from text content.
    Returns 'finals', 'prelims', or 'unknown'.
    """
    sample = " ".join(pages[:6]).lower()
    # Count weighted signals
    finals_hits  = len(re.findall(r"\bfinals?\b", sample))
    prelim_hits  = len(re.findall(r"\bprelim(?:inar(?:y|ies))?\b|\bheats?\b", sample))
    if finals_hits > prelim_hits:
        return "finals"
    if prelim_hits > finals_hits:
        return "prelims"
    return "unknown"


# ── Psych-sheet / heat-sheet detection ───────────────────────────────────────

def looks_like_psych_sheet(pages: list[str]) -> bool:
    """
    Return True if the PDF looks like a psych/heat sheet rather than results.
    """
    sample = " ".join(pages[:3]).lower()
    if any(kw in sample for kw in ["psych sheet", "heat sheet", "time trial"]):
        return True
    nt_count   = len(re.findall(r"\bnt\b|\bns\b", sample))
    time_count = len(re.findall(r"\d:\d\d\.\d\d|\b\d\d\.\d\d\b", sample))
    if nt_count > 0 and time_count > 0 and nt_count > time_count * 0.5:
        return True
    return False


# ── Event header detection ────────────────────────────────────────────────────

_MEN_EVENT_RE = re.compile(
    r"""
    (?:event\s+\d+\s+)?         # optional "Event N "
    (?:men'?s?\s+)?             # optional "Men's "
    (\d+)\s*                    # distance
    (?:yard|yd|meter|m)s?\s+   # unit
    (freestyle|free|backstroke|back|
     breaststroke|breast|butterfly|fly|
     individual\s+medley|medley|im)
    (?:\s*[-–]\s*finals?)?      # optional "- Final"
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ALT_EVENT_RE = re.compile(
    r"event\s+\d+\s+men'?s?\s+(\d+)\s*(?:yard|yd|meter|m)s?\s+"
    r"(freestyle|free|backstroke|back|breaststroke|breast|butterfly|fly|"
    r"individual\s+medley|medley|im)",
    re.IGNORECASE,
)

# Women's equivalent patterns (needed for combined-PDF section detection)
_WOMEN_EVENT_RE = re.compile(
    r"""
    (?:event\s+\d+\s+)?
    women'?s?\s+
    (\d+)\s*(?:yard|yd|meter|m)s?\s+
    (freestyle|free|backstroke|back|
     breaststroke|breast|butterfly|fly|
     individual\s+medley|medley|im)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_men_event_header(line: str) -> bool:
    if _RELAY_KEYWORDS.search(line) or _DIVING_KEYWORDS.search(line):
        return False
    if re.search(r"\bwomens?\b|\bwomen'?s?\b", line, re.IGNORECASE):
        return False
    return bool(_MEN_EVENT_RE.search(line)) or bool(_ALT_EVENT_RE.search(line))


def is_women_event_header(line: str) -> bool:
    if _RELAY_KEYWORDS.search(line) or _DIVING_KEYWORDS.search(line):
        return False
    if re.search(r"\bwomens?\b|\bwomen'?s?\b", line, re.IGNORECASE):
        return bool(_WOMEN_EVENT_RE.search(line)) or bool(
            re.search(r"event\s+\d+\s+women'?s?\s+\d+", line, re.IGNORECASE)
        )
    return False


# ── Result row parsing ────────────────────────────────────────────────────────

_TIME_PAT = r"(?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2}"


def parse_place_and_time(line: str) -> tuple[int | None, str | None]:
    """
    Extract (place, time_str) from a result row.
    Returns (None, None) if the line doesn't look like a result row.
    """
    line = line.strip()
    if not line:
        return None, None

    m = re.match(r"^(\d{1,2})\b", line)
    if not m:
        return None, None
    place = int(m.group(1))
    if place < 1 or place > 64:
        return None, None

    times = re.findall(_TIME_PAT, line)
    if not times:
        return None, None

    # Accept the first time ≥ 10 seconds (filters score-like decimals)
    for t in times:
        sec = time_to_seconds(t)
        if sec is not None and sec >= 10:
            return place, t

    # 50 Free times can be < 20 s — accept any positive
    if times:
        return place, times[0]

    return None, None


# ── Team score parsing ────────────────────────────────────────────────────────

_TEAM_SCORE_HEADER_RE = re.compile(
    r"(?:team|men'?s?)\s+(?:final\s+)?(?:team\s+)?(?:scores?|standings?|results?)",
    re.IGNORECASE,
)


def parse_team_scores(pages: list[str], conference: str, source_file: str) -> list[dict]:
    """
    Scan pages for a men's team standings block.
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

            if _TEAM_SCORE_HEADER_RE.search(stripped):
                in_team_section = True
                in_women = bool(re.search(r"women'?s?", stripped, re.IGNORECASE))
                continue

            if in_team_section:
                if re.search(r"women'?s?\s+(team|final|standing)", stripped, re.IGNORECASE):
                    in_women = True
                if re.search(r"\bmen'?s?\s+(team|final|standing)", stripped, re.IGNORECASE):
                    in_women = False

            if not in_team_section or in_women:
                continue

            m = re.match(
                r"(?:\d+\s+)?([A-Za-z][\w\s,.'&\-]{2,40?}?)\s+(\d+(?:\.\d+)?)\s*$",
                stripped,
            )
            if m:
                team_name = m.group(1).strip()
                score     = m.group(2).strip()
                if any(kw in team_name.lower() for kw in ["team", "place", "score", "school"]):
                    continue
                results.append({
                    "Conference":  conference,
                    "Team":        team_name,
                    "Team_Score":  score,
                    "Source_File": source_file,
                })

    return results
