import os, json, re, time, threading
import datetime
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
try:
    import psycopg2
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()
app = Flask(__name__, static_folder='static', static_url_path='')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = os.environ.get('SESSION_SECRET', 'dev-secret-change-me')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') != 'development'

# ---------------------------------------------------------------------------
# Language prompts (moved to prompts_config.py)
from prompts_config import LANE4_LANGUAGE_PROMPT, LANE4_DEEP_DIVE_PROMPT

# DB and auth helpers moved to db.py and auth.py
from db import get_db, _init_db, _bootstrap_initial_admin, _is_user_admin
from auth import login_required, admin_required, user_admin_required
# Models — pure data constants
from models.school_data import TEAM_NAME_MAP, CONF_DIVISION, SCHOOL_META, OOU_SCHOOL_META, _oou_lookup
from models.swimmer_defaults import JAMES, ALL_EVENTS
from models.school_aliases import _UAA_SHORT, _CAND_STOP, _UNIVERSE_ALIASES, _ACRONYM_ALIASES, _US_STATES
# Runtime state — mutable module-level singletons (mutated in place by load_data)
from state import EXPLORE_SCHOOLS, SCHOOL_LOCATIONS, BENCHMARKS, TEAMS, TEAMS_LIST, CONFERENCES, NORMALIZATION_LOG
# Data loading pipeline
from data_loader import load_data, CSV_BENCH_PATH, CSV_SNAP_PATH
# Scoring engine
from scoring.motivational import _load_usa_standards, _parse_time_sec_float, _compute_a_score, _a_tier_label
from scoring.primitives import _float, parse_time, estimate_place, exp_points, confidence_weight, place_label, tier_label
from scoring.swim_scoring import _score_event, _score_school_swim
from scoring.admission import _oou_admission, admission_chance
from scoring.universe import build_school_universe, score_one_school
# Search pipeline
from search.intent import _detect_query_intent
from search.filters import (
    _ADM_LABEL_SCORE, _SWIM_NOT_COMPETITIVE, _ADM_IMPOSSIBLE,
    _similarity_sort, _pre_sort, _program_strength_desc, _build_school_line,
    _hard_filter, _search_rank_score,
)
from search.school_resolver import (
    _cname_norm, _cname_toks, _qnorm, _school_entity_surface,
    _resolve_school_names, _map_to_universe,
)
from search.prompts import (
    _build_student_context, _build_candidate_prompt,
    _parse_candidate_names, _parse_search_response,
)
# Deep dive helpers
from deep_dive.merit import _act_to_sat, _estimate_merit_block
# Admin image curation
from admin.image_curation import (
    _CANDIDATES_PATH, _CURATED_PATH, _BLOCKLIST_PATH, _SCHOOL_IMAGES_PATH,
    _load_candidates_manifest, _load_curated_manifest, _save_curated_manifest,
    _push_curated_to_school_images, _rebuild_school_images_from_curated,
    _load_blocklist, _save_blocklist, _load_school_images, _save_school_images,
)
from routes.static_pages import static_pages_bp
from routes.utility import utility_bp
from routes.auth import auth_bp
from routes.data_sync import data_sync_bp
from routes.swimcloud import swimcloud_bp


@app.route('/api/rank-events', methods=['POST'])
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


load_data()

def _init_db_background():
    try:
        _init_db()
        _bootstrap_initial_admin()
        print('[startup] DB init complete.')
    except Exception as _e:
        print(f'[startup] DB init warning: {_e} — admin auth may be unavailable')

threading.Thread(target=_init_db_background, daemon=True).start()
_rebuild_school_images_from_curated()

app.register_blueprint(static_pages_bp)
app.register_blueprint(utility_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(data_sync_bp)
app.register_blueprint(swimcloud_bp)


def _get_anthropic():
    """Return an Anthropic client, or None if no key is configured."""
    key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception:
        return None




# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/api/meta', methods=['GET'])
def meta():
    return jsonify({
        'conferences':       sorted(CONFERENCES.keys()),
        'teams':             CONFERENCES,
        'events':            ALL_EVENTS,
        'normalizationLog':  NORMALIZATION_LOG,
    })

@app.route('/api/schools', methods=['GET'])
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

@app.route('/api/school-locations', methods=['GET'])
@login_required
def api_school_locations():
    """Return pre-computed lat/lng for all 324 schools. Served from school_locations.json."""
    return jsonify(SCHOOL_LOCATIONS)


# --- SCORING / UNIVERSE API ROUTES ---

@app.route('/api/score-all', methods=['GET', 'POST'])
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

@app.route('/api/search', methods=['POST'])
def search():
    """
    Natural-language search: re-sort top-35 by query intent, call Claude,
    return 6 annotated SchoolResult objects.

    If the query exactly (or partially) matches a school name, that school is
    returned first as a directMatch, and Claude picks 5 related schools.
    Otherwise Claude picks 6 schools from the pre-sorted pool.

    Body: { query, eliminated?, myList? }
    Response: { answer, schools, directMatch? } or { error, fallback? }
    """
    data       = request.json or {}
    query      = data.get('query', '').strip()
    eliminated = data.get('eliminated', [])
    my_list    = data.get('myList', [])
    prof_ovr   = data.get('profile', {})

    if not query:
        return jsonify({'error': 'Query is required'}), 400

    times         = prof_ovr.get('times') or JAMES['times']
    sat           = int(prof_ovr.get('sat')  or JAMES['sat'])
    gpa           = float(prof_ovr.get('gpa') or JAMES['gpa'])
    act_score     = prof_ovr.get('actScore', JAMES.get('actScore', 0)) or 0
    ap_count      = prof_ovr.get('apCount',  JAMES.get('apCount',  0)) or 0
    swimmer_name  = prof_ovr.get('name') or JAMES['name']
    # ONE unified pool — all ~324 schools through the same builder
    all_results = build_school_universe(times, sat, gpa)

    # ── School-name resolution (alias + fuzzy, replaces old exact+substring) ────
    excl_names   = set(eliminated) | set(my_list)
    resolved     = _resolve_school_names(query, all_results)
    # Strip schools the user already eliminated or added to their list
    resolved     = [r for r in resolved if r['school'] not in excl_names]

    # Multi-match: ambiguous query matched 2+ schools → return them all, skip AI
    if len(resolved) >= 2:
        for r in resolved:
            adm_lbl  = (r.get('admission') or {}).get('label', '')
            swim_lbl = r.get('adjTier', '')
            parts    = [p for p in [swim_lbl, adm_lbl]
                        if p and p not in ('Unknown', '', 'Not Competitive', 'Below Roster Level')]
            r['aiWhy'] = ' · '.join(parts[:2])
        n = len(resolved)
        answer = (
            f'Found {n} schools matching "{query}" — which one did you mean?'
            if n <= 8 else
            f'Found {n} schools matching "{query}".'
        )
        return jsonify({
            'answer':      answer,
            'schools':     resolved,
            'directMatch': False,
            'multiMatch':  True,
        })

    direct_match = resolved[0] if resolved else None

    # Out-of-universe stub — ONLY for school names truly not in the 324.
    # This handles schools like Harvard, Stanford that are outside D3 swimming.
    if not direct_match:
        _desc_words = {
            'find','show','good','best','near','with','help','looking','want',
            'suggest','recommend','schools','colleges','programs','like','similar',
            'strong','competitive','academic','research','liberal','division','d3',
            'private','public','small','large','northeast','south','west','midwest',
            # Question / intent words — prevent "where should I swim" from being
            # treated as a school name stub
            'where','should','would','could','can','what','which','how','why',
            'when','swim','swimming','get','into','for','me','my','i',
        }
        _words = query.lower().split()
        _looks_like_name = (
            1 <= len(_words) <= 5 and
            not any(w in _desc_words for w in _words) and
            not query.lower().endswith('?')
        )
        if _looks_like_name:
            display_name = ' '.join(
                w.upper() if w == w.upper() and len(w) > 1 else w.title()
                for w in query.split()
            )
            oou_meta = _oou_lookup(display_name) or {}
            oou_adm  = (_oou_admission(oou_meta, sat, gpa)
                        if oou_meta else {'label': '', 'color': '#94A3B8', 'total': None})
            direct_match = {
                'school':         display_name,
                'conference':     '',
                'division':       '',
                'adjTier':        '',
                'psf':            1.0,
                'admission':      oou_adm,
                'top3':           [],
                'hasDepth':       False,
                'allEvents':      [],
                'meta':           oou_meta,
                'confTierShort':  '',
                'confTier':       '',
                'confFinish2026': None,
                'confScore2026':  None,
                'confPowerClass': '',
                'hasSwimData':    False,
                'outOfUniverse':  True,
            }

    if direct_match:
        excl_names.add(direct_match['school'])

    candidates = [r for r in all_results if r['school'] not in excl_names]
    if direct_match:
        # Sort by institutional similarity (division + selectivity + conf tier),
        # NOT by swimmer adjPts — so "similar schools" reflects what the school
        # is like, independently of how well this swimmer happens to score there.
        pool = _similarity_sort(candidates, direct_match)[:35]
    else:
        pool = candidates[:35]

    client = _get_anthropic()
    if not client:
        fallback = ([{**direct_match, 'directMatch': True}] if direct_match else []) + pool[:5 if direct_match else 6]
        return jsonify({
            'error': 'AI search is not configured',
            'detail': 'ANTHROPIC_API_KEY is missing or invalid',
            'fallback': fallback[:6],
            'directMatch': bool(direct_match),
        }), 503

    # ── NON-DIRECT: BROAD-POOL PATH or 4-STEP PIPELINE ──────────────────────
    if not direct_match:
        # ── BROAD-POOL PATH ──────────────────────────────────────────────────
        # Triggered for: prestige + personal + swim queries
        #   e.g. "most elite schools I can swim for"
        #        "every academic school I can contribute at"
        #        "all schools where I can make the team"
        #
        # Bypasses Claude's narrow 12–15 candidate generation.
        # Filters the full ~330-school universe to every swim-viable school,
        # sorts by academic selectivity (prestige first) then swim fit,
        # and returns the entire viable pool — typically 50–150 schools.
        _bp_intent = _detect_query_intent(query)
        _bp_intent['has_any_times'] = bool(times)

        _BROAD_EXPLICIT = (
            'all schools', 'every school', 'full list', 'complete list',
            'all the schools', 'every college', 'all colleges',
        )
        _is_broad_pool = (
            (_bp_intent['is_prestige_sort'] or
             any(t in query.lower() for t in _BROAD_EXPLICIT))
            and _bp_intent['is_personal']
            and _bp_intent['is_swim']
            and not _bp_intent['is_explicit_reach']
        )

        if _is_broad_pool:
            excl_set  = set(eliminated) | set(my_list)
            full_pool = [r for r in all_results if r['school'] not in excl_set]

            # Viable = has benchmark data AND swim tier above "Not Competitive".
            # "Below Roster Level" included as near-scoring / development range.
            viable = [
                r for r in full_pool
                if r.get('hasSwimData')
                and r.get('adjTier', '') not in ('', 'Not Competitive')
            ]

            # Primary sort: most selective first (accept% asc; unknown → 999).
            # Secondary: better swim fit wins within the same selectivity band.
            viable.sort(key=lambda r: (
                r['meta'].get('accept') or 999,
                -r.get('adjPts', 0),
            ))

            # Attach fit labels — same logic as main pipeline
            for r in viable:
                adm_lbl  = r.get('admission', {}).get('label', '')
                swim_lbl = r.get('adjTier', '')
                parts    = [p for p in [adm_lbl, swim_lbl] if p and p not in ('Unknown', '')]
                r['aiWhy'] = ' · '.join(parts[:2])

            n       = len(viable)
            top_str = ', '.join(r['school'] for r in viable[:5]) if viable else 'none found'

            # Tier breakdown for answer
            _tier_order = [
                'High-Point Contender', 'Conference Star', 'Top Recruit',
                'Priority Recruit', 'Recruitable', 'Below Roster Level',
            ]
            tier_counts = {t: 0 for t in _tier_order}
            for r in viable:
                t = r.get('adjTier', '')
                if t in tier_counts:
                    tier_counts[t] += 1
            tier_summary = ', '.join(
                f"{cnt} {t}" for t, cnt in tier_counts.items() if cnt > 0
            )

            answer = (
                f"Found {n} programs where you have a realistic shot to contribute, "
                f"ordered from most to least selective. "
                f"Leading the list: {top_str}. "
                f"Swim fit across the pool — {tier_summary}."
            )

            return jsonify({
                'answer':      answer,
                'schools':     viable,
                'directMatch': False,
                'broadPool':   True,
                '_debug': {
                    'intent':     _bp_intent,
                    'poolSize':   len(full_pool),
                    'viableSize': n,
                    'path':       'broad-pool-prestige-swim',
                },
            })

        # ── 4-STEP AI PIPELINE (unchanged for all other queries) ─────────────
        # Step 1 — AI generates candidate pool (~12–15 schools)
        # Step 2 — Hard filter using our truth labels
        # Step 3 — Sort by admissions fit / prestige
        # Step 4 — Return top 6 (or 12 for "show me more")
        vibe_answers  = data.get('vibeAnswers', {}) or {}
        other_prefs_s = data.get('otherPrefs', '') or ''

        # Always build student context — LLM uses it to interpret the query,
        # NOT to pre-filter for fit.
        student_ctx = _build_student_context(
            swimmer_name, gpa, sat, act_score, times, vibe_answers, other_prefs_s,
        )
        cand_sys, cand_usr = _build_candidate_prompt(query, student_ctx)

        try:
            # ── STEP 1: AI generates ~12–15 relevant schools ───────────────
            resp = client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=700,
                system=cand_sys,
                messages=[{'role': 'user', 'content': cand_usr}],
            )
            answer, candidate_names = _parse_candidate_names(resp.content[0].text)

            # Map LLM names → scored universe records (fuzzy match)
            excl_set   = set(eliminated) | set(my_list)
            avail      = [r for r in all_results if r['school'] not in excl_set]
            candidates = _map_to_universe(candidate_names, avail)

            # Fallback if mapping collapses entirely
            if len(candidates) < 3:
                fallback = _pre_sort(all_results, query, eliminated, my_list)[:6]
                return jsonify({
                    'error': 'Candidate mapping too narrow',
                    'fallback': fallback,
                    'directMatch': False,
                }), 200

            # ── STEP 2: Detect intent → objective or personal ───────────────
            intent = _detect_query_intent(query)
            intent['has_any_times'] = bool(times)   # False → no swim scoring possible
            want_more = any(w in query.lower() for w in (
                'more schools', 'more options', 'show me more', 'give me more',
            ))
            limit = 12 if want_more else 6

            if not intent['is_personal']:
                # ── OBJECTIVE query ("best STEM schools", "fastest teams") ──
                # Return Claude's list in its exact order. No Lane4 filtering,
                # no admissions re-sort. We only use our data for display.
                schools       = candidates[:limit]
                removed_debug = []
            else:
                # ── PERSONAL query ("fastest schools I can get into") ───────
                # Filter by admissions threshold and/or swim competitiveness.
                filtered, removed_debug = _hard_filter(candidates, intent)

                # Prestige / ceiling sort: re-rank survivors by academic
                # selectivity when the user asked for "hardest / strongest
                # academic / most selective / elite" options.
                # Ranking: lowest _ADM_LABEL_SCORE = hardest to get into = first.
                # Ties (same label) broken by adjPts descending — better swim
                # fit wins within the same academic difficulty band.
                if intent.get('is_prestige_sort') and filtered:
                    filtered = sorted(
                        filtered,
                        key=lambda r: (
                            _ADM_LABEL_SCORE.get(
                                r.get('admission', {}).get('label', 'Unknown'), 25
                            ),
                            -r.get('adjPts', 0),
                        ),
                    )

                schools = filtered[:limit]

            # Attach a brief fit label to each card (no extra AI call)
            for r in schools:
                adm_lbl  = r.get('admission', {}).get('label', '')
                swim_lbl = r.get('adjTier', '')
                parts    = [p for p in [adm_lbl, swim_lbl] if p and p not in ('Unknown', '')]
                r['aiWhy'] = ' · '.join(parts[:2])

            # ── DEBUG — visible in DevTools → Network tab ───────────────────
            survived_reason = (
                'objective query — AI order preserved, no filtering'
                if not intent['is_personal']
                else 'passed admissions/swim filters, AI order preserved'
            )
            debug_kept = [
                {
                    'school':      r['school'],
                    'admissions':  r.get('admission', {}).get('label', 'Unknown'),
                    'swimTier':    r.get('adjTier', 'n/a') or '(empty — no scorable events)',
                    'hasSwimData': r.get('hasSwimData', False),
                    'survived':    survived_reason,
                }
                for r in schools
            ]

            return jsonify({
                'answer':      answer,
                'schools':     schools,
                'directMatch': False,
                '_debug': {
                    'intent':       intent,
                    'aiCandidates': candidate_names,
                    'mapped':       [r['school'] for r in candidates],
                    'removed':      removed_debug,
                    'kept':         debug_kept,
                    'finalTop6':    [r['school'] for r in schools[:6]],
                },
            })

        except json.JSONDecodeError as e:
            fallback = _pre_sort(all_results, query, eliminated, my_list)[:6]
            return jsonify({
                'error': 'AI returned malformed JSON',
                'detail': str(e),
                'fallback': fallback,
                'directMatch': False,
            }), 200

        except (ValueError, Exception) as e:
            fallback = _pre_sort(all_results, query, eliminated, my_list)[:6]
            return jsonify({
                'error': 'Search failed',
                'detail': str(e),
                'fallback': fallback,
                'directMatch': False,
            }), 200

    # ── DIRECT MATCH PATH — unchanged (similarity sort + Claude picks 5) ─────
    system_prompt = (
        "You are Lane4. Respond ONLY with a valid JSON object. "
        "No markdown. No explanation. Start with { end with }. "
        "Keep 'why' fields under 15 words each. Keep 'answer' under 30 words."
    )

    school_lines = '\n'.join(_build_school_line(i, r) for i, r in enumerate(pool))
    user_prompt  = (
        f'The user searched for "{direct_match["school"]}" '
        f'({direct_match["conference"] or "independent"}, {direct_match.get("division","")}, '
        f'program strength: {_program_strength_desc(direct_match)}).\n\n'
        f"{swimmer_name}: GPA {gpa}, SAT {sat}" + (f", ACT {act_score}" if act_score else "") + ".\n\n"
        f"This list is already sorted by institutional similarity to {direct_match['school']} "
        f"(same division, selectivity, and conference tier). "
        "Pick the 5 schools that are most genuinely similar — consider academic culture, "
        "school size, mission, and athletic program level. Ignore the swimmer's times when judging similarity. "
        "Return ONLY JSON.\n\n"
        f"{school_lines}\n\n"
        'JSON format:\n{"answer":"1-2 sentences why these schools are similar to '
        f'{direct_match["school"]}' + '","schools":[{"number":1,"why":"under 15 words"}]}'
    )

    try:
        resp = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=800,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        raw_text = resp.content[0].text
        answer, ai_schools = _parse_search_response(raw_text, pool)

        _dm_conf  = direct_match.get('conference', '')
        _dm_div   = direct_match.get('division', '')
        _dm_str   = _program_strength_desc(direct_match)
        _dm_parts = [p for p in [_dm_conf, _dm_div, _dm_str] if p]
        _dm_why   = ' · '.join(_dm_parts) if _dm_parts else 'Matched from our database'
        dm        = {**direct_match, 'directMatch': True, 'aiWhy': _dm_why}
        schools   = [dm] + ai_schools

        return jsonify({'answer': answer, 'schools': schools, 'directMatch': True})

    except json.JSONDecodeError as e:
        fallback = [{**direct_match, 'directMatch': True}] + pool[:5]
        return jsonify({
            'error': 'AI returned malformed JSON',
            'detail': str(e),
            'fallback': fallback[:6],
            'directMatch': True,
        }), 200

    except ValueError as e:
        fallback = [{**direct_match, 'directMatch': True}] + pool[:5]
        return jsonify({'error': str(e), 'fallback': fallback[:6], 'directMatch': True}), 200

    except Exception as e:
        fallback = [{**direct_match, 'directMatch': True}] + pool[:5]
        return jsonify({
            'error': 'Search failed',
            'detail': str(e),
            'fallback': fallback[:6],
            'directMatch': True,
        }), 200


# --- AI / DEEP DIVE ROUTES ---

@app.route('/api/deep-dive/academic', methods=['POST'])
def deep_dive_academic():
    """
    Lazy-load the "More about this program" academic expansion for a school.

    Body: { school, primaryMajor, location, schoolVibe }
    Response: { body: "plain prose" } or { error }
    """
    data         = request.json or {}
    school_name  = data.get('school', '').strip()
    major        = data.get('primaryMajor', '').strip()
    location     = data.get('location', '').strip()
    vibe         = data.get('schoolVibe', '').strip()

    if not school_name or not major:
        return jsonify({'error': 'school and primaryMajor are required'}), 400

    client = _get_anthropic()
    if not client:
        return jsonify({'error': 'AI is not configured — add ANTHROPIC_API_KEY to enable deep dives'}), 200

    system_prompt = (
        "You are an experienced college advisor who understands how academic programs work at universities.\n\n"
        "Your job is to explain an academic program clearly to a student and their parents so they understand "
        "how the program actually works and what the experience would be like.\n\n"
        "This content appears in the 'More about this program' expansion inside a school Deep Dive. "
        "The summary Deep Dive already introduced the school, so this section should add new insight "
        "rather than repeat information.\n\n"
        "GOAL\n"
        "Help the reader understand the structure and realities of the academic program. Focus on details "
        "a student might not immediately learn from a quick look at the school's website.\n\n"
        "THINK FIRST\n"
        "Before writing, briefly identify 3-5 distinctive aspects of the program that a student might "
        "not already know. These might include program structure, unusual pathways, research access, "
        "cross-registration opportunities, internship patterns, or career outcomes. "
        "Use those insights to guide the explanation. Do not output the list.\n\n"
        "WRITING STYLE\n"
        "Write like an experienced college advisor explaining the program to a student and parent.\n"
        "The tone should be: knowledgeable, clear, natural, engaging.\n"
        "Avoid sounding like: a marketing brochure, an academic paper, an AI assistant.\n\n"
        "STRUCTURE\n"
        "Use short paragraphs. Each paragraph should explain one idea. "
        "Depth should come from additional short paragraphs, not longer sentences. "
        "Most expansions will include 4-6 short paragraphs. "
        "No section headings. No bullet points. Just short paragraphs.\n\n"
        "CONTENT GUIDELINES\n"
        "Focus on explaining how the program actually works. "
        "Helpful topics often include:\n"
        "- Department structure\n"
        "- Cross-registration options\n"
        "- Undergraduate research access\n"
        "- Program pathways (for example 3-2 engineering or interdisciplinary tracks)\n"
        "- Internship pipelines\n"
        "- Career directions or graduate study trends\n"
        "- Practical realities students should know\n"
        "Avoid repeating information already stated in the Deep Dive summary.\n\n"
        "STYLE RULES\n"
        "- Write clearly and avoid long academic sentences.\n"
        "- No em dashes anywhere.\n"
        "- Do not address the reader directly.\n"
        "- Do not over-personalize using hobbies or profile details.\n"
        "- Avoid marketing phrases such as 'renowned program' or 'world-class faculty'.\n"
        "- Avoid unverifiable claims about specific employers recruiting from the school.\n"
        "- Avoid listing elite graduate schools unless it is widely documented.\n\n"
        "QUALITY CHECK\n"
        "If the writing becomes dense, academic, or generic, rewrite it so it is clearer, "
        "more natural, and easier to read."
    )

    user_prompt = (
        f"Write the 'More about this program' expansion for the {major} program at {school_name}.\n\n"
        f"School: {school_name}\n"
        + (f"Location: {location}\n" if location else "")
        + (f"School character: {vibe}\n" if vibe else "")
        + "\nWrite 4-6 short paragraphs. Each paragraph covers one idea about how the program "
        "actually works at this specific school. Focus on structure, research access, "
        "distinctive pathways, and practical realities. "
        "No headings. No bullet points. No em dashes. No marketing language. "
        "Do not address the reader directly."
    )

    try:
        resp = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=900,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        body = resp.content[0].text.strip()
        return jsonify({'body': body})
    except Exception as e:
        return jsonify({'error': str(e)}), 200


@app.route('/api/deep-dive', methods=['POST'])
def deep_dive():
    """
    Generate the 8-section deep dive narrative for one school.

    Body: { school }   (school must match a key in score_all results)
    Response: { sections: [{title, body}] } or { error }
    """
    data     = request.json or {}
    school   = data.get('school', '').strip()
    prof_ovr = data.get('profile', {})

    if not school:
        return jsonify({'error': 'school is required'}), 400

    times = prof_ovr.get('times') or JAMES['times']
    sat   = int(prof_ovr.get('sat')  or JAMES['sat'])
    gpa   = float(prof_ovr.get('gpa') or JAMES['gpa'])
    swimmer_name = prof_ovr.get('name') or JAMES['name']
    math_sat         = prof_ovr.get('mathSat',          JAMES.get('mathSat', ''))
    sat_projected    = prof_ovr.get('satProjected',     JAMES.get('satProjected', ''))
    math_sat_proj    = prof_ovr.get('mathSatProjected', JAMES.get('mathSatProjected', ''))
    act_score        = prof_ovr.get('actScore',         JAMES.get('actScore', 0)) or 0
    ap_count         = prof_ovr.get('apCount',          JAMES.get('apCount',  0)) or 0
    grad_year        = prof_ovr.get('gradYear',         '2026')
    # ONE unified pool — all ~324 schools through the same builder
    all_results = build_school_universe(times, sat, gpa)
    result = next((r for r in all_results if r['school'] == school), None)
    is_oou = False

    if result is None:
        # School not in the D3 universe — check out-of-universe well-known schools
        oou_meta_found = _oou_lookup(school)
        if oou_meta_found:
            oou_adm = _oou_admission(oou_meta_found, sat, gpa)
            result = {
                'school':         school,
                'conference':     '',
                'division':       '',
                'adjTier':        '',
                'psf':            1.0,
                'admission':      oou_adm,
                'top3':           [],
                'hasDepth':       False,
                'allEvents':      [],
                'meta':           oou_meta_found,
                'confTierShort':  '',
                'confTier':       '',
                'confFinish2026': None,
                'confScore2026':  None,
                'confPowerClass': '',
                'hasSwimData':    False,
                'outOfUniverse':  True,
            }
            is_oou = True
        else:
            return jsonify({'error': f'School "{school}" not found'}), 404

    client = _get_anthropic()
    if not client:
        return jsonify({
            'error': 'AI deep dive is not configured',
            'detail': 'ANTHROPIC_API_KEY is missing or invalid',
        }), 503

    meta = result['meta']
    top3_text  = _build_top3_text(result['top3'])
    vibe_answers = data.get('vibeAnswers') or prof_ovr.get('vibe') or {}
    other_prefs  = data.get('otherPrefs', '')
    vibe_lines   = _build_vibe_lines(vibe_answers, other_prefs)

    _merit_sat = sat or (_act_to_sat(act_score) if act_score else 0)
    money = _estimate_merit_block(
        merit_level = meta.get('merit', 'moderate'),
        sat         = _merit_sat,
        gpa         = gpa,
        sat_median  = meta.get('satMedian', 0),
        accept      = meta.get('accept', 50),
    )
    money_block = (
        f"MONEY DATA — use these exact figures, do not invent different numbers:\n"
        f"Estimated COA: {money['coa']}\n"
        f"Estimated Merit: {money['merit']}"
        + (" (based on your academics)" if money['has_merit'] else "") + "\n"
        f"Estimated Net: {money['net']}\n"
        f"Merit note: {money['note']}"
    )

    vibe_block = ''
    if vibe_lines:
        vibe_block = (
            f"\n{swimmer_name.upper()}'S PERSONALITY & PREFERENCES "
            f"(use these to personalize Campus Life and tone):\n{vibe_lines}\n"
        )

    hidden_ivy_note = '\nThis is a Hidden Ivy — academically elite, employer-respected, without the brand tax.' if meta.get('hiddenIvy') else ''
    stem_note       = '\nStrong STEM programs.' if meta.get('stem') else ''

    sat_detail = f"SAT {sat}" if sat else ""
    if sat and math_sat:
        sat_detail += f" (math {math_sat})"
    if act_score:
        sat_detail += (", " if sat_detail else "") + f"ACT {act_score}"
    ap_detail = f", {ap_count} projected APs" if ap_count else ""

    # Structured major inputs (take priority over vibe career/academic fallback)
    primary_major   = (prof_ovr.get('primaryMajor')   or data.get('primaryMajor',   '')).strip()
    secondary_major = (prof_ovr.get('secondaryMajor') or data.get('secondaryMajor', '')).strip()

    # Determine academic direction for optional section.
    # Source of truth: primaryMajor (structured picker), then secondaryMajor.
    # Fallback: academicGoal from vibe (vibe.academic). Never inferred from career vibe.
    if primary_major:
        _major_parts = [primary_major]
        if secondary_major:
            _major_parts.append(secondary_major)
        academic_direction = ' / '.join(_major_parts)
    else:
        academic_raw = (vibe_answers.get('academic') or '').strip()
        _generic = academic_raw in ('', 'Genuinely want to be well-rounded')
        academic_direction = academic_raw if not _generic else None

    # Admission comparison block
    sat_median   = meta.get('satMedian', 0)
    sat25        = meta.get('sat25', 0)
    sat75        = meta.get('sat75', 0)
    gpa_mean     = meta.get('gpaMean', 0)
    accept_rate  = meta.get('accept', 0)
    adm_swimmer  = f"GPA {gpa} unweighted"
    if sat:
        adm_swimmer += f", SAT {sat}"
    if act_score:
        adm_swimmer += f", ACT {act_score}"
    adm_school_parts = [f"~{accept_rate}% acceptance rate"]
    if sat_median:
        adm_school_parts.append(f"SAT median ~{sat_median}")
    if sat25 and sat75:
        adm_school_parts.append(f"SAT range ~{sat25}-{sat75}")
    if gpa_mean:
        adm_school_parts.append(f"GPA average ~{gpa_mean}")
    admission_comparison = (
        f"ADMISSION COMPARISON (use to write 'Are You Admissible?' — do not invent different numbers):\n"
        f"Swimmer: {adm_swimmer}\n"
        f"School: {', '.join(adm_school_parts)}\n"
        f"Admission outlook: {result['admission']['label']}"
    )

    prog_strength = _program_strength_desc(result)
    conf_tier_short = result.get('confTierShort', '')
    super_powerhouse_note = (
        f"\nIMPORTANT: {result['school']} is a Super Powerhouse — they dominate their conference "
        f"and recruit well above what most peer schools in {result['conference']} can attract. "
        "In 'In the Pool', call this out directly and tell the swimmer to look closely "
        "at the current roster and committed recruits before assuming a spot."
    ) if conf_tier_short == '1A' else ''

    system_prompt = (
        LANE4_DEEP_DIVE_PROMPT + "\n\n"
        "Lane4 Technical Vocabulary (always apply):\n"
        "- Never use the word 'tier' — describe programs as 'Super Powerhouse', 'Powerhouse', "
        "'dominant in conference', 'competitive', etc.\n"
        "- 'Hidden Ivy' = academically elite and employer-respected without the Stanford rejection "
        "rate. Use naturally when applicable.\n"
        "- Never use the words 'profile', 'good school', 'strong fit', or 'also'.\n"
        "- No em dashes anywhere in the output.\n"
        "- Respond using markdown sections starting with ## for each section title.\n"
        "- 2-3 sentences per section. Short paragraphs. Strong declarative sentences."
    )

    ivy_note = '\nThis is an Ivy League school — need-based aid only, no merit scholarships.' if meta.get('ivyLeague') else ''

    # Build optional academic section instruction
    if academic_direction:
        _school_nm = result['school']
        acad_section_instr = (
            "## Academic Program\n"
            "Use EXACTLY this heading: 'Academic Program'\n"
            f"Major focus: {academic_direction} at {_school_nm}. "
            f"This is the highest-priority section when a major is known. 4-5 sentences. Be specific.\n"
            f"Cover: the exact department or program name at {_school_nm}; whether it sits in "
            f"engineering, arts and sciences, a dedicated college, or another structure; "
            f"how established or respected the program is; undergraduate research or lab access; "
            f"practical vs theoretical tilt; faculty accessibility; and what makes it distinctive at "
            f"{_school_nm} specifically. Include employer or grad school outcomes where relevant. "
            "Do not write generic 'strong academics' language. Sound informed and specific.\n\n"
        )
    else:
        acad_section_instr = (
            "[SKIP the academic section entirely. No major has been provided. "
            "Do not include an academic program section.]\n"
        )

    # Student Experience "More" section — always included
    _more_student_exp = (
        "\n## More: Student Experience\n"
        "Use EXACTLY this heading: 'More: Student Experience'\n"
        "Expanded student life section (shown behind a 'More about student life' button). 4-6 sentences:\n"
        "- Academic pressure level and pacing at this specific school\n"
        "- Collaboration vs competition in the academic culture\n"
        "- What students actually do outside of class and team\n"
        "- Social life anchors (campus, city, team, greek life, etc.)\n"
        "- What students commonly praise and what they commonly complain about\n"
        "No direct callbacks to stated preferences. No overpersonalization.\n"
    )

    # Outcomes + Career Paths — always included
    _outcomes_section = (
        "\n## Outcomes\n"
        "3-4 sentences. Where do graduates from this school typically land? "
        "Cover: employment patterns, typical industries, graduate school rates if known, "
        "geographic patterns. Be specific to this school. No generic statements.\n"
        "\n## More: Career Paths\n"
        "Use EXACTLY this heading: 'More: Career Paths'\n"
        "Expanded career section (shown behind a 'More about career paths' button). 6-8 sentences:\n"
        "- Typical employers by name if known (not just 'finance' but specific firms)\n"
        "- Graduate school pipelines: where graduates apply, acceptance rates if known\n"
        "- Industry concentrations this school is known for placing into\n"
        "- Geographic career advantages: does location or alumni base help in specific cities\n"
        "- Alumni network strength and how alumni engage with undergraduates\n"
        "- On-campus recruiting, employer partnerships, or career center strengths\n"
        "- Honest gaps: industries or regions where this school's network is thin\n"
        "Sound informed. Name specifics where possible. Do not promote.\n"
    )

    if is_oou:
        user_prompt = (
            f"Write a deep dive for {swimmer_name} considering {result['school']}.\n\n"
            f"SWIMMER: {swimmer_name}, Class of {grad_year}, GPA {gpa} unweighted, "
            f"{sat_detail}{ap_detail}."
            f"{vibe_block}\n"
            f"SCHOOL: {result['school']}\n"
            f"School vibe: {meta.get('vibe', '')}\n"
            f"Location: {meta.get('location', '')}\n"
            f"{admission_comparison}\n"
            f"{money_block}\n"
            f"{hidden_ivy_note}{ivy_note}{stem_note}\n\n"
            "NOTE: This school is not in our swim recruiting database. The swimmer is comparing it "
            "against D3 options — be honest about what choosing this school means for swim.\n\n"
            "Write exactly these sections in this order:\n\n"
            "## Bottom Line\n"
            "2-3 sentences. School value + academic/personal fit + overall verdict.\n"
            f"## What {result['school']} Is Known For\n"
            "School identity. Make it feel important and real. Prestige when deserved. 3-4 sentences.\n"
            f"{acad_section_instr}"
            "## Are You Admissible?\n"
            "Use the ADMISSION COMPARISON above. Compare swimmer numbers to school numbers. "
            "Plain-English read. One brief note on whether swim support might help if applicable.\n"
            "## What It Costs\n"
            "Use EXACTLY the MONEY DATA figures above. Do not change the numbers. "
            "Cover COA, merit or no merit, net cost, aid philosophy.\n"
            "## Campus Life\n"
            "What do four years here actually feel like? Size, energy, setting, social scene. 3-4 sentences.\n"
            f"{_more_student_exp}"
            "## How It Compares to Your D3 Options\n"
            "Be honest — what does choosing this school mean for continuing to swim competitively?\n"
            f"{_outcomes_section}"
        )
    else:
        user_prompt = (
            f"Write a deep dive for {swimmer_name} considering {result['school']}.\n\n"
            f"SWIMMER: {swimmer_name}, Class of {grad_year}, GPA {gpa} unweighted, "
            f"{sat_detail}{ap_detail}."
            f"{vibe_block}\n"
            f"SWIM DATA AT {result['school'].upper()} ({result['conference']}):\n"
            f"Top events: {top3_text}\n"
            f"Program strength: {prog_strength}\n"
            f"{super_powerhouse_note}\n"
            f"School vibe: {meta.get('vibe', '')}\n"
            f"Location: {meta.get('location', '')}\n"
            f"{admission_comparison}\n"
            f"{money_block}\n"
            f"{hidden_ivy_note}{ivy_note}{stem_note}\n\n"
            "Write exactly these sections in this order. "
            "Swim fit is explained ONCE in 'In the Pool' — do not repeat it elsewhere. "
            "Use the free response lightly and naturally — no overpersonalization. "
            "Use 'Hidden Ivy' naturally if applicable.\n\n"
            "## Bottom Line\n"
            "2-3 sentences. Swim reality + school value + overall verdict. No hedging.\n"
            "## In the Pool\n"
            "Where this swimmer lands on the team. What that means. Trajectory if they hold or drop time. "
            "Sound like a coach talking plainly. No internal metrics.\n"
            "## Coach Interest — What to Expect\n"
            "Likely level of recruiting engagement. Will they respond quickly? Is this swimmer a priority? "
            "What moves the needle: time drops, roster gaps, academic strength, event needs.\n"
            f"## What {result['school']} Is Known For\n"
            "School identity. Make it feel important and real. Prestige and seriousness when deserved. 3-4 sentences.\n"
            f"{acad_section_instr}"
            "## Are You Admissible?\n"
            "Use the ADMISSION COMPARISON above. Compare swimmer numbers to school numbers directly. "
            "Plain-English read: in range, above, slightly below, or real reach. "
            "If highly selective, say so. One brief sentence on whether swim recruit support helps, if applicable.\n"
            "## What It Costs\n"
            "Use EXACTLY the MONEY DATA figures above. Do not change the numbers. "
            "Cover COA, merit or no merit, net cost, aid philosophy. Practical family language.\n"
            "## Campus Life\n"
            "What do four years here actually feel like? Size, energy, setting, social scene, "
            "what kind of student thrives. No brochure copy. 3-4 sentences.\n"
            f"{_more_student_exp}"
            f"{_outcomes_section}"
        )

    try:
        resp = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=3200,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_prompt}],
        )
        raw = resp.content[0].text

        # Split on section headers — per OUTPUT_SCHEMA response parsing spec
        # Section identity uses fixed header strings, never title-text guessing.

        def _classify_section(t):
            t = t.lower().strip()
            if 'bottom line' in t:                              return 'bottom_line'
            if t == 'academic program':                         return 'academic_program'
            if t == 'campus life':                              return 'student_experience'
            if t == 'outcomes':                                 return 'outcomes'
            if t == 'more: academic':                           return 'more_academic'
            if t == 'more: student experience':                 return 'more_student_experience'
            if t == 'more: career paths':                       return 'more_career_paths'
            return 'content'

        parts  = re.split(r'^## ', raw, flags=re.MULTILINE)
        sections = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = part.split('\n', 1)
            title = lines[0].strip()
            body  = lines[1].strip() if len(lines) > 1 else ''
            if title and not title.startswith('#') and not title.startswith('---'):
                sections.append({'title': title, 'body': body,
                                 'type': _classify_section(title)})

        if not sections:
            return jsonify({'error': 'AI returned empty deep dive', 'raw': raw}), 200

        return jsonify({
            'school':    result['school'],
            'sections':  sections,
            'admission': result['admission'],
            'adjTier':   result['adjTier'],
            'meta':      meta,
        })

    except Exception as e:
        return jsonify({'error': 'Deep dive failed', 'detail': str(e)}), 200


@app.route('/api/coach-email', methods=['POST'])
def coach_email():
    """
    Generate deterministic coach email for one school. No AI call.

    Body: { school, profile? }
    Response: { subject, body }
    """
    data     = request.json or {}
    school   = data.get('school', '').strip()
    prof_ovr = data.get('profile', {})

    if not school:
        return jsonify({'error': 'school is required'}), 400

    times         = prof_ovr.get('times') or JAMES['times']
    sat           = int(prof_ovr.get('sat')  or JAMES['sat'])
    gpa           = float(prof_ovr.get('gpa') or JAMES['gpa'])
    act_score     = prof_ovr.get('actScore', JAMES.get('actScore', 0)) or 0
    ap_count      = prof_ovr.get('apCount',  JAMES.get('apCount',  0)) or 0
    swimmer_name  = prof_ovr.get('name') or JAMES['name']
    grad_year     = prof_ovr.get('gradYear',     '2026')

    all_results = build_school_universe(times, sat, gpa)
    result = next((r for r in all_results if r['school'] == school), None)

    if result is None:
        return jsonify({'error': f'School "{school}" not found'}), 404

    meta  = result['meta']
    top3  = result['top3']
    best  = top3[0] if top3 else None

    if best is None:
        return jsonify({'error': 'No scored events found for this school'}), 400

    # Performance descriptor
    if best['place'] <= 1.5:
        perf = f"projected to win the {best['event']}"
    elif best['place'] <= 3.5:
        perf = f"projected to podium in the {best['event']}"
    else:
        perf = f"projected as a conference A finalist in the {best['event']}"

    second     = f" I also project to score in the {top3[1]['event']}." if len(top3) > 1 else ''
    stem_note  = ' Your programs in engineering and CS align directly with my academic direction.' if meta.get('stem') else ''
    merit_note = " I've also been looking closely at your merit scholarship opportunities." if meta.get('merit') == 'high' else ''

    # Build times summary from actual swimmer times
    time_entries = list(times.items())
    if time_entries:
        times_text = ', '.join(f"{t} in the {e.lower()}" for e, t in time_entries[:3])
    else:
        times_text = 'competitive times across multiple events'

    # Determine grad year class label
    class_label = f"Class of {grad_year}"

    subject = f"Prospective Student-Athlete Inquiry — {class_label} | Competitive Swimmer"
    body = (
        f"Dear Coach,\n\n"
        f"My name is {swimmer_name} and I'm a student in the {class_label} with strong interest "
        f"in {result['school']}'s swim program.\n\n"
        f"At the {result['conference']} conference level, I'm {perf}.{second} "
        f"My current bests include {times_text}.\n\n"
        f"Academically I carry a {gpa} GPA"
        + (f", {sat} SAT" if sat else "")
        + (f" / {act_score} ACT" if act_score else "")
        + (f", with {ap_count} APs projected" if ap_count else "")
        + f".{stem_note}{merit_note}\n\n"
        f"I'd love to connect about your program. Would you have time for a brief call or campus visit?\n\n"
        f"Thank you,\n{swimmer_name}"
    )

    return jsonify({'subject': subject, 'body': body})



# ── User-admin panel (/admin) ──────────────────────────────────────────────

@app.route('/admin')
@user_admin_required
def user_admin_page():
    return send_from_directory('static', 'admin.html')


@app.route('/api/ua/schools', methods=['GET'])
@user_admin_required
def ua_schools():
    school_conf = {s['school']: s.get('conference', '') for s in EXPLORE_SCHOOLS}
    candidates  = _load_candidates_manifest()
    curated     = _load_curated_manifest()
    imgs        = _load_school_images()
    result = []
    for name in sorted(school_conf.keys()):
        cands    = candidates.get(name, [])
        cur      = curated.get(name, {})
        stored   = imgs.get(name, {})
        result.append({
            'name':            name,
            'conference':      school_conf.get(name, ''),
            'has_candidates':  bool(cands),
            'candidate_count': len(cands),
            'is_curated':      bool(cur.get('hero_images') or cur.get('selected_in_order')),
            'stored_hero':     stored.get('hero'),
            'stored_swim':     stored.get('swim'),
            'stored_student':  stored.get('student_life'),
        })
    return jsonify(result)


@app.route('/api/ua/candidates/<path:school>', methods=['GET'])
@user_admin_required
def ua_candidates(school):
    candidates = _load_candidates_manifest()
    curated    = _load_curated_manifest()
    imgs       = _load_school_images()
    cands = candidates.get(school, [])
    cur   = curated.get(school, {})
    stored = imgs.get(school, {})
    # Apply blocklist
    blocklist = _load_blocklist()
    if blocklist:
        cands = [c for c in cands if c.get('url', '') not in blocklist]
    return jsonify({'candidates': cands, 'curated': cur, 'stored': stored})


@app.route('/api/ua/fetch-candidates', methods=['POST'])
@user_admin_required
def ua_fetch_candidates():
    body   = request.get_json(silent=True) or {}
    school = (body.get('school') or '').strip()
    if not school:
        return jsonify({'error': 'missing school'}), 400
    try:
        from harvest_candidates import fetch_candidates, _rescore_and_trim_by_category
        new_candidates = fetch_candidates(school)
        blocklist = _load_blocklist()
        if blocklist:
            new_candidates = [c for c in new_candidates if c.get('url', '') not in blocklist]
        manifest = _load_candidates_manifest()
        existing = manifest.get(school, [])
        existing_urls = {c['url'] for c in existing}
        merged  = existing + [c for c in new_candidates if c['url'] not in existing_urls]
        trimmed = _rescore_and_trim_by_category(merged, per_cat_limit=24)
        manifest[school] = trimmed
        with open(_CANDIDATES_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        return jsonify({'ok': True, 'count': len(new_candidates)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _school_city(school_name: str) -> str | None:
    """Return the city portion of a school's location from SCHOOL_META, if known.

    E.g. "Walla Walla, WA" → "Walla Walla"
    """
    meta = SCHOOL_META.get(school_name, {})
    loc  = meta.get('location', '') or ''
    if loc:
        city = loc.split(',')[0].strip()
        return city if city else None
    return None


@app.route('/api/ua/fetch-more', methods=['POST'])
@user_admin_required
def ua_fetch_more():
    """Fetch additional candidates for one section (category) of a school."""
    body     = request.get_json(silent=True) or {}
    school   = (body.get('school') or '').strip()
    category = (body.get('category') or '').strip()   # campus | pool | student_life
    if not school or category not in ('campus', 'pool', 'student_life'):
        return jsonify({'error': 'missing or invalid school/category'}), 400
    try:
        from harvest_candidates import fetch_candidates_for_category, _rescore_and_trim_by_category
        city      = _school_city(school)
        new_cands = fetch_candidates_for_category(school, category, city=city)
        blocklist = _load_blocklist()
        if blocklist:
            new_cands = [c for c in new_cands if c.get('url', '') not in blocklist]
        manifest = _load_candidates_manifest()
        existing = manifest.get(school, [])
        existing_urls = {c['url'] for c in existing}
        merged  = existing + [c for c in new_cands if c['url'] not in existing_urls]
        trimmed = _rescore_and_trim_by_category(merged, per_cat_limit=32)
        manifest[school] = trimmed
        with open(_CANDIDATES_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        added = [c for c in new_cands if c['url'] not in existing_urls]
        return jsonify({'ok': True, 'added': len(added), 'candidates': trimmed})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ua/redo-section', methods=['POST'])
@user_admin_required
def ua_redo_section():
    """Discard existing candidates for one category and run a fresh fetch."""
    body     = request.get_json(silent=True) or {}
    school   = (body.get('school') or '').strip()
    category = (body.get('category') or '').strip()
    if not school or category not in ('campus', 'pool', 'student_life'):
        return jsonify({'error': 'missing or invalid school/category'}), 400
    try:
        from harvest_candidates import fetch_candidates_for_category, _rescore_and_trim_by_category
        # Strip existing candidates for this category, keep other categories
        manifest = _load_candidates_manifest()
        existing = manifest.get(school, [])
        kept = [c for c in existing if c.get('category') != category]
        # Fresh fetch for just this category — pass city for better pool queries
        city      = _school_city(school)
        new_cands = fetch_candidates_for_category(school, category, city=city)
        blocklist = _load_blocklist()
        if blocklist:
            new_cands = [c for c in new_cands if c.get('url', '') not in blocklist]
        merged  = kept + new_cands
        trimmed = _rescore_and_trim_by_category(merged, per_cat_limit=32)
        manifest[school] = trimmed
        with open(_CANDIDATES_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        return jsonify({'ok': True, 'count': len(new_cands), 'candidates': trimmed})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ua/approve', methods=['POST'])
@user_admin_required
def ua_approve():
    body   = request.get_json(silent=True) or {}
    school = (body.get('school') or '').strip()
    if not school:
        return jsonify({'error': 'missing school'}), 400
    hero_images         = body.get('hero_images', [])
    pool_images         = body.get('pool_images', [])
    student_life_images = body.get('student_life_images', [])
    curated = _load_curated_manifest()
    curated[school] = {
        'hero_images':                hero_images,
        'pool_images':                pool_images,
        'student_life_images':        student_life_images,
        'approved_hero_image':        hero_images[0]         if hero_images         else None,
        'approved_pool_image':        pool_images[0]         if pool_images         else None,
        'approved_student_life_image': student_life_images[0] if student_life_images else None,
        'approved_extra_images':      hero_images[1:] + pool_images[1:] + student_life_images[1:],
        'saved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    _save_curated_manifest(curated)
    _push_curated_to_school_images(
        school,
        hero_images[0]         if hero_images         else None,
        pool_images[0]         if pool_images         else None,
        student_life_images[0] if student_life_images else None,
    )
    return jsonify({'ok': True, 'school': school})


@app.route('/api/ua/ban', methods=['POST'])
@user_admin_required
def ua_ban_images():
    """Permanently ban image URL(s) from the curator and candidates manifest."""
    body = request.get_json(silent=True) or {}
    urls_to_ban = [u for u in body.get('urls', []) if u and isinstance(u, str)]
    if not urls_to_ban:
        return jsonify({'error': 'No URLs provided'}), 400
    bl = _load_blocklist()
    bl.update(urls_to_ban)
    _save_blocklist(bl)
    # Prune banned URLs from candidates manifest immediately
    manifest = _load_candidates_manifest()
    changed = False
    for school in list(manifest.keys()):
        before = len(manifest[school])
        manifest[school] = [c for c in manifest[school] if c.get('url', '') not in bl]
        if len(manifest[school]) != before:
            changed = True
    if changed:
        with open(_CANDIDATES_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
    return jsonify({'ok': True, 'banned': len(urls_to_ban), 'total_in_blocklist': len(bl)})


@app.route('/api/ua/admins', methods=['GET'])
@user_admin_required
def ua_list_admins():
    """Return all admin records for the admin management UI."""
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT email, active, created_by,
                           TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') AS created_at
                    FROM admins
                    ORDER BY created_at
                """)
                rows = [dict(r) for r in cur.fetchall()]
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ua/admins/create', methods=['POST'])
@user_admin_required
def ua_create_admin():
    """Add a new active admin by email (no password required for user-admin access)."""
    body  = request.get_json(silent=True) or {}
    email = (body.get('email') or '').strip().lower()
    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'Valid email required'}), 400
    created_by = session.get('email', 'unknown')
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO admins (email, active, created_by)
                    VALUES (%s, TRUE, %s)
                    ON CONFLICT (email) DO UPDATE SET active = TRUE
                    RETURNING email
                """, (email, created_by))
                result = cur.fetchone()
            conn.commit()
        return jsonify({'ok': True, 'email': result[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- LEGACY ADMIN CURATE ROUTES (password-based admin session) ---

@app.route('/admin/login', methods=['GET'])
def admin_login_page():
    if session.get('admin_email'):
        return redirect('/admin/curate')
    return send_from_directory('static', 'admin_login.html')


@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    body     = request.get_json(silent=True) or {}
    email    = (body.get('email') or '').strip().lower()
    password = (body.get('password') or '').strip()
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT password_hash FROM admins WHERE email = %s', (email,))
                row = cur.fetchone()
        if not row or not check_password_hash(row[0], password):
            return jsonify({'error': 'Incorrect email or password'}), 401
        session['admin_email'] = email
        return jsonify({'ok': True, 'email': email})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/logout', methods=['POST'])
def api_admin_logout():
    session.pop('admin_email', None)
    return jsonify({'ok': True})


@app.route('/api/admin/me', methods=['GET'])
def api_admin_me():
    """Return current admin session info — used by frontend to show/hide Admin tab."""
    email = session.get('admin_email')
    if not email:
        return jsonify({'is_admin': False})
    return jsonify({'is_admin': True, 'email': email})


@app.route('/api/admin/list-admins', methods=['GET'])
@admin_required
def api_admin_list_admins():
    """List all admin accounts."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT email, created_by, created_at FROM admins ORDER BY created_at')
                rows = cur.fetchall()
        return jsonify([
            {'email': r[0], 'created_by': r[1] or 'bootstrap', 'created_at': str(r[2])}
            for r in rows
        ])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/create-admin', methods=['POST'])
@admin_required
def api_admin_create_admin():
    """Create a new admin account. Only existing admins can do this."""
    body     = request.get_json(silent=True) or {}
    email    = (body.get('email') or '').strip().lower()
    password = (body.get('password') or '').strip()
    if not email or '@' not in email:
        return jsonify({'error': 'A valid email address is required'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    creator  = session.get('admin_email', 'unknown')
    pw_hash  = generate_password_hash(password)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO admins (email, password_hash, created_by) VALUES (%s, %s, %s)',
                    (email, pw_hash, creator)
                )
            conn.commit()
        print(f'[admin] {creator} created new admin: {email}')
        return jsonify({'ok': True, 'email': email})
    except psycopg2.errors.UniqueViolation:
        return jsonify({'error': 'An admin with that email already exists'}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/admin/curate')
@admin_required
def admin_curate():
    return send_from_directory('static', 'admin_curate.html')


@app.route('/api/admin/conferences', methods=['GET'])
@admin_required
def api_admin_conferences():
    """Return all conferences with school counts and curated progress."""
    curated = _load_curated_manifest()
    candidates = _load_candidates_manifest()

    # Build school→conference lookup from EXPLORE_SCHOOLS
    school_conf = {s['school']: s.get('conference', '') for s in EXPLORE_SCHOOLS}

    # Group schools by conference
    conf_schools: dict[str, list[str]] = {}
    for school, conf in school_conf.items():
        if conf:
            conf_schools.setdefault(conf, []).append(school)

    result = []
    for conf in sorted(conf_schools.keys()):
        schools = conf_schools[conf]
        cur_count = sum(
            1 for s in schools
            if curated.get(s, {}).get('hero_images') or curated.get(s, {}).get('selected_in_order')
        )
        cand_count = sum(1 for s in schools if candidates.get(s))
        result.append({
            'name': conf,
            'school_count': len(schools),
            'curated_count': cur_count,
            'has_candidates_count': cand_count,
        })
    return jsonify(result)


@app.route('/api/admin/schools', methods=['GET'])
@admin_required
def api_admin_schools():
    conference_filter = request.args.get('conference', '').strip()

    # Build school→conference lookup
    school_conf = {s['school']: s.get('conference', '') for s in EXPLORE_SCHOOLS}

    try:
        with open('school_names.json', encoding='utf-8') as f:
            all_names = json.load(f)
    except Exception:
        all_names = [s['school'] for s in EXPLORE_SCHOOLS]

    if conference_filter:
        all_names = [n for n in all_names
                     if school_conf.get(n, '') == conference_filter]

    candidates = _load_candidates_manifest()
    curated    = _load_curated_manifest()

    result = []
    for name in all_names:
        cands = candidates.get(name, [])
        cur   = curated.get(name, {})
        is_curated = bool(cur.get('hero_images') or cur.get('selected_in_order'))
        result.append({
            'name':            name,
            'conference':      school_conf.get(name, ''),
            'has_candidates':  bool(cands),
            'candidate_count': len(cands),
            'is_curated':      is_curated,
            'hero_count':      len(cur.get('hero_images', cur.get('selected_in_order', [])[:1])),
            'pool_count':      len(cur.get('pool_images', [])),
            'student_count':   len(cur.get('student_life_images', [])),
        })
    return jsonify(result)


@app.route('/api/admin/candidates/<path:school>', methods=['GET'])
@admin_required
def api_admin_candidates(school):
    candidates = _load_candidates_manifest()
    curated    = _load_curated_manifest()
    cur = curated.get(school, {})
    # Back-compat: old order-based format → new per-type format
    if 'selected_in_order' not in cur and 'selected' in cur:
        cur['selected_in_order'] = cur['selected']

    cands = candidates.get(school, [])
    # Back-compat: add category field to old candidates that lack it
    _pool_tokens    = ('swim', 'pool', 'aquatic', 'natator', 'diving')
    _student_tokens = ('student', 'campus-life', 'campus_life', 'campuslife',
                       'student-life', 'student_life', 'residence', 'dorm', 'union')
    for c in cands:
        if 'category' not in c:
            pt  = c.get('page_type', 'general')
            url = c.get('url', '').lower()
            ctx = c.get('search_context', '').lower()
            if pt == 'swim' or any(t in url for t in _pool_tokens):
                c['category'] = 'pool'
            elif pt == 'student_life' or any(t in url for t in _student_tokens) \
                 or any(t in ctx for t in ('student', 'campus life', 'campus_life')):
                c['category'] = 'student_life'
            else:
                c['category'] = 'campus'

    # Filter globally blocklisted images
    blocklist = _load_blocklist()
    if blocklist:
        cands = [c for c in cands if c.get('url', '') not in blocklist]

    # Cross-school dedup: hide images that appear in 2+ OTHER schools — these
    # are generic stock/commons images that aren't school-specific.
    url_schools: dict[str, int] = {}
    for s, imgs in candidates.items():
        if s == school:
            continue
        for img in imgs:
            u = img.get('url', '')
            if u:
                url_schools[u] = url_schools.get(u, 0) + 1
    cands = [c for c in cands if url_schools.get(c.get('url', ''), 0) < 2]

    return jsonify({'candidates': cands, 'curated': cur})


@app.route('/api/admin/fetch-candidates', methods=['POST'])
@admin_required
def api_admin_fetch_candidates():
    body     = request.get_json(silent=True) or {}
    school   = (body.get('school')   or '').strip()
    category = (body.get('category') or '').strip()  # 'campus'|'pool'|'student_life'|''
    if not school:
        return jsonify({'error': 'missing school'}), 400
    try:
        if category:
            from harvest_candidates import fetch_candidates_for_category
            new_candidates = fetch_candidates_for_category(school, category, city=_school_city(school))
        else:
            from harvest_candidates import fetch_candidates
            new_candidates = fetch_candidates(school)

        # Filter globally blocklisted images
        blocklist = _load_blocklist()
        if blocklist:
            new_candidates = [c for c in new_candidates if c.get('url', '') not in blocklist]

        # Merge with existing, dedupe by URL, rescore, trim to best 24 per category
        from harvest_candidates import _rescore_and_trim_by_category
        manifest = _load_candidates_manifest()
        existing = manifest.get(school, [])
        existing_urls = {c['url'] for c in existing}
        merged = existing + [c for c in new_candidates if c['url'] not in existing_urls]
        trimmed = _rescore_and_trim_by_category(merged, per_cat_limit=24)
        manifest[school] = trimmed
        os.makedirs('static', exist_ok=True)
        with open(_CANDIDATES_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        # Return only the newly-fetched candidates for the UI merge
        return jsonify({'ok': True, 'candidates': new_candidates, 'count': len(new_candidates)})
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/blocklist', methods=['POST'])
@admin_required
def api_admin_blocklist():
    """Add an image URL to the global never-show-again blocklist and scrub it from all manifests."""
    body = request.get_json(silent=True) or {}
    url  = (body.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'missing url'}), 400

    bl = _load_blocklist()
    bl.add(url)
    _save_blocklist(bl)

    # Scrub from all school manifests immediately
    manifest = _load_candidates_manifest()
    changed = False
    for s in manifest:
        before = len(manifest[s])
        manifest[s] = [c for c in manifest[s] if c.get('url') != url]
        if len(manifest[s]) != before:
            changed = True
    if changed:
        with open(_CANDIDATES_PATH, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

    return jsonify({'ok': True, 'blocked_count': len(bl)})


# ── Pre-fetch tracking ────────────────────────────────────────────────────────
_prefetch_running: set[str] = set()
_prefetch_lock = threading.Lock()


@app.route('/api/admin/prefetch-conference', methods=['POST'])
@admin_required
def api_admin_prefetch_conference():
    """Background-fetch candidates for every school in a conference that lacks them."""
    body       = request.get_json(silent=True) or {}
    conference = (body.get('conference') or '').strip()
    if not conference:
        return jsonify({'error': 'missing conference'}), 400

    with _prefetch_lock:
        if conference in _prefetch_running:
            return jsonify({'ok': True, 'status': 'already_running', 'conference': conference})
        _prefetch_running.add(conference)

    TARGET_PER_CAT = 16

    def _do_prefetch():
        try:
            from harvest_candidates import (
                fetch_candidates, fetch_candidates_for_category,
                _load_domains, _save_domains,
                _rescore_and_trim_by_category, _category_counts,
            )
            manifest = _load_candidates_manifest()
            blocklist = _load_blocklist()

            school_conf = {s['school']: s.get('conference', '') for s in EXPLORE_SCHOOLS}
            try:
                with open('school_names.json', encoding='utf-8') as f:
                    all_names = json.load(f)
            except Exception:
                all_names = [s['school'] for s in EXPLORE_SCHOOLS]
            conf_schools = [n for n in all_names if school_conf.get(n, '') == conference]

            domains_cache = _load_domains()
            fetched_count = 0

            for school in conf_schools:
                try:
                    existing = manifest.get(school, [])
                    counts   = _category_counts(existing)
                    cats_needed = [
                        cat for cat in ('campus', 'pool', 'student_life')
                        if counts.get(cat, 0) < TARGET_PER_CAT
                    ]
                    if not cats_needed:
                        print(f'[prefetch:{conference}] {school} — all categories full, skipping')
                        continue

                    print(f'[prefetch:{conference}] {school} — needs {cats_needed} (counts: {counts})')
                    new_cands: list = []

                    if len(cats_needed) == 3 and not existing:
                        # Full fetch is more efficient for a blank school
                        new_cands = fetch_candidates(school, domains_cache)
                    else:
                        for cat in cats_needed:
                            cat_new = fetch_candidates_for_category(school, cat)
                            new_cands.extend(cat_new)

                    if blocklist:
                        new_cands = [c for c in new_cands if c.get('url', '') not in blocklist]

                    existing_urls = {c['url'] for c in existing}
                    merged = existing + [c for c in new_cands if c['url'] not in existing_urls]
                    trimmed = _rescore_and_trim_by_category(merged, per_cat_limit=24)
                    manifest[school] = trimmed
                    _save_domains(domains_cache)

                    final_counts = _category_counts(trimmed)
                    print(f'[prefetch:{conference}] {school} — stored {len(trimmed)} ({final_counts})')

                    with open(_CANDIDATES_PATH, 'w', encoding='utf-8') as fh:
                        json.dump(manifest, fh, indent=2, ensure_ascii=False)
                    fetched_count += 1
                except Exception as exc:
                    print(f'[prefetch:{conference}] {school} error: {exc}')

            print(f'[prefetch:{conference}] done — {fetched_count}/{len(conf_schools)} schools fetched')
        finally:
            with _prefetch_lock:
                _prefetch_running.discard(conference)

    threading.Thread(target=_do_prefetch, daemon=True).start()
    return jsonify({'ok': True, 'status': 'started', 'conference': conference})


@app.route('/api/admin/save', methods=['POST'])
@admin_required
def api_admin_save():
    body   = request.get_json(silent=True) or {}
    school = (body.get('school') or '').strip()
    if not school:
        return jsonify({'error': 'missing school'}), 400

    hero_images         = body.get('hero_images', [])
    pool_images         = body.get('pool_images', [])
    student_life_images = body.get('student_life_images', [])

    # Back-compat: if old order-based format sent, derive typed lists
    if 'selected_in_order' in body and not any([hero_images, pool_images, student_life_images]):
        sio = body['selected_in_order']
        hero_images         = sio[:1]
        pool_images         = sio[1:2]
        student_life_images = sio[2:3]

    curated = _load_curated_manifest()
    curated[school] = {
        'hero_images':          hero_images,
        'pool_images':          pool_images,
        'student_life_images':  student_life_images,
        # Legacy flat fields for back-compat with existing consumers
        'approved_hero_image':        hero_images[0] if hero_images else None,
        'approved_pool_image':        pool_images[0] if pool_images else None,
        'approved_student_life_image': student_life_images[0] if student_life_images else None,
        'approved_extra_images':      hero_images[1:] + pool_images[1:] + student_life_images[1:],
        'saved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    _save_curated_manifest(curated)

    # Push curated picks into school_images.json so the public app sees them immediately
    _push_curated_to_school_images(
        school,
        hero_images[0]         if hero_images         else None,
        pool_images[0]         if pool_images         else None,
        student_life_images[0] if student_life_images else None,
    )

    total = len(hero_images) + len(pool_images) + len(student_life_images)
    return jsonify({'ok': True, 'school': school, 'selected': total})


@app.route('/api/admin/rebuild-school-images', methods=['POST'])
@admin_required
def api_admin_rebuild_school_images():
    """Sync all curated selections → school_images.json. Useful after bulk curation."""
    n = _rebuild_school_images_from_curated()
    return jsonify({'ok': True, 'updated': n})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
