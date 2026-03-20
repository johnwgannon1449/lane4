"""
Build school image manifest from Wikipedia page images.

Usage:
  python3 scripts/build_school_images.py            # fill gaps only
  python3 scripts/build_school_images.py --rebuild  # force re-fetch all

Output: static/school_images.json

Strategy:
  For each school, inspect Wikipedia page image files and score them by
  campus relevance. Separate searches for swim/aquatics imagery.
  Images come from upload.wikimedia.org — stable, school-specific, CC licensed.
"""

import sys, os, json, time, urllib.request, urllib.parse, urllib.error
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SCRIPT_DIR)
MANIFEST   = os.path.join(ROOT_DIR, 'static', 'school_images.json')

# Stable default images (specific Unsplash photo IDs — permanent URLs)
DEFAULT_HERO    = 'https://images.unsplash.com/photo-1562774053-701939374585?w=1200&q=80'
DEFAULT_STUDENT = 'https://images.unsplash.com/photo-1523050854058-8df90110c9f1?w=1200&q=80'
DEFAULT_SWIM    = 'https://images.unsplash.com/photo-1530549387789-4c1017266635?w=1200&q=80'

WIKI_API = 'https://en.wikipedia.org/w/api.php'
UA = 'Lane4Recruit/1.0 (college swim recruiting; contact@lane4.app)'
_lock = threading.Lock()

# ── low-level API ─────────────────────────────────────────────────────────────

def _wiki_get(params, timeout=12):
    params['format'] = 'json'
    qs  = urllib.parse.urlencode(params)
    req = urllib.request.Request(f'{WIKI_API}?{qs}', headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def _page_images_list(title):
    """Return list of image file titles on a Wikipedia page."""
    data = _wiki_get({'action': 'query', 'titles': title,
                      'prop': 'images', 'imlimit': '50'})
    pages = data.get('query', {}).get('pages', {})
    for page in pages.values():
        return [img['title'] for img in page.get('images', [])]
    return []


def _image_url(file_title, width=1200):
    """Get the actual image URL for a Wikipedia file title."""
    data = _wiki_get({'action': 'query', 'titles': file_title,
                      'prop': 'imageinfo', 'iiprop': 'url', 'iiurlwidth': width})
    pages = data.get('query', {}).get('pages', {})
    for page in pages.values():
        info = page.get('imageinfo', [])
        if info:
            return info[0].get('thumburl') or info[0].get('url')
    return None


def _search_page_title(query):
    """Wikipedia search → first result page title, or None."""
    data = _wiki_get({'action': 'query', 'list': 'search',
                      'srsearch': query, 'srlimit': 5})
    results = data.get('query', {}).get('search', [])
    return results[0]['title'] if results else None


# ── image scoring ─────────────────────────────────────────────────────────────

def _score_campus(filename, school_tokens):
    """Score a filename for campus photo likelihood. Higher = better."""
    fn   = filename.lower()
    # disqualify
    BAD = ['svg', '.svg', 'logo', 'seal', 'flag', 'map', 'icon', 'coat',
           'shield', 'wordmark', 'crest', 'blank', 'stub', '.gif',
           'commons', 'placeholder', 'question', 'default']
    if any(b in fn for b in BAD):
        return -999
    # must be a photo
    if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
        return -999

    score = 0
    # boost if filename starts with a school token (school's own photos)
    for tok in school_tokens:
        if tok and fn.startswith(tok):
            score += 15
            break
        if tok and tok in fn:
            score += 8

    # strong campus keywords
    for kw in ['campus', 'aerial', 'quad', 'quadrangle', 'grounds',
                'entrance', 'courtyard', 'panorama', 'panoramic']:
        if kw in fn: score += 10

    # building keywords
    for kw in ['building', 'chapel', 'tower', 'auditorium', 'clocktower',
                'gymnasium', 'athletic', 'science', 'center', 'hall']:
        if kw in fn: score += 5

    # secondary building/campus words
    for kw in ['library', 'admin', 'arch', 'gate', 'fountain', 'square']:
        if kw in fn: score += 3

    # penalise obvious people/events photos
    for kw in ['portrait', 'headshot', 'rally', 'protest', 'ceremony',
                'graduation', 'commencement', 'game', 'team', 'flag']:
        if kw in fn: score -= 6

    return score


def _score_student_life(filename, school_tokens):
    """Score a filename for student-life likelihood."""
    fn = filename.lower()
    BAD = ['svg', '.svg', 'logo', 'seal', 'flag', 'map', 'icon', '.gif']
    if any(b in fn for b in BAD): return -999
    if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
        return -999

    score = 0
    for tok in school_tokens:
        if tok and tok in fn: score += 5

    for kw in ['student', 'students', 'class', 'lecture', 'study',
                'graduation', 'commencement', 'activity', 'club',
                'campus life', 'reading', 'library', 'dining']:
        if kw in fn: score += 8

    # penalise if it looks like a building exterior (those suit hero better)
    for kw in ['aerial', 'exterior', 'building', 'tower', 'chapel']:
        if kw in fn: score -= 4

    return score


def _score_swim(filename):
    fn = filename.lower()
    BAD = ['svg', '.svg', 'logo', 'seal', '.gif']
    if any(b in fn for b in BAD): return -999
    if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
        return -999

    score = 0
    for kw in ['swim', 'pool', 'aquat', 'natator', 'water']:
        if kw in fn: score += 10
    for kw in ['athletic', 'sport', 'recreation', 'fitness']:
        if kw in fn: score += 4
    return score


# ── per-school image fetch ────────────────────────────────────────────────────

def _school_tokens(school_name):
    """Short token list from school name for filename matching."""
    stop = {'university', 'college', 'of', 'the', 'and', 'at', 'institute',
            'technology', 'polytechnic', 'state', 'a', 'an', 'in', 'for'}
    tokens = [t.lower() for t in school_name.split() if t.lower() not in stop]
    return tokens[:3]  # first 3 meaningful tokens


def _best_image_from_page(title, score_fn, top_k=5, img_width=1200):
    """Get list of images from a Wikipedia page, score them, return best URL."""
    files = _page_images_list(title)
    if not files:
        return None

    scored = []
    for f in files:
        s = score_fn(f)
        if s > 0:
            scored.append((s, f))

    scored.sort(reverse=True)
    # Try top candidates (in case some return no URL)
    for _, fname in scored[:top_k]:
        url = _image_url(fname, width=img_width)
        if url:
            return url
    return None


def fetch_school_images(school):
    """Return {'hero', 'student_life', 'swim'} image URLs for one school."""
    tokens = _school_tokens(school)

    # ── find canonical Wikipedia page title ───────────────────────────────────
    page_title = _search_page_title(school)
    if not page_title:
        page_title = school  # fallback to direct lookup

    # ── hero ──────────────────────────────────────────────────────────────────
    hero = _best_image_from_page(
        page_title,
        lambda fn: _score_campus(fn, tokens),
        img_width=1200,
    )

    # ── student life ──────────────────────────────────────────────────────────
    # Try the school's main page first; then search student-life pages
    student_life = _best_image_from_page(
        page_title,
        lambda fn: _score_student_life(fn, tokens),
        img_width=900,
    )
    if not student_life:
        sl_title = _search_page_title(f'{school} student life')
        if sl_title and sl_title != page_title:
            student_life = _best_image_from_page(
                sl_title,
                lambda fn: _score_student_life(fn, tokens),
                img_width=900,
            )
    if not student_life:
        student_life = hero  # fall back to hero before the generic default

    # ── swim ──────────────────────────────────────────────────────────────────
    # Search for the school's aquatics/swimming article
    swim = None
    for q in [f'{school} swimming', f'{school} aquatics', f'{school} swim team']:
        swim_title = _search_page_title(q)
        if swim_title and swim_title != page_title:
            swim = _best_image_from_page(
                swim_title,
                _score_swim,
                img_width=900,
            )
            if swim:
                break
    # Also check the main school page for pool images
    if not swim:
        swim = _best_image_from_page(page_title, _score_swim, img_width=900)

    return {
        'hero':         hero         or DEFAULT_HERO,
        'student_life': student_life or DEFAULT_STUDENT,
        'swim':         swim         or DEFAULT_SWIM,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def get_all_schools():
    sys.path.insert(0, ROOT_DIR)
    import main as m
    return sorted(m.SCHOOL_META.keys())


def build_manifest(force_rebuild=False):
    schools = get_all_schools()
    print(f'[images] {len(schools)} schools in universe')

    if os.path.exists(MANIFEST) and not force_rebuild:
        with open(MANIFEST) as f:
            existing = json.load(f)
    else:
        existing = {}

    to_fetch = [s for s in schools if s not in existing] if not force_rebuild else list(schools)
    print(f'[images] {len(to_fetch)} to fetch  |  {len(schools)-len(to_fetch)} already cached')
    if not to_fetch:
        print('[images] Complete.')
        return existing

    done = [0]
    errors = [0]

    def worker(school):
        try:
            imgs = fetch_school_images(school)
            time.sleep(0.1)
            return school, imgs, None
        except Exception as e:
            errors[0] += 1
            return school, {
                'hero': DEFAULT_HERO,
                'student_life': DEFAULT_STUDENT,
                'swim': DEFAULT_SWIM,
            }, str(e)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(worker, s): s for s in to_fetch}
        for fut in as_completed(futures):
            school, imgs, err = fut.result()
            with _lock:
                existing[school] = imgs
                done[0] += 1
                if err:
                    print(f'  [{done[0]}/{len(to_fetch)}] ERR {school}: {err}')
                elif done[0] % 20 == 0 or done[0] == len(to_fetch):
                    print(f'  [{done[0]}/{len(to_fetch)}] OK  {school}')
                    print(f'    hero: {imgs["hero"][:70]}')
                # save incrementally every 10 schools
                if done[0] % 10 == 0 or done[0] == len(to_fetch):
                    with open(MANIFEST, 'w') as f:
                        json.dump(existing, f, indent=2)

    print(f'[images] Done — {done[0]} fetched, {errors[0]} errors.')
    print(f'[images] Manifest → {MANIFEST}')
    return existing


if __name__ == '__main__':
    force = '--rebuild' in sys.argv
    build_manifest(force_rebuild=force)
