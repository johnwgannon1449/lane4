# Pure scoring primitive functions extracted from main.py
# No global state — all inputs passed as parameters.

def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

# ── Primitives ─────────────────────────────────────────────────────────────

def parse_time(s):
    """'M:SS.ss' or 'SS.ss' → decimal seconds. Returns None if invalid/missing."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        if ':' in s:
            m, sec = s.split(':', 1)
            return float(m) * 60 + float(sec)
        return float(s)
    except ValueError:
        return None

def estimate_place(sec, b):
    """
    3-zone linear interpolation — workbook formula (Swimmer_Calcs):

      Zone 1  sec <= first      → 1.0          (capped; workbook IF behaviour)
      Zone 2  sec <= eighth     → 1 + (sec-1st)/(8th-1st) * 7
      Zone 3  sec <= sixteenth  → 8 + (sec-8th)/(16th-8th) * 8
      Zone 4  sec > sixteenth   → 16 + (sec-16th) / secPerPlace

    Returns a continuous float. No upper ceiling.
    """
    first     = b['first']
    eighth    = b['eighth']
    sixteenth = b['sixteenth']
    spp       = b['sec_per_place'] or 1.0

    if sec <= first:
        return 1.0
    if sec <= eighth:
        return 1.0 + (sec - first)  / ((eighth    - first)    or 1.0) * 7
    if sec <= sixteenth:
        return 8.0 + (sec - eighth) / ((sixteenth  - eighth)   or 1.0) * 8
    return 16.0 + (sec - sixteenth) / spp

def exp_points(place):
    """MAX(0, MIN(20, 21−place)) — workbook formula. Continuous float."""
    return max(0.0, min(20.0, 21.0 - place))

def confidence_weight(place):
    """
    Bubble-zone confidence discount — from Swimmer_Calcs.
    Full weight for A/B finalists; discounted for bubble; zero below 16th.
    """
    if place <= 12: return 1.0
    if place <= 14: return 0.85
    if place <= 16: return 0.65
    return 0.0

def place_label(place):
    """Human-readable place outcome — OUTPUT_SCHEMA thresholds."""
    if place <= 1.5:  return 'Contender'
    if place <= 3.5:  return '🏅 Podium'
    if place <= 8.5:  return 'A Final'
    if place <= 16.5: return 'B Final'
    if place <= 20:   return 'Bubble'
    return 'Out of range'

def tier_label(pts):
    """
    Swim tier from adjPts — workbook thresholds (authoritative over spec).
    Called with rawPts for display and adjPts for the canonical tier.
    NOTE: bottom two tiers use recruiting label names, not admissions label names.
    """
    if pts < 1:   return 'Not Competitive'
    if pts < 4:   return 'Below Roster Level'
    if pts < 10:  return 'Recruitable'
    if pts < 18:  return 'Priority Recruit'
    if pts < 35:  return 'Top Recruit'
    if pts < 50:  return 'Conference Star'
    return 'High-Point Contender'

