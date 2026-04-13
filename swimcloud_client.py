"""
swimcloud_client.py — SwimCloud public API wrapper for Lane4.

Endpoints used (no auth required):
  Search:  GET https://www.swimcloud.com/api/search/?q=<name>&type=swimmer
  Times:   GET https://www.swimcloud.com/api/swimmers/<id>/profile_fastest_times/

Two-tier fetch strategy:
  1. curl_cffi with Chrome TLS/HTTP2 impersonation — fast, works from
     residential IPs. Falls through on 403 (Cloudflare challenge).
  2. Playwright (real Chromium headless) — visits swimcloud.com homepage to
     solve Cloudflare's managed challenge, then makes in-browser fetch() calls.
     Used automatically on Render/datacenter IPs where curl_cffi gets 403.
"""

import json
import re
import threading
import time as _time
import urllib.parse

from curl_cffi import requests as cf_requests

_BASE = "https://www.swimcloud.com"

# Headers added on top of curl_cffi's Chrome impersonation.
# X-Requested-With signals AJAX so SwimCloud returns JSON, not HTML.
_API_HEADERS = {
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer":          "https://www.swimcloud.com/",
}

# ── curl_cffi session (fast path) ────────────────────────────────────────────
_SESSION = None
_SESSION_BUILT = 0.0
_SESSION_TTL = 1800  # 30 min

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


def _get_session():
    global _SESSION, _SESSION_BUILT
    now = _time.time()
    if _SESSION is None or (now - _SESSION_BUILT) > _SESSION_TTL:
        s = cf_requests.Session(impersonate="chrome124")
        s.headers.update(_API_HEADERS)
        try:
            s.get(_BASE + "/", timeout=15)
        except Exception:
            pass
        _SESSION = s
        _SESSION_BUILT = now
    return _SESSION


# ── Playwright browser singleton (fallback for datacenter IPs) ───────────────
_PW_PLAYWRIGHT = None
_PW_BROWSER    = None
_PW_PAGE       = None
_PW_PAGE_BUILT = 0.0
_PW_LOCK       = threading.Lock()
_PW_PAGE_TTL   = 1800  # 30 min — re-establish browser session periodically


def _get_pw_page():
    """Return a persistent Playwright page with an active SwimCloud session."""
    global _PW_PLAYWRIGHT, _PW_BROWSER, _PW_PAGE, _PW_PAGE_BUILT
    now = _time.time()
    if _PW_PAGE is not None and (now - _PW_PAGE_BUILT) < _PW_PAGE_TTL:
        return _PW_PAGE
    with _PW_LOCK:
        if _PW_PAGE is not None and (now - _PW_PAGE_BUILT) < _PW_PAGE_TTL:
            return _PW_PAGE   # another thread beat us here
        # Clean up stale objects
        for obj in (_PW_PAGE, _PW_BROWSER, _PW_PLAYWRIGHT):
            if obj is not None:
                try: obj.close()   # type: ignore[union-attr]
                except Exception: pass
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        # Patch out headless-browser detection signals before any page loads.
        # Cloudflare checks navigator.webdriver; removing it prevents escalation
        # from a solvable managed challenge to an interactive Turnstile.
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        """)
        page = ctx.new_page()
        # Visit the homepage — real Chromium executes Cloudflare's managed
        # challenge JS automatically, gets cf_clearance cookie, and lands on
        # the actual SwimCloud homepage. networkidle ensures the challenge
        # redirect cycle is fully complete before we start making API calls.
        page.goto(_BASE + "/", timeout=30_000, wait_until="networkidle")
        _PW_PLAYWRIGHT = pw
        _PW_BROWSER    = browser
        _PW_PAGE       = page
        _PW_PAGE_BUILT = _time.time()
        return page


def _pw_fetch_json(url: str, params: dict | None = None):
    """Run a fetch() inside the live Playwright page and return parsed JSON."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    page = _get_pw_page()
    result = page.evaluate(
        """async (url) => {
            const r = await fetch(url, {
                headers: {
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://www.swimcloud.com/"
                }
            });
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        }""",
        url,
    )
    return result


class _JSONResponse:
    """Thin wrapper so _pw_fetch_json fits the same interface as requests.Response."""
    def __init__(self, data):
        self._data       = data
        self.status_code = 200
    def json(self):        return self._data
    def raise_for_status(self): pass


def _get(url: str, params: dict | None = None, timeout: int = 15):
    """
    Fetch a SwimCloud URL and return a response-like object.

    Fast path: curl_cffi (no browser overhead, works from residential IPs).
    Fallback:  Playwright (real Chromium, always works, used on Render where
               curl_cffi gets 403-challenged by Cloudflare WAF).
    """
    global _SESSION, _SESSION_BUILT
    # ── Fast path ──────────────────────────────────────────────────────────
    try:
        s = _get_session()
        r = s.get(url, params=params, timeout=timeout)
        if r.status_code != 403:
            r.raise_for_status()
            return r
        # 403 → Cloudflare challenge; reset session so the homepage visit
        # is retried fresh before we fall through to Playwright.
        _SESSION = None
        _SESSION_BUILT = 0.0
    except Exception:
        pass
    # ── Playwright fallback ─────────────────────────────────────────────────
    data = _pw_fetch_json(url, params)
    return _JSONResponse(data)


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
