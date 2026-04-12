"""routes/utility.py — Health, debug, and admin-reset utility routes."""
import os
from urllib.parse import urlparse
from werkzeug.security import generate_password_hash
from flask import Blueprint, jsonify, send_from_directory
from state import EXPLORE_SCHOOLS, BENCHMARKS, TEAMS_LIST, NORMALIZATION_LOG
from models.school_data import SCHOOL_META
from db import get_db, using_sqlite

utility_bp = Blueprint('utility', __name__)


@utility_bp.route('/api/health', methods=['GET'])
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


@utility_bp.route('/snapshot', methods=['GET'])
def download_snapshot():
    """Serve the latest Lane4 team-tier snapshot CSV for download."""
    return send_from_directory('output', 'lane4_snapshot.csv', as_attachment=True)


@utility_bp.route('/api/resetadmin-lane4-2026')
def api_reset_admin():
    new_hash = generate_password_hash('4Freediver')
    results = {}
    try:
        with get_db() as conn:
            if using_sqlite():
                with conn:
                    cur = conn.execute(
                        "UPDATE admins SET password_hash = ? WHERE email = 'johngannon@pacesupply.com'",
                        (new_hash,)
                    )
                    results['admin_rows'] = cur.rowcount
                    cur = conn.execute(
                        """INSERT INTO users (email, password_hash)
                           VALUES ('johngannon@pacesupply.com', ?)
                           ON CONFLICT(email) DO UPDATE SET password_hash = excluded.password_hash""",
                        (new_hash,)
                    )
                    results['user_rows'] = cur.rowcount
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE admins SET password_hash = %s WHERE email = 'johngannon@pacesupply.com'",
                        (new_hash,)
                    )
                    results['admin_rows'] = cur.rowcount
                    cur.execute(
                        """INSERT INTO users (email, password_hash)
                           VALUES ('johngannon@pacesupply.com', %s)
                           ON CONFLICT (email) DO UPDATE SET password_hash = EXCLUDED.password_hash""",
                        (new_hash,)
                    )
                    results['user_rows'] = cur.rowcount
        results['ok'] = True
    except Exception as e:
        results['error'] = str(e)
    return jsonify(results)


@utility_bp.route('/api/dbcheck')
def api_dbcheck():
    url = os.environ.get('DATABASE_URL', '')
    p = urlparse(url)
    info = {'host': p.hostname, 'db': p.path.lstrip('/') or 'lane4_local.db'}
    try:
        with get_db() as conn:
            if using_sqlite():
                cur = conn.execute("SELECT COUNT(*) FROM users")
                info['user_count'] = cur.fetchone()[0]
                cur = conn.execute("SELECT COUNT(*) FROM admins")
                info['admin_count'] = cur.fetchone()[0]
                cur = conn.execute(
                    "SELECT substr(password_hash,1,20) FROM users WHERE email='johngannon@pacesupply.com'"
                )
                row = cur.fetchone()
                info['user_hash_prefix'] = row[0] if row else None
            else:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM users")
                    info['user_count'] = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM admins")
                    info['admin_count'] = cur.fetchone()[0]
                    cur.execute(
                        "SELECT LEFT(password_hash,20) FROM users WHERE email='johngannon@pacesupply.com'"
                    )
                    row = cur.fetchone()
                    info['user_hash_prefix'] = row[0] if row else None
    except Exception as e:
        info['error'] = str(e)
    return jsonify(info)
