# Swim layer scoring functions extracted from main.py.
# Uses: BENCHMARKS (state.py), CONF_DIVISION (models.school_data),
#        tier_label, _score_event (scoring.primitives).
from state import BENCHMARKS
from models.school_data import CONF_DIVISION
from scoring.primitives import parse_time, estimate_place, exp_points, confidence_weight, place_label, tier_label

def _score_event(event, time_str, conf):
    """
    Score one event for one conference.
    Returns an EventScore dict or None if no benchmark / no valid time.

    EventScore (OUTPUT_SCHEMA):
      event      — event name
      sec        — time in decimal seconds
      place      — estimated decimal finish place
      pts        — confidence-weighted points (workbook: expPts × confidence)
                   NOTE: spec defines pts as integer from placeToPoints() lookup;
                   workbook uses continuous formula — workbook is authoritative.
      expPts     — points before confidence discount (for tracing)
      confidence — confidence multiplier applied (for tracing)
      placeLabel — human-readable outcome
    """
    sec = parse_time(time_str)
    if sec is None:
        return None

    bench = BENCHMARKS.get(f"{conf}|{event}")
    if bench is None:
        return None   # event not benchmarked in this conference

    place  = estimate_place(sec, bench)
    exp_pt = exp_points(place)
    cw     = confidence_weight(place)
    pts    = exp_pt * cw               # workbook weighted value → spec's `pts` field

    return {
        'event':      event,
        'sec':        round(sec, 3),
        'place':      round(place, 2),
        'pts':        round(pts, 2),   # OUTPUT_SCHEMA field name
        'expPts':     round(exp_pt, 2),
        'confidence': cw,
        'placeLabel': place_label(place),
    }

def _score_school_swim(team_rec, times):
    """
    Pure swim-value layer for one school.

    Input:  team_rec (from TEAMS_LIST), times dict from swimmer profile
    Output: SwimResult dict, or None if the swimmer has no scorable events here.

    SwimResult fields (OUTPUT_SCHEMA — swim-layer subset):
      school, conference, finish, tier, psf
      rawPts   — sum of top-4 pts values
      adjPts   — rawPts × psf
      adjTier  — tier label from adjPts
      top3     — up to 4 highest-scoring EventScore objects (key kept for compat)
      allEvents — all scored EventScore objects, sorted pts desc
      hasDepth  — True if swimmer has 4+ events with pts > 0
      normalized, rawName — provenance flags
    """
    conf   = team_rec['conference']
    school = team_rec['school']
    psf    = team_rec['psf']

    all_events = []
    for event, time_str in times.items():
        es = _score_event(event, time_str, conf)
        if es is not None:
            all_events.append(es)

    # Sort by pts descending; top-4 drive rawPts
    all_events.sort(key=lambda e: e['pts'], reverse=True)
    top4    = all_events[:4]
    raw_pts = round(sum(e['pts'] for e in top4), 2)

    if raw_pts == 0:
        return None   # zero-score guardrail — school excluded from results

    adj_pts  = round(raw_pts * psf, 2)
    adj_tier = tier_label(adj_pts)
    has_depth = sum(1 for e in all_events if e['pts'] > 0) >= 4

    return {
        'school':          school,
        'conference':      conf,
        'division':        CONF_DIVISION.get(conf, 'D3'),
        'finish':          team_rec['finish'],
        'tier':            team_rec['tier'],
        'psf':             psf,
        'rawPts':          raw_pts,
        'adjPts':          adj_pts,
        'adjTier':         adj_tier,
        'top3':            top4,   # key kept for downstream compat; now holds top 4
        'hasDepth':        has_depth,
        'allEvents':       all_events,
        'normalized':      team_rec['normalized'],
        'rawName':         team_rec['raw_name'],
        'confTierShort':   team_rec.get('conf_tier_short', ''),
        'confTier':        team_rec.get('conf_tier', ''),
        'confFinish2026':  team_rec.get('conf_finish_2026'),
        'confScore2026':   team_rec.get('conf_score_2026', ''),
        'confPowerClass':  team_rec.get('conf_power_class', ''),
    }

# ── Admission layer v2 — matrix model ───────────────────────────────────────
#
# Labels (6): Very Strong Chance > Strong Chance > Realistic Shot >
#             Possible > Major Reach > Moonshot
# Academic band (0-4) × Swim support band (0-4) → base label → guardrails.
# ---------------------------------------------------------------------------

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

