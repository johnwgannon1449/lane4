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
        "all",   # e.g. "results_-_all__1_.pdf" version/scope word, not a conference signal
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
    ("b1g",           "Big Ten"),
    ("b10",           "Big Ten"),
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
    ("mpsf",          "MPSF"),
    ("pcsc",          "PCSC"),
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
        if re.fullmatch(r"\d", t):
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

_GENDER_MEN_EVENT_RE   = re.compile(r"\bmen'?s?\s+\d+")
_GENDER_WOMEN_EVENT_RE = re.compile(r"\bwomen'?s?\s+\d+")
_GENDER_MEN_SECT_RE    = re.compile(r"\bmen'?s?\s+(?:swimming|results|individual|championship)")
_GENDER_WOMEN_SECT_RE  = re.compile(r"\bwomen'?s?\s+(?:swimming|results|individual|championship)")


def _gender_counts(text: str) -> tuple[int, int]:
    """Return (men_total, women_total) gender signal counts for a text sample."""
    low = text.lower()
    men   = len(_GENDER_MEN_EVENT_RE.findall(low))   + len(_GENDER_MEN_SECT_RE.findall(low))   * 3
    women = len(_GENDER_WOMEN_EVENT_RE.findall(low)) + len(_GENDER_WOMEN_SECT_RE.findall(low)) * 3
    return men, women


def detect_file_gender_from_content(pages: list[str]) -> str:
    """
    Classify a PDF as 'men', 'women', 'combined', or 'unknown' from its text.

    Samples both the front and back of the file to handle HY-TEK combined PDFs
    that list ALL women's events first (pages 1-N) followed by ALL men's events
    (pages N+1 to end).  Sampling only the first 8 pages would mis-classify
    such files as women-only.

    Strategy:
      1. Sample first 8 pages — catches the more common layout where the gender
         is obvious early on.
      2. Sample last 8 pages — catches the all-women-first layout.
      3. Union the signals: if both genders appear in either sample → combined.
    """
    n = len(pages)
    front_text = " ".join(pages[:8])
    back_text  = " ".join(pages[max(0, n - 8):])

    men_f, women_f = _gender_counts(front_text)
    men_b, women_b = _gender_counts(back_text)

    men_total   = men_f   + men_b
    women_total = women_f + women_b

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

    Primary pass: 50-bin x0 histogram; a run of ≥ 3 consecutive bins with
    fewer than 5 % of the peak bin count, in the middle 25–75 % of the page,
    is treated as an inter-column gap.  At most 2 splits are returned
    (= max 3 columns).

    Fallback pass (activates only when the primary pass finds nothing):
    Restricts the search to the central 40–60 % of the page and requires
    only ≥ 2 consecutive empty bins.  Nearby gap clusters are merged with a
    50 px tolerance before checking that both resulting columns contain at
    least 15 words.  This catches distance-event pages where split-row times
    from the adjacent column partially fill what would otherwise be a clean
    3-bin gap, reducing the observable gap to just 2 consecutive empty bins.
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

    def _find_splits(lo: int, hi: int, min_gap: int, merge_px: float) -> list[float]:
        result: list[float] = []
        in_gap = False
        gap_start = 0
        for i in range(lo, hi + 1):
            if hist[i] <= empty_thresh:
                if not in_gap:
                    in_gap = True
                    gap_start = i
            else:
                if in_gap:
                    gap_len = i - gap_start
                    if gap_len >= min_gap:
                        gap_mid = (gap_start + i - 1) / 2 * bin_w
                        if result and abs(gap_mid - result[-1]) < merge_px:
                            result[-1] = (result[-1] + gap_mid) / 2
                        else:
                            result.append(gap_mid)
                    in_gap = False
        return result[:2]

    # Primary pass: 25–75 % zone, ≥ 3 consecutive empty bins, 30 px merge
    lo_bin = int(n_bins * 0.25)
    hi_bin = int(n_bins * 0.75)
    splits = _find_splits(lo_bin, hi_bin, min_gap=3, merge_px=30)

    if not splits:
        # Fallback: 40–60 % central zone only, ≥ 2 consecutive empty bins,
        # 50 px merge (wide enough to fuse two adjacent 2-bin clusters).
        lo_center = int(n_bins * 0.40)
        hi_center = int(n_bins * 0.60)
        candidates = _find_splits(lo_center, hi_center, min_gap=2, merge_px=50)
        for split_x in candidates:
            left_n  = sum(1 for w in words if w["x0"] <  split_x)
            right_n = sum(1 for w in words if w["x0"] >= split_x)
            if left_n >= 15 and right_n >= 15:
                splits.append(split_x)
        splits = splits[:2]

    return splits


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


# ── CID ligature normalization ────────────────────────────────────────────────
# pdfplumber renders unresolved font ligatures as "(cid:NNN)".
# HY-TEK Meet Manager PDFs commonly use the "fl" ligature in "butterfly",
# which renders as "(cid:976)" and silently breaks all butterfly event header
# detection.  Normalise before any pattern matching.
# CID → character mappings are FONT-SPECIFIC (not globally standardised).
# These values are correct for the HY-TEK Meet Manager 8 fonts observed in
# the 2026 championship PDFs.  "butterfly" = "Butter" + CID_976 + "ly" where
# CID_976 encodes the single glyph "f" (not the "fl" ligature as one might
# expect from Type-1 ligature tables).
_CID_MAP: dict[str, str] = {
    "(cid:976)": "f",    # 'f' glyph in HY-TEK MM8 font → e.g. "Butterfly"
    "(cid:977)": "fi",   # fi ligature
    "(cid:978)": "ffl",  # ffl ligature
    "(cid:979)": "ffi",  # ffi ligature
    "(cid:980)": "ff",   # ff ligature
    "(cid:948)": "d",    # sometimes seen in older HY-TEK fonts
}
_CID_RE = re.compile(r"\(cid:\d+\)")


def normalize_cid(text: str) -> str:
    """Replace CID ligature escape sequences with their character equivalents.

    Unknown CID codes are replaced with an empty string rather than left
    as-is, because leaving them intact would corrupt the surrounding word
    (e.g. "(cid:999)ly" instead of "fly") and still break pattern matching.
    """
    def _sub(m: re.Match) -> str:
        return _CID_MAP.get(m.group(0), "")
    return _CID_RE.sub(_sub, text)


def extract_text_pymupdf(pdf_path: str) -> list[str]:
    import fitz
    pages = []
    doc = fitz.open(pdf_path)
    for page in doc:
        pages.append(normalize_cid(page.get_text()))
    doc.close()
    return pages


def extract_pages(pdf_path: str) -> list[str]:
    """
    Simple page-by-page text extraction — the default for normal single-column PDFs.

    Uses pdfplumber's standard extract_text() with no column reordering.
    Falls back to PyMuPDF on failure.
    """
    try:
        import pdfplumber
        pages: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                pages.append(normalize_cid(page.extract_text() or ""))
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


_STANDALONE_TIME_RE = re.compile(
    # Numeric time optionally followed by a short suffix annotation
    # (e.g. "1:47.73*", "48.33@", "15:17.12 q", "1:59.02 (B)").
    # The suffix is capped at 30 chars to prevent matching swimmer names.
    r"^(?:(?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{2}|\d{2,3}\.\d{2})"
    r"(?:[ \t]*[*!@#A-Za-z()[\]]{1,30})?$"
)
_INLINE_TIME_RE = re.compile(
    r"(?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{2}|\d{2,3}\.\d{2}"
)

# Captures suffix annotations that may trail a numeric time value.
# Stops at double-whitespace, end-of-string, or the start of a new digit token.
_TIME_SUFFIX_RE = re.compile(
    r"[ \t]*([*!@#A-Za-z()\[\]][*!@#A-Za-z0-9 ()\[\]]{0,30}?)[ \t]*"
    r"(?=[ \t]{2,}|\s*$)"
)


def extract_time_and_suffix(token: str) -> tuple[str, str]:
    """
    Split a raw time token into its numeric portion and any trailing annotation.

    Examples
    --------
    "1:45.32*"       -> ("1:45.32", "*")
    "2:01.55!"       -> ("2:01.55", "!")
    "4:22.10A"       -> ("4:22.10", "A")
    "15:17.12 q"     -> ("15:17.12", "q")
    "48.33@"         -> ("48.33", "@")
    "1:59.02 (B)"    -> ("1:59.02", "(B)")
    "1:47.88# NCAA B"-> ("1:47.88", "# NCAA B")
    "1:45.32"        -> ("1:45.32", "")
    """
    token = token.strip()
    m = re.match(
        r"((?:\d{1,2}:)?\d{1,2}:\d{2}\.\d{2}|\d{1,3}\.\d{2})"
        r"([ \t]*[*!@#A-Za-z()[\]][^\n]*)?$",
        token,
    )
    if m:
        numeric = m.group(1)
        suffix  = (m.group(2) or "").strip()
        return numeric, suffix
    return token, ""


def _group_words_by_y(word_list: list[dict], y_tol: float = 4.0) -> dict:
    """Group pdfplumber word dicts into rows keyed by rounded y-coordinate."""
    rows: dict[float, list[dict]] = {}
    for w in word_list:
        y_key = round(w["top"] / y_tol) * y_tol
        rows.setdefault(y_key, []).append(w)
    return rows


def _row_text(words: list[dict]) -> str:
    return " ".join(w["text"] for w in sorted(words, key=lambda x: x["x0"])).strip()


def _should_row_pair(col1_rows: dict) -> bool:
    """
    Return True when col1 has ≥2 rows that look like swimmer names with NO
    inline time — meaning the times must be in col2 and need to be paired.

    A row is an "orphaned name" when it:
      • starts with a 1-2 digit place number then a capitalised last name
        followed by a comma  (e.g. "11 Smith, John 19SOME-NC")
      • contains NO time pattern

    We intentionally check col1 rather than col2 to avoid false-positive
    triggers from split-time fragments (e.g. "48.92") that look like
    standalone times but are really per-50 split data lines.
    """
    _ORPHAN_NAME_RE = re.compile(r"^\d{1,2}\s+[A-Z][a-z][A-Za-z'.\-]+,")
    count = sum(
        1 for rw in col1_rows.values()
        if (
            _ORPHAN_NAME_RE.match(_row_text(rw))
            and not _INLINE_TIME_RE.search(_row_text(rw))
        )
    )
    return count >= 2


def _row_pair_page(page) -> tuple[str, int, bool]:
    """
    Attempt ODAC row-pairing for a single page.

    Returns (page_text, n_cols, was_row_paired).

    Row-pairing only activates when EXACTLY one column split is detected
    (true 2-column layout) AND col1 has ≥2 orphaned swimmer name rows with
    no inline time.  Three-column pages are concatenated normally so that
    col3's complete-result rows are not corrupted by merging col2 times onto
    the same text line.

    For each y-row on a row-paired page:
      • col1 text has no inline time AND col2 text is a standalone time
        → merge into one line  ("11 Smith, John 19SOME-NC 59.63")
      • Otherwise emit col1 row then col2 row as separate lines.
    """
    words = page.extract_words(x_tolerance=3, y_tolerance=3)
    if not words:
        return page.extract_text() or "", 1, False

    splits = _detect_column_splits(words, page.width)
    if not splits:
        return _words_to_text(words), 1, False

    # ── 3-column pages: smart pairing of col1 (names) + col2 (times). ─────────
    # When there are 2 splits the page has 3 columns.  The common ODAC pattern
    # is col1=swimmer-names, col2=times-only, col3=consolation/complete results.
    # Naively concatenating all three loses the name→time association for col1.
    # Detect the orphan-name case and pair col1+col2 by y-coordinate; col3 is
    # appended normally.  When col1 has NO orphans fall through to plain order.
    if len(splits) > 1:
        boundaries = [0.0] + splits + [page.width]
        col_buckets = []
        for i in range(len(boundaries) - 1):
            col_buckets.append(
                [w for w in words if boundaries[i] <= w["x0"] < boundaries[i + 1]]
            )
        col1r = _group_words_by_y(col_buckets[0])
        col2r = _group_words_by_y(col_buckets[1])
        if _should_row_pair(col1r):
            # Pair col1 names with col2 times, then emit col3+ sequentially.
            all_y12 = sorted(set(list(col1r.keys()) + list(col2r.keys())))
            merged12: list[str] = []
            for y in all_y12:
                c1t = _row_text(col1r.get(y, []))
                c2t = _row_text(col2r.get(y, []))
                if c1t and c2t:
                    c2_is_time = bool(_STANDALONE_TIME_RE.match(c2t))
                    if c2_is_time and not _INLINE_TIME_RE.search(c1t):
                        merged12.append(c1t + " " + c2t)
                    else:
                        merged12.append(c1t)
                        merged12.append(c2t)
                elif c1t:
                    merged12.append(c1t)
                elif c2t:
                    merged12.append(c2t)
            rest = [_words_to_text(col_buckets[i]) for i in range(2, len(col_buckets))]
            parts = ["\n".join(merged12)] + rest
            return "\n\n".join(p for p in parts if p), len(splits) + 1, True
        else:
            col_texts = [_words_to_text(cb) for cb in col_buckets]
            return "\n\n".join(col_texts), len(splits) + 1, False

    split_x = splits[0]
    col1_words = [w for w in words if w["x0"] < split_x]
    col2_words = [w for w in words if w["x0"] >= split_x]

    col1_rows = _group_words_by_y(col1_words)
    col2_rows = _group_words_by_y(col2_words)

    if not _should_row_pair(col1_rows):
        c1 = _words_to_text(col1_words)
        c2 = _words_to_text(col2_words)
        return (c1 + "\n\n" + c2).strip(), 2, False

    all_y = sorted(set(list(col1_rows.keys()) + list(col2_rows.keys())))
    merged: list[str] = []
    for y in all_y:
        c1_txt = _row_text(col1_rows.get(y, []))
        c2_txt = _row_text(col2_rows.get(y, []))

        if c1_txt and c2_txt:
            c2_is_time = bool(_STANDALONE_TIME_RE.match(c2_txt))
            c1_has_time = bool(_INLINE_TIME_RE.search(c1_txt))
            if c2_is_time and not c1_has_time:
                merged.append(c1_txt + " " + c2_txt)
            else:
                # Mixed-row fallback: col2 may start with a time that belongs
                # to the col1 swimmer, followed by a complete col2 swimmer row
                # (same y-bucket due to 1-2 pt vertical misalignment between
                # the two columns).  Detect: first token is a standalone time
                # and the remainder opens with digit+space+capital (a swimmer).
                c2_words_list = c2_txt.split()
                if (
                    not c1_has_time
                    and len(c2_words_list) >= 2
                    and _STANDALONE_TIME_RE.match(c2_words_list[0])
                    and re.match(r"^\d{1,2}\s+[A-Z]", " ".join(c2_words_list[1:]))
                ):
                    merged.append(c1_txt + " " + c2_words_list[0])
                    merged.append(" ".join(c2_words_list[1:]))
                else:
                    merged.append(c1_txt)
                    merged.append(c2_txt)
        elif c1_txt:
            merged.append(c1_txt)
        elif c2_txt:
            merged.append(c2_txt)

    return "\n".join(merged), 2, True


def _extract_pages_2col(pdf_path: str) -> tuple[list[str], list[dict]]:
    """
    ODAC-targeted 2-column extraction.

    Each page is split into at most 2 columns using the x0 histogram.
    Pages where B-Final names land in col1 with times in col2 are processed
    with row-pairing so names and times are merged before the state machine
    sees them.  All other pages use normal left-then-right column order.
    """
    import pdfplumber
    pages: list[str] = []
    col_debug: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text, n_cols, row_paired = _row_pair_page(page)
            pages.append(normalize_cid(text))
            col_debug.append({
                "Page_Number":            page_num,
                "Columns_Detected":       n_cols,
                "Event_Headers_Detected": "",
                "Notes": (
                    "2col_row_paired" if row_paired
                    else ("2col_mode" if n_cols > 1 else "1col_fallback")
                ),
            })
    return pages, col_debug


_PATRIOT_COL_BOUNDARIES: tuple[float, float] = (196.0, 400.0)
"""
Fixed column split x-positions for the Patriot League PDF.

Empirically measured across all 31 result pages (pages 4-34):
  col1 max x ≈ 182-194  |  col2 first x = 204.13  |  col3 first x ≈ 406-417

Boundaries are placed in the clear white-space gaps:
  col1: x  <  196  (A-Final / Women's results)
  col2: x in [196, 400)  (B-Final)
  col3: x >= 400  (C-Final)

The Patriot PDF's column gaps are only ~22-25 px wide, which is below the
≥3-bin (≥37 px) threshold in _detect_column_splits.  Hard-coding the
boundaries avoids the gap-detection failure that caused 30/34 pages to fall
back to mixed single-column mode.
"""


def _extract_pages_3col(pdf_path: str) -> tuple[list[str], list[dict]]:
    """
    Patriot-targeted 3-column extraction using fixed column boundaries.

    All pages are split at the two hard-coded x-positions in
    _PATRIOT_COL_BOUNDARIES.  Each column is extracted top-to-bottom and the
    three columns are concatenated with a blank-line separator, giving the
    state machine a clean, column-ordered stream:

        <col1 — A-Final / Women>

        <col2 — B-Final>

        <col3 — C-Final>
    """
    import pdfplumber
    b1, b2 = _PATRIOT_COL_BOUNDARIES
    pages: list[str] = []
    col_debug: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(x_tolerance=3, y_tolerance=3)
            if not words:
                pages.append(normalize_cid(page.extract_text() or ""))
                col_debug.append({
                    "Page_Number":            page_num,
                    "Columns_Detected":       1,
                    "Event_Headers_Detected": "",
                    "Notes":                  "no_words_fallback",
                })
                continue

            pw = page.width
            bounds = [0.0, b1, b2, pw]
            col_texts = []
            for i in range(3):
                col_words = [w for w in words
                             if bounds[i] <= w["x0"] < bounds[i + 1]]
                col_texts.append(_words_to_text(col_words))

            pages.append(normalize_cid("\n\n".join(col_texts)))
            col_debug.append({
                "Page_Number":            page_num,
                "Columns_Detected":       3,
                "Event_Headers_Detected": "",
                "Notes":                  f"3col_fixed splits=[{b1},{b2}]",
            })
    return pages, col_debug


def extract_pages_targeted(pdf_path: str, mode: str) -> tuple[list[str], list[dict]]:
    """
    Route extraction to the correct column mode.

    Returns (pages, col_debug_rows) where col_debug_rows is a list of
    per-page dicts (Page_Number, Columns_Detected, Event_Headers_Detected, Notes).

    mode:
      "normal"          — simple pdfplumber extract_text(), no column detection
      "multi_column_2"  — ODAC 2-column detection
      "multi_column_3"  — Patriot 3-column detection / partition
    """
    if mode == "multi_column_2":
        try:
            return _extract_pages_2col(pdf_path)
        except Exception:
            return extract_pages(pdf_path), []
    elif mode == "multi_column_3":
        try:
            return _extract_pages_3col(pdf_path)
        except Exception:
            return extract_pages(pdf_path), []
    else:
        return extract_pages(pdf_path), []


# ── Conference parse-mode registry ────────────────────────────────────────────

CONFERENCE_PARSE_MODE: dict[str, str] = {
    "ODAC":    "multi_column_2",
    "Patriot": "multi_column_3",
    "MPSF":    "multi_column_2",
    "PCSC":    "multi_column_2",
    # WAC: mixed single/multi-column layout — not safe for uniform multi_column_2;
    # remains on "normal" until a per-page adaptive mode is implemented.
    # All other conferences default to "normal"
}

# Per-file parse mode overrides (keyed by PDF filename stem, no extension).
# Priority: FILE_PARSE_MODE > CONFERENCE_PARSE_MODE > "normal"
# Use when individual files in a bundle have different column layouts.
FILE_PARSE_MODE: dict[str, str] = {
    # Ivy League: women's PDF is 2-column; men's PDF is single-column.
    # Conference-level mode cannot be used because it would apply to both files.
    "2026 Ivy Womens Conf": "multi_column_2",
    "2026 Ivy Mens Conf":   "normal",
}

# Events known to not be contested by certain conferences (used in coverage report).
LIKELY_NOT_CONTESTED: dict[str, set[str]] = {
    "ODAC":         {"1000 Free"},
    "Patriot":      {"1000 Free"},
    "Summit League": {"1000 Free"},
}


# ── Patriot League line-splitting preprocessing ───────────────────────────────
#
# The Patriot 3-col extraction joins words across three heat columns by
# y-coordinate.  This means one text line can contain multiple swimmer results
# side-by-side (e.g. "4 Perecinsky, Martin 19NAVY-MD 4:19.73 17 Magner, Reid J
# 18ARMY-MR 4:27.36") and embedded section labels ("B - Final", "C - Final").
# preprocess_lines_patriot() splits these merged lines so the state machine
# sees one swimmer result per line and section labels on their own lines.

# Matches the start of a swimmer result: optional *, 1-2 digit place, then a
# capitalised last name followed immediately by a comma.
# E.g. "4 Perecinsky," or "*4 Schreiner," but NOT "26.0" or "3:54.16".
_PATRIOT_SWIMMER_RE = re.compile(
    r"(?:(?<=\s)|^)(\*?\d{1,2})\s+([A-Z][a-z][A-Za-z'.\-]+,)"
)

# Matches a standalone heat-section label at the END of a chunk of text so we
# can split it onto its own line.  Anchored to end-of-string (with optional
# trailing whitespace / asterisk junk).
_PATRIOT_SECTION_TRAIL_RE = re.compile(
    r"\s*\b([ABC]\s*-\s*Final)\s*\*?\s*$",
    re.IGNORECASE,
)


def _patriot_split_text(text: str) -> list[str]:
    """
    Split a chunk of text that may contain trailing heat-section labels
    (e.g. "A - Final ... (#7 Men 500 Yard Free) C - Final") into the
    substantive part plus the section label on its own line.
    """
    text = text.strip()
    if not text:
        return []
    m = _PATRIOT_SECTION_TRAIL_RE.search(text)
    if m:
        before = text[: m.start()].strip()
        label = m.group(1).strip()
        parts = []
        if before:
            parts.append(before)
        parts.append(label)
        return parts
    return [text]


def _split_patriot_line(line: str) -> list[str]:
    """
    Split a single extracted Patriot text line into individual pieces:
    • swimmer result lines (one per swimmer)
    • section label lines  ("B - Final", "C - Final")
    • event header / split-data lines (passed through unchanged)

    Leading '*' on place numbers (e.g. "*4 Schreiner,") is stripped so that
    parse_place_and_time can see the bare digit.
    """
    line = line.strip()
    if not line:
        return []

    # Find all positions where a new swimmer result begins.
    starts: list[int] = []
    for m in _PATRIOT_SWIMMER_RE.finditer(line):
        place_str = m.group(1).lstrip("*")
        place = int(place_str)
        if 1 <= place <= 64:
            starts.append(m.start())

    if len(starts) == 0:
        # No swimmer result in this line — check for trailing section label only.
        return _patriot_split_text(line)

    parts: list[str] = []

    # Text before the first swimmer result (event header / split data / label).
    if starts[0] > 0:
        pre = line[: starts[0]].strip()
        if pre:
            parts.extend(_patriot_split_text(pre))

    # Extract each swimmer slice.
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(line)
        chunk = line[start:end].strip().lstrip("*")
        if chunk:
            parts.append(chunk)

    return parts


def preprocess_lines_patriot(lines: list[str]) -> list[str]:
    """
    Apply Patriot-specific line splitting to the flat line list produced by
    the 3-col extractor before the state machine processes it.

    Each merged line is split into individual swimmer results + section labels.
    """
    out: list[str] = []
    for line in lines:
        if "<<<PAGE_BREAK>>>" in line:
            out.append(line)
            continue
        out.extend(_split_patriot_line(line))
    return out


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

def parse_place_and_time(line: str) -> tuple[int | None, str | None, str]:
    """
    Parse a result row into (place, numeric_time, raw_suffix).

    Rules:
    - Line must start with a 1–2 digit place number (1–64).
    - Line must contain at least one alphabetic word of ≥ 2 characters after
      the place number (swimmer name or school code).  This guards against
      distance-event split lines such as "24.36 27.51 28.02 28.55" which are
      pure numbers and must not be treated as result rows.
    - The returned time is the numeric portion of the first time-shaped token
      with ≥ 10 seconds; any trailing annotation (e.g. *, !, @, A, (B)) is
      returned separately as raw_suffix.
    - Returns (None, None, "") when no valid place is found.
    - Returns (place, None, "") when a swimmer name is present but no time
      (caller should watch the next line for a deferred time).
    """
    line = line.strip()
    if not line:
        return None, None, ""
    # Strip leading cut-standard marks (I=invited, @=NCAA-B, #=NCAA-A,
    # !=meet-record, *=conference-record) that ODAC prefixes to result lines.
    line = re.sub(r"^[I@#!*]\s+(?=\d)", "", line)
    m = re.match(r"^(\d{1,2})\b", line)
    if not m:
        return None, None, ""
    place = int(m.group(1))
    if place < 1 or place > 64:
        return None, None, ""
    # Split-line guard: real result rows always contain a swimmer name or school
    # abbreviation (≥ 2 consecutive letters).  Pure-number split lines don't.
    after_place = line[m.end():]
    if after_place and after_place[0] == ":":
        return None, None, ""
    if not re.search(r"[A-Za-z]{2,}", after_place):
        return None, None, ""

    # Collect all valid times (≥ 10 s) and return the LAST one.
    # HY-TEK "Prelim Time  Finals Time" rows carry two times on the same line:
    #   "1 Smith, Ann SO School 1:02.34 1:01.89"
    #              prelim time ↑       ↑ finals time
    # Using the LAST valid time picks the finals time automatically.
    # For single-time rows (normal format) LAST == FIRST, so no change.
    last_valid_numeric: str | None = None
    last_valid_suffix:  str        = ""
    for tm in re.finditer(_TIME_PAT, line):
        numeric_time = tm.group(0)
        sec = time_to_seconds(numeric_time)
        if sec is None or sec < 10:
            continue
        rest = line[tm.end():]
        sfx_m = _TIME_SUFFIX_RE.match(rest)
        raw_suffix = sfx_m.group(1).strip() if (sfx_m and sfx_m.group(1)) else ""
        last_valid_numeric = numeric_time
        last_valid_suffix  = raw_suffix

    if last_valid_numeric is not None:
        return place, last_valid_numeric, last_valid_suffix

    # Fallback: last time found even if < 10 s (e.g. 50 Free in a distance meet)
    last_any_numeric: str | None = None
    last_any_suffix:  str        = ""
    for tm in re.finditer(_TIME_PAT, line):
        numeric_time = tm.group(0)
        rest = line[tm.end():]
        sfx_m = _TIME_SUFFIX_RE.match(rest)
        raw_suffix = sfx_m.group(1).strip() if (sfx_m and sfx_m.group(1)) else ""
        last_any_numeric = numeric_time
        last_any_suffix  = raw_suffix

    if last_any_numeric is not None:
        return place, last_any_numeric, last_any_suffix

    # No time found — return (place, None) so caller can watch the next line.
    return place, None, ""


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
