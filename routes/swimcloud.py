"""routes/swimcloud.py — SwimCloud integration routes."""
import json
import datetime

try:
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

from flask import Blueprint, request, jsonify, session
from db import get_db, get_dict_cursor, using_sqlite
from auth import login_required

swimcloud_bp = Blueprint('swimcloud', __name__)


def _sc_load_swimmer_record(user_id: int) -> dict:
    """Load the user's saved 'swimmer' JSON from sync_data. Returns {} if missing."""
    try:
        with get_db() as conn:
            with get_dict_cursor(conn) as cur:
                sql = (
                    "SELECT data_value FROM sync_data WHERE user_id = ? AND data_key = 'swimmer'"
                    if using_sqlite()
                    else "SELECT data_value FROM sync_data WHERE user_id = %s AND data_key = 'swimmer'"
                )
                cur.execute(
                    sql,
                    (user_id,)
                )
                row = cur.fetchone()
        if row and row['data_value']:
            val = row['data_value']
            return val if isinstance(val, dict) else json.loads(val)
    except Exception as e:
        print(f'[swimcloud] Error loading swimmer record: {e}')
    return {}


@swimcloud_bp.route('/api/public/swimcloud/search', methods=['GET'])
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


@swimcloud_bp.route('/api/public/swimcloud/propose', methods=['GET'])
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


@swimcloud_bp.route('/api/swimcloud/search', methods=['GET'])
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


@swimcloud_bp.route('/api/swimcloud/propose', methods=['GET'])
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


@swimcloud_bp.route('/api/swimcloud/check-prs', methods=['GET'])
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
