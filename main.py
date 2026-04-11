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
# LANGUAGE PROMPT  (loaded once at startup; used as AI system prompt)
# ---------------------------------------------------------------------------
def _load_language_prompt():
    path = os.path.join(os.path.dirname(__file__), 'prompts', 'lane4_language_prompt.txt')
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ''

LANE4_LANGUAGE_PROMPT = _load_language_prompt()

def _load_deep_dive_prompt():
    path = os.path.join(os.path.dirname(__file__), 'prompts', 'lane4_deep_dive_prompt.txt')
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return ''

LANE4_DEEP_DIVE_PROMPT = _load_deep_dive_prompt()

# DB and auth helpers moved to db.py and auth.py
from db import get_db, _init_db, _bootstrap_initial_admin, _is_user_admin
from auth import login_required, admin_required, user_admin_required


# ---------------------------------------------------------------------------
# AUTH ENDPOINTS
# ---------------------------------------------------------------------------
@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    body = request.get_json(silent=True) or {}
    email    = (body.get('email') or '').strip().lower()
    password = (body.get('password') or '').strip()
    if not email or '@' not in email:
        return jsonify({'error': 'A valid email is required.'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters.'}), 400
    pw_hash = generate_password_hash(password)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id',
                    (email, pw_hash)
                )
                user_id = cur.fetchone()[0]
        session['user_id'] = user_id
        session['email']   = email
        return jsonify({'ok': True, 'email': email})
    except psycopg2.errors.UniqueViolation:
        return jsonify({'error': 'An account with that email already exists.'}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    body = request.get_json(silent=True) or {}
    email    = (body.get('email') or '').strip().lower()
    password = (body.get('password') or '').strip()
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute('SELECT id, password_hash FROM users WHERE email = %s', (email,))
                row = cur.fetchone()
        if not row or not check_password_hash(row['password_hash'], password):
            return jsonify({'error': 'Incorrect email or password.'}), 401
        session['user_id'] = row['id']
        session['email']   = email
        return jsonify({'ok': True, 'email': email})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    if 'user_id' not in session:
        return jsonify({'authenticated': False}), 401
    email = session.get('email', '')
    return jsonify({
        'authenticated': True,
        'email':         email,
        'user_id':       session['user_id'],
        'is_admin':      _is_user_admin(email),
    })

# ---------------------------------------------------------------------------
# DATA SYNC ENDPOINTS
# ---------------------------------------------------------------------------
_ALLOWED_KEYS = {'swimmer', 'my_list', 'crm_data', 'vibe_state', 'other_prefs', 'preferences'}

@app.route('/api/data/load', methods=['GET'])
@login_required
def data_load():
    user_id = session['user_id']
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    'SELECT data_key, data_value FROM sync_data WHERE user_id = %s',
                    (user_id,)
                )
                rows = cur.fetchall()
        result = {r['data_key']: r['data_value'] for r in rows}
        return jsonify({'ok': True, 'data': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/data/save', methods=['POST'])
@login_required
def data_save():
    user_id = session['user_id']
    body    = request.get_json(silent=True) or {}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                for key, value in body.items():
                    if key not in _ALLOWED_KEYS:
                        continue
                    cur.execute(
                        '''INSERT INTO sync_data (user_id, data_key, data_value, updated_at)
                           VALUES (%s, %s, %s::jsonb, NOW())
                           ON CONFLICT (user_id, data_key)
                           DO UPDATE SET data_value = EXCLUDED.data_value, updated_at = NOW()''',
                        (user_id, key, json.dumps(value))
                    )
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ---------------------------------------------------------------------------
# SWIMCLOUD INTEGRATION ROUTES
# ---------------------------------------------------------------------------

def _sc_load_swimmer_record(user_id: int) -> dict:
    """Load the user's saved 'swimmer' JSON from sync_data. Returns {} if missing."""
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT data_value FROM sync_data WHERE user_id = %s AND data_key = 'swimmer'",
                    (user_id,)
                )
                row = cur.fetchone()
        if row and row['data_value']:
            val = row['data_value']
            return val if isinstance(val, dict) else json.loads(val)
    except Exception as e:
        print(f'[swimcloud] Error loading swimmer record: {e}')
    return {}


@app.route('/api/public/swimcloud/search', methods=['GET'])
def sc_search_public():
    """Public SwimCloud search — used during onboarding before account creation."""
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': 'Query required'}), 400
    try:
        from swimcloud_client import search_swimmers
        results = search_swimmers(q)
        return jsonify({'results': results})
    except Exception as e:
        print(f'[swimcloud/public/search] {e}')
        return jsonify({'error': 'SwimCloud search failed', 'detail': str(e)}), 502


@app.route('/api/public/swimcloud/propose', methods=['GET'])
def sc_propose_public():
    """Public SwimCloud propose — used during onboarding before account creation."""
    swimmer_id = (request.args.get('swimmer_id') or '').strip()
    gender     = (request.args.get('gender') or 'men').strip()
    if not swimmer_id:
        return jsonify({'error': 'swimmer_id required'}), 400
    try:
        from swimcloud_client import get_swimmer_scy_bests
        from motivational_ranking import rank_swimcloud_bests
        scy_bests, profile_info, seed_prs = get_swimmer_scy_bests(swimmer_id)
        effective_gender = profile_info.get('gender') or gender
        if not scy_bests:
            return jsonify({'swimmer': profile_info, 'proposed': [], 'seed_prs': []})
        top10 = rank_swimcloud_bests(scy_bests, effective_gender, n=10)
        return jsonify({'swimmer': profile_info, 'proposed': top10, 'seed_prs': seed_prs})
    except Exception as e:
        print(f'[swimcloud/public/propose] {e}')
        return jsonify({'error': 'SwimCloud time fetch failed', 'detail': str(e)}), 502


@app.route('/api/swimcloud/search', methods=['GET'])
@login_required
def sc_search():
    """Search SwimCloud by name. Returns up to 10 candidates."""
    q = (request.args.get('q') or '').strip()
    if not q:
        return jsonify({'error': 'Query required'}), 400
    try:
        from swimcloud_client import search_swimmers
        results = search_swimmers(q)
        return jsonify({'results': results})
    except Exception as e:
        print(f'[swimcloud/search] {e}')
        return jsonify({'error': 'SwimCloud search failed', 'detail': str(e)}), 502


@app.route('/api/swimcloud/propose', methods=['GET'])
@login_required
def sc_propose():
    """
    Fetch a swimmer's SCY times, rank by A-score, return top 10.

    Query params: swimmer_id, gender (men|women)
    """
    swimmer_id = (request.args.get('swimmer_id') or '').strip()
    gender     = (request.args.get('gender') or 'men').strip()
    if not swimmer_id:
        return jsonify({'error': 'swimmer_id required'}), 400
    try:
        from swimcloud_client import get_swimmer_scy_bests
        from motivational_ranking import rank_swimcloud_bests

        scy_bests, profile_info, seed_prs = get_swimmer_scy_bests(swimmer_id)
        # Prefer gender detected from SwimCloud records; fall back to caller-supplied param
        effective_gender = profile_info.get("gender") or gender
        if not scy_bests:
            return jsonify({
                'swimmer': profile_info,
                'proposed': [],
                'seed_prs': [],
                'warning': 'No SCY times found for this swimmer on SwimCloud.',
            })

        top10 = rank_swimcloud_bests(scy_bests, effective_gender, n=10)
        return jsonify({'swimmer': profile_info, 'proposed': top10, 'seed_prs': seed_prs})
    except Exception as e:
        print(f'[swimcloud/propose] {e}')
        return jsonify({'error': 'SwimCloud time fetch failed', 'detail': str(e)}), 502


@app.route('/api/swimcloud/check-prs', methods=['GET'])
@login_required
def sc_check_prs():
    """
    48-hour PR sync check.

    1. Load user's swimmer record to get swimcloud.swimmer_id and last_sync_at.
    2. If no link → {linked: false}
    3. If < 48 h since last sync → {linked: true, has_new_prs: false, reason: 'too_soon'}
    4. Fetch SwimCloud times; compare against swimcloud.accepted_events.
    5. Return {linked: true, has_new_prs: bool, proposed: [...], swimmer: {...}}
       + sync_timestamp (frontend uses this to update last_sync_at)
    """
    user_id   = session['user_id']
    gender    = (request.args.get('gender') or 'men').strip()

    swimmer_rec = _sc_load_swimmer_record(user_id)
    sc = swimmer_rec.get('swimcloud') or {}

    swimmer_id = sc.get('swimmer_id', '')
    if not swimmer_id:
        return jsonify({'linked': False})

    # 48-hour gate
    last_sync = sc.get('last_sync_at', '')
    if last_sync:
        try:
            last_dt = datetime.datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
            diff = datetime.datetime.now(datetime.timezone.utc) - last_dt
            if diff.total_seconds() < 48 * 3600:
                return jsonify({'linked': True, 'has_new_prs': False, 'reason': 'too_soon'})
        except Exception:
            pass  # malformed timestamp — proceed with sync

    try:
        from swimcloud_client import get_swimmer_scy_bests
        from motivational_ranking import rank_swimcloud_bests

        scy_bests, profile_info, _seed_prs = get_swimmer_scy_bests(swimmer_id)
        sync_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if not scy_bests:
            return jsonify({
                'linked': True, 'has_new_prs': False,
                'reason': 'no_scy_times',
                'sync_timestamp': sync_ts,
            })

        top10 = rank_swimcloud_bests(scy_bests, gender, n=10)

        # Compare against previously accepted events
        accepted = sc.get('accepted_events', {})
        new_prs  = []
        for ev in top10:
            event = ev['event']
            new_t = ev['time_sec']
            old   = accepted.get(event, {})
            old_t = old.get('time_sec') if isinstance(old, dict) else None
            if old_t is None or new_t < old_t:
                ev['old_time'] = old.get('time') if isinstance(old, dict) else None
                new_prs.append(ev)

        return jsonify({
            'linked':        True,
            'has_new_prs':   bool(new_prs),
            'proposed':      top10,
            'new_prs':       new_prs,
            'swimmer':       profile_info,
            'sync_timestamp': sync_ts,
        })
    except Exception as e:
        print(f'[swimcloud/check-prs] {e}')
        return jsonify({'linked': True, 'has_new_prs': False, 'reason': 'fetch_error', 'detail': str(e)}), 200

# ---------------------------------------------------------------------------
# USA SWIMMING MOTIVATIONAL STANDARDS — A-SCORE RANKING
# ---------------------------------------------------------------------------
from scoring.motivational import _load_usa_standards, _parse_time_sec_float, _compute_a_score, _a_tier_label
from scoring.primitives import _float, parse_time, estimate_place, exp_points, confidence_weight, place_label, tier_label

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


# Data constants (moved to models/)
from models.school_data import TEAM_NAME_MAP, CONF_DIVISION, SCHOOL_META, OOU_SCHOOL_META, _oou_lookup
from models.swimmer_defaults import JAMES, ALL_EVENTS
from models.school_aliases import _UAA_SHORT, _CAND_STOP, _UNIVERSE_ALIASES, _ACRONYM_ALIASES, _US_STATES

# Runtime data stores live in state.py (mutable module-level singletons)
from state import (
    EXPLORE_SCHOOLS, SCHOOL_LOCATIONS,
    BENCHMARKS, TEAMS, TEAMS_LIST, CONFERENCES, NORMALIZATION_LOG,
)

CSV_BENCH_PATH = os.path.join(os.path.dirname(__file__), 'output', 'all_event_anchors.csv')
CSV_SNAP_PATH  = os.path.join(os.path.dirname(__file__), 'output', 'lane4_snapshot_compatible.csv')

def load_data():
    # all_event_anchors.csv is the canonical benchmark source.
    # Legacy Excel benchmark loading has been retired.
    # If benchmark gaps exist, they must be fixed in the master CSV — not patched at runtime.
    _load_benchmarks()
    _load_teams_from_csv()
    _load_conf_tier_lookup()


def _load_benchmarks():
    """
    Load BENCHMARKS from output/all_event_anchors.csv — the single canonical source.
    Fails loudly if the file is missing, malformed, or contains non-monotonic rows.
    Men's rows only; BENCHMARKS keys carry no gender suffix.
    """
    import csv as _csv

    _REQUIRED_COLS = {'Conference', 'Gender', 'Event', '1st', '8th', '16th'}

    if not os.path.exists(CSV_BENCH_PATH):
        raise FileNotFoundError(
            f"FATAL: Benchmark CSV not found: {CSV_BENCH_PATH}\n"
            "output/all_event_anchors.csv is the canonical benchmark source.\n"
            "Do not attempt to reconstruct from any legacy file."
        )

    with open(CSV_BENCH_PATH, newline='', encoding='utf-8') as f:
        reader = _csv.DictReader(f)
        actual_cols = set(reader.fieldnames or [])
        missing = _REQUIRED_COLS - actual_cols
        if missing:
            raise ValueError(
                f"FATAL: output/all_event_anchors.csv is missing required columns: {missing}\n"
                f"Found columns: {sorted(actual_cols)}"
            )
        rows = list(reader)

    invalid_count = 0
    loaded_count = 0
    for row in rows:
        if (row.get('Gender') or '').strip().lower() != 'men':
            continue
        conf  = (row.get('Conference') or '').strip()
        event = (row.get('Event') or '').strip()
        if not conf or not event:
            continue

        # Use pre-converted *_seconds columns when available; fall back to raw columns
        first     = _float(row.get('1st_seconds') or row.get('1st'))
        eighth    = _float(row.get('8th_seconds') or row.get('8th'))
        sixteenth = _float(row.get('16th_seconds') or row.get('16th'))
        spp       = _float(row.get('Sec_per_place'))

        if first is None and eighth is None:
            continue  # no usable benchmark data

        # Monotonic validation: 1st ≤ 8th ≤ 16th
        if first is not None and eighth is not None and sixteenth is not None:
            if not (first <= eighth <= sixteenth):
                print(
                    f"[WARN] Benchmark monotonicity violation: "
                    f"{conf} | {event} | 1st={first} 8th={eighth} 16th={sixteenth}"
                )
                invalid_count += 1

        BENCHMARKS[f"{conf}|{event}"] = {
            'first':         first,
            'eighth':        eighth,
            'sixteenth':     sixteenth,
            'sec_per_place': spp,
        }
        loaded_count += 1

    if invalid_count:
        print(f"[WARN] {invalid_count} benchmark row(s) failed monotonic validation — see above.")
    print(f"[benchmarks] Loaded {loaded_count} rows from output/all_event_anchors.csv "
          f"({invalid_count} invalid).")


def _load_teams_from_csv():
    """
    Load TEAMS, TEAMS_LIST, and CONFERENCES from output/lane4_snapshot_compatible.csv.
    Men's rows only. Fails loudly if the file is missing.
    """
    import csv as _csv

    if not os.path.exists(CSV_SNAP_PATH):
        raise FileNotFoundError(
            f"FATAL: Snapshot CSV not found: {CSV_SNAP_PATH}"
        )

    loaded_count = 0
    with open(CSV_SNAP_PATH, newline='', encoding='utf-8') as f:
        for row in _csv.DictReader(f):
            if (row.get('gender') or '').lower() != 'men':
                continue
            conf = (row.get('Conference') or '').strip()
            raw  = (row.get('Team') or '').strip()
            if not conf or not raw or conf == 'Unknown':
                continue

            canonical = raw
            normalized = False
            if raw in TEAM_NAME_MAP:
                canonical, reason = TEAM_NAME_MAP[raw]
                normalized = True
                NORMALIZATION_LOG.append({
                    'raw': raw, 'canonical': canonical,
                    'reason': reason, 'conference': conf,
                })

            key = f"{conf}|{canonical}"
            if key in TEAMS:
                continue  # deduplicate

            try:
                finish = int(row.get('Finish') or 0) or None
            except (ValueError, TypeError):
                finish = None

            team_rec = {
                'conference':       conf,
                'school':           canonical,
                'raw_name':         raw,
                'psf':              _float(row.get('PSF')) or 1.0,
                'tier':             row.get('Tier') or '',
                'finish':           finish,
                'men_points':       _float(row.get('MenPoints')),
                'normalized':       normalized,
                'conf_tier_short':  row.get('tier_short') or '',
                'conf_tier':        row.get('final_tier') or '',
                'conf_finish_2026': finish,
                'conf_score_2026':  row.get('MenPoints') or '',
                'conf_power_class': row.get('PowerClass') or '',
            }
            TEAMS[key] = team_rec
            TEAMS_LIST.append(team_rec)

            if conf not in CONFERENCES:
                CONFERENCES[conf] = []
            if canonical not in CONFERENCES[conf]:
                CONFERENCES[conf].append(canonical)
            loaded_count += 1

    # Sort teams within each conference by finish position
    for conf in CONFERENCES:
        CONFERENCES[conf].sort(
            key=lambda s: TEAMS.get(f"{conf}|{s}", {}).get('finish') or 99
        )
    print(f"[teams] Loaded {loaded_count} team records from output/lane4_snapshot_compatible.csv")


def _norm_key(s):
    """Lowercase, strip all non-alphanumeric for fuzzy matching."""
    import re
    return re.sub(r'[^a-z0-9]', '', s.lower().strip()) if s else ''


def _load_conf_tier_lookup():
    """
    Reads output/lane4_snapshot_compatible.csv and enriches each team_rec in
    TEAMS_LIST with 2026 conference championship data:
      conf_tier_short  — '1A' / '1B' / '2' / '3' / '4'  (empty if not in snapshot)
      conf_tier        — 'Tier 1A' … 'Tier 4'
      conf_finish_2026 — integer conference finish rank
      conf_score_2026  — team score at the 2026 championship
      conf_power_class — 'Super Powerhouse' / 'Powerhouse' / ''
    """
    import csv as _csv
    snap_path = os.path.join(os.path.dirname(__file__), 'output', 'lane4_snapshot_compatible.csv')
    if not os.path.exists(snap_path):
        return

    # Build lookup: norm_conf|norm_team → row  (men's rows preferred)
    snap_rows = []
    with open(snap_path, newline='', encoding='utf-8') as f:
        snap_rows = list(_csv.DictReader(f))

    exact = {}   # norm(conf)|norm(team) → row
    by_team = {} # norm(team) → list of rows
    for row in snap_rows:
        if row.get('gender', '').lower() != 'men':
            continue
        ck = _norm_key(row['Conference']) + '|' + _norm_key(row['Team'])
        exact[ck] = row
        by_team.setdefault(_norm_key(row['Team']), []).append(row)

    def _find(conf, school):
        # 1. exact conf|team match
        ck = _norm_key(conf) + '|' + _norm_key(school)
        if ck in exact:
            return exact[ck]
        # 2. UAA abbreviation table
        norm_school = _norm_key(school)
        full_name = _UAA_SHORT.get(norm_school)
        if full_name:
            rows = by_team.get(_norm_key(full_name), [])
            if rows:
                return rows[0]
        # 3. team-name match, same conference
        candidates = by_team.get(norm_school, [])
        same_conf = [r for r in candidates if _norm_key(r['Conference']) == _norm_key(conf)]
        if same_conf:
            return same_conf[0]
        # 4. team-name match, any conference (only if unique)
        if len(candidates) == 1:
            return candidates[0]
        return None

    for team_rec in TEAMS_LIST:
        hit = _find(team_rec['conference'], team_rec['school'])
        # Fallback: try the original raw Excel name (before TEAM_NAME_MAP normalization)
        if not hit and team_rec.get('raw_name') and team_rec['raw_name'] != team_rec['school']:
            hit = _find(team_rec['conference'], team_rec['raw_name'])
        if hit:
            try:
                finish_2026 = int(hit['Finish'])
            except (ValueError, TypeError):
                finish_2026 = None
            team_rec['conf_tier_short']  = hit.get('tier_short', '')
            team_rec['conf_tier']        = hit.get('final_tier', '')
            team_rec['conf_finish_2026'] = finish_2026
            team_rec['conf_score_2026']  = hit.get('MenPoints', '')
            team_rec['conf_power_class'] = hit.get('PowerClass', '')
        else:
            team_rec['conf_tier_short']  = ''
            team_rec['conf_tier']        = ''
            team_rec['conf_finish_2026'] = None
            team_rec['conf_score_2026']  = ''
            team_rec['conf_power_class'] = ''

    _build_explore_schools()


def _build_explore_schools():
    """
    Build EXPLORE_SCHOOLS — one record per unique school across the full
    2026 championship snapshot (~324 schools).  Every school gets the SAME
    object shape.  SCHOOL_META is an enrichment source; its absence never
    changes the code path — only the richness of the values.
    """
    import csv as _csv
    from collections import defaultdict

    EXPLORE_SCHOOLS.clear()

    snap_path = os.path.join(os.path.dirname(__file__), 'output', 'lane4_snapshot_compatible.csv')
    if not os.path.exists(snap_path):
        return

    snap_rows = []
    with open(snap_path, newline='', encoding='utf-8') as f:
        snap_rows = list(_csv.DictReader(f))

    # Build per-school, per-gender rows
    by_school = defaultdict(dict)   # school_name → {'men': row, 'women': row}
    for row in snap_rows:
        gender = row.get('gender', '').lower()
        school = row.get('Team', '').strip()
        if school in TEAM_NAME_MAP:
            school, _ = TEAM_NAME_MAP[school]   # normalize PDF team names to canonical
        if school and gender:
            by_school[school][gender] = row

    # Modeled lookup by norm of canonical name AND raw name.
    # Also add _UAA_SHORT reverse mapping so snapshot full-names (e.g. "Emory University")
    # correctly resolve to their abbreviated TEAMS_LIST entries (e.g. "Emory").
    modeled_by = {}
    for tr in TEAMS_LIST:
        modeled_by[_norm_key(tr['school'])] = tr
        raw = tr.get('raw_name', '')
        if raw:
            modeled_by[_norm_key(raw)] = tr
    # Reverse UAA_SHORT: short_norm → full_name; add full_name → team_rec
    for short_norm, full_name in _UAA_SHORT.items():
        if short_norm in modeled_by:
            modeled_by[_norm_key(full_name)] = modeled_by[short_norm]

    def _si(v):
        try:   return int(v)
        except: return None

    def _sf(v):
        try:   return float(v)
        except: return None

    tier_order = {'1A': 0, '1B': 1, '2': 2, '3': 3, '4': 4, '': 5}

    for school, gmap in by_school.items():
        men_row   = gmap.get('men')
        women_row = gmap.get('women')
        primary   = men_row or women_row
        if not primary:
            continue

        tr = modeled_by.get(_norm_key(school))

        men_ts   = men_row.get('tier_short', '')   if men_row   else ''
        women_ts = women_row.get('tier_short', '') if women_row else ''
        ts       = men_ts or women_ts

        raw_conf = primary.get('Conference', '')
        # If the snapshot CSV recorded 'Unknown' (unrecognised PDF conference),
        # fall back to the team_rec's conference so scoring and display use the
        # correct real conference name.
        display_conf = (tr['conference']
                        if tr and raw_conf in ('Unknown', '', None)
                        else raw_conf)

        entry = {
            'school':            school,
            'conference':        display_conf,
            'conf_tier_short':   ts,
            'conf_tier':         (men_row or women_row).get('final_tier', ''),
            'conf_power_class':  (men_row or women_row).get('PowerClass', ''),
            'men_finish_2026':   _si(men_row['Finish'])          if men_row   else None,
            'women_finish_2026': _si(women_row['Finish'])        if women_row else None,
            'men_score_2026':    _sf(men_row.get('MenPoints',''))   if men_row   else None,
            'women_score_2026':  _sf(women_row.get('MenPoints','')) if women_row else None,
            'men_tier_short':    men_ts,
            'women_tier_short':  women_ts,
            'gender_coverage':   sorted(gmap.keys()),
        }

        # Unified meta merge — SCHOOL_META is an enrichment source, not a gate.
        # Try direct school name first, then canonical team name.
        meta_raw = SCHOOL_META.get(school) or (SCHOOL_META.get(tr['school']) if tr else None) or {}
        entry['meta'] = {
            'accept':    meta_raw.get('accept'),
            'satMedian': meta_raw.get('satMedian'),
            'location':  meta_raw.get('location', ''),
            'hiddenIvy': meta_raw.get('hiddenIvy', False),
            'ivyLeague': meta_raw.get('ivyLeague', False),
            'stem':      meta_raw.get('stem', False),
            'merit':     meta_raw.get('merit', ''),
            'vibe':      meta_raw.get('vibe', ''),
        }
        if meta_raw.get('moonshot'):
            entry['meta']['moonshot'] = True

        if tr:
            entry['row_type']    = 'modeled_school'
            entry['psf']         = tr.get('psf', 1.0)
            entry['tier']        = tr.get('tier', '')
            entry['hasSwimData'] = True
            entry['_team_rec']   = tr   # stored so build_school_universe() re-uses the match
        else:
            entry['row_type']    = 'snapshot_only'
            entry['hasSwimData'] = False
            entry['_team_rec']   = None

        EXPLORE_SCHOOLS.append(entry)

    # Sort: tier (1A first), then men's finish, then school name
    EXPLORE_SCHOOLS.sort(key=lambda s: (
        tier_order.get(s.get('conf_tier_short', ''), 5),
        s.get('men_finish_2026') or s.get('women_finish_2026') or 99,
        s['school'],
    ))


load_data()

def _init_db_background():
    try:
        _init_db()
        _bootstrap_initial_admin()
        print('[startup] DB init complete.')
    except Exception as _e:
        print(f'[startup] DB init warning: {_e} — admin auth may be unavailable')

threading.Thread(target=_init_db_background, daemon=True).start()

# ---------------------------------------------------------------------------
# Scoring engine — all formulas from Swimmer_Calcs (workbook authoritative)
#
# Layer architecture (clean separation for later admissions layer):
#
#   SWIM LAYER  ──  _score_event()  →  EventScore
#                   _score_school_swim()  →  SwimResult
#
#   ADMISSION LAYER  ──  admission_chance(school, sat, gpa, adj_tier, psf)  →  AdmissionResult
#                        (takes SwimResult outputs + academic inputs; returns {label, color, …})
#
#   FULL PIPELINE  ──  build_school_universe(times, sat, gpa)  →  [SchoolResult …]
#                      Starts from EXPLORE_SCHOOLS (324-school snapshot universe).
#                      Merges swim scoring + SCHOOL_META + admission into one uniform shape.
#
# Output field names follow OUTPUT_SCHEMA.md exactly:
#   EventScore:  { event, sec, place, pts }   (+ expPts, confidence, placeLabel for tracing)
#   SchoolResult: { school, conference, tier, psf, rawPts, adjPts, adjTier,
#                   top3, allEvents, admission, meta, normalized, rawName }
# ---------------------------------------------------------------------------

# ── Swim layer ──────────────────────────────────────────────────────────────

from scoring.swim_scoring import _score_event, _score_school_swim
from scoring.admission import _oou_admission, admission_chance
from scoring.universe import build_school_universe, score_one_school
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


def _detect_query_intent(query: str) -> dict:
    """Detect what filtering rules apply to a query.

    Returns:
      is_personal      — query is about THIS swimmer's realistic options.
                         If False the query is objective — return AI's list as-is.
      is_swim          — query is about swimming / being recruited / contributing.
      is_explicit_reach — user explicitly asked for reaches / dream / long-shot
                         schools. When True the swim hard-filter is bypassed even
                         if is_swim and is_personal are both True.
      adm_threshold    — minimum admissions label score required to survive filter.
                         None = no admissions filter (general "for me" queries)
                         80   = Strong Chance or better  ("definitely get in")
                         60   = Realistic Shot or better ("can get in")
    """
    q = query.lower()

    # ── Swim intent ─────────────────────────────────────────────────────────
    # 'recruit' root catches recruited / recruiting / recruitable / recruitment.
    # 'team' catches "teams" (substring). 'fastest'/'fast enough' cover speed
    # queries idiomatic to swim recruiting. 'score' covers swim points queries.
    # 'in range' covers "schools in range for me" (swim-level range).
    is_swim = any(s in q for s in [
        'swim', 'pool', 'stroke', 'relay', 'contribute', 'compete',
        'make the team', 'recruit', 'roster', 'athletic fit',
        'lineup', 'cuts', 'finals', 'time trial',
        'team', 'score', 'in range', 'fast enough', 'fastest',
        # event names
        '50 free', '100 free', '200 free', '500 free', '1000 free', '1650',
        '100 back', '200 back', '100 breast', '200 breast',
        '100 fly', '200 fly', '200 im', '400 im',
        'backstroke', 'breaststroke', 'butterfly', 'individual medley',
        'distance swimmer', 'sprinter',
        # composite swim-fit phrases
        'swim fit', 'event profile', 'distance free',
    ])

    # ── Personal-fit intent ──────────────────────────────────────────────────
    # Covers first-person constructions in both normal and inverted word order
    # (e.g. "I could" AND "could I"), plus fit/chance signal words.
    is_personal = any(s in q for s in [
        # first-person pronouns / possessives
        'for me', 'for my', 'help me', 'find me', 'my list', 'my fit',
        'my shot', 'my chance', 'my best', 'my options', 'my realistic',
        # "I <verb>" constructions
        'i should', 'i can', 'i could', 'i want', 'i need',
        'i have', 'i would', "i'm", 'i am',
        # inverted-order constructions ("where am I", "where could I", "where can I")
        'am i', 'can i', 'could i', 'would i', 'should i',
        # contraction
        "i'd",
        # directive phrases
        'where should i', 'where i', 'recommend', 'find me',
        # fit / chance signals
        'shot at', 'have a shot', 'have a chance', 'a chance at',
        'chance of being', 'realistically', 'realistic', 'able to',
        'good fit', 'best fit', 'right fit', 'fit best', 'in range',
        # possessive / proximity signals
        'my ',          # "my 1650", "my 500 free", "my times", "my chances"
        'like me',      # "distance swimmers like me"
        'recruitable',  # "recruitable schools" implies "schools that recruit me"
        'pipe dream',   # "not pipe dreams" = "realistic for me"
        'swim fit',     # "swim fits" / "swim fit" — inherently personal (fit for me)
        'recruit me',   # "would recruit me", "that would recruit me", "recruit me"
        'want me',      # "schools that would want me"
        'take me',      # "schools that would take me as a swimmer"
    ])

    # ── Explicit-reach override ──────────────────────────────────────────────
    # User intentionally wants schools beyond their realistic level.
    # When True, the swim hard-filter is bypassed so dream/long-shot schools
    # can appear even if the swimmer's recruiting label there is non-competitive.
    is_explicit_reach = any(s in q for s in [
        'dream school', 'reach school', 'long shot', 'longshot',
        'unrealistic', "probably can't", "can't swim", 'stretch school',
        'aspiration', 'long-shot',
        'not fast enough', 'too fast for', 'too slow', "can't make",
        "wouldn't be competitive", 'out of my league',
        # negative-fit / eliminate intent — user is asking about schools to avoid
        'below roster level', 'stop considering', 'no shot', 'have no shot',
        'wasting my time', 'not competitive', 'not in range', 'too far out',
    ])

    # Admissions threshold — only meaningful when is_personal is True.
    # Strong Chance or better (80): explicit certainty language.
    # Realistic Shot or better (60): any "can get in" / admissibility language.
    high_bar = any(s in q for s in [
        'definitely get', 'certain to get', 'guaranteed', 'can definitely',
        'easy to get', 'safety', 'sure thing', 'will get in',
    ])
    std_bar = any(s in q for s in [
        'get in', 'get into', 'admissible', 'realistic',
        'where i can get', 'i could get into', 'can get into', 'i can get in',
    ])

    if high_bar:
        adm_threshold = 80   # Strong Chance or better
    elif std_bar:
        adm_threshold = 60   # Realistic Shot or better
    else:
        adm_threshold = None  # General "for me" — no admissions filter

    # ── Prestige / ceiling sort ───────────────────────────────────────────────
    # When True, viable survivors are re-ranked by academic selectivity
    # (lowest admissions-label score = hardest to get into = first), instead of
    # preserving Claude's ordering or defaulting to adjPts-descending.
    # Only applies when is_personal=True so the swim gate fires first.
    # "hardest" / "strongest academic" / "elite" / "most selective" language.
    is_prestige_sort = is_personal and any(s in q for s in [
        'hardest', 'toughest',
        'most selective', 'most prestigious', 'most impressive',
        'highest ranked', 'highest-ranked', 'best ranked', 'top ranked',
        'strongest academic', 'strongest academics', 'most academically',
        'smartest school', 'smartest schools', 'smartest college',
        'elite school', 'elite schools', 'elite college', 'elite program',
        'most elite', 'most academic', 'academic school', 'academic college',
        'best academic', 'top academic', 'highly selective', 'high academic',
        'most well-known', 'most well known', 'best known',
        'ranked school', 'ranked college', 'ranked university',
    ])

    return {
        'is_personal':       is_personal,
        'is_swim':           is_swim,
        'is_explicit_reach': is_explicit_reach,
        'adm_threshold':     adm_threshold,
        'is_prestige_sort':  is_prestige_sort,
    }


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


def _build_student_context(name, gpa, sat, act, times, vibe, other_prefs) -> str:
    """Build a concise student context string for GUIDED/CONSTRAINED prompts."""
    parts = [f"Student: {name or 'the swimmer'}"]
    if gpa:
        parts.append(f"GPA {gpa:.1f}")
    if sat:
        parts.append(f"SAT {sat}")
    elif act:
        parts.append(f"ACT {act}")
    if times:
        parts.append(f"events: {', '.join(list(times.keys())[:3])}")
    if vibe:
        skip = {'Not sure yet', 'Genuinely want to be well-rounded', '', None}
        prefs = [v for v in vibe.values() if v not in skip]
        if prefs:
            parts.append(f"preferences: {'; '.join(prefs[:4])}")
    if other_prefs and str(other_prefs).strip():
        parts.append(f"notes: {str(other_prefs).strip()[:200]}")
    return ', '.join(parts)


def _build_candidate_prompt(query: str, student_ctx: str) -> tuple:
    """Build system + user prompts for candidate school generation.

    Single path — always generates a strong pool of 12–15 relevant schools.
    Focus is purely on academic/program relevance to the query.
    Admissions and swim filtering are handled downstream, not here.
    Student context is always included so the LLM can interpret the query
    correctly (e.g. 'best bio schools for me'), but must NOT be used to
    pre-filter for fit.
    """
    system = (
        "You are an expert U.S. college counselor generating a candidate list of colleges.\n\n"
        "Rules — follow EXACTLY:\n"
        "- Focus on academic and program relevance to the query\n"
        "- Do NOT filter for admissions likelihood\n"
        "- Do NOT filter for swim/athletic fit\n"
        "- Do NOT rank for the student — just return a strong, relevant pool\n"
        "- Include a quality range — not just elite schools\n"
        "- Honor explicit constraints exactly (NESCAC, Midwest, D3, pre-med, STEM, etc.)\n"
        "- Return ONLY valid JSON — no markdown, no extra text\n"
        "- 'schools' must contain 12 to 15 full school name strings\n"
        "- 'answer' is 1-2 plain-English sentences describing the search\n"
        'Format: {"answer": "...", "schools": ["Full School Name", ...]}'
    )
    user_lines = [f'Search: "{query}"']
    if student_ctx:
        user_lines.append(
            f"\nStudent context (use only to understand the query — "
            f"do NOT pre-filter for admissibility or swim fit):\n{student_ctx}"
        )
    user_lines.append("\nReturn JSON only.")
    return system, '\n'.join(user_lines)


def _parse_candidate_names(text: str) -> tuple:
    """Parse LLM JSON → (answer str, list of school name strings)."""
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text).strip()
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        raise ValueError('No JSON in candidate response')
    parsed = json.loads(m.group())
    answer = str(parsed.get('answer', '')).strip()
    names  = [str(s).strip() for s in parsed.get('schools', []) if str(s).strip()]
    if not names:
        raise ValueError('Empty candidate list returned by AI')
    return answer, names




def _cname_norm(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', s.lower())).strip()


def _cname_toks(s: str) -> frozenset:
    return frozenset(t for t in _cname_norm(s).split()
                     if t not in _CAND_STOP and len(t) > 1)




# ── School-name query normalizer ──────────────────────────────────────────────
def _qnorm(s: str) -> str:
    """Normalize a user-typed query for liberal school-name matching.

    Handles: lowercase, punctuation strip, & → and, st → saint,
    trailing/leading whitespace, collapsed internal spaces.
    """
    s = s.lower().strip()
    s = s.replace('&', ' and ')
    s = re.sub(r"['\u2019`\-\.]", '', s)   # apostrophes, hyphens, periods
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r'\bst\b', 'saint', s)      # st → saint (before whitespace collapse)
    s = re.sub(r'\buniv\b', 'university', s)
    s = re.sub(r'\bcoll\b', 'college', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s





def _school_entity_surface(record: dict) -> str:
    """Build a normalized search surface for a school record.

    Combines canonical name + city + state abbreviation + full state name so
    that city-based queries ("Nashville", "Pittsburgh", "Santa Barbara") can
    match schools even when the city doesn't appear in the school's name.
    Conference is intentionally excluded — too broad, causes false positives.
    """
    parts = [_qnorm(record['school'])]
    meta  = record.get('meta') or {}
    loc   = meta.get('location', '')      # e.g. "Santa Barbara, CA"
    if loc:
        clean = re.sub(r'[,.]', ' ', loc)
        parts.append(_qnorm(clean))
        bits = [b.strip() for b in loc.split(',')]
        if len(bits) >= 2:
            state_ab   = bits[-1].strip().upper()
            full_state = _US_STATES.get(state_ab, '')
            if full_state:
                parts.append(_qnorm(full_state))
    return ' '.join(parts)


def _resolve_school_names(query: str, all_results: list) -> list:
    """School-entity resolver: find plausible universe matches for a user query.

    Six-pass pipeline (highest confidence first):
      1. Acronym alias    — pure initials / nickname contractions (CMU, WashU …)
      2. Exact normalized — _qnorm(query) == _qnorm(school_name)
      3. Name substring   — query contained in school name, or vice-versa
      4. Prefix-token     — every query token is a prefix of some school-name
                            token; catches truncated canonicals:
                              "Penn State"   → "Pennsylvania State University"
                              "Georgia Tech" → "Georgia Institute of Technolog"
      5. difflib fuzzy    — typos / near-misspellings (cutoff 0.60)
      6. Surface fallback — city / state match, ONLY when passes 1–5 return
                            nothing (e.g. "Nashville" → Vanderbilt University)

    Favors recall: returns all matches with confidence ≥ 0.55 so that
    ambiguous queries like "Washington" surface multiple schools for the user
    to pick from, rather than silently returning the first hit.
    """
    import difflib

    q_raw = query.strip()
    q_n   = _qnorm(q_raw)

    by_name    = {r['school']: r for r in all_results}
    canon_list = list(by_name.keys())
    scores: dict[str, float] = {}

    def _add(school: str, score: float) -> None:
        if school in by_name:
            scores[school] = max(scores.get(school, 0.0), score)

    # ── Pass 1: Acronym / nickname alias ──────────────────────────────────────
    alias_hit = _ACRONYM_ALIASES.get(q_n)
    if alias_hit:
        targets = alias_hit if isinstance(alias_hit, list) else [alias_hit]
        for t in targets:
            _add(t, 1.0)

    # ── Pass 2: Exact normalized name ─────────────────────────────────────────
    norm_to_canon = {_qnorm(n): n for n in canon_list}
    exact = norm_to_canon.get(q_n)
    if exact:
        _add(exact, 1.0)

    # ── Pass 3: Name substring (both directions, min 4 chars) ─────────────────
    if len(q_n) >= 4:
        for name in canon_list:
            s_n = _qnorm(name)
            if q_n in s_n:
                _add(name, 0.85)
            elif len(s_n) >= 4 and s_n in q_n:
                _add(name, 0.80)

    # ── Pass 4: Prefix-token match ────────────────────────────────────────────
    # Every query token must be a prefix of at least one school-name token.
    # Handles truncated canonical names and common abbreviations:
    #   "Penn State"   → tokens ["penn","state"] prefix-match ["pennsylvania","state",…]
    #   "Georgia Tech" → tokens ["georgia","tech"] prefix-match ["georgia","…technolog"]
    #   "Johns Hopkin" → ["johns","hopkin"] prefix-match ["johns","hopkins",…]
    _PREFIX_STOP = frozenset({'of', 'the', 'and', 'at', 'for', 'in', 'a'})
    q_toks = [t for t in q_n.split() if t not in _PREFIX_STOP and len(t) > 1]
    if len(q_toks) >= 2:
        for name in canon_list:
            s_toks = [t for t in _qnorm(name).split()
                      if t not in _PREFIX_STOP and len(t) > 1]
            if s_toks and all(any(st.startswith(qt) for st in s_toks)
                              for qt in q_toks):
                _add(name, 0.78)

    # ── Pass 5: difflib fuzzy (typos / close misspellings, cutoff 0.80) ───────
    norm_list = list(norm_to_canon.keys())
    for fuzzy_q, src, lookup in [
        (q_raw, canon_list, lambda h: h if h in by_name else None),
        (q_n,   norm_list,  lambda h: norm_to_canon.get(h)),
    ]:
        for hit in difflib.get_close_matches(fuzzy_q, src, n=6, cutoff=0.80):
            canon = lookup(hit)
            if canon:
                ratio = difflib.SequenceMatcher(None, q_n, _qnorm(canon)).ratio()
                _add(canon, ratio * 0.80)

    # ── Pass 6: City / state surface fallback ─────────────────────────────────
    # ONLY activated when passes 1–5 found nothing.
    # Allows city-based queries: "Nashville" → Vanderbilt, "Pittsburgh" → Pitt.
    # Does NOT fire when passes 1–5 already returned name-based matches, so
    # "Washington" stays clean (4 name matches) without adding every DC school.
    if not scores and len(q_n) >= 4:
        for record in all_results:
            if q_n in _school_entity_surface(record):
                _add(record['school'], 0.65)

    if not scores:
        return []

    # Confidence gate (0.55): drops weak difflib coincidences while keeping
    # alias hits (1.0), substring hits (0.80–0.85), prefix-token (0.78),
    # strong difflib (≥ 0.60 ratio × 0.80 = 0.48 … raise cutoff to 0.60 so
    # stored score ≥ 0.60 × 0.80 = 0.48 … hmm).
    # With cutoff=0.60, difflib stored score = ratio*0.80 ≥ 0.48 for a hit.
    # Alias/exact/substring/prefix are all ≥ 0.78, well above 0.55.
    MIN_CONF = 0.55
    return [by_name[name] for name, sc in
            sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
            if sc >= MIN_CONF]


def _map_to_universe(candidate_names: list, all_results: list) -> list:
    """
    Fuzzy-map LLM-generated school names → Lane4 school records.

    Matching priority:
      0. Alias map  — handles acronyms/short-forms (MIT, Caltech, NYU, etc.)
      1. Exact normalized match
      2. Substring match (normalized name contained in/containing each other)
      3. Key-token Jaccard similarity ≥ 0.50
    Ignores candidates that don't match confidently; never fabricates schools.
    """
    by_norm       = {_cname_norm(r['school']): r for r in all_results}
    by_canon_norm = {_cname_norm(r['school']): r for r in all_results}
    mapped, seen  = [], set()

    for cand in candidate_names:
        cand = cand.strip()
        if not cand:
            continue
        record = None

        # 0. Alias map — full official name → known short-form in universe
        alias_target = _UNIVERSE_ALIASES.get(_cname_norm(cand))
        if alias_target:
            record = by_canon_norm.get(_cname_norm(alias_target))

        # 1. Exact normalized match
        if not record:
            record = by_norm.get(_cname_norm(cand))

        # 2. Substring match
        if not record:
            c_n = _cname_norm(cand)
            for s_n, r in by_norm.items():
                if c_n and (c_n in s_n or s_n in c_n):
                    record = r
                    break

        # 3. Key-token Jaccard ≥ 0.50
        if not record:
            c_t = _cname_toks(cand)
            best, best_r = 0.0, None
            for r in all_results:
                r_t = _cname_toks(r['school'])
                if not c_t or not r_t:
                    continue
                jac = len(c_t & r_t) / len(c_t | r_t)
                if jac > best:
                    best, best_r = jac, r
            if best >= 0.50:
                record = best_r

        if record and record['school'] not in seen:
            seen.add(record['school'])
            mapped.append(record)

    return mapped


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


def _parse_search_response(text, sorted_35):
    """
    Parse Claude's JSON search response.
    Returns list of enriched SchoolResult dicts (with aiWhy), or raises ValueError.
    """
    # Strip markdown fences
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()

    match = re.search(r'\{[\s\S]*\}', text)
    if not match:
        raise ValueError('No JSON object found in response')

    parsed = json.loads(match.group())
    answer  = parsed.get('answer', '')
    picks   = parsed.get('schools', [])

    schools = []
    for pick in picks:
        idx = pick.get('number')
        if idx is None:
            continue
        idx = int(idx) - 1
        if idx < 0 or idx >= len(sorted_35):
            continue
        r = dict(sorted_35[idx])
        r['aiWhy'] = pick.get('why', '')
        schools.append(r)

    if not schools:
        raise ValueError('No valid school picks in response')

    return answer, schools

def _build_top3_text(top3):
    """'1650 Free: Contender; 500 Free: 🏅 Podium' style string."""
    return '; '.join(f"{e['event']}: {place_label(e['place'])}" for e in top3)

def _build_vibe_lines(vibe, other_prefs=''):
    """Format answered vibe questions for deep dive prompt."""
    labels = {
        'swimGoal': 'Swim environment goal',
        'campus':   'Ideal campus feel',
        'friday':   'Friday night preference',
        'academic': 'Academic priority',
        'compete':  'Competition mindset',
        'location': 'Location preference',
        'career':   'Career interest',
    }
    lines = []
    if vibe:
        for k, v in vibe.items():
            if v:
                lines.append(f"  - {labels.get(k, k)}: {v}")
    if other_prefs and other_prefs.strip():
        lines.append(f"  - Additional preferences: {other_prefs.strip()}")
    return '\n'.join(lines)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# --- STATIC / PAGE SERVE ROUTES ---

@app.route('/')
def index():
    resp = send_from_directory('static', 'index.html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/login', methods=['GET'])
def login_page():
    """Standalone login page for returning swimmers."""
    return send_from_directory('static', 'login.html')


@app.route('/debug-ui')
def debug_ui():
    return send_from_directory('static', 'debug_ui.html')

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


# ---------------------------------------------------------------------------
# ADMIN — Image Curation UI
# ---------------------------------------------------------------------------

_CANDIDATES_PATH    = os.path.join('static', 'data', 'candidates_manifest.json')
_CURATED_PATH       = os.path.join('static', 'data', 'curated_manifest.json')
_BLOCKLIST_PATH     = os.path.join('static', 'data', 'image_blocklist.json')
_SCHOOL_IMAGES_PATH = os.path.join('static', 'data', 'school_images.json')


def _load_blocklist() -> set:
    try:
        with open(_BLOCKLIST_PATH, encoding='utf-8') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_blocklist(bl: set):
    os.makedirs('static', exist_ok=True)
    with open(_BLOCKLIST_PATH, 'w', encoding='utf-8') as f:
        json.dump(sorted(bl), f, indent=2)


def _load_candidates_manifest():
    try:
        with open(_CANDIDATES_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _load_curated_manifest():
    try:
        with open(_CURATED_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_curated_manifest(data: dict):
    os.makedirs('static', exist_ok=True)
    with open(_CURATED_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── school_images.json helpers ────────────────────────────────────────────────
# school_images.json is the file the public frontend reads at startup.
# Format: { "School Name": { hero, student_life, swim, is_fallback, source_pages } }
# We write curated selections here so they appear immediately in explore cards
# and in the hero / photo row of every deep dive.

def _load_school_images() -> dict:
    try:
        with open(_SCHOOL_IMAGES_PATH, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_school_images(data: dict):
    os.makedirs('static', exist_ok=True)
    with open(_SCHOOL_IMAGES_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _push_curated_to_school_images(school: str, hero, pool, student_life):
    """Merge one school's curated picks into school_images.json."""
    imgs = _load_school_images()
    entry = imgs.get(school, {})
    if hero         is not None: entry['hero']         = hero
    if pool         is not None: entry['swim']         = pool
    if student_life is not None: entry['student_life'] = student_life
    is_fb = entry.get('is_fallback', {})
    src   = entry.get('source_pages', {})
    for key, val in [('hero', hero), ('swim', pool), ('student_life', student_life)]:
        if val is not None:
            is_fb[key] = False
            src[key]   = 'curated'
    entry['is_fallback']  = is_fb
    entry['source_pages'] = src
    imgs[school] = entry
    _save_school_images(imgs)

def _rebuild_school_images_from_curated():
    """Sync all curated selections → school_images.json. Called at startup and on demand."""
    curated = _load_curated_manifest()
    if not curated:
        return 0
    imgs = _load_school_images()
    updated = 0
    for school, cur in curated.items():
        hero  = cur.get('approved_hero_image') or (cur.get('hero_images') or [None])[0]
        pool  = cur.get('approved_pool_image') or (cur.get('pool_images') or [None])[0]
        sl    = cur.get('approved_student_life_image') or (cur.get('student_life_images') or [None])[0]
        if not any([hero, pool, sl]):
            continue
        entry = imgs.get(school, {})
        if hero: entry['hero']         = hero
        if pool: entry['swim']         = pool
        if sl:   entry['student_life'] = sl
        is_fb = entry.get('is_fallback', {})
        src   = entry.get('source_pages', {})
        for key, val in [('hero', hero), ('swim', pool), ('student_life', sl)]:
            if val:
                is_fb[key] = False
                src[key]   = 'curated'
        entry['is_fallback']  = is_fb
        entry['source_pages'] = src
        imgs[school] = entry
        updated += 1
    _save_school_images(imgs)
    return updated


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


# --- UTILITY / DEBUG ROUTES ---

@app.route('/api/health', methods=['GET'])
def health():
    key_ok = bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())
    swim_data_schools = sum(1 for s in EXPLORE_SCHOOLS if s.get('hasSwimData'))
    return jsonify({
        'status':              'ok',
        # ── Primary universe (source of truth) ───
        'universeSource':      'output/lane4_snapshot_compatible.csv',
        'totalSchools':        len(EXPLORE_SCHOOLS),
        'schoolsWithSwimData': swim_data_schools,
        'conferenceOnlySchools': len(EXPLORE_SCHOOLS) - swim_data_schools,
        # ── Enrichment sources ────────────────────
        'enrichmentSource':    'output/all_event_anchors.csv',
        'benchmarks':          len(BENCHMARKS),
        'enrichmentRecords':   len(TEAMS_LIST),
        'admissionRecords':    len(SCHOOL_META),
        'normalized':          len(NORMALIZATION_LOG),
        'anthropicKey':        key_ok,
    })

@app.route('/snapshot', methods=['GET'])
def download_snapshot():
    """Serve the latest Lane4 team-tier snapshot CSV for download."""
    return send_from_directory('output', 'lane4_snapshot.csv', as_attachment=True)


# Sync curated picks → school_images.json once all helpers are defined
_rebuild_school_images_from_curated()

@app.route('/api/resetadmin-lane4-2026')
def api_reset_admin():
    from werkzeug.security import generate_password_hash
    new_hash = generate_password_hash('4Freediver')
    results = {}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE admins SET password_hash = %s WHERE email = 'johngannon@pacesupply.com'", (new_hash,))
                results['admin_rows'] = cur.rowcount
                cur.execute("""INSERT INTO users (email, password_hash)
                               VALUES ('johngannon@pacesupply.com', %s)
                               ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash""",
                            (new_hash,))
                results['user_rows'] = cur.rowcount
        results['ok'] = True
    except Exception as e:
        results['error'] = str(e)
    return jsonify(results)


@app.route('/api/dbcheck')
def api_dbcheck():
    import os
    from urllib.parse import urlparse
    url = os.environ.get('DATABASE_URL', '')
    p = urlparse(url)
    info = {'host': p.hostname, 'db': p.path.lstrip('/')}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                info['user_count'] = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM admins")
                info['admin_count'] = cur.fetchone()[0]
                cur.execute("SELECT LEFT(password_hash,20) FROM users WHERE email='johngannon@pacesupply.com'")
                row = cur.fetchone()
                info['user_hash_prefix'] = row[0] if row else None
    except Exception as e:
        info['error'] = str(e)
    return jsonify(info)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
