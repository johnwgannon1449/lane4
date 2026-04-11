# Search filtering and sorting functions extracted from main.py.
import re
from models.school_data import SCHOOL_META
from scoring.admission import _selectivity_tier

# Admissions label → numeric score (used for sorting and filtering)
_ADM_LABEL_SCORE = {
    'Very Strong Chance': 100,
    'Strong Chance':       80,
    'Realistic Shot':      60,
    'Possible':            40,
    'Major Reach':         15,
    'Moonshot':             5,
    'Moonshot — Apply for Fun': 2,
    'Unknown':             25,
}

# adjTier values that mean the swimmer is NOT competitive at this school
_SWIM_NOT_COMPETITIVE = {'Below Roster Level', 'Not Competitive'}

# adjTier values that mean swim is literally impossible / should be hidden
_ADM_IMPOSSIBLE = {'Not Competitive'}


def _similarity_sort(candidates, target):
    """
    Sort candidates by institutional similarity to a target school.
    Used when the user searches by school name — produces the pool that
    Claude picks the 5 'similar schools' from.

    Purely institutional dimensions — the swimmer's adjPts / swim score
    plays NO role here.  The recruiting badge still appears on each card
    from the normal scoring pipeline; it just doesn't shape which schools
    are considered 'similar'.

    Dimensions and weights:
      1. Division match         (0 if same, +4 penalty if different)
      2. Academic selectivity   (|tier distance| × 3, 4-bucket scale)
      3. Conference tier        (|tier distance| × 1, only when target has one)
    """
    SEL_ORDER  = {'ultra_selective': 0, 'highly_selective': 1,
                  'selective': 2, 'broader_admit': 3}
    TIER_ORDER = {'1A': 0, '1B': 1, '2': 2, '3': 3, '4': 4, '': 99}

    t_meta   = target.get('meta') or {}
    t_div    = target.get('division', '')
    t_sel    = _selectivity_tier(t_meta.get('accept'), t_meta.get('satMedian'))
    t_sel_n  = SEL_ORDER.get(t_sel, 3)
    t_tier   = target.get('confTierShort', '')
    t_tier_n = TIER_ORDER.get(t_tier, 99)
    has_tier = t_tier_n != 99  # skip tier distance when target has no conf-tier data

    def _score(r):
        r_meta   = r.get('meta') or {}
        r_div    = r.get('division', '')
        r_sel    = _selectivity_tier(r_meta.get('accept'), r_meta.get('satMedian'))
        r_sel_n  = SEL_ORDER.get(r_sel, 3)
        r_tier   = r.get('confTierShort', '')
        r_tier_n = TIER_ORDER.get(r_tier, 99)

        div_penalty = 0 if r_div == t_div else 4
        sel_dist    = abs(r_sel_n - t_sel_n) * 3
        tier_dist   = abs(r_tier_n - t_tier_n) if (has_tier and r_tier_n != 99) else 0
        return div_penalty + sel_dist + tier_dist

    return sorted(candidates, key=_score)


def _pre_sort(results, query, eliminated, my_list):
    """
    Re-sort top-35 slice based on query intent.
    Excludes eliminated schools and my-list schools before sorting.
    Returns top 35 from the resulting list.
    """
    excl = set(eliminated) | set(my_list)
    pool = [r for r in results if r['school'] not in excl]

    q = query.lower()

    # Unified strength score that works for both swim-scored and non-swim schools.
    # Swim schools have adjPts (real scored range); non-swim schools get a proxy
    # from confTierShort so they can compete in the pool instead of all sinking to 0.
    _TIER_PROXY = {'1A': 120, '1B': 95, '2': 70, '3': 45, '4': 20}
    def _strength(r):
        pts = r.get('adjPts') or 0
        return pts if pts > 0 else _TIER_PROXY.get(r.get('confTierShort', ''), 0)

    # Geographic region → set of 2-letter state abbreviations found in meta.location
    _GEO: dict[str, set[str]] = {
        'west coast':       {'CA', 'OR', 'WA'},
        'california':       {'CA'},
        'pacific northwest':{'OR', 'WA'},
        'southwest':        {'CA', 'AZ', 'NM', 'NV', 'UT', 'CO'},
        'mountain':         {'CO', 'UT', 'WY', 'ID', 'MT', 'NV', 'AZ', 'NM'},
        'new england':      {'MA', 'ME', 'NH', 'VT', 'RI', 'CT'},
        'northeast':        {'NY', 'NJ', 'CT', 'MA', 'ME', 'NH', 'VT', 'RI', 'PA', 'MD', 'DE'},
        'east coast':       {'NY', 'NJ', 'CT', 'MA', 'ME', 'NH', 'VT', 'RI', 'MD', 'DE',
                             'VA', 'NC', 'SC', 'GA', 'FL', 'DC'},
        'southeast':        {'VA', 'NC', 'SC', 'GA', 'FL', 'TN', 'AL', 'MS', 'LA', 'AR',
                             'KY', 'WV'},
        'south':            {'TX', 'LA', 'MS', 'AL', 'GA', 'FL', 'SC', 'NC', 'TN',
                             'KY', 'AR', 'OK', 'VA'},
        'texas':            {'TX'},
        'midwest':          {'OH', 'IN', 'IL', 'MI', 'WI', 'MN', 'IA', 'MO',
                             'ND', 'SD', 'NE', 'KS'},
        'florida':          {'FL'},
        'new york':         {'NY'},
    }

    geo_states: set[str] | None = None
    for kw, states in _GEO.items():
        if kw in q:
            geo_states = states
            break

    def _state(r) -> str:
        loc = r['meta'].get('location', '')
        return loc.rsplit(', ', 1)[-1].strip() if ', ' in loc else ''

    if geo_states:
        pool.sort(key=lambda r: (0 if _state(r) in geo_states else 1, -_strength(r)))
    elif any(k in q for k in (
        'prestig', 'best school', 'academic', 'selective',
        'hardest', 'toughest', 'elite', 'smartest', 'most impressive',
        'highest ranked', 'highest-ranked', 'strongest academic',
    )):
        pool.sort(key=lambda r: (r['meta'].get('accept') or 999))
    elif any(k in q for k in ('stem', 'engineer', 'tech', 'med', 'science')):
        pool.sort(key=lambda r: (0 if r['meta'].get('stem') else 1, -_strength(r)))
    elif any(k in q for k in ('money', 'cost', 'afford', 'save', 'merit', 'scholarship')):
        rank = {'high': 0, 'moderate': 1, 'none': 2, '': 3}
        pool.sort(key=lambda r: rank.get(r['meta'].get('merit', ''), 3))
    elif any(k in q for k in ('star', 'podium', 'win', 'lead', 'competi')):
        pool.sort(key=lambda r: -_strength(r))
    elif any(k in q for k in ('fun', 'social', 'happy', 'vibe', 'culture')):
        pool.sort(key=lambda r: -(r['meta'].get('accept') or 0))
    elif 'hidden ivy' in q or 'ivy' in q:
        pool.sort(key=lambda r: (0 if r['meta'].get('hiddenIvy') or r['meta'].get('ivyLeague') else 1))
    elif any(k in q for k in ('d1', 'division 1', 'division one', 'big school', 'large')):
        pool.sort(key=lambda r: (0 if r.get('division') == 'D1' else 1, -_strength(r)))
    elif any(k in q for k in ('d3', 'division 3', 'division iii', 'small school', 'small college')):
        pool.sort(key=lambda r: (0 if r.get('division') == 'D3' else 1, -_strength(r)))
    else:
        # Default: balanced sort using unified strength so non-swim schools
        # are not all buried at adjPts=0 behind every swim-scored D3 school.
        pool.sort(key=lambda r: -_strength(r))

    return pool[:150]

def _program_strength_desc(r):
    """Plain-language program strength label — never uses the word 'tier'."""
    ts = r.get('confTierShort', '')
    if ts == '1A': return 'Super Powerhouse'
    if ts == '1B': return 'Powerhouse'
    if ts == '2':  return 'Strong'
    if ts == '3':  return 'Mid-pack'
    if ts == '4':  return 'Developing'
    return r.get('adjTier', '')  # fallback to internal score label

def _build_school_line(i, r):
    """Format one numbered line for the Claude search prompt."""
    vibe = (r['meta'].get('vibe') or '')[:60]
    return (
        f"{i+1}. {r['school']} ({r['conference']}): "
        f"programStrength={_program_strength_desc(r)}, admission={r['admission']['label']}, "
        f"hiddenIvy={str(r['meta'].get('hiddenIvy', False)).lower()}, "
        f"stem={str(r['meta'].get('stem', False)).lower()}, "
        f"merit={r['meta'].get('merit', '')}, "
        f"accept={r['meta'].get('accept', '?')}%, "
        f"vibe=\"{vibe}\""
    )

# ─────────────────────────────────────────────────────────────────────────────
# AI-FIRST SEARCH PIPELINE — Steps 1-5 helpers
# ─────────────────────────────────────────────────────────────────────────────


def _hard_filter(candidates: list, intent: dict) -> tuple:
    """Remove schools that contradict our scoring labels for personal queries.

    Only called when intent['is_personal'] is True. Two independent checks:

    SWIM filter (is_swim=True, is_explicit_reach=False):
      Remove schools missing swim data OR where the swimmer is not competitive.
      Bypassed entirely when is_explicit_reach=True — user asked for dream /
      long-shot / reach schools and wants non-competitive options shown.

    ADMISSIONS threshold filter (adm_threshold is not None):
      Remove schools whose admissions label score falls below the threshold.
        80 → Strong Chance or better  ("definitely get in")
        60 → Realistic Shot or better ("can get in")
      Threshold is None for general "for me" queries — no admissions filter.

    Both filters can apply simultaneously (e.g. swim query + "I can get into").
    Returns (kept_list, removed_debug_list).
    """
    kept, removed = [], []
    threshold       = intent.get('adm_threshold')    # int or None
    has_any_times   = intent.get('has_any_times', True)
    explicit_reach  = intent.get('is_explicit_reach', False)

    for r in candidates:
        adm_label = r.get('admission', {}).get('label', 'Unknown')
        adj_tier  = r.get('adjTier', '')
        has_swim  = r.get('hasSwimData', False)
        reasons   = []

        # Swim filter — skipped when user explicitly asked for reaches/dream schools
        if intent['is_swim'] and not explicit_reach:
            if not has_swim:
                reasons.append('no swim data')
            elif not adj_tier and has_any_times:
                # School has benchmark data but none of this swimmer's events
                # are benchmarked for that conference → cannot evaluate fit.
                # Only filter when the student has actually entered times —
                # if they haven't, adj_tier is empty for every school and we
                # fall back to admissions-only filtering rather than removing all.
                reasons.append('no scorable events at this school')
            elif adj_tier in _SWIM_NOT_COMPETITIVE:
                reasons.append(f'not competitive — {adj_tier}')

        # Admissions threshold filter
        if threshold is not None:
            score = _ADM_LABEL_SCORE.get(adm_label, 25)
            if score < threshold:
                label_needed = 'Strong Chance' if threshold >= 80 else 'Realistic Shot'
                reasons.append(f'below threshold ({adm_label}, need {label_needed}+)')

        if reasons:
            removed.append({'school': r['school'], 'reason': '; '.join(reasons)})
        else:
            kept.append(r)

    return kept, removed



def _search_rank_score(r: dict, mode: str) -> float:
    """
    Step 5 — Composite ranking score (higher = shown first).

    GUIDED / CONSTRAINED: admissions truth dominates (0.60) + swim fit (0.40).
      Swim fit uses the scorer's actual adjPts when available:
        adjPts > 0          → swimmer earns points there, use actual score
        adjPts = 0 + has swim data → swimmer is below the roster bar → swim_n = 0
        adjPts = 0 + no swim data  → use a soft program-strength proxy (×0.25)
      → Michigan sinks when the swimmer would not make the roster.
      → MIT sinks when the student is not admissible.

    OBJECTIVE / EXPLORATORY: program strength leads (0.65) + admissions (0.35).
      Uses full program-strength proxy so results reflect genuine quality,
      not personalised fit for this swimmer.
    """
    _ADM_S = {
        'Very Strong Chance': 100, 'Strong Chance': 80,
        'Realistic Shot': 60,     'Possible': 40,
        'Major Reach': 15,        'Moonshot': 5,
        'Moonshot — Apply for Fun': 2, 'Unknown': 25,
    }
    adm_s    = _ADM_S.get(r.get('admission', {}).get('label', 'Unknown'), 25)
    adj      = r.get('adjPts') or 0
    has_swim = r.get('hasSwimData', False)
    _PROX    = {'1A': 130, '1B': 100, '2': 72, '3': 46, '4': 22}

    if mode in ('GUIDED', 'CONSTRAINED'):
        # Only credit swim fit when the scorer has confirmed the swimmer earns
        # points at this school.  Both "below the bar" (adjPts=0, hasSwimData=True)
        # and "no data" (hasSwimData=False) get swim_n=0 — if we can't confirm
        # relevance, we don't reward it.  Ranking falls back to admissions truth.
        swim_proxy = adj if adj > 0 else 0
        swim_n = min(swim_proxy / 130.0, 1.0) * 100
        # Tie-breaker: within the same admissions band, prefer broader-admit
        # schools (higher accept rate → easier reach → more honest for this kid)
        accept_bonus = (r.get('meta') or {}).get('accept') or 50
        accept_bonus = min(accept_bonus / 100.0, 1.0) * 3   # 0-3 pt tiebreak
        return adm_s * 0.60 + swim_n * 0.40 + accept_bonus

    # OBJECTIVE / EXPLORATORY — program strength, not personalised
    swim_proxy = adj if adj > 0 else _PROX.get(r.get('confTierShort', ''), 0)
    swim_n     = min(swim_proxy / 130.0, 1.0) * 100
    return swim_n * 0.65 + adm_s * 0.35


