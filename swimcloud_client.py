"""
swimcloud_client.py — SwimCloud public API wrapper for Lane4.

Endpoints used (no auth required):
  Search:  GET https://www.swimcloud.com/api/search/?q=<name>&type=swimmer
  Times:   GET https://www.swimcloud.com/api/swimmers/<id>/profile_fastest_times/

Uses curl_cffi with Chrome impersonation to bypass Cloudflare's TLS fingerprinting
and bot-detection challenge even from datacenter IPs.
"""

import re
import time as _time
from curl_cffi import requests as cf_requests

_SESSION = None
_SESSION_BUILT = 0.0
_SESSION_TTL = 3600  # re-create session after 1 hour

_BASE = "https://www.swimcloud.com"

# SwimCloud stroke code → Lane4 suffix
_STROKE = {
    "1": "Free",
    "2": "Back",
    "3": "Breast",
    "4": "Fly",
    "5": "IM",
}

# (distance, stroke_code) → Lane4 event name (SCY only)
_EVENT_MAP = {
    (50,   "1"): "50 Free",
    (100,  "1"): "100 Free",
    (200,  "1"): "200 Free",
    (500,  "1"): "500 Free",
    (1000, "1"): "1000 Free",
    (1650, "1"): "1650 Free",
    (100,  "2"): "100 Back",
    (200,  "2"): "200 Back",
    (100,  "3"): "100 Breast",
    (200,  "3"): "200 Breast",
    (100,  "4"): "100 Fly",
    (200,  "4"): "200 Fly",
    (200,  "5"): "200 IM",
    (400,  "5"): "400 IM",
}


def _get_session() -> cf_requests.Session:
    global _SESSION, _SESSION_BUILT
    now = _time.time()
    if _SESSION is None or (now - _SESSION_BUILT) > _SESSION_TTL:
        s = cf_requests.Session(impersonate="chrome")
        _SESSION = s
        _SESSION_BUILT = now
    return _SESSION


def _get(url: str, params: dict | None = None, timeout: int = 20):
    s = _get_session()
    r = s.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r


def sec_to_time_str(t_sec: float) -> str:
    """Convert float seconds to Lane4 display format: '1:32.40' or '52.50'."""
    t_sec = round(float(t_sec), 2)
    mins = int(t_sec) // 60
    secs = t_sec - mins * 60
    if mins > 0:
        return f"{mins}:{secs:05.2f}"
    return f"{secs:.2f}"


def search_swimmers(name: str) -> list[dict]:
    """
    Search SwimCloud by name.

    Returns a list of dicts:
      {swimmer_id, display_name, team, grad_year, location, profile_url}
    Up to 10 results.
    """
    if not name or not name.strip():
        return []

    url = _BASE + "/api/search/"
    r = _get(url, params={"q": name.strip(), "type": "swimmer"})
    raw = r.json()

    results = []
    for item in raw:
        if item.get("doc_type") != "Swimmers":
            continue
        href = item.get("url", "")
        m = re.search(r"/swimmer/(\d+)", href)
        if not m:
            # try id field "swimmer.12345"
            id_field = item.get("id", "")
            m2 = re.search(r"swimmer\.(\d+)", id_field)
            if not m2:
                continue
            swimmer_id = m2.group(1)
        else:
            swimmer_id = m.group(1)

        results.append(
            {
                "swimmer_id":  swimmer_id,
                "display_name": item.get("primary_text") or item.get("name", ""),
                "team":         item.get("team", ""),
                "grad_year":    None,
                "location":     item.get("location", ""),
                "profile_url":  _BASE + "/swimmer/" + swimmer_id + "/",
            }
        )
        if len(results) >= 10:
            break

    return results


def fetch_profile_info(swimmer_id: str) -> dict:
    """
    Fetch structured profile info for a swimmer.
    Uses /api/swimmers/search/?swimmer_id=X and, if available,
    /api/swimmers/{id}/ for richer metadata.
    Falls back gracefully on any error; no field is required.

    Returned keys:
      swimmer_id, display_name, team, grad_year, profile_url,
      club_team, high_school, lsc
    (gender is populated separately from raw fastest_times records)
    """
    _fallback = {
        "swimmer_id":   swimmer_id,
        "display_name": "",
        "team":         "",
        "grad_year":    None,
        "profile_url":  _BASE + "/swimmer/" + swimmer_id + "/",
        "club_team":    None,
        "high_school":  None,
        "lsc":          None,
    }
    try:
        url = _BASE + "/api/swimmers/search/"
        r = _get(url, params={"swimmer_id": swimmer_id})
        data = r.json()
        results = data.get("results", [])
        if results:
            rec = results[0]
            if str(rec.get("id", "")) == str(swimmer_id):
                info = {
                    "swimmer_id":   str(rec.get("id", swimmer_id)),
                    "display_name": rec.get("display_name") or rec.get("name", ""),
                    "team":         rec.get("primary_team", "") or "",
                    "grad_year":    rec.get("gradhs"),
                    "profile_url":  _BASE + "/swimmer/" + str(swimmer_id) + "/",
                    # Additional enrichment fields — present only if SwimCloud returns them
                    "club_team":    rec.get("primary_team") or rec.get("club_team") or None,
                    "high_school":  rec.get("high_school") or rec.get("highschool") or None,
                    "lsc":          rec.get("lsc") or rec.get("lsc_name") or None,
                }
                # Try the swimmer detail endpoint for any extra metadata
                try:
                    detail_url = _BASE + f"/api/swimmers/{swimmer_id}/"
                    dr = _get(detail_url)
                    if dr.status_code == 200:
                        d = dr.json()
                        if not info["high_school"]:
                            info["high_school"] = d.get("high_school") or d.get("highschool") or None
                        if not info["lsc"]:
                            info["lsc"] = d.get("lsc") or d.get("lsc_name") or None
                        if not info["club_team"]:
                            info["club_team"] = d.get("primary_team") or d.get("club_team") or None
                except Exception:
                    pass
                return info
    except Exception:
        pass
    return _fallback


def fetch_fastest_times(swimmer_id: str) -> list[dict]:
    """
    Fetch profile_fastest_times for a swimmer.
    Returns raw list of SwimCloud time records.
    """
    url = _BASE + f"/api/swimmers/{swimmer_id}/profile_fastest_times/"
    r = _get(url)
    return r.json()


# ── Seed-PR filter constants ────────────────────────────────────────────────

_MIN_SEED_IMPROVEMENT = 0.15   # seconds — ignore trivially small differences

# Maximum believable improvement (eventtime − seedtime) per event.
# Seeds claiming bigger drops are treated as junk/typo entries.
_MAX_SEED_DROP: dict[str, float] = {
    "50 Free":    0.7,  "50 Back":    0.7,  "50 Breast":  0.7,  "50 Fly":    0.7,
    "100 Free":   1.2,  "100 Back":   1.2,  "100 Breast": 1.2,  "100 Fly":   1.2,
    "200 Free":   2.0,  "200 Back":   2.0,  "200 Breast": 2.0,  "200 Fly":   2.0,
    "200 IM":     2.0,
    "400 IM":     4.0,  "500 Free":   4.0,
    "1000 Free":  8.0,
    "1650 Free": 12.0,
}


def _seed_is_placeholder(seed_raw: str) -> bool:
    """
    Return True if the seed looks like a rounded placeholder rather than a real swim.

    Rejects:
      - no decimal part (e.g. "50")
      - decimal == "00" (e.g. "1:44.00", "55.00")
      - decimal == "50" (e.g. "1:44.50", "55.50")
    """
    s = str(seed_raw).strip()
    if "." not in s:
        return True
    hundredths = s.split(".")[-1]
    return hundredths in ("00", "50")


def extract_seed_prs(raw_times: list[dict], scy_bests: dict) -> list[dict]:
    """
    Identify seedtime values that may represent faster swims not captured in
    SwimCloud's verified results, applying a conservative multi-stage filter.

    Steps:
      1. Reject placeholder seeds (.00, .50, no decimal)
      2. Only consider seeds strictly faster than the verified eventtime
      3. Require at least 0.15 s improvement (ignore noise)
      4. Reject implausible drops (per _MAX_SEED_DROP)

    Returns a list sorted by improvement descending. These must never be
    auto-imported — they require explicit user confirmation.
    """
    potential: list[dict] = []

    for rec in raw_times:
        if rec.get("eventcourse") != "Y":
            continue
        if not rec.get("legal", True):
            continue

        dist   = rec.get("eventdistance")
        stroke = str(rec.get("eventstroke", ""))
        key    = (dist, stroke)
        event  = _EVENT_MAP.get(key)
        if not event:
            continue

        et_raw = rec.get("eventtime")
        st_raw = rec.get("seedtime")
        if not et_raw or not st_raw:
            continue

        # Step 1 — placeholder filter
        if _seed_is_placeholder(st_raw):
            continue

        try:
            et_sec = float(et_raw)
            st_sec = float(st_raw)
        except (TypeError, ValueError):
            continue

        if et_sec <= 0 or st_sec <= 0:
            continue

        # Step 2 — seed must be strictly faster
        if st_sec >= et_sec:
            continue

        # Step 3 — minimum significant improvement
        improvement = et_sec - st_sec
        if improvement < _MIN_SEED_IMPROVEMENT:
            continue

        # Step 4 — plausibility cap
        max_drop = _MAX_SEED_DROP.get(event)
        if max_drop is None or improvement > max_drop:
            continue

        best = scy_bests.get(event, {})
        potential.append({
            "event":         event,
            "verified_time": best.get("time", sec_to_time_str(et_sec)),
            "verified_sec":  best.get("time_sec", et_sec),
            "seed_time":     sec_to_time_str(st_sec),
            "seed_sec":      st_sec,
            "improvement":   round(improvement, 2),
            "meet":          rec.get("name") or rec.get("meet_name") or "",
            "date":          rec.get("dateofswim") or "",
        })

    potential.sort(key=lambda x: x["improvement"], reverse=True)
    return potential


def extract_scy_bests(raw_times: list[dict]) -> dict[str, dict]:
    """
    From SwimCloud raw time records, extract SCY best times for Lane4-supported events.

    Returns dict: {lane4_event_name: {time: str, time_sec: float, course: 'SCY'}}
    Picks the fastest (lowest seconds) legal SCY swim per event.
    """
    bests: dict[str, dict] = {}

    for rec in raw_times:
        # Only SCY ("Y" = Yards) legal swims
        if rec.get("eventcourse") != "Y":
            continue
        if not rec.get("legal", True):
            continue

        dist   = rec.get("eventdistance")
        stroke = str(rec.get("eventstroke", ""))
        key    = (dist, stroke)
        event  = _EVENT_MAP.get(key)
        if not event:
            continue

        # Use eventtime (actual verified race result).
        # Fall back to seedtime only when eventtime is absent — seedtime is an
        # unverified entry declaration and must never take precedence over a real result.
        t_raw = rec.get("eventtime") or rec.get("seedtime")
        if t_raw is None:
            continue
        try:
            t_sec = float(t_raw)
        except (TypeError, ValueError):
            continue
        if t_sec <= 0:
            continue

        existing = bests.get(event)
        if existing is None or t_sec < existing["time_sec"]:
            bests[event] = {
                "time":     sec_to_time_str(t_sec),
                "time_sec": t_sec,
                "course":   "SCY",
            }

    return bests


def detect_gender_from_raw(raw_times: list[dict]) -> str | None:
    """
    Detect swimmer gender from SwimCloud fastest_times records.

    SwimCloud encodes gender in the 'eventgender' field as 'M' or 'F'.
    Returns 'men' or 'women' (matching Lane4 convention), or None if unknown.
    """
    for rec in raw_times:
        eg = str(rec.get("eventgender", "")).strip().upper()
        if eg == "M":
            return "men"
        if eg == "F":
            return "women"
    return None


def get_swimmer_scy_bests(swimmer_id: str) -> tuple[dict, dict, list]:
    """
    High-level function: fetch and extract SCY best times + raw profile info
    + potential seed PRs.

    Returns (scy_bests, profile_info, seed_prs)
      scy_bests:   {event_name: {time, time_sec, course}} for supported SCY events
      profile_info:{swimmer_id, display_name, team, grad_year, profile_url, gender}
                   gender is 'men', 'women', or None
      seed_prs:    list of seed-time candidates that passed all filters
    """
    raw          = fetch_fastest_times(swimmer_id)
    scy_bests    = extract_scy_bests(raw)
    profile_info = fetch_profile_info(swimmer_id)
    seed_prs     = extract_seed_prs(raw, scy_bests)
    if not profile_info.get("display_name"):
        profile_info["swimmer_id"] = swimmer_id
    # Attach gender detected from the raw result records (more reliable than profile API)
    profile_info["gender"] = detect_gender_from_raw(raw)
    return scy_bests, profile_info, seed_prs
