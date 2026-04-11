# School universe builder extracted from main.py.
from state import EXPLORE_SCHOOLS, SCHOOL_LOCATIONS, TEAMS
from models.school_data import SCHOOL_META, CONF_DIVISION
from models.swimmer_defaults import JAMES
from scoring.swim_scoring import _score_school_swim
from scoring.admission import admission_chance
from scoring.primitives import tier_label

def build_school_universe(times, sat, gpa):
    """
    ONE unified school pipeline for ALL ~324 schools in EXPLORE_SCHOOLS.

    Every school goes through the SAME builder regardless of data richness.
    SCHOOL_META is an enrichment source.  Its absence changes VALUES, not CODE.

    Every result shares this schema:
      school, conference, division
      adjTier, adjPts, rawPts, psf
      top3, allEvents
      admission  { label, color, total, acadScore, swimScore }
      meta       { accept, satMedian, hiddenIvy, ivyLeague, stem, merit,
                   location, vibe, moonshot? }
      confTierShort, confTier, confFinish2026, confScore2026, confPowerClass
      hasSwimData   bool — True if this school has benchmark data in TEAMS_LIST

    Schools with swim data get full scoring.
    Schools without swim data get adjPts=0, adjTier='', empty events.
    Admission computed for all — 'Unknown' normalised to '' (value diff, not path diff).
    """
    results = []

    for es in EXPLORE_SCHOOLS:
        school_name = es['school']
        conference  = es.get('conference', '')

        # Re-use the team_rec match already resolved by _build_explore_schools()
        # (which already applies UAA short-name expansion and raw_name fallbacks).
        team_rec = es.get('_team_rec')
        has_swim = team_rec is not None

        # Unified meta — SCHOOL_META enriches, never gates
        meta_raw = (SCHOOL_META.get(school_name)
                    or (SCHOOL_META.get(team_rec['school']) if team_rec else None)
                    or {})
        meta = {
            'accept':    meta_raw.get('accept'),
            'satMedian': meta_raw.get('satMedian'),
            'hiddenIvy': meta_raw.get('hiddenIvy', False),
            'ivyLeague': meta_raw.get('ivyLeague', False),
            'stem':      meta_raw.get('stem', False),
            'merit':     meta_raw.get('merit', ''),
            'location':  meta_raw.get('location', ''),
            'vibe':      meta_raw.get('vibe', ''),
        }
        if meta_raw.get('moonshot'):
            meta['moonshot'] = True

        # Swim scoring — same call for all schools, values differ
        if has_swim:
            swim = _score_school_swim(team_rec, times)
            if swim:
                adj_tier   = swim['adjTier']
                adj_pts    = swim['adjPts']
                raw_pts    = swim['rawPts']
                top4       = swim['top3']   # dict key kept as 'top3' for compat
                all_events = swim['allEvents']
                psf        = swim['psf']
                has_depth  = swim['hasDepth']
            else:
                # Team data exists but swimmer has zero scorable events here
                adj_tier = ''
                adj_pts = raw_pts = 0.0
                top4 = all_events = []
                has_depth = False
                psf = team_rec.get('psf', 1.0)
        else:
            adj_tier = ''
            adj_pts = raw_pts = 0.0
            top4 = all_events = []
            has_depth = False
            psf = 1.0

        # Admission — same function for all schools.
        # SCHOOL_META keys may differ from snapshot CSV names (UAA abbreviated forms,
        # 30-char truncations, casing).  team_rec['school'] has already had TEAM_NAME_MAP
        # applied during load_data(), so it carries the correct canonical key.
        # Try snapshot name first; fall back to team_rec canonical if not found.
        _sm_key = (school_name
                   if SCHOOL_META.get(school_name)
                   else (team_rec['school']
                         if team_rec and SCHOOL_META.get(team_rec['school'])
                         else school_name))
        adm = admission_chance(_sm_key, sat, gpa, adj_tier, psf)
        # 'Unknown' means no SCHOOL_META entry — normalise to empty (value diff, not path diff)
        if adm.get('label') == 'Unknown':
            adm = {'label': '', 'color': '#94A3B8',
                   'total': None, 'acadScore': None, 'swimScore': None}

        results.append({
            'school':         school_name,
            'conference':     conference,
            'division':       CONF_DIVISION.get(conference, 'D3'),
            'adjTier':        adj_tier,
            'adjPts':         float(adj_pts),
            'rawPts':         float(raw_pts),
            'psf':            float(psf),
            'top3':           top4,
            'hasDepth':       has_depth,
            'allEvents':      all_events,
            'admission':      adm,
            'meta':           meta,
            'confTierShort':  es.get('conf_tier_short', ''),
            'confTier':       es.get('conf_tier', ''),
            'confFinish2026': es.get('men_finish_2026') or es.get('women_finish_2026'),
            'confScore2026':  es.get('men_score_2026') or es.get('women_score_2026'),
            'confPowerClass': es.get('conf_power_class', ''),
            'hasSwimData':    has_swim,
        })

    # Sort: swim-fit score descending, then alphabetical
    results.sort(key=lambda r: (-r['adjPts'], r['school']))
    return results


def score_one_school(times, conference, school):
    """
    Score arbitrary times at one specific school — for the manual calculator.
    Uses the TEAMS enrichment records directly (PSF + benchmarks).
    """
    team_key  = f"{conference}|{school}"
    team_rec  = TEAMS.get(team_key)
    if team_rec is None:
        return {'error': f'School "{school}" not found in {conference}'}

    # Swim layer — also collect unscored events for display
    scored, unscored = [], []
    for event, time_str in times.items():
        sec = parse_time(time_str)
        if sec is None:
            continue
        es = _score_event(event, time_str, conference)
        if es is not None:
            scored.append({**es, 'time': time_str, 'benchmarked': True})
        else:
            unscored.append({'event': event, 'time': time_str,
                             'sec': sec, 'benchmarked': False})

    scored.sort(key=lambda e: e['pts'], reverse=True)
    top4    = scored[:4]
    raw_pts = round(sum(e['pts'] for e in top4), 2)
    psf     = team_rec['psf']
    adj_pts = round(raw_pts * psf, 2)
    adj_tier = tier_label(adj_pts)
    adm     = admission_chance(school, JAMES['sat'], JAMES['gpa'], adj_tier, psf)
    meta_raw = SCHOOL_META.get(school, {})

    return {
        'school':     school,
        'conference': conference,
        'tier':       team_rec['tier'],
        'psf':        psf,
        'rawPts':     raw_pts,
        'adjPts':     adj_pts,
        'adjTier':    adj_tier,
        'top3':       [e['event'] for e in top4],   # key kept; now holds top 4
        'events':     scored + unscored,
        'admission':  adm,
        'meta': {
            'accept':    meta_raw.get('accept'),
            'satMedian': meta_raw.get('satMedian'),
            'hiddenIvy': meta_raw.get('hiddenIvy', False),
            'stem':      meta_raw.get('stem', False),
            'merit':     meta_raw.get('merit', ''),
            'location':  meta_raw.get('location', ''),
            'vibe':      meta_raw.get('vibe', ''),
        },
        'normalized': team_rec['normalized'],
        'rawName':    team_rec['raw_name'],
    }

# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------

