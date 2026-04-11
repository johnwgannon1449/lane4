"""routes/data_sync.py — User data sync endpoints (load/save to Postgres)."""
import json

try:
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

from flask import Blueprint, request, jsonify, session
from db import get_db
from auth import login_required

data_sync_bp = Blueprint('data_sync', __name__)

_ALLOWED_KEYS = {'swimmer', 'my_list', 'crm_data', 'vibe_state', 'other_prefs', 'preferences'}


@data_sync_bp.route('/api/data/load', methods=['GET'])
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


@data_sync_bp.route('/api/data/save', methods=['POST'])
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
