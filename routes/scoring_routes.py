"""routes/scoring_routes.py — Scoring, meta, and school-info API endpoints."""
from flask import Blueprint, request, jsonify
from auth import login_required
from state import CONFERENCES, NORMALIZATION_LOG, EXPLORE_SCHOOLS, SCHOOL_LOCATIONS
from models.swimmer_defaults import JAMES, ALL_EVENTS
from scoring.motivational import _load_usa_standards, _parse_time_sec_float, _compute_a_score, _a_tier_label
from scoring.universe import build_school_universe

scoring_routes_bp = Blueprint('scoring_routes', __name__)


@scoring_routes_bp.route('/api/rank-events', methods=['POST'])
def rank_events():
    """
    Rank a swimmer's events by A-score using USA Swimming 17-18 SCY motivational standards.
    Body: { times: { "100 Free": "47.59", ... }, gender: "men"|"women" }
    Returns up to 14 events sorted by A-score descending, with top-5 flag.
    """
    body   = request.get_json(silent=True) or {}
    times  = body.get('times') or {}
    gender = (body.get('gender') or 'men').lower()
    gender_key = 'women' if gender.startswith('w') else 'men'

    stds = _load_usa_standards()
    gender_stds = stds.get(gender_key, {})

    ranked = []
    unranked = []
    for event, time_str in times.items():
        sec = _parse_time_sec_float(time_str)
        if sec is None or sec <= 0:
            unranked.append({'event': event, 'time': time_str})
            continue
        std = gender_stds.get(event)
        if std is None:
            unranked.append({'event': event, 'time': time_str})
            continue
        score = _compute_a_score(sec, std)
        ranked.append({
            'event':     event,
            'time':      time_str,
            'a_score':   round(score, 2),
            'a_label':   f'{score:.1f}A',
            'tier':      _a_tier_label(score),
            'top5':      False,
        })

    ranked.sort(key=lambda x: x['a_score'], reverse=True)
    for i, r in enumerate(ranked):
        r['top5'] = (i < 5)
    return jsonify({'ok': True, 'ranked': ranked, 'unranked': unranked})


@scoring_routes_bp.route('/api/meta', methods=['GET'])
def meta():
    return jsonify({
        'conferences':       sorted(CONFERENCES.keys()),
        'teams':             CONFERENCES,
        'events':            ALL_EVENTS,
        'normalizationLog':  NORMALIZATION_LOG,
    })


@scoring_routes_bp.route('/api/schools', methods=['GET'])
@login_required
def api_schools():
    """Return the unified 324-school explore dataset (modeled + snapshot_only)."""
    modeled   = sum(1 for s in EXPLORE_SCHOOLS if s.get('hasSwimData'))
    no_swim   = sum(1 for s in EXPLORE_SCHOOLS if not s.get('hasSwimData'))
    # Strip internal Python-only field before serialising
    schools_out = [{k: v for k, v in s.items() if k != '_team_rec'} for s in EXPLORE_SCHOOLS]
    return jsonify({
        'schools':       schools_out,
        'total':         len(schools_out),
        'withSwimData':  modeled,
        'conferenceOnly': no_swim,
    })


@scoring_routes_bp.route('/api/school-locations', methods=['GET'])
@login_required
def api_school_locations():
    """Return pre-computed lat/lng for all 324 schools. Served from school_locations.json."""
    return jsonify(SCHOOL_LOCATIONS)


@scoring_routes_bp.route('/api/score-all', methods=['GET', 'POST'])
def score_all():
    """Score against all ~324 programs. POST body may include profile overrides."""
    if request.method == 'POST':
        body    = request.json or {}
        times   = body.get('times', JAMES['times'])
        sat     = int(body.get('sat',  JAMES['sat']))
        gpa     = float(body.get('gpa', JAMES['gpa']))
        profile = body if body else JAMES
    else:
        times, sat, gpa = JAMES['times'], JAMES['sat'], JAMES['gpa']
        profile = JAMES
    results = build_school_universe(times, sat, gpa)
    return jsonify({
        'profile':       profile,
        'totalSchools':  len(EXPLORE_SCHOOLS),
        'scoredSchools': sum(1 for r in results if r['adjPts'] > 0),
        'results':       results,
    })
