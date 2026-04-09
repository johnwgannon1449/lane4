import os, json, re, time, threading
import urllib.request, urllib.parse
import datetime
from flask import Flask, request, jsonify, send_from_directory, session, redirect
from dotenv import load_dotenv
from functools import wraps
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

# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
def get_db():
    if not _HAS_PSYCOPG2:
        raise RuntimeError('psycopg2 not available — admin auth disabled')
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set — admin auth disabled')
    return psycopg2.connect(db_url, connect_timeout=10)

def _init_db():
    """Create tables if they don't exist (safe to run on every startup)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sync_data (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    data_key   TEXT NOT NULL,
                    data_value JSONB,
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (user_id, data_key)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id            SERIAL PRIMARY KEY,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT,
                    created_by    TEXT,
                    created_at    TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Safe migrations for existing deployments
            cur.execute("""
                ALTER TABLE admins
                    ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE
            """)
            cur.execute("""
                ALTER TABLE admins
                    ALTER COLUMN password_hash DROP NOT NULL
            """)
        conn.commit()


def _bootstrap_initial_admin():
    """Ensure johngannon@pacesupply.com is always an active admin (idempotent).
    Also sets a password_hash if ADMIN_PASSWORD env var is provided (for the
    legacy /admin/curate login system).  Safe to call on every startup.
    """
    bootstrap_email = 'johngannon@pacesupply.com'
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Ensure the seed admin always exists and is active
                cur.execute("""
                    INSERT INTO admins (email, active, created_by)
                    VALUES (%s, TRUE, 'bootstrap')
                    ON CONFLICT (email) DO UPDATE SET active = TRUE
                """, (bootstrap_email,))
                # Optionally attach a password for the legacy curator login
                initial_password = os.environ.get('ADMIN_PASSWORD', '')
                if initial_password:
                    pw_hash = generate_password_hash(initial_password)
                    cur.execute(
                        'UPDATE admins SET password_hash = %s WHERE email = %s AND password_hash IS NULL',
                        (pw_hash, bootstrap_email)
                    )
                print(f'[admin bootstrap] Seed admin verified: {bootstrap_email}')
            conn.commit()
    except Exception as e:
        print(f'[admin bootstrap] Error: {e}')


def _is_user_admin(email: str) -> bool:
    """Return True if the email is an active admin in the admins table."""
    if not email:
        return False
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1 FROM admins WHERE email = %s AND active = TRUE', (email,))
                return cur.fetchone() is not None
    except Exception:
        return False

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Gate that requires an active admin session (email-based DB auth)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_email'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Not authenticated'}), 401
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated

def user_admin_required(f):
    """Gate: user must be logged in AND appear in the admins table (active=TRUE)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Not authenticated'}), 401
            return redirect('/')
        if not _is_user_admin(session.get('email', '')):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Forbidden'}), 403
            return 'Forbidden', 403
        return f(*args, **kwargs)
    return decorated

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
_USA_STANDARDS_CACHE: dict | None = None

def _load_usa_standards() -> dict:
    global _USA_STANDARDS_CACHE
    if _USA_STANDARDS_CACHE is None:
        path = os.path.join(os.path.dirname(__file__), 'static', 'usa_motivational_times_17_18_scy.json')
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


# ---------------------------------------------------------------------------
# TEAM NAME NORMALIZATIONS
# Workbook cell widths truncate long names and add trailing qualifiers.
# These are the ONLY 9 places where workbook text differs from canonical names.
# Flagged explicitly per guardrail — no silent guessing.
# ---------------------------------------------------------------------------
TEAM_NAME_MAP = {
    "McDaniel College Swim Team":     ("McDaniel College",                 "trailing qualifier"),
    "Rochester Institute of Technol": ("Rochester Institute of Technology", "truncated at 30 chars"),
    "Rensselaer Polytechnic Institu": ("Rensselaer Polytechnic Institute",  "truncated at 30 chars"),
    "Union College (New York)":        ("Union College",                     "parenthetical qualifier"),
    "Massachusetts Institute of Tec": ("MIT",                               "truncated — schema uses 'MIT'"),
    "Worcester Polytechnic Institut": ("Worcester Polytechnic Institute",   "truncated at 30 chars"),
    "Wheaton College (Ma)":            ("Wheaton College (MA)",              "wrong casing"),
    "Whitworth University Swim Team": ("Whitworth University",              "trailing qualifier"),
    "California Institute of Techno": ("Caltech",                           "truncated — schema uses 'Caltech'"),
    "Saint Johns University":          ("Saint John's University",           "missing apostrophe"),
    "Harvard Men's Swimming":          ("Harvard University",                 "gendered PDF team name artifact"),
    # UAA schools — snapshot uses full institutional names; SCHOOL_META uses short forms
    "Brandeis University":             ("Brandeis",                          "SCHOOL_META uses short name"),
    "Carnegie Mellon University":      ("Carnegie Mellon",                   "SCHOOL_META drops University"),
    "Case Western Reserve Universit":  ("Case Western",                      "truncated + SCHOOL_META short name"),
    "Case Western Reserve University": ("Case Western",                      "SCHOOL_META uses short name"),
    "Emory University":                ("Emory",                             "SCHOOL_META drops University"),
    "New York University":             ("NYU",                               "SCHOOL_META uses abbreviation"),
    "University of Chicago":           ("Chicago",                           "SCHOOL_META uses city name"),
    "University of Rochester":         ("Rochester",                         "SCHOOL_META uses city name"),
    "Washington University St Louis":  ("Washington (Mo)",                   "SCHOOL_META uses location suffix"),
    "Washington University in St. Lou":("Washington (Mo)",                   "SCHOOL_META uses location suffix"),
}

# ---------------------------------------------------------------------------
# JAMES — hardcoded swimmer profile (source of truth for all scoring runs)
# ---------------------------------------------------------------------------
JAMES = {
    "name":             "James",
    "gpa":              4.0,
    "sat":              1460,
    "satProjected":     1500,
    "actScore":         0,
    "apCount":          0,
    "mathSat":          720,
    "mathSatProjected": 760,
    "times": {
        "1650 Free":              "16:06",
        "1000 Free":              "9:30",
        "500 Free":               "4:37",
        "200 Free":               "1:43",
        "400 IM":                 "4:09",
        "200 IM":                 "1:56",
        "100 Breast":             "59.5",
        "50 Breast (Relay Split)": "25.68",
    },
    "vibe": {
        "campus":   "Small and tight-knit — everyone knows everyone",
        "friday":   "Library with 2–3 close friends",
        "academic": "Genuinely want to be well-rounded",
        "compete":  "Love pushing myself inside a team environment",
        "location": None,
        "career":   None,
    },
}

# ---------------------------------------------------------------------------
# Conference → NCAA division label (used by build_school_universe)
CONF_DIVISION: dict[str, str] = {
    # ── D1 ────────────────────────────────────────────────────────────────────
    "ACC":            "D1", "ASUN":          "D1", "America East":  "D1",
    "Atlantic 10":    "D1", "Big 12":        "D1", "Big East":      "D1",
    "Big Ten":        "D1", "Big West":      "D1", "CAA":           "D1",
    "Horizon League": "D1", "Ivy League":    "D1", "MAAC":          "D1",
    "MPSF":           "D1", "Patriot":       "D1", "SEC":           "D1",
    "Summit League":  "D1", "WAC":           "D1",
    # ── D2 ────────────────────────────────────────────────────────────────────
    "GLIAC":          "D2", "PSAC":          "D2", "SAC":           "D2",
    # ── D3 ────────────────────────────────────────────────────────────────────
    "CCIW":           "D3", "Centennial":    "D3", "Colorado College": "D3",
    "Landmark":       "D3", "Liberty League":"D3", "MAC":           "D3",
    "MIAC":           "D3", "NCAC":          "D3", "NESCAC":        "D3",
    "NEWMAC":         "D3", "NWC":           "D3", "ODAC":          "D3",
    "SCIAC":          "D3", "UAA":           "D3",
    # ── NAIA ──────────────────────────────────────────────────────────────────
    "PCSC":           "NAIA",
}

# SCHOOL_META — per-school metadata for all 76 programs
# Fields: accept (int %), satMedian (int), hiddenIvy (bool), stem (bool),
#         merit ("none"|"moderate"|"high"), location (str), vibe (str),
#         moonshot (bool, optional)
# Keys must match canonical names after TEAM_NAME_MAP normalization.
# ---------------------------------------------------------------------------
SCHOOL_META = {
    # ── CENTENNIAL ───────────────────────────────────────────────────────────
    "Johns Hopkins University": {
        "accept": 7, "satMedian": 1510, "sat25": 1530, "sat75": 1560, "gpaMean": 3.93, "hiddenIvy": True, "stem": True,
        "merit": "none", "location": "Baltimore, MD",
        "vibe": "Research powerhouse where pre-med and STEM culture run the campus",
    },
    "Gettysburg College": {
        "accept": 43, "satMedian": 1230, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Gettysburg, PA",
        "vibe": "Historic campus with strong Greek life and leadership culture",
    },
    "Swarthmore College": {
        "accept": 7, "satMedian": 1505, "sat25": 1490, "sat75": 1550, "gpaMean": 3.91, "hiddenIvy": True, "stem": True,
        "merit": "none", "location": "Swarthmore, PA",
        "vibe": "Academically intense and collaborative with Quaker roots",
    },
    "Dickinson College": {
        "accept": 48, "satMedian": 1235, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Carlisle, PA",
        "vibe": "Sustainability-focused with strong international programs",
    },
    "Franklin & Marshall College": {
        "accept": 34, "satMedian": 1280, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Lancaster, PA",
        "vibe": "Pre-professional culture with strong pre-law and alumni network",
    },
    "Ursinus College": {
        "accept": 67, "satMedian": 1130, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Collegeville, PA",
        "vibe": "Warm undergraduate-focused campus with strong research access",
    },
    "Washington College": {
        "accept": 75, "satMedian": 1095, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Chestertown, MD",
        "vibe": "Small waterfront campus; close community and strong writing tradition",
    },
    "McDaniel College": {
        "accept": 82, "satMedian": 1100, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Westminster, MD",
        "vibe": "Friendly and personal campus with strong teacher education programs",
    },
    # ── LIBERTY LEAGUE ───────────────────────────────────────────────────────
    "Ithaca College": {
        "accept": 68, "satMedian": 1180, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Ithaca, NY",
        "vibe": "Creative and artsy; media, music, and performance everywhere",
    },
    "Rochester Institute of Technology": {
        "accept": 73, "satMedian": 1295, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Rochester, NY",
        "vibe": "Tech and project-driven culture built around co-ops and real careers",
    },
    "Rensselaer Polytechnic Institute": {
        "accept": 63, "satMedian": 1410, "sat25": 1280, "sat75": 1480, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Troy, NY",
        "vibe": "Pure engineering culture; hard-working students who live for problems",
    },
    "Clarkson University": {
        "accept": 80, "satMedian": 1205, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Potsdam, NY",
        "vibe": "Tight-knit STEM community in the North Country; outdoorsy and close",
    },
    "Union College": {
        "accept": 38, "satMedian": 1335, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Schenectady, NY",
        "vibe": "Liberal arts meets engineering; theme houses and strong traditions",
    },
    "Skidmore College": {
        "accept": 29, "satMedian": 1300, "sat25": 1230, "sat75": 1390, "gpaMean": 3.76, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Saratoga Springs, NY",
        "vibe": "Creative and arts-forward campus in a lively upstate NY town",
    },
    "Vassar College": {
        "accept": 18, "satMedian": 1455, "sat25": 1400, "sat75": 1540, "gpaMean": 3.82, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Poughkeepsie, NY",
        "vibe": "Progressive intellectual culture; students who love big ideas",
    },
    "St. Lawrence University": {
        "accept": 60, "satMedian": 1230, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Canton, NY",
        "vibe": "Outdoorsy North Country campus with a close community and athletics",
    },
    "Hobart and William Smith": {
        "accept": 58, "satMedian": 1185, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Geneva, NY",
        "vibe": "Lakeside dual-college campus with strong social scene and sailing",
    },
    "Bard College": {
        "accept": 64, "satMedian": 1265, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Annandale-on-Hudson, NY",
        "vibe": "Artsy and progressive with discussion-heavy classes and bohemian feel",
    },
    # ── NCAC ─────────────────────────────────────────────────────────────────
    "Denison University": {
        "accept": 28, "satMedian": 1325, "sat25": 1200, "sat75": 1400, "gpaMean": 3.73, "hiddenIvy": True, "stem": False,
        "merit": "high", "location": "Granville, OH",
        "vibe": "Beautiful hilltop campus; ambitious academics and a strong social scene",
    },
    "Kenyon College": {
        "accept": 33, "satMedian": 1370, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Gambier, OH",
        "vibe": "Literary and deeply intellectual; famous writing program in rural Ohio",
    },
    "John Carroll University": {
        "accept": 80, "satMedian": 1135, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "University Heights, OH",
        "vibe": "Jesuit values; service-oriented with strong business programs",
    },
    "DePauw University": {
        "accept": 67, "satMedian": 1195, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Greencastle, IN",
        "vibe": "Greek life-heavy with strong communications and music programs",
    },
    "Wabash College": {
        "accept": 69, "satMedian": 1165, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Crawfordsville, IN",
        "vibe": "All-male liberal arts with intense brotherhood and strong traditions",
    },
    "College of Wooster": {
        "accept": 57, "satMedian": 1195, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Wooster, OH",
        "vibe": "Every senior writes a thesis; close-knit with strong independent study",
    },
    "Oberlin College": {
        "accept": 33, "satMedian": 1385, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Oberlin, OH",
        "vibe": "Progressive and artistic; famous conservatory and engaged campus politics",
    },
    "Ohio Wesleyan University": {
        "accept": 83, "satMedian": 1150, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Delaware, OH",
        "vibe": "Emphasis on global experience; internship-focused with civic engagement",
    },
    "Wittenberg University": {
        "accept": 86, "satMedian": 1125, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Springfield, OH",
        "vibe": "Lutheran-rooted campus; personal and strong in teacher education",
    },
    # ── NESCAC ───────────────────────────────────────────────────────────────
    "Williams College": {
        "accept": 9, "satMedian": 1510, "sat25": 1500, "sat75": 1560, "gpaMean": 3.94, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Williamstown, MA",
        "vibe": "Consistently ranked #1 LAC; mountain campus with elite academics",
    },
    "Tufts University": {
        "accept": 9, "satMedian": 1500, "sat25": 1390, "sat75": 1540, "gpaMean": 3.87, "hiddenIvy": True, "stem": True,
        "merit": "none", "location": "Medford, MA",
        "vibe": "Globally minded near Boston; research-intensive with elite academics",
    },
    "Amherst College": {
        "accept": 9, "satMedian": 1515, "sat25": 1500, "sat75": 1560, "gpaMean": 3.93, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Amherst, MA",
        "vibe": "Open curriculum, no required courses; fiercely intellectual with 5-College access",
    },
    "Connecticut College": {
        "accept": 38, "satMedian": 1315, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "New London, CT",
        "vibe": "Student self-governance model; students run nearly everything on campus",
    },
    "Bates College": {
        "accept": 13, "satMedian": 1430, "sat25": 1330, "sat75": 1500, "gpaMean": 3.78, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Lewiston, ME",
        "vibe": "Politically engaged and outdoorsy; tight community in coastal Maine",
    },
    "Hamilton College": {
        "accept": 14, "satMedian": 1440, "sat25": 1360, "sat75": 1500, "gpaMean": 3.79, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Clinton, NY",
        "vibe": "Writing-intensive; every major leads to a thesis on a beautiful rural campus",
    },
    "Bowdoin College": {
        "accept": 9, "satMedian": 1495, "sat25": 1470, "sat75": 1540, "gpaMean": 3.86, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Brunswick, ME",
        "vibe": "Outdoorsy and intellectual in coastal Maine; sustainability and community",
    },
    "Middlebury College": {
        "accept": 13, "satMedian": 1445, "sat25": 1390, "sat75": 1520, "gpaMean": 3.83, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Middlebury, VT",
        "vibe": "Environmental passion meets rigorous academics in a beautiful Vermont setting",
    },
    "Colby College": {
        "accept": 11, "satMedian": 1435, "sat25": 1360, "sat75": 1490, "gpaMean": 3.79, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Waterville, ME",
        "vibe": "Liberal arts in the Maine wilderness; entrepreneurial with a tight community",
    },
    "Trinity College": {
        "accept": 34, "satMedian": 1310, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Hartford, CT",
        "vibe": "Classic New England campus with strong city partnerships and Greek life",
    },
    "Wesleyan University": {
        "accept": 17, "satMedian": 1455, "sat25": 1410, "sat75": 1530, "gpaMean": 3.85, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Middletown, CT",
        "vibe": "Quirky and politically active; film and social sciences define the culture",
    },
    # ── NEWMAC ───────────────────────────────────────────────────────────────
    "MIT": {
        "accept": 4, "satMedian": 1565, "sat25": 1500, "sat75": 1580, "gpaMean": 3.97, "hiddenIvy": False, "stem": True,
        "merit": "none", "moonshot": True, "location": "Cambridge, MA",
        "vibe": "The world's most famous STEM institution; unmatched resources and intensity",
    },
    "U.S. Coast Guard Academy": {
        "accept": 14, "satMedian": 1265, "sat25": 1190, "sat75": 1360, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "New London, CT",
        "vibe": "Military service academy; full scholarship, intense discipline, meaningful mission",
    },
    "Worcester Polytechnic Institute": {
        "accept": 58, "satMedian": 1370, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Worcester, MA",
        "vibe": "Project-based learning at a tech school with strong industry connections",
    },
    "Babson College": {
        "accept": 24, "satMedian": 1330, "sat25": 1260, "sat75": 1410, "gpaMean": 3.65, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Wellesley, MA",
        "vibe": "#1 entrepreneurship school; every freshman runs a real business for credit",
    },
    "Wheaton College (MA)": {
        "accept": 71, "satMedian": 1200, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Norton, MA",
        "vibe": "Small and personal campus reinventing itself with a bold connected curriculum",
    },
    "Springfield College": {
        "accept": 77, "satMedian": 1095, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Springfield, MA",
        "vibe": "Birthplace of basketball; health sciences and PT define the campus culture",
    },
    "Clark University": {
        "accept": 52, "satMedian": 1225, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Worcester, MA",
        "vibe": "Free fifth year for any grad program; research-first culture in an urban campus",
    },
    # ── NWC ──────────────────────────────────────────────────────────────────
    "Whitworth University": {
        "accept": 89, "satMedian": 1175, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Spokane, WA",
        "vibe": "Christian liberal arts in the Pacific Northwest with strong education programs",
    },
    "University of Puget Sound": {
        "accept": 87, "satMedian": 1200, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Tacoma, WA",
        "vibe": "NW outdoor culture meets classic liberal arts; tight-knit campus",
    },
    "Linfield University": {
        "accept": 88, "satMedian": 1120, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "McMinnville, OR",
        "vibe": "Small Oregon liberal arts in wine country; personal and community-focused",
    },
    "Whitman College": {
        "accept": 46, "satMedian": 1295, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Walla Walla, WA",
        "vibe": "Quirky Pacific NW intellectual gem with outdoor access and high grad school rates",
    },
    "Pacific Lutheran University": {
        "accept": 87, "satMedian": 1125, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Tacoma, WA",
        "vibe": "Lutheran values in the Pacific Northwest; strong music and education programs",
    },
    "Lewis & Clark College": {
        "accept": 65, "satMedian": 1275, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Portland, OR",
        "vibe": "Hippie-intellectual Portland culture; environmental law and social justice focus",
    },
    "Willamette University": {
        "accept": 81, "satMedian": 1190, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Salem, OR",
        "vibe": "NW LAC with strong law school connections and active civic engagement",
    },
    "George Fox University": {
        "accept": 93, "satMedian": 1090, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Newberg, OR",
        "vibe": "Christian university with strong community, athletics, and business programs",
    },
    # ── SCIAC ────────────────────────────────────────────────────────────────
    "Pomona-Pitzer": {
        "accept": 7, "satMedian": 1510, "sat25": 1470, "sat75": 1550, "gpaMean": 3.91, "hiddenIvy": True, "stem": True,
        "merit": "none", "location": "Claremont, CA",
        "vibe": "Elite SoCal LAC in the Claremont Consortium; 5 colleges sharing resources",
    },
    "Claremont-Mudd-Scripps": {
        "accept": 9, "satMedian": 1490, "sat25": 1470, "sat75": 1560, "gpaMean": 3.9, "hiddenIvy": True, "stem": True,
        "merit": "none", "location": "Claremont, CA",
        "vibe": "Harvey Mudd's STEM intensity meets Scripps' creative and humanistic edge",
    },
    "Chapman University": {
        "accept": 52, "satMedian": 1230, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Orange, CA",
        "vibe": "Film school prestige meets SoCal sunshine; entrepreneurial and media-forward",
    },
    "Caltech": {
        "accept": 3, "satMedian": 1560, "sat25": 1530, "sat75": 1580, "gpaMean": 3.97, "hiddenIvy": False, "stem": True,
        "merit": "none", "moonshot": True, "location": "Pasadena, CA",
        "vibe": "Hardest STEM school to enter in America; Nobel laureates teach undergrads",
    },
    "Whittier College": {
        "accept": 73, "satMedian": 1100, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Whittier, CA",
        "vibe": "Liberal arts with strong Latino heritage and close community feel in SoCal",
    },
    "Cal Lutheran University": {
        "accept": 61, "satMedian": 1125, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Thousand Oaks, CA",
        "vibe": "Lutheran roots in Ventura County; strong business and communications programs",
    },
    "Occidental College": {
        "accept": 37, "satMedian": 1295, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Los Angeles, CA",
        "vibe": "Progressive urban LAC in Eagle Rock; politics and international relations culture",
    },
    "University of Redlands": {
        "accept": 67, "satMedian": 1140, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Redlands, CA",
        "vibe": "Inland Empire LAC; strong music, environmental studies, and pre-law culture",
    },
    "University of La Verne": {
        "accept": 65, "satMedian": 1080, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "La Verne, CA",
        "vibe": "Close-knit SoCal campus with strong business and criminal justice programs",
    },
    # ── UAA ──────────────────────────────────────────────────────────────────
    # Names are abbreviated in the workbook — kept as-is (not truncation, just short forms)
    "Emory": {
        "accept": 12, "satMedian": 1470, "sat25": 1360, "sat75": 1530, "gpaMean": 3.87, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Atlanta, GA",
        "vibe": "Research powerhouse in Atlanta; dominant pre-med culture and strong social scene",
    },
    "NYU": {
        "accept": 12, "satMedian": 1460, "sat25": 1350, "sat75": 1530, "gpaMean": 3.79, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "New York, NY",
        "vibe": "Urban campus without borders; Greenwich Village is your quad in the heart of NYC",
    },
    "Chicago": {
        "accept": 6, "satMedian": 1530, "sat25": 1510, "sat75": 1570, "gpaMean": 3.94, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Chicago, IL",
        "vibe": "Intellectual intensity above all else; famous for taking ideas more seriously than sleep",
    },
    "Washington (Mo)": {
        "accept": 14, "satMedian": 1500, "sat25": 1480, "sat75": 1560, "gpaMean": 3.94, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "St. Louis, MO",
        "vibe": "Research powerhouse in the Midwest; strong pre-med, engineering, and business",
    },
    "Carnegie Mellon": {
        "accept": 11, "satMedian": 1535, "sat25": 1460, "sat75": 1560, "gpaMean": 3.87, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Pittsburgh, PA",
        "vibe": "Top CS and engineering with a rigorous, career-driven campus culture",
    },
    "Case Western": {
        "accept": 30, "satMedian": 1455, "sat25": 1430, "sat75": 1560, "gpaMean": 3.82, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Cleveland, OH",
        "vibe": "STEM-focused research university; pre-med and engineering define campus life",
    },
    "Rochester": {
        "accept": 29, "satMedian": 1440, "sat25": 1380, "sat75": 1530, "gpaMean": 3.83, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Rochester, NY",
        "vibe": "Research-intensive with strong engineering, optics, and pre-med programs",
    },
    "Brandeis": {
        "accept": 37, "satMedian": 1420, "sat25": 1380, "sat75": 1530, "gpaMean": 3.79, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Waltham, MA",
        "vibe": "Social justice mission and research strength near Boston with a unique founding story",
    },
    # ── MIAC ─────────────────────────────────────────────────────────────────
    "Gustavus Adolphus College": {
        "accept": 72, "satMedian": 1195, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Saint Peter, MN",
        "vibe": "Lutheran liberal arts with Swedish heritage and strong athletics in Minnesota",
    },
    "Carleton College": {
        "accept": 18, "satMedian": 1495, "sat25": 1470, "sat75": 1540, "gpaMean": 3.89, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Northfield, MN",
        "vibe": "One of the Midwest's best LACs; intellectual culture with top grad school placement",
    },
    "Saint John's University": {
        "accept": 75, "satMedian": 1145, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Collegeville, MN",
        "vibe": "Coordinate college with Saint Benedict; Benedictine tradition and strong community",
    },
    "Macalester College": {
        "accept": 28, "satMedian": 1430, "sat25": 1380, "sat75": 1520, "gpaMean": 3.85, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Saint Paul, MN",
        "vibe": "Globally focused and politically active urban LAC with high international enrollment",
    },
    "Saint Olaf College": {
        "accept": 48, "satMedian": 1270, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Northfield, MN",
        "vibe": "Norwegian Lutheran roots; world-famous music programs and close Minnesota community",
    },
    "Hamline University": {
        "accept": 82, "satMedian": 1130, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Saint Paul, MN",
        "vibe": "Urban liberal arts with social justice focus; personal and accessible in Twin Cities",
    },
    # ── MIAC (remaining 4 — 6 already above) ─────────────────────────────────
    "Augsburg University": {
        "accept": 72, "satMedian": 1115, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Minneapolis, MN",
        "vibe": "Urban Lutheran campus in Minneapolis with strong social work and nursing programs",
    },
    "College of Saint Benedict": {
        "accept": 69, "satMedian": 1175, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "St. Joseph, MN",
        "vibe": "Women's college partnered with Saint John's; strong Catholic identity and close community",
    },
    "Concordia College": {
        "accept": 60, "satMedian": 1150, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Moorhead, MN",
        "vibe": "Lutheran liberal arts on the Minnesota-North Dakota border; strong music and global programs",
    },
    "Saint Catherine University": {
        "accept": 62, "satMedian": 1110, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "St. Paul, MN",
        "vibe": "Catholic women's university in the Twin Cities with strong health sciences programs",
    },
    # ── NEWMAC (remaining 3 — 7 already above) ───────────────────────────────
    "Mount Holyoke College": {
        "accept": 49, "satMedian": 1335, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "South Hadley, MA",
        "vibe": "Historic women's college in the Pioneer Valley; Seven Sisters with strong social science traditions",
    },
    "Smith College": {
        "accept": 33, "satMedian": 1390, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Northampton, MA",
        "vibe": "Premier women's liberal arts college; consistently top-ranked with exceptional alumnae network",
    },
    "Wellesley College": {
        "accept": 14, "satMedian": 1440, "sat25": 1370, "sat75": 1510, "gpaMean": 3.87, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Wellesley, MA",
        "vibe": "Elite women's college near Boston; Hillary Clinton and Madeline Albright territory",
    },
    # ── ODAC ─────────────────────────────────────────────────────────────────
    "Bridgewater College": {
        "accept": 63, "satMedian": 1100, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Bridgewater, VA",
        "vibe": "Small Church of the Brethren college in the Shenandoah Valley with a close athletic community",
    },
    "Greensboro College": {
        "accept": 47, "satMedian": 1065, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Greensboro, NC",
        "vibe": "Methodist-affiliated urban college; small and personal with strong arts and teacher ed programs",
    },
    "Hampden-Sydney College": {
        "accept": 65, "satMedian": 1185, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Hampden Sydney, VA",
        "vibe": "All-male liberal arts college with a strong honor code tradition and close brotherhood",
    },
    "Hollins University": {
        "accept": 62, "satMedian": 1160, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Roanoke, VA",
        "vibe": "Women's university in the Blue Ridge with creative writing fame and an equestrian program",
    },
    "Randolph College": {
        "accept": 62, "satMedian": 1150, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Lynchburg, VA",
        "vibe": "Small coed liberal arts college with an equestrian program and strong individualized attention",
    },
    "Randolph-Macon College": {
        "accept": 58, "satMedian": 1175, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Ashland, VA",
        "vibe": "Methodist-affiliated LAC near Richmond with strong pre-law and business tracks",
    },
    "Roanoke College Swimming Maroo": {
        "accept": 65, "satMedian": 1150, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Salem, VA",
        "vibe": "Lutheran liberal arts college in the Blue Ridge foothills with a strong athletics tradition",
    },
    "Sweet Briar College": {
        "accept": 47, "satMedian": 1130, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Sweet Briar, VA",
        "vibe": "Women's college with an equestrian program on a stunning Virginia estate; intimate and resilient",
    },
    "University of Lynchburg": {
        "accept": 60, "satMedian": 1120, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Lynchburg, VA",
        "vibe": "Disciples of Christ-affiliated campus with strong health sciences and a growing athletics profile",
    },
    "Virginia Wesleyan University": {
        "accept": 74, "satMedian": 1090, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Norfolk, VA",
        "vibe": "Methodist university near Virginia Beach with a student-centered campus and growing athletics",
    },
    "Washington and Lee University": {
        "accept": 21, "satMedian": 1455, "sat25": 1390, "sat75": 1520, "gpaMean": 3.82, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Lexington, VA",
        "vibe": "Honor-code-driven LAC with an exceptional law school pipeline and Southern intellectual tradition",
    },
    # ── CCIW ─────────────────────────────────────────────────────────────────
    "Augustana College": {
        "accept": 66, "satMedian": 1175, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Rock Island, IL",
        "vibe": "Swedish Lutheran LAC on the Mississippi with strong pre-health and music programs",
    },
    "Carroll University": {
        "accept": 71, "satMedian": 1165, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Waukesha, WI",
        "vibe": "Presbyterian-rooted campus near Milwaukee with strong physical therapy and nursing programs",
    },
    "Carthage College": {
        "accept": 62, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Kenosha, WI",
        "vibe": "Lutheran-affiliated college on Lake Michigan with a semester-based calendar and strong arts",
    },
    "Illinois Wesleyan University": {
        "accept": 61, "satMedian": 1205, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Bloomington, IL",
        "vibe": "Highly selective for a Midwest LAC; strong theatre, pre-law, and business in a college town",
    },
    "Millikin University": {
        "accept": 59, "satMedian": 1115, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Decatur, IL",
        "vibe": "Performance-focused LAC with a nationally known entrepreneurship program and strong arts",
    },
    "North Central College": {
        "accept": 62, "satMedian": 1190, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Naperville, IL",
        "vibe": "Methodist-affiliated college in Chicago's suburbs with strong business and pre-professional programs",
    },
    "Wheaton College": {
        "accept": 64, "satMedian": 1300, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Wheaton, IL",
        "vibe": "Evangelical Christian LAC with rigorous academics; produces many graduate school attendees",
    },
    # ── LANDMARK ─────────────────────────────────────────────────────────────
    "Catholic University of America": {
        "accept": 76, "satMedian": 1175, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Washington, DC",
        "vibe": "The national university of the Catholic Church; DC location enables strong internship access",
    },
    "Drew University": {
        "accept": 63, "satMedian": 1205, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Madison, NJ",
        "vibe": "Methodis LAC in NJ suburbs; small and personal with strong connections to NYC and Wall Street",
    },
    "Elizabethtown College": {
        "accept": 67, "satMedian": 1165, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Elizabethtown, PA",
        "vibe": "Church of the Brethren college in Lancaster County; strong occupational therapy and social work",
    },
    "Goucher College": {
        "accept": 73, "satMedian": 1200, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Baltimore, MD",
        "vibe": "Globally focused LAC near Baltimore; every student studies abroad and faculty are highly accessible",
    },
    "Juniata College": {
        "accept": 68, "satMedian": 1165, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Huntingdon, PA",
        "vibe": "Student-designed majors and strong pre-health in rural Pennsylvania; personal and innovative",
    },
    "Lycoming College": {
        "accept": 72, "satMedian": 1100, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Williamsport, PA",
        "vibe": "Methodist LAC in north-central PA; generous merit aid and strong undergraduate research",
    },
    "Moravian University": {
        "accept": 70, "satMedian": 1145, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Bethlehem, PA",
        "vibe": "Historic Moravian campus in Bethlehem; strong nursing, business, and community engagement",
    },
    "Susquehanna University": {
        "accept": 81, "satMedian": 1160, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Selinsgrove, PA",
        "vibe": "Lutheran LAC on the Susquehanna River; strong communications and creative writing programs",
    },
    "University of Scranton": {
        "accept": 72, "satMedian": 1195, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Scranton, PA",
        "vibe": "Jesuit university in the Pocono foothills; strong business, health sciences, and service culture",
    },
    "Wilkes University": {
        "accept": 73, "satMedian": 1125, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Wilkes-Barre, PA",
        "vibe": "Engineering and health sciences focus in northeast PA; hands-on and career-oriented",
    },
    # ── MAC ───────────────────────────────────────────────────────────────────
    "Arcadia University": {
        "accept": 62, "satMedian": 1175, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Glenside, PA",
        "vibe": "Global-focused LAC in Philadelphia's suburbs with a stunning castle campus and strong study-abroad",
    },
    "Fairleigh Dickinson University": {
        "accept": 80, "satMedian": 1085, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Teaneck, NJ",
        "vibe": "Multi-campus metro NJ university with strong business and pharmacy programs",
    },
    "Hood College Swimming": {
        "accept": 62, "satMedian": 1170, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Frederick, MD",
        "vibe": "Small coed LAC in Frederick's historic district with strong biomedical science and education",
    },
    "King's College": {
        "accept": 71, "satMedian": 1115, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Wilkes-Barre, PA",
        "vibe": "Catholic LAC in northeast PA with strong physician assistant and business programs",
    },
    "Lebanon Valley College": {
        "accept": 66, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Annville, PA",
        "vibe": "United Methodist LAC in central PA; strong physical therapy, music, and athletic training",
    },
    "Messiah University": {
        "accept": 63, "satMedian": 1195, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Mechanicsburg, PA",
        "vibe": "Christian university with strong nursing, engineering, and social work near Harrisburg",
    },
    "Misericordia University": {
        "accept": 68, "satMedian": 1125, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Dallas, PA",
        "vibe": "Health sciences powerhouse in northeast PA; outstanding PT, OT, and nursing programs",
    },
    "Stevens Institute of Technolog": {
        "accept": 42, "satMedian": 1400, "sat25": 1310, "sat75": 1480, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Hoboken, NJ",
        "vibe": "Engineering and tech university on the Hudson with a stunning Manhattan skyline view",
    },
    "Stevenson University": {
        "accept": 57, "satMedian": 1100, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Owings Mills, MD",
        "vibe": "Career-focused university near Baltimore with strong forensics, nursing, and business programs",
    },
    "Widener University": {
        "accept": 64, "satMedian": 1115, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Chester, PA",
        "vibe": "Engineering, nursing, and law-pipeline campus near Philadelphia with strong professional programs",
    },
    "York College of Pennsylvania": {
        "accept": 61, "satMedian": 1145, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "York, PA",
        "vibe": "Business and nursing-focused university in south-central PA; career-oriented and affordable",
    },
    # ── MAAC ─────────────────────────────────────────────────────────────────
    "Canisius University": {
        "accept": 81, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Buffalo, NY",
        "vibe": "Jesuit university in Buffalo with strong business, health sciences, and athletics identity",
    },
    "Fairfield University": {
        "accept": 60, "satMedian": 1275, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Fairfield, CT",
        "vibe": "Jesuit university on Connecticut's Gold Coast; strong business and nursing near NYC",
    },
    "Iona University": {
        "accept": 72, "satMedian": 1120, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "New Rochelle, NY",
        "vibe": "Irish Christian Brothers university 20 minutes from Manhattan; strong business and communications",
    },
    "Manhattan University": {
        "accept": 76, "satMedian": 1165, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Riverdale, NY",
        "vibe": "Christian Brothers university in the Bronx with strong engineering and education programs",
    },
    "Marist University": {
        "accept": 49, "satMedian": 1230, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Poughkeepsie, NY",
        "vibe": "Scenic Hudson Valley campus with strong fashion, communications, and business programs",
    },
    "Merrimack College": {
        "accept": 77, "satMedian": 1175, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "North Andover, MA",
        "vibe": "Augustinian university north of Boston with strong health sciences and business programs",
    },
    "Mount Saint Mary's University": {
        "accept": 75, "satMedian": 1115, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Emmitsburg, MD",
        "vibe": "Catholic university in the Blue Ridge foothills with a close community and strong nursing",
    },
    "Niagara University": {
        "accept": 78, "satMedian": 1115, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Niagara University, NY",
        "vibe": "Vincentian Catholic university near Niagara Falls; strong education, social work, and business",
    },
    "Rider University": {
        "accept": 72, "satMedian": 1120, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Lawrenceville, NJ",
        "vibe": "Business and education-focused university near Princeton with Westminster Choir College",
    },
    "Sacred Heart University": {
        "accept": 72, "satMedian": 1180, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Fairfield, CT",
        "vibe": "Catholic university in affluent coastal Connecticut with strong PT, nursing, and athletics",
    },
    "Saint Peter's University": {
        "accept": 80, "satMedian": 1095, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Jersey City, NJ",
        "vibe": "Jesuit university across from lower Manhattan; strong business and pre-health in an urban setting",
    },
    "Siena University": {
        "accept": 71, "satMedian": 1145, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Loudonville, NY",
        "vibe": "Franciscan Catholic university near Albany with strong business, biology, and social work",
    },
    # ── PATRIOT ──────────────────────────────────────────────────────────────
    "American University": {
        "accept": 35, "satMedian": 1310, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Washington, DC",
        "vibe": "International relations and politics powerhouse in DC; students go straight from class to Capitol Hill",
    },
    "Boston University": {
        "accept": 14, "satMedian": 1450, "sat25": 1350, "sat75": 1500, "gpaMean": 3.73, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Boston, MA",
        "vibe": "Large research university on the Charles with elite engineering, business, and communications",
    },
    "Bucknell University": {
        "accept": 36, "satMedian": 1345, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Lewisburg, PA",
        "vibe": "Selective LAC-meets-engineering in central PA; strong alumni network and Greek life culture",
    },
    "Colgate University": {
        "accept": 21, "satMedian": 1420, "sat25": 1380, "sat75": 1510, "gpaMean": 3.82, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Hamilton, NY",
        "vibe": "Highly selective rural NY LAC with strong preprofessional culture and loyal athletic fanbase",
    },
    "College of the Holy Cross": {
        "accept": 32, "satMedian": 1360, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Worcester, MA",
        "vibe": "Jesuit LAC with rigorous core curriculum and strong Jesuit service tradition in New England",
    },
    "Lafayette College": {
        "accept": 33, "satMedian": 1325, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Easton, PA",
        "vibe": "Engineering-meets-liberal arts on a hilltop campus; strong alumni network and Division 1 rivalry",
    },
    "Lehigh University": {
        "accept": 33, "satMedian": 1385, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Bethlehem, PA",
        "vibe": "STEM-forward research university with a strong engineering and business culture in the Lehigh Valley",
    },
    "Loyola University": {
        "accept": 54, "satMedian": 1265, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Baltimore, MD",
        "vibe": "Jesuit university overlooking Baltimore; strong business, communications, and service programs",
    },
    "United States Military Academy": {
        "accept": 9, "satMedian": 1270, "sat25": 1200, "sat75": 1400, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "West Point, NY",
        "vibe": "West Point — full scholarship, intense commitment, and guaranteed career in military leadership",
    },
    "United States Navy Academy": {
        "accept": 9, "satMedian": 1290, "sat25": 1220, "sat75": 1390, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Annapolis, MD",
        "vibe": "Annapolis — full scholarship, rigorous engineering curriculum, and a Navy or Marine career ahead",
    },
    # ── IVY LEAGUE ───────────────────────────────────────────────────────────
    "Brown University": {
        "accept": 5, "satMedian": 1510, "sat25": 1440, "sat75": 1570, "gpaMean": 3.94, "hiddenIvy": False, "ivyLeague": True, "stem": False,
        "merit": "none", "location": "Providence, RI",
        "vibe": "Open Curriculum Ivy gives students unusual freedom — Brown is the most academically flexible of the eight",
    },
    "Columbia University": {
        "accept": 4, "satMedian": 1530, "sat25": 1470, "sat75": 1560, "gpaMean": 3.92, "hiddenIvy": False, "ivyLeague": True, "stem": True,
        "merit": "none", "location": "New York, NY",
        "vibe": "Ivy in Morningside Heights — the Core Curriculum meets the greatest city on earth",
    },
    "Cornell University": {
        "accept": 7, "satMedian": 1490, "sat25": 1400, "sat75": 1560, "gpaMean": 3.89, "hiddenIvy": False, "ivyLeague": True, "stem": True,
        "merit": "none", "location": "Ithaca, NY",
        "vibe": "The broadest Ivy — engineering, agriculture, hotel, and arts all on one spectacular gorge campus",
    },
    "Dartmouth College": {
        "accept": 6, "satMedian": 1510, "sat25": 1440, "sat75": 1560, "gpaMean": 3.95, "hiddenIvy": False, "ivyLeague": True, "stem": False,
        "merit": "none", "location": "Hanover, NH",
        "vibe": "Smallest Ivy with fierce alumni loyalty; outdoor culture, Greek life, and an undergrad-first focus",
    },
    "Harvard University": {
        "accept": 3, "satMedian": 1540, "sat25": 1460, "sat75": 1570, "gpaMean": 3.95, "hiddenIvy": False, "ivyLeague": True, "stem": True,
        "merit": "none", "location": "Cambridge, MA",
        "vibe": "The most recognized university brand in the world — extraordinary in every dimension",
    },
    "Princeton University": {
        "accept": 4, "satMedian": 1530, "sat25": 1460, "sat75": 1570, "gpaMean": 3.94, "hiddenIvy": False, "ivyLeague": True, "stem": True,
        "merit": "none", "location": "Princeton, NJ",
        "vibe": "No-loan financial aid and the strongest endowment per student of any university in America",
    },
    "University of Pennsylvania": {
        "accept": 6, "satMedian": 1510, "sat25": 1450, "sat75": 1560, "gpaMean": 3.9, "hiddenIvy": False, "ivyLeague": True, "stem": True,
        "merit": "none", "location": "Philadelphia, PA",
        "vibe": "Wharton, Penn Medicine, and interdisciplinary programs in one of America's greatest college cities",
    },
    "Yale University": {
        "accept": 5, "satMedian": 1535, "sat25": 1460, "sat75": 1570, "gpaMean": 3.95, "hiddenIvy": False, "ivyLeague": True, "stem": False,
        "merit": "none", "location": "New Haven, CT",
        "vibe": "Architecture, drama, law, and music in a campus that looks like what college is supposed to look like",
    },
    # ── ACC ───────────────────────────────────────────────────────────────────
    "Boston College": {
        "accept": 19, "satMedian": 1410, "sat25": 1360, "sat75": 1520, "gpaMean": 3.87, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Chestnut Hill, MA",
        "vibe": "Jesuit university with major football culture and strong pre-law, finance, and nursing programs",
    },
    "California, University of, Ber": {
        "accept": 14, "satMedian": 1415, "sat25": 1310, "sat75": 1530, "gpaMean": 3.9, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Berkeley, CA",
        "vibe": "The flagship UC — world-class research, Nobel laureates, and legendary campus activism",
    },
    "Duke University": {
        "accept": 6, "satMedian": 1530, "sat25": 1450, "sat75": 1570, "gpaMean": 3.94, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Durham, NC",
        "vibe": "Elite research university with a powerhouse basketball program and a beautiful Gothic campus",
    },
    "Florida State University": {
        "accept": 25, "satMedian": 1265, "sat25": 1230, "sat75": 1390, "gpaMean": 3.69, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Tallahassee, FL",
        "vibe": "Major public flagship with strong business, film, and performing arts in Florida's capital city",
    },
    "Georgia Institute of Technolog": {
        "accept": 17, "satMedian": 1440, "sat25": 1360, "sat75": 1530, "gpaMean": 3.87, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Atlanta, GA",
        "vibe": "Top-3 public engineering school; demanding but with a world-class alumni network and Atlanta access",
    },
    "Louisville, University of": {
        "accept": 70, "satMedian": 1170, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Louisville, KY",
        "vibe": "Urban research university in Louisville with strong health sciences, business, and engineering",
    },
    "North Carolina State Universit": {
        "accept": 47, "satMedian": 1290, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Raleigh, NC",
        "vibe": "NC's engineering and agriculture flagship in Research Triangle; career-focused and highly connected",
    },
    "North Carolina, University of": {
        "accept": 17, "satMedian": 1340, "sat25": 1300, "sat75": 1490, "gpaMean": 3.89, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Chapel Hill, NC",
        "vibe": "The birthplace of public higher education in America; research powerhouse with a legendary campus",
    },
    "Notre Dame, University of": {
        "accept": 13, "satMedian": 1500, "sat25": 1400, "sat75": 1550, "gpaMean": 3.94, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Notre Dame, IN",
        "vibe": "Elite Catholic university with a massive football identity, strong business, and an unmatched alumni network",
    },
    "Pittsburgh, University of": {
        "accept": 53, "satMedian": 1310, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Pittsburgh, PA",
        "vibe": "Major research university in a revitalized city; strong STEM, business, and health sciences",
    },
    "Southern Methodist University": {
        "accept": 45, "satMedian": 1380, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Dallas, TX",
        "vibe": "Well-heeled Dallas campus with Dedman School of Law and a strong pre-law and business culture",
    },
    "Stanford University": {
        "accept": 4, "satMedian": 1530, "sat25": 1440, "sat75": 1570, "gpaMean": 3.96, "hiddenIvy": False, "stem": True,
        "merit": "none", "moonshot": True, "location": "Stanford, CA",
        "vibe": "Silicon Valley's university — extraordinary in every dimension, and nearly impossible to enter",
    },
    "University of Miami (Florida)": {
        "accept": 23, "satMedian": 1380, "sat25": 1290, "sat75": 1440, "gpaMean": 3.75, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Coral Gables, FL",
        "vibe": "Selective private research university in South Florida; strong music, marine science, and business",
    },
    "VA Tech": {
        "accept": 57, "satMedian": 1305, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Blacksburg, VA",
        "vibe": "Engineering and architecture powerhouse in the Blue Ridge; strong Hokie athletics culture",
    },
    "Virginia, University of": {
        "accept": 20, "satMedian": 1425, "sat25": 1340, "sat75": 1520, "gpaMean": 3.88, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Charlottesville, VA",
        "vibe": "Jefferson's academical village — academically elite public flagship with strong honor culture",
    },
    # ── BIG TEN ───────────────────────────────────────────────────────────────
    "Indiana University": {
        "accept": 82, "satMedian": 1220, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Bloomington, IN",
        "vibe": "Big Ten flagship with a world-class music school, strong business, and a classic college-town feel",
    },
    "Iowa, University of": {
        "accept": 85, "satMedian": 1185, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Iowa City, IA",
        "vibe": "Big Ten flagship with the nation's top-ranked creative writing program and strong health sciences",
    },
    "Michigan, University of": {
        "accept": 18, "satMedian": 1445, "sat25": 1340, "sat75": 1530, "gpaMean": 3.89, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Ann Arbor, MI",
        "vibe": "The Michigan Difference — elite public university with a massive endowment and All-American campus",
    },
    "Northwestern University": {
        "accept": 7, "satMedian": 1530, "sat25": 1440, "sat75": 1550, "gpaMean": 3.94, "hiddenIvy": True, "stem": True,
        "merit": "none", "location": "Evanston, IL",
        "vibe": "The Ivy of the Midwest — elite research university on Lake Michigan with a powerhouse journalism school",
    },
    "Ohio State University": {
        "accept": 52, "satMedian": 1335, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Columbus, OH",
        "vibe": "Massive flagship with world-class research, medicine, and one of America's most passionate fanbases",
    },
    "Pennsylvania State University": {
        "accept": 56, "satMedian": 1245, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "State College, PA",
        "vibe": "Penn State pride is legendary; massive STEM and business programs in a quintessential college town",
    },
    "Purdue University": {
        "accept": 68, "satMedian": 1290, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "West Lafayette, IN",
        "vibe": "Engineering powerhouse that launched more Fortune 500 leaders than almost any school in America",
    },
    "Rutgers University": {
        "accept": 67, "satMedian": 1225, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "New Brunswick, NJ",
        "vibe": "New Jersey's flagship public university with strong pharmacy, business, and public policy programs",
    },
    "University of California, Los": {
        "accept": 9, "satMedian": 1405, "sat25": 1280, "sat75": 1530, "gpaMean": 3.9, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Los Angeles, CA",
        "vibe": "UCLA — elite public research university in LA with Bruin athletics and world-class film and medicine",
    },
    "University of Illinois": {
        "accept": 62, "satMedian": 1310, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Urbana-Champaign, IL",
        "vibe": "Top-5 public engineering school; massive campus with elite CS, business, and architecture programs",
    },
    "University of Minnesota": {
        "accept": 75, "satMedian": 1275, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Minneapolis, MN",
        "vibe": "Big Ten flagship in the Twin Cities with strong medical, law, and business schools",
    },
    "University of Nebraska-Lincoln": {
        "accept": 80, "satMedian": 1190, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Lincoln, NE",
        "vibe": "Nebraska Cornhusker pride meets strong engineering and agriculture in a welcoming college town",
    },
    "University of Southern Califor": {
        "accept": 12, "satMedian": 1455, "sat25": 1360, "sat75": 1530, "gpaMean": 3.87, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Los Angeles, CA",
        "vibe": "USC — elite private research university in LA; elite alumni network, film school, and strong athletics",
    },
    "Wisconsin, University of, Madi": {
        "accept": 57, "satMedian": 1355, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Madison, WI",
        "vibe": "UW-Madison — top public research university on a beautiful lakefront campus; strong in nearly everything",
    },
    # ── SEC ───────────────────────────────────────────────────────────────────
    "Auburn University": {
        "accept": 80, "satMedian": 1255, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Auburn, AL",
        "vibe": "War Eagle — strong engineering, business, and pharmacy programs in a tight-knit college town",
    },
    "Georgia, University of": {
        "accept": 45, "satMedian": 1340, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Athens, GA",
        "vibe": "Georgia Bulldogs — elite public flagship with a legendary campus town and strong business programs",
    },
    "Kentucky, University of": {
        "accept": 95, "satMedian": 1195, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Lexington, KY",
        "vibe": "Wildcat country — strong pharmacy, business, and engineering programs in the Horse Capital of the World",
    },
    "Louisiana State University": {
        "accept": 73, "satMedian": 1215, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Baton Rouge, LA",
        "vibe": "LSU — Tiger Stadium is the loudest place in college football; strong engineering and mass comm",
    },
    "Missouri": {
        "accept": 80, "satMedian": 1225, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Columbia, MO",
        "vibe": "Mizzou — top journalism school in America and a flagship with strong agriculture and business",
    },
    "South Carolina, University of": {
        "accept": 66, "satMedian": 1250, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Columbia, SC",
        "vibe": "Gamecocks flagship with a top-ranked international business school and strong nursing programs",
    },
    "Texas A&M University": {
        "accept": 57, "satMedian": 1245, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "College Station, TX",
        "vibe": "Aggie tradition runs deep — one of America's largest engineering and agriculture programs",
    },
    "University of Alabama": {
        "accept": 80, "satMedian": 1215, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Tuscaloosa, AL",
        "vibe": "Roll Tide — massive flagship with strong engineering, business, and enormous SEC football culture",
    },
    "University of Arkansas": {
        "accept": 79, "satMedian": 1205, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Fayetteville, AR",
        "vibe": "Razorbacks flagship in the Ozarks with a strong Walton School of Business and growing research profile",
    },
    "University of Florida": {
        "accept": 23, "satMedian": 1385, "sat25": 1310, "sat75": 1470, "gpaMean": 3.89, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Gainesville, FL",
        "vibe": "Top-5 public university — UF has elite research, Gator athletics, and an outstanding value proposition",
    },
    "University of Tennessee": {
        "accept": 67, "satMedian": 1235, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Knoxville, TN",
        "vibe": "Vol Nation — Tennessee orange campus on the Tennessee River with strong business and engineering",
    },
    "University of Texas": {
        "accept": 29, "satMedian": 1360, "sat25": 1230, "sat75": 1460, "gpaMean": 3.72, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Austin, TX",
        "vibe": "UT Austin — What starts here changes the world; massive flagship in the live music capital",
    },
    "Vanderbilt University": {
        "accept": 7, "satMedian": 1530, "sat25": 1460, "sat75": 1560, "gpaMean": 3.92, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Nashville, TN",
        "vibe": "Elite private research university in Nashville; Vandy offers Ivy-caliber academics with SEC athletics",
    },
    # ── BIG 12 ────────────────────────────────────────────────────────────────
    "Arizona State University": {
        "accept": 88, "satMedian": 1215, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Tempe, AZ",
        "vibe": "America's largest innovation university; strong engineering, business, and journalism in the Sunbelt",
    },
    "Brigham Young University": {
        "accept": 65, "satMedian": 1265, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Provo, UT",
        "vibe": "LDS flagship with strong accounting, animation, and pre-law — honor code shapes daily campus life",
    },
    "Iowa State University": {
        "accept": 92, "satMedian": 1185, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Ames, IA",
        "vibe": "Cyclones — top-10 design school, strong engineering and agriculture, welcoming Midwest culture",
    },
    "Texas Christian University": {
        "accept": 45, "satMedian": 1290, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Fort Worth, TX",
        "vibe": "TCU — selective Methodist university in Fort Worth with strong business and communications",
    },
    "University of Arizona": {
        "accept": 84, "satMedian": 1195, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Tucson, AZ",
        "vibe": "Wildcats flagship in the Sonoran Desert; strong optics, astronomy, and business programs",
    },
    "University of Cincinnati": {
        "accept": 86, "satMedian": 1215, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Cincinnati, OH",
        "vibe": "Bearcats flagship with a top-10 co-op program; pioneered cooperative education in America",
    },
    "University of Houston": {
        "accept": 65, "satMedian": 1200, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Houston, TX",
        "vibe": "Cougars — urban research university in Houston with strong engineering and entrepreneurship",
    },
    "University of Kansas": {
        "accept": 93, "satMedian": 1195, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Lawrence, KS",
        "vibe": "KU — Jayhawks flagship with a legendary pharmacy program and top-ranked journalism school",
    },
    "University of Utah": {
        "accept": 84, "satMedian": 1235, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Salt Lake City, UT",
        "vibe": "Utes — flagship with access to world-class skiing and strong gaming, engineering, and health programs",
    },
    "West Virginia University": {
        "accept": 79, "satMedian": 1170, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Morgantown, WV",
        "vibe": "Mountaineers flagship with strong forensic science, pharmacy, and a tight-knit Appalachian community",
    },
    # ── BIG EAST ──────────────────────────────────────────────────────────────
    "Butler University": {
        "accept": 72, "satMedian": 1225, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Indianapolis, IN",
        "vibe": "Selective private university with a gorgeous campus and strong pharmacy, business, and performing arts",
    },
    "Connecticut, University of": {
        "accept": 56, "satMedian": 1280, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Storrs, CT",
        "vibe": "UConn — flagship with elite nursing, business, and pharmacy programs and a historic basketball tradition",
    },
    "Georgetown University": {
        "accept": 12, "satMedian": 1490, "sat25": 1380, "sat75": 1550, "gpaMean": 3.89, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Washington, DC",
        "vibe": "Elite Jesuit university in DC — politics, pre-law, and international relations on Capitol Hill's doorstep",
    },
    "Providence College": {
        "accept": 50, "satMedian": 1235, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Providence, RI",
        "vibe": "Dominican Catholic college with a required Western civilization core and a passionate Friar basketball culture",
    },
    "Seton Hall University": {
        "accept": 73, "satMedian": 1225, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "South Orange, NJ",
        "vibe": "Catholic university 14 miles from Manhattan; strong diplomacy, nursing, and business programs",
    },
    "Villanova University": {
        "accept": 24, "satMedian": 1415, "sat25": 1310, "sat75": 1470, "gpaMean": 3.8, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Villanova, PA",
        "vibe": "Augustinian university near Philadelphia; highly selective with elite business, nursing, and law pipeline",
    },
    "Xavier University": {
        "accept": 74, "satMedian": 1185, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Cincinnati, OH",
        "vibe": "Jesuit university in Cincinnati with strong business, pre-health, and a passionate Musketeer basketball culture",
    },
    # ── AMERICA EAST ──────────────────────────────────────────────────────────
    "Binghamton University": {
        "accept": 43, "satMedian": 1295, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Binghamton, NY",
        "vibe": "SUNY flagship known as the Public Ivy of New York; strong business, engineering, and nursing",
    },
    "Bryant University": {
        "accept": 71, "satMedian": 1200, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Smithfield, RI",
        "vibe": "Business-focused university near Providence with a strong actuarial science and data analytics track",
    },
    "Maine, University of": {
        "accept": 89, "satMedian": 1155, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Orono, ME",
        "vibe": "Maine's flagship land-grant with strong forestry, marine science, and engineering in the North Woods",
    },
    "New Hampshire, University of": {
        "accept": 82, "satMedian": 1200, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Durham, NH",
        "vibe": "New England's flagship with strong ocean engineering, environmental science, and a vibrant campus culture",
    },
    "New Jersey Institute of Techno": {
        "accept": 68, "satMedian": 1270, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Newark, NJ",
        "vibe": "NJIT — urban tech university with strong CS, architecture, and engineering 20 minutes from NYC",
    },
    "University of Maryland Baltimo": {
        "accept": 52, "satMedian": 1240, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Baltimore, MD",
        "vibe": "UMBC — ranked #1 for transforming undergraduate education; strong STEM and Meyerhoff Scholars program",
    },
    "Vermont, University of": {
        "accept": 63, "satMedian": 1245, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Burlington, VT",
        "vibe": "UVM — New England's outdoor university in Burlington; strong environmental, health, and agriculture",
    },
    "Virginia Military Institute": {
        "accept": 63, "satMedian": 1170, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Lexington, VA",
        "vibe": "VMI — America's oldest state military college; rigorous discipline, camaraderie, and leadership",
    },
    # ── ATLANTIC 10 ───────────────────────────────────────────────────────────
    "Davidson College": {
        "accept": 19, "satMedian": 1400, "sat25": 1350, "sat75": 1490, "gpaMean": 3.83, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Davidson, NC",
        "vibe": "Elite honor-code LAC near Charlotte; consistently tops national rankings and places students at top grad schools",
    },
    "Duquesne University": {
        "accept": 73, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Pittsburgh, PA",
        "vibe": "Spiritan Catholic university on a hilltop in Pittsburgh with strong pharmacy, business, and law pipeline",
    },
    "Fordham University": {
        "accept": 49, "satMedian": 1305, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "New York, NY",
        "vibe": "Jesuit university in the Bronx and Lincoln Center — Rose Hill campus meets the heart of Manhattan",
    },
    "George Mason University": {
        "accept": 87, "satMedian": 1235, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Fairfax, VA",
        "vibe": "Northern Virginia's flagship; tech corridor access, strong CS, policy, and economics near DC",
    },
    "George Washington University": {
        "accept": 43, "satMedian": 1365, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Washington, DC",
        "vibe": "Urban university two blocks from the White House; political science and international affairs culture",
    },
    "La Salle University": {
        "accept": 79, "satMedian": 1115, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Philadelphia, PA",
        "vibe": "De La Salle Brothers Catholic university in Philadelphia with strong nursing and business programs",
    },
    "Saint Louis University": {
        "accept": 65, "satMedian": 1265, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "St. Louis, MO",
        "vibe": "Jesuit university with a top aviation program and strong pre-health in the Gateway City",
    },
    "St Bonaventure University": {
        "accept": 79, "satMedian": 1140, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "St. Bonaventure, NY",
        "vibe": "Franciscan university in the Southern Tier with a nationally respected journalism program",
    },
    "University of Rhode Island": {
        "accept": 80, "satMedian": 1190, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Kingston, RI",
        "vibe": "URI — strong pharmacy, engineering, and ocean science programs in coastal Rhode Island",
    },
    "University of Richmond": {
        "accept": 28, "satMedian": 1380, "sat25": 1240, "sat75": 1420, "gpaMean": 3.79, "hiddenIvy": False, "stem": False,
        "merit": "none", "location": "Richmond, VA",
        "vibe": "Selective private university with a beautiful campus; strong business, law pipeline, and generous aid",
    },
    # ── CAA ───────────────────────────────────────────────────────────────────
    "Campbell University": {
        "accept": 61, "satMedian": 1160, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Buies Creek, NC",
        "vibe": "Baptist university in North Carolina with a highly regarded pharmacy and law school",
    },
    "Drexel University": {
        "accept": 77, "satMedian": 1295, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Philadelphia, PA",
        "vibe": "Co-op powerhouse in Philadelphia — Drexel students graduate with up to 18 months of real work experience",
    },
    "Monmouth University": {
        "accept": 85, "satMedian": 1130, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "West Long Branch, NJ",
        "vibe": "NJ shore university near NYC with a stunning campus and strong communications and business programs",
    },
    "Northeastern University": {
        "accept": 7, "satMedian": 1490, "sat25": 1440, "sat75": 1550, "gpaMean": 3.84, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Boston, MA",
        "vibe": "Co-op model taken to the extreme — Northeastern's network places students at the world's top employers",
    },
    "Stony Brook University": {
        "accept": 49, "satMedian": 1295, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Stony Brook, NY",
        "vibe": "SUNY's research flagship on Long Island; strong STEM and medical programs with a large international community",
    },
    "Towson University": {
        "accept": 84, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Towson, MD",
        "vibe": "Maryland's largest university by enrollment; strong education, health professions, and business programs",
    },
    "University of North Carolina W": {
        "accept": 73, "satMedian": 1185, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Wilmington, NC",
        "vibe": "UNCW — coastal NC campus with outstanding marine biology, film, and business programs",
    },
    "William and Mary": {
        "accept": 33, "satMedian": 1400, "sat25": 1280, "sat75": 1470, "gpaMean": 3.79, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Williamsburg, VA",
        "vibe": "America's second-oldest university and the 'Public Ivy' of Virginia; strong law, business, and history",
    },
    # ── ASUN ──────────────────────────────────────────────────────────────────
    "Bellarmine University": {
        "accept": 62, "satMedian": 1150, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Louisville, KY",
        "vibe": "Catholic LAC in Louisville with strong nursing, education, and a student-focused campus community",
    },
    "Delaware": {
        "accept": 75, "satMedian": 1210, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Newark, DE",
        "vibe": "UD — flagship with a top-ranked physical therapy program and strong business and engineering",
    },
    "Florida Atlantic University": {
        "accept": 64, "satMedian": 1150, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Boca Raton, FL",
        "vibe": "Sun Belt university in South Florida with growing research profile and strong engineering programs",
    },
    "Florida Gulf Coast University": {
        "accept": 78, "satMedian": 1130, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Fort Myers, FL",
        "vibe": "Young and growing Southwest Florida university with strong business and health sciences programs",
    },
    "Gardner-Webb University": {
        "accept": 75, "satMedian": 1100, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Boiling Springs, NC",
        "vibe": "Baptist-affiliated university in the North Carolina foothills with strong nursing and education",
    },
    "Georgia Southern University": {
        "accept": 81, "satMedian": 1140, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Statesboro, GA",
        "vibe": "Eagle Nation — growing Sun Belt university with strong business, IT, and health sciences programs",
    },
    "Old Dominion University": {
        "accept": 91, "satMedian": 1110, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Norfolk, VA",
        "vibe": "ODU — Hampton Roads research university with strong engineering, maritime, and health sciences",
    },
    "Queens University of Charlotte": {
        "accept": 70, "satMedian": 1150, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Charlotte, NC",
        "vibe": "Presbyterian-affiliated urban university in Charlotte with strong nursing and business programs",
    },
    "Univ North Carolina Asheville": {
        "accept": 79, "satMedian": 1165, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Asheville, NC",
        "vibe": "UNCA — liberal arts focus in one of America's most vibrant mountain cities; strong humanities",
    },
    "University of North Florida": {
        "accept": 75, "satMedian": 1165, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Jacksonville, FL",
        "vibe": "UNF — coastal Jacksonville campus with strong business, education, and health sciences programs",
    },
    # ── BIG WEST ──────────────────────────────────────────────────────────────
    "Cal State Bakersfield": {
        "accept": 71, "satMedian": 1085, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Bakersfield, CA",
        "vibe": "CSU campus in California's Central Valley with strong nursing, criminal justice, and business",
    },
    "Grand Canyon University": {
        "accept": 73, "satMedian": 1145, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Phoenix, AZ",
        "vibe": "Christian university in Phoenix with rapid growth, strong nursing, and a vibrant athletics culture",
    },
    "Seattle U": {
        "accept": 79, "satMedian": 1200, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Seattle, WA",
        "vibe": "Jesuit university on Capitol Hill with strong business, nursing, and law pipeline in a world-class city",
    },
    "UC Davis": {
        "accept": 39, "satMedian": 1270, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Davis, CA",
        "vibe": "UC Davis — world's top agriculture and veterinary school; bike-friendly campus near Sacramento",
    },
    "UC San Diego": {
        "accept": 24, "satMedian": 1370, "sat25": 1290, "sat75": 1450, "gpaMean": 3.9, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "La Jolla, CA",
        "vibe": "UCSD — elite research campus with top-3 global ranking in oceanography and strong STEM programs",
    },
    "UC Santa Barbara": {
        "accept": 26, "satMedian": 1330, "sat25": 1270, "sat75": 1440, "gpaMean": 3.83, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Santa Barbara, CA",
        "vibe": "UCSB — stunning ocean campus with Nobel-laureate faculty and a strong surf and research culture",
    },
    "University of Hawaii": {
        "accept": 61, "satMedian": 1140, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Honolulu, HI",
        "vibe": "UH Manoa — flagship in paradise; strong marine biology, Asian studies, and education programs",
    },
    "University of San Diego": {
        "accept": 49, "satMedian": 1250, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "San Diego, CA",
        "vibe": "Catholic university on a stunning mesa overlooking the Pacific with strong law and business programs",
    },
    # ── HORIZON LEAGUE ────────────────────────────────────────────────────────
    "Cleveland State University": {
        "accept": 72, "satMedian": 1160, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Cleveland, OH",
        "vibe": "Urban research university in downtown Cleveland with strong engineering, law, and health sciences",
    },
    "Green Bay Phoenix": {
        "accept": 85, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Green Bay, WI",
        "vibe": "UW-Green Bay — interdisciplinary focus and strong environmental science programs in Packer country",
    },
    "IU Indianapolis": {
        "accept": 80, "satMedian": 1155, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Indianapolis, IN",
        "vibe": "IU's urban campus in Indiana's capital; strong health sciences, business, and law programs",
    },
    "Northern Kentucky University": {
        "accept": 86, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Highland Heights, KY",
        "vibe": "Growing metro-Cincinnati university with strong business, nursing, and computer science programs",
    },
    "Oakland University": {
        "accept": 76, "satMedian": 1175, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Rochester, MI",
        "vibe": "Oakland — suburban Detroit university with strong engineering, business, and health sciences programs",
    },
    "University of Wisconsin-Milwau": {
        "accept": 85, "satMedian": 1170, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Milwaukee, WI",
        "vibe": "UWM — urban research campus in Milwaukee with strong engineering, architecture, and nursing programs",
    },
    "Youngstown State University": {
        "accept": 72, "satMedian": 1125, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Youngstown, OH",
        "vibe": "Regional university with strong engineering and health sciences serving the Mahoning Valley",
    },
    # ── GLIAC ─────────────────────────────────────────────────────────────────
    "Augustana University": {
        "accept": 72, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Sioux Falls, SD",
        "vibe": "Lutheran university in the Sioux Falls metro; strong business, education, and nursing programs",
    },
    "Davenport University": {
        "accept": 71, "satMedian": 1100, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Grand Rapids, MI",
        "vibe": "Career-focused university with strong cybersecurity, business, and healthcare administration",
    },
    "Grand Valley State University": {
        "accept": 82, "satMedian": 1150, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Allendale, MI",
        "vibe": "GVSU — one of Michigan's fastest-growing universities; strong education, nursing, and business",
    },
    "Lake Superior State University": {
        "accept": 75, "satMedian": 1100, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Sault Ste. Marie, MI",
        "vibe": "Small STEM-focused university on the Canada border with strong robotics and fisheries programs",
    },
    "Northern Michigan University": {
        "accept": 76, "satMedian": 1135, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Marquette, MI",
        "vibe": "NMU — outdoor recreation capital of the UP; strong nursing, education, and culinary programs",
    },
    "Saginaw Valley State Universit": {
        "accept": 82, "satMedian": 1090, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "University Center, MI",
        "vibe": "Regional Michigan university with strong nursing, education, and engineering technology programs",
    },
    "St Cloud State University (M)": {
        "accept": 79, "satMedian": 1130, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "St. Cloud, MN",
        "vibe": "SCSU — one of Minnesota's largest universities; strong aviation, engineering, and business programs",
    },
    "Wayne State University": {
        "accept": 75, "satMedian": 1155, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Detroit, MI",
        "vibe": "Urban research university in Detroit's Midtown; strong medicine, law, and engineering programs",
    },
    # ── MPSF ─────────────────────────────────────────────────────────────────
    "Cal Baptist University": {
        "accept": 77, "satMedian": 1130, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Riverside, CA",
        "vibe": "Christian university in the Inland Empire with a growing athletics program and strong nursing",
    },
    "Idaho, University of": {
        "accept": 80, "satMedian": 1175, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Moscow, ID",
        "vibe": "Idaho's flagship land-grant with strong forestry, engineering, and agriculture in a college-town setting",
    },
    "New Mexico State University": {
        "accept": 97, "satMedian": 1100, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Las Cruces, NM",
        "vibe": "NMSU — Aggie engineering and agriculture in the Mesilla Valley near the Rio Grande",
    },
    "Northern Arizona University": {
        "accept": 76, "satMedian": 1125, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Flagstaff, AZ",
        "vibe": "Ponderosa pine campus at 7,000 feet; strong nursing, education, and hotel management programs",
    },
    "Northern Colorado, University": {
        "accept": 80, "satMedian": 1140, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Greeley, CO",
        "vibe": "UNC Bears — strong education, health sciences, and performing arts in northern Colorado",
    },
    "Pepperdine University": {
        "accept": 36, "satMedian": 1340, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Malibu, CA",
        "vibe": "Christian university on a Pacific Ocean bluff in Malibu; strong law, business, and international programs",
    },
    "US Air Force Academy": {
        "accept": 11, "satMedian": 1340, "sat25": 1240, "sat75": 1410, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Colorado Springs, CO",
        "vibe": "Full scholarship service academy with rigorous STEM curriculum and a guaranteed Air Force career",
    },
    "Univ Texas Rio Grande Valley": {
        "accept": 100, "satMedian": 1025, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Edinburg, TX",
        "vibe": "UTRGV — open-access Hispanic-serving institution along the Rio Grande with strong STEM growth",
    },
    "University of Incarnate Word": {
        "accept": 82, "satMedian": 1090, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "San Antonio, TX",
        "vibe": "Catholic university in San Antonio with strong nursing, optometry, and pharmacy programs",
    },
    "University of the Pacific": {
        "accept": 67, "satMedian": 1215, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Stockton, CA",
        "vibe": "West Coast private with a top pharmacy school, conservatory of music, and accelerated dental program",
    },
    # ── SUMMIT LEAGUE ─────────────────────────────────────────────────────────
    "Eastern Illinois University": {
        "accept": 57, "satMedian": 1100, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Charleston, IL",
        "vibe": "Regional Illinois university with strong education, business, and athletic training programs",
    },
    "South Dakota State University": {
        "accept": 81, "satMedian": 1190, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Brookings, SD",
        "vibe": "SDSU — land-grant flagship with strong agriculture, engineering, and pharmacy programs",
    },
    "University of Denver": {
        "accept": 68, "satMedian": 1310, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Denver, CO",
        "vibe": "Private research university in the Mile High City with strong business, law, and international programs",
    },
    "University of Nebraska Omaha": {
        "accept": 80, "satMedian": 1165, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Omaha, NE",
        "vibe": "Urban NU campus in Omaha with strong IT, business, and criminal justice programs",
    },
    "University of South Dakota": {
        "accept": 87, "satMedian": 1175, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Vermillion, SD",
        "vibe": "South Dakota's flagship with a top law school, strong health sciences, and a close campus community",
    },
    "University of Southern Indiana": {
        "accept": 80, "satMedian": 1095, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Evansville, IN",
        "vibe": "Regional university in southwest Indiana with strong dental hygiene, nursing, and engineering tech",
    },
    "University of St. Thomas MN": {
        "accept": 77, "satMedian": 1230, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "St. Paul, MN",
        "vibe": "Catholic university in the Twin Cities; strong business, law, and education with a large alumni network",
    },
    # ── WAC ───────────────────────────────────────────────────────────────────
    "Air Force": {
        "accept": 11, "satMedian": 1340, "sat25": 1240, "sat75": 1410, "hiddenIvy": False, "stem": True,
        "merit": "none", "location": "Colorado Springs, CO",
        "vibe": "Full scholarship service academy with rigorous STEM curriculum and a guaranteed Air Force career",
    },
    "California Baptist": {
        "accept": 73, "satMedian": 1130, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Riverside, CA",
        "vibe": "Christian university in the Inland Empire with a growing athletics program and strong nursing",
    },
    "Grand Canyon": {
        "accept": 73, "satMedian": 1145, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Phoenix, AZ",
        "vibe": "Christian university in Phoenix with rapid growth, strong nursing, and a vibrant athletics culture",
    },
    "Idaho": {
        "accept": 80, "satMedian": 1175, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Moscow, ID",
        "vibe": "Idaho's flagship land-grant with strong forestry, engineering, and agriculture in a college-town setting",
    },
    "New Mexico State": {
        "accept": 97, "satMedian": 1100, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Las Cruces, NM",
        "vibe": "NMSU — Aggie engineering and agriculture in the Mesilla Valley near the Rio Grande",
    },
    "Northern Arizona": {
        "accept": 76, "satMedian": 1125, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Flagstaff, AZ",
        "vibe": "Ponderosa pine campus at 7,000 feet; strong nursing, education, and hotel management programs",
    },
    "Northern Colorado": {
        "accept": 80, "satMedian": 1140, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Greeley, CO",
        "vibe": "UNC Bears — strong education, health sciences, and performing arts in northern Colorado",
    },
    "Seattle": {
        "accept": 79, "satMedian": 1200, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Seattle, WA",
        "vibe": "Jesuit university on Capitol Hill with strong business, nursing, and law pipeline in a world-class city",
    },
    "University of Nevada Las Vegas": {
        "accept": 83, "satMedian": 1130, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Las Vegas, NV",
        "vibe": "UNLV — urban research university with top hospitality and hotel management programs in Las Vegas",
    },
    "University of Texas Rio Grande": {
        "accept": 100, "satMedian": 1025, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Edinburg, TX",
        "vibe": "UTRGV — open-access Hispanic-serving institution along the Rio Grande with strong STEM growth",
    },
    "University of Wyoming": {
        "accept": 96, "satMedian": 1155, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Laramie, WY",
        "vibe": "Wyoming's only four-year university; strong engineering, geology, and agriculture at 7,200 feet",
    },
    "Utah Tech University": {
        "accept": 100, "satMedian": 1080, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "St. George, UT",
        "vibe": "Open-access university in Utah's red rock country with strong healthcare, business, and technology",
    },
    # ── PCSC ─────────────────────────────────────────────────────────────────
    "Arizona Christian University": {
        "accept": 44, "satMedian": 1085, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Phoenix, AZ",
        "vibe": "Christian liberal arts in metro Phoenix; small and student-focused with strong ministry programs",
    },
    "Azusa Pacific University": {
        "accept": 51, "satMedian": 1155, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Azusa, CA",
        "vibe": "Evangelical Christian university in the San Gabriel Valley with strong nursing and music programs",
    },
    "Biola University": {
        "accept": 60, "satMedian": 1200, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "La Mirada, CA",
        "vibe": "Evangelical university near LA with a strong biblical studies core and excellent nursing program",
    },
    "California State University, E": {
        "accept": 72, "satMedian": 1055, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Hayward, CA",
        "vibe": "Cal State East Bay — diverse urban campus in the East Bay with strong business and nursing programs",
    },
    "College of Idaho": {
        "accept": 90, "satMedian": 1150, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Caldwell, ID",
        "vibe": "Small Idaho LAC with generous merit aid and a close-knit campus community near Boise",
    },
    "Concordia University Irvine": {
        "accept": 73, "satMedian": 1130, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Irvine, CA",
        "vibe": "Lutheran university in Orange County; strong business and education in a Southern California setting",
    },
    "Fresno Pacific University": {
        "accept": 49, "satMedian": 1075, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Fresno, CA",
        "vibe": "Mennonite Christian university in the Central Valley with strong teacher education programs",
    },
    "Ottawa University of Arizona": {
        "accept": 82, "satMedian": 1050, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Surprise, AZ",
        "vibe": "Baptist-affiliated university in the Phoenix metro with small class sizes and career-focused programs",
    },
    "Simpson University": {
        "accept": 53, "satMedian": 1070, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Redding, CA",
        "vibe": "Christian LAC in Northern California with strong biblical studies and teacher education programs",
    },
    "Soka University": {
        "accept": 36, "satMedian": 1240, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Aliso Viejo, CA",
        "vibe": "Unique Buddhist-inspired liberal arts university with a strong global citizenship mission and full scholarships",
    },
    "The Master's University": {
        "accept": 46, "satMedian": 1190, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Santa Clarita, CA",
        "vibe": "Conservative Christian university in the Santa Clarita Valley with a strong biblical studies emphasis",
    },
    "University of Alaska Fairbanks": {
        "accept": 77, "satMedian": 1105, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Fairbanks, AK",
        "vibe": "UAF — world-class arctic research with strong engineering, geophysics, and wildlife biology programs",
    },
    "University of California, Sant": {
        "accept": 47, "satMedian": 1240, "hiddenIvy": False, "stem": True,
        "merit": "moderate", "location": "Santa Cruz, CA",
        "vibe": "UC Santa Cruz — redwood campus culture with strong marine science, CS, and social justice programs",
    },
    "Westmont College": {
        "accept": 71, "satMedian": 1290, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Santa Barbara, CA",
        "vibe": "Selective Christian LAC in the Santa Barbara hills; strong academics with evangelical community values",
    },
    # ── PSAC ─────────────────────────────────────────────────────────────────
    "Bloomsburg University": {
        "accept": 77, "satMedian": 1080, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Bloomsburg, PA",
        "vibe": "PASSHE university in the Susquehanna Valley with strong nursing, education, and business programs",
    },
    "East Stroudsburg University": {
        "accept": 79, "satMedian": 1080, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "East Stroudsburg, PA",
        "vibe": "Poconos campus with strong health, physical education, and tourism and hospitality programs",
    },
    "Gannon University": {
        "accept": 80, "satMedian": 1150, "hiddenIvy": False, "stem": True,
        "merit": "high", "location": "Erie, PA",
        "vibe": "Catholic university in Erie with strong engineering, PA studies, and health sciences programs",
    },
    "Indiana University of PA": {
        "accept": 72, "satMedian": 1085, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Indiana, PA",
        "vibe": "IUP — western PA regional university with strong education, criminology, and culinary programs",
    },
    "Kutztown University": {
        "accept": 78, "satMedian": 1075, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Kutztown, PA",
        "vibe": "PASSHE campus in Pennsylvania Dutch Country with strong education, art, and communication programs",
    },
    "Lock Haven University": {
        "accept": 82, "satMedian": 1050, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Lock Haven, PA",
        "vibe": "Small river-town Pennsylvania campus with strong health science, education, and outdoor recreation",
    },
    "Millersville University": {
        "accept": 75, "satMedian": 1095, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Millersville, PA",
        "vibe": "Lancaster County campus with strong education, meteorology, and social work programs",
    },
    "PennWest Edinboro University": {
        "accept": 84, "satMedian": 1050, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Edinboro, PA",
        "vibe": "Northwest Pennsylvania campus known for strong art, education, and social work programs",
    },
    "PennWest University Clarion": {
        "accept": 82, "satMedian": 1050, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Clarion, PA",
        "vibe": "Rural western Pennsylvania campus with strong communication, education, and business programs",
    },
    "PennWest University,California": {
        "accept": 81, "satMedian": 1050, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "California, PA",
        "vibe": "PASSHE campus along the Monongahela with strong education, sport management, and criminal justice",
    },
    "Shippensburg University of PA": {
        "accept": 81, "satMedian": 1075, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "Shippensburg, PA",
        "vibe": "South-central PA campus with strong business, public administration, and criminal justice programs",
    },
    "West Chester University": {
        "accept": 62, "satMedian": 1165, "hiddenIvy": False, "stem": False,
        "merit": "moderate", "location": "West Chester, PA",
        "vibe": "Philadelphia suburb campus with strong music, education, nursing, and a large and active student body",
    },
    # ── SAC ───────────────────────────────────────────────────────────────────
    "Carson-Newman University": {
        "accept": 73, "satMedian": 1120, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Jefferson City, TN",
        "vibe": "Baptist university in the East Tennessee hills with strong nursing, education, and athletics programs",
    },
    "Catawba College": {
        "accept": 53, "satMedian": 1080, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Salisbury, NC",
        "vibe": "Reformed Church-affiliated LAC in the Carolina Piedmont with strong business and education programs",
    },
    "Lenoir-Rhyne University": {
        "accept": 67, "satMedian": 1115, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Hickory, NC",
        "vibe": "Lutheran university in the North Carolina foothills with strong nursing, business, and education",
    },
    "Mars Hill University": {
        "accept": 62, "satMedian": 1075, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Mars Hill, NC",
        "vibe": "Baptist-affiliated mountain university near Asheville with strong education and criminal justice",
    },
    "Wingate University": {
        "accept": 65, "satMedian": 1110, "hiddenIvy": False, "stem": False,
        "merit": "high", "location": "Wingate, NC",
        "vibe": "Baptist-affiliated university near Charlotte with strong pharmacy, business, and education programs",
    },
    # ── COLORADO COLLEGE (lone remaining school) ──────────────────────────────
    "Bryn Mawr College Owls": {
        "accept": 34, "satMedian": 1385, "hiddenIvy": True, "stem": False,
        "merit": "none", "location": "Bryn Mawr, PA",
        "vibe": "Elite Seven Sisters college near Philadelphia; rigorous academics and a powerful women's leadership network",
    },
}

# ---------------------------------------------------------------------------
# Runtime data stores
# ---------------------------------------------------------------------------
# ── PRIMARY UNIVERSE (source of truth) ──────────────────────────────────────
# EXPLORE_SCHOOLS is the canonical 324-school swimming universe.
# Built at startup from output/lane4_snapshot_compatible.csv.
# Every endpoint that needs a school list starts here — never from TEAMS_LIST.
EXPLORE_SCHOOLS = []   # [{school, conference, conf_tier_short, meta, hasSwimData, …}]

# Pre-computed lat/lng for all 324 schools (generated by generate_school_locations.py).
# Never geocode live — always serve from this dict.
_school_locs_path = os.path.join(os.path.dirname(__file__), 'output', 'school_locations.json')
SCHOOL_LOCATIONS = {}
if os.path.exists(_school_locs_path):
    with open(_school_locs_path, encoding='utf-8') as _f:
        SCHOOL_LOCATIONS = json.load(_f)

# ── ENRICHMENT SOURCES (read-only, merged into EXPLORE_SCHOOLS at startup) ──
# all_event_anchors.csv is the canonical benchmark source.
# Legacy Excel benchmark loading has been retired.
# If benchmark gaps exist, they must be fixed in the master CSV — not patched at runtime.
CSV_BENCH_PATH = os.path.join(os.path.dirname(__file__), 'output', 'all_event_anchors.csv')
CSV_SNAP_PATH  = os.path.join(os.path.dirname(__file__), 'output', 'lane4_snapshot_compatible.csv')
BENCHMARKS = {}    # "Conference|Event" -> {first, eighth, sixteenth, sec_per_place}
TEAMS = {}         # "Conference|School" -> enrichment record (PSF, tier, finish)
TEAMS_LIST = []    # flat list of TEAMS records — enrichment only, not the universe
CONFERENCES = {}   # conference name -> sorted list of canonical school names
NORMALIZATION_LOG = []  # records every name-normalization applied during load

# ── Out-of-universe well-known schools ───────────────────────────────────────
# Metadata for commonly searched schools outside our scored pool.
# Keys are the canonical display names (title-cased as users would type them).
# Fields match SCHOOL_META conventions; ivyLeague=True adds the Ivy badge.
OOU_SCHOOL_META = {
    # Ivy League
    "Harvard":      {"accept": 4,  "satMedian": 1580, "ivyLeague": True,  "merit": "none",     "location": "Cambridge, MA",     "vibe": "The most storied name in higher education — ultra-selective by any measure."},
    "Yale":         {"accept": 5,  "satMedian": 1570, "ivyLeague": True,  "merit": "none",     "location": "New Haven, CT",      "vibe": "World-class academics, drama, and debate — near-impossible odds."},
    "Princeton":    {"accept": 4,  "satMedian": 1580, "ivyLeague": True,  "merit": "none",     "location": "Princeton, NJ",      "vibe": "Legendary campus, no-loan financial aid, and 4% acceptance."},
    "Columbia":     {"accept": 4,  "satMedian": 1560, "ivyLeague": True,  "merit": "none",     "location": "New York, NY",       "vibe": "Ivy in the heart of Manhattan — urban energy meets academic prestige."},
    "Brown":        {"accept": 6,  "satMedian": 1555, "ivyLeague": True,  "merit": "none",     "location": "Providence, RI",     "vibe": "Open Curriculum gives students unusual freedom to design their education."},
    "Dartmouth":    {"accept": 6,  "satMedian": 1560, "ivyLeague": True,  "merit": "none",     "location": "Hanover, NH",        "vibe": "Tight-knit Ivy with a strong outdoors culture and fierce alumni loyalty."},
    "Cornell":      {"accept": 9,  "satMedian": 1510, "ivyLeague": True,  "merit": "none",     "location": "Ithaca, NY",         "vibe": "The most accessible Ivy — broad programs, engineering powerhouse."},
    "Penn":         {"accept": 7,  "satMedian": 1535, "ivyLeague": True,  "merit": "none",     "location": "Philadelphia, PA",   "vibe": "Wharton business, Penn Medicine, and strong interdisciplinary programs."},
    "UPenn":        {"accept": 7,  "satMedian": 1535, "ivyLeague": True,  "merit": "none",     "location": "Philadelphia, PA",   "vibe": "Wharton business, Penn Medicine, and strong interdisciplinary programs."},
    # Elite non-Ivy
    "MIT":          {"accept": 4,  "satMedian": 1580, "stem": True,       "merit": "none",     "location": "Cambridge, MA",      "vibe": "Global STEM leader — demanding, collaborative, and transformative."},
    "Stanford":     {"accept": 4,  "satMedian": 1560, "stem": True,       "merit": "none",     "location": "Stanford, CA",       "vibe": "Silicon Valley's university — entrepreneurship and research at scale."},
    "Duke":         {"accept": 6,  "satMedian": 1540, "merit": "none",    "location": "Durham, NC",       "vibe": "Elite academics meets ACC athletics in a beautiful residential campus."},
    "Northwestern": {"accept": 7,  "satMedian": 1530, "merit": "none",    "location": "Evanston, IL",     "vibe": "Quarter system, Big Ten athletics, and one of the strongest journalism schools."},
    "Georgetown":   {"accept": 12, "satMedian": 1470, "merit": "none",    "location": "Washington, DC",   "vibe": "Jesuit traditions, global affairs, and unmatched access to DC institutions."},
    "Notre Dame":   {"accept": 13, "satMedian": 1480, "merit": "none",    "location": "Notre Dame, IN",   "vibe": "Catholic identity, storied football, and a fiercely loyal alumni network."},
    "Vanderbilt":   {"accept": 7,  "satMedian": 1540, "merit": "moderate","location": "Nashville, TN",    "vibe": "Southern hospitality meets elite academics in a vibrant music city."},
    "Emory":        {"accept": 11, "satMedian": 1480, "hiddenIvy": True,  "merit": "moderate", "location": "Atlanta, GA",        "vibe": "Hidden Ivy with top pre-med programs and CDC proximity."},
    "Tufts":        {"accept": 11, "satMedian": 1490, "hiddenIvy": True,  "merit": "none",     "location": "Medford, MA",        "vibe": "Hidden Ivy bridging liberal arts and research, just outside Boston."},
    "Wake Forest":  {"accept": 21, "satMedian": 1400, "sat25": 1290, "sat75": 1480, "gpaMean": 3.82, "merit": "moderate","location": "Winston-Salem, NC","vibe": "Small research university with a pro-human motto and strong business school."},
    "Boston College": {"accept": 15,"satMedian": 1430,"sat25": 1360,"sat75": 1520,"gpaMean": 3.87,"merit": "none",    "location": "Chestnut Hill, MA","vibe": "Jesuit institution with strong business, nursing, and law programs."},
    "Boston University": {"accept": 19,"satMedian": 1400,"sat25": 1350,"sat75": 1500,"gpaMean": 3.73,"merit": "moderate","location": "Boston, MA",   "vibe": "Large urban research university with 300+ programs along the Charles River."},
    "Northeastern": {"accept": 7,  "satMedian": 1490,"sat25": 1440,"sat75": 1550,"gpaMean": 3.84,"stem": True,       "merit": "moderate", "location": "Boston, MA",         "vibe": "Co-op powerhouse — students graduate with up to two years of work experience."},
    "Rice":         {"accept": 9,  "satMedian": 1540, "sat25": 1490, "sat75": 1570, "gpaMean": 3.92, "stem": True,       "merit": "none",     "location": "Houston, TX",        "vibe": "Tiny but mighty — residential college system and elite engineering."},
    "University of Chicago": {"accept": 7,"satMedian": 1560,"hiddenIvy": True,"merit": "none","location": "Chicago, IL",         "vibe": "Rigorously intellectual — the life of the mind in the heart of Chicago."},
    "UChicago":     {"accept": 7,  "satMedian": 1560, "hiddenIvy": True,  "merit": "none",     "location": "Chicago, IL",        "vibe": "Rigorously intellectual — the life of the mind in the heart of Chicago."},
    "Carnegie Mellon": {"accept": 11,"satMedian": 1530,"stem": True,      "merit": "none",     "location": "Pittsburgh, PA",     "vibe": "CS and engineering titan with one of the world's top drama programs."},
    "WashU":        {"accept": 12, "satMedian": 1530, "hiddenIvy": True,  "merit": "moderate", "location": "St. Louis, MO",      "vibe": "Washington University — Hidden Ivy with generous merit aid and strong med school."},
    "Washington University": {"accept": 12,"satMedian": 1530,"hiddenIvy": True,"merit": "moderate","location": "St. Louis, MO",  "vibe": "Hidden Ivy with generous merit aid and a strong medical school."},
    "Bowdoin":      {"accept": 9,  "satMedian": 1490, "hiddenIvy": True,  "merit": "none",     "location": "Brunswick, ME",      "vibe": "Stunning Maine campus, need-blind admissions, and a powerhouse swim tradition."},
    "Middlebury":   {"accept": 13, "satMedian": 1430, "hiddenIvy": True,  "merit": "none",     "location": "Middlebury, VT",     "vibe": "World-renowned language programs and a stunning Vermont campus."},
    "Colby":        {"accept": 10, "satMedian": 1420, "hiddenIvy": True,  "merit": "none",     "location": "Waterville, ME",     "vibe": "NESCAC liberal arts with need-blind admissions and strong athletics."},
    "Colgate":      {"accept": 21, "satMedian": 1400, "hiddenIvy": True,  "merit": "none",     "location": "Hamilton, NY",       "vibe": "Strong academics in a tight-knit upstate NY community."},
}

# Case-insensitive lookup helper
def _oou_lookup(name: str) -> dict | None:
    name_l = name.lower().strip()
    for k, v in OOU_SCHOOL_META.items():
        if k.lower() == name_l:
            return v
    # partial: e.g. "Harvard University" → "Harvard"
    for k, v in OOU_SCHOOL_META.items():
        if k.lower() in name_l or name_l in k.lower():
            return v
    return None

# ── 2026 snapshot tier enrichment ────────────────────────────────────────────
# Abbreviated Excel names → full snapshot names (UAA uses short names in the Excel)
_UAA_SHORT = {
    'emory':          'emory university',
    'nyu':            'new york university',
    'chicago':        'university of chicago',
    'washingtonmo':   'washington university st louis',
    'carnegiemellon': 'carnegie mellon university',
    'casewestern':    'case western reserve universit',
    'rochester':      'university of rochester',
    'brandeis':       'brandeis university',
}

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


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

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

ALL_EVENTS = [
    '50 Free', '100 Free', '200 Free', '500 Free',
    '1000 Free', '1650 Free',
    '100 Back', '200 Back',
    '100 Breast', '200 Breast',
    '100 Fly', '200 Fly',
    '200 IM', '400 IM',
    '50 Breast (Relay Split)',
]

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

# ── Swim layer ──────────────────────────────────────────────────────────────

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

_ADM_LABEL_SCORE: dict = {
    'Very Strong Chance':       100,
    'Strong Chance':             80,
    'Realistic Shot':            60,
    'Possible':                  40,
    'Major Reach':               15,
    'Moonshot':                   5,
    'Moonshot — Apply for Fun':   2,
    'Unknown':                   25,
}




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


_CAND_STOP = frozenset({
    'university', 'college', 'institute', 'school', 'of', 'the', 'at',
    'and', 'in', 'a', 'tech', 'for',
})


def _cname_norm(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', '', s.lower())).strip()


def _cname_toks(s: str) -> frozenset:
    return frozenset(t for t in _cname_norm(s).split()
                     if t not in _CAND_STOP and len(t) > 1)


# Full names Claude commonly returns → canonical short names used in our universe.
# Required because the universe stores these as acronyms/short-forms that the
# three-tier fuzzy matcher cannot bridge from the full official name.
_UNIVERSE_ALIASES: dict[str, str] = {
    'massachusetts institute of technology': 'mit',
    'california institute of technology':    'caltech',
    'new york university':                   'nyu',
    'university of chicago':                 'chicago',
    'emory university':                      'emory',
    'virginia tech':                         'va tech',
    'virginia polytechnic institute':        'va tech',
    'virginia polytechnic institute and state university': 'va tech',
    'university of idaho':                   'idaho',
    'seattle university':                    'seattle',
}


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


# ── School-entity search: acronym/nickname alias table ────────────────────────
# Maps _qnorm(query) → canonical school name(s) stored in the universe.
# ONLY genuine acronyms and nickname contractions that text-surface matching
# fundamentally cannot resolve (pure initials, stored abbreviations, etc.).
# City/substring/prefix-token/difflib passes handle everything else.
_ACRONYM_ALIASES: dict = {
    # UC system — acronyms not deducible from stored canonical names
    'ucsb':              'UC Santa Barbara',
    'ucsd':              'UC San Diego',
    'ucla':              'University of California, Los',
    'ucb':               'California, University of, Ber',
    'uc berkeley':       'California, University of, Ber',
    'ucd':               'UC Davis',
    'uc davis':          'UC Davis',
    # Common school initials (city alone can't bridge these)
    'cmu':               'Carnegie Mellon',
    'jhu':               'Johns Hopkins University',
    'cwru':              'Case Western',
    'wpi':               'Worcester Polytechnic Institute',
    'rpi':               'Rensselaer Polytechnic Institute',
    'rit':               'Rochester Institute of Technology',
    'njit':              'New Jersey Institute of Techno',
    'mit':               'MIT',
    'nyu':               'NYU',
    'gwu':               'George Washington University',
    # Nickname contractions
    'washu':             'Washington (Mo)',
    'wustl':             'Washington (Mo)',
    'wash u':            'Washington (Mo)',
    # Stored-abbreviation mismatches (canonical in universe is a short form)
    'virginia tech':     'VA Tech',
    'vt':                'VA Tech',
    'nc state':          'North Carolina State Universit',
    'ncsu':              'North Carolina State Universit',
    'georgia tech':      'Georgia Institute of Technolog',
    'gatech':            'Georgia Institute of Technolog',
    'gt':                'Georgia Institute of Technolog',
    'odu':               'Old Dominion University',
    'gmu':               'George Mason University',
    'uva':               'Virginia, University of',
    'slu':               'Saint Louis University',
    'unc':               'North Carolina, University of',
    'hws':               'Hobart and William Smith',
    'byu':               'Brigham Young University',
    'lssu':              'Lake Superior State University',
    # Ambiguous multi-candidate entries (explicitly known)
    'rochester':         ['Rochester', 'Rochester Institute of Technology'],
    'augustana':         ['Augustana College', 'Augustana University'],
    'wheaton':           ['Wheaton College', 'Wheaton College (MA)'],
    'idaho':             ['Idaho', 'Idaho, University of', 'College of Idaho'],
    'grand canyon':      ['Grand Canyon', 'Grand Canyon University'],
}

# ── US state abbreviation → full name (for city/state surface matching) ───────
_US_STATES: dict[str, str] = {
    'AL': 'Alabama',        'AK': 'Alaska',         'AZ': 'Arizona',
    'AR': 'Arkansas',       'CA': 'California',      'CO': 'Colorado',
    'CT': 'Connecticut',    'DE': 'Delaware',        'DC': 'District of Columbia',
    'FL': 'Florida',        'GA': 'Georgia',         'HI': 'Hawaii',
    'ID': 'Idaho',          'IL': 'Illinois',        'IN': 'Indiana',
    'IA': 'Iowa',           'KS': 'Kansas',          'KY': 'Kentucky',
    'LA': 'Louisiana',      'ME': 'Maine',           'MD': 'Maryland',
    'MA': 'Massachusetts',  'MI': 'Michigan',        'MN': 'Minnesota',
    'MS': 'Mississippi',    'MO': 'Missouri',        'MT': 'Montana',
    'NE': 'Nebraska',       'NV': 'Nevada',          'NH': 'New Hampshire',
    'NJ': 'New Jersey',     'NM': 'New Mexico',      'NY': 'New York',
    'NC': 'North Carolina', 'ND': 'North Dakota',    'OH': 'Ohio',
    'OK': 'Oklahoma',       'OR': 'Oregon',          'PA': 'Pennsylvania',
    'RI': 'Rhode Island',   'SC': 'South Carolina',  'SD': 'South Dakota',
    'TN': 'Tennessee',      'TX': 'Texas',           'UT': 'Utah',
    'VT': 'Vermont',        'VA': 'Virginia',        'WA': 'Washington',
    'WV': 'West Virginia',  'WI': 'Wisconsin',       'WY': 'Wyoming',
}


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

@app.route('/')
def index():
    resp = send_from_directory('static', 'index.html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

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

_CANDIDATES_PATH    = os.path.join('static', 'candidates_manifest.json')
_CURATED_PATH       = os.path.join('static', 'curated_manifest.json')
_BLOCKLIST_PATH     = os.path.join('static', 'image_blocklist.json')
_SCHOOL_IMAGES_PATH = os.path.join('static', 'school_images.json')


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
        new_cands = fetch_candidates_for_category(school, category)
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
        # Fresh fetch for just this category
        new_cands = fetch_candidates_for_category(school, category)
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
            new_candidates = fetch_candidates_for_category(school, category)
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
