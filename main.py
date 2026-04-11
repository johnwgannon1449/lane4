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
from routes.scoring_routes import scoring_routes_bp
from routes.user_admin import user_admin_bp
from routes.legacy_admin import legacy_admin_bp
from routes.search import search_bp
from routes.deep_dive import deep_dive_bp


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
app.register_blueprint(scoring_routes_bp)
app.register_blueprint(user_admin_bp)
app.register_blueprint(legacy_admin_bp)
app.register_blueprint(search_bp)
app.register_blueprint(deep_dive_bp)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

