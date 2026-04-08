"""
swimcloud_client.py — SwimCloud public API wrapper for Lane4.

Endpoints used (no auth required):
  Search:  GET https://www.swimcloud.com/api/search/?q=<name>&type=swimmer
  Times:   GET https://www.swimcloud.com/api/swimmers/<id>/profile_fastest_times/
"""

import re
import time as _time
import requests

_SESSION = None
_SESSION_BUILT = 0.0
_SESSION_TTL = 3600  # re-create session after 1 hour

_BASE = "https://www.swimcloud.com"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":          "https://www.swimcloud.com/",
}

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


def _get_session() -> requests.Session:
    global _SESSION, _SESSION_BUILT
    now = _time.time()
    if _SESSION is None or (now - _SESSION_BUILT) > _SESSION_TTL:
        s = requests.Session()
        s.headers.update(_HEADERS)
        try:
            s.get(_BASE + "/", timeout=10)
        except Exception:
            pass
        _SESSION = s
        _SESSION_BUILT = now
    return _SESSION


def _get(url: str, params: dict | None = None, timeout: int = 12) -> requests.Response:
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
    Fetch structured profile info for a swimmer (name, team, grad year).
    Uses the /api/swimmers/search/ endpoint with the swimmer ID.
    Falls back gracefully on error.
    """
    try:
        url = _BASE + "/api/swimmers/search/"
        r = _get(url, params={"swimmer_id": swimmer_id})
        data = r.json()
        results = data.get("results", [])
        if results:
            rec = results[0]
            if str(rec.get("id", "")) == str(swimmer_id):
                return {
                    "swimmer_id":   str(rec.get("id", swimmer_id)),
                    "display_name": rec.get("display_name") or rec.get("name", ""),
                    "team":         rec.get("primary_team", "") or "",
                    "grad_year":    rec.get("gradhs"),
                    "profile_url":  _BASE + "/swimmer/" + str(swimmer_id) + "/",
                }
    except Exception:
        pass
    return {
        "swimmer_id":  swimmer_id,
        "display_name": "",
        "team":         "",
        "grad_year":    None,
        "profile_url":  _BASE + "/swimmer/" + swimmer_id + "/",
    }


def fetch_fastest_times(swimmer_id: str) -> list[dict]:
    """
    Fetch profile_fastest_times for a swimmer.
    Returns raw list of SwimCloud time records.
    """
    url = _BASE + f"/api/swimmers/{swimmer_id}/profile_fastest_times/"
    r = _get(url)
    return r.json()


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


def get_swimmer_scy_bests(swimmer_id: str) -> tuple[dict, dict]:
    """
    High-level function: fetch and extract SCY best times + raw profile info.

    Returns (scy_bests, profile_info)
      scy_bests: {event_name: {time, time_sec, course}} for supported SCY events
      profile_info: {swimmer_id, display_name, team, grad_year, profile_url}
    """
    raw = fetch_fastest_times(swimmer_id)
    scy_bests = extract_scy_bests(raw)
    profile_info = fetch_profile_info(swimmer_id)
    if not profile_info.get("display_name"):
        profile_info["swimmer_id"] = swimmer_id
    return scy_bests, profile_info
