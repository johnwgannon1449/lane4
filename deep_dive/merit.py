# Merit estimation helpers extracted from main.py.

def _act_to_sat(act):
    """Rough ACT composite → SAT composite conversion."""
    table = {
        36: 1590, 35: 1540, 34: 1500, 33: 1460, 32: 1430, 31: 1400,
        30: 1370, 29: 1340, 28: 1310, 27: 1280, 26: 1240, 25: 1210,
        24: 1180, 23: 1140, 22: 1110, 21: 1080, 20: 1040, 19: 1010,
        18:  970, 17:  930, 16:  890, 15:  850,
    }
    return table.get(int(act), round((int(act) / 36) * 1200 + 400))


def _estimate_merit_block(merit_level, sat, gpa, sat_median, accept):
    """
    Compute deterministic merit estimates for The Money Conversation.
    Returns a dict with coa, merit, net, note, has_merit.
    """
    # COA estimate from acceptance rate
    if accept <= 20:
        coa = 72000
    elif accept <= 35:
        coa = 67000
    elif accept <= 55:
        coa = 61000
    elif accept <= 70:
        coa = 56000
    else:
        coa = 51000
    coa_str = f"~${coa:,}"

    if merit_level == 'none':
        return {
            'coa':       coa_str,
            'merit':     'None',
            'net':       f"~${coa:,} before need-based aid",
            'note':      "This school does not offer merit scholarships—aid here is need-based.",
            'has_merit': False,
        }

    max_merit = 25000 if merit_level == 'high' else 15000
    sat_diff  = (sat or 1000) - (sat_median or 1200)
    elite_gpa = gpa >= 3.9

    if sat_diff < -50:
        lo_pct, hi_pct = 0.0, 0.0
    elif sat_diff <= 50 and not elite_gpa:
        lo_pct, hi_pct = 0.25, 0.40
    elif sat_diff <= 120 and not elite_gpa:
        lo_pct, hi_pct = 0.50, 0.65
    else:
        lo_pct, hi_pct = 0.65, 0.75

    if lo_pct == 0.0:
        return {
            'coa':       coa_str,
            'merit':     '$0',
            'net':       coa_str,
            'note':      "Your current academic numbers are below this school's typical merit threshold.",
            'has_merit': False,
        }

    lo = round(max_merit * lo_pct / 1000) * 1000
    hi = round(max_merit * hi_pct / 1000) * 1000
    return {
        'coa':       coa_str,
        'merit':     f"~${lo:,}–${hi:,}",
        'net':       f"~${max(0, coa - hi):,}–${max(0, coa - lo):,}",
        'note':      "Strong students like you are often considered for merit here.",
        'has_merit': True,
    }

