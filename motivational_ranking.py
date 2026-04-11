"""
motivational_ranking.py — USA Swimming motivational-times A-score ranking.

Uses static/usa_motivational_times_17_18_scy.json (already in the repo).

A-score anchors (lower time = faster = better):
  0A = B time    (slowest qualifying standard)
  1A = A time
  2A = AA time
  3A = AAA time
  4A = AAAA time  (fastest qualifying standard)

  score < 0 → slower than B
  score > 4 → faster than AAAA
"""

import json
import os
import re

_STANDARDS: dict | None = None
_STANDARDS_PATH = os.path.join(os.path.dirname(__file__), "static", "data", "usa_motivational_times_17_18_scy.json")

# Ordered anchors (A-level, dict-key) — slowest to fastest.
# BB is intentionally excluded: it is stored in the JSON for reference only
# and must never be used as an interpolation rung.
_ANCHOR_KEYS   = ["B", "A", "AA", "AAA", "AAAA"]
_ANCHOR_SCORES = [0,   1,   2,    3,     4     ]

# Map caller-supplied gender strings → JSON top-level keys ("boys" / "girls").
# Both the traditional "men"/"women" and the new "boys"/"girls" spellings are accepted.
_GENDER_KEY: dict[str, str] = {
    "men":   "boys",
    "boys":  "boys",
    "male":  "boys",
    "m":     "boys",
    "women": "girls",
    "girls": "girls",
    "female":"girls",
    "f":     "girls",
}


def load_standards() -> dict:
    """Load the USA Swimming motivational standards (cached after first load)."""
    global _STANDARDS
    if _STANDARDS is None:
        with open(_STANDARDS_PATH, "r", encoding="utf-8") as f:
            _STANDARDS = json.load(f)
    return _STANDARDS


def parse_time_to_sec(t: str) -> float | None:
    """
    Parse a Lane4 time string to seconds.
    Accepts '52.50', '1:32.40', '4:37.00', etc.
    Returns None on failure.
    """
    if not t or not isinstance(t, str):
        return None
    t = t.strip()
    if ":" in t:
        parts = t.split(":", 1)
        try:
            return int(parts[0]) * 60 + float(parts[1])
        except (ValueError, IndexError):
            return None
    try:
        return float(t)
    except ValueError:
        return None


def a_score(event: str, t_sec: float, gender: str, standards: dict) -> float:
    """
    Compute the fractional A-score for a given event and time.

    gender: 'men' or 'women'
    t_sec:  time in seconds (smaller = faster = better)

    Returns a float; 0=B, 1=A, 2=AA, 3=AAA, 4=AAAA. Can exceed 4 or be negative.
    Returns -999 if event/gender not found in standards.
    """
    gender_key = _GENDER_KEY.get(gender.lower().strip(), "boys")
    ev_std = standards.get(gender_key, {}).get(event)
    if not ev_std:
        return -999.0

    # Build anchor list: (a_score, threshold_seconds)
    # Smaller seconds = faster = better → AAAA < AAA < AA < A < B
    anchors = [(score, ev_std[key]) for score, key in zip(_ANCHOR_SCORES, _ANCHOR_KEYS)]
    # anchors[0] = (0, B_time) → slowest
    # anchors[4] = (4, AAAA_time) → fastest

    B_time    = anchors[0][1]  # highest seconds
    AAAA_time = anchors[4][1]  # lowest seconds

    # Faster than AAAA → extrapolate beyond 4
    if t_sec <= AAAA_time:
        interval = anchors[3][1] - AAAA_time  # AAA_time - AAAA_time
        if interval <= 0:
            return 4.0
        extra = (AAAA_time - t_sec) / interval
        return 4.0 + extra

    # Slower than B → extrapolate below 0
    if t_sec >= B_time:
        interval = B_time - anchors[1][1]  # B_time - A_time
        if interval <= 0:
            return 0.0
        deficit = (t_sec - B_time) / interval
        return -deficit

    # Find which bracket the time falls in
    for i in range(len(anchors) - 1):
        lo_score, lo_time = anchors[i]      # lower A-score, slower standard (higher sec)
        hi_score, hi_time = anchors[i + 1]  # higher A-score, faster standard (lower sec)
        if hi_time <= t_sec <= lo_time:
            interval = lo_time - hi_time
            if interval <= 0:
                return float(lo_score)
            frac = (lo_time - t_sec) / interval
            return lo_score + frac

    return 0.0  # fallback (should not reach here)


def rank_events(times_dict: dict[str, str], gender: str, n: int = 10) -> list[dict]:
    """
    Rank events by A-score and return the top-n.

    times_dict: {lane4_event_name: time_string}
    gender: 'men' or 'women'
    n: how many top events to return

    Returns list of dicts (sorted descending by a_score):
      {event, time, time_sec, a_score, course}
    Each dict has 'course' defaulting to 'SCY' (all Lane4 times are assumed SCY).
    """
    standards = load_standards()
    scored = []
    for event, t_str in times_dict.items():
        t_sec = parse_time_to_sec(t_str)
        if t_sec is None or t_sec <= 0:
            continue
        score = a_score(event, t_sec, gender, standards)
        if score <= -500:
            continue  # unknown event
        scored.append(
            {
                "event":    event,
                "time":     t_str,
                "time_sec": t_sec,
                "a_score":  round(score, 3),
                "course":   "SCY",
            }
        )

    scored.sort(key=lambda x: x["a_score"], reverse=True)
    return scored[:n]


def rank_swimcloud_bests(scy_bests: dict[str, dict], gender: str, n: int = 10) -> list[dict]:
    """
    Rank SwimCloud SCY best-times dict by A-score and return top-n.

    scy_bests: {event_name: {time, time_sec, course}} (from swimcloud_client)
    gender: 'men' or 'women'

    Returns list of dicts sorted descending by a_score:
      {event, time, time_sec, a_score, course}
    """
    standards = load_standards()
    scored = []
    for event, info in scy_bests.items():
        t_sec = info.get("time_sec", 0)
        if not t_sec or t_sec <= 0:
            continue
        score = a_score(event, t_sec, gender, standards)
        if score <= -500:
            continue
        scored.append(
            {
                "event":    event,
                "time":     info.get("time", ""),
                "time_sec": t_sec,
                "a_score":  round(score, 3),
                "course":   info.get("course", "SCY"),
            }
        )

    scored.sort(key=lambda x: x["a_score"], reverse=True)
    return scored[:n]
