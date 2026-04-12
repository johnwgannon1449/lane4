"""routes/data_sync.py — User data sync endpoints (load/save to Postgres)."""
import json

from flask import Blueprint, request, jsonify, session
from db import get_db, get_dict_cursor, using_sqlite
from auth import login_required

data_sync_bp = Blueprint('data_sync', __name__)

_ALLOWED_KEYS = {'swimmer', 'my_list', 'crm_data', 'vibe_state', 'other_prefs', 'preferences'}


@data_sync_bp.route('/api/data/load', methods=['GET'])
@login_required
def data_load():
    user_id = session['user_id']
    try:
        with get_db() as conn:
            with get_dict_cursor(conn) as cur:
                sql = (
                    'SELECT data_key, data_value FROM sync_data WHERE user_id = ?'
                    if using_sqlite()
                    else 'SELECT data_key, data_value FROM sync_data WHERE user_id = %s'
                )
                cur.execute(sql, (user_id,))
                rows = cur.fetchall()
        result = {}
        for row in rows:
            value = row['data_value']
            if using_sqlite() and isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            result[row['data_key']] = value
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
            if using_sqlite():
                with conn:
                    for key, value in body.items():
                        if key not in _ALLOWED_KEYS:
                            continue
                        conn.execute(
                            '''INSERT INTO sync_data (user_id, data_key, data_value, updated_at)
                               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                               ON CONFLICT(user_id, data_key)
                               DO UPDATE SET data_value = excluded.data_value, updated_at = CURRENT_TIMESTAMP''',
                            (user_id, key, json.dumps(value))
                        )
            else:
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
