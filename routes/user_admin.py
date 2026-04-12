"""routes/user_admin.py — User-facing admin panel (/admin and /api/ua/*)."""
import json
import time

try:
    import psycopg2.extras
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

from flask import Blueprint, request, jsonify, session, send_from_directory
from auth import user_admin_required
from db import get_db, get_dict_cursor, using_sqlite
from state import EXPLORE_SCHOOLS
from models.school_data import SCHOOL_META
from admin.image_curation import (
    _CANDIDATES_PATH,
    _load_candidates_manifest, _load_curated_manifest, _save_curated_manifest,
    _push_curated_to_school_images,
    _load_blocklist, _save_blocklist, _load_school_images,
)

user_admin_bp = Blueprint('user_admin', __name__)


def _school_city(school_name: str) -> str | None:
    """Return the city portion of a school's location from SCHOOL_META, if known."""
    meta = SCHOOL_META.get(school_name, {})
    loc  = meta.get('location', '') or ''
    if loc:
        city = loc.split(',')[0].strip()
        return city if city else None
    return None


@user_admin_bp.route('/admin')
@user_admin_required
def user_admin_page():
    return send_from_directory('static', 'admin.html')


@user_admin_bp.route('/api/ua/schools', methods=['GET'])
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


@user_admin_bp.route('/api/ua/candidates/<path:school>', methods=['GET'])
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


@user_admin_bp.route('/api/ua/fetch-candidates', methods=['POST'])
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


@user_admin_bp.route('/api/ua/fetch-more', methods=['POST'])
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


@user_admin_bp.route('/api/ua/redo-section', methods=['POST'])
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


@user_admin_bp.route('/api/ua/approve', methods=['POST'])
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


@user_admin_bp.route('/api/ua/ban', methods=['POST'])
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


@user_admin_bp.route('/api/ua/admins', methods=['GET'])
@user_admin_required
def ua_list_admins():
    """Return all admin records for the admin management UI."""
    try:
        with get_db() as conn:
            with get_dict_cursor(conn) as cur:
                sql = (
                    """SELECT email, active, created_by, created_at
                        FROM admins
                        ORDER BY created_at"""
                    if using_sqlite()
                    else
                    """SELECT email, active, created_by,
                              TO_CHAR(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI') AS created_at
                       FROM admins
                       ORDER BY created_at"""
                )
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchall()]
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@user_admin_bp.route('/api/ua/admins/create', methods=['POST'])
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
            if using_sqlite():
                with conn:
                    conn.execute("""
                        INSERT INTO admins (email, active, created_by)
                        VALUES (?, 1, ?)
                        ON CONFLICT(email) DO UPDATE SET active = 1
                    """, (email, created_by))
                return jsonify({'ok': True, 'email': email})
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
