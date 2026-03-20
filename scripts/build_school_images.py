"""
Build school image manifest from Wikipedia page images.

Uses generator=images to fetch all images + URLs from a Wikipedia page in ONE
API call, then scores by filename. Much more reliable than listing images and
resolving URLs separately (avoids rate-limiting from sequential individual calls).

Usage:
  python3 scripts/build_school_images.py            # fill gaps + re-fetch bad ones
  python3 scripts/build_school_images.py --rebuild  # force re-fetch all

Output: static/school_images.json
"""

import sys, os, json, time, urllib.request, urllib.parse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.dirname(SCRIPT_DIR)
MANIFEST   = os.path.join(ROOT_DIR, 'static', 'school_images.json')

WIKI_API = 'https://en.wikipedia.org/w/api.php'
UA       = 'Lane4Recruit/1.0 (college swim recruiting; contact@lane4.app)'
_lock    = threading.Lock()

# These old Unsplash URLs mean the school was never actually fetched successfully
BAD_FALLBACK_URLS = {
    'https://images.unsplash.com/photo-1562774053-701939374585?w=1200&q=80',
    'https://images.unsplash.com/photo-1523050854058-8df90110c9f1?w=1200&q=80',
    'https://images.unsplash.com/photo-1530549387789-4c1017266635?w=1200&q=80',
}

# Filename fragments that disqualify an image
BAD_FNAME = [
    'svg', 'logo', 'seal', 'flag', 'map', 'icon', 'coat', 'shield',
    'wordmark', 'crest', '.gif', 'placeholder', 'question', 'default',
    'vector', 'badge', 'insignia', 'monogram', 'mascot', 'patch',
    'athletics_mark', 'spirit_mark', '_mark.',
]

# Max schools allowed to share the same image URL (above this → likely generic)
DEDUPE_MAX = 3


# ── low-level Wikipedia API ────────────────────────────────────────────────────

def _wiki_get(params, timeout=12):
    """Wikipedia API call with polite delay and 429 back-off."""
    import urllib.error
    params['format'] = 'json'
    qs  = urllib.parse.urlencode(params)
    req = urllib.request.Request(f'{WIKI_API}?{qs}', headers={'User-Agent': UA})
    time.sleep(0.4)  # polite per-call delay
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f'  [rate-limit] 429 — sleeping {wait}s …')
                time.sleep(wait)
            else:
                return {}
        except Exception:
            return {}
    return {}


def _search_title(query):
    """Wikipedia search → first result page title, or None."""
    data = _wiki_get({'action': 'query', 'list': 'search',
                      'srsearch': query, 'srlimit': 3})
    results = data.get('query', {}).get('search', [])
    return results[0]['title'] if results else None


def _all_images_with_urls(page_title, width=1000):
    """
    Return list of (score_placeholder, url, file_title) for all images
    on a Wikipedia page, fetched in a SINGLE API call via generator=images.
    """
    data = _wiki_get({
        'action':   'query',
        'titles':   page_title,
        'generator': 'images',
        'gimlimit': '50',
        'prop':     'imageinfo',
        'iiprop':   'url',
        'iiurlwidth': str(width),
        'redirects': '',
    })
    images = []
    pages  = data.get('query', {}).get('pages', {})
    for pid, page in pages.items():
        title = page.get('title', '')
        info  = page.get('imageinfo', [{}])
        if not info:
            continue
        url = info[0].get('thumburl') or info[0].get('url', '')
        if url:
            images.append((title, url))
    return images


# ── image scoring ──────────────────────────────────────────────────────────────

def _school_tokens(school_name):
    stop = {'university', 'college', 'of', 'the', 'and', 'at', 'institute',
            'technology', 'polytechnic', 'state', 'a', 'an', 'in', 'for'}
    return [t.lower() for t in school_name.split() if t.lower() not in stop][:3]


def _score_campus(file_title, tokens):
    fn = file_title.lower()
    if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
        return -999
    if any(b in fn for b in BAD_FNAME):
        return -999
    s = 0
    for tok in tokens:
        if tok and fn.startswith('file:' + tok): s += 15
        elif tok and tok in fn:                  s += 8
    for kw in ['campus', 'aerial', 'quad', 'quadrangle', 'grounds',
                'entrance', 'courtyard', 'panoram']:
        if kw in fn: s += 10
    for kw in ['building', 'chapel', 'tower', 'auditorium', 'clocktower',
                'gymnasium', 'hall', 'center', 'science', 'library']:
        if kw in fn: s += 5
    for kw in ['arch', 'gate', 'fountain', 'square', 'green']:
        if kw in fn: s += 3
    for kw in ['portrait', 'headshot', 'rally', 'protest', 'game']:
        if kw in fn: s -= 6
    return s


def _score_student(file_title, tokens):
    fn = file_title.lower()
    if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
        return -999
    if any(b in fn for b in BAD_FNAME):
        return -999
    s = 0
    for tok in tokens:
        if tok and tok in fn: s += 5
    for kw in ['student', 'students', 'class', 'lecture', 'graduation',
                'commencement', 'activity', 'club', 'library', 'dining',
                'reading', 'study']:
        if kw in fn: s += 8
    for kw in ['aerial', 'exterior', 'building', 'tower', 'chapel']:
        if kw in fn: s -= 4
    return s


def _score_swim(file_title):
    fn = file_title.lower()
    if not (fn.endswith('.jpg') or fn.endswith('.jpeg') or fn.endswith('.png')):
        return -999
    if any(b in fn for b in ['svg', 'logo', '.gif']):
        return -999
    s = 0
    for kw in ['swim', 'pool', 'aquat', 'natator', 'water']:
        if kw in fn: s += 10
    for kw in ['athletic', 'sport', 'recreation', 'fitness']:
        if kw in fn: s += 4
    return s


def _best_from_images(images, score_fn, exclude_urls=None):
    """Score a list of (file_title, url) pairs and return the best URL."""
    exclude_urls = set(exclude_urls or [])
    scored = []
    for title, url in images:
        if url in exclude_urls:
            continue
        s = score_fn(title)
        scored.append((s, url, title))
    scored.sort(reverse=True)
    # Accept any positive-scored image; as fallback accept score >= 0
    for threshold in (0, -5, -100):
        for s, url, _ in scored:
            if s >= threshold:
                return url
        break  # only run threshold=0 pass; fallbacks below handled by caller
    return None


# ── per-school fetch ───────────────────────────────────────────────────────────

def fetch_school_images(school):
    tokens     = _school_tokens(school)
    page_title = _search_title(school) or school

    # ── hero & student_life from main school page ──────────────────────────────
    images = _all_images_with_urls(page_title, width=1000)

    hero_url    = None
    student_url = None

    if images:
        campus_scored = sorted(
            [(title, url) for title, url in images],
            key=lambda x: _score_campus(x[0], tokens),
            reverse=True
        )
        # Hero: best campus image with score > 0
        for title, url in campus_scored:
            if _score_campus(title, tokens) > 0:
                hero_url = url
                break
        # Student life: second-best campus image (different URL)
        for title, url in campus_scored:
            if url != hero_url and _score_campus(title, tokens) > 0:
                student_url = url
                break
        if not student_url:
            student_url = hero_url  # reuse hero rather than go generic

    # ── swim from athletics / swimming page ────────────────────────────────────
    swim_url = None
    for q in [f'{school} swimming and diving', f'{school} athletics',
              f'{school} swim team']:
        swim_title = _search_title(q)
        if not swim_title or swim_title == page_title:
            continue
        swim_images = _all_images_with_urls(swim_title, width=900)
        if swim_images:
            sw_scored = sorted(swim_images,
                               key=lambda x: _score_swim(x[0]), reverse=True)
            for title, url in sw_scored:
                if _score_swim(title) > 0:
                    swim_url = url
                    break
        if swim_url:
            break

    return {
        'hero':             hero_url,
        'student_life':     student_url,
        'swim':             swim_url,
        'hero_is_fallback': hero_url is None,
        'swim_is_fallback': swim_url is None,
    }


# ── dedupe pass ────────────────────────────────────────────────────────────────

def dedupe_manifest(manifest):
    """Clear any URL shared by more than DEDUPE_MAX schools (generic reuse)."""
    for cat in ['hero', 'swim']:
        urls   = [v.get(cat) for v in manifest.values() if v.get(cat)]
        counts = Counter(urls)
        for url, cnt in counts.items():
            if cnt > DEDUPE_MAX:
                cleared = 0
                for imgs in manifest.values():
                    if imgs.get(cat) == url:
                        imgs[cat] = None
                        if cat == 'hero':
                            imgs['student_life'] = None
                        imgs[f'{cat}_is_fallback'] = True
                        cleared += 1
                print(f'  [DEDUPE] {cat}: cleared {cleared} schools sharing → {url[:80]}')
    return manifest


# ── audit ──────────────────────────────────────────────────────────────────────

def audit_manifest(manifest):
    total = len(manifest)
    print(f'\n=== IMAGE MANIFEST AUDIT ({total} schools) ===')
    for cat in ['hero', 'student_life', 'swim']:
        have    = sum(1 for v in manifest.values() if v.get(cat))
        missing = total - have
        urls    = [v.get(cat) for v in manifest.values() if v.get(cat)]
        dupes   = {u: c for u, c in Counter(urls).items() if c > 1}
        print(f'  {cat.upper():12s}: {have:3d} real  |  {missing:3d} null'
              f'  |  {len(dupes)} shared URLs')
        for u, c in sorted(dupes.items(), key=lambda x: -x[1])[:3]:
            print(f'    [{c}x] {u[:80]}')
    print()


# ── main ──────────────────────────────────────────────────────────────────────

def get_all_schools():
    sys.path.insert(0, ROOT_DIR)
    import main as m
    return sorted(m.SCHOOL_META.keys())


def _needs_fetch(school, existing, force_rebuild):
    if force_rebuild:
        return True
    if school not in existing:
        return True
    imgs = existing[school]
    return (imgs.get('hero') in BAD_FALLBACK_URLS or
            imgs.get('swim') in BAD_FALLBACK_URLS or
            imgs.get('hero_is_fallback') is True)    # previously stored as null


def build_manifest(force_rebuild=False):
    schools = get_all_schools()
    print(f'[images] {len(schools)} schools in universe')

    if os.path.exists(MANIFEST) and not force_rebuild:
        with open(MANIFEST) as f:
            existing = json.load(f)
    else:
        existing = {}

    to_fetch = [s for s in schools if _needs_fetch(s, existing, force_rebuild)]
    cached   = len(schools) - len(to_fetch)
    print(f'[images] {len(to_fetch)} to fetch  |  {cached} already good')

    if not to_fetch:
        audit_manifest(existing)
        print('[images] Complete.')
        return existing

    done   = [0]
    errors = [0]

    def worker(school):
        try:
            imgs = fetch_school_images(school)
            time.sleep(0.15)
            return school, imgs, None
        except Exception as e:
            errors[0] += 1
            return school, {
                'hero': None, 'student_life': None, 'swim': None,
                'hero_is_fallback': True, 'swim_is_fallback': True,
            }, str(e)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(worker, s): s for s in to_fetch}
        for fut in as_completed(futures):
            school, imgs, err = fut.result()
            with _lock:
                existing[school] = imgs
                done[0] += 1
                label = '[ERR]' if err else '[ok ]'
                h = (imgs.get('hero') or 'null')[:70]
                w = (imgs.get('swim') or 'null')[:70]
                if err or done[0] % 25 == 0 or done[0] == len(to_fetch):
                    print(f'  {label} [{done[0]:3}/{len(to_fetch)}] {school}')
                    if err:   print(f'         {err}')
                    else:     print(f'         hero: {h}\n         swim: {w}')
                if done[0] % 10 == 0 or done[0] == len(to_fetch):
                    with open(MANIFEST, 'w') as f:
                        json.dump(existing, f, indent=2)

    print(f'\n[images] Fetch done — {done[0]} ok, {errors[0]} errors')
    print('[images] Running dedupe pass …')
    existing = dedupe_manifest(existing)
    with open(MANIFEST, 'w') as f:
        json.dump(existing, f, indent=2)
    print(f'[images] Manifest → {MANIFEST}')
    audit_manifest(existing)
    return existing


if __name__ == '__main__':
    force = '--rebuild' in sys.argv
    build_manifest(force_rebuild=force)
