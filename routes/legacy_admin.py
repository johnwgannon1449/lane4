"""routes/legacy_admin.py — Password-based legacy admin panel (/admin/login, /api/admin/*)."""
import os
import json
import time
import threading

try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.errors
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

from werkzeug.security import generate_password_hash, check_password_hash
from flask import Blueprint, request, jsonify, session, redirect, send_from_directory
from auth import admin_required
from db import get_db, using_sqlite
from state import EXPLORE_SCHOOLS
from models.school_data import SCHOOL_META
from admin.image_curation import (
    _CANDIDATES_PATH,
    _load_candidates_manifest, _load_curated_manifest, _save_curated_manifest,
    _push_curated_to_school_images, _rebuild_school_images_from_curated,
    _load_blocklist, _save_blocklist,
)

legacy_admin_bp = Blueprint('legacy_admin', __name__)

# ── Pre-fetch tracking ────────────────────────────────────────────────────────
_prefetch_running: set[str] = set()
_prefetch_lock = threading.Lock()


def _is_duplicate_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return 'unique' in msg or 'duplicate' in msg


def _school_city(school_name: str) -> str | None:
    """Return the city portion of a school's location from SCHOOL_META, if known."""
    meta = SCHOOL_META.get(school_name, {})
    loc  = meta.get('location', '') or ''
    if loc:
        city = loc.split(',')[0].strip()
        return city if city else None
    return None


@legacy_admin_bp.route('/admin/login', methods=['GET'])
def admin_login_page():
    if session.get('admin_email'):
        return redirect('/admin/curate')
    return send_from_directory('static', 'admin_login.html')


@legacy_admin_bp.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    body     = request.get_json(silent=True) or {}
    email    = (body.get('email') or '').strip().lower()
    password = (body.get('password') or '').strip()
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    try:
        with get_db() as conn:
            if using_sqlite():
                cur = conn.execute('SELECT password_hash FROM admins WHERE email = ?', (email,))
                row = cur.fetchone()
            else:
                with conn.cursor() as cur:
                    cur.execute('SELECT password_hash FROM admins WHERE email = %s', (email,))
                    row = cur.fetchone()
        if not row or not check_password_hash(row[0], password):
            return jsonify({'error': 'Incorrect email or password'}), 401
        session['admin_email'] = email
        return jsonify({'ok': True, 'email': email})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@legacy_admin_bp.route('/api/admin/logout', methods=['POST'])
def api_admin_logout():
    session.pop('admin_email', None)
    return jsonify({'ok': True})


@legacy_admin_bp.route('/api/admin/me', methods=['GET'])
def api_admin_me():
    """Return current admin session info — used by frontend to show/hide Admin tab."""
    email = session.get('admin_email')
    if not email:
        return jsonify({'is_admin': False})
    return jsonify({'is_admin': True, 'email': email})


@legacy_admin_bp.route('/api/admin/list-admins', methods=['GET'])
@admin_required
def api_admin_list_admins():
    """List all admin accounts."""
    try:
        with get_db() as conn:
            if using_sqlite():
                rows = conn.execute(
                    'SELECT email, created_by, created_at FROM admins ORDER BY created_at'
                ).fetchall()
            else:
                with conn.cursor() as cur:
                    cur.execute('SELECT email, created_by, created_at FROM admins ORDER BY created_at')
                    rows = cur.fetchall()
        return jsonify([
            {'email': r[0], 'created_by': r[1] or 'bootstrap', 'created_at': str(r[2])}
            for r in rows
        ])
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@legacy_admin_bp.route('/api/admin/create-admin', methods=['POST'])
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
            if using_sqlite():
                with conn:
                    conn.execute(
                        'INSERT INTO admins (email, password_hash, created_by) VALUES (?, ?, ?)',
                        (email, pw_hash, creator)
                    )
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        'INSERT INTO admins (email, password_hash, created_by) VALUES (%s, %s, %s)',
                        (email, pw_hash, creator)
                    )
                conn.commit()
        print(f'[admin] {creator} created new admin: {email}')
        return jsonify({'ok': True, 'email': email})
    except Exception as e:
        if _is_duplicate_error(e):
            return jsonify({'error': 'An admin with that email already exists'}), 409
        return jsonify({'error': str(e)}), 500


@legacy_admin_bp.route('/admin/curate')
@admin_required
def admin_curate():
    return send_from_directory('static', 'admin_curate.html')


@legacy_admin_bp.route('/api/admin/conferences', methods=['GET'])
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


@legacy_admin_bp.route('/api/admin/schools', methods=['GET'])
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


@legacy_admin_bp.route('/api/admin/candidates/<path:school>', methods=['GET'])
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

    # Cross-school dedup: hide images that appear in 2+ OTHER schools
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


@legacy_admin_bp.route('/api/admin/fetch-candidates', methods=['POST'])
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


@legacy_admin_bp.route('/api/admin/blocklist', methods=['POST'])
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


@legacy_admin_bp.route('/api/admin/prefetch-conference', methods=['POST'])
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


@legacy_admin_bp.route('/api/admin/save', methods=['POST'])
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


@legacy_admin_bp.route('/api/admin/rebuild-school-images', methods=['POST'])
@admin_required
def api_admin_rebuild_school_images():
    """Sync all curated selections → school_images.json. Useful after bulk curation."""
    n = _rebuild_school_images_from_curated()
    return jsonify({'ok': True, 'updated': n})
