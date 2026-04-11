# Admission chance model extracted from main.py.
from models.school_data import SCHOOL_META, OOU_SCHOOL_META, _oou_lookup
from scoring.primitives import tier_label

_LABEL_ORDER = {
    'Moonshot': 0, 'Major Reach': 1, 'Possible': 2,
    'Realistic Shot': 3, 'Strong Chance': 4, 'Very Strong Chance': 5,
}

_LABEL_COLORS = {
    'Very Strong Chance':       '#059669',
    'Strong Chance':            '#10B981',
    'Realistic Shot':           '#2563EB',
    'Possible':                 '#3B82F6',
    'Major Reach':              '#F59E0B',
    'Moonshot':                 '#6B7280',
    'Moonshot — Apply for Fun': '#6B7280',
    'Unknown':                  '#94A3B8',
}

def _oou_admission(oou_meta: dict, sat: int | None, gpa: float | None) -> dict:
    """Compute a simplified admission label for out-of-universe schools."""
    accept    = oou_meta.get('accept', 50)
    sat_med   = oou_meta.get('satMedian', 1200)
    s = sat if sat and sat >= 400 else None
    g = gpa if gpa and gpa > 0  else None

    if accept <= 7:
        label = 'Moonshot'
    elif accept <= 15:
        if s and s >= sat_med - 30:
            label = 'Major Reach'
        else:
            label = 'Moonshot'
    elif accept <= 25:
        if s and s >= sat_med:
            label = 'Realistic Shot'
        elif s and s >= sat_med - 60:
            label = 'Major Reach'
        else:
            label = 'Moonshot'
    else:
        if s and s >= sat_med:
            label = 'Strong Chance'
        elif s and s >= sat_med - 80:
            label = 'Realistic Shot'
        else:
            label = 'Major Reach'

    return {'label': label, 'color': _LABEL_COLORS.get(label, '#94A3B8'),
            'total': None, 'acadScore': None, 'swimScore': None}

# Academic band × Swim support band → base admission label
_ADMIT_MATRIX = {
    4: {4: 'Very Strong Chance', 3: 'Strong Chance',  2: 'Strong Chance',  1: 'Realistic Shot', 0: 'Possible'},
    3: {4: 'Strong Chance',      3: 'Strong Chance',  2: 'Realistic Shot', 1: 'Possible',       0: 'Possible'},
    2: {4: 'Strong Chance',      3: 'Realistic Shot', 2: 'Possible',       1: 'Possible',       0: 'Major Reach'},
    1: {4: 'Realistic Shot',     3: 'Possible',       2: 'Possible',       1: 'Major Reach',    0: 'Moonshot'},
    0: {4: 'Major Reach',        3: 'Major Reach',    2: 'Moonshot',       1: 'Moonshot',       0: 'Moonshot'},
}

# Swim tier → base swim support band (PSF adjusts by ±1 max)
_SWIM_BASE_BAND = {
    'High-Point Contender': 4,
    'Conference Star':      4,
    'Priority Recruit':     3,
    'Top Recruit':          3,
    'Recruitable':          2,
    'Below Roster Level':   1,
    'Not Competitive':      0,
}


def _adm_clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _cap_label(current, max_label):
    """Return current label, or max_label if current is better than max_label."""
    return current if _LABEL_ORDER[current] <= _LABEL_ORDER[max_label] else max_label


def _selectivity_tier(accept_pct, sat_median):
    """
    Classify school selectivity from accept % (integer) and SAT median (integer).
    Returns: 'ultra_selective' | 'highly_selective' | 'selective' | 'broader_admit'
    """
    acc = (accept_pct / 100.0) if accept_pct is not None else None
    sat = sat_median
    if (acc is not None and acc <= 0.15) or (sat is not None and sat >= 1500):
        return 'ultra_selective'
    if (acc is not None and acc <= 0.30) or (sat is not None and sat >= 1400):
        return 'highly_selective'
    if (acc is not None and acc <= 0.50) or (sat is not None and sat >= 1300):
        return 'selective'
    return 'broader_admit'



def admission_chance(school, sat, gpa, adj_tier, psf):
    """
    Matrix-based admission model.  Same call signature as the old function.

    Inputs
      school   — canonical school name
      sat      — swimmer SAT composite (0 or None = not entered)
      gpa      — swimmer GPA (0 or None = not entered)
      adj_tier — swim tier string from tier_label(adjPts)
      psf      — program strength factor from TEAMS_LIST

    Returns AdmissionResult:
      label     — one of 6 labels (or 'Moonshot — Apply for Fun' / 'Unknown')
      color     — hex color for UI
      total     — None (old field kept for compatibility)
      acadScore — academic band 0-4 (debug)
      swimScore — swim support band 0-4 (debug)
    """
    meta = SCHOOL_META.get(school)
    if meta is None:
        return {'label': 'Unknown', 'color': _LABEL_COLORS['Unknown'],
                'total': None, 'acadScore': None, 'swimScore': None}

    if meta.get('moonshot'):
        return {'label': 'Moonshot — Apply for Fun',
                'color': _LABEL_COLORS['Moonshot — Apply for Fun'],
                'total': None, 'acadScore': None, 'swimScore': None}

    # Normalise inputs — treat 0 as "not entered"
    s = sat if sat and sat >= 400 else None
    g = gpa if gpa and gpa > 0  else None

    accept_pct = meta.get('accept')      # integer percent, e.g. 7 for 7%
    sat_median = meta.get('satMedian')   # integer, e.g. 1510

    # ── School academic ranges ────────────────────────────────────────────────
    # Prefer real CDS sat25/sat75 stored in SCHOOL_META; fall back to satMedian ±60.
    sat25 = meta.get('sat25') or ((sat_median - 60) if sat_median else None)
    sat75 = meta.get('sat75') or ((sat_median + 60) if sat_median else None)

    # ── Selectivity tier & floors ────────────────────────────────────────────
    sel_tier = _selectivity_tier(accept_pct, sat_median)
    gpa_off  = {'ultra_selective': 0.20, 'highly_selective': 0.25,
                'selective': 0.30,       'broader_admit':    0.35}
    sat_off  = {'ultra_selective': 60,   'highly_selective': 80,
                'selective': 100,        'broader_admit':    120}
    sat_floor = max(800, sat25 - sat_off[sel_tier]) if sat25 is not None else None
    gpa_floor = None   # no gpa25 available — gpa floor only applies via hard-stop

    # ── Academic band ────────────────────────────────────────────────────────
    gpa_below_floor = False
    sat_below_floor = False

    # Hard stop: GPA < 2.0 → band 0 regardless of SAT
    if g is not None and g < 2.0:
        acad_band = 0
        gpa_below_floor = True
        # Still check SAT floor so both_below_floor can trigger G1
        if s is not None and sat_floor is not None and s < sat_floor:
            sat_below_floor = True
    else:
        # SAT sub-score
        if s is None or sat25 is None or sat75 is None:
            sat_sub = 0
        else:
            sat_below_floor = sat_floor is not None and s < sat_floor
            if   s > sat75:            sat_sub =  2
            elif s >= sat25:           sat_sub =  1
            elif not sat_below_floor:  sat_sub =  0
            else:                      sat_sub = -2

        # GPA sub-score — compare swimmer GPA to school's real mean unweighted GPA.
        # gpaMean is sourced from published CDS or institutional data stored in SCHOOL_META.
        # When no real gpaMean is available, gpa_sub stays 0 (neutral) — no derivation.
        # +1: GPA > gpaMean + 0.10  (meaningfully above school's average)
        # -1: GPA < gpaMean - 0.15  (user-specified penalty threshold)
        #  0: within range, or no gpaMean data for this school
        gpa_mean = meta.get('gpaMean')
        if g is None or gpa_mean is None:
            gpa_sub = 0
        elif g > gpa_mean + 0.10:
            gpa_sub = 1
        elif g < gpa_mean - 0.15:
            gpa_sub = -1
        else:
            gpa_sub = 0

        raw = sat_sub + gpa_sub
        if   raw == 4:         acad_band = 4
        elif raw in (2, 3):    acad_band = 3
        elif raw in (0, 1):    acad_band = 2
        elif raw in (-1, -2):  acad_band = 1
        else:                  acad_band = 0

    both_below_floor = sat_below_floor and gpa_below_floor

    # ── Swim support band ────────────────────────────────────────────────────
    swim_base = _SWIM_BASE_BAND.get(adj_tier, 0)
    psf_mod   = 1 if psf > 1.00 else (-1 if psf <= 0.78 else 0)
    swim_band = _adm_clamp(swim_base + psf_mod, 0, 4)

    # ── Academic floor + school tier (admissions realism rules) ──────────────
    # hiddenIvy=True in SCHOOL_META marks top NESCAC/SCIAC LACs (Williams, Amherst,
    # Bowdoin, Middlebury, Pomona-Pitzer, etc.) — spec's HIGHLY_SELECTIVE tier.
    # MIT/Caltech are already caught by moonshot=True above and never reach here.
    # Ivy League schools are D1/OOU and use _oou_admission() — not this path.
    is_highly_selective = meta.get('hiddenIvy', False)

    # Academic floor: sat < satMedian-120 OR GPA < 3.4 → swim cannot assist
    sat_diff_from_med = (s - sat_median) if (s is not None and sat_median is not None) else None
    below_acad_floor  = (
        (sat_diff_from_med is not None and sat_diff_from_med <= -120) or
        (g is not None and g < 3.4)
    )

    # Apply swim constraints before matrix lookup
    if below_acad_floor:
        swim_band = 0                       # swim cannot rescue academic weakness
    elif is_highly_selective:
        swim_band = min(swim_band, 2)       # max +2 swim assist at elite D3 LACs

    # ── Base label from matrix ────────────────────────────────────────────────
    label = _ADMIT_MATRIX[acad_band][swim_band]

    # ── Guardrails (applied in order) ────────────────────────────────────────
    # G1: Both below floor → Moonshot
    if both_below_floor:
        label = 'Moonshot'
    # G2: GPA below floor (hard-stop triggered or explicit floor breach)
    elif gpa_below_floor:
        label = 'Major Reach' if swim_band == 4 else 'Moonshot'
    # G3: SAT below floor AND band 0 → cap at Major Reach
    elif sat_below_floor and acad_band == 0:
        label = _cap_label(label, 'Major Reach')

    # G4-GPA: Highly/ultra-selective + GPA < 3.5 → Moonshot regardless of SAT or swim
    if sel_tier in ('highly_selective', 'ultra_selective') and g is not None and g < 3.5:
        label = 'Moonshot'

    # G4: No swim support
    # CORE_D3 schools with strong academics (acad_band ≥ 3) can reach Strong Chance
    # without any swim support — academics alone justify it at broader-admit schools.
    # Elite LACs (hiddenIvy) and any below-floor case still cap at Possible.
    if swim_band == 0:
        if acad_band >= 3 and not is_highly_selective and not below_acad_floor:
            label = 'Strong Chance'
        else:
            label = _cap_label(label, 'Possible')

    # G5: Ultra-selective school extra caps
    if sel_tier == 'ultra_selective':
        if swim_band == 0:
            label = 'Moonshot'          # no swim support at elite schools → Extreme Reach
        elif acad_band == 1 and swim_band == 4:
            label = _cap_label(label, 'Possible')
        if acad_band == 0:
            label = 'Moonshot'
    # G6: Highly-selective school caps for band 0
    elif sel_tier == 'highly_selective':
        if acad_band == 0:
            if swim_band >= 3:
                label = _cap_label(label, 'Major Reach')  # no better than Major Reach
            else:
                label = 'Moonshot'

    # Spec floor cap: regardless of all guardrails, academic weakness ≤ Major Reach
    if below_acad_floor:
        label = _cap_label(label, 'Major Reach')

    return {
        'label':     label,
        'color':     _LABEL_COLORS.get(label, '#94A3B8'),
        'total':     None,
        'acadScore': acad_band,
        'swimScore': swim_band,
    }

# score_all_schools() removed — replaced by build_school_universe()
# which starts from the 324-school snapshot universe (EXPLORE_SCHOOLS),
# not from TEAMS_LIST.  See build_school_universe() below.


