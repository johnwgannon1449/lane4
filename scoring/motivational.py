# A-score scoring against USA Swimming motivational standards.
# NOTE: This module is used by /api/rank-events. It is deliberately kept
# separate from motivational_ranking.py (which is used by SwimCloud propose).
# The key behavioral difference: this version clamps sub-B scores to -1.0;
# motivational_ranking.py does not clamp.
import os, json

_USA_STANDARDS_CACHE: dict | None = None

def _load_usa_standards() -> dict:
    global _USA_STANDARDS_CACHE
    if _USA_STANDARDS_CACHE is None:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'data', 'usa_motivational_times_17_18_scy.json')
        try:
            with open(path) as f:
                _USA_STANDARDS_CACHE = json.load(f)
        except Exception:
            _USA_STANDARDS_CACHE = {}
    return _USA_STANDARDS_CACHE


def _parse_time_sec_float(time_str: str) -> float | None:
    """Convert '1:47.50' or '47.50' or '47' to float seconds. Returns None on failure."""
    s = (time_str or '').strip()
    if not s:
        return None
    try:
        if ':' in s:
            parts = s.split(':')
            return float(parts[0]) * 60 + float(parts[1])
        return float(s)
    except (ValueError, IndexError):
        return None


def _compute_a_score(time_sec: float, std: dict) -> float:
    """
    Compute fractional A-score against USA Swimming motivational standards.
    Scale: 0=B, 1=A, 2=AA, 3=AAA, 4=AAAA (lower time = higher score).
    BB falls naturally between 0 and 1 by linear interpolation.
    """
    levels = [
        (0.0, std['B']),
        (1.0, std['A']),
        (2.0, std['AA']),
        (3.0, std['AAA']),
        (4.0, std['AAAA']),
    ]
    if time_sec >= std['B']:
        # Slower than B — extrapolate negative (clamped to -1)
        denom = std['B'] - std['A']
        return max(-1.0, (std['B'] - time_sec) / denom if denom else -1.0)
    if time_sec <= std['AAAA']:
        # Faster than AAAA — extrapolate above 4
        denom = std['AAA'] - std['AAAA']
        extra = (std['AAAA'] - time_sec) / denom if denom else 0.0
        return 4.0 + extra
    for i in range(len(levels) - 1):
        lo_score, lo_time = levels[i]
        hi_score, hi_time = levels[i + 1]
        if hi_time <= time_sec <= lo_time:
            denom = lo_time - hi_time
            frac = (lo_time - time_sec) / denom if denom else 0.5
            return lo_score + frac * (hi_score - lo_score)
    return 0.0


def _a_tier_label(score: float) -> str:
    if score < 0:   return 'Sub-B'
    if score < 1.0: return 'B/BB'
    if score < 2.0: return 'A'
    if score < 3.0: return 'AA'
    if score < 4.0: return 'AAA'
    return 'AAAA+'

