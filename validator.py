"""
Post-parse plausibility validator for Lane4 Data Harvester.

Reads a list of event anchor rows (from event_anchors.csv or caller-supplied),
learns robust per-event profiles from the population, then validates each row
and assigns: valid / review / likely_wrong_event.

Parser logic is NOT touched.  This module is advisory-only: it never removes,
alters, or suppresses any parsed row.

Profile variables
-----------------
  sw     = 1st_seconds  (winner time — smallest / fastest)
  deep   = 16th_seconds (deepest anchor — largest / slowest)
  spread = deep - sw    (always positive for valid events)
  shape  = deep / sw    (spread ratio; > 1.0 for valid events)

Priority of evidence (high → low)
----------------------------------
  1. broken ordering (sw > deep)            — very strong
  2. spread implausible                      — strong
  3. shape implausible                       — strong
  4. deep far outside profile                — medium-high
  5. cross-event mismatch (combined signal)  — medium
  6. sw far outside profile                  — low-medium (outlier-resistant)
  7. sw mildly outside profile               — very low
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional

OUTPUT_DIR = Path("output")

MIN_PROFILE_N: int = 8   # minimum samples required to build a profile
ABSURDITY_LO:  float = 2.5   # sw < p05 / ABSURDITY_LO  → absurd
ABSURDITY_HI:  float = 2.5   # deep > p95 * ABSURDITY_HI → absurd
ABSURDITY_SW_HI: float = 2.0  # sw > p95 * ABSURDITY_SW_HI → absurd winner


# ─────────────────────────────────────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────────────────────────────────────

def time_to_seconds(t: str) -> Optional[float]:
    """Convert 'MM:SS.xx' or 'SS.xx' to float seconds.  Returns None on failure."""
    if not t or not t.strip():
        return None
    t = t.strip()
    try:
        if ":" in t:
            parts = t.split(":", 1)
            return float(parts[0]) * 60.0 + float(parts[1])
        return float(t)
    except (ValueError, IndexError):
        return None


def normalize_event(event: str) -> str:
    return event.strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# Robust statistics helpers
# ─────────────────────────────────────────────────────────────────────────────

def _percentile(sorted_vals: list[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    idx = (p / 100.0) * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    return sorted_vals[lo] + (idx - lo) * (sorted_vals[hi] - sorted_vals[lo])


def _median(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return (s[n // 2 - 1] + s[n // 2]) / 2.0 if n % 2 == 0 else s[n // 2]


def _mad(vals: list[float], med: Optional[float] = None) -> Optional[float]:
    if not vals:
        return None
    if med is None:
        med = _median(vals)
    return _median([abs(v - med) for v in vals])


def _winsorize(vals: list[float], lo_p: float = 5.0, hi_p: float = 95.0) -> list[float]:
    if not vals:
        return []
    s = sorted(vals)
    lo = _percentile(s, lo_p)
    hi = _percentile(s, hi_p)
    if lo is None or hi is None:
        return vals
    return [max(lo, min(hi, v)) for v in vals]


def robust_stats(raw_vals: list, min_n: int = MIN_PROFILE_N) -> Optional[dict]:
    """
    Build winsorized robust statistics from a list of values.
    Returns None when the cleaned sample is smaller than min_n.
    """
    vals = [v for v in raw_vals
            if v is not None and not math.isnan(v) and not math.isinf(v) and v > 0]
    if len(vals) < min_n:
        return None
    w = _winsorize(vals)
    s = sorted(w)
    med = _median(w)
    return {
        "n":      len(vals),
        "median": med,
        "mad":    _mad(w, med) or 0.0,
        "p05":    _percentile(s, 5),
        "p25":    _percentile(s, 25),
        "p75":    _percentile(s, 75),
        "p95":    _percentile(s, 95),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Profile building
# ─────────────────────────────────────────────────────────────────────────────

def build_profiles(
    anchor_rows: list[dict],
    min_n: int = MIN_PROFILE_N,
) -> dict[tuple[str, str], dict]:
    """
    Learn per-(gender, normalized_event) profiles from a list of anchor rows.
    Each row must have: Gender, Event, 1st_seconds, 16th_seconds (strings or floats).

    Returns:
        profiles dict keyed by (gender_lower, norm_event)
        Each value contains flat stat keys: sw_median, sw_mad, sw_p05 … etc.
    """
    buckets: dict[tuple, dict[str, list]] = defaultdict(
        lambda: {"sw": [], "deep": [], "spread": [], "shape": []}
    )

    for r in anchor_rows:
        gender = (r.get("Gender") or "").lower().strip()
        event  = normalize_event(r.get("Event") or "")
        sw_raw = r.get("1st_seconds", "")
        dp_raw = r.get("16th_seconds", "")

        sw   = time_to_seconds(str(sw_raw))   if sw_raw else None
        deep = time_to_seconds(str(dp_raw))   if dp_raw else None

        if sw is None or deep is None:
            continue
        if sw <= 0 or deep <= 0:
            continue
        if deep < sw:
            continue          # skip invalid-ordering rows while building profiles

        spread = deep - sw
        shape  = deep / sw   # always > 1.0

        key = (gender, event)
        buckets[key]["sw"].append(sw)
        buckets[key]["deep"].append(deep)
        buckets[key]["spread"].append(spread)
        buckets[key]["shape"].append(shape)

    profiles: dict[tuple, dict] = {}
    for (gender, event), data in buckets.items():
        profile: dict = {"gender": gender, "event": event}
        for metric in ("sw", "deep", "spread", "shape"):
            stats = robust_stats(data[metric], min_n)
            if stats:
                for k, v in stats.items():
                    profile[f"{metric}_{k}"] = v
        profiles[(gender, event)] = profile

    return profiles


def write_profiles_csv(profiles: dict, path: Path) -> None:
    all_metric_keys = []
    for metric in ("sw", "deep", "spread", "shape"):
        for stat in ("n", "median", "mad", "p05", "p25", "p75", "p95"):
            all_metric_keys.append(f"{metric}_{stat}")

    fieldnames = ["gender", "event"] + all_metric_keys
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for profile in sorted(profiles.values(), key=lambda p: (p["gender"], p["event"])):
            w.writerow(profile)


# ─────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ─────────────────────────────────────────────────────────────────────────────

def _robust_z(value: float, profile: dict, metric: str) -> Optional[float]:
    """
    Robust z-score: |v - median| / (1.4826 * MAD + epsilon).
    1.4826 scales MAD to match the std dev of a normal distribution.
    """
    med = profile.get(f"{metric}_median")
    mad = profile.get(f"{metric}_mad")
    if med is None or mad is None:
        return None
    sigma = 1.4826 * mad + 1e-6
    return abs(value - med) / sigma


def _penalty(z: Optional[float], soft: float, hard: float,
             p_soft: float, p_hard: float, p_extreme: float) -> tuple[float, str]:
    """Return (penalty_points, severity_tag) from a robust z-score."""
    if z is None:
        return 0.0, ""
    if z <= soft:
        return 0.0, ""
    if z <= hard:
        return p_soft, "soft"
    if z <= 5.0:
        return p_hard, "hard"
    return p_extreme, "extreme"


def _combined_distance(
    row_vals: dict[str, float],
    profile: dict,
    metrics: tuple[str, ...],
    weights: dict[str, float],
) -> Optional[float]:
    """
    Weighted Euclidean distance in robust-z space across multiple metrics.
    Returns None if no metrics can be computed.
    """
    total, wt = 0.0, 0.0
    for m in metrics:
        v = row_vals.get(m)
        if v is None:
            continue
        z = _robust_z(v, profile, m)
        if z is None:
            continue
        w = weights.get(m, 1.0)
        total += w * z * z
        wt    += w
    if wt == 0.0:
        return None
    return math.sqrt(total / wt)


# ─────────────────────────────────────────────────────────────────────────────
# Row validation
# ─────────────────────────────────────────────────────────────────────────────

CROSS_EVENT_WEIGHTS = {"sw": 0.5, "deep": 1.0, "spread": 1.5, "shape": 1.5}


def validate_row(
    gender: str,
    event: str,
    sw: float,
    deep: float,
    profiles: dict[tuple, dict],
    all_profiles: dict[tuple, dict],
) -> dict:
    """
    Validate one anchor row.  Returns a dict with:
        confidence_score, validation_label, reasons,
        closest_other_event, claimed_event_distance, closest_other_event_distance
    """
    gender_norm = gender.lower().strip()
    event_norm  = normalize_event(event)
    key         = (gender_norm, event_norm)
    profile     = profiles.get(key)        # may be None (insufficient data)

    reasons: list[str] = []
    confidence = 100.0

    spread = deep - sw
    shape  = deep / sw if sw > 0 else None

    row_vals = {
        "sw": sw, "deep": deep,
        "spread": spread,
        "shape":  shape,
    }

    # ── A. Monotonicity (ordering) ────────────────────────────────────────
    if sw > deep:
        confidence -= 55.0      # very strong signal — single broken ordering → likely_wrong
        reasons.append("anchor_order_broken")

    # ── Always: family-level absurdity guardrail ──────────────────────────
    # Run unconditionally — catches impossible values even without a rich profile.
    # Uses -55 so a single hit alone → likely_wrong_event (<50).
    hard_floor, sw_ceil, hard_ceil = _family_absurdity(event_norm)
    if hard_floor and sw < hard_floor / ABSURDITY_LO:
        confidence -= 55.0
        reasons.append("sw_absurdly_fast")
    if sw_ceil and sw > sw_ceil:
        confidence -= 55.0
        reasons.append("sw_absurdly_slow")
    if hard_ceil and deep > hard_ceil * ABSURDITY_HI:
        confidence -= 55.0
        reasons.append("deep_absurdly_slow")

    # ── B–E require a profile ─────────────────────────────────────────────
    claimed_dist: Optional[float] = None
    closest_other: Optional[str]  = None
    closest_other_dist: Optional[float] = None

    if profile:
        # ── B. sw plausibility (low weight) ──────────────────────────────
        z_sw = _robust_z(sw, profile, "sw")
        pen, sev = _penalty(z_sw, soft=2.0, hard=3.0,
                            p_soft=3.0, p_hard=8.0, p_extreme=15.0)
        confidence -= pen
        if sev:
            reasons.append(f"sw_{sev}_outlier")

        # ── C. deep plausibility (medium-high weight) ─────────────────────
        z_dp = _robust_z(deep, profile, "deep")
        pen, sev = _penalty(z_dp, soft=2.0, hard=3.0,
                            p_soft=8.0, p_hard=18.0, p_extreme=30.0)
        confidence -= pen
        if sev:
            reasons.append(f"deep_{sev}_outlier")

        # ── D. spread plausibility (high weight) ──────────────────────────
        z_sp = _robust_z(spread, profile, "spread")
        pen, sev = _penalty(z_sp, soft=2.0, hard=3.0,
                            p_soft=10.0, p_hard=22.0, p_extreme=38.0)
        confidence -= pen
        if sev:
            reasons.append("spread_implausible" if sev in ("hard", "extreme")
                           else "spread_soft_outlier")

        # ── E. shape plausibility (high weight) ───────────────────────────
        if shape is not None:
            z_sh = _robust_z(shape, profile, "shape")
            pen, sev = _penalty(z_sh, soft=2.0, hard=3.0,
                                p_soft=10.0, p_hard=22.0, p_extreme=38.0)
            confidence -= pen
            if sev:
                reasons.append("shape_implausible" if sev in ("hard", "extreme")
                               else "shape_soft_outlier")

        # ── F. Profile-refined absurdity (supplements family guardrail) ──────
        # Only adds profile-derived check for sw_absurdly_slow (no family ceiling for sw).
        p95_sw = profile.get("sw_p95")
        if p95_sw is not None and sw > p95_sw * ABSURDITY_SW_HI:
            if "sw_absurdly_slow" not in reasons:
                confidence -= 45.0
                reasons.append("sw_absurdly_slow")

        # ── G. Cross-event mismatch ────────────────────────────────────────
        claimed_dist = _combined_distance(row_vals, profile, ("sw", "deep", "spread", "shape"),
                                          CROSS_EVENT_WEIGHTS)
        best_dist: Optional[float] = None
        best_key:  Optional[str]   = None

        for (g, e), other_profile in all_profiles.items():
            if g != gender_norm or e == event_norm:
                continue
            d = _combined_distance(row_vals, other_profile,
                                   ("sw", "deep", "spread", "shape"),
                                   CROSS_EVENT_WEIGHTS)
            if d is None:
                continue
            if best_dist is None or d < best_dist:
                best_dist = d
                best_key  = e

        closest_other      = best_key
        closest_other_dist = best_dist

        if claimed_dist is not None and best_dist is not None and best_dist > 0:
            ratio = claimed_dist / best_dist
            if ratio > 3.0:
                confidence -= 15.0
                if "closer_to_other_event" not in reasons:
                    reasons.append("closer_to_other_event")
            elif ratio > 2.0:
                confidence -= 10.0
                if "closer_to_other_event" not in reasons:
                    reasons.append("closer_to_other_event")

    confidence = max(0.0, min(100.0, confidence))

    if confidence >= 80.0:
        label = "valid"
    elif confidence >= 50.0:
        label = "review"
    else:
        label = "likely_wrong_event"

    return {
        "confidence_score":            round(confidence, 1),
        "validation_label":            label,
        "reasons":                     "; ".join(reasons) if reasons else "",
        "closest_other_event":         closest_other or "",
        "claimed_event_distance":      round(claimed_dist, 3) if claimed_dist is not None else "",
        "closest_other_event_distance": round(closest_other_dist, 3) if closest_other_dist is not None else "",
    }


def _family_absurdity(event_norm: str) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Conservative hard bounds by event.
    Returns (sw_floor, sw_ceil, deep_ceil):
      sw_floor — fastest plausible winner (sw < sw_floor/ABSURDITY_LO → absurdly fast)
      sw_ceil  — slowest plausible winner in any sanctioned meet (sw > sw_ceil → absurdly slow)
      deep_ceil — slowest plausible 16th-place time (deep > deep_ceil*ABSURDITY_HI → absurdly slow)

    Checked longest-name-first to avoid substring collisions
    (e.g. '50 free' must not match inside '1650 free').

    sw_ceil is set conservatively — even very weak conferences should have winners
    under these times. They only fire on truly non-competitive or mislabeled rows.
    """
    e = event_norm
    # (sw_floor, sw_ceil, deep_ceil) — all in seconds
    if "1650 free"  in e: return (700.0,  1920.0, 2400.0)
    if "1000 free"  in e: return (400.0,  1200.0, 1500.0)
    if "500 free"   in e: return (200.0,   560.0,  720.0)
    if "200 free"   in e: return ( 80.0,   220.0,  420.0)
    if "100 free"   in e: return ( 35.0,   100.0,  210.0)
    if "50 free"    in e: return ( 15.0,    52.0,  130.0)
    if "200 back"   in e: return ( 85.0,   230.0,  420.0)
    if "100 back"   in e: return ( 40.0,   110.0,  210.0)
    if "200 breast" in e: return (100.0,   255.0,  460.0)
    if "100 breast" in e: return ( 45.0,   120.0,  220.0)
    if "200 fly"    in e: return ( 85.0,   235.0,  420.0)
    if "100 fly"    in e: return ( 38.0,   108.0,  210.0)
    if "400 im"     in e: return (200.0,   490.0,  720.0)
    if "200 im"     in e: return ( 85.0,   230.0,  420.0)
    return (None, None, None)


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

VALIDATION_REPORT_HEADER = [
    "Conference", "Bundle_ID", "Source_File", "Gender", "Event",
    "sw_seconds", "deep_seconds", "spread_seconds", "shape_ratio",
    "validation_label", "confidence_score", "reasons",
    "closest_other_event", "claimed_event_distance", "closest_other_event_distance",
]


def validate(
    anchor_rows: list[dict],
    output_dir: Path = OUTPUT_DIR,
    min_n: int = MIN_PROFILE_N,
) -> list[dict]:
    """
    Main entry point.  Call with the full list of anchor rows after all bundles
    have been processed.

    Steps:
      1. Build robust profiles from anchor_rows.
      2. Validate each row.
      3. Write output/event_time_profiles.csv.
      4. Write output/event_validation_report.csv (non-valid rows only).
      5. Return enriched list of all rows (adds validation_label, confidence_score).

    anchor_rows must have keys:
        Conference, Bundle_ID, Source_File, Gender, Event,
        1st_seconds, 8th_seconds, 16th_seconds
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: learn profiles ────────────────────────────────────────────
    profiles = build_profiles(anchor_rows, min_n=min_n)
    write_profiles_csv(profiles, output_dir / "event_time_profiles.csv")
    print(f"  [validator] Learned profiles for {len(profiles)} (gender, event) pairs "
          f"from {len(anchor_rows)} anchor rows.")

    # ── Step 2: validate ──────────────────────────────────────────────────
    enriched: list[dict] = []
    report_rows: list[dict] = []

    for r in anchor_rows:
        gender = (r.get("Gender") or "").strip()
        event  = (r.get("Event")  or "").strip()
        sw_raw = r.get("1st_seconds",  "")
        dp_raw = r.get("16th_seconds", "")

        sw   = time_to_seconds(str(sw_raw))  if sw_raw else None
        deep = time_to_seconds(str(dp_raw))  if dp_raw else None

        row = dict(r)

        if sw is None or deep is None:
            row["validation_label"]  = "review"
            row["confidence_score"]  = 50.0
            enriched.append(row)
            continue

        result = validate_row(gender, event, sw, deep, profiles, profiles)
        row.update(result)
        enriched.append(row)

        if result["validation_label"] != "valid":
            spread = deep - sw
            shape  = deep / sw if sw > 0 else None
            report_rows.append({
                "Conference":           r.get("Conference", ""),
                "Bundle_ID":            r.get("Bundle_ID",  ""),
                "Source_File":          r.get("Source_File",""),
                "Gender":               gender,
                "Event":                event,
                "sw_seconds":           round(sw,   3),
                "deep_seconds":         round(deep, 3),
                "spread_seconds":       round(spread, 3),
                "shape_ratio":          round(shape, 4) if shape else "",
                "validation_label":     result["validation_label"],
                "confidence_score":     result["confidence_score"],
                "reasons":              result["reasons"],
                "closest_other_event":         result["closest_other_event"],
                "claimed_event_distance":      result["claimed_event_distance"],
                "closest_other_event_distance":result["closest_other_event_distance"],
            })

    # ── Step 3: write reports ─────────────────────────────────────────────
    with open(output_dir / "event_validation_report.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=VALIDATION_REPORT_HEADER)
        w.writeheader()
        w.writerows(report_rows)

    n_valid   = sum(1 for r in enriched if r.get("validation_label") == "valid")
    n_review  = sum(1 for r in enriched if r.get("validation_label") == "review")
    n_wrong   = sum(1 for r in enriched if r.get("validation_label") == "likely_wrong_event")
    print(f"  [validator] Results — valid:{n_valid}  review:{n_review}  "
          f"likely_wrong_event:{n_wrong}")
    print(f"  [validator] Wrote event_validation_report.csv  ({len(report_rows)} flagged rows)")
    print(f"  [validator] Wrote event_time_profiles.csv  ({len(profiles)} profiles)")

    return enriched
