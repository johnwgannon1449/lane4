# Admin image curation file I/O helpers extracted from main.py.
import os, json

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
