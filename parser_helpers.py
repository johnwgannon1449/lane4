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

def _words_to_text(words: list[dict], y_tolerance: float = 4.0) -> str:
    """Reconstruct page text from a list of pdfplumber word dicts, grouping by y-position."""
    if not words:
        return ""
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[str] = []
    current_line: list[str] = [words[0]["text"]]
    current_top: float = words[0]["top"]
    for w in words[1:]:
        if abs(w["top"] - current_top) <= y_tolerance:
            current_line.append(w["text"])
        else:
            lines.append(" ".join(current_line))
            current_line = [w["text"]]
            current_top = w["top"]
    if current_line:
        lines.append(" ".join(current_line))
    return "\n".join(lines)


def _detect_column_splits(words: list[dict], page_width: float) -> list[float]:
    """
    Return x-coordinates of column boundaries (empty list = single column).

    Uses a 50-bin x0 histogram.  A run of ≥ 3 consecutive bins with fewer
    than 5 % of the peak bin count, located in the middle 25–75 % of the
    page, is treated as an inter-column gap.  At most 2 splits are returned
    (= max 3 columns).
    """
    if not words or page_width <= 0:
        return []

    n_bins = 50
    bin_w = page_width / n_bins
    hist = [0] * n_bins
    for w in words:
        idx = min(int(w["x0"] / bin_w), n_bins - 1)
        hist[idx] += 1

    peak = max(hist) if hist else 0
    if peak == 0:
        return []
    empty_thresh = max(2, int(peak * 0.05))   # < 5 % of peak → "empty"

    # Middle region: 25 % – 75 % of page width
    lo_bin = int(n_bins * 0.25)
    hi_bin = int(n_bins * 0.75)

    splits: list[float] = []
    in_gap = False
    gap_start: int = 0

    for i in range(lo_bin, hi_bin + 1):
        if hist[i] <= empty_thresh:
            if not in_gap:
                in_gap = True
                gap_start = i
        else:
            if in_gap:
                gap_len = i - gap_start
                if gap_len >= 3:                         # gap ≥ 6 % page width
                    gap_mid = (gap_start + i - 1) / 2 * bin_w
                    # Merge splits that are very close (<30 px)
                    if splits and abs(gap_mid - splits[-1]) < 30:
                        splits[-1] = (splits[-1] + gap_mid) / 2
                    else:
                        splits.append(gap_mid)
                in_gap = False

    # Cap at 2 boundaries (3 columns max); too many gaps = noise
    return splits[:2]


def _extract_page_columns(page) -> tuple[list[str], int]:
    """
    Extract text from a pdfplumber page, separating multi-column layouts.

    Returns (column_texts, n_columns).
    If no column split is detected, returns ([extract_text()], 1).
    If columns are detected, returns text for each column in left→right order.
    """
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    if not words:
        return [page.extract_text() or ""], 1

    splits = _detect_column_splits(words, page.width)
    if not splits:
        return [_words_to_text(words)], 1

    boundaries = [0.0] + splits + [page.width]
    n_cols = len(boundaries) - 1
    col_texts: list[str] = []
    for i in range(n_cols):
        col_words = [w for w in words if boundaries[i] <= w["x0"] < boundaries[i + 1]]
        col_texts.append(_words_to_text(col_words))
    return col_texts, n_cols


def extract_text_pymupdf(pdf_path: str) -> list[str]:
    import fitz
    pages = []
    doc = fitz.open(pdf_path)
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return pages


def extract_pages_ex(pdf_path: str) -> tuple[list[str], int]:
    """
    Extract text from a PDF with automatic multi-column detection.

    Returns (pages, multicolumn_page_count).

    For multi-column pages the columns are concatenated in left→right order
    so that the calling state machine processes each column fully before
    moving to the next (avoiding cross-column interleaving).
    """
    try:
        import pdfplumber
        pages: list[str] = []
        mc_count = 0
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                col_texts, n_cols = _extract_page_columns(page)
                if n_cols > 1:
                    mc_count += 1
                # Join columns with a blank separator line so the state
                # machine doesn't bleed one column's header into another.
                pages.append("\n\n".join(col_texts))
        if sum(1 for p in pages if len(p.strip()) > 50) == 0:
            raise ValueError("pdfplumber returned mostly empty pages")
        return pages, mc_count
    except Exception as e:
        try:
            return extract_text_pymupdf(pdf_path), 0
        except Exception as e2:
            raise RuntimeError(
                f"Both pdfplumber ({e}) and PyMuPDF ({e2}) failed"
            )


def extract_pages(pdf_path: str) -> list[str]:
    """Backward-compatible wrapper — returns pages only."""
    pages, _ = extract_pages_ex(pdf_path)
    return pages


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


# ── Final-section (A/B/C Final) detection ─────────────────────────────────────

# Standalone or embedded labels indicating which heat of finals this section is.
# Must be specific enough to not match "Final Day" or "Friday Final Results".

_A_FINAL_RE = re.compile(
    r"""
    \b(?:
        a\s*[-–]\s*final            # "A - Final", "A-Final"
        | a\s+final\b               # "A Final"
        | championship\s+final      # "Championship Final"
        | champion\s+final          # "Champion Final"
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_B_FINAL_RE = re.compile(
    r"""
    \b(?:
        b\s*[-–]\s*final            # "B - Final", "B-Final"
        | b\s+final\b               # "B Final"
        | consolation\s+final       # "Consolation Final"
        | consol\.?\s+final         # "Consol. Final"
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_C_FINAL_RE = re.compile(
    r"\b(?:c\s*[-–]\s*final|c\s+final)\b",
    re.IGNORECASE,
)

# Place offset per final type (displayed place → actual overall place)
# B Final:  place 1-8 in PDF  → actual overall places  9-16
# A Final:  place 1-8 in PDF  → actual overall places  1-8
# C Final:  place 1-8 in PDF  → actual overall places 17-24
FINAL_PLACE_OFFSETS: dict[str, int] = {
    "A":       0,
    "B":       8,
    "C":      16,
    "unknown": 0,   # default: treat as A-Final
}


def detect_final_type(line: str) -> str | None:
    """
    Detect whether `line` contains (or is) an A/B/C Final section label.

    Returns 'A', 'B', 'C', or None.

    Deliberately strict: will not match generic uses of "final" (e.g. "Friday
    Final Results", "Final Day") because those don't include the letter prefix
    or "championship / consolation" keywords.

    Fast-path: returns None immediately if the word "final" isn't in the line
    (case-insensitive), avoiding three regex searches on the vast majority of
    result rows.
    """
    stripped = line.strip()
    if not stripped:
        return None
    if "final" not in stripped.lower():
        return None
    if _C_FINAL_RE.search(stripped):
        return "C"
    if _B_FINAL_RE.search(stripped):
        return "B"
    if _A_FINAL_RE.search(stripped):
        return "A"
    return None


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
    """
    Parse a result row into (place, time_string).

    Rules:
    - Line must start with a 1–2 digit place number (1–64).
    - Line must contain at least one alphabetic word of ≥ 2 characters after
      the place number (swimmer name or school code).  This guards against
      distance-event split lines such as "24.36 27.51 28.02 28.55" which are
      pure numbers and must not be treated as result rows.
    - The returned time is the first time-shaped token with ≥ 10 seconds.
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
    # Split-line guard: real result rows always contain a swimmer name or school
    # abbreviation (≥ 2 consecutive letters).  Pure-number split lines don't.
    after_place = line[m.end():]
    if not re.search(r"[A-Za-z]{2,}", after_place):
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


# ── Loose event matching for second-pass recovery ─────────────────────────────

# Maps canonical event → (stroke_key, distance_string)
_CANONICAL_TO_STROKE_DIST: dict[str, tuple[str, str]] = {
    "50 Free":    ("free",   "50"),
    "100 Free":   ("free",   "100"),
    "200 Free":   ("free",   "200"),
    "500 Free":   ("free",   "500"),
    "1000 Free":  ("free",   "1000"),
    "1650 Free":  ("free",   "1650"),
    "100 Back":   ("back",   "100"),
    "200 Back":   ("back",   "200"),
    "100 Breast": ("breast", "100"),
    "200 Breast": ("breast", "200"),
    "100 Fly":    ("fly",    "100"),
    "200 Fly":    ("fly",    "200"),
    "200 IM":     ("im",     "200"),
    "400 IM":     ("im",     "400"),
}

# Per-stroke keyword patterns used in the loose scan
_LOOSE_STROKE_RE: dict[str, re.Pattern] = {
    "free":   re.compile(r"freestyle|(?<!\w)free(?!\w)", re.IGNORECASE),
    "back":   re.compile(r"backstroke|(?<!\w)back(?!\w)", re.IGNORECASE),
    "breast": re.compile(r"breaststroke|(?<!\w)breast(?!\w)", re.IGNORECASE),
    "fly":    re.compile(r"butterfly|(?<!\w)fly(?!\w)", re.IGNORECASE),
    "im":     re.compile(r"individual\s+medley|medley(?!\s+relay)|\bim\b", re.IGNORECASE),
}


def loose_event_match(line: str, canonical: str, gender: str = "both") -> bool:
    """
    Loosely match a line against a specific canonical event name.

    More permissive than is_event_header_any:
      - Does not require a specific prefix structure (Event N, Men's, etc.)
      - Only needs the correct distance AND stroke keyword on the line
      - Still rejects result rows, relays, and diving

    gender: 'men'   → reject lines with explicit women's label
            'women' → reject lines with explicit men's (but not women's) label
            'both'  → accept any
    """
    stripped = line.strip()
    if not stripped:
        return False

    lower = stripped.lower()

    # Hard rejects
    if _RELAY_KEYWORDS.search(lower) or _DIVING_KEYWORDS.search(lower):
        return False

    # Reject result rows: small leading place number + a time
    m = re.match(r"^(\d{1,2})\b", stripped)
    if m and int(m.group(1)) <= 64 and re.search(_TIME_PAT, lower):
        return False

    # Gender filter
    has_women_label = bool(re.search(r"\bwomens?\b|\bwomen'?s\b", lower))
    has_men_label   = bool(re.search(r"\bmens?\b|\bmen'?s\b", lower)) and not has_women_label

    if gender == "men" and has_women_label:
        return False
    if gender == "women" and has_men_label and not has_women_label:
        return False

    # Must contain the canonical distance as a word boundary
    stroke_key, dist = _CANONICAL_TO_STROKE_DIST[canonical]
    if not re.search(r"(?<!\d)" + re.escape(dist) + r"(?!\d)", lower):
        return False

    # Must contain the stroke keyword
    if not _LOOSE_STROKE_RE[stroke_key].search(lower):
        return False

    return True


# ── Team score parsing ────────────────────────────────────────────────────────

# Broad header regex — catches many formats seen in real championship PDFs
_TEAM_SCORE_HEADER_RE = re.compile(
    r"""
    (?:
        \bteam\s+(?:final\s+)?(?:scores?|standings?|rankings?|results?|points?)\b
        | \bfinal\s+(?:team\s+)?(?:scores?|standings?|rankings?|results?|points?)\b
        | \b(?:men'?s?|women'?s?)\s*[-–]?\s*(?:team\s+)?(?:final\s+)?
          (?:scores?|standings?|rankings?|results?|points?)\b
        | \boverall\s+(?:team\s+)?(?:scores?|standings?|rankings?)\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Header/column label lines to skip inside a standings block
_TEAM_SCORE_SKIP_RE = re.compile(
    r"^\s*(?:rank|place|pos|#|team|school|points?|score|pts)\s*$",
    re.IGNORECASE,
)

# Words that are definitely NOT team names
_TEAM_NAME_SKIP_WORDS = frozenset([
    "team", "place", "score", "school", "rank", "points", "pts",
    "total", "women", "mens", "women's", "men's",
    "conference", "championship", "standings", "final",
])


def _is_valid_team_name(name: str) -> bool:
    """Return True if `name` looks like a real team/school name."""
    if not name or len(name) < 3 or len(name) > 60:
        return False
    if name.lower() in _TEAM_NAME_SKIP_WORDS:
        return False
    if any(kw in name.lower() for kw in _TEAM_NAME_SKIP_WORDS):
        return False
    # Must start with a letter
    if not re.match(r"[A-Za-z]", name):
        return False
    return True


def _parse_score_line(stripped: str) -> tuple[str | None, str | None]:
    """
    Attempt to extract (team_name, score) from a standings line.

    Tries multiple formats:
      1. "1  Michigan            1234.5"   (rank + name + score)
      2. "Michigan               987.0"    (name + score)
      3. "1. Michigan  1234"              (rank with punctuation)
      4. Tab/multi-space delimited

    Returns (None, None) if line doesn't look like a score row.
    """
    if not stripped or len(stripped) < 5:
        return None, None

    # Pattern A: optional rank (1-3 digits + separator) then name then score
    m = re.match(
        r"^(?:\d{1,3}\s*[.):\-]?\s+)?(.+?)\s{2,}(\d{1,5}(?:\.\d{1,2})?)\s*$",
        stripped,
    )
    if m:
        name  = m.group(1).strip()
        score = m.group(2).strip()
        # Strip leading rank from name if still present
        name = re.sub(r"^\d{1,3}\s*[.):\-]?\s*", "", name).strip()
        if _is_valid_team_name(name):
            try:
                s = float(score)
                if s > 0:
                    return name, score
            except ValueError:
                pass

    # Pattern B: tab-delimited
    parts = re.split(r"\t", stripped)
    if len(parts) >= 2:
        name_part  = re.sub(r"^\d{1,3}\s*[.):\-]?\s*", "", parts[0]).strip()
        score_part = parts[-1].strip()
        m_score = re.fullmatch(r"\d{1,5}(?:\.\d{1,2})?", score_part)
        if m_score and _is_valid_team_name(name_part):
            try:
                if float(score_part) > 0:
                    return name_part, score_part
            except ValueError:
                pass

    # Pattern C: single space between name and integer score (tighter)
    m = re.match(
        r"^(?:\d{1,3}\s+)?([A-Za-z][A-Za-z\s,.'&\-()+]{2,50}?)\s+(\d{2,5}(?:\.\d{1,2})?)\s*$",
        stripped,
    )
    if m:
        name  = m.group(1).strip()
        score = m.group(2).strip()
        if _is_valid_team_name(name):
            try:
                s = float(score)
                if s > 0:
                    return name, score
            except ValueError:
                pass

    return None, None


def _detect_score_section_gender(header_line: str, default: str) -> str:
    """Infer gender from a team score section header line."""
    lower = header_line.lower()
    if re.search(r"\bwomen'?s?\b", lower):
        return "women"
    if re.search(r"\bmen'?s?\b", lower):
        return "men"
    return default


def parse_team_scores(
    pages: list[str],
    conference: str,
    source_file: str,
    gender: str = "men",
    reverse_pages: bool = False,
) -> list[dict]:
    """
    Scan pages for a team standings block matching `gender`.

    Args:
        pages:        List of page text strings.
        conference:   Conference name for output rows.
        source_file:  Filename for output rows.
        gender:       'men' or 'women' — which block to extract.
        reverse_pages: If True, scan from the last page backward (end-of-meet strategy).

    Returns list of dicts: {Conference, Team, Team_Score, Source_File, Gender}
    """
    results: list[dict] = []
    in_team_section = False
    current_section_gender = "unknown"
    consecutive_misses = 0   # stop collecting after N non-score lines in a row
    MAX_MISSES = 12

    scan_pages = list(reversed(pages)) if reverse_pages else pages

    for page_text in scan_pages:
        lines = page_text.splitlines()
        if reverse_pages:
            lines = list(reversed(lines))

        for line in lines:
            stripped = line.strip()
            if not stripped:
                if in_team_section:
                    consecutive_misses += 1
                continue

            # Detect a team score section header
            if _TEAM_SCORE_HEADER_RE.search(stripped):
                in_team_section = True
                consecutive_misses = 0
                current_section_gender = _detect_score_section_gender(stripped, gender)
                continue

            # In-block gender switches
            if in_team_section:
                if re.search(r"\bwomen'?s?\s*(team|final|standing|score|rank)", stripped, re.IGNORECASE):
                    current_section_gender = "women"
                    consecutive_misses = 0
                    continue
                if re.search(r"\bmen'?s?\s*(team|final|standing|score|rank)", stripped, re.IGNORECASE):
                    current_section_gender = "men"
                    consecutive_misses = 0
                    continue

            if not in_team_section:
                continue

            # Skip column headers inside the block
            if _TEAM_SCORE_SKIP_RE.match(stripped):
                continue

            # Stop collecting if we've drifted too far from the last score row
            if consecutive_misses >= MAX_MISSES:
                in_team_section = False
                consecutive_misses = 0
                continue

            if current_section_gender != gender and current_section_gender != "unknown":
                consecutive_misses += 1
                continue

            name, score = _parse_score_line(stripped)
            if name and score:
                results.append({
                    "Conference":  conference,
                    "Team":        name,
                    "Team_Score":  score,
                    "Source_File": source_file,
                    "Gender":      gender,
                })
                consecutive_misses = 0
            else:
                consecutive_misses += 1

        # If we found results scanning this page and are in reverse mode, stop
        if reverse_pages and results:
            break

    return results
