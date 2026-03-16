"""
Lane4 Data Harvester — parser_helpers.py
Helper functions for PDF extraction, event normalization, metadata parsing,
bundle grouping, session detection, and gender classification.
"""

import re
import json
import os
from pathlib import Path


# ── Target events (gender-neutral names) ──────────────────────────────────────

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

_VALID_DISTS = {"50", "100", "200", "400", "500", "1000", "1650"}

_RELAY_KEYWORDS = re.compile(r"\brelay\b|\b4x\b|\b4 x\b", re.IGNORECASE)

_DIVING_KEYWORDS = re.compile(
    r"\bdiv(ing|e)?\b|\bplatform\b|\bspringboard\b", re.IGNORECASE
)


# ── Sanity ranges for 1st-place anchor times ──────────────────────────────────
# (min_sec, max_sec) for plausible men's and women's collegiate winning times.
_SANITY_RANGES = {
    "men": {
        "50 Free":    (17.5,  23.0),
        "100 Free":   (39.0,  50.0),
        "200 Free":   (85.0,  110.0),
        "500 Free":   (232.0, 300.0),
        "1000 Free":  (500.0, 640.0),
        "1650 Free":  (850.0, 1080.0),
        "100 Back":   (43.0,  56.0),
        "200 Back":   (95.0,  124.0),
        "100 Breast": (50.0,  65.0),
        "200 Breast": (110.0, 145.0),
        "100 Fly":    (43.0,  56.0),
        "200 Fly":    (96.0,  126.0),
        "200 IM":     (94.0,  122.0),
        "400 IM":     (206.0, 268.0),
    },
    "women": {
        "50 Free":    (21.0,  27.0),
        "100 Free":   (45.0,  56.0),
        "200 Free":   (98.0,  124.0),
        "500 Free":   (264.0, 335.0),
        "1000 Free":  (560.0, 710.0),
        "1650 Free":  (950.0, 1200.0),
        "100 Back":   (49.0,  63.0),
        "200 Back":   (108.0, 138.0),
        "100 Breast": (57.0,  73.0),
        "200 Breast": (124.0, 160.0),
        "100 Fly":    (49.0,  63.0),
        "200 Fly":    (110.0, 142.0),
        "200 IM":     (106.0, 136.0),
        "400 IM":     (232.0, 302.0),
    },
}


def is_time_plausible(event_name: str, seconds: float, gender: str = "men") -> bool:
    """Return True if seconds is within the expected range for the event winner."""
    gender_key = gender if gender in _SANITY_RANGES else "men"
    r = _SANITY_RANGES[gender_key].get(event_name)
    if r is None:
        return True
    return r[0] <= seconds <= r[1]


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
_META_STRIP_TOKENS = (
    _GENDER_WOMEN_TOKENS | _GENDER_MEN_TOKENS | _GENDER_COMBINED_TOKENS |
    _SESSION_PRELIM_TOKENS | _SESSION_FINAL_TOKENS | _DAY_OF_WEEK_TOKENS |
    frozenset([
        "swimming", "swim", "diving", "s", "d", "sd",
        "championship", "championships", "champ", "champs",
        "results", "result", "conference", "ncaa", "session",
        "event", "events", "day", "daily", "meet", "invite", "invitational",
        "full", "complete", "final", "finals",
        "pdf",   # e.g. "Big_12_S_D_Champ_Results_pdf.pdf"
        "w",     # "w" alone is ambiguous — strip it; gender detected separately
    ])
)

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
    ("empireeight",   "Empire 8"),
    ("empire8",       "Empire 8"),
    ("patriot",       "Patriot"),
    ("ivyleague",     "Ivy League"),
    ("ivyl",          "Ivy League"),
    ("ivy",           "Ivy League"),
    ("pac12",         "Pac-12"),
    ("pac10",         "Pac-10"),
    ("bigten",        "Big Ten"),
    ("big10",         "Big Ten"),
    ("bigeight",      "Big Eight"),
    ("big12",         "Big 12"),
    ("bigwest",       "Big West"),
    ("bigeast",       "Big East"),
    ("acc",           "ACC"),
    ("sec",           "SEC"),
    ("maac",          "MAAC"),
    ("asun",          "ASUN"),
    ("caa",           "CAA"),
    ("cciw",          "CCIW"),
    ("gliac",         "GLIAC"),
    ("glvc",          "GLVC"),
    ("psac",          "PSAC"),
    ("summitleague",  "Summit League"),
    ("summit",        "Summit League"),
    ("americaeast",   "America East"),
    ("horizonleague", "Horizon League"),
    ("horizon",       "Horizon League"),
    ("atlantic10",    "Atlantic 10"),
    ("atlanticten",   "Atlantic 10"),
    ("a10",           "Atlantic 10"),
    ("wac",           "WAC"),
    ("mac",           "MAC"),
    ("meac",          "MEAC"),
    ("socon",         "SoCon"),
    ("southernconf",  "SoCon"),
    ("sunbelt",       "Sun Belt"),
    ("ovc",           "OVC"),
    ("mvc",           "MVC"),
    ("cac",           "CAC"),
    ("csac",          "CSAC"),
    ("amcc",          "AMCC"),
    ("nia",           "NIA"),
    ("cc",            "CC"),
    ("d3swim",        "D3Swim"),
    ("sac",           "SAC"),     # Southern / Sooner Athletic Conference
]


# ── Time helpers ──────────────────────────────────────────────────────────────

def time_to_seconds(time_str: str) -> float | None:
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

def _extract_stroke_and_distance(raw_lower: str):
    """Extract (stroke_key, distance_str) from a lowercased line, or (None, None)."""
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
        return None, None

    numbers = re.findall(r"\b(\d+)\b", raw_lower)
    for num in reversed(numbers):
        if num in _VALID_DISTS:
            return stroke, num
    return stroke, None


def normalize_event_name(raw: str) -> str | None:
    """
    Normalize a raw event name → canonical label or None.
    Rejects women's, relays, diving. Men's or bare headers are accepted.
    """
    raw_lower = raw.lower()
    if _RELAY_KEYWORDS.search(raw_lower):
        return None
    if _DIVING_KEYWORDS.search(raw_lower):
        return None
    if re.search(r"\bwomens?\b|\bwomen'?s\b|\bwomen\b", raw_lower):
        return None

    stroke, dist = _extract_stroke_and_distance(raw_lower)
    if stroke and dist:
        return _EVENT_NORM_MAP.get((dist, stroke))
    return None


def normalize_event_name_any(raw: str) -> tuple[str | None, str]:
    """
    Normalize a raw event name for EITHER gender.

    Returns (canonical_event, gender) where:
        gender = 'men'    — explicit men's label found
        gender = 'women'  — explicit women's label found
        gender = 'either' — no gender label (bare header like "500 Freestyle")
        gender = 'none'   — unrecognized / relay / diving

    Returns (None, 'none') when the line is not an event header.
    """
    raw_lower = raw.lower()
    if _RELAY_KEYWORDS.search(raw_lower):
        return None, "none"
    if _DIVING_KEYWORDS.search(raw_lower):
        return None, "none"

    has_women = bool(re.search(r"\bwomens?\b|\bwomen'?s\b|\bwomen\b", raw_lower))
    has_men   = bool(re.search(r"\bmens?\b|\bmen'?s\b", raw_lower)) and not has_women

    gender = "women" if has_women else ("men" if has_men else "either")

    stroke, dist = _extract_stroke_and_distance(raw_lower)
    if stroke and dist:
        canonical = _EVENT_NORM_MAP.get((dist, stroke))
        if canonical:
            return canonical, gender

    return None, "none"


# ── Conference detection ──────────────────────────────────────────────────────

def load_conference_map() -> dict:
    map_path = Path(__file__).parent / "conference_map.json"
    if map_path.exists():
        with open(map_path, "r") as f:
            data = json.load(f)
        return data.get("mappings", {})
    return {}


def _conf_token_match(key: str, text: str) -> bool:
    pattern = r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])"
    if re.search(pattern, text):
        return True
    compressed = re.sub(r"[_\-\s]", "", text)
    return bool(re.search(pattern, compressed))


def detect_conference(filename: str, pdf_title_text: str = "") -> str:
    conf_map = load_conference_map()

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

    for key, conf_name in conf_map.items():
        k = key.lower()
        if _conf_token_match(k, fname_lower) or _conf_token_match(k, clean_stem):
            return conf_name

    for key, name in _BUILTIN_CONF:
        if _conf_token_match(key, clean_stem) or _conf_token_match(key, fname_lower):
            return name

    title_lower = pdf_title_text.lower()
    for key, name in _BUILTIN_CONF:
        if _conf_token_match(key, title_lower):
            return name

    return "Unknown"


# ── Filename metadata parsing ─────────────────────────────────────────────────

def parse_filename_metadata(filename: str) -> dict:
    stem = Path(filename).stem.lower()
    tokens = re.split(r"[_\s\-]+", stem)

    year = None
    for t in tokens:
        if re.fullmatch(r"20\d\d", t):
            year = t
            break

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

    is_prelim = any(t in _SESSION_PRELIM_TOKENS for t in tokens)
    is_final  = any(t in _SESSION_FINAL_TOKENS  for t in tokens) and not is_prelim

    day = None
    for i, t in enumerate(tokens):
        if re.fullmatch(r"day[1-9]", t) or re.fullmatch(r"d[1-9]", t):
            day = t
            break
        if t == "day" and i + 1 < len(tokens) and re.fullmatch(r"[1-9]", tokens[i + 1]):
            day = f"day{tokens[i + 1]}"
            break
        if t in _DAY_OF_WEEK_TOKENS:
            day = t
            break

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


# ── File gender classification from content ──────────────────────────────────

def detect_file_gender_from_content(pages: list[str]) -> str:
    """
    Classify a PDF as 'men', 'women', 'combined', or 'unknown' from its text.

    Heuristic: count explicit gender mentions near event headers and section
    headers in the first 8 pages.
    """
    sample = " ".join(pages[:8]).lower()

    # Count gender-specific event mentions
    men_event_hits   = len(re.findall(
        r"\bmen'?s?\s+\d+", sample
    ))
    women_event_hits = len(re.findall(
        r"\bwomen'?s?\s+\d+", sample
    ))

    # Count section header mentions
    men_section   = len(re.findall(r"\bmen'?s?\s+(?:swimming|results|individual|championship)", sample))
    women_section = len(re.findall(r"\bwomen'?s?\s+(?:swimming|results|individual|championship)", sample))

    men_total   = men_event_hits   + men_section   * 3
    women_total = women_event_hits + women_section * 3

    if men_total > 0 and women_total > 0:
        return "combined"
    if women_total > 0:
        return "women"
    if men_total > 0:
        return "men"
    return "unknown"  # bare headers, no gender labels — treat as combined


def classify_file_gender(meta: dict, pages: list[str]) -> str:
    """
    Final file gender type, prioritising filename then content.

    Returns one of: 'men', 'women', 'combined', 'unknown'
    """
    fname_gender = meta.get("gender", "combined")

    if fname_gender == "men":
        return "men"
    if fname_gender == "women":
        return "women"

    # combined / unknown: look at content
    content_gender = detect_file_gender_from_content(pages)
    if content_gender != "unknown":
        return content_gender

    # No content signal at all — default to combined
    return "combined"


# ── Bundle grouping ───────────────────────────────────────────────────────────

def group_into_bundles(pdf_paths: list, conf_map: dict | None = None) -> dict:
    """
    Group PDF paths into bundles keyed by (conference, year).

    Unlike v1, gender is NOT part of the bundle key — instead each bundle
    holds files of any gender mix, and the merge step extracts men's and
    women's rows separately.  This allows combined files and gender-split
    files to share the same conference bundle.
    """
    if conf_map is None:
        conf_map = load_conference_map()

    bundles: dict = {}

    for path in pdf_paths:
        meta = parse_filename_metadata(path.name)

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
        year = meta["year"] or "unknown"

        conf_slug = re.sub(r"[^a-z0-9]", "", conf.lower())
        bundle_id = f"{conf_slug}_{year}"

        if bundle_id not in bundles:
            bundles[bundle_id] = {
                "bundle_id":  bundle_id,
                "conference": conf,
                "year":       year,
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


# ── Session type detection ────────────────────────────────────────────────────

def detect_session_type_from_text(pages: list[str]) -> str:
    """
    Returns 'finals', 'prelims', or 'unknown'.

    Scoring:
      +3  championship/consolation/b-final/c-final mentions
      +1  plain "final(s)"
      -3  "preliminary/ies" (strongly suggests prelims)
      -1  "prelim(s)" / "heats"
    """
    sample = " ".join(pages[:8]).lower()

    score = 0
    score += 3 * len(re.findall(
        r"championship\s+final|consolation\s+final|b[\-\s]?final|c[\-\s]?final",
        sample
    ))
    score += 1 * len(re.findall(r"\bfinals?\b", sample))
    score -= 3 * len(re.findall(r"\bpreliminar(?:y|ies)\b", sample))
    score -= 1 * len(re.findall(r"\bprelims?\b|\bheats?\b", sample))

    if score > 0:
        return "finals"
    if score < 0:
        return "prelims"
    return "unknown"


# ── Psych-sheet detection ─────────────────────────────────────────────────────

def looks_like_psych_sheet(pages: list[str]) -> bool:
    sample = " ".join(pages[:3]).lower()
    if any(kw in sample for kw in ["psych sheet", "heat sheet", "time trial"]):
        return True
    nt_count   = len(re.findall(r"\bnt\b|\bns\b", sample))
    time_count = len(re.findall(r"\d:\d\d\.\d\d|\b\d\d\.\d\d\b", sample))
    if nt_count > 0 and time_count > 0 and nt_count > time_count * 0.5:
        return True
    return False


# ── Event header regexes ──────────────────────────────────────────────────────

# Matches men's or bare (no gender label) event headers.
# Unit (Yard/Meter) is OPTIONAL.
_MEN_EVENT_RE = re.compile(
    r"""
    (?:event\s+\d+\s+)?                     # optional "Event N "
    (?:men'?s?\s+)?                         # optional "Men's "
    (\d+)\s*                                # distance
    (?:(?:yard|yd|meter|m)s?\s+)?           # optional unit
    (freestyle|free|backstroke|back|
     breaststroke|breast|butterfly|fly|
     individual\s+medley|medley|im)
    (?:\s*[-–]\s*(?:championship\s+)?finals?)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ALT_EVENT_RE = re.compile(
    r"event\s+\d+\s+men'?s?\s+(\d+)\s*(?:(?:yard|yd|meter|m)s?\s+)?"
    r"(freestyle|free|backstroke|back|breaststroke|breast|butterfly|fly|"
    r"individual\s+medley|medley|im)",
    re.IGNORECASE,
)

# Women's event header (same structure, explicit women's label required)
_WOMEN_EVENT_RE = re.compile(
    r"""
    (?:event\s+\d+\s+)?
    women'?s?\s+
    (\d+)\s*(?:(?:yard|yd|meter|m)s?\s+)?
    (freestyle|free|backstroke|back|
     breaststroke|breast|butterfly|fly|
     individual\s+medley|medley|im)
    (?:\s*[-–]\s*(?:championship\s+)?finals?)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TIME_PAT = r"(?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2}"

# Section header patterns that indicate a gender shift
_MEN_SECTION_RE = re.compile(
    r"\bmen'?s?\s+(?:swimming|results|championship|individual|events?)\b"
    r"|^\s*men'?s?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_WOMEN_SECTION_RE = re.compile(
    r"\bwomen'?s?\s+(?:swimming|results|championship|individual|events?)\b"
    r"|^\s*women'?s?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def is_event_header_any(line: str) -> bool:
    """
    Return True if `line` looks like a swim event header (either gender).
    Result-row guard: rejects lines that start with a place number AND contain a time.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if _RELAY_KEYWORDS.search(stripped) or _DIVING_KEYWORDS.search(stripped):
        return False
    m = re.match(r"^(\d{1,2})\b", stripped)
    if m and int(m.group(1)) <= 64 and re.search(_TIME_PAT, stripped):
        return False
    return (
        bool(_MEN_EVENT_RE.search(stripped))
        or bool(_ALT_EVENT_RE.search(stripped))
        or bool(_WOMEN_EVENT_RE.search(stripped))
    )


def is_men_event_header(line: str) -> bool:
    """Return True if line is a men's (or bare) event header."""
    stripped = line.strip()
    if not stripped:
        return False
    if _RELAY_KEYWORDS.search(stripped) or _DIVING_KEYWORDS.search(stripped):
        return False
    if re.search(r"\bwomens?\b|\bwomen'?s?\b", stripped, re.IGNORECASE):
        return False
    m = re.match(r"^(\d{1,2})\b", stripped)
    if m and int(m.group(1)) <= 64 and re.search(_TIME_PAT, stripped):
        return False
    return bool(_MEN_EVENT_RE.search(stripped)) or bool(_ALT_EVENT_RE.search(stripped))


def is_women_event_header(line: str) -> bool:
    """Return True if line is explicitly a women's event header."""
    stripped = line.strip()
    if not stripped:
        return False
    if _RELAY_KEYWORDS.search(stripped) or _DIVING_KEYWORDS.search(stripped):
        return False
    m = re.match(r"^(\d{1,2})\b", stripped)
    if m and int(m.group(1)) <= 64 and re.search(_TIME_PAT, stripped):
        return False
    if re.search(r"\bwomens?\b|\bwomen'?s?\b", stripped, re.IGNORECASE):
        return bool(_WOMEN_EVENT_RE.search(stripped)) or bool(
            re.search(r"event\s+\d+\s+women'?s?\s+\d+", stripped, re.IGNORECASE)
        )
    return False


# ── Result row parsing ────────────────────────────────────────────────────────

def parse_place_and_time(line: str) -> tuple[int | None, str | None]:
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
    for t in times:
        sec = time_to_seconds(t)
        if sec is not None and sec >= 10:
            return place, t
    if times:
        return place, times[0]
    return None, None


# ── Team score parsing ────────────────────────────────────────────────────────

_TEAM_SCORE_HEADER_RE = re.compile(
    r"(?:team|men'?s?|women'?s?)\s+(?:final\s+)?(?:team\s+)?(?:scores?|standings?|results?)",
    re.IGNORECASE,
)


def parse_team_scores(
    pages: list[str], conference: str, source_file: str, gender: str = "men"
) -> list[dict]:
    """
    Scan pages for a team standings block matching `gender`.
    Returns list of dicts: {Conference, Team, Team_Score, Source_File, Gender}
    """
    results = []
    in_team_section = False
    current_section_gender = "unknown"

    for page_text in pages:
        for line in page_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            if _TEAM_SCORE_HEADER_RE.search(stripped):
                in_team_section = True
                if re.search(r"women'?s?", stripped, re.IGNORECASE):
                    current_section_gender = "women"
                elif re.search(r"\bmen'?s?\b", stripped, re.IGNORECASE):
                    current_section_gender = "men"
                else:
                    current_section_gender = gender
                continue

            if not in_team_section:
                continue

            # Section switch inside standings block
            if re.search(r"women'?s?\s+(team|final|standing)", stripped, re.IGNORECASE):
                current_section_gender = "women"
                continue
            if re.search(r"\bmen'?s?\s+(team|final|standing)", stripped, re.IGNORECASE):
                current_section_gender = "men"
                continue

            if current_section_gender != gender:
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
                    "Gender":      gender,
                })

    return results
